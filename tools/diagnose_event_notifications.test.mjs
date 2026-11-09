import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import * as diagnostic from "./diagnose_event_notifications.mjs";

const NOW = Date.parse("2026-11-09T15:00:00Z");
const ENV = { CLOUDFLARE_ACCOUNT_ID: "a".repeat(32), CLOUDFLARE_API_TOKEN: "private-token",
  START_UTC: "2026-11-09T04:25:00Z", END_UTC: "2026-11-09T04:50:00Z" };
const VERSION = "11111111-2222-4333-8444-555555555555";
const DEPLOYMENT = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee";
const hash = value => createHash("sha256").update(value).digest("hex");
const response = (result, extra = {}) => ({ ok: true, status: 200,
  json: async () => ({ success: true, result, ...extra }) });

function event(timestamp = Date.parse("2026-11-09T04:36:00Z")) {
  return { timestamp, $metadata: { service: "ie-event-bot", statusCode: 500, error: "private-message" },
    $workers: { scriptName: "ie-event-bot", eventType: "fetch", outcome: "exception", cpuTimeMs: 12,
      wallTimeMs: 300, event: { request: { url: "https://private-host/sync/all?token=private-query" } } },
    source: { message: "private-body", authorization: "private-token", author: "private-author" } };
}

function settings() {
  return { compatibility_date: "2026-03-07", limits: { cpu_ms: 30000 }, usage_model: "standard",
    observability: { enabled: true, head_sampling_rate: 0.25 },
    bindings: [
      { name: "STATE_KV", type: "kv_namespace", namespace_id: "b".repeat(32) },
      { name: "SYNC_COORDINATOR", type: "durable_object_namespace", namespace_id: "c".repeat(32) },
      { name: "CRON_ENABLE_SYNC", type: "plain_text", text: "true" },
      { name: "SYNC_DO_LOCK_TTL_SECONDS", type: "plain_text", text: "120" },
      { name: "DISCORD_TOKEN", type: "secret_text", text: "private-token" },
      { name: "OTHER", type: "plain_text", text: "private-text" },
    ] };
}

function deployment() {
  return { id: DEPLOYMENT, created_on: "2026-11-09T01:00:00.123456Z", source: "wrangler",
    versions: [{ version_id: VERSION, percentage: 100 }], author_email: "private-author",
    annotations: { "workers/message": "private-message" } };
}

function version() {
  return { id: VERSION, metadata: { created_on: "2026-11-09T01:00:00Z", source: "wrangler",
    author_email: "private-author", author_id: "private-author-id" },
    annotations: { "workers/tag": "d".repeat(40), "workers/message": "private-message" },
    resources: { bindings: [{ name: "private-binding", text: "private-text" }] } };
}

function requestStub(calls, overrides = {}) {
  return async (url, options) => {
    calls.push({ url, options });
    if (url.endsWith("/settings")) return (overrides.settings ?? response(settings()));
    if (url.includes("/deployments?")) return (overrides.deployments ?? response({ deployments: [deployment()] },
      { result_info: { page: 1, total_pages: 1 } }));
    if (url.endsWith(`/versions/${VERSION}`)) return (overrides.version ?? response(version()));
    if (url.endsWith("/telemetry/query")) return (overrides.telemetry ?? response({ events: { events: [event()] } }));
    throw new Error("unexpected_request_private-value");
  };
}

test("UTC入力を暦・過去・順序・60分上限で検証し、不正入力では通信しない", async () => {
  const cases = [
    { START_UTC: undefined }, { END_UTC: undefined },
    { START_UTC: "2026-02-30T04:25:00Z" }, { START_UTC: "2026-11-09T13:25:00+09:00" },
    { END_UTC: ENV.START_UTC }, { END_UTC: "2026-11-09T04:24:59Z" },
    { END_UTC: "2026-11-09T05:25:00.001Z" }, { END_UTC: "2026-11-10T04:50:00Z" },
    { END_UTC: "2026-11-09T05:25:00.000001Z" },
  ];
  for (const values of cases) {
    let calls = 0;
    await assert.rejects(diagnostic.diagnoseNotifications({ ...ENV, ...values }, async () => { calls++; }, NOW),
      /event_diagnostic_window_invalid/);
    assert.equal(calls, 0);
  }
  assert.deepEqual(diagnostic.diagnosticWindow(ENV, NOW),
    { from: "2026-11-09T04:25:00.000Z", to: "2026-11-09T04:50:00.000Z" });
  assert.equal(diagnostic.diagnosticWindow({ ...ENV, END_UTC: "2026-11-09T05:25:00Z" }, NOW).to,
    "2026-11-09T05:25:00.000Z");
});

