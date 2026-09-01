import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { appendFile, lstat, mkdir, readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";


export const RUN_ID_PATTERN = /^E2E-\d{8}T\d{6}Z-[0-9a-f]{8}$/;
export const WORKER_NAME = "ie-event-bot-e2e";
export const TOOL_NAMES = [
  "preflight",
  "deploy_e2e",
  "seed_fixture",
  "trigger_sync",
  "trigger_webhook",
  "trigger_job",
  "read_status",
  "assert_external_state",
  "collect_evidence",
  "cleanup_run",
];

const MODULE_PATH = fileURLToPath(import.meta.url);
const REPO_ROOT = resolve(dirname(MODULE_PATH), "..");
const AUDIT_ROOT = resolve(REPO_ROOT, "test-results");
const AUDIT_DIR = resolve(AUDIT_ROOT, "e2e-mcp");
const MAX_RESPONSE_BYTES = 65_536;
const WORKER_TIMEOUT_MS = 60_000;
const DEPLOY_TIMEOUT_MS = 300_000;

const SERVICE_ROUTES = Object.freeze({
  google: "/admin/e2e/google-crud",
  discord: "/admin/e2e/discord-crud",
  notion: "/admin/e2e/notion-crud",
});
const CLEANUP_ROUTES = Object.freeze({
  google: "/admin/e2e/google-crud/cleanup",
  discord: "/admin/e2e/discord-crud/cleanup",
  notion: "/admin/e2e/notion-crud/cleanup",
});
const JOB_ROUTES = Object.freeze({
  qa_check: "/jobs/qa-check",
  reminder: "/jobs/reminder",
  cleanup: "/jobs/cleanup",
  run_all: "/jobs/run-all",
});
const REQUIRED_ENV_KEYS = [
  "notion_token",
  "notion_internal_db",
  "notion_qa_db",
  "google_calendar_id",
  "discord_token",
  "discord_guild_id",
  "discord_event_channel",
  "discord_event_role",
  "discord_qa_channel",
  "discord_reminder_channel",
  "discord_reminder_role",
];
const LAST_RESULT_KEYS = [
  "sync_all",
  "sync_discord_notion",
  "job_qa_check",
  "job_reminder",
  "job_cleanup",
  "job_run_all",
  "gcal_watch_ensure",
];

const runIdField = z
  .string()
  .regex(RUN_ID_PATTERN)
  .describe("E2E-<UTC timestamp>-<8 lowercase hex>形式のrun ID");
const serviceField = z.enum(["google", "discord", "notion"]);
const jobField = z.enum(["qa_check", "reminder", "cleanup", "run_all"]);


function safeErrorCode(value, fallback = "worker_operation_failed") {
  const text = String(value ?? "").trim();
  return /^[a-z0-9_]{1,96}$/.test(text) ? text : fallback;
}


function toolResult(payload, isError = false) {
  return {
    content: [{ type: "text", text: JSON.stringify(payload) }],
    isError,
  };
}


export function loadE2eEnvironment(env = process.env) {
  const issues = [];
  const rawWorkerUrl = String(env.E2E_WORKER_URL ?? "").trim();
  const expectedWorkerUrlSha256 = String(env.E2E_WORKER_URL_SHA256 ?? "")
    .trim()
    .toLowerCase();
  const internalApiToken = String(env.INTERNAL_API_TOKEN ?? "").trim();
  const cloudflareAccountId = String(env.CLOUDFLARE_ACCOUNT_ID ?? "").trim();
  const cloudflareApiToken = String(env.CLOUDFLARE_API_TOKEN ?? "").trim();
  let workerUrl = "";

  if (!rawWorkerUrl) {
    issues.push("missing_e2e_worker_url");
  } else {
    try {
      const parsed = new URL(rawWorkerUrl);
      const labels = parsed.hostname.toLowerCase().split(".");
      const isDedicatedWorker =
        labels.length >= 4 &&
        labels[0] === WORKER_NAME &&
        labels.at(-2) === "workers" &&
        labels.at(-1) === "dev";
      if (
        parsed.protocol !== "https:" ||
        parsed.username ||
        parsed.password ||
        parsed.pathname !== "/" ||
        parsed.search ||
        parsed.hash ||
        !isDedicatedWorker
      ) {
        issues.push("invalid_e2e_worker_url");
      } else {
        workerUrl = parsed.origin;
      }
    } catch {
      issues.push("invalid_e2e_worker_url");
    }
  }

  if (!expectedWorkerUrlSha256) {
    issues.push("missing_e2e_worker_url_sha256");
  } else if (!/^[0-9a-f]{64}$/.test(expectedWorkerUrlSha256)) {
    issues.push("invalid_e2e_worker_url_sha256");
  } else if (
    workerUrl &&
    createHash("sha256").update(workerUrl).digest("hex") !== expectedWorkerUrlSha256
  ) {
    issues.push("e2e_worker_url_fingerprint_mismatch");
  }

  if (!internalApiToken) {
    issues.push("missing_internal_api_token");
  } else if (/[\r\n]/.test(internalApiToken)) {
    issues.push("invalid_internal_api_token");
  }
  if (cloudflareAccountId && !/^[0-9a-fA-F]{32}$/.test(cloudflareAccountId)) {
    issues.push("invalid_cloudflare_account_id");
  }
  if (/\r|\n/.test(cloudflareApiToken)) {
    issues.push("invalid_cloudflare_api_token");
  }

  return {
    ok: issues.length === 0,
    issues,
    workerUrl,
    workerUrlSha256: expectedWorkerUrlSha256,
    internalApiToken,
    cloudflareAccountId,
    cloudflareApiToken,
  };
}


function publicEnvironmentStatus(config) {
  return {
    worker_url_configured: Boolean(config.workerUrl),
    worker_url_fingerprint_configured: Boolean(config.workerUrlSha256),
    internal_api_token_configured: Boolean(config.internalApiToken),
    cloudflare_account_id_configured: Boolean(config.cloudflareAccountId),
    cloudflare_api_token_configured: Boolean(config.cloudflareApiToken),
    configuration_errors: [...config.issues],
  };
}


async function assertSafeAuditPath(path, kind) {
  try {
    const stat = await lstat(path);
    if (stat.isSymbolicLink()) {
      throw new Error("audit_symlink_forbidden");
    }
    if (kind === "directory" && !stat.isDirectory()) {
      throw new Error("audit_directory_invalid");
    }
    if (kind === "file" && !stat.isFile()) {
      throw new Error("audit_file_invalid");
    }
  } catch (error) {
    if (error && error.code === "ENOENT") {
      return false;
    }
    throw error;
  }
  return true;
}


export async function appendAuditEntry(entry) {
  if (!RUN_ID_PATTERN.test(String(entry.run_id ?? ""))) {
    throw new Error("audit_run_id_invalid");
  }
  await mkdir(AUDIT_ROOT, { recursive: true, mode: 0o700 });
  await assertSafeAuditPath(AUDIT_ROOT, "directory");
  await mkdir(AUDIT_DIR, { recursive: true, mode: 0o700 });
  await assertSafeAuditPath(AUDIT_DIR, "directory");
  const auditPath = resolve(AUDIT_DIR, `${entry.run_id}.jsonl`);
  const existing = await assertSafeAuditPath(auditPath, "file");
  const safeEntry = {
    timestamp: new Date().toISOString(),
    run_id: entry.run_id,
    tool: entry.tool,
    target: entry.target,
    phase: entry.phase,
    ok: Boolean(entry.ok),
    status: Number.isInteger(entry.status) ? entry.status : null,
    error: entry.error ? safeErrorCode(entry.error, "operation_failed") : null,
  };
  const options = {
    encoding: "utf8",
    flag: "a",
  };
  if (!existing) {
    options.mode = 0o600;
  }
  await appendFile(auditPath, `${JSON.stringify(safeEntry)}\n`, options);
}


export async function readAuditEntries(runId) {
  if (!RUN_ID_PATTERN.test(String(runId ?? ""))) {
    throw new Error("audit_run_id_invalid");
  }
  if (!(await assertSafeAuditPath(AUDIT_ROOT, "directory"))) {
    return [];
  }
  if (!(await assertSafeAuditPath(AUDIT_DIR, "directory"))) {
    return [];
  }
  const auditPath = resolve(AUDIT_DIR, `${runId}.jsonl`);
  if (!(await assertSafeAuditPath(auditPath, "file"))) {
    return [];
  }
  const text = await readFile(auditPath, "utf8");
  const entries = [];
  for (const line of text.split("\n")) {
    if (!line) {
      continue;
    }
    try {
      const entry = JSON.parse(line);
      if (
        entry &&
        entry.run_id === runId &&
        TOOL_NAMES.includes(entry.tool) &&
        /^(?:start|finish)$/.test(String(entry.phase ?? "")) &&
        /^[a-z0-9_-]{1,64}$/.test(String(entry.target ?? ""))
      ) {
        entries.push({
          timestamp: /^\d{4}-\d{2}-\d{2}T/.test(String(entry.timestamp ?? ""))
            ? String(entry.timestamp)
            : null,
          run_id: runId,
          tool: entry.tool,
          target: entry.target,
          phase: entry.phase,
          ok: entry.ok === true,
          status: Number.isInteger(entry.status) ? entry.status : null,
          error: entry.error ? safeErrorCode(entry.error, "operation_failed") : null,
        });
      }
    } catch {
      continue;
    }
  }
  return entries.slice(-200);
}


export function deploymentEnvironment(env = process.env) {
  const allowedKeys = [
    "HOME",
    "LOGNAME",
    "PATH",
    "SHELL",
    "TERM",
    "USER",
    "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME",
  ];
  const result = {};
  for (const key of allowedKeys) {
    if (typeof env[key] === "string" && env[key]) {
      result[key] = env[key];
    }
  }
  return result;
}


async function workerRequest(config, route, method, runId, fetchImpl) {
  if (!config.ok) {
    return {
      ok: false,
      status: 0,
      error: "e2e_mcp_configuration_invalid",
      payload: {},
    };
  }
  const headers = {
    Authorization: `Bearer ${config.internalApiToken}`,
    Accept: "application/json",
  };
  if (runId) {
    headers["X-E2E-Run-ID"] = runId;
  }

  let response;
  try {
    response = await fetchImpl(`${config.workerUrl}${route}`, {
      method,
      headers,
      redirect: "error",
      signal: AbortSignal.timeout(WORKER_TIMEOUT_MS),
    });
  } catch {
    return { ok: false, status: 0, error: "worker_request_failed", payload: {} };
  }

  let text = "";
  try {
    text = await response.text();
  } catch {
    return {
      ok: false,
      status: Number(response.status) || 0,
      error: "worker_response_read_failed",
      payload: {},
    };
  }
  if (Buffer.byteLength(text, "utf8") > MAX_RESPONSE_BYTES) {
    return {
      ok: false,
      status: Number(response.status) || 0,
      error: "worker_response_too_large",
      payload: {},
    };
  }

  let payload = {};
  if (text) {
    try {
      const parsed = JSON.parse(text);
      payload = parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
    } catch {
      payload = {};
    }
  }
  const status = Number(response.status) || 0;
  return {
    ok: status >= 200 && status < 300,
    status,
    error: safeErrorCode(payload.error, status ? `worker_http_${status}` : "worker_request_failed"),
    payload,
  };
}


function sanitizeStages(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return {};
  }
  const stages = {};
  for (const [key, status] of Object.entries(value)) {
    if (/^[a-z0-9_]{1,64}$/.test(key) && Number.isInteger(status)) {
      stages[key] = status;
    }
  }
  return stages;
}


