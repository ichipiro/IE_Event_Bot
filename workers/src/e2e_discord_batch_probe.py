"""所有2件を上限1件で適用し、別HTTPでKV残件を処理する。"""

from hashlib import sha256
from urllib.parse import quote
from uuid import uuid4

from discord_notion_sync import (
    _apply_discord_event_diff,
    _fingerprint,
    _sync_discord_event_upsert,
)
from e2e_discord_batch_state import (
    SERVICE,
    KIND,
    COUNT,
    BatchDiscordKV,
    check_batch_owner,
    cleanup_batch_kv,
    fixture_fingerprint,
    slot_run_id,
)
from e2e_discord_delta_probe import _DeltaEnv
from e2e_discord_notion_probe import (
    _cleanup_resources,
    _configuration_error,
    _discord_event_is_owned,
    _env_text,
    _event_payload,
    _notion_page_matches,
    _target_fingerprints,
    _verify_guild,
)
from e2e_discord_probe import _find_event_by_run, _request_stage as discord_request
from e2e_notion_probe import (
    _EVENT_SCHEMA,
    _find_page_by_marker,
    _request_stage as notion_request,
    _verify_database,
)


class BatchError(Exception):
    pass


def _source_digest(event: dict) -> str:
    fingerprint = _fingerprint(event)
    if not isinstance(fingerprint, str):
        raise BatchError("discord_batch_source_invalid")
    return sha256(fingerprint.encode()).hexdigest()


async def _save(store, manifest):
    await store.put_e2e_manifest(SERVICE, manifest)


async def _read_source(env, slot, stages, retries):
    guild = _env_text(env, "DISCORD_GUILD_ID")
    event_id = slot["discord_event_id"]
    status, event = await discord_request(
        env,
        stages,
        retries,
        "batch_source_read",
        "GET",
        f"/guilds/{quote(guild, safe='')}/scheduled-events/{quote(event_id, safe='')}",
    )
    if (
        status != 200
        or not isinstance(event, dict)
        or not _discord_event_is_owned(
            event,
            event_id=event_id,
            guild_id=guild,
            run_id=slot["run_id"],
        )
    ):
        raise BatchError("discord_batch_source_mismatch")
    digest = _source_digest(event)
    if slot.get("source_sha256") and slot["source_sha256"] != digest:
        raise BatchError("discord_batch_source_changed")
    return event


async def _read_page(env, slot, event, stages, retries):
    page_id = slot.get("notion_page_id")
    if not page_id:
        raise BatchError("discord_batch_page_missing")
    status, page = await notion_request(
        env,
        stages,
        retries,
        "batch_page_read",
        "GET",
        f"/pages/{quote(page_id, safe='')}",
    )
    if (
        status != 200
        or not isinstance(page, dict)
        or not _notion_page_matches(
            page,
            page_id=page_id,
            database_id=_env_text(env, "NOTION_EVENT_INTERNAL_ID"),
            discord_event=event,
            run_id=slot["run_id"],
        )
    ):
        raise BatchError("discord_batch_page_mismatch")


async def _create_sources(env, store, manifest):
    guild = _env_text(env, "DISCORD_GUILD_ID")
    for slot in manifest["fixtures"]:
        stages, retries = {}, {}
        found, error = await _find_event_by_run(
            env, guild, slot["run_id"], stages, retries, "batch_precheck"
        )
        if error or found:
            raise BatchError("discord_batch_source_collision")
        slot["create_attempted"]["discord_event"] = True
        await _save(store, manifest)
        status, event = await discord_request(
            env,
            stages,
            retries,
            "batch_create",
            "POST",
            f"/guilds/{quote(guild, safe='')}/scheduled-events",
            _event_payload(slot["run_id"]),
        )
        event_id = str(event.get("id") or "") if isinstance(event, dict) else ""
        if not (
            200 <= status < 300
            and event_id
            and _discord_event_is_owned(
                event,
                event_id=event_id,
                guild_id=guild,
                run_id=slot["run_id"],
            )
        ):
            event_id, error = await _find_event_by_run(
                env, guild, slot["run_id"], stages, retries, "batch_reconcile"
            )
            if error or not event_id:
                raise BatchError("discord_batch_source_unresolved")
        slot["discord_event_id"] = event_id
        await _save(store, manifest)
        event = await _read_source(env, slot, stages, retries)
        slot["source_sha256"] = _source_digest(event)
        await _save(store, manifest)
    manifest["stages"]["batch_sources_created"] = 200


