import assert from "node:assert/strict";
import test from "node:test";
import { diagnose, diagnosticWindows, failureReport, queryBody, redactApiError, redactEvent } from "./diagnose_google_lock.mjs";

const credentials = { CLOUDFLARE_ACCOUNT_ID: "a".repeat(32), CLOUDFLARE_API_TOKEN: "fake-token" };

test("復旧検証のログを当該run開始から1時間以内に限定する", () => {
  const now = Date.parse("2026-09-25T10:10:00Z");
  assert.deepEqual(diagnosticWindows("E2E-20260925T100000Z-123456ab", now),
    [["release_recovery", "2026-09-25T10:00:00.000Z", "2026-09-25T10:10:00.000Z"]]);
  for (const run of ["private-value", "E2E-20260925T080000Z-123456ab", "E2E-20260925T110000Z-123456ab"]) {
    assert.throws(() => diagnosticWindows(run, now), /diagnostic_run_invalid/);
  }
});

test("復旧ログを固定分類し、任意の本文は残さない", () => {
  for (const [name, category] of [["sync_lock_release_recovered", "lock_release_recovered"],
    ["sync_lock_release_recovery_failed", "lock_release_recovery_failed"],
    ["e2e_lock_release_probe_passed", "lock_release_probe_passed"]]) {
    const raw = event();
    raw.source.message = JSON.stringify({ event: name, secret: "private-log-value" });
    const redacted = redactEvent(raw);
    assert.ok(redacted.categories.includes(category));
    assert.ok(!JSON.stringify(redacted).includes("private-log-value"));
  }
});
function event(timestamp = Date.parse("2026-09-24T09:26:58Z")) {
  return { timestamp, $metadata: { service: "ie-event-bot-e2e", statusCode: 409,
    error: "secret-test-value: Durable Object storage operation exceeded timeout" },
  source: { authorization: "secret-test-value", url: "https://secret-test-value/" },
  $workers: { scriptName: "ie-event-bot-e2e", eventType: "rpc", outcome: "exception", wallTimeMs: 12000 } };
}

test("ログ本文・URL・任意文字列を出さず固定分類と数値だけ返す", () => {
  const raw = event();
  raw.$workers.executionModel = "secret-test-value";
  const result = redactEvent(raw);
  assert.deepEqual(result.categories, ["storage_timeout"]);
  assert.equal(result.wall_ms, 12000);
  assert.equal(result.execution, "unknown");
  assert.equal(result.message_present, true);
  assert.deepEqual(result.message_terms, ["timeout", "storage"]);
  assert.ok(!JSON.stringify(result).includes("secret-test-value"));
});

test("別Workerのログを拒否する", () => {
  const raw = event();
  raw.$metadata.service = "production";
  assert.throws(() => redactEvent(raw), /diagnostic_scope_mismatch/);
});

test("接続上限とRPC切断を本文を残さず分類する", () => {
  const raw = event();
  raw.$metadata.error = "secret-test-value: RPC session disconnected: Response closed due to connection limit";
  assert.deepEqual(redactEvent(raw).categories, ["disconnected", "connection_limit", "rpc_session"]);
  assert.ok(!JSON.stringify(redactEvent(raw)).includes("secret-test-value"));
});

test("runtimeの未消費応答警告を固定分類する", () => {
  const raw = event();
  delete raw.$metadata.error;
  raw.message = "A stalled HTTP response was canceled to prevent deadlock. private-secret";
  assert.deepEqual(redactEvent(raw).categories, ["stalled_response"]);
  assert.ok(!JSON.stringify(redactEvent(raw)).includes("private-secret"));
});

test("2つの固定時間帯とE2E Workerだけを保存なしで照会する", async () => {
  const calls = [];
  const report = await diagnose(credentials, async (url, options) => {
    const body = JSON.parse(options.body);
    calls.push({ url, options, body });
    return { ok: true, json: async () => ({ success: true, result: { events: { events: [event(body.timeframe.from + 1000)] } } }) };
  });
  assert.equal(calls.length, 2);
  assert.equal(report.windows.length, 2);
  assert.deepEqual(report.windows.map(({ label, from, to }) => [label, from, to]), [
    ["watch_prepare", "2026-09-25T09:16:35Z", "2026-09-25T09:17:45Z"],
    ["watch_recovery", "2026-09-25T09:22:00Z", "2026-09-25T09:23:30Z"],
  ]);
  for (const { url, options, body } of calls) {
    assert.match(url, /^https:\/\/api\.cloudflare\.com\/client\/v4\/accounts\/[a-f0-9]{32}\/workers\/observability\/telemetry\/query$/);
    assert.equal(options.redirect, "error");
    assert.equal(body.dry, true);
    assert.ok(body.timeframe.to - body.timeframe.from <= 90000);
    assert.deepEqual(body.parameters.filters, [{ key: "$metadata.service", operation: "eq", type: "string", value: "ie-event-bot-e2e" }]);
  }
  assert.ok(report.windows.every((window) => window.complete));
  assert.ok(!JSON.stringify(report).includes("secret-test-value"));
});

