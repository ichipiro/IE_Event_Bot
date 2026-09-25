// 実Cronだけを待つ専用runner。秘密値・外部API応答本文・Wranglerログを保存しない。
import { createHash } from "node:crypto";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = fileURLToPath(new URL("../", import.meta.url));
const OUTPUT = resolve(ROOT, "test-results/cron-e2e");
const RUN_PATTERN = /^E2E-\d{8}T\d{6}Z-[a-f0-9]{8}$/;
const NAMESPACE = "119c6ebb96b0468cb7c25d96980b416e";
const CRON = "* * * * *";
const WINDOW_MS = 20 * 60_000;
const execute = promisify(execFile);
const hash = (value) => createHash("sha256").update(value).digest("hex");
const pause = (ms) => new Promise((done) => setTimeout(done, ms));

function requireThat(condition, code) {
  if (!condition) { throw new Error(`cron_${code}`); }
}

export function scope(runId) {
  requireThat(RUN_PATTERN.test(runId), "run_id_invalid");
  return { worker: `ie-event-bot-cron-${runId.toLowerCase()}`, prefix: `e2e:real-cron:${runId}:` };
}

export function validateReceipt(value, report, key) {
  requireThat(value && value.run_id === report.run_id && value.version_tag === report.run_id
    && value.version_id === report.version_id && value.commit === report.commit
    && value.source === "scheduled" && value.cron === CRON
    && value.normal_dispatch_completed === true && value.job_count === 0
    && Number.isSafeInteger(value.scheduled_time_ms) && value.scheduled_time_ms % 60_000 === 0
    && Number.isSafeInteger(value.observed_time_ms)
    && report.start_ms <= value.scheduled_time_ms
    && value.scheduled_time_ms <= value.observed_time_ms && value.observed_time_ms < report.deadline_ms
    && key === `${scope(report.run_id).prefix}${value.scheduled_time_ms}`, "receipt_invalid");
  // 固定schemaだけを証跡へ残す。
  return Object.fromEntries([
    "run_id", "version_id", "version_tag", "commit", "source", "cron",
    "normal_dispatch_completed", "job_count", "scheduled_time_ms", "observed_time_ms",
  ].map((key) => [key, value[key]]));
}

export function redactCronEvent(event, worker) {
  requireThat(event.$metadata?.service === worker, "diagnostic_scope_mismatch");
  const metadata = event.$metadata ?? {};
  const runtime = event.$workers ?? {};
  const detail = JSON.stringify([metadata.error, metadata.message, event.source]);
  const patterns = {
    attribute_error: /AttributeError/,
    type_error: /TypeError/,
    missing_arguments: /missing.*required positional/,
    controller_dict: /dict.*(?:scheduledTime|cron)/,
    metadata_dict: /dict.*(?:tag|id)/,
    scheduled_time_missing: /(?:attribute|property).*scheduledTime/,
    kv_error: /KV|kv_namespace/,
    import_error: /ImportError|ModuleNotFoundError/,
  };
  return { timestamp: Number.isFinite(event.timestamp) ? event.timestamp : null,
    event_type: ["scheduled", "fetch", "rpc"].includes(runtime.eventType) ? runtime.eventType : "unknown",
    outcome: ["ok", "exception", "exceededCpu", "exceededMemory", "canceled"].includes(runtime.outcome) ? runtime.outcome : "unknown",
    error_present: Boolean(metadata.error),
    categories: Object.entries(patterns).filter(([, pattern]) => pattern.test(detail)).map(([name]) => name) };
}

export class CronE2E {
  constructor(env, { request = fetch, sleep = pause, now = Date.now, deploy } = {}) {
    requireThat(/^[a-f0-9]{32}$/.test(env.CLOUDFLARE_ACCOUNT_ID ?? "")
      && Boolean(env.CLOUDFLARE_API_TOKEN), "credentials_missing");
    this.env = env;
    this.request = request;
    this.sleep = sleep;
    this.now = now;
    this.deploy = deploy ?? (async (config, runId) => {
      try {
        await execute(resolve(ROOT, "node_modules/.bin/wrangler"), [
          "deploy", "--config", config, "--tag", runId,
        ], { cwd: ROOT, timeout: 180_000, maxBuffer: 8 * 1024 * 1024,
          env: { PATH: process.env.PATH, HOME: process.env.HOME, CI: "true",
            WRANGLER_SEND_METRICS: "false", CLOUDFLARE_ACCOUNT_ID: env.CLOUDFLARE_ACCOUNT_ID,
            CLOUDFLARE_API_TOKEN: env.CLOUDFLARE_API_TOKEN } });
      } catch { throw new Error("cron_deploy_failed"); }
    });
  }

