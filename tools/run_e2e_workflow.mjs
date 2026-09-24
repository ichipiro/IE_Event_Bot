import { randomBytes } from "node:crypto";
import { lstat, mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";

import {
  RUN_ID_PATTERN,
  createE2eMcpServer,
  readAuditEntries,
} from "./e2e_mcp_server.mjs";


export const SERVICES = Object.freeze(["google", "discord", "notion"]);
export const CLEANUP_TARGETS = Object.freeze([
  "google",
  "discord",
  "notion",
  "discord_google",
  "discord_notion",
  "discord_delta",
  "discord_state",
  "discord_kv",
  "discord_batch",
  "discord_batch_google",
  "discord_batch_notification",
  "sync_lock",
  "sync_faults",
  "google_sync",
  "google_discord",
  "google_notion",
  "qa_notification",
  "reminder",
  "notion_cleanup",
  "webhook_dispatch",
  "webhook_delivery",
  "webhook_change",
]);
export const COMMANDS = Object.freeze([
  "run-id",
  "preflight",
  "deploy-and-crud-smoke",
  "deploy-and-discord-google-smoke",
  "deploy-and-discord-notion-smoke",
  "deploy-and-discord-delta-smoke",
  "deploy-and-discord-state-smoke",
  "deploy-and-discord-kv-smoke",
  "deploy-and-discord-batch-smoke",
  "deploy-and-discord-batch-google-smoke",
  "deploy-and-discord-batch-notification-smoke",
  "deploy-and-sync-lock-smoke",
  "deploy-and-sync-faults-smoke",
  "deploy-and-google-sync-smoke",
  "deploy-and-google-full-smoke",
  "deploy-and-google-matrix-smoke",
  "deploy-and-all-sync-smoke",
  "deploy-and-all-http-smoke",
  "deploy-and-watch-shared-smoke",
  "deploy-and-google-calendar-check",
  "deploy-and-google-sync-recovery",
  "deploy-and-discord-delta-recovery",
  "deploy-and-google-discord-smoke",
  "deploy-and-google-notion-smoke",
  "deploy-and-qa-notification-smoke",
  "deploy-and-qa-normal-smoke",
  "deploy-and-reminder-normal-smoke",
  "deploy-and-notion-cleanup-normal-smoke",
  "deploy-and-reminder-smoke",
  "deploy-and-notion-cleanup-smoke",
  "deploy-and-webhook-simulation-smoke",
  "deploy-and-webhook-delivery-smoke",
  "deploy-and-webhook-change-smoke",
  "cleanup",
  "evidence",
]);

const MODULE_PATH = fileURLToPath(import.meta.url);
const REPO_ROOT = resolve(dirname(MODULE_PATH), "..");
const EVIDENCE_ROOT = resolve(REPO_ROOT, "test-results");
const EVIDENCE_DIR = resolve(EVIDENCE_ROOT, "e2e-mcp");
const PREFLIGHT_ATTEMPTS = 5;
const PREFLIGHT_DELAY_MS = 2_000;
const CLEANUP_ATTEMPTS = 4;
const CLEANUP_DELAY_MS = 1_000;
const STATE_VERIFY_ATTEMPTS = 25;
const STATE_VERIFY_DELAY_MS = 3_000;
const GOOGLE_VERIFY_TRANSPORT_ATTEMPTS = 3;
const NON_RETRYABLE_CLEANUP_ERRORS = new Set([
  "cleanup_confirmation_mismatch",
  "cleanup_run_id_mismatch",
  "cleanup_target_mismatch",
  "dirty_manifest_target_mismatch",
  "e2e_mcp_configuration_invalid",
  "invalid_dirty_manifest",
  "legacy_e2e_manifest_review_required",
  "webhook_dedupe_target_mismatch",
  "discord_state_owner_mismatch",
  "discord_kv_owner_mismatch",
  "discord_batch_owner_mismatch",
]);


export class E2eWorkflowError extends Error {
  constructor(code) {
    const sanitizedCode = safeErrorCode(code);
    super(sanitizedCode);
    this.name = "E2eWorkflowError";
    this.code = sanitizedCode;
  }
}


function safeErrorCode(value, fallback = "e2e_workflow_failed") {
  const text = String(value ?? "").trim();
  return /^[a-z0-9_]{1,96}$/.test(text) ? text : fallback;
}


function sleep(delayMs) {
  return new Promise((resolveDelay) => setTimeout(resolveDelay, delayMs));
}


export function selectWorkflowRunId(mode, recoveryRunId = "") {
  if (["deploy-and-discord-delta-recovery", "deploy-and-google-sync-recovery"].includes(mode)) {
    if (!RUN_ID_PATTERN.test(recoveryRunId)) {
      throw new E2eWorkflowError("recovery_run_id_invalid");
    }
    return recoveryRunId;
  }
  if (recoveryRunId) {
    throw new E2eWorkflowError("recovery_run_id_forbidden");
  }
  return createRunId();
}

export function createRunId(now = new Date(), randomBytesImpl = randomBytes) {
  if (!(now instanceof Date) || !Number.isFinite(now.getTime())) {
    throw new E2eWorkflowError("run_id_time_invalid");
  }
  const timestamp = now
    .toISOString()
    .replaceAll("-", "")
    .replaceAll(":", "")
    .replace(/\.\d{3}Z$/, "Z");
  const suffix = randomBytesImpl(4).toString("hex");
  const runId = `E2E-${timestamp}-${suffix}`;
  if (!RUN_ID_PATTERN.test(runId)) {
    throw new E2eWorkflowError("run_id_generation_failed");
  }
  return runId;
}


export function parseArguments(argv) {
  const [command, ...rest] = argv;
  if (!COMMANDS.includes(command)) {
    throw new E2eWorkflowError("command_invalid");
  }
  if (command === "run-id") {
    if (rest.length !== 0) {
      throw new E2eWorkflowError("arguments_invalid");
    }
    return { command, runId: null };
  }
  if (rest.length !== 2 || rest[0] !== "--run-id") {
    throw new E2eWorkflowError("arguments_invalid");
  }
  const runId = String(rest[1] ?? "");
  if (!RUN_ID_PATTERN.test(runId)) {
    throw new E2eWorkflowError("run_id_invalid");
  }
  return { command, runId };
}


export function parseToolPayload(result) {
  if (
    !result ||
    !Array.isArray(result.content) ||
    result.content.length !== 1 ||
    result.content[0]?.type !== "text"
  ) {
    throw new E2eWorkflowError("mcp_result_invalid");
  }
  let payload;
  try {
    payload = JSON.parse(result.content[0].text);
  } catch {
    throw new E2eWorkflowError("mcp_result_invalid");
  }
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new E2eWorkflowError("mcp_result_invalid");
  }
  return payload;
}


async function toolOutcome(callTool, name, args) {
  try {
    const result = await callTool(name, args);
    const payload = parseToolPayload(result);
    const ok = result.isError !== true && payload.ok === true;
    return {
      ok,
      error: ok
        ? null
        : safeErrorCode(payload.error, `${name}_failed`),
      payload,
    };
  } catch (error) {
    return {
      ok: false,
      error: safeErrorCode(error?.code, "mcp_call_failed"),
      payload: {},
    };
  }
}


async function requireTool(callTool, name, args) {
  const outcome = await toolOutcome(callTool, name, args);
  if (!outcome.ok) {
    throw new E2eWorkflowError(outcome.error);
  }
  return outcome.payload;
}


