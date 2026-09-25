import assert from "node:assert/strict";
import { createHash, randomBytes } from "node:crypto";
import test from "node:test";
import { CronE2E, scope, validateReceipt, redactCronEvent } from "./run_cron_e2e.mjs";

const START = 1_800_000_000_000;
const COMMIT = "a".repeat(40);
const env = { CLOUDFLARE_ACCOUNT_ID: "a".repeat(32), CLOUDFLARE_API_TOKEN: "fake-test-only" };
const newRun = () => `E2E-20260925T000000Z-${randomBytes(4).toString("hex")}`;
const record = (run, at) => ({ run_id: run, version_id: "v1", version_tag: run, commit: COMMIT,
  cron: "* * * * *", source: "scheduled", job_count: 0, normal_dispatch_completed: true,
  scheduled_time_ms: at, observed_time_ms: at + 100 });

function fixture({ timeout = false, foreign = false, failDelete = false, badVersion = false, tick = false } = {}) {
  const run = newRun();
  let clock = START;
  let exists = false;
  let schedules = [];
  const keys = new Map();
  const calls = [];
  let saved;
  const request = async (url, options) => {
    const path = new URL(url).pathname;
    calls.push({ path, method: options.method, body: options.body });
    let result;
    let status = 200;
    if (path.endsWith("/settings")) {
      if (!exists) { return new Response("{}", { status: 404 }); }
      result = { bindings: [
        { name: "E2E_CRON_RUN_ID", type: "plain_text", text: foreign ? "foreign" : run },
        { name: "STATE_KV", type: "kv_namespace", namespace_id: "119c6ebb96b0468cb7c25d96980b416e" },
      ] };
    } else if (path.endsWith("/deployments")) {
      result = { deployments: [{ versions: [{ version_id: "v1", percentage: 100 }] }] };
    } else if (path.endsWith("/versions/v1")) {
      result = { annotations: { "workers/tag": badVersion ? "other" : run } };
    } else if (path.endsWith("/schedules")) {
      if (options.method === "PUT") {
        schedules = JSON.parse(options.body);
        if (schedules.length && !timeout) {
          for (const at of [START + 60_000, START + 120_000]) {
            keys.set(`${scope(run).prefix}${at}`, record(run, at));
          }
          clock += 120_100;
        }
      }
      result = { schedules };
    } else if (path.endsWith("/keys")) {
      result = [...keys.keys()].map((name) => ({ name }));
    } else if (path.includes("/values/")) {
      const key = decodeURIComponent(path.split("/values/")[1]);
      if (options.method === "DELETE") {
        if (failDelete) { status = 503; } else { keys.delete(key); }
        result = null;
      } else {
        return new Response(JSON.stringify(keys.get(key) ?? {}), { status: keys.has(key) ? 200 : 404 });
      }
    } else if (options.method === "DELETE" && path.endsWith(scope(run).worker)) {
      exists = false;
      result = null;
    } else { assert.fail(`unexpected request ${path}`); }
    return new Response(JSON.stringify({ success: status === 200, result }), { status });
  };
  const runner = new CronE2E(env, { request, now: () => tick ? clock++ : clock,
    sleep: async (ms) => { clock += ms; }, deploy: async () => { exists = true; } });
  runner.save = async (report) => { saved = structuredClone(report); };
  return { run, runner, calls, keys, report: () => saved };
}

test("real scheduled receipts pass only after schedule, Worker and KV cleanup", async () => {
  const f = fixture();
  const report = await f.runner.run(f.run, COMMIT);
  assert.equal(report.outcome, "passed");
  assert.equal(report.dirty, false);
  assert.equal(report.receipts.length, 2);
  assert.equal(report.cleanup.schedules_empty, true);
  assert.equal(report.cleanup.worker_absent, true);
  assert.equal(report.cleanup.kv_absent, true);
  assert.equal(report.cleanup.deleted_keys.length, 2);
  assert.equal(f.keys.size, 0);
  const deletes = f.calls.filter((c) => c.method === "DELETE");
  assert.ok(deletes[0].path.endsWith(scope(f.run).worker));
  assert.ok(deletes.slice(1).every((c) => c.path.includes(encodeURIComponent(scope(f.run).prefix))));
});

test("delivery timeout is failed_clean, not passed", async () => {
  const f = fixture({ timeout: true });
  await assert.rejects(f.runner.run(f.run, COMMIT), /cron_scenario_failed/);
  assert.equal(f.report().error, "cron_delivery_timeout");
  assert.equal(f.report().outcome, "failed_clean");
  assert.equal(f.report().dirty, false);
});