  async save(report) {
    await mkdir(OUTPUT, { recursive: true });
    await writeFile(resolve(OUTPUT, `${report.run_id}.json`), `${JSON.stringify(report, null, 2)}\n`);
  }

  async diagnose(runId) {
    const owned = scope(runId);
    const stamp = runId.slice(4, 20);
    const from = `${stamp.slice(0, 4)}-${stamp.slice(4, 6)}-${stamp.slice(6, 8)}T${stamp.slice(9, 11)}:${stamp.slice(11, 13)}:${stamp.slice(13, 15)}Z`;
    const fromMs = Date.parse(from);
    requireThat(Number.isFinite(fromMs), "diagnostic_time_invalid");
    const to = new Date(fromMs + 23 * 60_000).toISOString();
    const report = { run_id: runId, ...owned, from, to, audit: [], events: [] };
    try {
      const data = await this.api(report, "read_cron_logs", "workers/observability/telemetry/query", {
        method: "POST", body: { queryId: "real-cron-diagnostic", dry: true, view: "events", limit: 1000,
          timeframe: { from: fromMs, to: Date.parse(to) }, parameters: { datasets: ["cloudflare-workers"], filterCombination: "and",
            filters: [{ key: "$metadata.service", operation: "eq", type: "string", value: owned.worker }] } },
      });
      const events = data.result?.events?.events;
      requireThat(Array.isArray(events) && events.length < 1000, "diagnostic_response_invalid");
      report.events = events.map((event) => {
        requireThat(Number.isFinite(event.timestamp) && event.timestamp >= fromMs
          && event.timestamp <= Date.parse(to), "diagnostic_time_mismatch");
        return redactCronEvent(event, owned.worker);
      });
      report.complete = true;
    } catch (error) { report.error = safeError(error); }
    await this.save(report);
    requireThat(report.complete, "diagnostic_failed");
    return report;
  }

  async api(report, label, path, { method = "GET", body, missing = false } = {}) {
    let response;
    try {
      response = await this.request(
        `https://api.cloudflare.com/client/v4/accounts/${this.env.CLOUDFLARE_ACCOUNT_ID}/${path}`,
        { method, redirect: "error", signal: AbortSignal.timeout(20_000),
          headers: { Authorization: `Bearer ${this.env.CLOUDFLARE_API_TOKEN}`, "Content-Type": "application/json" },
          ...(body === undefined ? {} : { body: JSON.stringify(body) }) },
      );
    } catch { throw new Error("cron_api_transport_failed"); }
    report.audit.push({ operation: label, method, status: response.status, at_ms: this.now() });
    await this.save(report);
    if (missing && response.status === 404) { return null; }
    requireThat(response.ok, `api_http_${response.status}`);
    const data = await response.json();
    requireThat(data?.success !== false, "api_unsuccessful");
    return data;
  }

  script(report) { return `workers/scripts/${scope(report.run_id).worker}`; }
  kv() { return `storage/kv/namespaces/${NAMESPACE}`; }

  async keys(report) {
    const data = await this.api(report, "kv_list", `${this.kv()}/keys?prefix=${encodeURIComponent(scope(report.run_id).prefix)}&limit=1000`);
    requireThat(Array.isArray(data.result) && !data.result_info?.cursor && data.result.length <= 21, "key_list_invalid");
    for (const entry of data.result) {
      requireThat(typeof entry.name === "string" && entry.name.startsWith(scope(report.run_id).prefix)
        && /^\d{13}$/.test(entry.name.slice(scope(report.run_id).prefix.length)), "key_scope_mismatch");
    }
    return data.result.map((entry) => entry.name);
  }