async function readStatusWithRetry(callTool, runId, options = {}) {
  for (let attempt = 1; attempt <= GOOGLE_VERIFY_TRANSPORT_ATTEMPTS; attempt += 1) {
    const result = await toolOutcome(callTool, "read_status", { run_id: runId });
    if (result.ok) {
      return result.payload;
    }
    const transportFailure = (result.error === "worker_request_failed" && result.payload.status === 0) ||
      (result.error === "worker_response_read_failed" && result.payload.status === 200);
    if (!transportFailure || result.payload.run_id !== runId || attempt === GOOGLE_VERIFY_TRANSPORT_ATTEMPTS) {
      throw new E2eWorkflowError(result.error);
    }
    await (options.sleepImpl ?? sleep)(STATE_VERIFY_DELAY_MS);
  }
}


export async function runPreflight(callTool, runId, options = {}) {
  const attempts = options.attempts ?? PREFLIGHT_ATTEMPTS;
  const delayMs = options.delayMs ?? PREFLIGHT_DELAY_MS;
  const sleepImpl = options.sleepImpl ?? sleep;
  let lastError = "preflight_failed";

  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    const outcome = await toolOutcome(callTool, "preflight", { run_id: runId });
    if (outcome.ok) {
      return outcome.payload;
    }
    lastError = outcome.error;
    if (attempt < attempts) {
      await sleepImpl(delayMs);
    }
  }
  throw new E2eWorkflowError(lastError);
}


export async function cleanupServices(callTool, runId, services, options = {}) {
  const attempts = options.attempts ?? CLEANUP_ATTEMPTS;
  const delayMs = options.delayMs ?? CLEANUP_DELAY_MS;
  const sleepImpl = options.sleepImpl ?? sleep;
  const selected = CLEANUP_TARGETS.filter((service) => new Set(services).has(service));
  const results = {};

  for (const service of selected) {
    let lastError = "cleanup_run_failed";
    let completed = false;
    let usedAttempts = 0;
    for (let attempt = 1; attempt <= attempts; attempt += 1) {
      usedAttempts = attempt;
      const outcome = await toolOutcome(callTool, "cleanup_run", {
        run_id: runId,
        service,
        confirmation: `cleanup:${service}:${runId}`,
      });
      if (outcome.ok) {
        completed = true;
        lastError = null;
        break;
      }
      lastError = outcome.error;
      if (
        NON_RETRYABLE_CLEANUP_ERRORS.has(lastError) ||
        attempt === attempts
      ) {
        break;
      }
      await sleepImpl(delayMs);
    }
    results[service] = {
      ok: completed,
      attempts: usedAttempts,
      error: lastError,
    };
  }

  return {
    ok: Object.values(results).every((result) => result.ok),
    services: results,
  };
}


export async function runDeployAndCrudSmoke(callTool, runId, options = {}) {
  await requireTool(callTool, "deploy_e2e", {
    run_id: runId,
    confirmation: `deploy:ie-event-bot-e2e:${runId}`,
  });
  await runPreflight(callTool, runId, options.preflight);

  const touched = [];
  let primaryError = null;
  try {
    for (const service of SERVICES) {
      touched.push(service);
      await requireTool(callTool, "seed_fixture", {
        run_id: runId,
        service,
      });
      await requireTool(callTool, "assert_external_state", {
        run_id: runId,
        service,
      });
    }
  } catch (error) {
    primaryError = error;
  }

  const cleanup = await cleanupServices(callTool, runId, touched, {
    attempts: 1,
    sleepImpl: options.cleanup?.sleepImpl,
  });
  if (primaryError) {
    throw primaryError;
  }
  if (!cleanup.ok) {
    throw new E2eWorkflowError("cleanup_run_failed");
  }
  return { ok: true, services: [...touched] };
}


export async function runDeployAndGoogleNotionSmoke(callTool, runId, options = {}) {
  await requireTool(callTool, "deploy_e2e", {
    run_id: runId,
    confirmation: `deploy:ie-event-bot-e2e:${runId}`,
  });
  await runPreflight(callTool, runId, options.preflight);

  let primaryError = null;
  try {
    await requireTool(callTool, "trigger_sync", {
      run_id: runId,
      scenario: "google_notion",
    });
    await requireTool(callTool, "assert_external_state", {
      run_id: runId,
      service: "google_notion",
    });
  } catch (error) {
    primaryError = error;
  }

  const cleanup = await cleanupServices(callTool, runId, ["google_notion"], {
    attempts: 1,
    sleepImpl: options.cleanup?.sleepImpl,
  });
  if (primaryError) {
    throw primaryError;
  }
  if (!cleanup.ok) {
    throw new E2eWorkflowError("cleanup_run_failed");
  }
  return { ok: true, scenarios: ["google_notion"] };
}


export async function runDeployAndGoogleDiscordSmoke(callTool, runId, options = {}) {
  await requireTool(callTool, "deploy_e2e", {
    run_id: runId,
    confirmation: `deploy:ie-event-bot-e2e:${runId}`,
  });
  await runPreflight(callTool, runId, options.preflight);

  let primaryError = null;
  try {
    await requireTool(callTool, "trigger_sync", {
      run_id: runId,
      scenario: "google_discord",
    });
    await requireTool(callTool, "assert_external_state", {
      run_id: runId,
      service: "google_discord",
    });
  } catch (error) {
    primaryError = error;
  }

  const cleanup = await cleanupServices(callTool, runId, ["google_discord"], {
    attempts: 1,
    sleepImpl: options.cleanup?.sleepImpl,
  });
  if (primaryError) {
    throw primaryError;
  }
  if (!cleanup.ok) {
    throw new E2eWorkflowError("cleanup_run_failed");
  }
  return { ok: true, scenarios: ["google_discord"] };
}


export async function runDeployAndDiscordGoogleSmoke(callTool, runId, options = {}) {
  await requireTool(callTool, "deploy_e2e", {
    run_id: runId,
    confirmation: `deploy:ie-event-bot-e2e:${runId}`,
  });
  await runPreflight(callTool, runId, options.preflight);

  let primaryError = null;
  try {
    await requireTool(callTool, "trigger_sync", {
      run_id: runId,
      scenario: "discord_google",
    });
    await requireTool(callTool, "assert_external_state", {
      run_id: runId,
      service: "discord_google",
    });
  } catch (error) {
    primaryError = error;
  }

  const cleanup = await cleanupServices(callTool, runId, ["discord_google"], {
    attempts: 1,
    sleepImpl: options.cleanup?.sleepImpl,
  });
  if (primaryError) {
    throw primaryError;
  }
  if (!cleanup.ok) {
    throw new E2eWorkflowError("cleanup_run_failed");
  }
  return { ok: true, scenarios: ["discord_google"] };
}


export async function runDeployAndDiscordNotionSmoke(callTool, runId, options = {}) {
  await requireTool(callTool, "deploy_e2e", {
    run_id: runId,
    confirmation: `deploy:ie-event-bot-e2e:${runId}`,
  });
  await runPreflight(callTool, runId, options.preflight);

  let primaryError = null;
  try {
    await requireTool(callTool, "trigger_sync", {
      run_id: runId,
      scenario: "discord_notion",
    });
    await requireTool(callTool, "assert_external_state", {
      run_id: runId,
      service: "discord_notion",
    });
  } catch (error) {
    primaryError = error;
  }

  const cleanup = await cleanupServices(callTool, runId, ["discord_notion"], {
    attempts: 1,
    sleepImpl: options.cleanup?.sleepImpl,
  });
  if (primaryError) {
    throw primaryError;
  }
  if (!cleanup.ok) {
    throw new E2eWorkflowError("cleanup_run_failed");
  }
  return { ok: true, scenarios: ["discord_notion"] };
}