function sanitizeFingerprints(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return {};
  }
  const fingerprints = {};
  for (const [key, fingerprint] of Object.entries(value)) {
    const normalized = String(fingerprint ?? "").toLowerCase();
    if (
      /^[a-z][a-z0-9_]{0,62}_sha256$/.test(key) &&
      /^[0-9a-f]{64}$/.test(normalized)
    ) {
      fingerprints[key] = normalized;
    }
  }
  return fingerprints;
}


function sanitizeTimestamp(value) {
  const text = String(value ?? "");
  return /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/.test(text)
    ? text
    : null;
}


function sanitizeOperation(response, runId) {
  const payload = response.payload;
  const cleanup = payload.cleanup && typeof payload.cleanup === "object" ? payload.cleanup : {};
  const executionStatus = /^[a-z_]{1,64}$/.test(String(payload.status ?? ""))
    ? String(payload.status)
    : null;
  const skipped = executionStatus === "cooldown_skip" || executionStatus === "in_progress_skip";
  const ok = response.ok && payload.ok !== false && !skipped;
  return {
    ok,
    status: response.status,
    execution_status: executionStatus,
    run_id: runId,
    dirty: Boolean(payload.dirty),
    stages: sanitizeStages(payload.stages),
    cleanup: {
      ok: cleanup.ok === true,
      attempts: Number.isInteger(cleanup.attempts) ? cleanup.attempts : null,
    },
    error: ok
      ? null
      : skipped
        ? `worker_${executionStatus}`
        : safeErrorCode(payload.error, response.error),
  };
}