async def _read_state(env, store, manifest, pending: bool):
    stages, retries = {}, {}
    events = [
        await _read_source(env, slot, stages, retries) for slot in manifest["fixtures"]
    ]
    for index, slot in enumerate(manifest["fixtures"]):
        if pending and index == 1:
            found, error = await _find_page_by_marker(
                env,
                _env_text(env, "NOTION_EVENT_INTERNAL_ID"),
                "メッセージID",
                slot["discord_event_id"],
                stages,
                retries,
                "batch_pending_page",
                "notion_page",
            )
            if found or error or slot.get("notion_page_id"):
                raise BatchError("discord_batch_pending_page_exists")
        else:
            await _read_page(env, slot, events[index], stages, retries)
    state = BatchDiscordKV(store, manifest).state()
    queue = (
        [{"op": "upsert", "id": manifest["fixtures"][1]["discord_event_id"]}]
        if pending
        else []
    )
    if (
        await state.get_discord_snapshot()
        != {str(e["id"]): _fingerprint(e) for e in events}
        or await state.get_json("sync:discord_notion_queue") != queue
    ):
        raise BatchError("discord_batch_not_ready")
    return events


async def _apply_batch(env, store, manifest, events, index):
    slot = manifest["fixtures"][index]
    event_id = slot["discord_event_id"]

    async def create_owned(scoped_env, event, token):
        # KVの再読込が古くても、今回許可した1件以外の外部書込みは拒否する。
        if str(event.get("id") or "") != event_id:
            return False
        await check_batch_owner(store, manifest)
        slot["create_attempted"]["notion_page"] = True
        await _save(store, manifest)
        ok = await _sync_discord_event_upsert(
            scoped_env, event, token, require_new_internal_page=True
        )
        stages, retries = {}, {}
        page_id, error = await _find_page_by_marker(
            env,
            _env_text(env, "NOTION_EVENT_INTERNAL_ID"),
            "メッセージID",
            event_id,
            stages,
            retries,
            "batch_find_page",
            "notion_page",
        )
        if error or not page_id:
            return False
        slot["notion_page_id"] = page_id
        await _read_page(env, slot, event, stages, retries)
        await _save(store, manifest)
        return ok

    async def reject_delete(scoped_env, event_id, token):
        return False

    state = BatchDiscordKV(store, manifest).state()
    result = await _apply_discord_event_diff(
        _DeltaEnv(env),
        state,
        events,
        upsert_runner=create_owned,
        delete_runner=reject_delete,
    )
    expected = {
        "created": COUNT if index == 0 else 0,
        "updated": 0,
        "deleted": 0,
        "processed_changes": 1,
        "pending_changes": 1 if index == 0 else 0,
        "error_count": 0,
    }
    if result.get("ok") is not True or any(
        result.get(k) != v for k, v in expected.items()
    ):
        raise BatchError("discord_batch_apply_failed")
    manifest["stages"][
        "batch_first_limit" if index == 0 else "batch_remaining_applied"
    ] = 200


async def _cleanup(env, store, manifest):
    manifest["stage"] = "batch_cleanup"
    await _save(store, manifest)
    failed = False
    for index, slot in enumerate(manifest["fixtures"]):
        if slot.get("cleanup_done"):
            continue
        if not slot["create_attempted"]["discord_event"]:
            slot["cleanup_done"] = True
            await _save(store, manifest)
            continue
        result = await _cleanup_resources(env, slot)
        for key in ("discord_event_id", "notion_page_id"):
            if result.get(key):
                slot[key] = result[key]
        slot["cleanup_done"] = result["ok"] is True
        failed |= not slot["cleanup_done"]
        for key, status in result["stages"].items():
            manifest["stages"][f"batch_{index}_{key}"] = status
        await _save(store, manifest)
    if failed:
        return {"ok": False, "dirty": True, "error": "discord_batch_cleanup_failed"}
    await cleanup_batch_kv(store, manifest)
    await _save(
        store,
        {
            "kind": KIND,
            "version": 1,
            "dirty": False,
            "last_run_id": manifest["run_id"],
            "outcome": "passed"
            if manifest.get("verification_passed")
            else "failed_clean",
            "stages": {**manifest["stages"], "batch_kv_cleanup": 200},
            "resource_fingerprints": {
                **manifest["target_fingerprints"],
                "state_scope_sha256": manifest["state_scope_sha256"],
                "scope_sha256": sha256(manifest["scope_id"].encode()).hexdigest(),
                "fixtures_sha256": fixture_fingerprint(manifest["fixtures"]),
            },
        },
    )
    return {"ok": True, "dirty": False}


