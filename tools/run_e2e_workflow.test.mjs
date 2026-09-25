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
  runDeployAndSyncLockSmoke,
  runDeployAndSyncFaultsSmoke,
  runDeployAndGoogleSyncSmoke,
  runGoogleCalendarCheck,
  runDeployAndDiscordKvSmoke,
  runDeployAndDiscordBatchSmoke,
  runDeployAndDiscordBatchGoogleSmoke,
  runDeployAndDiscordBatchNotificationSmoke,
  runDiscordDeltaRecovery,
  runGoogleSyncRecovery,
  selectWorkflowRunId,
  runDeployAndGoogleDiscordSmoke,
  runDeployAndGoogleNotionSmoke,
  runDeployAndNotionCleanupSmoke,
  runDeployAndQaNotificationSmoke,
  runDeployAndQaNormalSmoke,
  runDeployAndJobsKvRetrySmoke,
  runDeployAndJobsRetrySmoke,
  runDeployAndJobsListRetrySmoke,
  runDeployAndReminderNormalSmoke,
  runDeployAndNotionCleanupNormalSmoke,
  runDeployAndReminderSmoke,
  runDeployAndWebhookSimulationSmoke,
  runDeployAndWebhookDeliverySmoke,
  runDeployAndWebhookChangeSmoke,
  runPreflight,
  touchedServicesFromAudit,
} from "./run_e2e_workflow.mjs";


const RUN_ID = "E2E-20260901T000000Z-1234abcd";

for (const failure of [null, "notify", "missing_stage"]) {
  test(`通常Q&Aの段階実行・読戻し・回収: ${failure}`, async () => {
    let phase = "";
    let reads = 0;
    const sleeps = [];
    const { calls, callTool } = stateWorkflowFixture({
      trigger_job: async ({ job }) => {
        if (job === "qa_normal_verify") {
          if (++reads === 1) {
            return { ok: false, error: "qa_normal_kv_not_ready" };
          }
          return { ok: true, stages: { [`qa_normal_verify_${phase}`]: 200 } };
        }
        phase = job.replace("qa_normal_", "");
        if (phase === failure) {
          return { ok: false, error: "qa_normal_job_failed" };
        }
        return { ok: true, stages: failure === "missing_stage" ? {} : { [`qa_normal_${phase}`]: 200 } };
      },
      assert_external_state: async () => ({ ok: true, manifest: { outcome: "passed", stages: { qa_normal_cleanup: 200 } } }),
    });
    const task = runDeployAndQaNormalSmoke(callTool, RUN_ID, { sleepImpl: async (ms) => sleeps.push(ms) });
    if (failure) {
      await assert.rejects(task, /qa_normal_/);
    } else {
      assert.deepEqual(await task, { ok: true, scenarios: ["qa_notification"] });
      assert.deepEqual(sleeps, [3000, 65000]);
      assert.equal(calls.filter(c => c.args.job === "qa_normal_first").length, 1);
    }
    assert.equal(calls.filter(c => c.name === "cleanup_run").length, 1);
    assert.deepEqual(touchedServicesFromAudit(calls.filter(c => c.name === "trigger_job").map(c => ({
      run_id: RUN_ID, phase: "start", tool: c.name, target: c.args.job,
    })), RUN_ID), ["qa_notification"]);
  });
}

for (const failure of [null, "notify", "missing_stage"]) {
  test(`通常リマインドの段階実行・読戻し・回収: ${failure}`, async () => {
    let phase = "";
    let reads = 0;
    const sleeps = [];
    const { calls, callTool } = stateWorkflowFixture({
      trigger_job: async ({ job }) => {
        if (job === "reminder_normal_verify") {
          if (++reads === 1) {
            return { ok: false, error: "reminder_normal_kv_not_ready" };
          }
          return { ok: true, stages: { [`reminder_normal_verify_${phase}`]: 200 } };
        }
        phase = job.replace("reminder_normal_", "");
        if (phase === failure) {
          return { ok: false, error: "reminder_normal_job_failed" };
        }
        return { ok: true, stages: failure === "missing_stage" ? {} : { [`reminder_normal_${phase}`]: 200 } };
      },
      assert_external_state: async () => ({ ok: true, manifest: { outcome: "passed", stages: { reminder_normal_cleanup: 200 } } }),
    });
    const task = runDeployAndReminderNormalSmoke(callTool, RUN_ID, { sleepImpl: async (ms) => sleeps.push(ms) });
    if (failure) {
      await assert.rejects(task, /reminder_normal_/);
    } else {
      assert.deepEqual(await task, { ok: true, scenarios: ["reminder"] });
      assert.deepEqual(sleeps, [3000]);
      assert.equal(calls.filter(c => c.args.job === "reminder_normal_prepare").length, 1);
    }
    assert.equal(calls.filter(c => c.name === "cleanup_run").length, 1);
    assert.deepEqual(touchedServicesFromAudit(calls.filter(c => c.name === "trigger_job").map(c => ({
      run_id: RUN_ID, phase: "start", tool: c.name, target: c.args.job,
    })), RUN_ID), ["reminder"]);
  });
}

for (const failure of [null, "execute", "missing_stage"]) {
  test(`通常Notion cleanupの段階実行・読戻し・回収: ${failure}`, async () => {
    let phase = "";
    let reads = 0;
    const sleeps = [];
    const { calls, callTool } = stateWorkflowFixture({
      trigger_job: async ({ job }) => {
        if (job === "cleanup_normal_verify") {
          if (++reads === 1) {
            return { ok: false, error: "cleanup_normal_kv_not_ready" };
          }
          return { ok: true, stages: { [`cleanup_normal_verify_${phase}`]: 200 } };
        }
        phase = job.replace("cleanup_normal_", "");
        if (phase === failure) {
          return { ok: false, error: "cleanup_normal_job_failed" };
        }
        return { ok: true, stages: failure === "missing_stage" ? {} : { [`cleanup_normal_${phase}`]: 200 } };
      },
      assert_external_state: async () => ({ ok: true, manifest: { outcome: "passed", stages: { cleanup_normal_cleanup: 200 } } }),
    });
    const task = runDeployAndNotionCleanupNormalSmoke(callTool, RUN_ID, { sleepImpl: async (ms) => sleeps.push(ms) });
    if (failure) {
      await assert.rejects(task, /cleanup_normal_/);
    } else {
      assert.deepEqual(await task, { ok: true, scenarios: ["notion_cleanup"] });
      assert.deepEqual(sleeps, [3000]);
      assert.equal(calls.filter(c => c.args.job === "cleanup_normal_prepare").length, 1);
    }
    assert.equal(calls.filter(c => c.name === "cleanup_run").length, 1);
    assert.deepEqual(touchedServicesFromAudit(calls.filter(c => c.name === "trigger_job").map(c => ({
      run_id: RUN_ID, phase: "start", tool: c.name, target: c.args.job,
    })), RUN_ID), ["notion_cleanup"]);
  });
}