function sanitizeManifest(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return { present: false, dirty: false, run_id: null };
  }
  const runId = String(value.run_id ?? "");
  return {
    present: Boolean(value.present),
    dirty: Boolean(value.dirty),
    run_id: RUN_ID_PATTERN.test(runId) ? runId : null,
    outcome: /^[a-z_]{1,40}$/.test(String(value.outcome ?? ""))
      ? String(value.outcome)
      : null,
    stage: /^[a-z_]{1,40}$/.test(String(value.stage ?? ""))
      ? String(value.stage)
      : null,
    cleanup_attempts: Number.isInteger(value.cleanup_attempts)
      ? value.cleanup_attempts
      : null,
    stages: sanitizeStages(value.stages),
    resource_fingerprints: sanitizeFingerprints(value.resource_fingerprints),
  };
}


function sanitizeStatus(response) {
  const payload = response.payload;
  const requiredEnvs = {};
  const rawRequiredEnvs =
    payload.required_envs && typeof payload.required_envs === "object"
      ? payload.required_envs
      : {};
  for (const key of REQUIRED_ENV_KEYS) {
    requiredEnvs[key] = Boolean(rawRequiredEnvs[key]);
  }

  const lastResults = {};
  const rawLastResults =
    payload.last_results && typeof payload.last_results === "object"
      ? payload.last_results
      : {};
  for (const key of LAST_RESULT_KEYS) {
    const value = rawLastResults[key];
    lastResults[key] = {
      present: value !== null && value !== undefined,
      ok: value && typeof value === "object" ? value.ok === true : null,
    };
  }

  const rawServices =
    payload.services && typeof payload.services === "object" ? payload.services : {};
  const services = {};
  for (const service of Object.keys(SERVICE_ROUTES)) {
    services[service] = sanitizeManifest(rawServices[service]);
  }

  const googleAuth =
    payload.google_auth && typeof payload.google_auth === "object" ? payload.google_auth : {};
  const googleCache =
    googleAuth.cache && typeof googleAuth.cache === "object" ? googleAuth.cache : {};
  const syncLock =
    payload.sync_lock && typeof payload.sync_lock === "object" ? payload.sync_lock : {};
  const rawRoutes =
    payload.routes_enabled && typeof payload.routes_enabled === "object"
      ? payload.routes_enabled
      : {};
  const rawWorkerVersion =
    payload.worker_version && typeof payload.worker_version === "object"
      ? payload.worker_version
      : {};
  const rawWatch = payload.watch && typeof payload.watch === "object" ? payload.watch : {};
  const rawLegacy =
    payload.legacy_manifests && typeof payload.legacy_manifests === "object"
      ? payload.legacy_manifests
      : {};

  return {
    ok: response.ok && payload.ok !== false,
    status: response.status,
    mode: /^[a-z0-9_]{1,32}$/.test(String(payload.mode ?? ""))
      ? String(payload.mode)
      : null,
    kv_enabled: Boolean(payload.kv_enabled ?? payload.kv_state_enabled),
    e2e_manifest_enabled: Boolean(payload.e2e_manifest_enabled),
    orchestrated_writes_enabled:
      typeof payload.orchestrated_writes_enabled === "boolean"
        ? payload.orchestrated_writes_enabled
        : null,
    legacy_manifest_check_complete: payload.legacy_manifest_check_complete === true,
    legacy_manifests: Object.fromEntries(
      Object.keys(SERVICE_ROUTES).map((service) => {
        const value =
          rawLegacy[service] && typeof rawLegacy[service] === "object"
            ? rawLegacy[service]
            : {};
        return [
          service,
          {
            present: value.present === true,
            dirty: value.dirty === true,
          },
        ];
      }),
    ),
    routes_enabled: Object.fromEntries(
      Object.keys(SERVICE_ROUTES).map((service) => [service, rawRoutes[service] === true]),
    ),
    required_envs: requiredEnvs,
    google_auth: {
      direct_env: Boolean(googleAuth.direct_env),
      broker_configured: Boolean(googleAuth.broker_configured),
      service_account_json_configured: Boolean(googleAuth.service_account_json_configured),
      cache_present: Boolean(googleCache.present),
      cache_valid: Boolean(googleCache.valid),
    },
    worker_version: {
      present: rawWorkerVersion.present === true,
      id_sha256: /^[0-9a-f]{64}$/.test(String(rawWorkerVersion.id_sha256 ?? ""))
        ? String(rawWorkerVersion.id_sha256)
        : null,
      timestamp: sanitizeTimestamp(rawWorkerVersion.timestamp),
    },
    watch: {
      present: rawWatch.present === true,
      ...sanitizeFingerprints(rawWatch),
    },
    last_results: lastResults,
    sync_lock: {
      enabled: Boolean(syncLock.enabled),
      ok: syncLock.ok === true,
      status: Number.isInteger(syncLock.status) ? syncLock.status : null,
    },
    services,
    error: response.ok ? null : response.error,
  };
}