export async function runDiscordDeltaRecovery(callTool, runId) {
  await requireTool(callTool, "deploy_e2e", {
    run_id: runId, confirmation: `deploy:ie-event-bot-e2e:${runId}`,
  });
  await requireTool(callTool, "cleanup_run", {
    run_id: runId, service: "discord_delta", confirmation: `cleanup:discord_delta:${runId}`,
  });
  await runPreflight(callTool, runId);
  return { ok: true, recovered: "discord_delta" };
}

export async function runGoogleSyncRecovery(callTool, runId) {
  const before = await requireTool(callTool, "read_status", { run_id: runId });
  const owner = before.scenarios?.google_sync;
  if (before.mode !== "e2e" || before.orchestrated_writes_enabled !== false ||
      before.worker_version?.tag !== runId || !owner?.present || !owner.dirty ||
      owner.run_id !== runId || !["cleanup", "ready", "working"].includes(owner.stage) ||
      Object.values(before.services ?? {}).some(item => item.dirty) ||
      Object.entries(before.scenarios ?? {}).some(([key, item]) => key !== "google_sync" && item.dirty)) {
    throw new E2eWorkflowError("google_sync_recovery_owner_mismatch");
  }
  await requireTool(callTool, "deploy_e2e", {
    run_id: runId, confirmation: `deploy:ie-event-bot-e2e:${runId}`,
    previous_version_sha256: before.worker_version.id_sha256,
  });
  const cleanup = await cleanupServices(callTool, runId, ["google_sync"]);
  if (!cleanup.ok) { throw new E2eWorkflowError("google_sync_recovery_failed"); }
  const result = await requireTool(callTool, "assert_external_state", { run_id: runId, service: "google_sync" });
  if (result.manifest?.outcome !== "failed_clean") {
    throw new E2eWorkflowError("google_sync_recovery_outcome_mismatch");
  }
  await runPreflight(callTool, runId);
  return { ok: true, recovered: "google_sync" };
}

const SYNC_FAULT_CASE_COUNT = 8;

async function runDiscordKvSmoke(callTool, runId, scenario, verifiedStage, options) {
  const deployed = await requireTool(callTool, "deploy_e2e", {
    run_id: runId, confirmation: `deploy:ie-event-bot-e2e:${runId}`,
  });
  if (!/^[0-9a-f]{64}$/.test(String(deployed.version_sha256 ?? ""))) {
    throw new E2eWorkflowError(`${scenario}_version_missing`);
  }
  await runPreflight(callTool, runId, options.preflight);
  let primaryError = null;
  try {
    const prepared = await requireTool(callTool, "trigger_sync", {
      run_id: runId, scenario, sync_phase: "prepare",
    });
    if (prepared.status !== 200 || prepared.dirty !== true || prepared.run_id !== runId) {
      throw new E2eWorkflowError(`${scenario}_prepare_failed`);
    }
    if (scenario === "sync_faults") {
      if (prepared.execution_status !== "partial") {
        throw new E2eWorkflowError("sync_faults_prepare_failed");
      }
      // 残り7ケースを1 HTTPずつ実行する。書込みの自動再送はしない。
      for (let index = 1; index < SYNC_FAULT_CASE_COUNT; index += 1) {
        const advanced = await requireTool(callTool, "trigger_sync", {
          run_id: runId, scenario, sync_phase: "advance",
        });
        if (advanced.status !== 200 || advanced.dirty !== true || advanced.run_id !== runId ||
            advanced.execution_status !== (index === SYNC_FAULT_CASE_COUNT - 1 ? "prepared" : "partial")) {
          throw new E2eWorkflowError("sync_faults_advance_failed");
        }
      }
    }
    async function verify(expectedStage) {
      const attempts = options.verify?.attempts ?? STATE_VERIFY_ATTEMPTS;
      if (!Number.isInteger(attempts) || attempts < 1 || attempts > STATE_VERIFY_ATTEMPTS) {
        throw new E2eWorkflowError(`${scenario}_attempts_invalid`);
      }
      const sleepImpl = options.verify?.sleepImpl ?? sleep;
      for (let attempt = 1; attempt <= attempts; attempt += 1) {
        const result = await toolOutcome(callTool, "trigger_sync", {
          run_id: runId, scenario, sync_phase: "resume",
        });
        if (result.ok) {
          if (result.payload.status !== 200 || result.payload.dirty !== true || result.payload.run_id !== runId) {
            throw new E2eWorkflowError(`${scenario}_resume_failed`);
          }
          break;
        }
        // 保存は再送せず、KVがまだ見えないという固定応答だけを有限回待つ。
        if (result.error !== `${scenario}_not_ready` || result.payload.status !== 409 ||
            result.payload.run_id !== runId || result.payload.dirty !== true || attempt === attempts) {
          throw new E2eWorkflowError(result.error);
        }
        await sleepImpl(STATE_VERIFY_DELAY_MS);
      }
      const status = await requireTool(callTool, "read_status", { run_id: runId });
      const manifest = status.scenarios?.[scenario];
      if (!manifest?.present || manifest.dirty !== true || manifest.run_id !== runId ||
          manifest.stage !== expectedStage || status.worker_version?.tag !== runId ||
          status.worker_version?.id_sha256 !== deployed.version_sha256) {
        throw new E2eWorkflowError(`${scenario}_verification_mismatch`);
      }
    }
    if (scenario === "discord_batch_notification") {
      await verify("batch_pending_verified");
      const retried = await requireTool(callTool, "trigger_sync", {
        run_id: runId, scenario, sync_phase: "advance",
      });
      if (retried.status !== 200 || retried.dirty !== true || retried.run_id !== runId ||
          retried.execution_status !== "retry_drained") {
        throw new E2eWorkflowError(`${scenario}_retry_failed`);
      }
    }
    if (["discord_batch", "discord_batch_google", "discord_batch_notification"].includes(scenario)) {
      await verify(scenario === "discord_batch_notification" ? "batch_retry_verified" : "batch_pending_verified");
      const advanced = await requireTool(callTool, "trigger_sync", {
        run_id: runId, scenario, sync_phase: "advance",
      });
      if (advanced.status !== 200 || advanced.dirty !== true || advanced.run_id !== runId || advanced.execution_status !== "drained") {
        throw new E2eWorkflowError(`${scenario}_advance_failed`);
      }
    }
    await verify(verifiedStage);
  } catch (error) {
    primaryError = error;
  }
  const cleanup = await cleanupServices(callTool, runId, [scenario], options.cleanup);
  if (primaryError) {
    throw primaryError;
  }
  if (!cleanup.ok) {
    throw new E2eWorkflowError("cleanup_run_failed");
  }
  const clean = await requireTool(callTool, "assert_external_state", {
    run_id: runId, service: scenario,
  });
  if (clean.manifest?.outcome !== "passed") {
    throw new E2eWorkflowError(`${scenario}_outcome_failed`);
  }
  return { ok: true, scenarios: [scenario] };
}


export async function runGoogleCalendarCheck(callTool, runId, options = {}) {
  const deployed = await requireTool(callTool, "deploy_e2e", {
    run_id: runId, confirmation: `deploy:ie-event-bot-e2e:${runId}`,
  });
  if (!/^[0-9a-f]{64}$/.test(String(deployed.version_sha256 ?? ""))) {
    throw new E2eWorkflowError("google_sync_version_missing");
  }
  await runPreflight(callTool, runId, options.preflight);
  const result = await requireTool(callTool, "trigger_sync", {
    run_id: runId, scenario: "google_sync", sync_phase: "inspect",
  });
  if (result.status !== 200 || result.dirty !== false || result.run_id !== runId ||
      !["calendar_empty", "calendar_active", "calendar_deleted", "calendar_mixed"].includes(result.execution_status)) {
    throw new E2eWorkflowError("google_sync_inspect_invalid");
  }
  const status = await requireTool(callTool, "read_status", { run_id: runId });
  if (status.worker_version?.tag !== runId || status.worker_version?.id_sha256 !== deployed.version_sha256) {
    throw new E2eWorkflowError("google_sync_version_mismatch");
  }
  return result;
}


