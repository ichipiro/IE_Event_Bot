import assert from "node:assert/strict";
import test from "node:test";

import {
  COMMANDS,
  CLEANUP_TARGETS,
  E2eWorkflowError,
  SERVICES,
  cleanupServices,
  collectAndWriteEvidence,
  createRunId,
  evidencePathForRun,
  parseArguments,
  parseToolPayload,
  runDeployAndCrudSmoke,
  runDeployAndDiscordGoogleSmoke,
  runDeployAndDiscordNotionSmoke,
  runDeployAndDiscordDeltaSmoke,
  runDeployAndDiscordStateSmoke,
  runDeployAndDiscordKvSmoke,
  runDiscordDeltaRecovery,
  selectWorkflowRunId,
  runDeployAndGoogleDiscordSmoke,
  runDeployAndGoogleNotionSmoke,
  runDeployAndNotionCleanupSmoke,
  runDeployAndQaNotificationSmoke,
  runDeployAndReminderSmoke,
  runDeployAndWebhookSimulationSmoke,
  runDeployAndWebhookDeliverySmoke,
  runDeployAndWebhookChangeSmoke,
  runPreflight,
  touchedServicesFromAudit,
} from "./run_e2e_workflow.mjs";


const RUN_ID = "E2E-20260901T000000Z-1234abcd";


function stateWorkflowFixture(overrides = {}, scenario = "discord_state") {
  const calls = [];
  const callTool = async (name, args) => {
    calls.push({ name, args });
    if (overrides[name]) {
      return toolResult(await overrides[name](args));
    }
    if (name === "deploy_e2e") {
      return toolResult({ ok: true, version_sha256: "a".repeat(64) });
    }
    if (name === "trigger_sync") {
      return toolResult({ ok: true, run_id: RUN_ID, status: 200, dirty: true });
    }
    if (name === "read_status") {
      return toolResult({ ok: true, worker_version: { tag: RUN_ID, id_sha256: "a".repeat(64) },
        scenarios: { [scenario]: { present: true, dirty: true, run_id: RUN_ID, stage: scenario === "discord_state" ? "state_verified" : "kv_verified" } } });
    }
    return toolResult({ ok: true, run_id: RUN_ID, manifest: { outcome: "passed" } });
  };
  return { calls, callTool };
}


test("通常KV workflowは保存・別HTTP検証・回収・clean確認を順に実行する", async () => {
  const { calls, callTool } = stateWorkflowFixture();
  assert.deepEqual(await runDeployAndDiscordStateSmoke(callTool, RUN_ID), { ok: true, scenarios: ["discord_state"] });
  assert.deepEqual(calls.map((call) => [call.name, call.args.sync_phase ?? call.args.service ?? null]), [
    ["deploy_e2e", null], ["preflight", null], ["trigger_sync", "prepare"],
    ["trigger_sync", "resume"], ["read_status", null], ["cleanup_run", "discord_state"],
    ["assert_external_state", "discord_state"],
  ]);
  assert.equal(COMMANDS.includes("deploy-and-discord-state-smoke"), true);
});


test("通常KVの保存開始後に中断しても監査から同runの回収対象を復元する", () => {
  assert.deepEqual(touchedServicesFromAudit([
    { run_id: "other", phase: "start", tool: "trigger_sync", target: "discord_notion" },
    { run_id: RUN_ID, phase: "end", tool: "trigger_sync", target: "discord_google" },
    { run_id: RUN_ID, phase: "start", tool: "trigger_sync", target: "discord_state", sync_phase: "prepare" },
  ], RUN_ID), ["discord_state"]);
});


test("KVが見えない間だけ読戻しを待ち、保存要求を再送しない", async () => {
  let resumes = 0;
  const sleeps = [];
  const { calls, callTool } = stateWorkflowFixture({ trigger_sync: async (args) => {
    if (args.sync_phase === "resume" && ++resumes < 3) {
      return { ok: false, run_id: RUN_ID, status: 409, dirty: true, error: "discord_state_not_ready" };
    }
    return { ok: true, run_id: RUN_ID, status: 200, dirty: true };
  } });
  await runDeployAndDiscordStateSmoke(callTool, RUN_ID, { verify: { sleepImpl: async (ms) => sleeps.push(ms) } });
  assert.deepEqual(sleeps, [3000, 3000]);
  assert.equal(calls.filter((call) => call.args.sync_phase === "prepare").length, 1);
  assert.equal(resumes, 3);
});