function preflightFailureCode(config, health, status, checks) {
  if (!config.ok) {
    return "e2e_mcp_configuration_invalid";
  }
  if (!health.ok) {
    return safeErrorCode(health.error, "worker_health_unavailable");
  }
  if (!status.ok) {
    return safeErrorCode(status.error, "worker_status_unavailable");
  }
  const failedCheck = Object.entries(checks)
    .find(([, ready]) => ready !== true)?.[0];
  return failedCheck ? `preflight_${failedCheck}_failed` : "preflight_failed";
}


export async function deployDedicatedWorker(config, spawnImpl = spawn) {
  if (!config.cloudflareAccountId) {
    return { ok: false, status: null, error: "missing_cloudflare_account_id" };
  }
  if (!config.cloudflareApiToken) {
    return { ok: false, status: null, error: "missing_cloudflare_api_token" };
  }
  const wranglerConfig = await readFile(
    resolve(REPO_ROOT, "workers", "wrangler.e2e.jsonc"),
    "utf8",
  );
  if (!new RegExp(`"name"\\s*:\\s*"${WORKER_NAME}"`).test(wranglerConfig)) {
    return { ok: false, status: null, error: "e2e_worker_name_not_fixed" };
  }

  return await new Promise((resolveResult) => {
    const child = spawnImpl(
      "npm",
      ["run", "wrangler", "--", "deploy", "--config", "workers/wrangler.e2e.jsonc"],
      {
        cwd: REPO_ROOT,
        env: {
          ...deploymentEnvironment(),
          CLOUDFLARE_ACCOUNT_ID: config.cloudflareAccountId,
          CLOUDFLARE_API_TOKEN: config.cloudflareApiToken,
        },
        shell: false,
        stdio: "ignore",
      },
    );
    let settled = false;
    const finish = (result) => {
      if (!settled) {
        settled = true;
        clearTimeout(timeout);
        resolveResult(result);
      }
    };
    const timeout = setTimeout(() => {
      child.kill("SIGTERM");
      finish({ ok: false, status: null, error: "wrangler_deploy_timeout" });
    }, DEPLOY_TIMEOUT_MS);
    child.once("error", () => {
      finish({ ok: false, status: null, error: "wrangler_deploy_start_failed" });
    });
    child.once("close", (code) => {
      finish({
        ok: code === 0,
        status: Number.isInteger(code) ? code : null,
        error: code === 0 ? null : "wrangler_deploy_failed",
      });
    });
  });
}