export async function runDeployAndGoogleSyncSmoke(callTool, runId, options = {}) {
  const deployed = await requireTool(callTool, "deploy_e2e", {
    run_id: runId, confirmation: `deploy:ie-event-bot-e2e:${runId}`,
  });
  if (!/^[0-9a-f]{64}$/.test(String(deployed.version_sha256 ?? ""))) {
    throw new E2eWorkflowError("google_sync_version_missing");
  }
  await runPreflight(callTool, runId, options.preflight);
  let primaryError = null;
  try {
    const steps = options.httpSync ? ["prepared", "drained", "updated", "drained"] : options.allSync ? ["prepared", "drained", "updated", "drained", "retry_pending", "retried", "retry_pending", "retried", "drained"] : options.matrix ? [...Array(5).fill("prepared"), "pending", "pending", "drained", "updated", "updated", "deleted", "deleted", "retry_pending", "retried", "retry_pending", "pending", "pending", "retried",
      "prepared", "prepared", "pending", "pending", "pending", "drained", "retry_pending", "retried", "retry_pending", "retried"]
      : options.fullApply ? ["pending", "drained", "updated", "deleted"]
      : ["pending", "drained", "updated", "deleted", "retry_pending", "retried"];
    for (const [index, step] of steps.entries()) {
      const written = await requireTool(callTool, "trigger_sync", {
        run_id: runId, scenario: "google_sync", sync_phase: index === 0 ? (options.webhookSync ? "prepare_webhook" : options.httpSync ? "prepare_http" : options.allSync ? "prepare_all" : options.matrix ? "prepare_matrix" : options.fullApply ? "prepare_full" : "prepare") : options.webhookSync ? "webhook_trigger" : options.httpSync ? "http_advance" : "advance",
      });
      if (written.status !== 200 || !written.dirty || written.run_id !== runId || written.execution_status !== step) {
        throw new E2eWorkflowError("google_sync_phase_mismatch");
      }
      let transportFailures = 0;
      for (let attempt = 1; attempt <= STATE_VERIFY_ATTEMPTS; attempt += 1) {
        const verified = await toolOutcome(callTool, "trigger_sync", {
          run_id: runId, scenario: "google_sync", sync_phase: "resume",
        });
        if (verified.ok) {
          if (verified.payload.status !== 200 || !verified.payload.dirty || verified.payload.run_id !== runId || verified.payload.execution_status !== step) {
            throw new E2eWorkflowError("google_sync_verify_mismatch");
          }
          break;
        }
        const transportFailure = (verified.error === "worker_request_failed" && verified.payload.status === 0) ||
          (verified.error === "worker_response_read_failed" && verified.payload.status === 200);
        if (transportFailure) { transportFailures += 1; }
        const retryable = transportFailure ? transportFailures < GOOGLE_VERIFY_TRANSPORT_ATTEMPTS
          : (verified.error === "google_sync_not_ready" && verified.payload.status === 409 && verified.payload.dirty === true) ||
            (options.webhookSync && verified.error === "google_sync_busy");
        // verifyだけを再送する。応答不明のprepare/advanceは再実行しない。
        if (!retryable || verified.payload.run_id !== runId || attempt === STATE_VERIFY_ATTEMPTS) {
          throw new E2eWorkflowError(verified.error);
        }
        await (options.verify?.sleepImpl ?? sleep)(STATE_VERIFY_DELAY_MS);
      }
      const status = await readStatusWithRetry(callTool, runId, options.verify);
      const manifest = status.scenarios?.google_sync;
      if (!manifest?.present || !manifest.dirty || manifest.run_id !== runId || manifest.stage !== "verified" ||
          manifest.stages?.[options.httpSync ? `all_http_step_${index}` : options.allSync ? `all_sync_step_${index}` : options.matrix ? `google_matrix_step_${index}` : `google_sync_${step}`] !== 200 || status.worker_version?.tag !== runId ||
          (options.fullApply && !options.matrix && (manifest.stages?.google_sync_full_input !== 200 ||
            manifest.stages?.google_sync_shared_empty !== 200)) ||
          (!options.httpSync && !options.allSync && !options.matrix && index >= 4 && manifest.stages?.google_sync_discord_failure_injected !== 200) ||
          (!options.httpSync && !options.allSync && !options.matrix && index >= 4 && (manifest.stages?.google_sync_discord_invalid_update !== 400 ||
            manifest.stages?.google_sync_discord_rejection_verified !== 200)) ||
          (options.matrix && (manifest.stages?.google_sync_shared_empty !== 200 ||
            (index >= 5 && manifest.stages?.google_matrix_full_input !== 200) ||
            (index >= 12 && (manifest.stages?.google_matrix_api_rejection !== 400 || manifest.stages?.google_matrix_cursor_preserved !== 200)) ||
            (index >= 14 && (manifest.stages?.google_matrix_multi_cursor_preserved !== 200 ||
              [1, 2, 4].some(slot => manifest.stages?.[`google_matrix_rejection_${slot}`] !== 400))) ||
            ([15, 16, 17, 21, 22, 23, 25, 27].includes(index) && manifest.stages?.[`google_matrix_queue_drain_${index}`] !== 200) ||
            (index >= 20 && (manifest.stages?.google_matrix_pagination !== 200 || manifest.stages?.google_matrix_seven_inputs !== 200)) ||
            (index >= 24 && (manifest.stages?.google_matrix_notion_failure_injected !== 200 || manifest.stages?.google_matrix_notion_cursor_preserved !== 200)) ||
            (index >= 26 && (manifest.stages?.google_matrix_delete_failure_injected !== 200 || manifest.stages?.google_matrix_delete_cursor_preserved !== 200)))) ||
          (options.webhookSync && (manifest.stages?.watch_shared_maintenance !== 200 || manifest.stages?.[`watch_shared_step_${index}`] !== 200 ||
            (index > 0 && (manifest.stages?.[`watch_shared_alarm_${index}`] !== 200 ||
              manifest.stages?.watch_shared_busy_retry_recovered !== 200 || manifest.stages?.watch_shared_failure_retry_recovered !== 200)))) ||
          (options.httpSync && index > 0 && manifest.stages?.[`all_http_dispatch_${index}`] !== 200) ||
          (options.allSync && ((index >= 4 && manifest.stages?.all_sync_failure_4 !== 200) ||
            (index >= 6 && manifest.stages?.all_sync_failure_6 !== 200) ||
            (index >= 8 && (manifest.stages?.all_sync_cooldown !== 200 || manifest.stages?.all_sync_contention !== 200)))) ||
          status.worker_version?.id_sha256 !== deployed.version_sha256) {
        throw new E2eWorkflowError("google_sync_verification_mismatch");
      }
    }
  } catch (error) {
    primaryError = error;
  }
  const cleanup = await cleanupServices(callTool, runId, ["google_sync"], options.cleanup);
  if (primaryError) { throw primaryError; }
  if (!cleanup.ok) { throw new E2eWorkflowError("cleanup_run_failed"); }
  const clean = await requireTool(callTool, "assert_external_state", { run_id: runId, service: "google_sync" });
  if (clean.manifest?.outcome !== "passed" ||
      (options.webhookSync && (clean.manifest?.stages?.watch_shared_cleanup !== 200 || clean.manifest?.stages?.watch_shared_queue_cleanup !== 200)) ||
      ((options.fullApply || options.httpSync) && clean.manifest?.stages?.google_sync_shared_cleanup !== 200) ||
      (options.matrix && clean.manifest?.stages?.google_matrix_series_cleanup !== 200)) {
    throw new E2eWorkflowError("google_sync_outcome_failed");
  }
  return { ok: true, scenarios: ["google_sync"] };
}