for (const failure of ["limit", "owner", "http", "run"]) {
  test(`読戻しの${failure}失敗を維持し所有KVだけを回収する`, async () => {
    let resumes = 0;
    const { calls, callTool } = stateWorkflowFixture({ trigger_sync: async (args) => {
      if (args.sync_phase === "prepare") {
        return { ok: true, run_id: RUN_ID, status: 200, dirty: true };
      }
      resumes += 1;
      return { ok: false, run_id: failure === "run" ? "other" : RUN_ID,
        status: failure === "http" ? 500 : 409, dirty: true,
        error: failure === "owner" ? "discord_state_owner_mismatch" : "discord_state_not_ready" };
    } });
    await assert.rejects(runDeployAndDiscordStateSmoke(callTool, RUN_ID, {
      verify: { attempts: 3, sleepImpl: async () => {} },
    }), /discord_state_/);
    assert.equal(resumes, failure === "limit" ? 3 : 1);
    assert.deepEqual(calls.filter((call) => call.name === "cleanup_run").map((call) => call.args.service), ["discord_state"]);
    assert.equal(calls.some((call) => call.name === "assert_external_state"), false);
  });
}


for (const failure of ["deploy_e2e", "trigger_sync", "read_status", "cleanup_run", "assert_external_state"]) {
  test(`通常KV workflowの${failure}失敗を成功扱いしない`, async () => {
    const { calls, callTool } = stateWorkflowFixture({ [failure]: async () => ({ ok: false, error: "injected_failure" }) });
    await assert.rejects(runDeployAndDiscordStateSmoke(callTool, RUN_ID, { cleanup: { attempts: 1 } }));
    assert.equal(calls.some((call) => call.name === "cleanup_run"), failure !== "deploy_e2e");
  });
}


for (const mismatch of ["version", "stage", "outcome"]) {
  test(`通常KV workflowは${mismatch}の不一致を回収後も失敗とする`, async () => {
    const overrides = mismatch === "outcome"
      ? { assert_external_state: async () => ({ ok: true, manifest: { outcome: "failed_clean" } }) }
      : { read_status: async () => ({
        ok: true,
        worker_version: { tag: RUN_ID, id_sha256: (mismatch === "version" ? "b" : "a").repeat(64) },
        scenarios: { discord_state: { present: true, dirty: true, run_id: RUN_ID,
          stage: mismatch === "stage" ? "state_verifying" : "state_verified" } },
      }) };
    const { calls, callTool } = stateWorkflowFixture(overrides);
    await assert.rejects(runDeployAndDiscordStateSmoke(callTool, RUN_ID),
      mismatch === "outcome" ? /discord_state_outcome_failed/ : /discord_state_verification_mismatch/);
    assert.equal(calls.filter((call) => call.name === "cleanup_run").length, 1);
  });
}


for (const failure of [null, "discord_kv_not_ready", "discord_kv_owner_mismatch"]) {
  test(`外部fixture付きKVは保存を再送せず所有資源を回収する: ${failure}`, async () => {
    let resumes = 0;
    const { calls, callTool } = stateWorkflowFixture({ trigger_sync: async (args) => {
      if (args.sync_phase === "resume" && ++resumes === 1 && failure) {
        return { ok: false, run_id: RUN_ID, status: 409, dirty: true, error: failure };
      }
      return { ok: true, run_id: RUN_ID, status: 200, dirty: true };
    } }, "discord_kv");
    const result = runDeployAndDiscordKvSmoke(callTool, RUN_ID, { verify: { sleepImpl: async () => {} } });
    if (failure === "discord_kv_owner_mismatch") {
      await assert.rejects(result, /discord_kv_owner_mismatch/);
    } else {
      assert.deepEqual(await result, { ok: true, scenarios: ["discord_kv"] });
    }
    assert.equal(resumes, failure === "discord_kv_not_ready" ? 2 : 1);
    assert.equal(calls.filter((call) => call.args.sync_phase === "prepare").length, 1);
    assert.deepEqual(calls.filter((call) => call.name === "cleanup_run").map((call) => call.args.service), ["discord_kv"]);
    assert.deepEqual(touchedServicesFromAudit([
      { run_id: RUN_ID, phase: "start", tool: "trigger_sync", target: "discord_kv" },
    ], RUN_ID), ["discord_kv"]);
  });
}


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