async def run_discord_batch_probe(env, store, run_id: str, phase: str) -> dict:
    scope = _env_text(env, "E2E_STATE_SCOPE")
    if not scope or not store.enabled() or not store.e2e_manifest_enabled():
        return {"ok": False, "error": "discord_batch_bindings_required"}
    targets = _target_fingerprints(
        _env_text(env, "DISCORD_GUILD_ID"), _env_text(env, "NOTION_EVENT_INTERNAL_ID")
    )
    scope_sha = sha256(scope.encode()).hexdigest()
    manifest = await store.get_e2e_manifest(SERVICE)
    if phase == "prepare":
        if manifest and (
            manifest.get("dirty") or manifest.get("last_run_id") == run_id
        ):
            return {
                "ok": False,
                "dirty": bool(manifest.get("dirty")),
                "error": "environment_dirty",
            }
        error = _configuration_error(env)
        if error:
            return {"ok": False, "error": error}
        stages, retries = {}, {}
        error = await _verify_guild(
            env, _env_text(env, "DISCORD_GUILD_ID"), stages, retries
        )
        if not error:
            error = await _verify_database(
                env,
                _env_text(env, "NOTION_EVENT_INTERNAL_ID"),
                _EVENT_SCHEMA,
                stages,
                retries,
                "target_notion_database",
                "notion_event",
            )
        if error:
            return {"ok": False, "error": error}
        manifest = {
            "kind": KIND,
            "version": 1,
            "dirty": True,
            "run_id": run_id,
            "scope_id": uuid4().hex,
            "state_scope_sha256": scope_sha,
            "target_fingerprints": targets,
            "stage": "batch_creating",
            "verification_passed": False,
            "stages": stages,
            "fixtures": [
                {
                    "run_id": slot_run_id(run_id, i),
                    "create_attempted": {"discord_event": False, "notion_page": False},
                }
                for i in range(COUNT)
            ],
        }
        await _save(store, manifest)
    elif not manifest or not manifest.get("dirty"):
        if (
            phase == "cleanup"
            and manifest
            and manifest.get("last_run_id") == run_id
            and all(
                manifest.get("resource_fingerprints", {}).get(k) == v
                for k, v in {**targets, "state_scope_sha256": scope_sha}.items()
            )
        ):
            return {"ok": True, "dirty": False}
        return {"ok": False, "error": "discord_batch_not_prepared"}
    if (
        manifest["run_id"] != run_id
        or manifest["target_fingerprints"] != targets
        or manifest["state_scope_sha256"] != scope_sha
    ):
        return {"ok": False, "dirty": True, "error": "discord_batch_owner_mismatch"}
    await check_batch_owner(store, manifest)
    try:
        if phase == "cleanup":
            return await _cleanup(env, store, manifest)
        error = _configuration_error(env)
        if error:
            raise BatchError(error)
        if phase == "prepare":
            await _create_sources(env, store, manifest)
            events = [
                await _read_source(env, slot, {}, {}) for slot in manifest["fixtures"]
            ]
            manifest["stage"] = "batch_first_applying"
            await _save(store, manifest)
            await _apply_batch(env, store, manifest, events, 0)
            manifest["stage"] = "batch_pending"
            await _save(store, manifest)
            return {"ok": True, "dirty": True, "status": "prepared"}
        if phase == "advance":
            if manifest["stage"] != "batch_pending_verified":
                raise BatchError("discord_batch_advance_forbidden")
            events = await _read_state(env, store, manifest, True)
            manifest["stage"] = "batch_applying"
            await _save(store, manifest)
            await _apply_batch(env, store, manifest, events, 1)
            manifest["stage"] = "batch_drained"
            await _save(store, manifest)
            return {"ok": True, "dirty": True, "status": "drained"}
        pending = manifest["stage"] in (
            "batch_pending",
            "batch_pending_verifying",
            "batch_pending_verified",
        )
        if phase != "verify" or (
            not pending
            and manifest["stage"]
            not in ("batch_drained", "batch_verifying", "batch_verified")
        ):
            raise BatchError("discord_batch_verify_forbidden")
        manifest["stage"] = "batch_pending_verifying" if pending else "batch_verifying"
        manifest["verification_passed"] = False
        await _save(store, manifest)
        await _read_state(env, store, manifest, pending)
        manifest["stage"] = "batch_pending_verified" if pending else "batch_verified"
        manifest["verification_passed"] = not pending
        manifest["stages"][
            "batch_pending_readback" if pending else "batch_final_readback"
        ] = 200
        await _save(store, manifest)
        return {"ok": True, "dirty": True, "stage": manifest["stage"]}
    except BatchError as error:
        return {"ok": False, "dirty": True, "error": str(error)}
