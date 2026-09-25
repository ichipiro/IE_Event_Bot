// 実Cronと別HTTPの両方向の競合を、既存の期間限定runnerへ接続する。
import { resolve } from "node:path";
import { CronE2E, ROOT, requireThat, scope } from "./run_cron_e2e.mjs";

function sameIdentity(value, report) {
  requireThat(value?.run_id === report.run_id && value.version_tag === report.run_id
    && value.version_id === report.version_id && value.commit === report.commit, "contention_identity_mismatch");
}

export function validateContention(value, report, key) {
  if (key === `${scope(report.run_id).prefix}result`) {
    sameIdentity(value?.payload, report);
    requireThat(value.payload.ok === true && ["cron", "hold", "after"].includes(value.payload.probe_mode), "contention_result_invalid");
    return { result_mode: value.payload.probe_mode };
  }
  sameIdentity(value, report);
  requireThat(value.source === "scheduled" && value.cron === "* * * * *"
    && Number.isSafeInteger(value.scheduled_time_ms) && Number.isSafeInteger(value.observed_time_ms)
    && Number.isSafeInteger(value.completed_time_ms)
    && report.start_ms <= value.scheduled_time_ms && value.scheduled_time_ms <= value.observed_time_ms
    && value.observed_time_ms <= value.completed_time_ms && value.completed_time_ms < report.deadline_ms
    && key === `${scope(report.run_id).prefix}${value.scheduled_time_ms}`, "contention_receipt_invalid");
  requireThat((value.status === 200 && value.body_calls === 1 && value.result_writes === 1 && /^[a-f0-9]{64}$/.test(value.body_owner))
    || (value.status === 409 && value.body_calls === 0 && value.result_writes === 0 && value.body_owner === ""), "contention_execution_invalid");
  for (const lock of [value.before, value.after]) {
    requireThat(lock && typeof lock.locked === "boolean" && ["", "manual", "cron"].includes(lock.holder)
      && (lock.locked ? /^[a-f0-9]{64}$/.test(lock.owner_sha256) : lock.owner_sha256 === ""), "contention_lock_invalid");
  }
  return Object.fromEntries(["run_id", "version_id", "version_tag", "commit", "source", "cron",
    "scheduled_time_ms", "observed_time_ms", "completed_time_ms", "status", "body_calls", "result_writes",
    "before", "after", "body_owner"].map((name) => [name, value[name]]));
}

export class CronContention extends CronE2E {
  configure(config) {
    requireThat(Boolean(this.env.INTERNAL_API_TOKEN), "contention_token_missing");
    return { ...config, main: resolve(ROOT, "workers/src/e2e_cron_contention.py"), workers_dev: true,
      durable_objects: { bindings: [{ name: "SYNC_COORDINATOR", class_name: "SyncCoordinator", script_name: "ie-event-bot-e2e" }] },
      vars: { ...config.vars, CRON_ENABLE_DISCORD_NOTION_SYNC: "true", SYNC_DO_LOCK_ENABLED: "true",
        KV_RESULT_MIN_WRITE_SECONDS: "0" } };
  }

  validSuffix(suffix) { return suffix === "result" || super.validSuffix(suffix); }
  validateRecord(value, report, key) { return validateContention(value, report, key); }

  async beforeSchedule(report) {
    report.scenario = "real-cron-contention";
    await this.api(report, "install_http_auth", `${this.script(report)}/secrets`, {
      method: "PUT", body: { name: "INTERNAL_API_TOKEN", type: "secret_text", text: this.env.INTERNAL_API_TOKEN },
    });
    // secret更新は新versionを作るため、同じ設定・run tagを再deployして固定する。
    await this.deploy(resolve(ROOT, "test-results/cron-e2e", `${report.run_id}-config.json`), report.run_id);
    const deployed = await this.api(report, "authenticated_deployment", `${this.script(report)}/deployments`);
    const versions = deployed.result?.deployments?.[0]?.versions;
    requireThat(versions?.length === 1 && versions[0].percentage === 100, "deployment_invalid");
    report.version_id = versions[0].version_id;
    const version = await this.api(report, "authenticated_version", `${this.script(report)}/versions/${report.version_id}`);
    requireThat(version.result?.annotations?.["workers/tag"] === report.run_id, "version_tag_mismatch");
    const subdomain = await this.api(report, "workers_subdomain", "workers/subdomain");
    requireThat(/^[a-z0-9-]+$/.test(subdomain.result?.subdomain), "subdomain_invalid");
    this.baseUrl = `https://${report.worker}.${subdomain.result.subdomain}.workers.dev`;
    for (let n = 0; n < 12; n++) {
      try {
        const status = await this.http(report);
        requireThat(!status.locked, "contention_initial_lock_present");
        report.http_ready = true;
        await this.save(report);
        return;
      } catch (error) { if (n === 11) { throw error; } await this.sleep(5000); }
    }
  }

