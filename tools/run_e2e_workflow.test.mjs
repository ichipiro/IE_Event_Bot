import assert from "node:assert/strict";
import test from "node:test";

import {
  COMMANDS,
  E2eWorkflowError,
  SERVICES,
  cleanupServices,
  collectAndWriteEvidence,
  createRunId,
  evidencePathForRun,
  parseArguments,
  parseToolPayload,
  runDeployAndCrudSmoke,
  runPreflight,
  touchedServicesFromAudit,
} from "./run_e2e_workflow.mjs";


const RUN_ID = "E2E-20260901T000000Z-1234abcd";


function toolResult(payload) {
  return {
    content: [{ type: "text", text: JSON.stringify(payload) }],
    isError: payload.ok !== true,
  };
}


test("run IDをUTC時刻と8桁hexから生成する", () => {
  const runId = createRunId(
    new Date("2026-09-01T12:34:56.789Z"),
    () => Buffer.from("1234abcd", "hex"),
  );

  assert.equal(runId, "E2E-20260901T123456Z-1234abcd");
});


test("CLIは固定commandとrun ID以外を受け取らない", () => {
  assert.deepEqual(parseArguments(["run-id"]), { command: "run-id", runId: null });
  for (const command of COMMANDS.filter((value) => value !== "run-id")) {
    assert.deepEqual(parseArguments([command, "--run-id", RUN_ID]), {
      command,
      runId: RUN_ID,
    });
  }

  assert.throws(() => parseArguments(["unknown"]), /command_invalid/);
  assert.throws(
    () => parseArguments(["cleanup", "--run-id", "../../outside"]),
    /run_id_invalid/,
  );
  assert.throws(() => evidencePathForRun("../../outside"), /run_id_invalid/);
});


test("MCP応答が不正でも本文を例外へ含めない", () => {
  const sensitiveText = "sensitive-response-value";
  let error;
  try {
    parseToolPayload({
      content: [{ type: "text", text: sensitiveText }],
    });
  } catch (caught) {
    error = caught;
  }

  assert.equal(error.code, "mcp_result_invalid");
  assert.equal(String(error).includes(sensitiveText), false);
  const wrapped = new E2eWorkflowError(sensitiveText);
  assert.equal(wrapped.code, "e2e_workflow_failed");
  assert.equal(String(wrapped).includes(sensitiveText), false);
});


test("preflightを固定回数内で再試行する", async () => {
  let calls = 0;
  const delays = [];
  const callTool = async (name, args) => {
    calls += 1;
    assert.equal(name, "preflight");
    assert.deepEqual(args, { run_id: RUN_ID });
    if (calls < 3) {
      return toolResult({ ok: false, error: "worker_request_failed" });
    }
    return toolResult({ ok: true });
  };

  const result = await runPreflight(callTool, RUN_ID, {
    attempts: 3,
    delayMs: 25,
    sleepImpl: async (delay) => delays.push(delay),
  });

  assert.equal(result.ok, true);
  assert.equal(calls, 3);
  assert.deepEqual(delays, [25, 25]);
});


test("preflightは再試行後もstatusの固定エラーを保持する", async () => {
  let calls = 0;
  const callTool = async () => {
    calls += 1;
    return toolResult({ ok: false, error: "sync_coordinator_required" });
  };

  await assert.rejects(
    runPreflight(callTool, RUN_ID, {
      attempts: 2,
      delayMs: 0,
      sleepImpl: async () => {},
    }),
    (error) => error.code === "sync_coordinator_required",
  );
  assert.equal(calls, 2);
});


