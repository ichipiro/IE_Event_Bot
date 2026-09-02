import { randomBytes } from "node:crypto";
import { lstat, mkdir, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";

import {
  RUN_ID_PATTERN,
  createE2eMcpServer,
  readAuditEntries,
} from "./e2e_mcp_server.mjs";


export const SERVICES = Object.freeze(["google", "discord", "notion"]);
export const CLEANUP_TARGETS = Object.freeze([
  "google",
  "discord",
  "notion",
  "discord_notion",
  "google_discord",
  "google_notion",
]);
export const COMMANDS = Object.freeze([
  "run-id",
  "preflight",
  "deploy-and-crud-smoke",
  "deploy-and-discord-notion-smoke",
  "deploy-and-google-discord-smoke",
  "deploy-and-google-notion-smoke",
  "cleanup",
  "evidence",
]);

const MODULE_PATH = fileURLToPath(import.meta.url);
const REPO_ROOT = resolve(dirname(MODULE_PATH), "..");
const EVIDENCE_ROOT = resolve(REPO_ROOT, "test-results");
const EVIDENCE_DIR = resolve(EVIDENCE_ROOT, "e2e-mcp");
const PREFLIGHT_ATTEMPTS = 5;
const PREFLIGHT_DELAY_MS = 2_000;
const CLEANUP_ATTEMPTS = 4;
const CLEANUP_DELAY_MS = 1_000;
const NON_RETRYABLE_CLEANUP_ERRORS = new Set([
  "cleanup_confirmation_mismatch",
  "cleanup_run_id_mismatch",
  "cleanup_target_mismatch",
  "dirty_manifest_target_mismatch",
  "e2e_mcp_configuration_invalid",
  "invalid_dirty_manifest",
  "legacy_e2e_manifest_review_required",
]);


export class E2eWorkflowError extends Error {
  constructor(code) {
    const sanitizedCode = safeErrorCode(code);
    super(sanitizedCode);
    this.name = "E2eWorkflowError";
    this.code = sanitizedCode;
  }
}


function safeErrorCode(value, fallback = "e2e_workflow_failed") {
  const text = String(value ?? "").trim();
  return /^[a-z0-9_]{1,96}$/.test(text) ? text : fallback;
}


function sleep(delayMs) {
  return new Promise((resolveDelay) => setTimeout(resolveDelay, delayMs));
}


export function createRunId(now = new Date(), randomBytesImpl = randomBytes) {
  if (!(now instanceof Date) || !Number.isFinite(now.getTime())) {
    throw new E2eWorkflowError("run_id_time_invalid");
  }
  const timestamp = now
    .toISOString()
    .replaceAll("-", "")
    .replaceAll(":", "")
    .replace(/\.\d{3}Z$/, "Z");
  const suffix = randomBytesImpl(4).toString("hex");
  const runId = `E2E-${timestamp}-${suffix}`;
  if (!RUN_ID_PATTERN.test(runId)) {
    throw new E2eWorkflowError("run_id_generation_failed");
  }
  return runId;
}


export function parseArguments(argv) {
  const [command, ...rest] = argv;
  if (!COMMANDS.includes(command)) {
    throw new E2eWorkflowError("command_invalid");
  }
  if (command === "run-id") {
    if (rest.length !== 0) {
      throw new E2eWorkflowError("arguments_invalid");
    }
    return { command, runId: null };
  }
  if (rest.length !== 2 || rest[0] !== "--run-id") {
    throw new E2eWorkflowError("arguments_invalid");
  }
  const runId = String(rest[1] ?? "");
  if (!RUN_ID_PATTERN.test(runId)) {
    throw new E2eWorkflowError("run_id_invalid");
  }
  return { command, runId };
}


export function parseToolPayload(result) {
  if (
    !result ||
    !Array.isArray(result.content) ||
    result.content.length !== 1 ||
    result.content[0]?.type !== "text"
  ) {
    throw new E2eWorkflowError("mcp_result_invalid");
  }
  let payload;
  try {
    payload = JSON.parse(result.content[0].text);
  } catch {
    throw new E2eWorkflowError("mcp_result_invalid");
  }
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new E2eWorkflowError("mcp_result_invalid");
  }
  return payload;
}