export async function runDeployAndSyncFaultsSmoke(callTool, runId, options = {}) {
  return runDiscordKvSmoke(callTool, runId, "sync_faults", "fault_verified", options);
}

export async function runDeployAndSyncLockSmoke(callTool, runId, options = {}) {
  return runDiscordKvSmoke(callTool, runId, "sync_lock", "lock_verified", options);
}

export async function runDeployAndDiscordStateSmoke(callTool, runId, options = {}) {
  return runDiscordKvSmoke(callTool, runId, "discord_state", "state_verified", options);
}

export async function runDeployAndDiscordKvSmoke(callTool, runId, options = {}) {
  return runDiscordKvSmoke(callTool, runId, "discord_kv", "kv_verified", options);
}


export async function runDeployAndDiscordBatchNotificationSmoke(callTool, runId, options = {}) {
  return runDiscordKvSmoke(callTool, runId, "discord_batch_notification", "batch_verified", options);
}


export async function runDeployAndDiscordBatchGoogleSmoke(callTool, runId, options = {}) {
  return runDiscordKvSmoke(callTool, runId, "discord_batch_google", "batch_verified", options);
}


export async function runDeployAndDiscordBatchSmoke(callTool, runId, options = {}) {
  return runDiscordKvSmoke(callTool, runId, "discord_batch", "batch_verified", options);
}


export async function runDeployAndDiscordDeltaSmoke(callTool, runId, options = {}) {
  const initialDeploy = await requireTool(callTool, "deploy_e2e", {
    run_id: runId,
    confirmation: `deploy:ie-event-bot-e2e:${runId}`,
  });
  let version = initialDeploy.version_sha256;
  if (!/^[0-9a-f]{64}$/.test(String(version ?? ""))) {
    throw new E2eWorkflowError("delta_deploy_version_missing");
  }
  await runPreflight(callTool, runId, options.preflight);

  let primaryError = null;
  try {
    await requireTool(callTool, "trigger_sync", {
      run_id: runId,
      scenario: "discord_delta",
      version_sha256: version,
      sync_phase: "prepare",
    });
    const lostUpdate = await toolOutcome(callTool, "trigger_sync", {
      run_id: runId,
      scenario: "discord_delta",
      version_sha256: version,
      sync_phase: "advance",
      response_mode: "discard_after_headers",
    });
    if (lostUpdate.ok || lostUpdate.error !== "worker_response_discarded" ||
        lostUpdate.payload.status !== 200 || lostUpdate.payload.response_discarded !== true ||
        lostUpdate.payload.run_id !== runId) {
      throw new E2eWorkflowError("delta_response_loss_not_observed");
    }
    const redeployed = await requireTool(callTool, "deploy_e2e", {
      run_id: runId,
      confirmation: `deploy:ie-event-bot-e2e:${runId}`,
      previous_version_sha256: version,
    });
    if (!/^[0-9a-f]{64}$/.test(String(redeployed.version_sha256 ?? "")) ||
        redeployed.version_sha256 === version || redeployed.previous_version_sha256 !== version) {
      throw new E2eWorkflowError("delta_redeploy_version_mismatch");
    }
    version = redeployed.version_sha256;
    const updateReplay = await requireTool(callTool, "trigger_sync", {
      run_id: runId,
      scenario: "discord_delta",
      version_sha256: version,
      sync_phase: "advance",
    });
    if (updateReplay.execution_status !== "updated" || updateReplay.dirty !== true) {
      throw new E2eWorkflowError("delta_update_replay_failed");
    }
    // 拒否側が先に終わっても、続行側の完了を待ってからcleanupする。
    const concurrent = await Promise.all([0, 1].map(() => toolOutcome(callTool, "trigger_sync", {
      run_id: runId,
      scenario: "discord_delta",
      version_sha256: version,
      sync_phase: "resume",
    })));
    const completed = concurrent.filter((result) => result.ok && result.payload.status === 200 &&
      result.payload.dirty === false && result.payload.execution_status === null);
    const rejected = concurrent.filter((result) => !result.ok && result.payload.status === 409 &&
      result.error === "e2e_lock_unavailable");
    if (completed.length !== 1 || rejected.length !== 1 ||
        concurrent.some((result) => result.payload.run_id !== runId)) {
      throw new E2eWorkflowError("delta_concurrent_resume_failed");
    }
    const completedReplay = await requireTool(callTool, "trigger_sync", {
      run_id: runId,
      scenario: "discord_delta",
      version_sha256: version,
      sync_phase: "resume",
    });
    if (completedReplay.execution_status !== "already_completed" || completedReplay.dirty !== false) {
      throw new E2eWorkflowError("delta_completed_replay_failed");
    }
    await requireTool(callTool, "assert_external_state", {
      run_id: runId,
      service: "discord_delta",
    });
  } catch (error) {
    primaryError = error;
  }

  const cleanup = await cleanupServices(callTool, runId, ["discord_delta"], {
    attempts: 1,
    sleepImpl: options.cleanup?.sleepImpl,
  });
  if (primaryError) {
    throw primaryError;
  }
  if (!cleanup.ok) {
    throw new E2eWorkflowError("cleanup_run_failed");
  }
  return { ok: true, scenarios: ["discord_delta"] };
}


export async function runDeployAndQaNormalSmoke(callTool, runId, options = {}) {
  await requireTool(callTool, "deploy_e2e", {
    run_id: runId, confirmation: `deploy:ie-event-bot-e2e:${runId}`,
  });
  await runPreflight(callTool, runId, options.preflight);
  const pause = options.sleepImpl ?? sleep;
  let primaryError = null;
  try {
    for (const phase of ["prepare", "first", "update", "notify", "duplicate"]) {
      // Notionの実更新時刻が変わるのを待ち、cache markerは作り替えない。
      if (phase === "update") {
        await pause(65_000);
      }
      const operation = await requireTool(callTool, "trigger_job", {
        run_id: runId, job: `qa_normal_${phase}`,
      });
      if (operation.stages?.[`qa_normal_${phase}`] !== 200) {
        throw new E2eWorkflowError("qa_normal_stage_missing");
      }
      for (let attempt = 0; attempt < STATE_VERIFY_ATTEMPTS; attempt += 1) {
        const verified = await toolOutcome(callTool, "trigger_job", { run_id: runId, job: "qa_normal_verify" });
        if (verified.ok && verified.payload.stages?.[`qa_normal_verify_${phase}`] === 200) {
          break;
        }
        if (attempt === STATE_VERIFY_ATTEMPTS - 1 ||
            !["qa_normal_kv_not_ready", "qa_normal_cache_not_ready", "qa_normal_message_count_failed"].includes(verified.error)) {
          throw new E2eWorkflowError(verified.error ?? "qa_normal_verify_failed");
        }
        await pause(STATE_VERIFY_DELAY_MS);
      }
    }
  } catch (error) {
    primaryError = error;
  }
  const cleanup = await cleanupServices(callTool, runId, ["qa_notification"], options.cleanup);
  if (primaryError) {
    throw primaryError;
  }
  if (!cleanup.ok) {
    throw new E2eWorkflowError("cleanup_run_failed");
  }
  const assertion = await requireTool(callTool, "assert_external_state", {
    run_id: runId, service: "qa_notification",
  });
  if (assertion.manifest?.outcome !== "passed" || assertion.manifest?.stages?.qa_normal_cleanup !== 200) {
    throw new E2eWorkflowError("qa_normal_evidence_missing");
  }
  return { ok: true, scenarios: ["qa_notification"] };
}