for (const stage of ["cleanup", "ready", "working"]) {
  for (const failure of [null, "run", "stage", "other_dirty", "version", "outcome"]) {
    test(`google同期の回収専用workflow: ${stage} ${failure ?? "success"}`, async () => {
      assert.equal(selectWorkflowRunId("deploy-and-google-sync-recovery", RUN_ID), RUN_ID);
      assert.throws(() => selectWorkflowRunId("deploy-and-google-sync-recovery", ""));
      const { calls, callTool } = stateWorkflowFixture({
        read_status: async () => ({ ok: true, mode: "e2e", orchestrated_writes_enabled: false,
          worker_version: { tag: failure === "version" ? "other" : RUN_ID, id_sha256: "a".repeat(64) },
          services: {}, scenarios: {
            google_sync: { present: true, dirty: true, run_id: failure === "run" ? "other" : RUN_ID,
              stage: failure === "stage" ? "unowned" : stage },
            discord_delta: { dirty: failure === "other_dirty" },
          } }),
        assert_external_state: async () => ({ ok: true, manifest: { outcome: failure === "outcome" ? "passed" : "failed_clean" } }),
      }, "google_sync");
      if (failure) {
        await assert.rejects(runGoogleSyncRecovery(callTool, RUN_ID), /google_sync_recovery_/);
        if (failure !== "outcome") { assert.deepEqual(calls.map(c => c.name), ["read_status"]); }
      } else {
        assert.deepEqual(await runGoogleSyncRecovery(callTool, RUN_ID), { ok: true, recovered: "google_sync" });
        assert.deepEqual(calls.map(c => c.name), ["read_status", "deploy_e2e", "cleanup_run", "assert_external_state", "preflight"]);
        assert.equal(calls[2].args.confirmation, `cleanup:google_sync:${RUN_ID}`);
        assert.equal(calls[1].args.previous_version_sha256, "a".repeat(64));
      }
    });
  }
}


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


for (const failure of [null, "pending_stage", "advance", "final_stage", "not_ready"]) {
  test(`2件workflowは残件照合後だけ1回続行し回収する: ${failure}`, async () => {
    let advanced = false;
    let reads = 0;
    const calls = [];
    const callTool = async (name, args) => {
      calls.push({ name, args });
      if (name === "deploy_e2e") return toolResult({ ok: true, version_sha256: "a".repeat(64) });
      if (name === "trigger_sync") {
        if (args.sync_phase === "resume" && failure === "not_ready" && ++reads === 1) {
          return toolResult({ ok: false, status: 409, dirty: true, run_id: RUN_ID, error: "discord_batch_not_ready" });
        }
        if (args.sync_phase === "advance") advanced = true;
        return toolResult({ ok: true, status: 200, dirty: true, run_id: RUN_ID,
          execution_status: args.sync_phase === "advance" ? (failure === "advance" ? "updated" : "drained") : "prepared" });
      }
      if (name === "read_status") return toolResult({ ok: true,
        worker_version: { tag: RUN_ID, id_sha256: "a".repeat(64) },
        scenarios: { discord_batch: { present: true, dirty: true, run_id: RUN_ID,
          stage: (advanced ? failure === "final_stage" : failure === "pending_stage")
            ? "wrong" : (advanced ? "batch_verified" : "batch_pending_verified") } },
      });
      return toolResult({ ok: true, manifest: { outcome: "passed" } });
    };
    const result = runDeployAndDiscordBatchSmoke(callTool, RUN_ID, { verify: { sleepImpl: async () => {} } });
    if (failure && failure !== "not_ready") await assert.rejects(result, /discord_batch_/);
    else assert.deepEqual(await result, { ok: true, scenarios: ["discord_batch"] });
    assert.equal(calls.filter((c) => c.args.sync_phase === "prepare").length, 1);
    assert.equal(calls.filter((c) => c.args.sync_phase === "advance").length, failure === "pending_stage" ? 0 : 1);
    assert.deepEqual(calls.filter((c) => c.name === "cleanup_run").map((c) => c.args.service), ["discord_batch"]);
    assert.deepEqual(touchedServicesFromAudit([
      { run_id: RUN_ID, phase: "start", tool: "trigger_sync", target: "discord_batch" },
    ], RUN_ID), ["discord_batch"]);
  });
}


for (const failure of [null, "pending_stage", "advance", "final_stage", "not_ready"]) {
  test(`Google付き2件workflowは残件照合後だけ1回続行し回収する: ${failure}`, async () => {
    let advanced = false;
    let reads = 0;
    const calls = [];
    const callTool = async (name, args) => {
      calls.push({ name, args });
      if (name === "deploy_e2e") { return toolResult({ ok: true, version_sha256: "a".repeat(64) }); }
      if (name === "trigger_sync") {
        if (args.sync_phase === "resume" && failure === "not_ready" && ++reads === 1) {
          return toolResult({ ok: false, status: 409, dirty: true, run_id: RUN_ID, error: "discord_batch_google_not_ready" });
        }
        if (args.sync_phase === "advance") { advanced = true; }
        return toolResult({ ok: true, status: 200, dirty: true, run_id: RUN_ID,
          execution_status: args.sync_phase === "advance" ? (failure === "advance" ? "updated" : "drained") : "prepared" });
      }
      if (name === "read_status") { return toolResult({ ok: true,
        worker_version: { tag: RUN_ID, id_sha256: "a".repeat(64) },
        scenarios: { discord_batch_google: { present: true, dirty: true, run_id: RUN_ID,
          stage: (advanced ? failure === "final_stage" : failure === "pending_stage")
            ? "wrong" : (advanced ? "batch_verified" : "batch_pending_verified") } },
      }); }
      return toolResult({ ok: true, manifest: { outcome: "passed" } });
    };
    const result = runDeployAndDiscordBatchGoogleSmoke(callTool, RUN_ID, { verify: { sleepImpl: async () => {} } });
    if (failure && failure !== "not_ready") {
      await assert.rejects(result, /discord_batch_google_/);
    } else {
      assert.deepEqual(await result, { ok: true, scenarios: ["discord_batch_google"] });
    }
    assert.equal(calls.filter((c) => c.args.sync_phase === "prepare").length, 1);
    assert.equal(calls.filter((c) => c.args.sync_phase === "advance").length, failure === "pending_stage" ? 0 : 1);
    assert.deepEqual(calls.filter((c) => c.name === "cleanup_run").map((c) => c.args.service), ["discord_batch_google"]);
    assert.deepEqual(touchedServicesFromAudit([
      { run_id: RUN_ID, phase: "start", tool: "trigger_sync", target: "discord_batch_google" },
    ], RUN_ID), ["discord_batch_google"]);
  });
}