test("deploy後にGoogle→Notion適用と所有状態を確認する", async () => {
  const calls = [];
  const callTool = async (name, args) => {
    calls.push({ name, args });
    return toolResult({ ok: true, run_id: RUN_ID });
  };

  const result = await runDeployAndGoogleNotionSmoke(callTool, RUN_ID, {
    preflight: { attempts: 1 },
  });

  assert.deepEqual(result, { ok: true, scenarios: ["google_notion"] });
  assert.deepEqual(
    calls.map(({ name, args }) => [name, args.service ?? null]),
    [
      ["deploy_e2e", null],
      ["preflight", null],
      ["trigger_sync", null],
      ["assert_external_state", "google_notion"],
      ["cleanup_run", "google_notion"],
    ],
  );
  assert.equal(
    calls.at(-1).args.confirmation,
    `cleanup:google_notion:${RUN_ID}`,
  );
  assert.equal(
    calls.find((call) => call.name === "trigger_sync").args.scenario,
    "google_notion",
  );
  assert.equal(CLEANUP_TARGETS.includes("google_notion"), true);
});


test("deploy後にGoogle→Discord適用と所有状態を確認する", async () => {
  const calls = [];
  const callTool = async (name, args) => {
    calls.push({ name, args });
    return toolResult({ ok: true, run_id: RUN_ID });
  };

  const result = await runDeployAndGoogleDiscordSmoke(callTool, RUN_ID, {
    preflight: { attempts: 1 },
  });

  assert.deepEqual(result, { ok: true, scenarios: ["google_discord"] });
  assert.deepEqual(
    calls.map(({ name, args }) => [name, args.service ?? null]),
    [
      ["deploy_e2e", null],
      ["preflight", null],
      ["trigger_sync", null],
      ["assert_external_state", "google_discord"],
      ["cleanup_run", "google_discord"],
    ],
  );
  assert.equal(
    calls.find((call) => call.name === "trigger_sync").args.scenario,
    "google_discord",
  );
  assert.equal(
    calls.at(-1).args.confirmation,
    `cleanup:google_discord:${RUN_ID}`,
  );
  assert.equal(CLEANUP_TARGETS.includes("google_discord"), true);
});


test("deploy後にDiscord→Google適用と所有状態を確認する", async () => {
  const calls = [];
  const callTool = async (name, args) => {
    calls.push({ name, args });
    return toolResult({ ok: true, run_id: RUN_ID });
  };

  const result = await runDeployAndDiscordGoogleSmoke(callTool, RUN_ID, {
    preflight: { attempts: 1 },
  });

  assert.deepEqual(result, { ok: true, scenarios: ["discord_google"] });
  assert.deepEqual(
    calls.map(({ name, args }) => [name, args.service ?? null]),
    [
      ["deploy_e2e", null],
      ["preflight", null],
      ["trigger_sync", null],
      ["assert_external_state", "discord_google"],
      ["cleanup_run", "discord_google"],
    ],
  );
  assert.equal(
    calls.find((call) => call.name === "trigger_sync").args.scenario,
    "discord_google",
  );
  assert.equal(
    calls.at(-1).args.confirmation,
    `cleanup:discord_google:${RUN_ID}`,
  );
  assert.equal(CLEANUP_TARGETS.includes("discord_google"), true);
});


test("deploy後にDiscord→Notion適用と所有状態を確認する", async () => {
  const calls = [];
  const callTool = async (name, args) => {
    calls.push({ name, args });
    return toolResult({ ok: true, run_id: RUN_ID });
  };

  const result = await runDeployAndDiscordNotionSmoke(callTool, RUN_ID, {
    preflight: { attempts: 1 },
  });

  assert.deepEqual(result, { ok: true, scenarios: ["discord_notion"] });
  assert.deepEqual(
    calls.map(({ name, args }) => [name, args.service ?? null]),
    [
      ["deploy_e2e", null],
      ["preflight", null],
      ["trigger_sync", null],
      ["assert_external_state", "discord_notion"],
      ["cleanup_run", "discord_notion"],
    ],
  );
  assert.equal(
    calls.find((call) => call.name === "trigger_sync").args.scenario,
    "discord_notion",
  );
  assert.equal(
    calls.at(-1).args.confirmation,
    `cleanup:discord_notion:${RUN_ID}`,
  );
  assert.equal(CLEANUP_TARGETS.includes("discord_notion"), true);
});