export async function runDeployAndReminderNormalSmoke(callTool, runId, options = {}) {
  await requireTool(callTool, "deploy_e2e", {
    run_id: runId, confirmation: `deploy:ie-event-bot-e2e:${runId}`,
  });
  await runPreflight(callTool, runId, options.preflight);
  const pause = options.sleepImpl ?? sleep;
  let primaryError = null;
  try {
    for (const phase of ["prepare", "notify", "duplicate"]) {
      const operation = await requireTool(callTool, "trigger_job", {
        run_id: runId, job: `reminder_normal_${phase}`,
      });
      if (operation.stages?.[`reminder_normal_${phase}`] !== 200) {
        throw new E2eWorkflowError("reminder_normal_stage_missing");
      }
      for (let attempt = 0; attempt < STATE_VERIFY_ATTEMPTS; attempt += 1) {
        const verified = await toolOutcome(callTool, "trigger_job", { run_id: runId, job: "reminder_normal_verify" });
        if (verified.ok && verified.payload.stages?.[`reminder_normal_verify_${phase}`] === 200) {
          break;
        }
        if (attempt === STATE_VERIFY_ATTEMPTS - 1 ||
            !["reminder_normal_kv_not_ready", "reminder_normal_cache_not_ready", "reminder_normal_message_count_failed"].includes(verified.error)) {
          throw new E2eWorkflowError(verified.error ?? "reminder_normal_verify_failed");
        }
        await pause(STATE_VERIFY_DELAY_MS);
      }
    }
  } catch (error) {
    primaryError = error;
  }
  const cleanup = await cleanupServices(callTool, runId, ["reminder"], options.cleanup);
  if (primaryError) {
    throw primaryError;
  }
  if (!cleanup.ok) {
    throw new E2eWorkflowError("cleanup_run_failed");
  }
  const assertion = await requireTool(callTool, "assert_external_state", {
    run_id: runId, service: "reminder",
  });
  if (assertion.manifest?.outcome !== "passed" || assertion.manifest?.stages?.reminder_normal_cleanup !== 200) {
    throw new E2eWorkflowError("reminder_normal_evidence_missing");
  }
  return { ok: true, scenarios: ["reminder"] };
}


export async function runDeployAndNotionCleanupNormalSmoke(callTool, runId, options = {}) {
  await requireTool(callTool, "deploy_e2e", {
    run_id: runId, confirmation: `deploy:ie-event-bot-e2e:${runId}`,
  });
  await runPreflight(callTool, runId, options.preflight);
  const pause = options.sleepImpl ?? sleep;
  let primaryError = null;
  try {
    for (const phase of ["prepare", "execute", "duplicate"]) {
      const operation = await requireTool(callTool, "trigger_job", {
        run_id: runId, job: `cleanup_normal_${phase}`,
      });
      if (operation.stages?.[`cleanup_normal_${phase}`] !== 200) {
        throw new E2eWorkflowError("cleanup_normal_stage_missing");
      }
      for (let attempt = 0; attempt < STATE_VERIFY_ATTEMPTS; attempt += 1) {
        const verified = await toolOutcome(callTool, "trigger_job", { run_id: runId, job: "cleanup_normal_verify" });
        if (verified.ok && verified.payload.stages?.[`cleanup_normal_verify_${phase}`] === 200) {
          break;
        }
        if (attempt === STATE_VERIFY_ATTEMPTS - 1 ||
            !["cleanup_normal_kv_not_ready"].includes(verified.error)) {
          throw new E2eWorkflowError(verified.error ?? "cleanup_normal_verify_failed");
        }
        await pause(STATE_VERIFY_DELAY_MS);
      }
    }
  } catch (error) {
    primaryError = error;
  }
  const cleanup = await cleanupServices(callTool, runId, ["notion_cleanup"], options.cleanup);
  if (primaryError) {
    throw primaryError;
  }
  if (!cleanup.ok) {
    throw new E2eWorkflowError("cleanup_run_failed");
  }
  const assertion = await requireTool(callTool, "assert_external_state", {
    run_id: runId, service: "notion_cleanup",
  });
  if (assertion.manifest?.outcome !== "passed" || assertion.manifest?.stages?.cleanup_normal_cleanup !== 200) {
    throw new E2eWorkflowError("cleanup_normal_evidence_missing");
  }
  return { ok: true, scenarios: ["notion_cleanup"] };
}


export async function runDeployAndQaNotificationSmoke(callTool, runId, options = {}) {
  await requireTool(callTool, "deploy_e2e", {
    run_id: runId,
    confirmation: `deploy:ie-event-bot-e2e:${runId}`,
  });
  await runPreflight(callTool, runId, options.preflight);

  let primaryError = null;
  try {
    await requireTool(callTool, "trigger_job", {
      run_id: runId,
      job: "qa_check",
    });
    await requireTool(callTool, "assert_external_state", {
      run_id: runId,
      service: "qa_notification",
    });
  } catch (error) {
    primaryError = error;
  }

  const cleanup = await cleanupServices(callTool, runId, ["qa_notification"], {
    attempts: 1,
    sleepImpl: options.cleanup?.sleepImpl,
  });
  if (primaryError) {
    throw primaryError;
  }
  if (!cleanup.ok) {
    throw new E2eWorkflowError("cleanup_run_failed");
  }
  return { ok: true, scenarios: ["qa_notification"] };
}


export async function runDeployAndReminderSmoke(callTool, runId, options = {}) {
  await requireTool(callTool, "deploy_e2e", {
    run_id: runId,
    confirmation: `deploy:ie-event-bot-e2e:${runId}`,
  });
  await runPreflight(callTool, runId, options.preflight);

  let primaryError = null;
  try {
    await requireTool(callTool, "trigger_job", {
      run_id: runId,
      job: "reminder",
    });
    await requireTool(callTool, "assert_external_state", {
      run_id: runId,
      service: "reminder",
    });
  } catch (error) {
    primaryError = error;
  }

  const cleanup = await cleanupServices(callTool, runId, ["reminder"], {
    attempts: 1,
    sleepImpl: options.cleanup?.sleepImpl,
  });
  if (primaryError) {
    throw primaryError;
  }
  if (!cleanup.ok) {
    throw new E2eWorkflowError("cleanup_run_failed");
  }
  return { ok: true, scenarios: ["reminder"] };
}


export async function runDeployAndNotionCleanupSmoke(callTool, runId, options = {}) {
  await requireTool(callTool, "deploy_e2e", {
    run_id: runId,
    confirmation: `deploy:ie-event-bot-e2e:${runId}`,
  });
  await runPreflight(callTool, runId, options.preflight);

  let primaryError = null;
  try {
    await requireTool(callTool, "trigger_job", {
      run_id: runId,
      job: "cleanup",
    });
    await requireTool(callTool, "assert_external_state", {
      run_id: runId,
      service: "notion_cleanup",
    });
  } catch (error) {
    primaryError = error;
  }

  const cleanup = await cleanupServices(callTool, runId, ["notion_cleanup"], {
    attempts: 1,
    sleepImpl: options.cleanup?.sleepImpl,
  });
  if (primaryError) {
    throw primaryError;
  }
  if (!cleanup.ok) {
    throw new E2eWorkflowError("cleanup_run_failed");
  }
  return { ok: true, scenarios: ["notion_cleanup"] };
}