  async ownedSettings(report) {
    const settings = await this.api(report, "worker_settings", `${this.script(report)}/settings`, { missing: true });
    if (settings === null) { return null; }
    const bindings = settings.result?.bindings;
    requireThat(Array.isArray(bindings)
      && bindings.some((b) => b.name === "E2E_CRON_RUN_ID" && b.type === "plain_text" && b.text === report.run_id)
      && bindings.some((b) => b.name === "STATE_KV" && b.type === "kv_namespace" && b.namespace_id === NAMESPACE),
    "worker_owner_mismatch");
    return settings;
  }

  async run(runId, commit) {
    const owned = scope(runId);
    requireThat(/^[a-f0-9]{40}$/.test(commit ?? ""), "commit_invalid");
    const start = this.now();
    const report = { run_id: runId, commit, ...owned, account_fingerprint: hash(this.env.CLOUDFLARE_ACCOUNT_ID),
      start_ms: start, deadline_ms: start + WINDOW_MS, dirty: false,
      outcome: "preparing", receipts: [], audit: [], cleanup: {}, deploy_attempted: false };
    // 同じrun IDのmanifestを上書きしない。
    await mkdir(OUTPUT, { recursive: true });
    await writeFile(resolve(OUTPUT, `${runId}.json`), JSON.stringify(report), { flag: "wx" });
    try {
      const existing = await this.api(report, "worker_absent_before", `${this.script(report)}/settings`, { missing: true });
      requireThat(existing === null, "worker_already_exists");
      requireThat((await this.keys(report)).length === 0, "keys_already_exist");
      const config = JSON.parse(await readFile(resolve(ROOT, "workers/wrangler.cron-e2e.jsonc"), "utf8"));
      config.name = owned.worker;
      config.main = resolve(ROOT, "workers/src/e2e_cron_entry.py");
      config.vars = { ...config.vars, E2E_CRON_RUN_ID: runId, E2E_CRON_COMMIT: commit,
        E2E_CRON_START_MS: String(report.start_ms), E2E_CRON_DEADLINE_MS: String(report.deadline_ms) };
      const configPath = resolve(OUTPUT, `${runId}-config.json`);
      await writeFile(configPath, JSON.stringify(config));
      report.dirty = true;
      report.deploy_attempted = true;
      await this.save(report);
      await this.deploy(configPath, runId);
      requireThat(await this.ownedSettings(report), "worker_missing_after_deploy");
      const deployed = await this.api(report, "deployment", `${this.script(report)}/deployments`);
      const versions = deployed.result?.deployments?.[0]?.versions;
      requireThat(versions?.length === 1 && versions[0].percentage === 100, "deployment_invalid");
      report.version_id = versions[0].version_id;
      const version = await this.api(report, "version", `${this.script(report)}/versions/${report.version_id}`);
      requireThat(version.result?.annotations?.["workers/tag"] === runId, "version_tag_mismatch");
      const before = await this.api(report, "schedule_before", `${this.script(report)}/schedules`);
      requireThat(Array.isArray(before.result?.schedules) && before.result.schedules.length === 0, "schedule_not_empty");
      await this.api(report, "schedule_enable", `${this.script(report)}/schedules`, { method: "PUT", body: [{ cron: CRON }] });
      const after = await this.api(report, "schedule_readback", `${this.script(report)}/schedules`);
      requireThat(after.result?.schedules?.length === 1 && after.result.schedules[0].cron === CRON, "schedule_readback_failed");
      report.schedule = CRON;
      report.outcome = "waiting";
      await this.save(report);
      while (this.now() < report.deadline_ms) {
        const receipts = [];
        for (const key of await this.keys(report)) {
          const value = await this.api(report, "receipt_read", `${this.kv()}/values/${encodeURIComponent(key)}`, { missing: true });
          if (value !== null) { receipts.push(validateReceipt(value, report, key)); }
        }
        report.receipts = receipts.sort((a, b) => a.scheduled_time_ms - b.scheduled_time_ms);
        await this.save(report);
        if (new Set(receipts.map((r) => r.scheduled_time_ms)).size >= 2) {
          report.scenario_passed = true;
          break;
        }
        console.log(`cron_waiting receipts=${receipts.length}`);
        await this.sleep(20_000);
      }
      requireThat(report.scenario_passed, "delivery_timeout");
    } catch (error) {
      report.error = safeError(error);
    } finally {
      await this.save(report);
      await this.cleanup(report);
    }
    requireThat(report.outcome === "passed", "scenario_failed");
    return report;
  }

