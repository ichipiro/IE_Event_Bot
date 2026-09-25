// 2026-09-25のwatch準備・回収だけを調べる。生ログ・URL・例外本文は保存しない。
import { mkdir, writeFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";

const SCRIPT = "ie-event-bot-e2e";
const WINDOWS = [
  ["watch_prepare", "2026-09-25T09:16:35Z", "2026-09-25T09:17:45Z"],
  ["watch_recovery", "2026-09-25T09:22:00Z", "2026-09-25T09:23:30Z"],
];
const CLASSIFIERS = {
  lock_release_failed: /sync_lock_release_failed/i,
  lock_release_recovered: /sync_lock_release_recovered/i,
  lock_release_recovery_failed: /sync_lock_release_recovery_failed/i,
  lock_release_probe_passed: /e2e_lock_release_probe_passed/i,
  release_failed: /google_sync_release_failed/i,
  busy: /google_sync_busy/i,
  storage_timeout: /storage operation exceeded timeout/i,
  object_reset: /durable object.*reset|object.*reset.*code.*updated/i,
  overloaded: /overloaded|too many (?:requests|concurrent|queued)|queued for too long/i,
  subrequest_limit: /too many subrequests|subrequest.*limit/i,
  cpu_limit: /cpu.*(?:limit|exceeded)/i,
  memory_limit: /memory.*(?:limit|exceeded)/i,
  disconnected: /disconnected|broken pipe|network connection lost/i,
  connection_limit: /connection limit|too many (?:open )?connections|response closed due to connection/i,
  rpc_session: /rpc.*(?:session|disconnect)|session.*(?:rpc|disconnect)/i,
  stalled_response: /stalled (?:HTTP )?response|prevent deadlock/i,
  internal_error: /internal error/i,
  data_clone: /DataCloneError|could not be cloned/i,
  awaitable_depth: /sync_state_rpc_awaitable_depth_exceeded/i,
  python_proxy: /JsException|JsProxy|borrowed proxy|PyProxy/i,
  io_context: /different request|different.*I\/O context|I\/O.*context/i,
  request_cancelled: /I\/O.*cancel|request.*cancel|context.*cancel/i,
  kv_rate_limit: /KV.*(?:429|rate limit)|too many writes/i,
};
const number = (value) => typeof value === "number" && Number.isFinite(value) ? value : null;
const pick = (value, allowed) => allowed.includes(value) ? value : "unknown";
const MESSAGE_TERMS = ["promise", "rpc", "stub", "connection", "canceled", "cancelled", "deadlock",
  "garbage", "collect", "response", "body", "timeout", "aborted", "limit", "memory", "deprecated",
  "storage", "request", "discarded", "reset", "rejected", "resolved", "error"];

export function redactApiError(data) {
  const errors = Array.isArray(data?.errors) ? data.errors.slice(0, 10) : [];
  const messages = errors.map((item) => typeof item?.message === "string" ? item.message : "").join("\n");
  const patterns = {
    authentication: /authenticat|invalid.*token|invalid.*credential/i,
    permission: /permission|not authorized|unauthorized|access denied|forbidden/i,
    account: /account/i,
    ip_restriction: /(?:ip|address).*(?:restrict|allow|denied|invalid)/i,
    expired: /expired|expiration/i,
    unsupported: /not supported|unsupported/i,
    observability: /observability/i,
    telemetry: /telemetry/i,
  };
  return {
    codes: errors.map((item) => item?.code).filter((code) => Number.isSafeInteger(code) && code >= 0),
    categories: Object.entries(patterns).filter(([, pattern]) => pattern.test(messages)).map(([key]) => key),
    message_present: messages.length > 0,
  };
}

async function readJson(response) {
  try { return await response.json(); } catch { return null; }
}

async function verifyCredential(account, token, request) {
  const results = [];
  // 所有形態が不明なので両verifyを照会する。片方の拒否だけで無効とは判定しない。
  for (const [kind, path] of [["user", "user/tokens/verify"], ["account", `accounts/${account}/tokens/verify`]]) {
    try {
      const response = await request(`https://api.cloudflare.com/client/v4/${path}`, {
        method: "GET", redirect: "error", signal: AbortSignal.timeout(15000),
        headers: { Authorization: `Bearer ${token}` },
      });
      const data = await readJson(response);
      results.push({ kind, http_status: number(response.status), success: response.ok && data?.success === true,
        token_status: response.ok && data?.success === true ? pick(data?.result?.status, ["active", "disabled", "expired"]) : "unknown",
        ...redactApiError(data) });
    } catch { results.push({ kind, error: "verification_request_failed" }); }
  }
  return results;
}

class DiagnosticHttpError extends Error {
  constructor(status, detail) {
    super(`diagnostic_http_${status}`);
    this.detail = detail;
  }
}

export function failureReport(error) {
  const code = error instanceof Error && /^diagnostic_(?:http_\d{3}|credentials_missing|response_invalid|scope_mismatch|time_mismatch|cursor_invalid)$/.test(error.message)
    ? error.message : "diagnostic_request_failed";
  return { error: code, ...(error instanceof DiagnosticHttpError ? { api_failure: error.detail } : {}) };
}

export function queryBody(label, from, to, offset) {
  return {
    queryId: `google-lock-${label}`, dry: true, view: "events", limit: 2000,
    timeframe: { from: Date.parse(from), to: Date.parse(to) },
    parameters: { datasets: ["cloudflare-workers"], filterCombination: "and",
      filters: [{ key: "$metadata.service", operation: "eq", type: "string", value: SCRIPT }] },
    ...(offset ? { offset, offsetDirection: "next" } : {}),
  };
}

export function redactEvent(event) {
  const meta = event.$metadata ?? {};
  const worker = event.$workers ?? {};
  if (meta.service !== SCRIPT || (worker.scriptName && worker.scriptName !== SCRIPT)) {
    throw new Error("diagnostic_scope_mismatch");
  }
  // 本文は分類器への入力にだけ使い、任意文字列を出力へコピーしない。
  const text = JSON.stringify(event);
  const messages = [meta.message, meta.error, event.message, event.error, event.source?.message]
    .filter((value) => typeof value === "string").join("\n");
  return {
    timestamp: number(event.timestamp), status: number(meta.statusCode),
    duration_ms: number(meta.duration), cpu_ms: number(worker.cpuTimeMs),
    wall_ms: number(worker.wallTimeMs),
    level: pick(meta.level, ["log", "debug", "info", "warn", "error"]),
    event_type: pick(worker.eventType, ["fetch", "rpc", "jsrpc", "alarm", "scheduled"]),
    execution: pick(worker.executionModel, ["durableObject", "stateless"]),
    outcome: pick(worker.outcome, ["ok", "exception", "exceededCpu", "exceededMemory", "canceled", "unknown"]),
    error_present: typeof meta.error === "string" && meta.error.length > 0,
    message_present: messages.length > 0,
    message_terms: MESSAGE_TERMS.filter((term) => new RegExp(`\\b${term}\\b`, "i").test(messages)),
    categories: Object.entries(CLASSIFIERS).filter(([, pattern]) => pattern.test(text)).map(([key]) => key),
  };
}

export function diagnosticWindows(runId, now = Date.now()) {
  if (!runId) { return WINDOWS; }
  const match = /^E2E-(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z-[a-f0-9]{8}$/.exec(runId);
  if (!match) { throw new Error("diagnostic_run_invalid"); }
  const from = Date.parse(`${match[1]}-${match[2]}-${match[3]}T${match[4]}:${match[5]}:${match[6]}Z`);
  if (!Number.isFinite(from) || now < from || now - from > 3600000) {
    throw new Error("diagnostic_run_invalid");
  }
  return [["release_recovery", new Date(from).toISOString(), new Date(now).toISOString()]];
}

export async function diagnose(env, request = fetch) {
  const account = env.CLOUDFLARE_ACCOUNT_ID;
  const token = env.CLOUDFLARE_API_TOKEN;
  if (!/^[a-f0-9]{32}$/.test(account ?? "") || !token) throw new Error("diagnostic_credentials_missing");
  const report = { script: SCRIPT, windows: [] };
  for (const [label, from, to] of diagnosticWindows(env.E2E_DIAGNOSTIC_RUN_ID)) {
    const summary = { label, from, to, events: [], complete: false };
    let offset;
    for (let page = 0; page < 5; page++) {
      const response = await request(`https://api.cloudflare.com/client/v4/accounts/${account}/workers/observability/telemetry/query`, {
        method: "POST", redirect: "error", signal: AbortSignal.timeout(30000),
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify(queryBody(label, from, to, offset)),
      });
      if (!response.ok) {
        const detail = { ...redactApiError(await readJson(response)) };
        if ([401, 403].includes(response.status)) {
          detail.credential_checks = await verifyCredential(account, token, request);
        }
        throw new DiagnosticHttpError(response.status, detail);
      }
      const data = await response.json();
      const events = data.result?.events?.events;
      if (data.success !== true || !Array.isArray(events)) throw new Error("diagnostic_response_invalid");
      for (const event of events) {
        if (!Number.isFinite(event.timestamp) || event.timestamp < Date.parse(from) || event.timestamp > Date.parse(to)) {
          throw new Error("diagnostic_time_mismatch");
        }
        summary.events.push(redactEvent(event));
      }
      if (events.length < 2000) { summary.complete = true; break; }
      const next = events.at(-1)?.$metadata?.id;
      if (typeof next !== "string" || !next || next === offset) throw new Error("diagnostic_cursor_invalid");
      offset = next;
    }
    report.windows.push(summary);
  }
  return report;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  let report;
  try { report = await diagnose(process.env); }
  catch (error) {
    report = failureReport(error);
    process.exitCode = 1;
  }
  await mkdir("test-results/google-lock-diagnostics", { recursive: true });
  await writeFile("test-results/google-lock-diagnostics/report.json", `${JSON.stringify(report, null, 2)}\n`);
  if (process.env.E2E_DIAGNOSTIC_RUN_ID && !report.error) {
    const events = report.windows.flatMap(window => window.events);
    const count = category => events.filter(event => event.categories.includes(category)).length;
    if (!report.windows.every(window => window.complete) || count("lock_release_recovered") < 6 ||
        count("lock_release_recovery_failed") < 2 || count("lock_release_probe_passed") < 8) {
      console.log("release_recovery_log_evidence_incomplete");
      process.exitCode = 1;
    }
  }
  console.log(report.error ?? "google_lock_diagnostics_collected");
}
