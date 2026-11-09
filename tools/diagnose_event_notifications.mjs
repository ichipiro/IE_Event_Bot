// 固定本番Workerの読み取り専用診断。任意の本文・URL・識別子・Secretは保存しない。
import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";
import { redactApiError } from "./diagnose_google_lock.mjs";

const SCRIPT = "ie-event-bot";
const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;
const SOURCES = ["api", "wrangler", "terraform", "dash", "cf_cli", "dash_template", "integration",
  "quick_editor", "playground", "workersci"];
const ROUTES = ["/sync/all", "/gcal/sync", "/sync/discord-notion", "/gcal/webhook",
  "/jobs/qa-check", "/jobs/reminder", "/jobs/cleanup", "/jobs/run-all", "/admin/gcal/watch/ensure",
  "/health", "/status"];
const BOOL_FLAGS = ["SYNC_ALL_INCLUDE_DISCORD_NOTION", "CRON_ENABLE_SYNC", "CRON_ENABLE_DISCORD_NOTION_SYNC",
  "SYNC_DO_LOCK_ENABLED", "KV_SYNC_COOLDOWN_ENABLED"];
const NUMBER_FLAGS = { SYNC_DO_LOCK_TTL_SECONDS: [10, 86400], SYNC_INTERVAL_SECONDS: [0, 86400],
  DISCORD_NOTION_MAX_CHANGES_PER_RUN: [1, 10000] };
const CATEGORIES = {
  cpu_limit: /exceededCpu|cpu.*(?:limit|exceeded)/i,
  memory_limit: /exceededMemory|memory.*(?:limit|exceeded)/i,
  kv_rate_limit: /KV.*(?:429|rate limit)|too many writes/i,
  storage_timeout: /storage operation exceeded timeout/i,
  subrequest_limit: /too many subrequests|subrequest.*limit/i,
  lock_lost: /sync_lock_lost|SyncLockLost/i,
  lock_busy: /sync_in_progress|in_progress_skip|google_sync_busy/i,
  lock_release_failed: /sync_lock_release_failed|google_sync_release_failed/i,
  timeout: /timeout|timed out/i,
  exception: /exception|Traceback/i,
  notification_failed: /create_notify_failed/i,
  upsert_failed: /upsert_failed/i,
  state_write_failed: /JobStateWriteError|state_write_failed|kv_write_failed/i,
};
const pick = (value, allowed) => allowed.includes(value) ? value : "unknown";
const numeric = value => typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
const digest = value => createHash("sha256").update(value).digest("hex");
const object = value => value !== null && typeof value === "object" && !Array.isArray(value);

function utcTime(value) {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$/.test(value)) return null;
  const stamp = Date.parse(value);
  if (!Number.isFinite(stamp) || new Date(stamp).toISOString().slice(0, 19) !== value.slice(0, 19)) return null;
  return new Date(stamp).toISOString();
}

export function diagnosticWindow(env, now = Date.now()) {
  const inputTime = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?Z$/;
  const from = utcTime(env.START_UTC);
  const to = utcTime(env.END_UTC);
  if (!inputTime.test(env.START_UTC ?? "") || !inputTime.test(env.END_UTC ?? "") ||
      !from || !to || !Number.isFinite(now) || Date.parse(from) >= Date.parse(to) ||
      Date.parse(to) > now || Date.parse(to) - Date.parse(from) > 3600000) {
    throw new Error("event_diagnostic_window_invalid");
  }
  return { from, to };
}

class ApiError extends Error {
  constructor(status, data) {
    super(`event_diagnostic_http_${status}`);
    this.status = status;
    this.detail = redactApiError(data);
  }
}

export function failureReport(error) {
  const code = error instanceof Error && /^event_diagnostic_(?:http_\d{3}|credentials_missing|window_invalid|arguments_invalid|response_invalid|scope_mismatch|time_mismatch|cursor_invalid)$/.test(error.message)
    ? error.message : "event_diagnostic_request_failed";
  return { error: code, ...(error instanceof ApiError ? { http_status: error.status, api_failure: error.detail } : {}) };
}

async function readApi(url, options, request) {
  const response = await request(url, { ...options, redirect: "error", signal: AbortSignal.timeout(30000) });
  let data;
  try { data = await response.json(); } catch { data = null; }
  if (!response.ok) throw new ApiError(response.status, data);
  if (!object(data) || data.success !== true) throw new Error("event_diagnostic_response_invalid");
  return { http_status: response.status, data };
}

async function capture(action) {
  try { return await action(); } catch (error) { return failureReport(error); }
}