for (const failure of [null, "pending_stage", "retry", "retry_stage", "advance", "final_stage", "not_ready", "cleanup"]) {
  test(`通知workflowは再試行と残件適用を別HTTPで1回ずつ行い回収する: ${failure}`, async () => {
    const scenario = "discord_batch_notification";
    let advances = 0;
    let reads = 0;
    const calls = [];
    const callTool = async (name, args) => {
      calls.push({ name, args });
      if (name === "deploy_e2e") { return toolResult({ ok: true, version_sha256: "a".repeat(64) }); }
      if (name === "trigger_sync") {
        if (args.sync_phase === "resume" && failure === "not_ready" && ++reads === 1) {
          return toolResult({ ok: false, status: 409, dirty: true, run_id: RUN_ID, error: `${scenario}_not_ready` });
        }
        if (args.sync_phase === "advance") { advances += 1; }
        return toolResult({ ok: true, status: 200, dirty: true, run_id: RUN_ID,
          execution_status: args.sync_phase !== "advance" ? "prepared"
            : (advances === 1 ? (failure === "retry" ? "drained" : "retry_drained")
              : (failure === "advance" ? "retry_drained" : "drained")) });
      }
      if (name === "read_status") {
        const stages = ["batch_pending_verified", "batch_retry_verified", "batch_verified"];
        return toolResult({ ok: true, worker_version: { tag: RUN_ID, id_sha256: "a".repeat(64) },
          scenarios: { [scenario]: { present: true, dirty: true, run_id: RUN_ID,
            stage: failure === ["pending_stage", "retry_stage", "final_stage"][advances] ? "wrong" : stages[advances] } } });
      }
      if (name === "cleanup_run" && failure === "cleanup") {
        return toolResult({ ok: false, dirty: true, error: "cleanup_failed" });
      }
      return toolResult({ ok: true, manifest: { outcome: "passed" } });
    };
    const result = runDeployAndDiscordBatchNotificationSmoke(callTool, RUN_ID, {
      verify: { sleepImpl: async () => {} }, cleanup: { attempts: 1 },
    });
    if (failure && failure !== "not_ready") {
      await assert.rejects(result, /discord_batch_notification_|cleanup_run_failed/);
      assert.equal(calls.some((c) => c.name === "assert_external_state"), false);
    } else {
      assert.deepEqual(await result, { ok: true, scenarios: [scenario] });
    }
    assert.equal(calls.filter((c) => c.args.sync_phase === "prepare").length, 1);
    assert.equal(advances, failure === "pending_stage" ? 0 : ["retry", "retry_stage"].includes(failure) ? 1 : 2);
    assert.ok(calls.some((c) => c.name === "cleanup_run" && c.args.service === scenario));
    assert.deepEqual(touchedServicesFromAudit([
      { run_id: RUN_ID, phase: "start", tool: "trigger_sync", target: scenario },
    ], RUN_ID), [scenario]);
    assert.ok(COMMANDS.includes("deploy-and-discord-batch-notification-smoke"));
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

for (const failure of [null, "prepare", "verify", "version", "outcome"]) {
  test(`通常同期ロックworkflow: ${failure ?? "success"}と必須cleanup`, async () => {
    const overrides = {
      read_status: async () => ({ ok: true,
        worker_version: { tag: RUN_ID, id_sha256: (failure === "version" ? "b" : "a").repeat(64) },
        scenarios: { sync_lock: { present: true, dirty: true, run_id: RUN_ID, stage: "lock_verified" } },
      }),
      trigger_sync: async (args) => ({
        ok: !(args.sync_phase === (failure === "verify" ? "resume" : failure)),
        run_id: RUN_ID, status: 200, dirty: true, error: "sync_lock_probe_failed",
      }),
      assert_external_state: async () => ({ ok: true, manifest: { outcome: failure === "outcome" ? "failed_clean" : "passed" } }),
    };
    const { calls, callTool } = stateWorkflowFixture(overrides, "sync_lock");
    if (failure) {
      await assert.rejects(runDeployAndSyncLockSmoke(callTool, RUN_ID), /sync_lock_/);
    } else {
      assert.deepEqual(await runDeployAndSyncLockSmoke(callTool, RUN_ID), { ok: true, scenarios: ["sync_lock"] });
    }
    assert.equal(calls.filter((call) => call.name === "deploy_e2e").length, 1);
    assert.equal(calls.filter((call) => call.args.sync_phase === "prepare").length, 1);
    assert.equal(calls.filter((call) => call.name === "cleanup_run" && call.args.service === "sync_lock").length, 1);
    assert.deepEqual(touchedServicesFromAudit([
      { run_id: RUN_ID, phase: "start", tool: "trigger_sync", target: "sync_lock" },
    ], RUN_ID), ["sync_lock"]);
  });
}

for (const failure of [null, "prepare", "advance", "early_done", "late_done", "verify", "version", "outcome"]) {
  test(`状態障害workflow: ${failure ?? "success"}と必須cleanup`, async () => {
    let advances = 0;
    const overrides = {
      read_status: async () => ({ ok: true,
        worker_version: { tag: RUN_ID, id_sha256: (failure === "version" ? "b" : "a").repeat(64) },
        scenarios: { sync_faults: { present: true, dirty: true, run_id: RUN_ID, stage: "fault_verified" } },
      }),
      trigger_sync: async (args) => {
        if (args.sync_phase === "advance") { advances += 1; }
        return {
          ok: !(args.sync_phase === (failure === "verify" ? "resume" : failure)),
          run_id: RUN_ID, status: 200, dirty: true, error: "sync_faults_probe_failed",
          execution_status: (advances === 7 && failure !== "late_done") || (failure === "early_done" && advances === 1) ? "prepared" : "partial",
        };
      },
      assert_external_state: async () => ({ ok: true, manifest: { outcome: failure === "outcome" ? "failed_clean" : "passed" } }),
    };
    const { calls, callTool } = stateWorkflowFixture(overrides, "sync_faults");
    if (failure) {
      await assert.rejects(runDeployAndSyncFaultsSmoke(callTool, RUN_ID), /sync_faults_/);
    } else {
      assert.deepEqual(await runDeployAndSyncFaultsSmoke(callTool, RUN_ID), { ok: true, scenarios: ["sync_faults"] });
    }
    if (!failure) {
      assert.deepEqual(calls.filter((call) => call.name === "trigger_sync").map((call) => call.args.sync_phase),
        ["prepare", ...Array(7).fill("advance"), "resume"]);
    }
    if (failure === "advance") { assert.equal(advances, 1); }
    assert.equal(calls.filter((call) => call.name === "deploy_e2e").length, 1);
    assert.equal(calls.filter((call) => call.args.sync_phase === "prepare").length, 1);
    assert.equal(calls.filter((call) => call.name === "cleanup_run" && call.args.service === "sync_faults").length, 1);
    assert.deepEqual(touchedServicesFromAudit([
      { run_id: RUN_ID, phase: "start", tool: "trigger_sync", target: "sync_faults" },
    ], RUN_ID), ["sync_faults"]);
  });
}

for (const failure of [null, "advance", "verify", "version", "outcome", "phase", "injection", "rejection", "rejection_evidence"]) {
  test(`通常Google同期workflow: ${failure ?? "success"}と回収`, async () => {
    let index = 0;
    const steps = ["pending", "drained", "updated", "deleted", "retry_pending", "retried"];
    const { calls, callTool } = stateWorkflowFixture({
      trigger_sync: async (args) => {
        if (args.sync_phase === "advance") { index += 1; }
        return { ok: args.sync_phase !== (failure === "verify" ? "resume" : failure),
          status: 200, dirty: true, run_id: RUN_ID, error: "google_sync_failed",
          execution_status: failure === "phase" ? "deleted" : steps[index] };
      },
      read_status: async () => ({ ok: true,
        worker_version: { tag: RUN_ID, id_sha256: (failure === "version" ? "b" : "a").repeat(64) },
        scenarios: { google_sync: { present: true, dirty: true, run_id: RUN_ID, stage: "verified",
          stages: { [`google_sync_${steps[index]}`]: 200,
            google_sync_discord_invalid_update: failure === "rejection" ? 503 : 400,
            google_sync_discord_rejection_verified: failure === "rejection_evidence" ? undefined : 200,
            google_sync_discord_failure_injected: failure === "injection" ? undefined : 200 } } },
      }),
      assert_external_state: async () => ({ ok: true, manifest: { outcome: failure === "outcome" ? "failed_clean" : "passed" } }),
    }, "google_sync");
    if (failure) {
      await assert.rejects(runDeployAndGoogleSyncSmoke(callTool, RUN_ID), /google_sync_/);
    } else {
      assert.deepEqual(await runDeployAndGoogleSyncSmoke(callTool, RUN_ID), { ok: true, scenarios: ["google_sync"] });
      assert.deepEqual(calls.filter(c => c.name === "trigger_sync").map(c => c.args.sync_phase),
        ["prepare", "resume", ...Array(5).fill(["advance", "resume"]).flat()]);
    }
    assert.equal(calls.filter(c => c.args.sync_phase === "prepare").length, 1);
    assert.equal(calls.filter(c => c.name === "cleanup_run" && c.args.service === "google_sync").length, 1);
    if (failure === "advance") { assert.equal(index, 1); }
    assert.deepEqual(touchedServicesFromAudit([{ run_id: RUN_ID, phase: "start", tool: "trigger_sync", target: "google_sync" }], RUN_ID), ["google_sync"]);
  });
}

for (const failure of ["fetch_once", "body_once", "fetch_persistent", "body_persistent", "advance", "prepare", "application", "wrong_run", "busy"]) {
  test(`Google確認の通信再試行は上限付きで書込みを再送しない: ${failure}`, async () => {
    const steps = ["pending", "drained", "updated", "deleted"];
    let index = 0;
    let failures = 0;
    const waits = [];
    const { calls, callTool } = stateWorkflowFixture({
      trigger_sync: async args => {
        if (args.sync_phase === "advance") { index += 1; }
        const writeFailure = args.sync_phase === (failure === "prepare" ? "prepare_full" : "advance") && ["prepare", "advance"].includes(failure);
        const verifyFailure = args.sync_phase === "resume" && !["prepare", "advance"].includes(failure) &&
          (!failure.endsWith("once") || failures === 0);
        if (writeFailure || verifyFailure) {
          failures += 1;
          return { ok: false, status: failure.startsWith("body") ? 200 : failure === "application" ? 409 : failure === "busy" ? 503 : 0,
            error: failure.startsWith("body") ? "worker_response_read_failed" : failure === "application" ? "google_sync_release_failed" : failure === "busy" ? "google_sync_busy" : "worker_request_failed",
            run_id: failure === "wrong_run" ? "E2E-20260901T000000Z-aaaaaaaa" : RUN_ID, dirty: false };
        }
        return { ok: true, status: 200, dirty: true, run_id: RUN_ID, execution_status: steps[index] };
      },
      read_status: async () => ({ ok: true, worker_version: { tag: RUN_ID, id_sha256: "a".repeat(64) },
        scenarios: { google_sync: { present: true, dirty: true, run_id: RUN_ID, stage: "verified",
          stages: { [`google_sync_${steps[index]}`]: 200, google_sync_full_input: 200, google_sync_shared_empty: 200 } } } }),
      assert_external_state: async () => ({ ok: true, manifest: { outcome: "passed", stages: { google_sync_shared_cleanup: 200 } } }),
    }, "google_sync");
    const execute = () => runDeployAndGoogleSyncSmoke(callTool, RUN_ID, { fullApply: true, verify: { sleepImpl: async ms => waits.push(ms) } });
    if (failure.endsWith("once")) {
      await execute();
      assert.equal(failures, 1);
      assert.equal(calls.filter(c => c.args.sync_phase === "advance").length, 3);
      assert.equal(calls.filter(c => c.args.sync_phase === "resume").length, 5);
    } else {
      await assert.rejects(execute(), /worker_request_failed|worker_response_read_failed|google_sync_release_failed|google_sync_busy/);
      assert.equal(failures, failure.endsWith("persistent") ? 3 : 1);
      assert.equal(calls.filter(c => c.args.sync_phase === "advance").length, failure === "advance" ? 1 : 0);
    }
    assert.equal(calls.filter(c => c.args.sync_phase === "prepare_full").length, 1);
    assert.equal(calls.filter(c => c.name === "cleanup_run").length, 1);
    assert.deepEqual(waits, Array(failure.endsWith("once") ? 1 : failure.endsWith("persistent") ? 2 : 0).fill(3000));
  });
}

for (const failure of [null, "input", "shared", "cleanup", "advance"]) {
  test(`Google全件workflow: ${failure ?? "success"}`, async () => {
    let index = 0;
    const steps = ["pending", "drained", "updated", "deleted"];
    const { calls, callTool } = stateWorkflowFixture({
      trigger_sync: async (args) => {
        if (args.sync_phase === "advance") { index += 1; }
        return { ok: !(failure === "advance" && index === 1), error: "google_sync_failed",
          status: 200, dirty: true, run_id: RUN_ID, execution_status: steps[index] };
      },
      read_status: async () => ({ ok: true, worker_version: { tag: RUN_ID, id_sha256: "a".repeat(64) },
        scenarios: { google_sync: { present: true, dirty: true, run_id: RUN_ID, stage: "verified",
          stages: { [`google_sync_${steps[index]}`]: 200,
            google_sync_full_input: failure === "input" ? undefined : 200,
            google_sync_shared_empty: failure === "shared" ? undefined : 200 } } } }),
      assert_external_state: async () => ({ ok: true, manifest: { outcome: "passed",
        stages: { google_sync_shared_cleanup: failure === "cleanup" ? undefined : 200 } } }),
    }, "google_sync");
    if (failure) {
      await assert.rejects(runDeployAndGoogleSyncSmoke(callTool, RUN_ID, { fullApply: true }), /google_sync_/);
    } else {
      await runDeployAndGoogleSyncSmoke(callTool, RUN_ID, { fullApply: true });
      assert.deepEqual(calls.filter(c => c.name === "trigger_sync").map(c => c.args.sync_phase),
        ["prepare_full", "resume", ...Array(3).fill(["advance", "resume"]).flat()]);
    }
    assert.equal(calls.filter(c => c.name === "cleanup_run").length, 1);
    assert.ok(COMMANDS.includes("deploy-and-google-full-smoke"));
  });
}

for (const classification of ["calendar_empty", "calendar_active", "calendar_deleted", "calendar_mixed", "invalid"]) {
  test(`Calendar診断: ${classification}を照合しcleanupを送らない`, async () => {
    const { calls, callTool } = stateWorkflowFixture({
      trigger_sync: async () => ({ ok: true, status: 200, dirty: false, run_id: RUN_ID, execution_status: classification }),
      read_status: async () => ({ ok: true, worker_version: { tag: RUN_ID, id_sha256: "a".repeat(64) } }),
    });
    if (classification === "invalid") {
      await assert.rejects(runGoogleCalendarCheck(callTool, RUN_ID), /google_sync_inspect_invalid/);
    } else {
      assert.equal((await runGoogleCalendarCheck(callTool, RUN_ID)).execution_status, classification);
    }
    assert.equal(calls.filter(c => c.name === "trigger_sync")[0].args.sync_phase, "inspect");
    assert.equal(calls.filter(c => c.name === "cleanup_run").length, 0);
  });
}

for (const failure of [null, "stage", "input", "rejection", "cursor", "multi_rejection", "multi_cursor", "queue_drain", "series_cleanup", "pagination", "seven_inputs", "notion_failure", "notion_cursor", "delete_failure", "delete_cursor"]) {
  test(`Google matrix workflow: ${failure ?? "success"}`, async () => {
    const steps = [...Array(5).fill("prepared"), "pending", "pending", "drained", "updated", "updated", "deleted", "deleted", "retry_pending", "retried", "retry_pending", "pending", "pending", "retried",
      "prepared", "prepared", "pending", "pending", "pending", "drained", "retry_pending", "retried", "retry_pending", "retried"];
    let index = 0;
    const { calls, callTool } = stateWorkflowFixture({
      trigger_sync: async args => {
        if (args.sync_phase === "advance") { index += 1; }
        return { ok: true, status: 200, dirty: true, run_id: RUN_ID, execution_status: steps[index] };
      },
      read_status: async () => ({ ok: true, worker_version: { tag: RUN_ID, id_sha256: "a".repeat(64) },
        scenarios: { google_sync: { present: true, dirty: true, run_id: RUN_ID, stage: "verified",
          stages: { [`google_matrix_step_${index}`]: failure === "stage" ? undefined : 200,
            google_matrix_full_input: failure === "input" ? undefined : 200, google_sync_shared_empty: 200,
            google_matrix_api_rejection: failure === "rejection" ? 503 : 400,
            google_matrix_cursor_preserved: failure === "cursor" ? undefined : 200,
            google_matrix_rejection_1: 400, google_matrix_rejection_2: 400,
            google_matrix_rejection_4: failure === "multi_rejection" ? 503 : 400,
            google_matrix_multi_cursor_preserved: failure === "multi_cursor" ? undefined : 200,
            google_matrix_pagination: failure === "pagination" ? undefined : 200,
            google_matrix_seven_inputs: failure === "seven_inputs" ? undefined : 200,
            google_matrix_notion_failure_injected: failure === "notion_failure" ? undefined : 200,
            google_matrix_notion_cursor_preserved: failure === "notion_cursor" ? undefined : 200,
            google_matrix_delete_failure_injected: failure === "delete_failure" ? undefined : 200,
            google_matrix_delete_cursor_preserved: failure === "delete_cursor" ? undefined : 200,
            [`google_matrix_queue_drain_${index}`]: failure === "queue_drain" ? undefined : 200 } } } }),
      assert_external_state: async () => ({ ok: true, manifest: { outcome: "passed", stages: {
        google_sync_shared_cleanup: 200, google_matrix_series_cleanup: failure === "series_cleanup" ? undefined : 200,
      } } }),
    }, "google_sync");
    if (failure) {
      await assert.rejects(runDeployAndGoogleSyncSmoke(callTool, RUN_ID, { matrix: true, fullApply: true }), /google_sync_/);
    } else {
      await runDeployAndGoogleSyncSmoke(callTool, RUN_ID, { matrix: true, fullApply: true });
      assert.deepEqual(calls.filter(c => c.name === "trigger_sync").map(c => c.args.sync_phase),
        ["prepare_matrix", "resume", ...Array(27).fill(["advance", "resume"]).flat()]);
    }
    assert.equal(calls.filter(c => c.name === "cleanup_run").length, 1);
  });
}


test("読み取り専用Calendar診断は回収対象へ追加しない", () => {
  assert.deepEqual(touchedServicesFromAudit([
    { run_id: RUN_ID, phase: "start", tool: "trigger_sync", target: "google_sync", sync_phase: "inspect" },
  ], RUN_ID), []);
});

for (const failure of [null, "stage", "google_failure", "discord_failure", "cooldown", "contention", "version", "outcome"]) {
  test(`全体同期workflow: ${failure ?? "success"}`, async () => {
    const steps = ["prepared", "drained", "updated", "drained", "retry_pending", "retried", "retry_pending", "retried", "drained"];
    let index = 0;
    const { calls, callTool } = stateWorkflowFixture({
      trigger_sync: async args => {
        if (args.sync_phase === "advance") { index += 1; }
        return { ok: true, status: 200, dirty: true, run_id: RUN_ID, execution_status: steps[index] };
      },
      read_status: async () => ({ ok: true, worker_version: { tag: RUN_ID, id_sha256: (failure === "version" ? "b" : "a").repeat(64) },
        scenarios: { google_sync: { present: true, dirty: true, run_id: RUN_ID, stage: "verified",
          stages: { [`all_sync_step_${index}`]: failure === "stage" ? undefined : 200,
            all_sync_failure_4: failure === "google_failure" ? undefined : 200,
            all_sync_failure_6: failure === "discord_failure" ? undefined : 200,
            all_sync_cooldown: failure === "cooldown" ? undefined : 200,
            all_sync_contention: failure === "contention" ? undefined : 200,
          } } } }),
      assert_external_state: async () => ({ ok: true, manifest: { outcome: failure === "outcome" ? "failed_clean" : "passed" } }),
    });
    if (failure) {
      await assert.rejects(runDeployAndGoogleSyncSmoke(callTool, RUN_ID, { allSync: true }), /google_sync_/);
    } else {
      await runDeployAndGoogleSyncSmoke(callTool, RUN_ID, { allSync: true });
      assert.deepEqual(calls.filter(c => c.name === "trigger_sync").map(c => c.args.sync_phase),
        ["prepare_all", "resume", ...Array(8).fill(["advance", "resume"]).flat()]);
    }
    assert.equal(calls.filter(c => c.name === "cleanup_run").length, 1);
  });
}

for (const failure of [null, "stage", "dispatch", "version", "cleanup", "outcome"]) {
  test(`通常HTTP全体同期workflow: ${failure ?? "success"}`, async () => {
    const steps = ["prepared", "drained", "updated", "drained"];
    let index = 0;
    const { calls, callTool } = stateWorkflowFixture({
      trigger_sync: async args => {
        if (args.sync_phase === "http_advance") { index += 1; }
        return { ok: true, status: 200, dirty: true, run_id: RUN_ID, execution_status: steps[index] };
      },
      read_status: async () => ({ ok: true,
        worker_version: { tag: RUN_ID, id_sha256: (failure === "version" ? "b" : "a").repeat(64) },
        scenarios: { google_sync: { present: true, dirty: true, run_id: RUN_ID, stage: "verified",
          stages: { [`all_http_step_${index}`]: failure === "stage" ? undefined : 200,
            [`all_http_dispatch_${index}`]: failure === "dispatch" ? undefined : 200 } } } }),
      assert_external_state: async () => ({ ok: true, manifest: {
        outcome: failure === "outcome" ? "failed_clean" : "passed",
        stages: { google_sync_shared_cleanup: failure === "cleanup" ? undefined : 200 } } }),
    }, "google_sync");
    if (failure) {
      await assert.rejects(runDeployAndGoogleSyncSmoke(callTool, RUN_ID, { httpSync: true }), /google_sync_/);
    } else {
      await runDeployAndGoogleSyncSmoke(callTool, RUN_ID, { httpSync: true });
      assert.deepEqual(calls.filter(c => c.name === "trigger_sync").map(c => c.args.sync_phase),
        ["prepare_http", "resume", ...Array(3).fill(["http_advance", "resume"]).flat()]);
    }
    assert.equal(calls.filter(c => c.name === "cleanup_run").length, 1);
  });
}


for (const failure of [null, "maintenance", "callback", "alarm", "retry", "old_token", "old_guard", "old_accepted", "old_duplicate", "release_recovery", "release_cleanup", "cleanup", "queue_cleanup", "transport_once", "transport_always", "unauthorized"]) {
  test(`通常watchと共有Webhook workflow: ${failure ?? "success"}`, async () => {
    const steps = ["prepared", "drained", "updated", "drained"];
    let index = 0;
    let readFailures = 0;
    const { calls, callTool } = stateWorkflowFixture({
      trigger_sync: async args => {
        if (args.sync_phase === "webhook_trigger") { index += 1; }
        return { ok: true, status: 200, dirty: true, run_id: RUN_ID, execution_status: steps[index] };
      },
      read_status: async () => {
        if (index === 3 && (["transport_always", "unauthorized"].includes(failure) || (failure === "transport_once" && readFailures === 0))) {
          readFailures += 1;
          return { ok: false, run_id: RUN_ID, status: failure === "unauthorized" ? 401 : 0,
            error: failure === "unauthorized" ? "worker_http_401" : "worker_request_failed" };
        }
        return { ok: true, worker_version: { tag: RUN_ID, id_sha256: "a".repeat(64) },
        scenarios: { google_sync: { present: true, dirty: true, run_id: RUN_ID, stage: "verified", stages: {
          [`all_http_step_${index}`]: 200, [`all_http_dispatch_${index}`]: 200,
          watch_shared_maintenance: failure === "maintenance" ? undefined : 200,
          watch_shared_release_recovery: failure === "release_recovery" ? undefined : 200,
          [`watch_shared_step_${index}`]: failure === "callback" ? undefined : 200,
          [`watch_shared_alarm_${index}`]: failure === "alarm" ? undefined : 200,
          watch_shared_busy_retry_recovered: failure === "retry" ? undefined : 200,
          watch_shared_failure_retry_recovered: 200,
          watch_shared_old_token_rejected: (index < 2 || failure === "old_token") ? undefined : 401,
          watch_shared_old_channel_guard: (index < 2 || failure === "old_guard") ? undefined : 404,
          watch_shared_old_channel_accepted: (index < 2 || failure === "old_accepted") ? undefined : 204,
          watch_shared_old_channel_duplicate: (index < 2 || failure === "old_duplicate") ? undefined : 204,
        } } } };
      },
      assert_external_state: async () => ({ ok: true, manifest: { outcome: "passed", stages: {
        google_sync_shared_cleanup: 200, watch_shared_cleanup: failure === "cleanup" ? undefined : 200,
        watch_shared_queue_cleanup: failure === "queue_cleanup" ? undefined : 200,
        watch_shared_release_recovery_cleanup: failure === "release_cleanup" ? undefined : 200,
      } } }),
    }, "google_sync");
    const options = { httpSync: true, webhookSync: true, verify: { sleepImpl: async () => {} } };
    if (failure && failure !== "transport_once") {
      await assert.rejects(runDeployAndGoogleSyncSmoke(callTool, RUN_ID, options), /google_sync_|worker_request_failed|worker_http_401/);
    } else {
      await runDeployAndGoogleSyncSmoke(callTool, RUN_ID, options);
      assert.deepEqual(calls.filter(c => c.name === "trigger_sync").map(c => c.args.sync_phase),
        ["prepare_webhook", "resume", ...Array(3).fill(["webhook_trigger", "resume"]).flat()]);
    }
    assert.equal(calls.filter(c => c.name === "cleanup_run").length, 1);
    if (failure === "transport_always") { assert.equal(readFailures, 3); }
    if (["transport_once", "unauthorized"].includes(failure)) { assert.equal(readFailures, 1); }
  });
}


test("deployはSDK既定60秒で打ち切らず、既存deployとrevision確認上限を待つ", async () => {
  const { e2eCallOptions } = await import("./run_e2e_workflow.mjs");
  assert.ok(e2eCallOptions("deploy_e2e", {}).timeout >= 300_000 + 20 * 60_000 + 19 * 3_000);
  assert.equal(e2eCallOptions("trigger_sync", { scenario: "google_sync" }).timeout, 180_000);
  assert.equal(e2eCallOptions("read_status", {}), undefined);
});

for (const failure of [null, "fail", "verify", "evidence"]) {
  test(`通常3ジョブの失敗再試行は1deploy・独立検証・回収を要求: ${failure}`, async () => {
    const phases = {};
    const stages = {};
    const prefixes = { qa_notification: "qa_normal", reminder: "reminder_normal", notion_cleanup: "cleanup_normal" };
    const { calls, callTool } = stateWorkflowFixture({
      trigger_job: async ({ job }) => {
        const prefix = Object.values(prefixes).find(p => job.startsWith(`${p}_`));
        const phase = job.slice(prefix.length + 1);
        stages[prefix] ??= {};
        if (phase === "verify") {
          if (failure === "verify" && phases[prefix] === "fail") {
            return { ok: false, error: "failure_result_mismatch" };
          }
          stages[prefix][`${prefix}_verify_${phases[prefix]}`] = 200;
        } else {
          if (failure === "fail" && phase === "fail") {
            return { ok: false, error: "failure_not_observed" };
          }
          phases[prefix] = phase;
          stages[prefix][`${prefix}_${phase}`] = 200;
          if (phase === "fail" && failure !== "evidence") {
            stages[prefix][`${prefix}_failed_http`] = 500;
          }
        }
        return { ok: true, stages: { ...stages[prefix] } };
      },
      assert_external_state: async ({ service }) => {
        const prefix = prefixes[service];
        return { ok: true, manifest: { outcome: "passed", stages: { ...stages[prefix], [`${prefix}_cleanup`]: 200 } } };
      },
    });
    const task = runDeployAndJobsRetrySmoke(callTool, RUN_ID, { sleepImpl: async () => {} });
    if (failure) {
      await assert.rejects(task, /failure_|job_retry_evidence_missing/);
      assert.equal(calls.filter(c => c.name === "cleanup_run").length, 1);
    } else {
      assert.deepEqual((await task).scenarios, Object.keys(prefixes));
      assert.equal(calls.filter(c => c.name === "cleanup_run").length, 3);
      for (const prefix of Object.values(prefixes)) {
        assert.equal(calls.filter(c => c.args.job === `${prefix}_fail`).length, 1);
        assert.equal(stages[prefix][`${prefix}_verify_fail`], 200);
      }
    }
    assert.equal(calls.filter(c => c.name === "deploy_e2e").length, 1);
    assert.deepEqual(touchedServicesFromAudit(Object.values(prefixes).map(prefix => ({
      run_id: RUN_ID, phase: "start", tool: "trigger_job", target: `${prefix}_fail`,
    })), RUN_ID).sort(), Object.keys(prefixes).sort());
  });
}


for (const failure of [null, "fail", "verify", "evidence"]) {
  test(`通常Notion一覧の失敗再試行は1deploy・独立検証・回収を要求: ${failure}`, async () => {
    const phases = {};
    const stages = {};
    const prefixes = { qa_notification: "qa_normal", notion_cleanup: "cleanup_normal" };
    const { calls, callTool } = stateWorkflowFixture({
      trigger_job: async ({ job }) => {
        const prefix = Object.values(prefixes).find(p => job.startsWith(`${p}_`));
        const phase = job.slice(prefix.length + 1);
        stages[prefix] ??= {};
        if (phase === "verify") {
          if (failure === "verify" && phases[prefix].startsWith("list_fail")) {
            return { ok: false, error: "failure_result_mismatch" };
          }
          stages[prefix][`${prefix}_verify_${phases[prefix]}`] = 200;
        } else {
          if (failure === "fail" && phase.startsWith("list_fail")) {
            return { ok: false, error: "failure_not_observed" };
          }
          phases[prefix] = phase;
          stages[prefix][`${prefix}_${phase}`] = 200;
          if (phase.startsWith("list_fail") && failure !== "evidence") {
            stages[prefix][`${prefix}_${phase}_http`] = 500;
            stages[prefix][`${prefix}_${phase}_injected`] = 503;
            stages[prefix][`${prefix}_${phase}_first_page`] = 200;
          }
        }
        return { ok: true, stages: { ...stages[prefix] } };
      },
      assert_external_state: async ({ service }) => {
        const prefix = prefixes[service];
        return { ok: true, manifest: { outcome: "passed", stages: { ...stages[prefix], [`${prefix}_cleanup`]: 200 } } };
      },
    });
    const task = runDeployAndJobsListRetrySmoke(callTool, RUN_ID, { sleepImpl: async () => {} });
    if (failure) {
      await assert.rejects(task, /failure_|job_list_retry_evidence_missing/);
      assert.equal(calls.filter(c => c.name === "cleanup_run").length, 1);
    } else {
      assert.deepEqual((await task).scenarios, Object.keys(prefixes));
      assert.equal(calls.filter(c => c.name === "cleanup_run").length, 2);
      for (const prefix of Object.values(prefixes)) {
        assert.equal(calls.filter(c => c.args.job === `${prefix}_list_fail`).length, 1);
        assert.equal(stages[prefix][`${prefix}_verify_list_fail`], 200);
      }
    }
    assert.equal(calls.filter(c => c.name === "deploy_e2e").length, 1);
    assert.deepEqual(touchedServicesFromAudit(Object.values(prefixes).map(prefix => ({
      run_id: RUN_ID, phase: "start", tool: "trigger_job", target: `${prefix}_list_fail`,
    })), RUN_ID).sort(), Object.keys(prefixes).sort());
  });
}


for (const failure of [null, "job", "verify", "evidence"]) {
  test(`通常ジョブKV再試行は保存前後の証跡と独立検証・回収を要求: ${failure}`, async () => {
    const phases = {};
    const stages = {};
    const prefixes = { qa_notification: "qa_normal", reminder: "reminder_normal", notion_cleanup: "cleanup_normal" };
    const { calls, callTool } = stateWorkflowFixture({
      trigger_job: async ({ job }) => {
        const prefix = Object.values(prefixes).find(p => job.startsWith(`${p}_`));
        const phase = job.slice(prefix.length + 1);
        stages[prefix] ??= {};
        if (phase === "verify") {
          if (failure === "verify" && phases[prefix] === "notify") {
            return { ok: false, error: "kv_result_mismatch" };
          }
          stages[prefix][`${prefix}_verify_${phases[prefix]}`] = 200;
        } else {
          phases[prefix] = phase === "kv_prepare" ? "prepare" : phase;
          stages[prefix][`${prefix}_${phases[prefix]}`] = 200;
          if (["notify", "execute"].includes(phase)) {
            if (failure === "job") {
              return { ok: false, error: "kv_retry_not_observed" };
            }
            for (const key of ["cache", "result"]) {
              for (const step of ["before", "after", "recovered"]) {
                if (failure !== "evidence" || step !== "recovered") {
                  stages[prefix][`${prefix}_kv_${key}_${step}`] = 200;
                }
              }
            }
          }
        }
        return { ok: true, stages: { ...stages[prefix] } };
      },
      assert_external_state: async ({ service }) => {
        const prefix = prefixes[service];
        return { ok: true, manifest: { outcome: "passed", stages: { ...stages[prefix], [`${prefix}_cleanup`]: 200 } } };
      },
    });
    const task = runDeployAndJobsKvRetrySmoke(callTool, RUN_ID, { sleepImpl: async () => {} });
    if (failure) {
      await assert.rejects(task, /kv_|job_kv_retry_evidence_missing/);
      assert.equal(calls.filter(c => c.name === "cleanup_run").length, 1);
    } else {
      assert.deepEqual((await task).scenarios, Object.keys(prefixes));
      assert.equal(calls.filter(c => c.name === "cleanup_run").length, 3);
      for (const prefix of Object.values(prefixes)) {
        assert.equal(calls.filter(c => c.args.job === `${prefix}_kv_prepare`).length, 1);
        assert.equal(stages[prefix][`${prefix}_verify_duplicate`], 200);
      }
    }
    assert.equal(calls.filter(c => c.name === "deploy_e2e").length, 1);
    assert.deepEqual(touchedServicesFromAudit(Object.values(prefixes).map(prefix => ({
      run_id: RUN_ID, phase: "start", tool: "trigger_job", target: `${prefix}_kv_prepare`,
    })), RUN_ID).sort(), Object.keys(prefixes).sort());
  });
}

for (const failure of [null, "stage", "input", "queue", "cursor", "count", "slot", "cleanup"]) {
  test(`Google17件・上限5件workflow: ${failure ?? "success"}`, async () => {
    const steps = [...Array(18).fill("prepared"), ...Array(4).fill("pending"), ...Array(5).fill("drained")];
    let index = 0;
    let cleanups = 0;
    const stages = () => ({
      google_sync_shared_empty: 200,
      [`google_boundary_step_${index}`]: failure === "stage" ? undefined : 200,
      google_boundary_input_17: failure === "input" ? undefined : 200,
      google_boundary_initial_cursor_preserved: 200,
      [`google_boundary_queue_${Math.max(0, 17 - Math.max(0, index - 18) * 5)}`]: failure === "queue" ? undefined : 200,
      [`google_boundary_cursor_queue_${index}`]: failure === "cursor" ? undefined : 200,
      [`google_boundary_apply_${index}_${index === 22 ? 2 : 5}`]: failure === "count" ? undefined : 200,
      ...Object.fromEntries(Array.from({ length: 17 }, (_, slot) => [`google_boundary_slot_${slot}`, failure === "slot" && slot === 16 ? undefined : 200])),
    });
    const { calls, callTool } = stateWorkflowFixture({
      trigger_sync: async args => {
        if (args.sync_phase === "advance") { index += 1; }
        return { ok: true, status: 200, dirty: true, run_id: RUN_ID, execution_status: steps[index] };
      },
      read_status: async () => ({ ok: true, worker_version: { tag: RUN_ID, id_sha256: "a".repeat(64) },
        scenarios: { google_sync: { present: true, dirty: true, run_id: RUN_ID, stage: "verified", stages: stages() } } }),
      cleanup_run: async () => {
        cleanups += 1;
        return cleanups < 4 ? { ok: false, dirty: true, error: "google_sync_cleanup_pending" } : { ok: true, dirty: false };
      },
      assert_external_state: async () => ({ ok: true, manifest: { outcome: "passed", stages: {
        google_sync_shared_cleanup: 200,
        ...Object.fromEntries(Array.from({ length: 17 }, (_, slot) => [`google_boundary_cleanup_${slot}`, failure === "cleanup" && slot === 16 ? undefined : 200])),
      } } }),
    }, "google_sync");
    const options = { boundary: true, fullApply: true, cleanup: { sleepImpl: async () => {} } };
    if (failure) {
      await assert.rejects(runDeployAndGoogleSyncSmoke(callTool, RUN_ID, options), /google_sync_/);
    } else {
      await runDeployAndGoogleSyncSmoke(callTool, RUN_ID, options);
      assert.deepEqual(calls.filter(c => c.name === "trigger_sync").map(c => c.args.sync_phase),
        ["prepare_boundary", "resume", ...Array(26).fill(["advance", "resume"]).flat()]);
    }
    assert.equal(cleanups, 4);
  });
}

for (const failure of [null, "notion_query_step_3", "notion_query_unique_2", "notion_query_api_rejection", "notion_query_validation_error", "notion_query_failed_dispatch", "notion_query_cursor_preserved", "notion_query_queue_only_retry", "notion_query_reapply", "notion_query_cleanup_2"]) {
  test(`Notion照会復旧workflow: ${failure ?? "success"}`, async () => {
    const steps = ["pending", "retry_pending", "retried", "drained"];
    let index = 0;
    let cleanups = 0;
    const stages = () => {
      const result = { google_sync_shared_empty: 200, google_sync_full_input: 200,
        [`notion_query_step_${index}`]: 200, [`notion_query_unique_${index}`]: 200,
        notion_query_api_rejection: 400, notion_query_validation_error: 200,
        notion_query_failed_dispatch: 500, notion_query_cursor_preserved: 200,
        notion_query_queue_only_retry: 200, notion_query_reapply: 200,
        google_sync_shared_cleanup: 200,
        notion_query_cleanup_0: 200, notion_query_cleanup_1: 200, notion_query_cleanup_2: 200 };
      if (failure) { delete result[failure]; }
      return result;
    };
    const { calls, callTool } = stateWorkflowFixture({
      trigger_sync: async args => {
        if (args.sync_phase === "advance") { index += 1; }
        return { ok: true, status: 200, dirty: true, run_id: RUN_ID, execution_status: steps[index] };
      },
      read_status: async () => ({ ok: true, worker_version: { tag: RUN_ID, id_sha256: "a".repeat(64) },
        scenarios: { google_sync: { present: true, dirty: true, run_id: RUN_ID, stage: "verified", stages: stages() } } }),
      cleanup_run: async () => { cleanups += 1; return { ok: true, dirty: false }; },
      assert_external_state: async () => ({ ok: true, manifest: { outcome: "passed", stages: stages() } }),
    }, "google_sync");
    const options = { notionQuery: true, fullApply: true };
    if (failure) {
      await assert.rejects(runDeployAndGoogleSyncSmoke(callTool, RUN_ID, options), /google_sync_/);
    } else {
      await runDeployAndGoogleSyncSmoke(callTool, RUN_ID, options);
      assert.deepEqual(calls.filter(c => c.name === "trigger_sync").map(c => c.args.sync_phase),
        ["prepare_notion_query", "resume", ...Array(3).fill(["advance", "resume"]).flat()]);
    }
    assert.equal(cleanups, 1);
  });
}

for (const failure of [null, "notion_create_step_3", "notion_create_unique_2", "notion_create_api_rejection", "notion_create_validation_error", "notion_create_failed_dispatch", "notion_create_cursor_preserved", "notion_create_queue_only_retry", "notion_create_reapply", "notion_create_cleanup_2"]) {
  test(`Notion作成復旧workflow: ${failure ?? "success"}`, async () => {
    const steps = ["pending", "retry_pending", "retried", "drained"];
    let index = 0;
    let cleanups = 0;
    const stages = () => {
      const result = { google_sync_shared_empty: 200, google_sync_full_input: 200,
        [`notion_create_step_${index}`]: 200, [`notion_create_unique_${index}`]: 200,
        notion_create_api_rejection: 400, notion_create_validation_error: 200,
        notion_create_failed_dispatch: 500, notion_create_cursor_preserved: 200,
        notion_create_queue_only_retry: 200, notion_create_reapply: 200,
        google_sync_shared_cleanup: 200,
        notion_create_cleanup_0: 200, notion_create_cleanup_1: 200, notion_create_cleanup_2: 200 };
      if (failure) { delete result[failure]; }
      return result;
    };
    const { calls, callTool } = stateWorkflowFixture({
      trigger_sync: async args => {
        if (args.sync_phase === "advance") { index += 1; }
        return { ok: true, status: 200, dirty: true, run_id: RUN_ID, execution_status: steps[index] };
      },
      read_status: async () => ({ ok: true, worker_version: { tag: RUN_ID, id_sha256: "a".repeat(64) },
        scenarios: { google_sync: { present: true, dirty: true, run_id: RUN_ID, stage: "verified", stages: stages() } } }),
      cleanup_run: async () => { cleanups += 1; return { ok: true, dirty: false }; },
      assert_external_state: async () => ({ ok: true, manifest: { outcome: "passed", stages: stages() } }),
    }, "google_sync");
    const options = { notionCreate: true, fullApply: true };
    if (failure) {
      await assert.rejects(runDeployAndGoogleSyncSmoke(callTool, RUN_ID, options), /google_sync_/);
    } else {
      await runDeployAndGoogleSyncSmoke(callTool, RUN_ID, options);
      assert.deepEqual(calls.filter(c => c.name === "trigger_sync").map(c => c.args.sync_phase),
        ["prepare_notion_create", "resume", ...Array(3).fill(["advance", "resume"]).flat()]);
    }
    assert.equal(cleanups, 1);
  });
}

for (const failure of [null, "notion_writeback_step_3", "notion_writeback_unique_2", "notion_writeback_api_rejection", "notion_writeback_validation_error", "notion_writeback_failed_dispatch", "notion_writeback_cursor_preserved", "notion_writeback_queue_only_retry", "notion_writeback_reapply", "notion_writeback_cleanup_2", "notion_writeback_partial_maps", "notion_writeback_writeback_missing", "notion_writeback_same_ids"]) {
  test(`Notion書戻し復旧workflow: ${failure ?? "success"}`, async () => {
    const steps = ["pending", "retry_pending", "retried", "drained"];
    let index = 0;
    let cleanups = 0;
    const stages = () => {
      const result = { google_sync_shared_empty: 200, google_sync_full_input: 200,
        [`notion_writeback_step_${index}`]: 200, [`notion_writeback_unique_${index}`]: 200,
        notion_writeback_api_rejection: 400, notion_writeback_validation_error: 200,
        notion_writeback_failed_dispatch: 500, notion_writeback_cursor_preserved: 200,
        notion_writeback_queue_only_retry: 200, notion_writeback_reapply: 200,
        notion_writeback_partial_maps: 200, notion_writeback_writeback_missing: 200, notion_writeback_same_ids: 200,
        google_sync_shared_cleanup: 200,
        notion_writeback_cleanup_0: 200, notion_writeback_cleanup_1: 200, notion_writeback_cleanup_2: 200 };
      if (failure) { delete result[failure]; }
      return result;
    };
    const { calls, callTool } = stateWorkflowFixture({
      trigger_sync: async args => {
        if (args.sync_phase === "advance") { index += 1; }
        return { ok: true, status: 200, dirty: true, run_id: RUN_ID, execution_status: steps[index] };
      },
      read_status: async () => ({ ok: true, worker_version: { tag: RUN_ID, id_sha256: "a".repeat(64) },
        scenarios: { google_sync: { present: true, dirty: true, run_id: RUN_ID, stage: "verified", stages: stages() } } }),
      cleanup_run: async () => { cleanups += 1; return { ok: true, dirty: false }; },
      assert_external_state: async () => ({ ok: true, manifest: { outcome: "passed", stages: stages() } }),
    }, "google_sync");
    const options = { notionWriteback: true, fullApply: true };
    if (failure) {
      await assert.rejects(runDeployAndGoogleSyncSmoke(callTool, RUN_ID, options), /google_sync_/);
    } else {
      await runDeployAndGoogleSyncSmoke(callTool, RUN_ID, options);
      assert.deepEqual(calls.filter(c => c.name === "trigger_sync").map(c => c.args.sync_phase),
        ["prepare_notion_writeback", "resume", ...Array(3).fill(["advance", "resume"]).flat()]);
    }
    assert.equal(cleanups, 1);
  });
}