async function runRepositoryCommand(args, spawnImpl = spawn) {
  return await new Promise((resolveResult) => {
    const child = spawnImpl("git", args, {
      cwd: REPO_ROOT,
      env: deploymentEnvironment(),
      shell: false,
      stdio: ["ignore", "pipe", "ignore"],
    });
    let stdout = "";
    let settled = false;
    const finish = (result) => {
      if (!settled) {
        settled = true;
        clearTimeout(timeout);
        resolveResult(result);
      }
    };
    const timeout = setTimeout(() => {
      child.kill("SIGTERM");
      finish({ ok: false, stdout: "" });
    }, 5_000);
    child.stdout?.on("data", (chunk) => {
      stdout += String(chunk);
      if (Buffer.byteLength(stdout, "utf8") > 1_048_576) {
        child.kill("SIGTERM");
        finish({ ok: false, stdout: "" });
      }
    });
    child.once("error", () => finish({ ok: false, stdout: "" }));
    child.once("close", (code) => {
      finish({ ok: code === 0, stdout: code === 0 ? stdout : "" });
    });
  });
}


export async function readRepositoryMetadata(spawnImpl = spawn) {
  const revision = await runRepositoryCommand(["rev-parse", "--verify", "HEAD"], spawnImpl);
  const status = await runRepositoryCommand(
    ["status", "--porcelain=v1", "--untracked-files=normal"],
    spawnImpl,
  );
  const gitSha = String(revision.stdout ?? "").trim().toLowerCase();
  if (!revision.ok || !status.ok || !/^[0-9a-f]{40,64}$/.test(gitSha)) {
    return { git_sha: null, dirty: null };
  }
  return { git_sha: gitSha, dirty: Boolean(String(status.stdout ?? "").trim()) };
}


function operationRoute(tool, target) {
  if (tool === "seed_fixture") {
    return SERVICE_ROUTES[target] ?? null;
  }
  if (tool === "cleanup_run") {
    return CLEANUP_ROUTES[target] ?? null;
  }
  if (tool === "trigger_sync") {
    return "/sync/all";
  }
  if (tool === "trigger_webhook") {
    return "/admin/e2e/trigger-webhook";
  }
  if (tool === "trigger_job") {
    return JOB_ROUTES[target] ?? null;
  }
  return null;
}