test("deadline uses the same clock sample as start", async () => {
  const f = fixture({ tick: true });
  const report = await f.runner.run(f.run, COMMIT);
  assert.equal(report.deadline_ms - report.start_ms, 1_200_000);
});

test("foreign Worker blocks schedule mutations and cleanup", async () => {
  const f = fixture({ foreign: true });
  await assert.rejects(f.runner.run(f.run, COMMIT), /cron_worker_owner_mismatch/);
  assert.equal(f.report().dirty, true);
  assert.ok(f.calls.every((c) => c.method === "GET"));
});

test("version mismatch never enables schedule", async () => {
  const f = fixture({ badVersion: true });
  await assert.rejects(f.runner.run(f.run, COMMIT), /cron_scenario_failed/);
  assert.equal(f.report().error, "cron_version_tag_mismatch");
  assert.ok(f.calls.filter((c) => c.method === "PUT").every((c) => c.body === "[]"));
});

test("KV deletion failure retains dirty evidence", async () => {
  const f = fixture({ failDelete: true });
  await assert.rejects(f.runner.run(f.run, COMMIT), /cron_api_http_503/);
  assert.equal(f.report().outcome, "dirty");
  assert.equal(f.report().dirty, true);
  assert.equal(f.keys.size, 2);
});

test("tampered cleanup scope cannot send requests", async () => {
  const f = fixture();
  const report = { run_id: f.run, ...scope(f.run), worker: "production",
    account_fingerprint: createHash("sha256").update(env.CLOUDFLARE_ACCOUNT_ID).digest("hex") };
  await assert.rejects(f.runner.cleanup(report), /cron_cleanup_scope_mismatch/);
  assert.equal(f.calls.length, 0);
});

test("forged HTTP receipt, version, time and run mismatch are rejected", () => {
  const run = newRun();
  const report = { run_id: run, version_id: "v1", commit: COMMIT, start_ms: START, deadline_ms: START + 1_200_000 };
  const good = record(run, START + 60_000);
  const key = `${scope(run).prefix}${good.scheduled_time_ms}`;
  for (const patch of [{ source: "fetch" }, { version_id: "other" }, { run_id: newRun() },
    { job_count: 1 }, { scheduled_time_ms: START - 60_000 }, { observed_time_ms: report.deadline_ms },
    { scheduled_time_ms: START + 60_001 }]) {
    assert.throws(() => validateReceipt({ ...good, ...patch }, report, key), /cron_receipt_invalid/);
  }
  assert.deepEqual(validateReceipt({ ...good, unexpected: "discard" }, report, key), good);
  assert.throws(() => scope("../../production"), /cron_run_id_invalid/);
});

test("diagnostics retain only known classifications and refuse foreign logs", () => {
  const event = { timestamp: START, $metadata: { service: "owned", error: "AttributeError: dict has no attribute scheduledTime PRIVATE" },
    $workers: { eventType: "scheduled", outcome: "exception", event: { scheduledTime: START + 16_000, cron: "* * * * *" } }, source: "arbitrary log" };
  const result = redactCronEvent(event, "owned");
  assert.equal(result.event_type, "scheduled");
  assert.equal(result.outcome, "exception");
  assert.equal(result.scheduled_time_ms, START + 16_000);
  assert.equal(result.cron_matches, true);
  assert.ok(result.categories.includes("controller_dict"));
  assert.ok(!JSON.stringify(result).includes("PRIVATE"));
  assert.ok(!JSON.stringify(result).includes("arbitrary"));
  assert.throws(() => redactCronEvent(event, "foreign"), /cron_diagnostic_scope_mismatch/);
});

test("diagnostics use only the fixed read-only telemetry API and bounded run window", async () => {
  const run = newRun();
  const runner = new CronE2E(env, { request: async (url, options) => {
    assert.ok(url.endsWith("/workers/observability/telemetry/query"));
    const body = JSON.parse(options.body);
    assert.equal(body.parameters.filters[0].value, scope(run).worker);
    assert.equal(typeof body.timeframe.from, "number");
    assert.equal(body.timeframe.to - body.timeframe.from, 23 * 60_000);
    return new Response(JSON.stringify({ success: true, result: { events: { events: [] } } }));
  } });
  runner.save = async () => {};
  const report = await runner.diagnose(run);
  assert.equal(report.complete, true);
  assert.deepEqual(report.events, []);
  assert.equal(report.audit.length, 1);
});
