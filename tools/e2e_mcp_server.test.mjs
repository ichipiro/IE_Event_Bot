import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { EventEmitter } from "node:events";
import test from "node:test";

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";

import {
  RUN_ID_PATTERN,
  TOOL_NAMES,
  appendAuditEntry,
  createE2eMcpServer,
  deployDedicatedWorker,
  deploymentEnvironment,
  loadE2eEnvironment,
  readAuditEntries,
} from "./e2e_mcp_server.mjs";


const RUN_ID = "E2E-20260901T000000Z-1234abcd";
const ENV = Object.freeze({
  E2E_WORKER_URL: "https://ie-event-bot-e2e.personal.workers.dev",
  E2E_WORKER_URL_SHA256: createHash("sha256")
    .update("https://ie-event-bot-e2e.personal.workers.dev")
    .digest("hex"),
  INTERNAL_API_TOKEN: "test-internal-token",
  CLOUDFLARE_ACCOUNT_ID: "a".repeat(32),
  CLOUDFLARE_API_TOKEN: "test-cloudflare-api-token",
});
const PLAYWRIGHT_ARGS = [
  "--isolated",
  "--browser",
  "chromium",
  "--block-service-workers",
  "--codegen",
  "none",
  "--console-level",
  "warning",
  "--image-responses",
  "omit",
  "--output-dir",
  "test-results/playwright-mcp",
  "--output-max-size",
  "52428800",
  "--timeout-action",
  "10000",
  "--timeout-navigation",
  "60000",
];
const PLAYWRIGHT_ALLOWED_TOOLS = [
  "browser_close",
  "browser_console_messages",
  "browser_handle_dialog",
  "browser_find",
  "browser_fill_form",
  "browser_press_key",
  "browser_type",
  "browser_navigate",
  "browser_navigate_back",
  "browser_take_screenshot",
  "browser_snapshot",
  "browser_click",
  "browser_hover",
  "browser_select_option",
  "browser_tabs",
  "browser_wait_for",
];