export async function runDeployAndWebhookSimulationSmoke(callTool, runId, options = {}) {
  await requireTool(callTool, "deploy_e2e", {
    run_id: runId,
    confirmation: `deploy:ie-event-bot-e2e:${runId}`,
  });
  await runPreflight(callTool, runId, options.preflight);

  let primaryError = null;
  try {
    await requireTool(callTool, "trigger_webhook", {
      run_id: runId,
    });
    await requireTool(callTool, "assert_external_state", {
      run_id: runId,
      service: "webhook_dispatch",
    });
  } catch (error) {
    primaryError = error;
  }

  const cleanup = await cleanupServices(callTool, runId, ["webhook_dispatch"], {
    attempts: 1,
    sleepImpl: options.cleanup?.sleepImpl,
  });
  if (primaryError) {
    throw primaryError;
  }
  if (!cleanup.ok) {
    throw new E2eWorkflowError("cleanup_run_failed");
  }
  return { ok: true, scenarios: ["webhook_dispatch"] };
}


export async function runDeployAndWebhookDeliverySmoke(callTool, runId, options = {}) {
  await requireTool(callTool, "deploy_e2e", {
    run_id: runId,
    confirmation: `deploy:ie-event-bot-e2e:${runId}`,
  });
  await runPreflight(callTool, runId, options.preflight);

  let primaryError = null;
  try {
    await requireTool(callTool, "trigger_webhook_delivery", {
      run_id: runId,
    });
    await requireTool(callTool, "assert_external_state", {
      run_id: runId,
      service: "webhook_delivery",
    });
  } catch (error) {
    primaryError = error;
  }

  const cleanup = await cleanupServices(callTool, runId, ["webhook_delivery"], {
    attempts: 1,
    sleepImpl: options.cleanup?.sleepImpl,
  });
  if (primaryError) {
    throw primaryError;
  }
  if (!cleanup.ok) {
    throw new E2eWorkflowError("cleanup_run_failed");
  }
  return { ok: true, scenarios: ["webhook_delivery"] };
}


export async function runDeployAndWebhookChangeSmoke(callTool, runId, options = {}) {
  await requireTool(callTool, "deploy_e2e", {
    run_id: runId,
    confirmation: `deploy:ie-event-bot-e2e:${runId}`,
  });
  await runPreflight(callTool, runId, options.preflight);

  let primaryError = null;
  try {
    await requireTool(callTool, "trigger_webhook_change", {
      run_id: runId,
    });
    await requireTool(callTool, "assert_external_state", {
      run_id: runId,
      service: "webhook_change",
    });
  } catch (error) {
    primaryError = error;
  }

  const cleanup = await cleanupServices(callTool, runId, ["webhook_change"], {
    attempts: 1,
    sleepImpl: options.cleanup?.sleepImpl,
  });
  if (primaryError) {
    throw primaryError;
  }
  if (!cleanup.ok) {
    throw new E2eWorkflowError("cleanup_run_failed");
  }
  return { ok: true, scenarios: ["webhook_change"] };
}


export function touchedServicesFromAudit(entries, runId) {
  const touched = new Set();
  for (const entry of Array.isArray(entries) ? entries : []) {
    if (
      entry?.run_id === runId &&
      entry.phase === "start" &&
      (
        (entry.tool === "cleanup_run" && entry.target === "discord_delta") ||
        (entry.tool === "seed_fixture" && SERVICES.includes(entry.target)) ||
        (entry.tool === "trigger_sync" && entry.sync_phase !== "inspect" &&
          [
            "discord_google",
            "discord_notion",
            "discord_delta",
            "discord_state",
            "discord_kv",
            "discord_batch",
            "discord_batch_google",
            "discord_batch_notification",
            "sync_lock",
            "sync_faults",
  "google_sync",
            "google_discord",
            "google_notion",
          ].includes(entry.target)) ||
        (entry.tool === "trigger_job" &&
          ["cleanup_normal_prepare", "cleanup_normal_execute", "cleanup_normal_duplicate", "cleanup_normal_verify", "reminder_normal_prepare", "reminder_normal_notify", "reminder_normal_duplicate", "reminder_normal_verify", "qa_check", "reminder", "cleanup", "qa_normal_prepare", "qa_normal_first", "qa_normal_update", "qa_normal_notify", "qa_normal_duplicate", "qa_normal_verify"].includes(entry.target)) ||
        (entry.tool === "trigger_webhook" && entry.target === "webhook_dispatch") ||
        (entry.tool === "trigger_webhook_delivery" &&
          entry.target === "webhook_delivery") ||
        (entry.tool === "trigger_webhook_change" &&
          entry.target === "webhook_change")
      )
    ) {
      const jobService = {
        qa_check: "qa_notification",
        cleanup_normal_prepare: "notion_cleanup",
        cleanup_normal_execute: "notion_cleanup",
        cleanup_normal_duplicate: "notion_cleanup",
        cleanup_normal_verify: "notion_cleanup",
        reminder_normal_prepare: "reminder",
        reminder_normal_notify: "reminder",
        reminder_normal_duplicate: "reminder",
        reminder_normal_verify: "reminder",
        qa_normal_prepare: "qa_notification",
        qa_normal_first: "qa_notification",
        qa_normal_update: "qa_notification",
        qa_normal_notify: "qa_notification",
        qa_normal_duplicate: "qa_notification",
        qa_normal_verify: "qa_notification",

        reminder: "reminder",
        cleanup: "notion_cleanup",
      }[entry.target];
      touched.add(jobService ?? entry.target);
    }
  }
  return CLEANUP_TARGETS.filter((service) => touched.has(service));
}


async function assertSafePath(path, kind) {
  try {
    const stat = await lstat(path);
    if (stat.isSymbolicLink()) {
      throw new E2eWorkflowError("evidence_symlink_forbidden");
    }
    if (kind === "directory" && !stat.isDirectory()) {
      throw new E2eWorkflowError("evidence_directory_invalid");
    }
    if (kind === "file" && !stat.isFile()) {
      throw new E2eWorkflowError("evidence_file_invalid");
    }
  } catch (error) {
    if (error?.code === "ENOENT") {
      return false;
    }
    throw error;
  }
  return true;
}


export function evidencePathForRun(runId) {
  if (!RUN_ID_PATTERN.test(String(runId ?? ""))) {
    throw new E2eWorkflowError("run_id_invalid");
  }
  return resolve(EVIDENCE_DIR, `${runId}-manifest.json`);
}


export async function writeRunManifest(runId, manifest) {
  if (
    !manifest ||
    typeof manifest !== "object" ||
    Array.isArray(manifest) ||
    manifest.run_id !== runId
  ) {
    throw new E2eWorkflowError("evidence_manifest_invalid");
  }
  await mkdir(EVIDENCE_ROOT, { recursive: true, mode: 0o700 });
  await assertSafePath(EVIDENCE_ROOT, "directory");
  await mkdir(EVIDENCE_DIR, { recursive: true, mode: 0o700 });
  await assertSafePath(EVIDENCE_DIR, "directory");
  const outputPath = evidencePathForRun(runId);
  await assertSafePath(outputPath, "file");
  await writeFile(outputPath, `${JSON.stringify(manifest, null, 2)}\n`, {
    encoding: "utf8",
    mode: 0o600,
  });
  return outputPath;
}


function unavailableManifest(runId, error) {
  return {
    version: 1,
    run_id: runId,
    repository: { git_sha: null, dirty: null },
    worker: {
      name: "ie-event-bot-e2e",
      environment: "e2e",
      url_sha256: null,
      version: { present: false, id_sha256: null, timestamp: null },
    },
    started_at: null,
    completed_at: null,
    operations: [],
    cleanup: {},
    services: {},
    scenarios: {},
    watch: { present: false },
    evidence: { ok: false, error },
  };
}