async function toolOutcome(callTool, name, args) {
  try {
    const result = await callTool(name, args);
    const payload = parseToolPayload(result);
    const ok = result.isError !== true && payload.ok === true;
    return {
      ok,
      error: ok
        ? null
        : safeErrorCode(payload.error, `${name}_failed`),
      payload,
    };
  } catch (error) {
    return {
      ok: false,
      error: safeErrorCode(error?.code, "mcp_call_failed"),
      payload: {},
    };
  }
}


async function requireTool(callTool, name, args) {
  const outcome = await toolOutcome(callTool, name, args);
  if (!outcome.ok) {
    throw new E2eWorkflowError(outcome.error);
  }
  return outcome.payload;
}


export async function runPreflight(callTool, runId, options = {}) {
  const attempts = options.attempts ?? PREFLIGHT_ATTEMPTS;
  const delayMs = options.delayMs ?? PREFLIGHT_DELAY_MS;
  const sleepImpl = options.sleepImpl ?? sleep;
  let lastError = "preflight_failed";

  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    const outcome = await toolOutcome(callTool, "preflight", { run_id: runId });
    if (outcome.ok) {
      return outcome.payload;
    }
    lastError = outcome.error;
    if (attempt < attempts) {
      await sleepImpl(delayMs);
    }
  }
  throw new E2eWorkflowError(lastError);
}


export async function cleanupServices(callTool, runId, services, options = {}) {
  const attempts = options.attempts ?? CLEANUP_ATTEMPTS;
  const delayMs = options.delayMs ?? CLEANUP_DELAY_MS;
  const sleepImpl = options.sleepImpl ?? sleep;
  const selected = CLEANUP_TARGETS.filter((service) => new Set(services).has(service));
  const results = {};

  for (const service of selected) {
    let lastError = "cleanup_run_failed";
    let completed = false;
    let usedAttempts = 0;
    for (let attempt = 1; attempt <= attempts; attempt += 1) {
      usedAttempts = attempt;
      const outcome = await toolOutcome(callTool, "cleanup_run", {
        run_id: runId,
        service,
        confirmation: `cleanup:${service}:${runId}`,
      });
      if (outcome.ok) {
        completed = true;
        lastError = null;
        break;
      }
      lastError = outcome.error;
      if (
        NON_RETRYABLE_CLEANUP_ERRORS.has(lastError) ||
        attempt === attempts
      ) {
        break;
      }
      await sleepImpl(delayMs);
    }
    results[service] = {
      ok: completed,
      attempts: usedAttempts,
      error: lastError,
    };
  }

  return {
    ok: Object.values(results).every((result) => result.ok),
    services: results,
  };
}


export async function runDeployAndCrudSmoke(callTool, runId, options = {}) {
  await requireTool(callTool, "deploy_e2e", {
    run_id: runId,
    confirmation: `deploy:ie-event-bot-e2e:${runId}`,
  });
  await runPreflight(callTool, runId, options.preflight);

  const touched = [];
  let primaryError = null;
  try {
    for (const service of SERVICES) {
      touched.push(service);
      await requireTool(callTool, "seed_fixture", {
        run_id: runId,
        service,
      });
      await requireTool(callTool, "assert_external_state", {
        run_id: runId,
        service,
      });
    }
  } catch (error) {
    primaryError = error;
  }

  const cleanup = await cleanupServices(callTool, runId, touched, {
    attempts: 1,
    sleepImpl: options.cleanup?.sleepImpl,
  });
  if (primaryError) {
    throw primaryError;
  }
  if (!cleanup.ok) {
    throw new E2eWorkflowError("cleanup_run_failed");
  }
  return { ok: true, services: [...touched] };
}


export async function runDeployAndGoogleNotionSmoke(callTool, runId, options = {}) {
  await requireTool(callTool, "deploy_e2e", {
    run_id: runId,
    confirmation: `deploy:ie-event-bot-e2e:${runId}`,
  });
  await runPreflight(callTool, runId, options.preflight);

  let primaryError = null;
  try {
    await requireTool(callTool, "trigger_sync", {
      run_id: runId,
      scenario: "google_notion",
    });
    await requireTool(callTool, "assert_external_state", {
      run_id: runId,
      service: "google_notion",
    });
  } catch (error) {
    primaryError = error;
  }

  const cleanup = await cleanupServices(callTool, runId, ["google_notion"], {
    attempts: 1,
    sleepImpl: options.cleanup?.sleepImpl,
  });
  if (primaryError) {
    throw primaryError;
  }
  if (!cleanup.ok) {
    throw new E2eWorkflowError("cleanup_run_failed");
  }
  return { ok: true, scenarios: ["google_notion"] };
}