test("recoveryは明示run IDだけを使いfixtureを作成しない", async () => {
  assert.equal(selectWorkflowRunId("deploy-and-discord-delta-recovery", RUN_ID), RUN_ID);
  assert.throws(() => selectWorkflowRunId("deploy-and-discord-delta-recovery", ""));
  assert.throws(() => selectWorkflowRunId("preflight", RUN_ID));
  const calls = [];
  await runDiscordDeltaRecovery(async (name, args) => {
    calls.push({ name, args });
    return toolResult({ ok: true, run_id: RUN_ID });
  }, RUN_ID);
  assert.deepEqual(calls.map((call) => call.name), ["deploy_e2e", "cleanup_run", "preflight"]);
  assert.equal(calls[1].args.confirmation, `cleanup:discord_delta:${RUN_ID}`);
  assert.deepEqual(touchedServicesFromAudit([
    { run_id: RUN_ID, tool: "cleanup_run", target: "discord_delta", phase: "start" },
  ], RUN_ID), ["discord_delta"]);
});

function deltaVersionResult(args) {
  return { version_sha256: args.previous_version_sha256 ? "b".repeat(64) : "a".repeat(64),
    previous_version_sha256: args.previous_version_sha256 ?? null };
}


function deltaSyncResult(args, resumeCount) {
  if (args.response_mode === "discard_after_headers") {
    return { ok: false, status: 200, error: "worker_response_discarded", response_discarded: true };
  }
  if (args.sync_phase === "resume") {
    if (resumeCount === 2) {
      return { ok: false, status: 409, error: "e2e_lock_unavailable" };
    }
    return { status: 200, dirty: false,
      execution_status: resumeCount === 1 ? null : "already_completed" };
  }
  return { status: 200, dirty: args.sync_phase === "advance",
    execution_status: args.sync_phase === "advance" ? "updated" : "prepared" };
}


test("deploy後にDiscord差分同期・更新後と完了後の再送・所有状態を確認する", async () => {
  const calls = [];
  let resumes = 0;
  const callTool = async (name, args) => {
    calls.push({ name, args });
    return toolResult({ ok: true, run_id: RUN_ID, ...deltaVersionResult(args), ...deltaSyncResult(args, args.sync_phase === "resume" ? ++resumes : 0) });
  };

  const result = await runDeployAndDiscordDeltaSmoke(callTool, RUN_ID, {
    preflight: { attempts: 1 },
  });

  assert.deepEqual(result, { ok: true, scenarios: ["discord_delta"] });
  assert.deepEqual(
    calls.map(({ name, args }) => [name, args.service ?? null]),
    [
      ["deploy_e2e", null],
      ["preflight", null],
      ["trigger_sync", null],
      ["trigger_sync", null],
      ["deploy_e2e", null],
      ["trigger_sync", null],
      ["trigger_sync", null],
      ["trigger_sync", null],
      ["trigger_sync", null],
      ["assert_external_state", "discord_delta"],
      ["cleanup_run", "discord_delta"],
    ],
  );
  assert.equal(
    calls.find((call) => call.name === "trigger_sync").args.scenario,
    "discord_delta",
  );
  assert.equal(
    calls.at(-1).args.confirmation,
    `cleanup:discord_delta:${RUN_ID}`,
  );
  assert.deepEqual(calls.filter((call) => call.name === "trigger_sync").map((call) => call.args.sync_phase),
    ["prepare", "advance", "advance", "resume", "resume", "resume"]);
  assert.deepEqual(calls.filter((call) => call.name === "trigger_sync").map((call) => call.args.version_sha256),
    ["a", "a", "b", "b", "b", "b"].map((value) => value.repeat(64)));
  assert.equal(calls.filter((call) => call.name === "deploy_e2e")[1].args.previous_version_sha256,
    "a".repeat(64));
  assert.deepEqual(calls.filter((call) => call.name === "trigger_sync").map((call) => call.args.response_mode),
    [undefined, "discard_after_headers", undefined, undefined, undefined, undefined]);
  assert.equal(CLEANUP_TARGETS.includes("discord_delta"), true);
});

