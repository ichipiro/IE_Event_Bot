import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";

import {
  RUN_ID_PATTERN,
  TOOL_NAMES,
  appendAuditEntry,
  createE2eMcpServer,
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


test("公開ツールを10件に固定して任意URLや資源IDを受け取らない", async () => {
  await withClient({ env: ENV }, async (client) => {
    const listed = await client.listTools();
    const names = listed.tools.map((tool) => tool.name);
    assert.deepEqual(names, TOOL_NAMES);

    const allowedFields = new Set([
      "run_id",
      "service",
      "scenario",
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
        google_discord: true,
        google_notion: true,
        qa_notification: true,
        reminder: true,
      },
      required_envs: Object.fromEntries([
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
      ].map((key) => [key, true])),
      google_auth: { direct_env: true },
      sync_lock: { enabled: true, ok: true, status: 200, owner: "sensitive-owner" },
      worker_version: {
        present: true,
        id_sha256: "a".repeat(64),
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
        google_discord: { present: false, dirty: false, run_id: null },
        google_notion: { present: false, dirty: false, run_id: null },
        qa_notification: { present: false, dirty: false, run_id: null },
        reminder: { present: false, dirty: false, run_id: null },
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
      "discord_google",
      "discord_google",
    ],
  );
});


test("trigger_jobは通知jobごとの所有資源限定routeだけを使う", async () => {
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
    },
  );

  assert.deepEqual(
    calls.map((call) => call.url),
    [
      `${ENV.E2E_WORKER_URL}/admin/e2e/qa-notification`,
      `${ENV.E2E_WORKER_URL}/admin/e2e/qa-notification/cleanup`,
      `${ENV.E2E_WORKER_URL}/admin/e2e/reminder`,
      `${ENV.E2E_WORKER_URL}/admin/e2e/reminder/cleanup`,
    ],
  );
  assert.equal(calls.every((call) => call.options.method === "POST"), true);
  assert.deepEqual(
    audit.filter((entry) => entry.phase === "start").map((entry) => entry.target),
    ["qa_check", "qa_notification", "reminder", "reminder"],
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

  await withClient(
    {
      env: ENV,
      deployImpl: async (config) => {
        receivedConfig = config;
        return { ok: true, status: 0, error: null };
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

      assert.equal(payload.ok, true);
      assert.equal(receivedConfig.cloudflareAccountId, ENV.CLOUDFLARE_ACCOUNT_ID);
      assert.equal(receivedConfig.cloudflareApiToken, ENV.CLOUDFLARE_API_TOKEN);
      assert.equal(JSON.stringify(payload).includes(ENV.CLOUDFLARE_ACCOUNT_ID), false);
      assert.equal(JSON.stringify(payload).includes(ENV.INTERNAL_API_TOKEN), false);
      assert.equal(JSON.stringify(payload).includes(ENV.CLOUDFLARE_API_TOKEN), false);
      assert.deepEqual(audit.map((entry) => entry.phase), ["start", "finish"]);
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