function jsonResponse(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

for (const [scenario, verifiedStage, path] of [
  ["discord_state", "state_verified", "/admin/e2e/discord-state"],
  ["sync_lock", "lock_verified", "/admin/e2e/sync-lock"],
  ["sync_faults", "fault_verified", "/admin/e2e/sync-faults"],
  ["discord_kv", "kv_verified", "/admin/e2e/discord-kv"],
]) {
test(`${scenario}の保存と読戻しを別要求へ分け、同runだけを回収する`, async () => {
  const calls = [];
  const audit = [];
  await withClient({ env: ENV, auditImpl: async (entry) => audit.push(entry),
    fetchImpl: async (url, options) => {
      calls.push({ path: new URL(url).pathname, headers: options.headers });
      return jsonResponse({ ok: true, run_id: RUN_ID, dirty: !String(url).endsWith("/cleanup"),
        status: scenario === "sync_faults" ? "partial" : "prepared", stage: String(url).endsWith("/verify") ? verifiedStage : "state_prepared" });
    },
  }, async (client) => {
    for (const syncPhase of (scenario === "sync_faults" ? ["prepare", "advance", "resume"] : ["prepare", "resume"])) {
      const result = parseToolResult(await client.callTool({ name: "trigger_sync", arguments: {
        run_id: RUN_ID, scenario, sync_phase: syncPhase,
      } }));
      assert.equal(result.ok, true, JSON.stringify(result));
    }
    const forbidden = parseToolResult(await client.callTool({ name: "trigger_sync", arguments: {
      run_id: RUN_ID, scenario, sync_phase: "advance", ...(scenario === "sync_faults" ? { version_sha256: "a".repeat(64) } : {}),
    } }));
    assert.equal(forbidden.error, "sync_phase_forbidden");
    const cleanup = parseToolResult(await client.callTool({ name: "cleanup_run", arguments: {
      run_id: RUN_ID, service: scenario, confirmation: `cleanup:${scenario}:${RUN_ID}`,
    } }));
    assert.equal(cleanup.ok, true);
  });
  assert.deepEqual(calls.map((call) => call.path), [
    path, ...(scenario === "sync_faults" ? [`${path}/advance`] : []), `${path}/verify`, `${path}/cleanup`,
  ]);
  for (const call of calls.slice(0, -1)) {
    assert.equal(call.headers["X-E2E-Run-ID"], RUN_ID);
    assert.equal(call.headers["X-E2E-Version-Tag"], RUN_ID);
  }
  assert.equal(audit.filter((entry) => entry.phase === "finish").length, scenario === "sync_faults" ? 4 : 3);
  if (scenario === "sync_faults") {
    assert.ok(audit.some((entry) => entry.sync_phase === "advance" && entry.execution_status === "partial"));
  }
});

for (const invalid of ["stage", "dirty", "run"]) {
  test(`${scenario}の読戻し応答は${invalid}不一致を成功扱いしない`, async () => {
    const payload = { ok: true, dirty: true, stage: verifiedStage, run_id: RUN_ID };
    if (invalid === "stage") {
      payload.stage = "state_prepared";
    } else if (invalid === "dirty") {
      payload.dirty = false;
    } else {
      payload.run_id = "E2E-20260901T000001Z-aaaaaaaa";
    }
    await withClient({ env: ENV, auditImpl: async () => {}, fetchImpl: async () => jsonResponse(payload) },
      async (client) => {
        const result = parseToolResult(await client.callTool({ name: "trigger_sync", arguments: {
          run_id: RUN_ID, scenario, sync_phase: "resume",
        } }));
        assert.equal(result.ok, false);
        assert.equal(result.error, invalid === "run" ? "worker_run_id_mismatch" : `${scenario}_not_ready`);
      });
  });
}

}


test("2件シナリオの固定経路とdrained応答を監査する", async () => {
  const calls = [], audit = [];
  await withClient({ env: ENV, auditImpl: async (entry) => audit.push(entry),
    fetchImpl: async (url, options) => {
      calls.push({ path: new URL(url).pathname, headers: options.headers });
      return jsonResponse({ ok: true, run_id: RUN_ID, dirty: !url.endsWith("/cleanup"),
        status: url.endsWith("/advance") ? "drained" : "prepared", stage: "batch_pending_verified" });
    },
  }, async (client) => {
    for (const phase of ["prepare", "resume", "advance"]) {
      const result = parseToolResult(await client.callTool({ name: "trigger_sync", arguments: {
        run_id: RUN_ID, scenario: "discord_batch", sync_phase: phase,
      } }));
      assert.equal(result.ok, true);
      if (phase === "advance") assert.equal(result.execution_status, "drained");
    }
    const result = parseToolResult(await client.callTool({ name: "cleanup_run", arguments: {
      run_id: RUN_ID, service: "discord_batch", confirmation: `cleanup:discord_batch:${RUN_ID}`,
    } }));
    assert.equal(result.ok, true);
  });
  assert.deepEqual(calls.map((c) => c.path), ["/admin/e2e/discord-batch", "/admin/e2e/discord-batch/verify",
    "/admin/e2e/discord-batch/advance", "/admin/e2e/discord-batch/cleanup"]);
  assert.ok(calls.slice(0, 3).every((c) => c.headers["X-E2E-Version-Tag"] === RUN_ID));
  assert.ok(audit.some((a) => a.sync_phase === "advance" && a.execution_status === "drained"));
});


for (const phase of ["prepare", "resume", "advance"]) {
  test(`2件シナリオの${phase}は不正な完了応答を拒否する`, async () => {
    await withClient({ env: ENV, auditImpl: async () => {}, fetchImpl: async () => jsonResponse({
      ok: true, dirty: true, run_id: RUN_ID, status: "wrong", stage: "wrong",
    }) }, async (client) => {
      const result = parseToolResult(await client.callTool({ name: "trigger_sync", arguments: {
        run_id: RUN_ID, scenario: "discord_batch", sync_phase: phase,
      } }));
      assert.equal(result.ok, false);
      assert.equal(result.error, "discord_batch_not_ready");
    });
  });
}


function parseToolResult(result) {
  assert.equal(result.content.length, 1);
  assert.equal(result.content[0].type, "text");
  return JSON.parse(result.content[0].text);
}


async function withClient(options, callback) {
  const server = createE2eMcpServer(options);
  const client = new Client({ name: "e2e-mcp-test", version: "1.0.0" });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  await Promise.all([client.connect(clientTransport), server.connect(serverTransport)]);
  try {
    return await callback(client);
  } finally {
    await client.close();
    await server.close();
  }
}


test("Google付き2件シナリオの固定経路とdrained応答を監査する", async () => {
  const calls = [], audit = [];
  await withClient({ env: ENV, auditImpl: async (entry) => audit.push(entry),
    fetchImpl: async (url, options) => {
      calls.push({ path: new URL(url).pathname, headers: options.headers });
      return jsonResponse({ ok: true, run_id: RUN_ID, dirty: !url.endsWith("/cleanup"),
        status: url.endsWith("/advance") ? "drained" : "prepared", stage: "batch_pending_verified" });
    },
  }, async (client) => {
    for (const phase of ["prepare", "resume", "advance"]) {
      const result = parseToolResult(await client.callTool({ name: "trigger_sync", arguments: {
        run_id: RUN_ID, scenario: "discord_batch_google", sync_phase: phase,
      } }));
      assert.equal(result.ok, true);
      if (phase === "advance") { assert.equal(result.execution_status, "drained"); }
    }
    const result = parseToolResult(await client.callTool({ name: "cleanup_run", arguments: {
      run_id: RUN_ID, service: "discord_batch_google", confirmation: `cleanup:discord_batch_google:${RUN_ID}`,
    } }));
    assert.equal(result.ok, true);
  });
  assert.deepEqual(calls.map((c) => c.path), ["/admin/e2e/discord-batch-google", "/admin/e2e/discord-batch-google/verify",
    "/admin/e2e/discord-batch-google/advance", "/admin/e2e/discord-batch-google/cleanup"]);
  assert.ok(calls.slice(0, 3).every((c) => c.headers["X-E2E-Version-Tag"] === RUN_ID));
  assert.ok(audit.some((a) => a.sync_phase === "advance" && a.execution_status === "drained"));
});


for (const phase of ["prepare", "resume", "advance"]) {
  test(`Google付き2件シナリオの${phase}は不正な完了応答を拒否する`, async () => {
    await withClient({ env: ENV, auditImpl: async () => {}, fetchImpl: async () => jsonResponse({
      ok: true, dirty: true, run_id: RUN_ID, status: "wrong", stage: "wrong",
    }) }, async (client) => {
      const result = parseToolResult(await client.callTool({ name: "trigger_sync", arguments: {
        run_id: RUN_ID, scenario: "discord_batch_google", sync_phase: phase,
      } }));
      assert.equal(result.ok, false);
      assert.equal(result.error, "discord_batch_google_not_ready");
    });
  });
}




test("通知シナリオはretry_drainedと中間読戻しを監査し所有対象を回収する", async () => {
  const calls = [], audit = [];
  let advances = 0;
  const scenario = "discord_batch_notification";
  await withClient({ env: ENV, auditImpl: async (entry) => audit.push(entry),
    fetchImpl: async (url, options) => {
      calls.push({ path: new URL(url).pathname, headers: options.headers });
      if (url.endsWith("/advance")) { advances += 1; }
      return jsonResponse({ ok: true, run_id: RUN_ID, dirty: !url.endsWith("/cleanup"),
        status: url.endsWith("/advance") ? (advances === 1 ? "retry_drained" : "drained") : "prepared",
        stage: ["batch_pending_verified", "batch_retry_verified", "batch_verified"][advances] });
    },
  }, async (client) => {
    for (const phase of ["prepare", "resume", "advance", "resume", "advance", "resume"]) {
      const result = parseToolResult(await client.callTool({ name: "trigger_sync", arguments: {
        run_id: RUN_ID, scenario, sync_phase: phase,
      } }));
      assert.equal(result.ok, true);
      if (phase === "advance") { assert.equal(result.execution_status, advances === 1 ? "retry_drained" : "drained"); }
    }
    const result = parseToolResult(await client.callTool({ name: "cleanup_run", arguments: {
      run_id: RUN_ID, service: scenario, confirmation: `cleanup:${scenario}:${RUN_ID}`,
    } }));
    assert.equal(result.ok, true);
  });
  const root = "/admin/e2e/discord-batch-notification";
  assert.deepEqual(calls.map((c) => c.path), [root, root + "/verify", root + "/advance",
    root + "/verify", root + "/advance", root + "/verify", root + "/cleanup"]);
  assert.ok(calls.slice(0, 6).every((c) => c.headers["X-E2E-Version-Tag"] === RUN_ID));
  assert.ok(audit.some((a) => a.sync_phase === "advance" && a.execution_status === "retry_drained"));
});


for (const phase of ["prepare", "resume", "advance"]) {
  test(`通知シナリオの${phase}は不正な応答を成功にしない`, async () => {
    await withClient({ env: ENV, auditImpl: async () => {}, fetchImpl: async () => jsonResponse({
      ok: true, dirty: true, run_id: RUN_ID, status: "wrong", stage: "wrong",
    }) }, async (client) => {
      const result = parseToolResult(await client.callTool({ name: "trigger_sync", arguments: {
        run_id: RUN_ID, scenario: "discord_batch_notification", sync_phase: phase,
      } }));
      assert.equal(result.ok, false);
      assert.equal(result.error, "discord_batch_notification_not_ready");
    });
  });
}


test("E2E Worker URLを固定host以外へ向けられない", () => {
  const production = loadE2eEnvironment({
    ...ENV,
    E2E_WORKER_URL: "https://production.example.com/",
  });
  const pathInjected = loadE2eEnvironment({
    ...ENV,
    E2E_WORKER_URL: `${ENV.E2E_WORKER_URL}/admin`,
  });
  const otherAccount = loadE2eEnvironment({
    ...ENV,
    E2E_WORKER_URL: "https://ie-event-bot-e2e.attacker.workers.dev",
  });

  assert.equal(production.ok, false);
  assert.deepEqual(production.issues, ["invalid_e2e_worker_url"]);
  assert.equal(pathInjected.ok, false);
  assert.deepEqual(pathInjected.issues, ["invalid_e2e_worker_url"]);
  assert.equal(otherAccount.ok, false);
  assert.deepEqual(otherAccount.issues, ["e2e_worker_url_fingerprint_mismatch"]);
  assert.match(RUN_ID, RUN_ID_PATTERN);
});


test("監査パスとdeploy環境へ任意値やSecretを渡さない", async () => {
  await assert.rejects(
    appendAuditEntry({ run_id: "../../outside", tool: "seed_fixture" }),
    /audit_run_id_invalid/,
  );
  await assert.rejects(readAuditEntries("../../outside"), /audit_run_id_invalid/);

  const selected = deploymentEnvironment({
    HOME: "/safe-home",
    PATH: "/safe-path",
    INTERNAL_API_TOKEN: "must-not-be-inherited",
    CLOUDFLARE_API_TOKEN: "must-not-be-inherited",
    GOOGLE_SERVICE_ACCOUNT_JSON_B64: "must-not-be-inherited",
  });
  assert.deepEqual(selected, { HOME: "/safe-home", PATH: "/safe-path" });
});


test("Wrangler deployへ検証対象run IDだけをversion tagとして渡す", async () => {
  let invocation;
  const spawnImpl = (command, args, options) => {
    invocation = { command, args, options };
    const child = new EventEmitter();
    child.kill = () => {};
    queueMicrotask(() => child.emit("close", 0));
    return child;
  };

  const result = await deployDedicatedWorker(
    loadE2eEnvironment(ENV),
    RUN_ID,
    spawnImpl,
  );

  assert.deepEqual(result, { ok: true, status: 0, error: null });
  assert.equal(invocation.command, "npm");
  assert.deepEqual(invocation.args.slice(-2), ["--tag", RUN_ID]);
  assert.equal(invocation.options.shell, false);
  assert.equal(invocation.options.env.CLOUDFLARE_ACCOUNT_ID, ENV.CLOUDFLARE_ACCOUNT_ID);
  assert.equal(invocation.options.env.CLOUDFLARE_API_TOKEN, ENV.CLOUDFLARE_API_TOKEN);
  assert.equal("INTERNAL_API_TOKEN" in invocation.options.env, false);
});


test("公開ツールを12件に固定して任意URLや資源IDを受け取らない", async () => {
  await withClient({ env: ENV }, async (client) => {
    const listed = await client.listTools();
    const names = listed.tools.map((tool) => tool.name);
    assert.deepEqual(names, TOOL_NAMES);

    const allowedFields = new Set([
      "run_id",
      "service",
      "scenario", "sync_phase", "version_sha256", "previous_version_sha256", "response_mode",
      "job",
      "confirmation",
    ]);
    for (const tool of listed.tools) {
      const properties = tool.inputSchema.properties ?? {};
      for (const field of Object.keys(properties)) {
        assert.equal(allowedFields.has(field), true, `${tool.name}:${field}`);
      }
    }

    const annotations = Object.fromEntries(
      listed.tools.map((tool) => [tool.name, tool.annotations ?? {}]),
    );
    for (const name of [
      "preflight",
      "read_status",
      "assert_external_state",
      "collect_evidence",
    ]) {
      assert.equal(annotations[name].readOnlyHint, true);
    }
    for (const name of [
      "deploy_e2e",
      "seed_fixture",
      "trigger_sync",
      "trigger_webhook",
      "trigger_webhook_delivery",
      "trigger_webhook_change",
      "trigger_job",
      "cleanup_run",
    ]) {
      assert.equal(annotations[name].readOnlyHint, false);
      assert.equal(annotations[name].destructiveHint, true);
    }
  });
});


test("preflightは固定routeだけを読み応答中のIDをマスクする", async () => {
  const calls = [];
  let orchestratedWritesEnabled = false;
  const fetchImpl = async (url, options) => {
    calls.push({ url, options });
    if (url.endsWith("/health")) {
      return jsonResponse({ ok: true, kv_state_enabled: true });
    }
    return jsonResponse({
      ok: true,
      mode: "e2e",
      kv_enabled: true,
      e2e_manifest_enabled: true,
      orchestrated_writes_enabled: orchestratedWritesEnabled,
      legacy_manifest_check_complete: true,
      legacy_manifests: {
        google: { present: false, dirty: false },
        discord: { present: false, dirty: false },
        notion: { present: false, dirty: false },
      },
      routes_enabled: { google: true, discord: true, notion: true },
      scenario_routes_enabled: {
        discord_google: true,
        discord_notion: true,
        discord_delta: true,
        google_discord: true,
        google_notion: true,
        qa_notification: true,
        reminder: true,
        notion_cleanup: true,
        webhook_dispatch: true,
        webhook_delivery: true,
        webhook_change: true,
      },
      required_envs: Object.fromEntries([
        "notion_token",
        "notion_internal_db",
        "notion_qa_db",
        "google_calendar_id",
        "gcal_webhook_url",
        "gcal_webhook_token",
        "discord_token",
        "discord_guild_id",
        "discord_event_channel",
        "discord_event_role",
        "discord_qa_channel",
        "discord_reminder_channel",
        "discord_reminder_role",
      ].map((key) => [key, true])),
      google_auth: { direct_env: true },
      sync_lock: { enabled: true, ok: true, status: 200, owner: "sensitive-owner" },
      worker_version: {
        present: true,
        id_sha256: "a".repeat(64),
        tag: RUN_ID,
        timestamp: "2026-09-01T00:00:00.000Z",
      },
      watch: {
        present: true,
        channel_id_sha256: "b".repeat(64),
      },
      services: {
        google: { present: true, dirty: false, run_id: RUN_ID, outcome: "passed" },
        discord: { present: false, dirty: false, run_id: null },
        notion: { present: false, dirty: false, run_id: null },
      },
      scenarios: {
        discord_google: { present: false, dirty: false, run_id: null },
        discord_notion: { present: false, dirty: false, run_id: null },
        discord_delta: { present: false, dirty: false, run_id: null },
        google_discord: { present: false, dirty: false, run_id: null },
        google_notion: { present: false, dirty: false, run_id: null },
        qa_notification: { present: false, dirty: false, run_id: null },
        reminder: { present: false, dirty: false, run_id: null },
        notion_cleanup: { present: false, dirty: false, run_id: null },
        webhook_dispatch: { present: false, dirty: false, run_id: null },
        webhook_delivery: { present: false, dirty: false, run_id: null },
        webhook_change: { present: false, dirty: false, run_id: null },
      },
    });
  };

  await withClient({ env: ENV, fetchImpl }, async (client) => {
    const result = await client.callTool({
      name: "preflight",
      arguments: { run_id: RUN_ID },
    });
    const payload = parseToolResult(result);
    const serialized = JSON.stringify(payload);

    assert.equal(payload.ok, true);
    assert.deepEqual(
      calls.map((call) => call.url),
      [
        `${ENV.E2E_WORKER_URL}/health`,
        `${ENV.E2E_WORKER_URL}/admin/e2e/status`,
      ],
    );
    assert.equal(calls.every((call) => call.options.method === "GET"), true);
    assert.equal(
      calls.every((call) => call.options.headers["X-E2E-Run-ID"] === RUN_ID),
      true,
    );
    assert.equal(serialized.includes("sensitive-watch-id"), false);
    assert.equal(serialized.includes("sensitive-event-id"), false);
    assert.equal(serialized.includes("sensitive-owner"), false);
    assert.equal(serialized.includes(ENV.INTERNAL_API_TOKEN), false);
    assert.equal(serialized.includes(ENV.E2E_WORKER_URL), false);
    assert.equal(Object.values(payload.checks).every(Boolean), true);
    assert.equal(payload.checks.unowned_writes_blocked, true);
    assert.equal(payload.checks.scenario_routes, true);
    assert.equal(payload.e2e_status.orchestrated_writes_enabled, false);
    assert.equal(payload.e2e_status.worker_version.tag, RUN_ID);
    assert.equal(payload.error, null);

    orchestratedWritesEnabled = true;
    const unsafeResult = await client.callTool({
      name: "preflight",
      arguments: { run_id: RUN_ID },
    });
    const unsafePayload = parseToolResult(unsafeResult);
    assert.equal(unsafePayload.ok, false);
    assert.equal(unsafePayload.checks.unowned_writes_blocked, false);
    assert.equal(unsafePayload.error, "preflight_unowned_writes_blocked_failed");
  });
});


test("trigger_syncとcleanupは選択した所有資源routeだけを使う", async () => {
  const calls = [];
  const audit = [];
  const fetchImpl = async (url, options) => {
    calls.push({ url, options });
    return jsonResponse({
      ok: true,
      dirty: false,
      run_id: RUN_ID,
      stages: { application_apply: 200 },
      cleanup: { ok: true, attempts: 1 },
    });
  };

  await withClient(
    { env: ENV, fetchImpl, auditImpl: async (entry) => audit.push(entry) },
    async (client) => {
      for (const scenario of [
        "google_notion",
        "google_discord",
        "discord_notion",
        "discord_delta",
        "discord_google",
      ]) {
        const syncResult = await client.callTool({
          name: "trigger_sync",
          arguments: { run_id: RUN_ID, scenario },
        });
        const cleanupResult = await client.callTool({
          name: "cleanup_run",
          arguments: {
            run_id: RUN_ID,
            service: scenario,
            confirmation: `cleanup:${scenario}:${RUN_ID}`,
          },
        });

        assert.equal(parseToolResult(syncResult).ok, true);
        assert.equal(parseToolResult(cleanupResult).ok, true);
      }
    },
  );

  assert.deepEqual(
    calls.map((call) => call.url),
    [
      `${ENV.E2E_WORKER_URL}/admin/e2e/google-notion-sync`,
      `${ENV.E2E_WORKER_URL}/admin/e2e/google-notion-sync/cleanup`,
      `${ENV.E2E_WORKER_URL}/admin/e2e/google-discord-sync`,
      `${ENV.E2E_WORKER_URL}/admin/e2e/google-discord-sync/cleanup`,
      `${ENV.E2E_WORKER_URL}/admin/e2e/discord-notion-sync`,
      `${ENV.E2E_WORKER_URL}/admin/e2e/discord-notion-sync/cleanup`,
      `${ENV.E2E_WORKER_URL}/admin/e2e/discord-delta-sync`,
      `${ENV.E2E_WORKER_URL}/admin/e2e/discord-delta-sync/cleanup`,
      `${ENV.E2E_WORKER_URL}/admin/e2e/discord-google-sync`,
      `${ENV.E2E_WORKER_URL}/admin/e2e/discord-google-sync/cleanup`,
    ],
  );
  assert.equal(calls.every((call) => call.options.method === "POST"), true);
  assert.deepEqual(
    audit.filter((entry) => entry.phase === "start").map((entry) => entry.target),
    [
      "google_notion",
      "google_notion",
      "google_discord",
      "google_discord",
      "discord_notion",
      "discord_notion",
      "discord_delta",
      "discord_delta",
      "discord_google",
      "discord_google",
    ],
  );
});


test("Discord差分のprepare・advance・resumeを固定routeへ送り監査に残す", async () => {
  const calls = [];
  const audit = [];
  await withClient({
    env: ENV,
    auditImpl: async (entry) => audit.push(entry),
    readAuditImpl: async () => audit,
    repositoryMetadataImpl: async () => ({ git_sha: "c".repeat(40), dirty: true }),
    fetchImpl: async (url) => {
      calls.push(url);
      return jsonResponse({ ok: true, run_id: RUN_ID,
        status: url.endsWith("/prepare") ? "prepared" : url.endsWith("/advance") ? "updated" : "completed",
        dirty: !url.endsWith("/resume"),
      });
    },
  }, async (client) => {
    for (const sync_phase of ["prepare", "advance", "resume"]) {
      const result = parseToolResult(await client.callTool({ name: "trigger_sync",
        arguments: { run_id: RUN_ID, scenario: "discord_delta", sync_phase },
      }));
      assert.equal(result.ok, true);
    }
    const result = parseToolResult(await client.callTool({ name: "collect_evidence",
      arguments: { run_id: RUN_ID },
    }));
    assert.deepEqual(result.manifest.operations.map((op) => op.route), [
      "/admin/e2e/discord-delta-sync/prepare", "/admin/e2e/discord-delta-sync/advance",
      "/admin/e2e/discord-delta-sync/resume",
    ]);
  });
  assert.deepEqual(calls.slice(0, 3), [
    `${ENV.E2E_WORKER_URL}/admin/e2e/discord-delta-sync/prepare`,
    `${ENV.E2E_WORKER_URL}/admin/e2e/discord-delta-sync/advance`,
    `${ENV.E2E_WORKER_URL}/admin/e2e/discord-delta-sync/resume`,
  ]);
  assert.deepEqual(audit.filter((entry) => entry.phase === "start").map((entry) => entry.sync_phase),
    ["prepare", "advance", "resume"]);
});

test("Discord差分は副作用前のversion不一致だけを待機して再送する", async () => {
  let calls = 0;
  const waits = [];
  await withClient({ env: ENV, auditImpl: async () => {},
    delayImpl: async (ms) => waits.push(ms),
    fetchImpl: async (url, options) => {
      calls += 1;
      assert.equal(options.headers["X-E2E-Version-Tag"], RUN_ID);
      assert.equal(options.headers["X-E2E-Version-ID-SHA256"], "c".repeat(64));
      return calls === 1
        ? jsonResponse({ ok: false, error: "worker_version_mismatch" }, 409)
        : jsonResponse({ ok: true, run_id: RUN_ID, dirty: true, status: "prepared" });
    },
  }, async (client) => {
    const result = parseToolResult(await client.callTool({ name: "trigger_sync",
      arguments: { run_id: RUN_ID, scenario: "discord_delta", sync_phase: "prepare", version_sha256: "c".repeat(64) },
    }));
    assert.equal(result.ok, true);
    assert.equal(calls, 2);
    assert.deepEqual(waits, [3000]);
  });
});

test("再送の固定応答だけを監査ファイルとmanifestへ残す", async () => {
  const runId = "E2E-20260911T000000Z-abcdef01";
  const audit = [];
  const replies = ["updated", "already_completed", "private_response_text"];
  let count = 0;
  await withClient({ env: ENV,
    auditImpl: async (entry) => audit.push(entry),
    readAuditImpl: async () => audit,
    repositoryMetadataImpl: async () => ({ git_sha: "c".repeat(40), dirty: false }),
    fetchImpl: async () => jsonResponse({ ok: true, run_id: runId, dirty: false, status: replies[count++] }),
  }, async (client) => {
    for (const sync_phase of ["run", "resume", "run"]) {
      const result = parseToolResult(await client.callTool({ name: "trigger_sync",
        arguments: { run_id: runId, scenario: "discord_delta", sync_phase },
      }));
      assert.equal(result.ok, true);
    }
    const result = parseToolResult(await client.callTool({ name: "collect_evidence",
      arguments: { run_id: runId },
    }));
    assert.deepEqual(result.manifest.operations.map((op) => op.execution_status),
      ["updated", "already_completed", null]);
    assert.equal(JSON.stringify(audit).includes("private_response_text"), false);
  });
  // 実際のJSONL書込み・読戻しでも任意文字列を取り込まない。
  for (const execution_status of replies) {
    await appendAuditEntry({ run_id: runId, tool: "trigger_sync", target: "discord_delta",
      phase: "finish", ok: true, status: 200, execution_status,
      version_sha256: execution_status === "updated" ? "c".repeat(64) : "private_version",
      previous_version_sha256: "private_previous_version" });
  }
  const saved = await readAuditEntries(runId);
  assert.deepEqual(saved.slice(-3).map((entry) => entry.version_sha256), ["c".repeat(64), null, null]);
  assert.deepEqual(saved.slice(-3).map((entry) => entry.previous_version_sha256), [null, null, null]);
  assert.deepEqual(saved.slice(-3).map((entry) => entry.execution_status),
    ["updated", "already_completed", null]);
});

test("Discord差分以外の分割実行と不完全なprepare・advance・resume応答を拒否する", async () => {
  const calls = [];
  await withClient({ env: ENV, auditImpl: async () => {},
    fetchImpl: async (url) => {
      calls.push(url);
      return jsonResponse({ ok: true, run_id: RUN_ID, dirty: true });
    },
  }, async (client) => {
    const forbidden = parseToolResult(await client.callTool({ name: "trigger_sync",
      arguments: { run_id: RUN_ID, scenario: "google_notion", sync_phase: "resume" },
    }));
    assert.equal(forbidden.error, "sync_phase_forbidden");
    assert.equal(calls.length, 0);
    for (const sync_phase of ["prepare", "advance", "resume"]) {
      const result = parseToolResult(await client.callTool({ name: "trigger_sync",
        arguments: { run_id: RUN_ID, scenario: "discord_delta", sync_phase },
      }));
      assert.equal(result.ok, false);
      assert.equal(result.error, sync_phase === "prepare" ? "delta_prepare_not_ready"
        : sync_phase === "advance" ? "delta_advance_not_ready" : "delta_resume_incomplete");
    }
  });
});

test("trigger_webhookとcleanupは専用ingress simulation routeだけを使う", async () => {
  const calls = [];
  const audit = [];
  const fetchImpl = async (url, options) => {
    calls.push({ url, options });
    return jsonResponse({
      ok: true,
      dirty: false,
      run_id: RUN_ID,
      stages: { webhook_dispatch: 200 },
      cleanup: { ok: true, attempts: 1 },
    });
  };

  await withClient(
    { env: ENV, fetchImpl, auditImpl: async (entry) => audit.push(entry) },
    async (client) => {
      const triggerResult = await client.callTool({
        name: "trigger_webhook",
        arguments: { run_id: RUN_ID },
      });
      const cleanupResult = await client.callTool({
        name: "cleanup_run",
        arguments: {
          run_id: RUN_ID,
          service: "webhook_dispatch",
          confirmation: `cleanup:webhook_dispatch:${RUN_ID}`,
        },
      });

      assert.equal(parseToolResult(triggerResult).ok, true);
      assert.equal(parseToolResult(cleanupResult).ok, true);
    },
  );

  assert.deepEqual(
    calls.map((call) => call.url),
    [
      `${ENV.E2E_WORKER_URL}/admin/e2e/trigger-webhook`,
      `${ENV.E2E_WORKER_URL}/admin/e2e/trigger-webhook/cleanup`,
    ],
  );
  assert.equal(calls.every((call) => call.options.method === "POST"), true);
  assert.deepEqual(
    audit.filter((entry) => entry.phase === "start").map((entry) => entry.target),
    ["webhook_dispatch", "webhook_dispatch"],
  );
});


test("trigger_webhook_deliveryとcleanupは短命watch専用routeだけを使う", async () => {
  const calls = [];
  const audit = [];
  const fetchImpl = async (url, options) => {
    calls.push({ url, options });
    return jsonResponse({
      ok: true,
      dirty: false,
      run_id: RUN_ID,
      stages: {
        watch_create: 200,
        webhook_sync_delivery: 204,
        watch_stop: 204,
      },
      cleanup: { ok: true, attempts: 1 },
    });
  };

  await withClient(
    { env: ENV, fetchImpl, auditImpl: async (entry) => audit.push(entry) },
    async (client) => {
      const triggerResult = await client.callTool({
        name: "trigger_webhook_delivery",
        arguments: { run_id: RUN_ID },
      });
      const cleanupResult = await client.callTool({
        name: "cleanup_run",
        arguments: {
          run_id: RUN_ID,
          service: "webhook_delivery",
          confirmation: `cleanup:webhook_delivery:${RUN_ID}`,
        },
      });

      assert.equal(parseToolResult(triggerResult).ok, true);
      assert.equal(parseToolResult(cleanupResult).ok, true);
    },
  );

  assert.deepEqual(
    calls.map((call) => call.url),
    [
      `${ENV.E2E_WORKER_URL}/admin/e2e/google-webhook-delivery`,
      `${ENV.E2E_WORKER_URL}/admin/e2e/google-webhook-delivery/cleanup`,
    ],
  );
  assert.equal(calls.every((call) => call.options.method === "POST"), true);
  assert.deepEqual(
    audit.filter((entry) => entry.phase === "start").map((entry) => entry.target),
    ["webhook_delivery", "webhook_delivery"],
  );
});


test("trigger_webhook_changeとcleanupは実exists通知専用routeだけを使う", async () => {
  const calls = [];
  const audit = [];
  const fetchImpl = async (url, options) => {
    calls.push({ url, options });
    return jsonResponse({
      ok: true,
      dirty: false,
      run_id: RUN_ID,
      stages: {
        watch_create: 200,
        webhook_exists_delivery: 204,
        watch_stop: 204,
      },
      cleanup: { ok: true, attempts: 1 },
    });
  };

  await withClient(
    { env: ENV, fetchImpl, auditImpl: async (entry) => audit.push(entry) },
    async (client) => {
      const triggerResult = await client.callTool({
        name: "trigger_webhook_change",
        arguments: { run_id: RUN_ID },
      });
      const cleanupResult = await client.callTool({
        name: "cleanup_run",
        arguments: {
          run_id: RUN_ID,
          service: "webhook_change",
          confirmation: `cleanup:webhook_change:${RUN_ID}`,
        },
      });

      assert.equal(parseToolResult(triggerResult).ok, true);
      assert.equal(parseToolResult(cleanupResult).ok, true);
    },
  );

  assert.deepEqual(
    calls.map((call) => call.url),
    [
      `${ENV.E2E_WORKER_URL}/admin/e2e/google-webhook-change`,
      `${ENV.E2E_WORKER_URL}/admin/e2e/google-webhook-change/cleanup`,
    ],
  );
  assert.equal(calls.every((call) => call.options.method === "POST"), true);
  assert.deepEqual(
    audit.filter((entry) => entry.phase === "start").map((entry) => entry.target),
    ["webhook_change", "webhook_change"],
  );
});


test("trigger_jobはjobごとの所有資源限定routeだけを使う", async () => {
  const calls = [];
  const audit = [];
  const fetchImpl = async (url, options) => {
    calls.push({ url, options });
    return jsonResponse({
      ok: true,
      dirty: false,
      run_id: RUN_ID,
      stages: { job_notify: 200 },
      cleanup: { ok: true, attempts: 1 },
    });
  };

  await withClient(
    { env: ENV, fetchImpl, auditImpl: async (entry) => audit.push(entry) },
    async (client) => {
      const jobResult = await client.callTool({
        name: "trigger_job",
        arguments: { run_id: RUN_ID, job: "qa_check" },
      });
      const cleanupResult = await client.callTool({
        name: "cleanup_run",
        arguments: {
          run_id: RUN_ID,
          service: "qa_notification",
          confirmation: `cleanup:qa_notification:${RUN_ID}`,
        },
      });

      assert.equal(parseToolResult(jobResult).ok, true);
      assert.equal(parseToolResult(cleanupResult).ok, true);

      const reminderResult = await client.callTool({
        name: "trigger_job",
        arguments: { run_id: RUN_ID, job: "reminder" },
      });
      const reminderCleanupResult = await client.callTool({
        name: "cleanup_run",
        arguments: {
          run_id: RUN_ID,
          service: "reminder",
          confirmation: `cleanup:reminder:${RUN_ID}`,
        },
      });

      assert.equal(parseToolResult(reminderResult).ok, true);
      assert.equal(parseToolResult(reminderCleanupResult).ok, true);

      const notionCleanupResult = await client.callTool({
        name: "trigger_job",
        arguments: { run_id: RUN_ID, job: "cleanup" },
      });
      const notionCleanupCleanupResult = await client.callTool({
        name: "cleanup_run",
        arguments: {
          run_id: RUN_ID,
          service: "notion_cleanup",
          confirmation: `cleanup:notion_cleanup:${RUN_ID}`,
        },
      });

      assert.equal(parseToolResult(notionCleanupResult).ok, true);
      assert.equal(parseToolResult(notionCleanupCleanupResult).ok, true);
    },
  );

  assert.deepEqual(
    calls.map((call) => call.url),
    [
      `${ENV.E2E_WORKER_URL}/admin/e2e/qa-notification`,
      `${ENV.E2E_WORKER_URL}/admin/e2e/qa-notification/cleanup`,
      `${ENV.E2E_WORKER_URL}/admin/e2e/reminder`,
      `${ENV.E2E_WORKER_URL}/admin/e2e/reminder/cleanup`,
      `${ENV.E2E_WORKER_URL}/admin/e2e/notion-cleanup`,
      `${ENV.E2E_WORKER_URL}/admin/e2e/notion-cleanup/cleanup`,
    ],
  );
  assert.equal(calls.every((call) => call.options.method === "POST"), true);
  assert.deepEqual(
    audit.filter((entry) => entry.phase === "start").map((entry) => entry.target),
    [
      "qa_check",
      "qa_notification",
      "reminder",
      "reminder",
      "cleanup",
      "notion_cleanup",
    ],
  );
});


test("preflightとevidenceはstatusの固定エラーだけを伝える", async () => {
  const sensitiveDetail = "sensitive-worker-status-detail";
  const fetchImpl = async (url) => {
    if (url.endsWith("/health")) {
      return jsonResponse({ ok: true, kv_state_enabled: true });
    }
    return jsonResponse(
      {
        ok: false,
        error: "sync_coordinator_required",
        detail: sensitiveDetail,
      },
      503,
    );
  };

  await withClient(
    {
      env: ENV,
      fetchImpl,
      readAuditImpl: async () => [],
      repositoryMetadataImpl: async () => ({
        git_sha: "c".repeat(40),
        dirty: false,
      }),
    },
    async (client) => {
      const preflightResult = await client.callTool({
        name: "preflight",
        arguments: { run_id: RUN_ID },
      });
      const preflightPayload = parseToolResult(preflightResult);
      assert.equal(preflightPayload.ok, false);
      assert.equal(preflightPayload.error, "sync_coordinator_required");
      assert.equal(JSON.stringify(preflightPayload).includes(sensitiveDetail), false);

      const evidenceResult = await client.callTool({
        name: "collect_evidence",
        arguments: { run_id: RUN_ID },
      });
      const evidencePayload = parseToolResult(evidenceResult);
      assert.equal(evidencePayload.ok, false);
      assert.equal(evidencePayload.error, "sync_coordinator_required");
      assert.equal(JSON.stringify(evidencePayload).includes(sensitiveDetail), false);
    },
  );
});


test("preflightはdirty manifestが1件でもあれば失敗する", async () => {
  const fetchImpl = async (url) => {
    if (url.endsWith("/health")) {
      return jsonResponse({ ok: true, kv_state_enabled: true });
    }
    return jsonResponse({
      ok: true,
      services: {
        google: { present: true, dirty: true, run_id: RUN_ID },
      },
    });
  };

  await withClient({ env: ENV, fetchImpl }, async (client) => {
    const result = await client.callTool({
      name: "preflight",
      arguments: { run_id: RUN_ID },
    });
    const payload = parseToolResult(result);

    assert.equal(payload.ok, false);
    assert.equal(payload.checks.clean_manifests, false);
  });
});


test("seed_fixtureは固定service routeと同じrun IDだけを受理する", async () => {
  const calls = [];
  const audit = [];
  const fetchImpl = async (url, options) => {
    calls.push({ url, options });
    return jsonResponse({
      ok: true,
      dirty: false,
      run_id: RUN_ID,
      stages: { create: 200, delete: 204 },
      cleanup: { ok: true, attempts: 1 },
      event_id: "sensitive-resource-id",
    });
  };

  await withClient(
    {
      env: ENV,
      fetchImpl,
      auditImpl: async (entry) => audit.push(entry),
    },
    async (client) => {
      const result = await client.callTool({
        name: "seed_fixture",
        arguments: { run_id: RUN_ID, service: "google" },
      });
      const payload = parseToolResult(result);

      assert.equal(payload.ok, true);
      assert.equal(JSON.stringify(payload).includes("sensitive-resource-id"), false);
      assert.equal(calls.length, 1);
      assert.equal(calls[0].url, `${ENV.E2E_WORKER_URL}/admin/e2e/google-crud`);
      assert.equal(calls[0].options.method, "POST");
      assert.equal(calls[0].options.headers["X-E2E-Run-ID"], RUN_ID);
      assert.deepEqual(audit.map((entry) => entry.phase), ["start", "finish"]);
      assert.equal(audit.every((entry) => entry.run_id === RUN_ID), true);
    },
  );
});


test("同期lockのHTTP 200 skipを実行成功として扱わない", async () => {
  const audit = [];
  await withClient(
    {
      env: ENV,
      fetchImpl: async () => jsonResponse({ ok: true, status: "in_progress_skip" }),
      auditImpl: async (entry) => audit.push(entry),
    },
    async (client) => {
      for (const name of ["trigger_sync", "trigger_webhook"]) {
        const result = await client.callTool({
          name,
          arguments: {
            run_id: RUN_ID,
            ...(name === "trigger_sync" ? { scenario: "google_notion" } : {}),
          },
        });
        const payload = parseToolResult(result);

        assert.equal(payload.ok, false);
        assert.equal(payload.execution_status, "in_progress_skip");
        assert.equal(payload.error, "worker_in_progress_skip");
      }
    },
  );

  assert.equal(
    audit.filter((entry) => entry.phase === "finish").every((entry) => entry.ok === false),
    true,
  );
});


test("cleanupとdeployは完全一致confirmationより前に外部操作しない", async () => {
  let fetchCalls = 0;
  let deployCalls = 0;
  let auditCalls = 0;

  await withClient(
    {
      env: ENV,
      fetchImpl: async () => {
        fetchCalls += 1;
        return jsonResponse({ ok: true });
      },
      deployImpl: async () => {
        deployCalls += 1;
        return { ok: true, status: 0, error: null };
      },
      auditImpl: async () => {
        auditCalls += 1;
      },
    },
    async (client) => {
      const cleanup = await client.callTool({
        name: "cleanup_run",
        arguments: { run_id: RUN_ID, service: "notion", confirmation: "wrong" },
      });
      const deploy = await client.callTool({
        name: "deploy_e2e",
        arguments: { run_id: RUN_ID, confirmation: "wrong" },
      });

      assert.equal(parseToolResult(cleanup).error, "cleanup_confirmation_mismatch");
      assert.equal(parseToolResult(deploy).error, "deploy_confirmation_mismatch");
      assert.equal(fetchCalls, 0);
      assert.equal(deployCalls, 0);
      assert.equal(auditCalls, 0);
    },
  );
});


test("deploy_e2eは固定confirmation後もマスク済み結果だけを返す", async () => {
  const audit = [];
  let receivedConfig;
  let receivedVersionTag;

  await withClient(
    {
      env: ENV,
      deployImpl: async (config, versionTag) => {
        receivedConfig = config;
        receivedVersionTag = versionTag;
        return { ok: true, status: 0, error: null };
      },
      fetchImpl: async () => jsonResponse({
        ok: true,
        worker_version: {
          present: true,
          id_sha256: "c".repeat(64),
          tag: RUN_ID,
          timestamp: "2026-09-01T00:00:01.000Z",
        },
      }),
      auditImpl: async (entry) => audit.push(entry),
    },
    async (client) => {
      const result = await client.callTool({
        name: "deploy_e2e",
        arguments: {
          run_id: RUN_ID,
          confirmation: `deploy:ie-event-bot-e2e:${RUN_ID}`,
        },
      });
      const payload = parseToolResult(result);

      assert.equal(payload.ok, true);
      assert.equal(payload.version_verified, true);
      assert.equal(payload.verification_attempts, 1);
      assert.equal(receivedConfig.cloudflareAccountId, ENV.CLOUDFLARE_ACCOUNT_ID);
      assert.equal(receivedConfig.cloudflareApiToken, ENV.CLOUDFLARE_API_TOKEN);
      assert.equal(receivedVersionTag, RUN_ID);
      assert.equal(JSON.stringify(payload).includes(ENV.CLOUDFLARE_ACCOUNT_ID), false);
      assert.equal(JSON.stringify(payload).includes(ENV.INTERNAL_API_TOKEN), false);
      assert.equal(JSON.stringify(payload).includes(ENV.CLOUDFLARE_API_TOKEN), false);
      assert.deepEqual(audit.map((entry) => entry.phase), ["start", "finish"]);
    },
  );
});


test("deploy_e2eはrun ID tagの反映前に成功を返さない", async () => {
  const audit = [];
  let fetchCalls = 0;
  let delayCalls = 0;

  await withClient(
    {
      env: ENV,
      deployImpl: async () => ({ ok: true, status: 0, error: null }),
      fetchImpl: async () => {
        fetchCalls += 1;
        return jsonResponse({
          ok: true,
          worker_version: {
            present: true,
            id_sha256: "b".repeat(64),
            tag: "E2E-20260901T000001Z-deadbeef",
            timestamp: "2026-09-01T00:00:00.000Z",
          },
        });
      },
      delayImpl: async (milliseconds) => {
        assert.equal(milliseconds, 3_000);
        delayCalls += 1;
      },
      auditImpl: async (entry) => audit.push(entry),
    },
    async (client) => {
      const result = await client.callTool({
        name: "deploy_e2e",
        arguments: {
          run_id: RUN_ID,
          confirmation: `deploy:ie-event-bot-e2e:${RUN_ID}`,
        },
      });
      const payload = parseToolResult(result);

      assert.equal(payload.ok, false);
      assert.equal(payload.error, "worker_version_propagation_timeout");
      assert.equal(payload.version_verified, false);
      assert.equal(payload.verification_attempts, 20);
      assert.equal(fetchCalls, 20);
      assert.equal(delayCalls, 19);
      assert.deepEqual(audit.map((entry) => entry.phase), ["start", "finish"]);
      assert.equal(audit.at(-1).ok, false);
    },
  );
});


test("deploy_e2eは接続先fingerprint不整合時にWranglerを起動しない", async () => {
  let deployCalls = 0;
  let auditCalls = 0;

  await withClient(
    {
      env: { ...ENV, E2E_WORKER_URL_SHA256: "0".repeat(64) },
      deployImpl: async () => {
        deployCalls += 1;
        return { ok: true, status: 0, error: null };
      },
      auditImpl: async () => {
        auditCalls += 1;
      },
    },
    async (client) => {
      const result = await client.callTool({
        name: "deploy_e2e",
        arguments: {
          run_id: RUN_ID,
          confirmation: `deploy:ie-event-bot-e2e:${RUN_ID}`,
        },
      });
      const payload = parseToolResult(result);

      assert.equal(payload.ok, false);
      assert.equal(payload.error, "e2e_mcp_configuration_invalid");
      assert.equal(deployCalls, 0);
      assert.equal(auditCalls, 0);
    },
  );
});


test("collect_evidenceは識別子をマスクしたrun manifestを返す", async () => {
  const audit = [
    {
      timestamp: "2026-09-01T00:00:00.000Z",
      run_id: RUN_ID,
      tool: "seed_fixture",
      target: "google",
      phase: "start",
      ok: true,
      status: null,
      error: null,
    },
    {
      timestamp: "2026-09-01T00:00:01.000Z",
      run_id: RUN_ID,
      tool: "seed_fixture",
      target: "google",
      phase: "finish",
      ok: true,
      status: 200,
      error: null,
    },
    {
      timestamp: "2026-09-01T00:00:02.000Z",
      run_id: RUN_ID,
      tool: "cleanup_run",
      target: "google",
      phase: "finish",
      ok: true,
      status: 200,
      error: null,
    },
  ];
  const rawVersionId = "sensitive-worker-version-id";
  const rawWatchId = "sensitive-watch-channel-id";
  const resourceFingerprint = "b".repeat(64);
  const fetchImpl = async () => jsonResponse({
    ok: true,
    mode: "e2e",
    kv_enabled: true,
    worker_version: {
      present: true,
      id_sha256: createHash("sha256").update(rawVersionId).digest("hex"),
      tag: RUN_ID,
      timestamp: "2026-09-01T00:00:00.000Z",
    },
    watch: {
      present: true,
      channel_id_sha256: createHash("sha256").update(rawWatchId).digest("hex"),
      resource_id_sha256: "a".repeat(64),
    },
    services: {
      google: {
        present: true,
        dirty: false,
        run_id: RUN_ID,
        outcome: "passed",
        cleanup_attempts: 1,
        stages: { create: 200, delete: 204 },
        resource_fingerprints: { event_id_sha256: resourceFingerprint },
      },
    },
    scenarios: {
      google_notion: {
        present: true,
        dirty: false,
        run_id: RUN_ID,
        outcome: "passed",
        cleanup_attempts: 1,
        stages: { application_apply: 200, google_delete: 204 },
        resource_fingerprints: { notion_page_id_sha256: "d".repeat(64) },
      },
    },
  });

  await withClient(
    {
      env: ENV,
      fetchImpl,
      readAuditImpl: async () => audit,
      repositoryMetadataImpl: async () => ({
        git_sha: "c".repeat(40),
        dirty: true,
      }),
    },
    async (client) => {
      const result = await client.callTool({
        name: "collect_evidence",
        arguments: { run_id: RUN_ID },
      });
      const payload = parseToolResult(result);
      const manifest = payload.manifest;

      assert.equal(payload.ok, true);
      assert.equal(manifest.version, 1);
      assert.equal(manifest.run_id, RUN_ID);
      assert.deepEqual(manifest.repository, {
        git_sha: "c".repeat(40),
        dirty: true,
      });
      assert.deepEqual(manifest.worker, {
        name: "ie-event-bot-e2e",
        environment: "e2e",
        url_sha256: createHash("sha256").update(ENV.E2E_WORKER_URL).digest("hex"),
        version: {
          present: true,
          id_sha256: createHash("sha256").update(rawVersionId).digest("hex"),
          tag: RUN_ID,
          timestamp: "2026-09-01T00:00:00.000Z",
        },
      });
      assert.equal(manifest.started_at, "2026-09-01T00:00:00.000Z");
      assert.equal(manifest.completed_at, "2026-09-01T00:00:02.000Z");
      assert.deepEqual(
        manifest.operations.map(({ tool, target, route, ok, status }) => ({
          tool,
          target,
          route,
          ok,
          status,
        })),
        [
          {
            tool: "seed_fixture",
            target: "google",
            route: "/admin/e2e/google-crud",
            ok: true,
            status: 200,
          },
          {
            tool: "cleanup_run",
            target: "google",
            route: "/admin/e2e/google-crud/cleanup",
            ok: true,
            status: 200,
          },
        ],
      );
      assert.deepEqual(manifest.cleanup.google, {
        ok: true,
        status: 200,
        error: null,
      });
      assert.equal(
        manifest.services.google.resource_fingerprints.event_id_sha256,
        resourceFingerprint,
      );
      assert.equal(manifest.watch.channel_id_sha256.length, 64);
      assert.equal(manifest.scenarios.google_notion.run_id, RUN_ID);
      assert.equal(
        manifest.scenarios.google_notion.resource_fingerprints.notion_page_id_sha256,
        "d".repeat(64),
      );

      const serialized = JSON.stringify(payload);
      assert.equal(serialized.includes(ENV.E2E_WORKER_URL), false);
      assert.equal(serialized.includes(ENV.INTERNAL_API_TOKEN), false);
      assert.equal(serialized.includes(rawVersionId), false);
      assert.equal(serialized.includes(rawWatchId), false);
    },
  );
});


test("固定版Playwright MCPがallowlist対象ツールを公開する", async () => {
  const client = new Client({ name: "playwright-mcp-test", version: "1.0.0" });
  const transport = new StdioClientTransport({
    command: "./node_modules/.bin/playwright-mcp",
    args: PLAYWRIGHT_ARGS,
    cwd: process.cwd(),
    stderr: "pipe",
  });
  await client.connect(transport);
  try {
    const listed = await client.listTools();
    const names = new Set(listed.tools.map((tool) => tool.name));
    for (const name of PLAYWRIGHT_ALLOWED_TOOLS) {
      assert.equal(names.has(name), true, name);
    }
  } finally {
    await client.close();
  }
});

function redeployStatus() {
  return { ok: true, mode: "e2e", e2e_manifest_enabled: true,
    orchestrated_writes_enabled: false,
    worker_version: { present: true, tag: RUN_ID, id_sha256: "c".repeat(64) },
    scenarios: { discord_delta: { present: true, dirty: true, run_id: RUN_ID, stage: "delta_updated" } } };
}

for (const invalid of ["stage", "run", "version", "missing_metadata", "writes_unknown", "other_dirty"]) {
  test(`再deployは${invalid}のcheckpointを拒否する`, async () => {
    const status = redeployStatus();
    if (invalid === "stage") {
      status.scenarios.discord_delta.stage = "delta_resuming";
    } else if (invalid === "run") {
      status.scenarios.discord_delta.run_id = "E2E-20260901T000000Z-abcdef01";
    } else if (invalid === "version") {
      status.worker_version.id_sha256 = "d".repeat(64);
    } else if (invalid === "missing_metadata") {
      status.worker_version.present = false;
    } else if (invalid === "writes_unknown") {
      delete status.orchestrated_writes_enabled;
    } else {
      status.services = { notion: { present: true, dirty: true } };
    }
    let deploys = 0;
    await withClient({ env: ENV, auditImpl: async () => {},
      fetchImpl: async () => jsonResponse(status),
      deployImpl: async () => { deploys += 1; return { ok: true }; },
    }, async (client) => {
      const result = parseToolResult(await client.callTool({ name: "deploy_e2e", arguments: {
        run_id: RUN_ID, confirmation: `deploy:ie-event-bot-e2e:${RUN_ID}`,
        previous_version_sha256: "c".repeat(64),
      } }));
      assert.equal(result.error, "redeploy_checkpoint_mismatch");
      assert.equal(deploys, 0);
    });
  });
}

test("再deployは同tagの旧versionを待機し新versionの系譜を監査へ残す", async () => {
  const audit = [];
  let reads = 0;
  const waits = [];
  await withClient({ env: ENV, auditImpl: async (entry) => audit.push(entry),
    readAuditImpl: async () => audit,
    repositoryMetadataImpl: async () => ({ git_sha: "e".repeat(40), dirty: false }),
    deployImpl: async () => ({ ok: true, status: 0 }),
    delayImpl: async (ms) => waits.push(ms),
    fetchImpl: async () => {
      const status = redeployStatus();
      if (++reads >= 3) {
        status.worker_version.id_sha256 = "d".repeat(64);
      }
      return jsonResponse(status);
    },
  }, async (client) => {
    const result = parseToolResult(await client.callTool({ name: "deploy_e2e", arguments: {
      run_id: RUN_ID, confirmation: `deploy:ie-event-bot-e2e:${RUN_ID}`,
      previous_version_sha256: "c".repeat(64),
    } }));
    assert.equal(result.ok, true);
    assert.equal(result.verification_attempts, 2);
    assert.equal(result.version_sha256, "d".repeat(64));
    assert.equal(result.previous_version_sha256, "c".repeat(64));
    assert.equal(waits.length, 1);
    const evidence = parseToolResult(await client.callTool({ name: "collect_evidence",
      arguments: { run_id: RUN_ID } }));
    const deploy = evidence.manifest.operations.find((op) => op.tool === "deploy_e2e");
    assert.equal(deploy.version_sha256, "d".repeat(64));
    assert.equal(deploy.previous_version_sha256, "c".repeat(64));
  });
});

test("同時resumeのロック拒否を再送せず成功要求と別々に監査する", { timeout: 2000 }, async () => {
  const blocked = Promise.withResolvers();
  const audit = [];
  let requests = 0;
  await withClient({ env: ENV, auditImpl: async (entry) => audit.push(entry),
    readAuditImpl: async () => audit,
    repositoryMetadataImpl: async () => ({ git_sha: "e".repeat(40), dirty: false }),
    delayImpl: async () => { assert.fail("ロック拒否は再試行しない"); },
    fetchImpl: async (url, options) => {
      if (options.method === "GET") {
        return jsonResponse({ ok: true });
      }
      assert.equal(options.headers["X-E2E-Version-ID-SHA256"], "c".repeat(64));
      if (++requests === 1) {
        await blocked.promise;
        return jsonResponse({ ok: true, run_id: RUN_ID, dirty: false });
      }
      blocked.resolve();
      return jsonResponse({ ok: false, error: "e2e_lock_unavailable", lock_owner: "private_owner" }, 409);
    },
  }, async (client) => {
    const results = await Promise.all([0, 1].map(() => client.callTool({ name: "trigger_sync",
      arguments: { run_id: RUN_ID, scenario: "discord_delta", sync_phase: "resume", version_sha256: "c".repeat(64) },
    })));
    assert.deepEqual(results.map((result) => parseToolResult(result).status).sort(), [200, 409]);
    assert.equal(requests, 2);
    const evidence = parseToolResult(await client.callTool({ name: "collect_evidence",
      arguments: { run_id: RUN_ID } }));
    assert.deepEqual(evidence.manifest.operations.map((op) => op.ok).sort(), [false, true]);
    assert.equal(evidence.manifest.operations.find((op) => !op.ok).error, "e2e_lock_unavailable");
    assert.equal(JSON.stringify(evidence).includes("private_owner"), false);
    assert.deepEqual(audit.slice(0, 2).map((entry) => entry.phase), ["start", "start"]);
  });
});

test("更新応答は本文を読まず破棄し監査へ固定flagを残す", async () => {
  const audit = [];
  let cancellations = 0;
  let posts = 0;
  await withClient({ env: ENV, auditImpl: async (entry) => audit.push(entry),
    readAuditImpl: async () => audit,
    repositoryMetadataImpl: async () => ({ git_sha: "e".repeat(40), dirty: false }),
    fetchImpl: async (url, options) => {
      if (options.method === "GET") {
        return jsonResponse({ ok: true });
      }
      posts += 1;
      return { status: 200, body: { cancel: async () => { cancellations += 1; } },
        text: async () => { assert.fail("破棄対象の本文を読んではならない"); } };
    },
  }, async (client) => {
    const result = await client.callTool({ name: "trigger_sync", arguments: {
      run_id: RUN_ID, scenario: "discord_delta", sync_phase: "advance", version_sha256: "c".repeat(64),
      response_mode: "discard_after_headers",
    } });
    const payload = parseToolResult(result);
    assert.equal(result.isError, true);
    assert.equal(payload.error, "worker_response_discarded");
    assert.equal(payload.response_discarded, true);
    assert.equal(payload.dirty, null);
    assert.equal(payload.execution_status, null);
    assert.deepEqual(payload.stages, {});
    assert.equal(posts, 1);
    assert.equal(cancellations, 1);
    const evidence = parseToolResult(await client.callTool({ name: "collect_evidence",
      arguments: { run_id: RUN_ID } }));
    assert.equal(evidence.manifest.operations[0].response_discarded, true);
    assert.equal(evidence.manifest.operations[0].ok, false);
  });
  for (const response_discarded of [true, "private_text", false]) {
    await appendAuditEntry({ run_id: RUN_ID, tool: "trigger_sync", target: "discord_delta",
      phase: "finish", ok: false, status: 200, response_discarded });
  }
  const saved = await readAuditEntries(RUN_ID);
  assert.deepEqual(saved.slice(-3).map((entry) => entry.response_discarded), [true, false, false]);
});

for (const failure of ["missing_body", "cancel_failed", "http_error", "network"]) {
  test(`応答破棄の${failure}を注入成功と偽らない`, async () => {
    await withClient({ env: ENV, auditImpl: async () => {}, fetchImpl: async () => {
      if (failure === "network") {
        throw new Error("network");
      }
      if (failure === "http_error") {
        return jsonResponse({ ok: false, error: "delta_resume_resource_mismatch" }, 409);
      }
      return { status: 200,
        body: failure === "missing_body" ? null : { cancel: async () => { throw new Error("cancel"); } },
        text: async () => { assert.fail("本文は読まない"); } };
    } }, async (client) => {
      const result = parseToolResult(await client.callTool({ name: "trigger_sync", arguments: {
        run_id: RUN_ID, scenario: "discord_delta", sync_phase: "advance", version_sha256: "c".repeat(64),
        response_mode: "discard_after_headers",
      } }));
      assert.equal(result.ok, false);
      assert.equal(result.response_discarded, false);
      assert.notEqual(result.error, "worker_response_discarded");
      if (failure === "http_error") {
        assert.equal(result.error, "delta_resume_resource_mismatch");
      }
    });
  });
}

test("応答破棄はversion指定付きのDiscord差分advanceだけに許可する", async () => {
  await withClient({ env: ENV, auditImpl: async () => { assert.fail("禁止入力は監査前に拒否する"); },
    fetchImpl: async () => { assert.fail("禁止入力で接続しない"); },
  }, async (client) => {
    for (const args of [{ scenario: "google_notion", sync_phase: "advance", version_sha256: "c".repeat(64) },
      { scenario: "discord_delta", sync_phase: "prepare", version_sha256: "c".repeat(64) },
      { scenario: "discord_delta", sync_phase: "resume", version_sha256: "c".repeat(64) },
      { scenario: "discord_delta", sync_phase: "advance" }]) {
      const result = parseToolResult(await client.callTool({ name: "trigger_sync", arguments: {
        run_id: RUN_ID, ...args, response_mode: "discard_after_headers",
      } }));
      assert.equal(result.error, "response_mode_forbidden");
    }
  });
});

test("旧versionの拒否後も応答破棄指定を維持する", async () => {
  let requests = 0;
  let cancellations = 0;
  await withClient({ env: ENV, auditImpl: async () => {}, delayImpl: async () => {},
    fetchImpl: async () => ++requests === 1
      ? jsonResponse({ ok: false, error: "worker_version_mismatch" }, 409)
      : { status: 200, body: { cancel: async () => { cancellations += 1; } },
        text: async () => { assert.fail("本文は読まない"); } },
  }, async (client) => {
    const result = parseToolResult(await client.callTool({ name: "trigger_sync", arguments: {
      run_id: RUN_ID, scenario: "discord_delta", sync_phase: "advance", version_sha256: "c".repeat(64),
      response_mode: "discard_after_headers",
    } }));
    assert.equal(result.response_discarded, true);
    assert.equal(requests, 2);
    assert.equal(cancellations, 1);
  });
});

test("通常Google同期の各phaseとcleanupは固定ルートへ送る", async () => {
  const calls = [];
  await withClient({ env: ENV, auditImpl: async () => {}, fetchImpl: async (url, options) => {
    calls.push({ path: new URL(url).pathname, headers: options.headers });
    return jsonResponse({ ok: true, dirty: !String(url).endsWith("cleanup"), run_id: RUN_ID,
      status: "pending", stage: "google_pending_verified" });
  } }, async (client) => {
    for (const phase of ["prepare", "advance", "resume"]) {
      const result = parseToolResult(await client.callTool({ name: "trigger_sync", arguments: {
        run_id: RUN_ID, scenario: "google_sync", sync_phase: phase,
      } }));
      assert.equal(result.ok, true, JSON.stringify(result));
    }
    const result = parseToolResult(await client.callTool({ name: "cleanup_run", arguments: {
      run_id: RUN_ID, service: "google_sync", confirmation: `cleanup:google_sync:${RUN_ID}`,
    } }));
    assert.equal(result.ok, true);
  });
  assert.deepEqual(calls.map(c => c.path), ["", "/advance", "/verify", "/cleanup"].map(suffix => `/admin/e2e/google-sync${suffix}`));
  assert.ok(calls.slice(0, 3).every(c => c.headers["X-E2E-Version-Tag"] === RUN_ID));
});

for (const invalid of ["stage", "dirty", "run"]) {
  test(`通常Google同期MCPは${invalid}不一致を拒否する`, async () => {
    const payload = { ok: true, dirty: true, run_id: RUN_ID, status: "pending", stage: "google_pending_verified" };
    if (invalid === "stage") { payload.stage = "ready"; }
    if (invalid === "dirty") { payload.dirty = false; }
    if (invalid === "run") { payload.run_id = RUN_ID.replace("1234abcd", "11111111"); }
    await withClient({ env: ENV, auditImpl: async () => {}, fetchImpl: async () => jsonResponse(payload) }, async (client) => {
      const result = parseToolResult(await client.callTool({ name: "trigger_sync", arguments: { run_id: RUN_ID, scenario: "google_sync", sync_phase: "resume" } }));
      assert.equal(result.ok, false);
    });
  });
}

for (const status of ["pending", "deleted", "retry_pending", "retried"]) {
  test(`通常Google同期の${status}を監査へ保持する`, async () => {
    const audit = [];
    await withClient({ env: ENV, auditImpl: async (entry) => audit.push(entry),
      fetchImpl: async () => jsonResponse({ ok: true, dirty: true, run_id: RUN_ID,
        status, stage: `google_${status}_verified` }),
    }, async (client) => {
      const result = parseToolResult(await client.callTool({ name: "trigger_sync", arguments: {
        run_id: RUN_ID, scenario: "google_sync", sync_phase: "resume",
      } }));
      assert.equal(result.ok, true);
      assert.equal(result.execution_status, status);
    });
    assert.equal(audit.find(entry => entry.phase === "finish").execution_status, status);
  });
}