for (const replayStep of [3, 6]) {
  for (const failure of ["status", "dirty", "tool_error"]) {
    test(`Discord差分の再送${replayStep}の${failure}異常を成功にせず回収する`, async () => {
      const calls = [];
      let syncCount = 0;
      let resumes = 0;
      const callTool = async (name, args) => {
        calls.push({ name, args });
        const result = { ok: true, run_id: RUN_ID, ...deltaVersionResult(args),
          ...deltaSyncResult(args, args.sync_phase === "resume" ? ++resumes : 0) };
        if (name === "trigger_sync" && ++syncCount === replayStep) {
          if (failure === "status") {
            result.execution_status = "completed";
          } else if (failure === "dirty") {
            result.dirty = !result.dirty;
          } else {
            result.ok = false;
          }
        }
        return toolResult(result);
      };
      await assert.rejects(runDeployAndDiscordDeltaSmoke(callTool, RUN_ID, {
        preflight: { attempts: 1 },
      }), (error) => error instanceof E2eWorkflowError && (failure === "tool_error" ||
        error.code === (replayStep === 3 ? "delta_update_replay_failed" : "delta_completed_replay_failed")));
      assert.equal(syncCount, replayStep);
      assert.equal(calls.some((call) => call.name === "assert_external_state"), false);
      assert.equal(calls.at(-1).name, "cleanup_run");
      assert.equal(calls.at(-1).args.confirmation, `cleanup:discord_delta:${RUN_ID}`);
    });
  }
}


for (const failurePhase of ["prepare", "advance", "resume"]) {
test(`Discord差分同期の${failurePhase}失敗でも同じrunだけをcleanupする`, async () => {
  const calls = [];
  const callTool = async (name, args) => {
    calls.push({ name, args });
    return toolResult({ ok: name !== "trigger_sync" || args.sync_phase !== failurePhase, run_id: RUN_ID, ...deltaVersionResult(args), execution_status: "updated", dirty: true,
      ...(args.sync_phase !== failurePhase ? deltaSyncResult(args, 0) : {}) });
  };
  await assert.rejects(
    runDeployAndDiscordDeltaSmoke(callTool, RUN_ID, { preflight: { attempts: 1 } }),
    E2eWorkflowError,
  );
  assert.deepEqual(calls.map(({ name }) => name), [
    "deploy_e2e", "preflight", "trigger_sync",
    ...(failurePhase === "prepare" ? [] : failurePhase === "advance" ? ["trigger_sync"]
      : ["trigger_sync", "deploy_e2e", "trigger_sync", "trigger_sync", "trigger_sync"]), "cleanup_run",
  ]);
  assert.equal(calls.at(-1).args.service, "discord_delta");
  assert.equal(calls.at(-1).args.confirmation, `cleanup:discord_delta:${RUN_ID}`);
  assert.deepEqual(touchedServicesFromAudit([
    { run_id: RUN_ID, tool: "trigger_sync", target: "discord_delta", phase: "start" },
    { run_id: "another-run", tool: "trigger_sync", target: "discord_notion", phase: "start" },
  ], RUN_ID), ["discord_delta"]);
});

}

test("deploy後にQA更新通知と所有状態を確認する", async () => {
  const calls = [];
  const callTool = async (name, args) => {
    calls.push({ name, args });
    return toolResult({ ok: true, run_id: RUN_ID });
  };

  const result = await runDeployAndQaNotificationSmoke(callTool, RUN_ID, {
    preflight: { attempts: 1 },
  });

  assert.deepEqual(result, { ok: true, scenarios: ["qa_notification"] });
  assert.deepEqual(
    calls.map(({ name, args }) => [name, args.service ?? null]),
    [
      ["deploy_e2e", null],
      ["preflight", null],
      ["trigger_job", null],
      ["assert_external_state", "qa_notification"],
      ["cleanup_run", "qa_notification"],
    ],
  );
  assert.equal(
    calls.find((call) => call.name === "trigger_job").args.job,
    "qa_check",
  );
  assert.equal(
    calls.at(-1).args.confirmation,
    `cleanup:qa_notification:${RUN_ID}`,
  );
  assert.equal(CLEANUP_TARGETS.includes("qa_notification"), true);
});