test("403では数値コードと分類だけを残し、有効トークンと拒否を区別する", async () => {
  const calls = [];
  let report;
  try {
    await diagnose(credentials, async (url, options) => {
      calls.push({ url, options });
      if (url.endsWith("/user/tokens/verify")) {
        return { ok: true, status: 200, json: async () => ({ success: true,
          result: { status: "active", id: "private-token-id", value: "private-token-value" } }) };
      }
      return { ok: false, status: 403, json: async () => ({ success: false,
        errors: [{ code: 10000, message: "Authentication error private-token-value" }] }) };
    });
  } catch (error) { report = failureReport(error); }
  assert.equal(calls.length, 3);
  assert.equal(calls[1].options.method, "GET");
  assert.match(calls[2].url, /\/accounts\/[a-f0-9]{32}\/tokens\/verify$/);
  assert.equal(report.error, "diagnostic_http_403");
  assert.deepEqual(report.api_failure.codes, [10000]);
  assert.deepEqual(report.api_failure.categories, ["authentication"]);
  assert.equal(report.api_failure.credential_checks[0].token_status, "active");
  assert.equal(report.api_failure.credential_checks[1].token_status, "unknown");
  assert.ok(!JSON.stringify(report).includes("private-token"));
});

test("エラー応答がHTMLでも元のHTTP状態を保持する", async () => {
  let report;
  try {
    await diagnose(credentials, async () => ({ ok: false, status: 403,
      json: () => { throw new Error("private-response"); } }));
  } catch (error) { report = failureReport(error); }
  assert.equal(report.error, "diagnostic_http_403");
  assert.deepEqual(report.api_failure.codes, []);
  assert.ok(!JSON.stringify(report).includes("private-response"));
});

test("account所有tokenのverify成功をuser側拒否で隠さない", async () => {
  let report;
  try {
    await diagnose(credentials, async (url) => {
      if (/\/accounts\/[^/]+\/tokens\/verify$/.test(url)) {
        return { ok: true, status: 200, json: async () => ({ success: true, result: { status: "active" } }) };
      }
      return { ok: false, status: 403, json: async () => ({ success: false, errors: [] }) };
    });
  } catch (error) { report = failureReport(error); }
  assert.equal(report.api_failure.credential_checks[0].success, false);
  assert.equal(report.api_failure.credential_checks[1].token_status, "active");
});

test("任意エラー詳細や不正コードをartifactへ流さない", () => {
  const result = redactApiError({ errors: [null, { code: "private-value", message: "private-value" },
    { code: 1234, message: "Account permission denied: Workers Observability private-value" }] });
  assert.deepEqual(result.codes, [1234]);
  assert.deepEqual(result.categories, ["permission", "account", "observability"]);
  assert.ok(!JSON.stringify(result).includes("private-value"));
  assert.deepEqual(failureReport(new Error("private-value")), { error: "diagnostic_request_failed" });
});

test("不正応答・時間帯外のイベントを成功として扱わない", async () => {
  for (const [data, expected] of [
    [{ success: true, result: {} }, /diagnostic_response_invalid/],
    [{ success: true, result: { events: { events: [event(0)] } } }, /diagnostic_time_mismatch/],
  ]) {
    await assert.rejects(diagnose(credentials, async () => ({ ok: true, json: async () => data })), expected);
  }
});

test("ページ上限では不完全と明示し、続きのcursorを使用する", async () => {
  let count = 0;
  const report = await diagnose(credentials, async (_, options) => {
    const body = JSON.parse(options.body);
    count++;
    if (count % 5 !== 1) assert.ok(body.offset);
    const raw = event(body.timeframe.from + 1000);
    raw.$metadata.id = `cursor-${count}`;
    return { ok: true, json: async () => ({ success: true, result: { events: { events: Array(2000).fill(raw) } } }) };
  });
  assert.equal(count, 10);
  assert.ok(report.windows.every((window) => !window.complete));
  assert.equal(queryBody("test", "2026-09-24", "2026-09-25", "cursor").offset, "cursor");
});