function redactSettings(result) {
  if (!object(result) || (result.bindings !== undefined && !Array.isArray(result.bindings))) {
    throw new Error("event_diagnostic_response_invalid");
  }
  const bindings = result.bindings ?? [];
  const binding = (name, type) => {
    const matches = bindings.filter(item => item?.name === name && item?.type === type);
    return matches.length === 1 ? matches[0] : null;
  };
  const namespaceHash = value => typeof value === "string" && (/^[a-f0-9]{32}$/i.test(value) || UUID.test(value))
    ? digest(value.toLowerCase()) : null;
  const flags = {};
  for (const name of BOOL_FLAGS) {
    const text = binding(name, "plain_text")?.text;
    flags[name] = ["true", "1", "yes", "on"].includes(text) ? true :
      ["false", "0", "no", "off"].includes(text) ? false : null;
  }
  for (const [name, [minimum, maximum]] of Object.entries(NUMBER_FLAGS)) {
    const text = binding(name, "plain_text")?.text;
    const value = typeof text === "string" && /^\d+(?:\.\d+)?$/.test(text) ? Number(text) : NaN;
    flags[name] = Number.isFinite(value) && value >= minimum && value <= maximum ? value : null;
  }
  const date = result.compatibility_date;
  const sampling = numeric(result.observability?.head_sampling_rate);
  return {
    compatibility_date: typeof date === "string" && /^\d{4}-\d{2}-\d{2}$/.test(date) && utcTime(`${date}T00:00:00Z`) ? date : null,
    cpu_ms: Number.isSafeInteger(result.limits?.cpu_ms) && result.limits.cpu_ms >= 0 ? result.limits.cpu_ms : null,
    usage_model: pick(result.usage_model, ["standard", "bundled", "unbound"]),
    state_kv_sha256: namespaceHash(binding("STATE_KV", "kv_namespace")?.namespace_id),
    sync_coordinator_sha256: namespaceHash(binding("SYNC_COORDINATOR", "durable_object_namespace")?.namespace_id),
    flags,
    observability: { enabled: typeof result.observability?.enabled === "boolean" ? result.observability.enabled : null,
      head_sampling_rate: sampling !== null && sampling <= 1 ? sampling : null },
  };
}

function redactDeployment(item) {
  if (!object(item) || !UUID.test(item.id ?? "") || !utcTime(item.created_on) ||
      !Array.isArray(item.versions) || !item.versions.length || item.versions.length > 10 ||
      item.versions.some(v => !UUID.test(v?.version_id ?? "") || numeric(v?.percentage) === null || v.percentage > 100) ||
      Math.abs(item.versions.reduce((sum, v) => sum + v.percentage, 0) - 100) > 0.0001) {
    throw new Error("event_diagnostic_response_invalid");
  }
  return { deployment_sha256: digest(item.id.toLowerCase()), created_on: utcTime(item.created_on),
    source: pick(item.source, SOURCES), versions: item.versions.map(v => ({
      version_sha256: digest(v.version_id.toLowerCase()), percentage: v.percentage })) };
}

function redactVersion(result, expected) {
  if (!object(result) || !UUID.test(result.id ?? "") || result.id.toLowerCase() !== expected.toLowerCase()) {
    throw new Error("event_diagnostic_scope_mismatch");
  }
  const candidates = [result.annotations?.["workers/tag"], result.annotations?.["workers/commit-sha"],
    result.annotations?.["workers/ci/commit-hash"], result.metadata?.git_sha, result.metadata?.commit_sha];
  const sha = candidates.find(value => typeof value === "string" && /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/i.test(value));
  return { version_sha256: digest(result.id.toLowerCase()), created_on: utcTime(result.metadata?.created_on),
    source: pick(result.metadata?.source, SOURCES), git_sha: sha?.toLowerCase() ?? null };
}

function eventRoute(event, worker) {
  for (const value of [worker.event?.request?.url, event.source?.request?.url, event.source?.url]) {
    if (typeof value !== "string") continue;
    try { return pick(new URL(value).pathname, ROUTES); } catch { /* 不正URLを出力しない。 */ }
  }
  return "unknown";
}

export function redactProductionEvent(event) {
  const meta = event?.$metadata ?? {};
  const worker = event?.$workers ?? {};
  if (meta.service !== SCRIPT || (worker.scriptName && worker.scriptName !== SCRIPT)) {
    throw new Error("event_diagnostic_scope_mismatch");
  }
  // 数値欄のcpuTimeMs等をエラー本文と取り違えない。本文はコピーしない。
  const text = [meta.message, meta.error, event.message, event.error, event.source?.message, worker.outcome]
    .filter(value => typeof value === "string").join("\n");
  return { timestamp: numeric(event.timestamp), status: numeric(meta.statusCode),
    event_type: pick(worker.eventType, ["fetch", "rpc", "jsrpc", "alarm", "scheduled"]),
    outcome: pick(worker.outcome, ["ok", "exception", "exceededCpu", "exceededMemory", "canceled", "unknown"]),
    cpu_ms: numeric(worker.cpuTimeMs), wall_ms: numeric(worker.wallTimeMs),
    route: eventRoute(event, worker),
    categories: Object.entries(CATEGORIES).filter(([, pattern]) => pattern.test(text)).map(([key]) => key) };
}