test("deploy後に前日リマインドと重複抑止の所有状態を確認する", async () => {
  const calls = [];
  const callTool = async (name, args) => {
    calls.push({ name, args });
    return toolResult({ ok: true, run_id: RUN_ID });
  };

  const result = await runDeployAndReminderSmoke(callTool, RUN_ID, {
    preflight: { attempts: 1 },
  });

  assert.deepEqual(result, { ok: true, scenarios: ["reminder"] });
  assert.deepEqual(
    calls.map(({ name, args }) => [name, args.service ?? null]),
    [
      ["deploy_e2e", null],
      ["preflight", null],
      ["trigger_job", null],
      ["assert_external_state", "reminder"],
      ["cleanup_run", "reminder"],
    ],
  );
  assert.equal(
    calls.find((call) => call.name === "trigger_job").args.job,
    "reminder",
  );
  assert.equal(
    calls.at(-1).args.confirmation,
    `cleanup:reminder:${RUN_ID}`,
  );
  assert.equal(CLEANUP_TARGETS.includes("reminder"), true);
});


test("deploy後にNotion期限cleanupとinterval guardを確認する", async () => {
  const calls = [];
  const callTool = async (name, args) => {
    calls.push({ name, args });
    return toolResult({ ok: true, run_id: RUN_ID });
  };

  const result = await runDeployAndNotionCleanupSmoke(callTool, RUN_ID, {
    preflight: { attempts: 1 },
  });

  assert.deepEqual(result, { ok: true, scenarios: ["notion_cleanup"] });
  assert.deepEqual(
    calls.map(({ name, args }) => [name, args.service ?? null]),
    [
      ["deploy_e2e", null],
      ["preflight", null],
      ["trigger_job", null],
      ["assert_external_state", "notion_cleanup"],
      ["cleanup_run", "notion_cleanup"],
    ],
  );
  assert.equal(
    calls.find((call) => call.name === "trigger_job").args.job,
    "cleanup",
  );
  assert.equal(
    calls.at(-1).args.confirmation,
    `cleanup:notion_cleanup:${RUN_ID}`,
  );
  assert.equal(CLEANUP_TARGETS.includes("notion_cleanup"), true);
});


test("deploy後にWebhook ingress simulationと分離状態を確認する", async () => {
  const calls = [];
  const callTool = async (name, args) => {
    calls.push({ name, args });
    return toolResult({ ok: true, run_id: RUN_ID });
  };

  const result = await runDeployAndWebhookSimulationSmoke(callTool, RUN_ID, {
    preflight: { attempts: 1 },
  });

  assert.deepEqual(result, { ok: true, scenarios: ["webhook_dispatch"] });
  assert.deepEqual(
    calls.map(({ name, args }) => [name, args.service ?? null]),
    [
      ["deploy_e2e", null],
      ["preflight", null],
      ["trigger_webhook", null],
      ["assert_external_state", "webhook_dispatch"],
      ["cleanup_run", "webhook_dispatch"],
    ],
  );
  assert.equal(
    calls.at(-1).args.confirmation,
    `cleanup:webhook_dispatch:${RUN_ID}`,
  );
  assert.equal(CLEANUP_TARGETS.includes("webhook_dispatch"), true);
});


test("deploy後にGoogleの初回Webhook実配信とwatch停止を確認する", async () => {
  const calls = [];
  const callTool = async (name, args) => {
    calls.push({ name, args });
    return toolResult({ ok: true, run_id: RUN_ID });
  };

  const result = await runDeployAndWebhookDeliverySmoke(callTool, RUN_ID, {
    preflight: { attempts: 1 },
  });

  assert.deepEqual(result, { ok: true, scenarios: ["webhook_delivery"] });
  assert.deepEqual(
    calls.map(({ name, args }) => [name, args.service ?? null]),
    [
      ["deploy_e2e", null],
      ["preflight", null],
      ["trigger_webhook_delivery", null],
      ["assert_external_state", "webhook_delivery"],
      ["cleanup_run", "webhook_delivery"],
    ],
  );
  assert.equal(
    calls.at(-1).args.confirmation,
    `cleanup:webhook_delivery:${RUN_ID}`,
  );
  assert.equal(CLEANUP_TARGETS.includes("webhook_delivery"), true);
});