export async function runDeployAndGoogleDiscordSmoke(callTool, runId, options = {}) {
  await requireTool(callTool, "deploy_e2e", {
    run_id: runId,
    confirmation: `deploy:ie-event-bot-e2e:${runId}`,
  });
  await runPreflight(callTool, runId, options.preflight);

  let primaryError = null;
  try {
    await requireTool(callTool, "trigger_sync", {
      run_id: runId,
      scenario: "google_discord",
    });
    await requireTool(callTool, "assert_external_state", {
      run_id: runId,
      service: "google_discord",
    });
  } catch (error) {
    primaryError = error;
  }

  const cleanup = await cleanupServices(callTool, runId, ["google_discord"], {
    attempts: 1,
    sleepImpl: options.cleanup?.sleepImpl,
  });
  if (primaryError) {
    throw primaryError;
  }
  if (!cleanup.ok) {
    throw new E2eWorkflowError("cleanup_run_failed");
  }
  return { ok: true, scenarios: ["google_discord"] };
}


export async function runDeployAndDiscordNotionSmoke(callTool, runId, options = {}) {
  await requireTool(callTool, "deploy_e2e", {
    run_id: runId,
    confirmation: `deploy:ie-event-bot-e2e:${runId}`,
  });
  await runPreflight(callTool, runId, options.preflight);

  let primaryError = null;
  try {
    await requireTool(callTool, "trigger_sync", {
      run_id: runId,
      scenario: "discord_notion",
    });
    await requireTool(callTool, "assert_external_state", {
      run_id: runId,
      service: "discord_notion",
    });
  } catch (error) {
    primaryError = error;
  }

  const cleanup = await cleanupServices(callTool, runId, ["discord_notion"], {
    attempts: 1,
    sleepImpl: options.cleanup?.sleepImpl,
  });
  if (primaryError) {
    throw primaryError;
  }
  if (!cleanup.ok) {
    throw new E2eWorkflowError("cleanup_run_failed");
  }
  return { ok: true, scenarios: ["discord_notion"] };
}


export function touchedServicesFromAudit(entries, runId) {
  const touched = new Set();
  for (const entry of Array.isArray(entries) ? entries : []) {
    if (
      entry?.run_id === runId &&
      entry.phase === "start" &&
      (
        (entry.tool === "seed_fixture" && SERVICES.includes(entry.target)) ||
        (entry.tool === "trigger_sync" &&
          ["discord_notion", "google_discord", "google_notion"].includes(
            entry.target,
          ))
      )
    ) {
      touched.add(entry.target);
    }
  }
  return CLEANUP_TARGETS.filter((service) => touched.has(service));
}


async function assertSafePath(path, kind) {
  try {
    const stat = await lstat(path);
    if (stat.isSymbolicLink()) {
      throw new E2eWorkflowError("evidence_symlink_forbidden");
    }
    if (kind === "directory" && !stat.isDirectory()) {
      throw new E2eWorkflowError("evidence_directory_invalid");
    }
    if (kind === "file" && !stat.isFile()) {
      throw new E2eWorkflowError("evidence_file_invalid");
    }
  } catch (error) {
    if (error?.code === "ENOENT") {
      return false;
    }
    throw error;
  }
  return true;
}


export function evidencePathForRun(runId) {
  if (!RUN_ID_PATTERN.test(String(runId ?? ""))) {
    throw new E2eWorkflowError("run_id_invalid");
  }
  return resolve(EVIDENCE_DIR, `${runId}-manifest.json`);
}