function buildRunManifest(runId, status, audit, repository, config) {
  const finished = audit
    .filter((entry) => entry.phase === "finish")
    .map((entry) => ({
      timestamp: sanitizeTimestamp(entry.timestamp),
      tool: entry.tool,
      target: entry.target,
      route: operationRoute(entry.tool, entry.target),
      ok: entry.ok === true,
      status: Number.isInteger(entry.status) ? entry.status : null,
      error: entry.error ? safeErrorCode(entry.error, "operation_failed") : null,
    }));
  const allTimestamps = audit
    .map((entry) => sanitizeTimestamp(entry.timestamp))
    .filter((value) => value !== null)
    .sort();
  const completedTimestamps = finished
    .map((entry) => entry.timestamp)
    .filter((value) => value !== null)
    .sort();
  const cleanup = {};
  for (const operation of finished) {
    if (operation.tool === "cleanup_run" && Object.hasOwn(CLEANUP_ROUTES, operation.target)) {
      cleanup[operation.target] = {
        ok: operation.ok,
        status: operation.status,
        error: operation.error,
      };
    }
  }
  const gitSha = String(repository?.git_sha ?? "").toLowerCase();
  const repositorySummary = {
    git_sha: /^[0-9a-f]{40,64}$/.test(gitSha) ? gitSha : null,
    dirty: typeof repository?.dirty === "boolean" ? repository.dirty : null,
  };
  return {
    version: 1,
    run_id: runId,
    repository: repositorySummary,
    worker: {
      name: WORKER_NAME,
      environment: "e2e",
      url_sha256: config.workerUrl
        ? createHash("sha256").update(config.workerUrl).digest("hex")
        : null,
      version: status.worker_version,
    },
    started_at: allTimestamps.at(0) ?? null,
    completed_at: completedTimestamps.at(-1) ?? null,
    operations: finished,
    cleanup,
    services: status.services,
    watch: status.watch,
  };
}


async function runAudited(auditImpl, entry, operation) {
  try {
    await auditImpl({ ...entry, phase: "start", ok: true, status: null, error: null });
  } catch {
    return { ok: false, status: null, error: "audit_start_failed" };
  }

  let result;
  try {
    result = await operation();
  } catch {
    result = { ok: false, status: null, error: "operation_exception" };
  }
  try {
    await auditImpl({
      ...entry,
      phase: "finish",
      ok: result.ok === true,
      status: result.status,
      error: result.error,
    });
  } catch {
    return { ok: false, status: result.status ?? null, error: "audit_finish_failed" };
  }
  return result;
}