test("固定prodだけにmetadata GETと保存しないtelemetry POSTを行う", async () => {
  const calls = [];
  const report = await diagnostic.diagnoseNotifications({ ...ENV, SCRIPT_NAME: "private-foreign" }, requestStub(calls), NOW);
  assert.equal(report.script, "ie-event-bot");
  assert.equal(report.ok, true);
  assert.equal(calls.length, 4);
  for (const { url, options } of calls) {
    assert.match(url, /^https:\/\/api\.cloudflare\.com\/client\/v4\/accounts\/a{32}\/workers\//);
    assert.equal(options.redirect, "error");
    assert.equal(options.headers.Authorization, "Bearer private-token");
    if (url.endsWith("/telemetry/query")) {
      assert.equal(options.method, "POST");
      const body = JSON.parse(options.body);
      assert.equal(body.dry, true);
      assert.deepEqual(body.timeframe, { from: Date.parse(ENV.START_UTC), to: Date.parse(ENV.END_UTC) });
      assert.deepEqual(body.parameters.filters, [{ key: "$metadata.service", operation: "eq", type: "string", value: "ie-event-bot" }]);
    } else {
      assert.equal(options.method, "GET");
      assert.equal(options.body, undefined);
      assert.match(url, /\/scripts\/ie-event-bot\/(settings|deployments\?per_page=100|versions\/[a-f0-9-]{36})$/);
    }
  }
  const saved = JSON.stringify(report);
  for (const forbidden of ["private-", ENV.CLOUDFLARE_ACCOUNT_ID, VERSION, DEPLOYMENT, "b".repeat(32), "c".repeat(32)]) {
    assert.ok(!saved.includes(forbidden));
  }
  assert.equal(report.settings.state_kv_sha256, hash("b".repeat(32)));
  assert.equal(report.settings.sync_coordinator_sha256, hash("c".repeat(32)));
  assert.equal(report.settings.flags.CRON_ENABLE_SYNC, true);
  assert.equal(report.settings.flags.SYNC_DO_LOCK_TTL_SECONDS, 120);
  assert.deepEqual(report.settings.observability, { enabled: true, head_sampling_rate: 0.25 });
  assert.equal(report.deployments.items[0].versions[0].version_sha256, hash(VERSION));
  assert.equal(report.active_versions[0].git_sha, "d".repeat(40));
  assert.equal(report.active_versions[0].version_sha256, hash(VERSION));
  assert.equal(report.telemetry.complete, true);
});

test("prodイベントは数値・固定route・有限分類だけを返す", () => {
  const raw = event();
  raw.source.message = "KV 429 storage operation exceeded timeout: sync_lock_lost too many subrequests private-value";
  const result = diagnostic.redactProductionEvent(raw);
  assert.equal(result.route, "/sync/all");
  assert.deepEqual(result.categories, ["kv_rate_limit", "storage_timeout", "subrequest_limit", "lock_lost", "timeout", "exception"]);
  assert.equal(result.cpu_ms, 12);
  assert.ok(!JSON.stringify(result).includes("private-"));
  raw.$workers.event.request.url = "https://private-host/private-path?private-query";
  raw.$workers.eventType = "private-type";
  raw.$workers.outcome = "private-outcome";
  assert.equal(diagnostic.redactProductionEvent(raw).route, "unknown");
  assert.equal(diagnostic.redactProductionEvent(raw).event_type, "unknown");
  assert.equal(diagnostic.redactProductionEvent(raw).outcome, "unknown");
});

test("別scriptのログを拒否する", () => {
  for (const field of ["service", "scriptName"]) {
    const raw = event();
    (field === "service" ? raw.$metadata : raw.$workers)[field] = "ie-event-bot-e2e";
    assert.throws(() => diagnostic.redactProductionEvent(raw), /event_diagnostic_scope_mismatch/);
  }
});

test("settingsの任意text・secret型・不正な数値やenumを保持しない", async () => {
  const raw = settings();
  raw.compatibility_date = "private-date";
  raw.limits.cpu_ms = -1;
  raw.usage_model = "private-model";
  raw.observability = { enabled: "private-value", head_sampling_rate: 2 };
  raw.bindings.push({ name: "SYNC_ALL_INCLUDE_DISCORD_NOTION", type: "secret_text", text: "true" },
    { name: "SYNC_INTERVAL_SECONDS", type: "plain_text", text: "private-value" },
    { name: "DISCORD_NOTION_MAX_CHANGES_PER_RUN", type: "plain_text", text: "10000000000" });
  const report = await diagnostic.diagnoseNotifications(ENV, requestStub([], { settings: response(raw) }), NOW);
  assert.equal(report.settings.compatibility_date, null);
  assert.equal(report.settings.cpu_ms, null);
  assert.equal(report.settings.usage_model, "unknown");
  assert.deepEqual(report.settings.observability, { enabled: null, head_sampling_rate: null });
  assert.equal(report.settings.flags.SYNC_ALL_INCLUDE_DISCORD_NOTION, null);
  assert.equal(report.settings.flags.SYNC_INTERVAL_SECONDS, null);
  assert.equal(report.settings.flags.DISCORD_NOTION_MAX_CHANGES_PER_RUN, null);
  assert.ok(!JSON.stringify(report).includes("private-"));
});

for (const status of [403, 404]) {
  test(`HTTP ${status}でも他の採取を保持し本文やIDを保存しない`, async () => {
    const bad = { ok: false, status, json: async () => ({ success: false,
      errors: [{ code: 10000, message: "Permission denied private-value" }] }) };
    const calls = [];
    const report = await diagnostic.diagnoseNotifications(ENV, requestStub(calls, { settings: bad, telemetry: bad }), NOW);
    assert.equal(report.ok, false);
    assert.equal(report.settings.error, `event_diagnostic_http_${status}`);
    assert.equal(report.telemetry.error, `event_diagnostic_http_${status}`);
    assert.equal(report.telemetry.complete, false);
    assert.equal(report.active_versions[0].git_sha, "d".repeat(40));
    assert.ok(!JSON.stringify(report).includes("private-value"));
    assert.equal(calls.length, 4);
  });
}

test("枠外ログと異常応答をcompleteにしない", async () => {
  for (const result of [{ events: { events: [event(0)] } }, { events: { events: [event(NOW)] } }, {}]) {
    const report = await diagnostic.diagnoseNotifications(ENV, requestStub([], { telemetry: response(result) }), NOW);
    assert.equal(report.ok, false);
    assert.equal(report.telemetry.complete, false);
    assert.match(report.telemetry.error, /^event_diagnostic_(time_mismatch|response_invalid)$/);
  }
});

test("telemetryの別scriptとJSON読取失敗を固定エラーにする", async () => {
  const foreign = event(); foreign.$metadata.service = "foreign-private-script";
  const report = await diagnostic.diagnoseNotifications(ENV,
    requestStub([], { telemetry: response({ events: { events: [foreign] } }),
      settings: { ok: true, status: 200, json: async () => { throw new Error("private-json-body"); } } }), NOW);
  assert.equal(report.telemetry.error, "event_diagnostic_scope_mismatch");
  assert.equal(report.telemetry.complete, false);
  assert.equal(report.settings.error, "event_diagnostic_response_invalid");
  assert.equal(report.active_versions[0].git_sha, "d".repeat(40));
  assert.ok(!JSON.stringify(report).includes("private-"));
});

test("不正なversion IDをAPI pathへ使わない", async () => {
  const d = deployment(); d.versions[0].version_id = "../../private-value";
  const calls = [];
  const report = await diagnostic.diagnoseNotifications(ENV, requestStub(calls,
    { deployments: response({ deployments: [d] }) }), NOW);
  assert.equal(report.deployments.error, "event_diagnostic_response_invalid");
  assert.equal(report.active_versions.length, 0);
  assert.equal(calls.length, 3);
  assert.ok(!JSON.stringify(report).includes("private-value"));
});

test("version metadataのID不一致と任意tagを採用しない", async () => {
  const raw = version(); raw.id = DEPLOYMENT;
  let report = await diagnostic.diagnoseNotifications(ENV, requestStub([], { version: response(raw) }), NOW);
  assert.equal(report.active_versions[0].error, "event_diagnostic_scope_mismatch");
  raw.id = VERSION; raw.annotations["workers/tag"] = "private-value";
  raw.metadata.source = "private-source";
  report = await diagnostic.diagnoseNotifications(ENV, requestStub([], { version: response(raw) }), NOW);
  assert.equal(report.active_versions[0].git_sha, null);
  assert.equal(report.active_versions[0].source, "unknown");
  assert.ok(!JSON.stringify(report).includes("private-"));
});

test("deployment履歴の上限・不正配分と未知sourceを明示する", async () => {
  let report = await diagnostic.diagnoseNotifications(ENV, requestStub([], {
    deployments: response({ deployments: [deployment()] }, { result_info: { page: 1, total_pages: 2 } }),
  }), NOW);
  assert.equal(report.deployments.complete, false);
  assert.equal(report.ok, false);
  assert.equal(report.active_versions.length, 1);
  const d = deployment(); d.versions[0].percentage = 99;
  report = await diagnostic.diagnoseNotifications(ENV, requestStub([], {
    deployments: response({ deployments: [d] }),
  }), NOW);
  assert.equal(report.deployments.error, "event_diagnostic_response_invalid");
  d.versions[0].percentage = 100; d.source = "private-source";
  report = await diagnostic.diagnoseNotifications(ENV, requestStub([], {
    deployments: response({ deployments: [d] }),
  }), NOW);
  assert.equal(report.deployments.items[0].source, "unknown");
  assert.ok(!JSON.stringify(report).includes("private-source"));
});

test("active配分が2版なら両方のmetadataだけをGETする", async () => {
  const second = "99999999-2222-4333-8444-555555555555";
  const d = deployment(); d.versions = [{ version_id: VERSION, percentage: 60 }, { version_id: second, percentage: 40 }];
  const calls = [];
  const base = requestStub(calls, { deployments: response({ deployments: [d] }) });
  const report = await diagnostic.diagnoseNotifications(ENV, async (url, options) => {
    if (url.endsWith(`/versions/${second}`)) {
      calls.push({ url, options });
      return response({ ...version(), id: second });
    }
    return base(url, options);
  }, NOW);
  assert.equal(report.ok, true);
  assert.equal(calls.length, 5);
  assert.deepEqual(report.active_versions.map(v => v.version_sha256), [hash(VERSION), hash(second)]);
  assert.deepEqual(report.deployments.items[0].versions.map(v => v.percentage), [60, 40]);
});

test("telemetryは最大5pageで打ち切り、cursorは保存しない", async () => {
  const base = requestStub([]); let pages = 0;
  const report = await diagnostic.diagnoseNotifications(ENV, async (url, options) => {
    if (!url.endsWith("/telemetry/query")) return base(url, options);
    pages++;
    const body = JSON.parse(options.body);
    if (pages > 1) assert.equal(body.offset, `private-cursor-${pages - 1}`);
    const raw = event(); raw.$metadata.id = `private-cursor-${pages}`;
    return response({ events: { events: Array(2000).fill(raw) } });
  }, NOW);
  assert.equal(pages, 5);
  assert.equal(report.telemetry.events.length, 10000);
  assert.equal(report.telemetry.complete, false);
  assert.equal(report.ok, false);
  assert.ok(!JSON.stringify(report).includes("private-cursor"));
});

test("cursor欠落・循環は固定エラーになり、部分結果をcompleteにしない", async () => {
  for (const cursor of [undefined, "same-private-cursor"]) {
    const base = requestStub([]);
    const report = await diagnostic.diagnoseNotifications(ENV, async (url, options) => {
      if (!url.endsWith("/telemetry/query")) return base(url, options);
      const raw = event(); if (cursor) raw.$metadata.id = cursor;
      return response({ events: { events: Array(2000).fill(raw) } });
    }, NOW);
    assert.equal(report.telemetry.error, "event_diagnostic_cursor_invalid");
    assert.equal(report.telemetry.complete, false);
  }
});

test("資格情報不足と任意例外の本文を出さない", async () => {
  let calls = 0;
  await assert.rejects(diagnostic.diagnoseNotifications({ ...ENV, CLOUDFLARE_ACCOUNT_ID: "private-id" }, async () => { calls++; }, NOW),
    /event_diagnostic_credentials_missing/);
  assert.equal(calls, 0);
  assert.deepEqual(diagnostic.failureReport(new Error("private-token")), { error: "event_diagnostic_request_failed" });
  const report = await diagnostic.diagnoseNotifications(ENV, async () => { throw new Error("private-network-url"); }, NOW);
  assert.equal(report.settings.error, "event_diagnostic_request_failed");
  assert.equal(report.deployments.error, "event_diagnostic_request_failed");
  assert.equal(report.telemetry.error, "event_diagnostic_request_failed");
  assert.equal(report.ok, false);
  assert.ok(!JSON.stringify(report).includes("private-network-url"));
});