export async function writeRunManifest(runId, manifest) {
  if (
    !manifest ||
    typeof manifest !== "object" ||
    Array.isArray(manifest) ||
    manifest.run_id !== runId
  ) {
    throw new E2eWorkflowError("evidence_manifest_invalid");
  }
  await mkdir(EVIDENCE_ROOT, { recursive: true, mode: 0o700 });
  await assertSafePath(EVIDENCE_ROOT, "directory");
  await mkdir(EVIDENCE_DIR, { recursive: true, mode: 0o700 });
  await assertSafePath(EVIDENCE_DIR, "directory");
  const outputPath = evidencePathForRun(runId);
  await assertSafePath(outputPath, "file");
  await writeFile(outputPath, `${JSON.stringify(manifest, null, 2)}\n`, {
    encoding: "utf8",
    mode: 0o600,
  });
  return outputPath;
}


function unavailableManifest(runId, error) {
  return {
    version: 1,
    run_id: runId,
    repository: { git_sha: null, dirty: null },
    worker: {
      name: "ie-event-bot-e2e",
      environment: "e2e",
      url_sha256: null,
      version: { present: false, id_sha256: null, timestamp: null },
    },
    started_at: null,
    completed_at: null,
    operations: [],
    cleanup: {},
    services: {},
    scenarios: {},
    watch: { present: false },
    evidence: { ok: false, error },
  };
}


export async function collectAndWriteEvidence(callTool, runId, options = {}) {
  const writeImpl = options.writeImpl ?? writeRunManifest;
  const outcome = await toolOutcome(callTool, "collect_evidence", { run_id: runId });
  const rawManifest = outcome.payload.manifest;
  const hasManifest =
    rawManifest &&
    typeof rawManifest === "object" &&
    !Array.isArray(rawManifest) &&
    rawManifest.run_id === runId;
  const manifest = hasManifest
    ? {
        ...rawManifest,
        evidence: { ok: outcome.ok, error: outcome.error },
      }
    : unavailableManifest(runId, outcome.error);
  await writeImpl(runId, manifest);
  if (!outcome.ok) {
    throw new E2eWorkflowError(outcome.error);
  }
  return manifest;
}


async function withE2eClient(callback) {
  const server = createE2eMcpServer();
  const client = new Client({ name: "ie-event-bot-e2e-workflow", version: "1.0.0" });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  await Promise.all([client.connect(clientTransport), server.connect(serverTransport)]);
  try {
    return await callback(async (name, args) => await client.callTool({
      name,
      arguments: args,
    }));
  } finally {
    await client.close();
    await server.close();
  }
}


async function runCommand(command, runId) {
  return await withE2eClient(async (callTool) => {
    if (command === "preflight") {
      await runPreflight(callTool, runId);
      return;
    }
    if (command === "deploy-and-crud-smoke") {
      await runDeployAndCrudSmoke(callTool, runId);
      return;
    }
    if (command === "deploy-and-discord-notion-smoke") {
      await runDeployAndDiscordNotionSmoke(callTool, runId);
      return;
    }
    if (command === "deploy-and-google-notion-smoke") {
      await runDeployAndGoogleNotionSmoke(callTool, runId);
      return;
    }
    if (command === "deploy-and-google-discord-smoke") {
      await runDeployAndGoogleDiscordSmoke(callTool, runId);
      return;
    }
    if (command === "cleanup") {
      let entries;
      try {
        entries = await readAuditEntries(runId);
      } catch {
        throw new E2eWorkflowError("audit_read_failed");
      }
      const services = touchedServicesFromAudit(entries, runId);
      const result = await cleanupServices(callTool, runId, services);
      if (!result.ok) {
        throw new E2eWorkflowError("cleanup_run_failed");
      }
      return;
    }
    if (command === "evidence") {
      await collectAndWriteEvidence(callTool, runId);
      return;
    }
    throw new E2eWorkflowError("command_invalid");
  });
}


async function main(argv = process.argv.slice(2)) {
  const { command, runId } = parseArguments(argv);
  if (command === "run-id") {
    process.stdout.write(`${createRunId()}\n`);
    return;
  }
  await runCommand(command, runId);
  process.stdout.write(`e2e_${command.replaceAll("-", "_")}_ok\n`);
}


const isMain =
  process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url;
if (isMain) {
  main().catch((error) => {
    process.stderr.write(`${safeErrorCode(error?.code)}\n`);
    process.exitCode = 1;
  });
}