test("deploy後に3サービスの自己cleanup型CRUDと所有状態を確認する", async () => {
  const calls = [];
  const callTool = async (name, args) => {
    calls.push({ name, args });
    return toolResult({ ok: true, run_id: RUN_ID });
  };

  const result = await runDeployAndCrudSmoke(callTool, RUN_ID, {
    preflight: { attempts: 1 },
  });

  assert.deepEqual(result, { ok: true, services: SERVICES });
  assert.deepEqual(
    calls.map(({ name, args }) => [name, args.service ?? null]),
    [
      ["deploy_e2e", null],
      ["preflight", null],
      ["seed_fixture", "google"],
      ["assert_external_state", "google"],
      ["seed_fixture", "discord"],
      ["assert_external_state", "discord"],
      ["seed_fixture", "notion"],
      ["assert_external_state", "notion"],
      ["cleanup_run", "google"],
      ["cleanup_run", "discord"],
      ["cleanup_run", "notion"],
    ],
  );
  assert.equal(
    calls[0].args.confirmation,
    `deploy:ie-event-bot-e2e:${RUN_ID}`,
  );
  for (const service of SERVICES) {
    const cleanup = calls.find(
      (call) => call.name === "cleanup_run" && call.args.service === service,
    );
    assert.equal(cleanup.args.confirmation, `cleanup:${service}:${RUN_ID}`);
  }
});


test("CRUD途中失敗時も開始済みserviceだけをcleanupする", async () => {
  const calls = [];
  const callTool = async (name, args) => {
    calls.push({ name, args });
    if (name === "seed_fixture" && args.service === "discord") {
      return toolResult({ ok: false, error: "discord_probe_failed" });
    }
    return toolResult({ ok: true, run_id: RUN_ID });
  };

  await assert.rejects(
    runDeployAndCrudSmoke(callTool, RUN_ID, {
      preflight: { attempts: 1 },
    }),
    (error) => error.code === "discord_probe_failed",
  );

  assert.deepEqual(
    calls
      .filter((call) => call.name === "cleanup_run")
      .map((call) => call.args.service),
    ["google", "discord"],
  );
  assert.equal(
    calls.some(
      (call) => call.name === "seed_fixture" && call.args.service === "notion",
    ),
    false,
  );
});


test("cleanupは一時失敗だけを再試行し所有権不一致では停止する", async () => {
  const calls = [];
  const delays = [];
  let googleAttempts = 0;
  const callTool = async (name, args) => {
    assert.equal(name, "cleanup_run");
    calls.push(args.service);
    if (args.service === "google") {
      googleAttempts += 1;
      return toolResult(
        googleAttempts < 3
          ? { ok: false, error: "worker_request_failed" }
          : { ok: true },
      );
    }
    return toolResult({ ok: false, error: "cleanup_run_id_mismatch" });
  };

  const result = await cleanupServices(
    callTool,
    RUN_ID,
    ["google", "discord"],
    {
      attempts: 4,
      delayMs: 50,
      sleepImpl: async (delay) => delays.push(delay),
    },
  );

  assert.equal(result.ok, false);
  assert.equal(result.services.google.ok, true);
  assert.equal(result.services.google.attempts, 3);
  assert.equal(result.services.discord.attempts, 1);
  assert.deepEqual(calls, ["google", "google", "google", "discord"]);
  assert.deepEqual(delays, [50, 50]);
});


test("監査startがあるserviceだけを常時cleanup対象にする", () => {
  const entries = [
    { run_id: RUN_ID, tool: "seed_fixture", target: "discord", phase: "start" },
    { run_id: RUN_ID, tool: "seed_fixture", target: "google", phase: "start" },
    { run_id: RUN_ID, tool: "seed_fixture", target: "google", phase: "start" },
    { run_id: RUN_ID, tool: "cleanup_run", target: "notion", phase: "start" },
    {
      run_id: "E2E-20260901T000001Z-1234abcd",
      tool: "seed_fixture",
      target: "notion",
      phase: "start",
    },
  ];

  assert.deepEqual(touchedServicesFromAudit(entries, RUN_ID), ["google", "discord"]);
});


test("evidence失敗時も固定codeだけを含むmanifestを書き出す", async () => {
  let written;
  const sensitiveText = "sensitive-worker-response";
  const callTool = async () => ({
    content: [{ type: "text", text: sensitiveText }],
    isError: true,
  });

  await assert.rejects(
    collectAndWriteEvidence(callTool, RUN_ID, {
      writeImpl: async (runId, manifest) => {
        written = { runId, manifest };
      },
    }),
    (error) => error.code === "mcp_result_invalid",
  );

  assert.equal(written.runId, RUN_ID);
  assert.deepEqual(written.manifest.evidence, {
    ok: false,
    error: "mcp_result_invalid",
  });
  assert.equal(JSON.stringify(written).includes(sensitiveText), false);
});
