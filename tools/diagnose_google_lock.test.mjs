import assert from "node:assert/strict";
import test from "node:test";
import { diagnose, queryBody, redactEvent } from "./diagnose_google_lock.mjs";

const credentials = { CLOUDFLARE_ACCOUNT_ID: "a".repeat(32), CLOUDFLARE_API_TOKEN: "fake-token" };
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
  assert.ok(!JSON.stringify(result).includes("secret-test-value"));
});

test("別Workerのログを拒否する", () => {
  const raw = event();
  raw.$metadata.service = "production";
  assert.throws(() => redactEvent(raw), /diagnostic_scope_mismatch/);
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
  for (const { url, options, body } of calls) {
    assert.match(url, /^https:\/\/api\.cloudflare\.com\/client\/v4\/accounts\/[a-f0-9]{32}\/workers\/observability\/telemetry\/query$/);
    assert.equal(options.redirect, "error");
    assert.equal(body.dry, true);
    assert.ok(body.timeframe.to - body.timeframe.from <= 35000);
    assert.deepEqual(body.parameters.filters, [{ key: "$metadata.service", operation: "eq", type: "string", value: "ie-event-bot-e2e" }]);
  }
  assert.ok(report.windows.every((window) => window.complete));
  assert.ok(!JSON.stringify(report).includes("secret-test-value"));
});

test("認証拒否時にAPIのエラー本文を読み出さない", async () => {
  await assert.rejects(diagnose(credentials, async () => ({ ok: false, status: 403,
    json: () => { throw new Error("must not read body"); } })), /diagnostic_http_403/);
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