function telemetryBody(window, offset) {
  return { queryId: "event-notification-diagnostics", dry: true, view: "events", limit: 2000,
    timeframe: { from: Date.parse(window.from), to: Date.parse(window.to) },
    parameters: { datasets: ["cloudflare-workers"], filterCombination: "and",
      filters: [{ key: "$metadata.service", operation: "eq", type: "string", value: SCRIPT }] },
    ...(offset ? { offset, offsetDirection: "next" } : {}) };
}

async function collectTelemetry(base, headers, window, request) {
  const summary = { events: [], complete: false };
  let offset;
  try {
    for (let page = 0; page < 5; page++) {
      const { data } = await readApi(`${base}/workers/observability/telemetry/query`, {
        method: "POST", headers: { ...headers, "Content-Type": "application/json" },
        body: JSON.stringify(telemetryBody(window, offset)),
      }, request);
      const events = data.result?.events?.events;
      if (!Array.isArray(events) || events.length > 2000) throw new Error("event_diagnostic_response_invalid");
      for (const event of events) {
        if (!Number.isFinite(event?.timestamp) || event.timestamp < Date.parse(window.from) || event.timestamp > Date.parse(window.to)) {
          throw new Error("event_diagnostic_time_mismatch");
        }
        summary.events.push(redactProductionEvent(event));
      }
      if (events.length < 2000) { summary.complete = true; break; }
      const next = events.at(-1)?.$metadata?.id;
      if (typeof next !== "string" || !next || next.length > 256 || next === offset) throw new Error("event_diagnostic_cursor_invalid");
      offset = next;
    }
    return summary;
  } catch (error) { return { ...summary, ...failureReport(error) }; }
}

export async function diagnoseNotifications(env, request = fetch, now = Date.now()) {
  const window = diagnosticWindow(env, now);
  const account = env.CLOUDFLARE_ACCOUNT_ID;
  const token = env.CLOUDFLARE_API_TOKEN;
  if (!/^[a-f0-9]{32}$/.test(account ?? "") || typeof token !== "string" || !token.trim()) {
    throw new Error("event_diagnostic_credentials_missing");
  }
  const base = `https://api.cloudflare.com/client/v4/accounts/${account}`;
  const scriptBase = `${base}/workers/scripts/${SCRIPT}`;
  const headers = { Authorization: `Bearer ${token}` };
  const get = path => readApi(`${scriptBase}/${path}`, { method: "GET", headers }, request);
  const settings = await capture(async () => {
    const { data, http_status } = await get("settings");
    return { http_status, ...redactSettings(data.result) };
  });
  let active = [];
  const deployments = await capture(async () => {
    const { data, http_status } = await get("deployments?per_page=100");
    const items = data.result?.deployments;
    if (!Array.isArray(items) || items.length > 100) throw new Error("event_diagnostic_response_invalid");
    const redacted = items.map(redactDeployment);
    // 公式APIでは先頭が現在trafficを受けるdeployment。IDは照会中だけ保持する。
    active = items[0]?.versions.filter(v => v.percentage > 0).map(v => v.version_id) ?? [];
    return { http_status, items: redacted, complete: data.result_info?.total_pages !== undefined
      ? data.result_info.total_pages === 1 : items.length < 100 };
  });
  const active_versions = [];
  for (const id of new Set(active)) {
    active_versions.push(await capture(async () => {
      const { data, http_status } = await get(`versions/${id}`);
      return { http_status, ...redactVersion(data.result, id) };
    }));
  }
  const telemetry = await collectTelemetry(base, headers, window, request);
  return { script: SCRIPT, observed_at: new Date(now).toISOString(), window, settings, deployments, active_versions, telemetry,
    ok: !settings.error && !deployments.error && deployments.complete === true && active_versions.length > 0 &&
      active_versions.every(v => !v.error) && !telemetry.error && telemetry.complete };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  let report;
  try {
    if (process.argv.length !== 2) throw new Error("event_diagnostic_arguments_invalid");
    report = await diagnoseNotifications(process.env);
    if (!report.ok) process.exitCode = 1;
  } catch (error) { report = failureReport(error); process.exitCode = 1; }
  await mkdir("test-results/event-notification-diagnostics", { recursive: true });
  await writeFile("test-results/event-notification-diagnostics/report.json", `${JSON.stringify(report, null, 2)}\n`);
  console.log(report.error ?? (report.ok ? "event_notification_diagnostics_collected" : "event_notification_diagnostics_incomplete"));
}