  async cleanup(report) {
    const expected = scope(report.run_id);
    requireThat(report.worker === expected.worker && report.prefix === expected.prefix
      && report.account_fingerprint === hash(this.env.CLOUDFLARE_ACCOUNT_ID), "cleanup_scope_mismatch");
    if (!report.deploy_attempted) {
      report.outcome = "failed_clean";
      await this.save(report);
      return report;
    }
    try {
      if (await this.ownedSettings(report)) {
        await this.api(report, "schedule_disable", `${this.script(report)}/schedules`, { method: "PUT", body: [] });
        const schedules = await this.api(report, "schedule_removed", `${this.script(report)}/schedules`);
        requireThat(schedules.result?.schedules?.length === 0, "schedule_cleanup_failed");
        report.cleanup.schedules_empty = true;
        await this.save(report);
        await this.api(report, "worker_delete", this.script(report), { method: "DELETE" });
      }
      requireThat(await this.ownedSettings(report) === null, "worker_cleanup_failed");
      report.cleanup.worker_absent = true;
      await this.save(report);
      // 削除直前に開始した短いハンドラのKV書込みを回収対象に含める。
      await this.sleep(60_000);
      await this.sleep(10_000);
      const deleted = new Set(report.cleanup.deleted_keys ?? []);
      for (let attempt = 0; attempt < 4; attempt++) {
        for (const key of await this.keys(report)) {
          const value = await this.api(report, "cleanup_receipt_read", `${this.kv()}/values/${encodeURIComponent(key)}`, { missing: true });
          if (value !== null) {
            // version読戻し前のdeploy失敗ではCronを有効化していないためキーは存在しない。
            validateReceipt(value, report, key);
            await this.api(report, "kv_delete", `${this.kv()}/values/${encodeURIComponent(key)}`, { method: "DELETE" });
            deleted.add(key);
            report.cleanup.deleted_keys = [...deleted];
            await this.save(report);
          }
        }
        await this.sleep(20_000);
        let absent = (await this.keys(report)).length === 0;
        for (const key of deleted) {
          absent = (await this.api(report, "kv_absent", `${this.kv()}/values/${encodeURIComponent(key)}`, { missing: true }) === null) && absent;
        }
        if (absent) {
          report.cleanup.kv_absent = true;
          report.dirty = false;
          report.outcome = report.scenario_passed ? "passed" : "failed_clean";
          delete report.cleanup_error;
          await this.save(report);
          return report;
        }
      }
      throw new Error("cron_kv_cleanup_not_observed");
    } catch (error) {
      report.dirty = true;
      report.outcome = "dirty";
      report.cleanup_error = safeError(error);
      await this.save(report);
      throw new Error(report.cleanup_error);
    }
  }
}

function safeError(error) {
  return error instanceof Error && /^cron_[a-z0-9_]+$/.test(error.message) ? error.message : "cron_operation_failed";
}

async function main() {
  const [command, flag, runId] = process.argv.slice(2);
  requireThat(["run", "cleanup", "diagnose"].includes(command) && flag === "--run-id" && process.argv.length === 5, "arguments_invalid");
  scope(runId);
  const runner = new CronE2E(process.env);
  if (command === "diagnose") {
    await runner.diagnose(runId);
  } else if (command === "cleanup") {
    let report;
    try { report = JSON.parse(await readFile(resolve(OUTPUT, `${runId}.json`), "utf8")); }
    catch (error) {
      if (error.code === "ENOENT") { console.log("cron_no_manifest"); return; }
      throw error;
    }
    await runner.cleanup(report);
  } else {
    await runner.run(runId, process.env.GITHUB_SHA);
  }
  console.log("cron_operation_ok");
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  main().catch((error) => { console.error(safeError(error)); process.exitCode = 1; });
}