  async http(report, mode, owner = "") {
    requireThat(this.baseUrl && Boolean(this.env.INTERNAL_API_TOKEN), "contention_http_not_ready");
    let response;
    try {
      response = await this.request(`${this.baseUrl}${mode ? "/sync/discord-notion" : "/admin/cron-lock"}`, {
        method: mode ? "POST" : "GET", redirect: "error", signal: AbortSignal.timeout(mode === "hold" ? 110_000 : 20_000),
        headers: { Authorization: `Bearer ${this.env.INTERNAL_API_TOKEN}`, "X-E2E-Run-ID": report.run_id,
          "X-E2E-Probe-Mode": mode ?? "", "X-E2E-Owner": owner },
      });
    } catch { throw new Error("cron_contention_http_transport_failed"); }
    report.audit.push({ operation: mode ? `manual_${mode}` : "lock_status", method: mode ? "POST" : "GET",
      status: response.status, at_ms: this.now() });
    await this.save(report);
    if (mode && response.status === 412) { return null; }
    requireThat(response.status === 200 || (mode === "probe" && response.status === 409), "contention_http_failed");
    const value = await response.json();
    sameIdentity(value, report);
    if (!mode) {
      requireThat(typeof value.locked === "boolean" && ["", "cron", "manual"].includes(value.holder)
        && (value.locked ? /^[a-f0-9]{64}$/.test(value.owner_sha256) : value.owner_sha256 === ""), "contention_lock_invalid");
    }
    if (mode) { requireThat(value.status === response.status && value.mode === mode, "contention_http_status_mismatch"); }
    return value;
  }

  async observe(report) {
    if (!report.manual_rejected) {
      // KVの伝播を待たず、DOの強整合なstatusで保持中のCronを探す。
      const status = await this.http(report);
      if (status.holder === "cron") {
        const rejected = await this.http(report, "probe", status.owner_sha256);
        if (rejected) {
          requireThat(rejected.status === 409 && rejected.body_calls === 0 && rejected.result_writes === 0
            && rejected.before.owner_sha256 === rejected.after.owner_sha256 && rejected.after.holder === "cron",
          "manual_not_rejected");
          report.manual_rejected = rejected;
        }
      }
    }
    if (report.manual_rejected && !report.manual_hold) {
      const status = await this.http(report);
      if (!status.locked) {
        const held = await this.http(report, "hold");
        if (held) {
          requireThat(held.status === 200 && held.body_calls === 1 && held.result_writes === 1 && !held.after.locked,
            "manual_hold_failed");
          report.manual_hold = held;
        }
      }
    }
    const rejected = report.receipts.find((r) => r.status === 409 && r.before.holder === "manual"
      && r.before.owner_sha256 === r.after.owner_sha256 && r.after.locked
      && r.before.owner_sha256 === report.manual_hold?.body_owner);
    const completed = report.receipts.find((r) => r.status === 200 && !r.after.locked
      && r.body_owner === report.manual_rejected?.before.owner_sha256);
    if (!(report.manual_rejected && report.manual_hold && rejected && completed)) { return false; }
    report.cron_rejected = rejected;
    await this.api(report, "schedule_disable_for_final_check", `${this.script(report)}/schedules`, { method: "PUT", body: [] });
    await this.beforeDelete(report);
    const after = await this.http(report, "after");
    requireThat(after?.status === 200 && after.body_calls === 1 && after.result_writes === 1 && !after.after.locked,
      "contention_recovery_failed");
    report.manual_after = after;
    // 通常StateStoreで保存した結果を管理APIから独立に読む。
    for (let n = 0; n < 6; n++) {
      const key = `${report.prefix}result`;
      const value = await this.api(report, "result_readback", `${this.kv()}/values/${encodeURIComponent(key)}`, { missing: true });
      if (value && validateContention(value, report, key).result_mode === "after") {
        report.result_readback = { mode: "after", identity_matches: true };
        return true;
      }
      await this.sleep(10_000);
    }
    throw new Error("cron_contention_result_not_observed");
  }

  async beforeDelete(report) {
    if (!report.http_ready) { return; }
    // finallyによる解放を待つ。所有者不明のロックを強制解放しない。
    if (!this.baseUrl) {
      const subdomain = await this.api(report, "cleanup_subdomain", "workers/subdomain");
      requireThat(/^[a-z0-9-]+$/.test(subdomain.result?.subdomain), "subdomain_invalid");
      this.baseUrl = `https://${report.worker}.${subdomain.result.subdomain}.workers.dev`;
    }
    for (let n = 0; n < 22; n++) {
      const status = await this.http(report);
      if (!status.locked) { report.cleanup.do_lock_absent = true; return; }
      await this.sleep(5000);
    }
    throw new Error("cron_contention_lock_not_released");
  }
}