export function createE2eMcpServer(options = {}) {
  const env = options.env ?? process.env;
  const fetchImpl = options.fetchImpl ?? globalThis.fetch;
  const auditImpl = options.auditImpl ?? appendAuditEntry;
  const readAuditImpl = options.readAuditImpl ?? readAuditEntries;
  const deployImpl = options.deployImpl ?? deployDedicatedWorker;
  const repositoryMetadataImpl = options.repositoryMetadataImpl ?? readRepositoryMetadata;
  const config = loadE2eEnvironment(env);
  const server = new McpServer({ name: "ie-event-bot-e2e", version: "1.0.0" });

  server.registerTool(
    "preflight",
    {
      description: "固定E2E Workerの設定、health、認証済み状態を値なしで確認する。",
      inputSchema: { run_id: runIdField },
      annotations: { readOnlyHint: true, idempotentHint: true, openWorldHint: true },
    },
    async ({ run_id: runId }) => {
      const health = await workerRequest(config, "/health", "GET", runId, fetchImpl);
      const e2eStatus = await workerRequest(
        config,
        "/admin/e2e/status",
        "GET",
        runId,
        fetchImpl,
      );
      const status = sanitizeStatus(e2eStatus);
      const checks = {
        configuration: config.ok,
        health:
          health.ok &&
          health.payload.ok === true &&
          health.payload.kv_state_enabled === true,
        mode: status.mode === "e2e",
        kv: status.kv_enabled,
        durable_manifest: status.e2e_manifest_enabled,
        legacy_manifests:
          status.legacy_manifest_check_complete &&
          Object.values(status.legacy_manifests).every((value) => value.present === false),
        clean_manifests: Object.values(status.services).every(
          (manifest) => manifest.dirty === false,
        ),
        unowned_writes_blocked: status.orchestrated_writes_enabled === false,
        routes: Object.values(status.routes_enabled).every((enabled) => enabled === true),
        required_envs: REQUIRED_ENV_KEYS.every((key) => status.required_envs[key] === true),
        google_auth:
          status.google_auth.direct_env ||
          status.google_auth.broker_configured ||
          status.google_auth.service_account_json_configured ||
          status.google_auth.cache_valid,
        sync_lock: status.sync_lock.enabled && status.sync_lock.ok,
        worker_version:
          status.worker_version.present && Boolean(status.worker_version.id_sha256),
      };
      const payload = {
        ok: status.ok && Object.values(checks).every((ready) => ready === true),
        run_id: runId,
        configuration: publicEnvironmentStatus(config),
        health: { ok: health.ok, status: health.status },
        checks,
        e2e_status: status,
      };
      payload.error = payload.ok
        ? null
        : preflightFailureCode(config, health, status, checks);
      return toolResult(payload, !payload.ok);
    },
  );

  server.registerTool(
    "deploy_e2e",
    {
      description: "固定wrangler.e2e.jsoncを使い、ie-event-bot-e2eだけをdeployする。",
      inputSchema: {
        run_id: runIdField,
        confirmation: z.string().describe("deploy:ie-event-bot-e2e:<run ID>の完全一致"),
      },
      annotations: {
        readOnlyHint: false,
        destructiveHint: true,
        idempotentHint: false,
        openWorldHint: true,
      },
    },
    async ({ run_id: runId, confirmation }) => {
      if (confirmation !== `deploy:${WORKER_NAME}:${runId}`) {
        return toolResult(
          { ok: false, run_id: runId, error: "deploy_confirmation_mismatch" },
          true,
        );
      }
      if (!config.ok) {
        return toolResult(
          { ok: false, run_id: runId, error: "e2e_mcp_configuration_invalid" },
          true,
        );
      }
      const result = await runAudited(
        auditImpl,
        { run_id: runId, tool: "deploy_e2e", target: WORKER_NAME },
        async () => await deployImpl(config),
      );
      return toolResult({ ...result, run_id: runId }, !result.ok);
    },
  );

  server.registerTool(
    "seed_fixture",
    {
      description: "選択した専用サービスで自己cleanup型CRUD fixtureを実行する。",
      inputSchema: { run_id: runIdField, service: serviceField },
      annotations: {
        readOnlyHint: false,
        destructiveHint: true,
        idempotentHint: false,
        openWorldHint: true,
      },
    },
    async ({ run_id: runId, service }) => {
      const result = await runAudited(
        auditImpl,
        { run_id: runId, tool: "seed_fixture", target: service },
        async () => {
          const response = await workerRequest(
            config,
            SERVICE_ROUTES[service],
            "POST",
            runId,
            fetchImpl,
          );
          const sanitized = sanitizeOperation(response, runId);
          if (response.payload.run_id !== runId) {
            return {
              ...sanitized,
              ok: false,
              error: "worker_run_id_mismatch",
            };
          }
          return sanitized;
        },
      );
      return toolResult(result, !result.ok);
    },
  );

  server.registerTool(
    "trigger_sync",
    {
      description: "所有権対応後に、固定E2E Workerの全体同期routeを実行する。",
      inputSchema: { run_id: runIdField },
      annotations: {
        readOnlyHint: false,
        destructiveHint: true,
        idempotentHint: false,
        openWorldHint: true,
      },
    },
    async ({ run_id: runId }) => {
      const result = await runAudited(
        auditImpl,
        { run_id: runId, tool: "trigger_sync", target: "sync_all" },
        async () => {
          const response = await workerRequest(config, "/sync/all", "POST", runId, fetchImpl);
          return sanitizeOperation(response, runId);
        },
      );
      return toolResult(result, !result.ok);
    },
  );

  server.registerTool(
    "trigger_webhook",
    {
      description: "所有権対応後に、認証済みwebhook-dispatch simulationを実行する。",
      inputSchema: { run_id: runIdField },
      annotations: {
        readOnlyHint: false,
        destructiveHint: true,
        idempotentHint: false,
        openWorldHint: true,
      },
    },
    async ({ run_id: runId }) => {
      const result = await runAudited(
        auditImpl,
        { run_id: runId, tool: "trigger_webhook", target: "webhook_dispatch" },
        async () => {
          const response = await workerRequest(
            config,
            "/admin/e2e/trigger-webhook",
            "POST",
            runId,
            fetchImpl,
          );
          return sanitizeOperation(response, runId);
        },
      );
      return toolResult(result, !result.ok);
    },
  );

  server.registerTool(
    "trigger_job",
    {
      description: "所有権対応後に、固定allowlistからE2E Workerのjobを1つ実行する。",
      inputSchema: { run_id: runIdField, job: jobField },
      annotations: {
        readOnlyHint: false,
        destructiveHint: true,
        idempotentHint: false,
        openWorldHint: true,
      },
    },
    async ({ run_id: runId, job }) => {
      const result = await runAudited(
        auditImpl,
        { run_id: runId, tool: "trigger_job", target: job },
        async () => {
          const response = await workerRequest(
            config,
            JOB_ROUTES[job],
            "POST",
            runId,
            fetchImpl,
          );
          return sanitizeOperation(response, runId);
        },
      );
      return toolResult(result, !result.ok);
    },
  );

  server.registerTool(
    "read_status",
    {
      description: "固定E2E Workerのマスク済み状態だけを読む。",
      inputSchema: { run_id: runIdField },
      annotations: { readOnlyHint: true, idempotentHint: true, openWorldHint: true },
    },
    async ({ run_id: runId }) => {
      const response = await workerRequest(
        config,
        "/admin/e2e/status",
        "GET",
        runId,
        fetchImpl,
      );
      const result = { ...sanitizeStatus(response), run_id: runId };
      return toolResult(result, !result.ok);
    },
  );

  server.registerTool(
    "assert_external_state",
    {
      description: "指定serviceの直近manifestが同じrun IDでcleanかを確認する。",
      inputSchema: { run_id: runIdField, service: serviceField },
      annotations: { readOnlyHint: true, idempotentHint: true, openWorldHint: true },
    },
    async ({ run_id: runId, service }) => {
      const response = await workerRequest(
        config,
        "/admin/e2e/status",
        "GET",
        runId,
        fetchImpl,
      );
      const status = sanitizeStatus(response);
      const manifest = status.services[service];
      const ok =
        status.ok && manifest.present && !manifest.dirty && manifest.run_id === runId;
      return toolResult(
        {
          ok,
          run_id: runId,
          service,
          manifest,
          error: ok ? null : "external_state_assertion_failed",
        },
        !ok,
      );
    },
  );

  server.registerTool(
    "collect_evidence",
    {
      description: "run IDのマスク済みWorker状態とローカル監査要約を集める。",
      inputSchema: { run_id: runIdField },
      annotations: { readOnlyHint: true, idempotentHint: true, openWorldHint: true },
    },
    async ({ run_id: runId }) => {
      const response = await workerRequest(
        config,
        "/admin/e2e/status",
        "GET",
        runId,
        fetchImpl,
      );
      let audit = [];
      try {
        audit = await readAuditImpl(runId);
      } catch {
        return toolResult(
          { ok: false, run_id: runId, error: "audit_read_failed" },
          true,
        );
      }
      let repository;
      try {
        repository = await repositoryMetadataImpl();
      } catch {
        return toolResult(
          { ok: false, run_id: runId, error: "repository_metadata_read_failed" },
          true,
        );
      }
      const status = sanitizeStatus(response);
      const manifest = buildRunManifest(runId, status, audit, repository, config);
      const repositoryReady =
        Boolean(manifest.repository.git_sha) &&
        typeof manifest.repository.dirty === "boolean";
      const result = {
        ok: status.ok && repositoryReady,
        run_id: runId,
        manifest,
        error: status.ok
          ? repositoryReady
            ? null
            : "repository_metadata_unavailable"
          : safeErrorCode(status.error, "worker_status_unavailable"),
      };
      return toolResult(result, !result.ok);
    },
  );

  server.registerTool(
    "cleanup_run",
    {
      description: "manifestとrun IDが一致するserviceだけをcleanupする。",
      inputSchema: {
        run_id: runIdField,
        service: serviceField,
        confirmation: z.string().describe("cleanup:<service>:<run ID>の完全一致"),
      },
      annotations: {
        readOnlyHint: false,
        destructiveHint: true,
        idempotentHint: true,
        openWorldHint: true,
      },
    },
    async ({ run_id: runId, service, confirmation }) => {
      if (confirmation !== `cleanup:${service}:${runId}`) {
        return toolResult(
          { ok: false, run_id: runId, service, error: "cleanup_confirmation_mismatch" },
          true,
        );
      }
      const result = await runAudited(
        auditImpl,
        { run_id: runId, tool: "cleanup_run", target: service },
        async () => {
          const response = await workerRequest(
            config,
            CLEANUP_ROUTES[service],
            "POST",
            runId,
            fetchImpl,
          );
          return sanitizeOperation(response, runId);
        },
      );
      return toolResult(result, !result.ok);
    },
  );

  return server;
}


async function main() {
  const server = createE2eMcpServer();
  await server.connect(new StdioServerTransport());
}


const isMain =
  process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url;
if (isMain) {
  main().catch(() => {
    process.stderr.write("e2e_mcp_startup_failed\n");
    process.exitCode = 1;
  });
}