export async function collectAndWriteEvidence(callTool, runId, options = {}) {
  const writeImpl = options.writeImpl ?? writeRunManifest;
  const outcome = await toolOutcome(callTool, "collect_evidence", { run_id: runId });
  const rawManifest = outcome.payload.manifest;
  const hasManifest =
    rawManifest &&
    typeof rawManifest === "object" &&
    !Array.isArray(rawManifest) &&
    rawManifest.run_id === runId;
  const manifest = hasManifest
    ? {
        ...rawManifest,
        evidence: { ok: outcome.ok, error: outcome.error },
      }
    : unavailableManifest(runId, outcome.error);
  await writeImpl(runId, manifest);
  if (!outcome.ok) {
    throw new E2eWorkflowError(outcome.error);
  }
  return manifest;
}


export function e2eCallOptions(name, args) {
  // deploy 300秒 + revision読戻し20回(各60秒) + 待機19回(各3秒) + 応答余裕。
  // SDK既定60秒で呼出しだけが終わり、裏でdeployが継続する状態を避ける。
  if (name === "deploy_e2e") { return { timeout: 1_620_000 }; }
  if (args.scenario === "google_sync" || args.service === "google_sync") {
    return { timeout: 180_000 };
  }
  return undefined;
}


async function withE2eClient(callback) {
  const server = createE2eMcpServer();
  const client = new Client({ name: "ie-event-bot-e2e-workflow", version: "1.0.0" });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  await Promise.all([client.connect(clientTransport), server.connect(serverTransport)]);
  try {
    return await callback(async (name, args) => await client.callTool({
      name,
      arguments: args,
    }, undefined, e2eCallOptions(name, args)));
  } finally {
    await client.close();
    await server.close();
  }
}


async function runCommand(command, runId) {
  return await withE2eClient(async (callTool) => {
    if (command === "preflight") {
      await runPreflight(callTool, runId);
      return;
    }
    if (command === "deploy-and-crud-smoke") {
      await runDeployAndCrudSmoke(callTool, runId);
      return;
    }
    if (command === "deploy-and-discord-google-smoke") {
      await runDeployAndDiscordGoogleSmoke(callTool, runId);
      return;
    }
    if (command === "deploy-and-discord-delta-smoke") {
      await runDeployAndDiscordDeltaSmoke(callTool, runId);
      return;
    }
    if (command === "deploy-and-google-calendar-check") {
      await runGoogleCalendarCheck(callTool, runId);
      return;
    }
    if (command === "deploy-and-watch-shared-smoke") {
      return await runDeployAndGoogleSyncSmoke(callTool, runId, { httpSync: true, webhookSync: true });
    }
    if (command === "deploy-and-all-http-smoke") {
      await runDeployAndGoogleSyncSmoke(callTool, runId, { httpSync: true });
      return;
    }
    if (command === "deploy-and-all-sync-smoke") {
      await runDeployAndGoogleSyncSmoke(callTool, runId, { allSync: true });
      return;
    }
    if (command === "deploy-and-google-matrix-smoke") {
      await runDeployAndGoogleSyncSmoke(callTool, runId, { fullApply: true, matrix: true });
      return;
    }
    if (command === "deploy-and-google-full-smoke") {
      await runDeployAndGoogleSyncSmoke(callTool, runId, { fullApply: true });
      return;
    }
    if (command === "deploy-and-google-sync-smoke") {
      await runDeployAndGoogleSyncSmoke(callTool, runId);
      return;
    }
    if (command === "deploy-and-google-sync-recovery") {
      await runGoogleSyncRecovery(callTool, runId);
      return;
    }
    if (command === "deploy-and-sync-faults-smoke") {
      await runDeployAndSyncFaultsSmoke(callTool, runId);
      return;
    }
    if (command === "deploy-and-sync-lock-smoke") {
      await runDeployAndSyncLockSmoke(callTool, runId);
      return;
    }
    if (command === "deploy-and-discord-batch-notification-smoke") {
      await runDeployAndDiscordBatchNotificationSmoke(callTool, runId);
      return;
    }
    if (command === "deploy-and-discord-batch-google-smoke") {
      await runDeployAndDiscordBatchGoogleSmoke(callTool, runId);
      return;
    }
    if (command === "deploy-and-discord-batch-smoke") {
      await runDeployAndDiscordBatchSmoke(callTool, runId);
      return;
    }
    if (command === "deploy-and-discord-kv-smoke") {
      await runDeployAndDiscordKvSmoke(callTool, runId);
      return;
    }
    if (command === "deploy-and-discord-state-smoke") {
      await runDeployAndDiscordStateSmoke(callTool, runId);
      return;
    }
    if (command === "deploy-and-discord-delta-recovery") {
      await runDiscordDeltaRecovery(callTool, runId);
      return;
    }
    if (command === "deploy-and-discord-notion-smoke") {
      await runDeployAndDiscordNotionSmoke(callTool, runId);
      return;
    }
    if (command === "deploy-and-google-notion-smoke") {
      await runDeployAndGoogleNotionSmoke(callTool, runId);
      return;
    }
    if (command === "deploy-and-google-discord-smoke") {
      await runDeployAndGoogleDiscordSmoke(callTool, runId);
      return;
    }
    if (command === "deploy-and-notion-cleanup-normal-smoke") {
      await runDeployAndNotionCleanupNormalSmoke(callTool, runId);
      return;
    }
    if (command === "deploy-and-reminder-normal-smoke") {
      await runDeployAndReminderNormalSmoke(callTool, runId);
      return;
    }
    if (command === "deploy-and-qa-normal-smoke") {
      await runDeployAndQaNormalSmoke(callTool, runId);
      return;
    }
    if (command === "deploy-and-qa-notification-smoke") {
      await runDeployAndQaNotificationSmoke(callTool, runId);
      return;
    }
    if (command === "deploy-and-reminder-smoke") {
      await runDeployAndReminderSmoke(callTool, runId);
      return;
    }
    if (command === "deploy-and-notion-cleanup-smoke") {
      await runDeployAndNotionCleanupSmoke(callTool, runId);
      return;
    }
    if (command === "deploy-and-webhook-simulation-smoke") {
      await runDeployAndWebhookSimulationSmoke(callTool, runId);
      return;
    }
    if (command === "deploy-and-webhook-delivery-smoke") {
      await runDeployAndWebhookDeliverySmoke(callTool, runId);
      return;
    }
    if (command === "deploy-and-webhook-change-smoke") {
      await runDeployAndWebhookChangeSmoke(callTool, runId);
      return;
    }
    if (command === "cleanup") {
      let entries;
      try {
        entries = await readAuditEntries(runId);
      } catch {
        throw new E2eWorkflowError("audit_read_failed");
      }
      const services = touchedServicesFromAudit(entries, runId);
      const result = await cleanupServices(callTool, runId, services);
      if (!result.ok) {
        throw new E2eWorkflowError("cleanup_run_failed");
      }
      return;
    }
    if (command === "evidence") {
      await collectAndWriteEvidence(callTool, runId);
      return;
    }
    throw new E2eWorkflowError("command_invalid");
  });
}


async function main(argv = process.argv.slice(2)) {
  const { command, runId } = parseArguments(argv);
  if (command === "run-id") {
    process.stdout.write(`${createRunId()}\n`);
    return;
  }
  await runCommand(command, runId);
  process.stdout.write(`e2e_${command.replaceAll("-", "_")}_ok\n`);
}


const isMain =
  process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url;
if (isMain) {
  main().catch((error) => {
    process.stderr.write(`${safeErrorCode(error?.code)}\n`);
    process.exitCode = 1;
  });
}