test("deploy後にGoogleのexists実通知と所有event限定dispatchを確認する", async () => {
  const calls = [];
  const callTool = async (name, args) => {
    calls.push({ name, args });
    return toolResult({ ok: true, run_id: RUN_ID });
  };

  const result = await runDeployAndWebhookChangeSmoke(callTool, RUN_ID, {
    preflight: { attempts: 1 },
  });

  assert.deepEqual(result, { ok: true, scenarios: ["webhook_change"] });
  assert.deepEqual(
    calls.map(({ name, args }) => [name, args.service ?? null]),
    [
      ["deploy_e2e", null],
      ["preflight", null],
      ["trigger_webhook_change", null],
      ["assert_external_state", "webhook_change"],
      ["cleanup_run", "webhook_change"],
    ],
  );
  assert.equal(
    calls.at(-1).args.confirmation,
    `cleanup:webhook_change:${RUN_ID}`,
  );
  assert.equal(CLEANUP_TARGETS.includes("webhook_change"), true);
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
    return toolResult({ ok: false, error: "cleanup_target_mismatch" });
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
    { run_id: RUN_ID, tool: "trigger_sync", target: "google_discord", phase: "start" },
    { run_id: RUN_ID, tool: "trigger_sync", target: "google_notion", phase: "start" },
    { run_id: RUN_ID, tool: "trigger_sync", target: "discord_google", phase: "start" },
    { run_id: RUN_ID, tool: "trigger_sync", target: "discord_notion", phase: "start" },
    { run_id: RUN_ID, tool: "trigger_job", target: "qa_check", phase: "start" },
    { run_id: RUN_ID, tool: "trigger_job", target: "reminder", phase: "start" },
    { run_id: RUN_ID, tool: "trigger_job", target: "cleanup", phase: "start" },
    {
      run_id: RUN_ID,
      tool: "trigger_webhook",
      target: "webhook_dispatch",
      phase: "start",
    },
    {
      run_id: RUN_ID,
      tool: "trigger_webhook_delivery",
      target: "webhook_delivery",
      phase: "start",
    },
    {
      run_id: RUN_ID,
      tool: "trigger_webhook_change",
      target: "webhook_change",
      phase: "start",
    },
    {
      run_id: "E2E-20260901T000001Z-1234abcd",
      tool: "seed_fixture",
      target: "notion",
      phase: "start",
    },
  ];

  assert.deepEqual(touchedServicesFromAudit(entries, RUN_ID), [
    "google",
    "discord",
    "discord_google",
    "discord_notion",
    "google_discord",
    "google_notion",
    "qa_notification",
    "reminder",
    "notion_cleanup",
    "webhook_dispatch",
    "webhook_delivery",
    "webhook_change",
  ]);
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

for (const failure of ["same", "missing", "wrong_previous", "tool_error"]) {
  test(`Discord差分の再deploy ${failure}では続行せず回収する`, async () => {
    const calls = [];
    const callTool = async (name, args) => {
      calls.push({ name, args });
      const result = { ok: true, run_id: RUN_ID, ...deltaVersionResult(args), ...deltaSyncResult(args, 0) };
      if (name === "deploy_e2e" && args.previous_version_sha256) {
        if (failure === "same") {
          result.version_sha256 = args.previous_version_sha256;
        } else if (failure === "missing") {
          delete result.version_sha256;
        } else if (failure === "wrong_previous") {
          result.previous_version_sha256 = "c".repeat(64);
        } else {
          result.ok = false;
        }
      }
      return toolResult(result);
    };
    await assert.rejects(runDeployAndDiscordDeltaSmoke(callTool, RUN_ID, {
      preflight: { attempts: 1 },
    }), E2eWorkflowError);
    assert.deepEqual(calls.map((call) => call.name), ["deploy_e2e", "preflight",
      "trigger_sync", "trigger_sync", "deploy_e2e", "cleanup_run"]);
    assert.equal(calls.at(-1).args.confirmation, `cleanup:discord_delta:${RUN_ID}`);
  });
}

test("初回deployのversionが不明ならfixtureを作成しない", async () => {
  const calls = [];
  await assert.rejects(runDeployAndDiscordDeltaSmoke(async (name) => {
    calls.push(name);
    return toolResult({ ok: true });
  }, RUN_ID), (error) => error.code === "delta_deploy_version_missing");
  assert.deepEqual(calls, ["deploy_e2e"]);
});

for (const failure of [null, "both_success", "both_rejected", "wrong_error", "wrong_status", "wrong_run", "completed_replay", "transport"]) {
  test(`同時resume ${failure ?? "success"}では両要求を待ってから後続処理する`, { timeout: 2000 }, async () => {
    const started = Promise.withResolvers();
    const release = Promise.withResolvers();
    const calls = [];
    let resumes = 0;
    let winnerFinished = false;
    const callTool = async (name, args) => {
      calls.push({ name, args });
      const result = { ok: true, run_id: RUN_ID, ...deltaVersionResult(args), ...deltaSyncResult(args, 0) };
      if (name === "trigger_sync" && args.sync_phase === "resume") {
        const number = ++resumes;
        Object.assign(result, deltaSyncResult(args, number));
        if (number === 1) {
          await release.promise;
          winnerFinished = true;
          if (failure === "both_rejected") {
            Object.assign(result, { ok: false, status: 409, error: "e2e_lock_unavailable" });
          } else if (failure === "completed_replay") {
            result.execution_status = "already_completed";
          }
        } else if (number === 2) {
          started.resolve();
          if (failure === "both_success") {
            Object.assign(result, { ok: true, status: 200, dirty: false, execution_status: null });
          } else if (failure === "wrong_error") {
            result.error = "delta_resume_owner_mismatch";
          } else if (failure === "wrong_status") {
            result.status = 503;
          } else if (failure === "wrong_run") {
            result.run_id = "another-run";
          } else if (failure === "transport") {
            throw new Error("transport");
          }
        }
      }
      if (["cleanup_run", "assert_external_state"].includes(name)) {
        assert.equal(winnerFinished, true);
      }
      return toolResult(result);
    };
    const workflow = runDeployAndDiscordDeltaSmoke(callTool, RUN_ID, { preflight: { attempts: 1 } });
    await started.promise;
    assert.equal(winnerFinished, false);
    assert.equal(calls.some((call) => call.name === "cleanup_run"), false);
    release.resolve();
    if (failure) {
      await assert.rejects(workflow, (error) => error.code === "delta_concurrent_resume_failed");
      assert.equal(resumes, 2);
      assert.equal(calls.some((call) => call.name === "assert_external_state"), false);
    } else {
      assert.equal((await workflow).ok, true);
      assert.equal(resumes, 3);
    }
    assert.equal(calls.at(-1).name, "cleanup_run");
  });
}

for (const failure of ["unexpected_success", "missing_flag", "wrong_status", "wrong_error", "wrong_run", "transport"]) {
  test(`更新応答破棄の${failure}では再deployせず回収する`, async () => {
    const calls = [];
    const callTool = async (name, args) => {
      calls.push({ name, args });
      const result = { ok: true, run_id: RUN_ID, ...deltaVersionResult(args), ...deltaSyncResult(args, 0) };
      if (args.response_mode === "discard_after_headers") {
        if (failure === "unexpected_success") {
          result.ok = true;
        } else if (failure === "missing_flag") {
          delete result.response_discarded;
        } else if (failure === "wrong_status") {
          result.status = 500;
        } else if (failure === "wrong_error") {
          result.error = "worker_response_read_failed";
        } else if (failure === "wrong_run") {
          result.run_id = "other-run";
        } else {
          throw new Error("transport");
        }
      }
      return toolResult(result);
    };
    await assert.rejects(runDeployAndDiscordDeltaSmoke(callTool, RUN_ID, { preflight: { attempts: 1 } }),
      (error) => error.code === "delta_response_loss_not_observed");
    assert.deepEqual(calls.map((call) => call.name),
      ["deploy_e2e", "preflight", "trigger_sync", "trigger_sync", "cleanup_run"]);
    assert.equal(calls.at(-1).args.confirmation, `cleanup:discord_delta:${RUN_ID}`);
  });
}
