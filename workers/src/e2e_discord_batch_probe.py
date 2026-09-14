"""所有2件を上限1件で適用し、別HTTPでKV残件を処理する。"""

from hashlib import sha256
from urllib.parse import quote
from uuid import uuid4

from discord_notion_sync import (
    run_discord_notion_poll_sync,
    _fingerprint,
    _sync_discord_event_upsert,
)
from e2e_discord_batch_state import (
    SERVICE,
    KIND,
    COUNT,
    GOOGLE_SERVICE,
    GOOGLE_KIND,
    NOTIFICATION_SERVICE,
    NOTIFICATION_KIND,
    manifest_service,
    google_event_id,
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


from e2e_discord_batch_google import GoogleBatch, GoogleBatchError
from e2e_discord_batch_notification import NotificationBatch, NotificationError


class BatchError(Exception):
    pass


def _source_digest(event: dict) -> str:
    fingerprint = _fingerprint(event)
    if not isinstance(fingerprint, str):
        raise BatchError("discord_batch_source_invalid")
    return sha256(fingerprint.encode()).hexdigest()


async def _save(store, manifest):
    await store.put_e2e_manifest(manifest_service(manifest), manifest)


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
            google_event_id=slot.get("google_event_id"),
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


async def _read_state(env, store, manifest, pending: bool, google=None, notification=None):
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
    if google:
        for index, slot in enumerate(manifest["fixtures"]):
            await google.read(slot, source=events[index], absent=pending and index == 1)
        manifest["stages"][
            "batch_google_pending" if pending else "batch_google_final"
        ] = 200
    state = BatchDiscordKV(store, manifest).state()
    queue = (
        [{"op": "upsert", "id": manifest["fixtures"][1]["discord_event_id"]}]
        if pending
        else []
    )
    if notification:
        await notification.read_state(pending)
        queue = notification.queue(pending)
    if (
        await state.get_discord_snapshot()
        != {str(e["id"]): _fingerprint(e) for e in events}
        or await state.get_json("sync:discord_notion_queue") != queue
    ):
        raise BatchError("discord_batch_not_ready")
    return events


async def _apply_batch(env, store, manifest, index, google=None, notification=None):
    slot = manifest["fixtures"][max(index, 0)]
    event_id = slot["discord_event_id"]

    async def select_owned(events):
        await check_batch_owner(store, manifest)
        owned = {item["discord_event_id"]: item for item in manifest["fixtures"]}
        selected = {}
        guild = _env_text(env, "DISCORD_GUILD_ID")
        for event in events:
            if not isinstance(event, dict):
                raise BatchError("discord_batch_poll_invalid")
            listed_id = str(event.get("id") or "")
            if listed_id not in owned:
                continue
            item = owned[listed_id]
            if (
                listed_id in selected
                or not _discord_event_is_owned(
                    event,
                    event_id=listed_id,
                    guild_id=guild,
                    run_id=item["run_id"],
                )
                or _source_digest(event) != item["source_sha256"]
            ):
                raise BatchError("discord_batch_poll_mismatch")
            selected[listed_id] = event
        if set(selected) != set(owned):
            raise BatchError("discord_batch_poll_missing")
        # Discordの一覧順にかかわらず、初回と残件の順序を固定する。
        manifest["stages"][
            "batch_first_poll" if index == 0 else "batch_remaining_poll"
        ] = 200
        return [selected[item["discord_event_id"]] for item in manifest["fixtures"]]

    async def create_owned(scoped_env, event, token):
        # KVの再読込が古くても、今回許可した1件以外の外部書込みは拒否する。
        if index < 0 or str(event.get("id") or "") != event_id:
            return False
        await check_batch_owner(store, manifest)

        async def before_notion():
            await check_batch_owner(store, manifest)
            slot["create_attempted"]["notion_page"] = True
            await _save(store, manifest)

        async def before_google():
            if google is None:
                raise BatchError("discord_batch_google_required")
            await check_batch_owner(store, manifest)
            await google.read(slot, absent=True)
            slot["create_attempted"]["google_event"] = True
            await _save(store, manifest)

        if not google:
            await before_notion()
        ok = await _sync_discord_event_upsert(
            scoped_env,
            event,
            token,
            require_new_internal_page=True,
            new_google_event_id=slot.get("google_event_id"),
            before_google_create=before_google if google else None,
            before_internal_create=before_notion if google else None,
        )
        if google:
            await google.read(slot, source=event)
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

    async def notify_owned(scoped_env, event, *, delivery):
        if notification is None or str(event.get("id") or "") != event_id:
            raise BatchError("discord_notification_phase_mismatch")
        return await notification.notify(scoped_env, event, delivery=delivery)

    state = BatchDiscordKV(store, manifest).state()
    scoped_env = google.env if google else _DeltaEnv(env)
    if notification:
        scoped_env = notification.env
    result = await run_discord_notion_poll_sync(
        scoped_env,
        state,
        event_selector=select_owned,
        upsert_runner=create_owned,
        delete_runner=reject_delete,
        notify_runner=notify_owned if notification else None,
    )
    expected: dict[str, object] = {
        "created": COUNT if index == 0 else 0,
        "updated": 0,
        "deleted": 0,
        "processed_changes": 1,
        "pending_changes": 1 if index == 0 else 0,
        "error_count": 0,
    }
    deferred = notification is not None and index == 0
    if notification:
        expected["pending_changes"] = 2 if deferred else 1 if index < 0 else 0
        expected["error_count"] = 1 if deferred else 0
        expected["errors"] = [f"create_notify_failed:{event_id}"] if deferred else []
        if deferred and not slot.get("reaction_deferred"):
            raise BatchError("discord_notification_failure_not_injected")
    if result.get("ok") is not (not deferred) or any(
        result.get(k) != v for k, v in expected.items()
    ):
        raise BatchError("discord_batch_apply_failed")
    if index < 0:
        manifest["stages"]["notification_retry_applied"] = 200
        return
    manifest["stages"][
        "batch_first_limit" if index == 0 else "batch_remaining_applied"
    ] = 200


async def _cleanup(env, store, manifest, google=None, notification=None):
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
        message_ok = True
        if notification:
            try:
                message_ok = await notification.cleanup(slot, index)
            except NotificationError:
                message_ok = False
        google_ok = True
        if google:
            stages = {}
            google_ok = await google.cleanup(slot, stages)
            slot["google_cleanup_done"] = google_ok
            for key, status in stages.items():
                manifest["stages"][f"batch_{index}_{key}"] = status
            await _save(store, manifest)
        result = await _cleanup_resources(env, slot)
        for key in ("discord_event_id", "notion_page_id"):
            if result.get(key):
                slot[key] = result[key]
        slot["cleanup_done"] = result["ok"] is True and google_ok and message_ok
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
            "kind": manifest["kind"],
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


async def run_discord_batch_probe(
    env, store, run_id: str, phase: str, *, service=SERVICE
) -> dict:
    if service not in (SERVICE, GOOGLE_SERVICE, NOTIFICATION_SERVICE):
        return {"ok": False, "error": "discord_batch_service_invalid"}
    scope = _env_text(env, "E2E_STATE_SCOPE")
    if not scope or not store.enabled() or not store.e2e_manifest_enabled():
        return {"ok": False, "error": "discord_batch_bindings_required"}
    targets = _target_fingerprints(
        _env_text(env, "DISCORD_GUILD_ID"), _env_text(env, "NOTION_EVENT_INTERNAL_ID")
    )
    if service == GOOGLE_SERVICE:
        targets["calendar_id_sha256"] = sha256(
            _env_text(env, "GOOGLE_CALENDAR_ID").encode()
        ).hexdigest()
    if service == NOTIFICATION_SERVICE:
        targets.update({
            "channel_id_sha256": sha256(_env_text(env, "EVENT_CREATE_CHANNEL_ID").encode()).hexdigest(),
            "role_id_sha256": sha256(_env_text(env, "EVENT_CREATE_ROLE_ID").encode()).hexdigest(),
        })
    scope_sha = sha256(scope.encode()).hexdigest()
    manifest = await store.get_e2e_manifest(service)
    google = None
    kind = KIND
    if service == GOOGLE_SERVICE:
        kind = GOOGLE_KIND
    elif service == NOTIFICATION_SERVICE:
        kind = NOTIFICATION_KIND
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
        if service == GOOGLE_SERVICE:
            try:
                google = await GoogleBatch.connect(env)
                await google.verify_target(stages)
            except GoogleBatchError as exc:
                return {"ok": False, "error": str(exc)}
        if service == NOTIFICATION_SERVICE:
            try:
                await NotificationBatch.verify_target(env, stages)
            except NotificationError as exc:
                return {"ok": False, "error": str(exc)}
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
            "kind": kind,
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
        if google:
            for slot in manifest["fixtures"]:
                slot["google_event_id"] = google_event_id(slot["run_id"])
                slot["create_attempted"]["google_event"] = False
        if service == NOTIFICATION_SERVICE:
            for slot in manifest["fixtures"]:
                slot["create_attempted"]["message"] = False
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
    notification = NotificationBatch(env, store, manifest) if service == NOTIFICATION_SERVICE else None
    try:
        if phase == "verify":
            manifest["verification_passed"] = False
            await _save(store, manifest)
        if phase != "cleanup":
            error = _configuration_error(env)
            if error:
                raise BatchError(error)
        if service == GOOGLE_SERVICE and google is None:
            google = await GoogleBatch.connect(env)
        if phase == "cleanup":
            return await _cleanup(env, store, manifest, google, notification)
        if phase == "prepare":
            await _create_sources(env, store, manifest)
            manifest["stage"] = "batch_first_applying"
            await _save(store, manifest)
            await _apply_batch(env, store, manifest, 0, google, notification)
            manifest["stage"] = "batch_pending"
            await _save(store, manifest)
            return {"ok": True, "dirty": True, "status": "prepared"}
        if phase == "advance":
            if manifest["stage"] not in ("batch_pending_verified", "batch_retry_verified"):
                raise BatchError("discord_batch_advance_forbidden")
            if not notification and manifest["stage"] != "batch_pending_verified":
                raise BatchError("discord_batch_advance_forbidden")
            await _read_state(env, store, manifest, True, google, notification)
            manifest["stage"] = "batch_applying"
            await _save(store, manifest)
            retry = notification is not None and not manifest["fixtures"][0].get("reaction_done")
            await _apply_batch(env, store, manifest, -1 if retry else 1, google, notification)
            manifest["stage"] = "batch_retry_drained" if retry else "batch_drained"
            await _save(store, manifest)
            return {"ok": True, "dirty": True, "status": "retry_drained" if retry else "drained"}
        pending = manifest["stage"] in (
            "batch_pending",
            "batch_pending_verifying",
            "batch_pending_verified",
            "batch_retry_drained",
            "batch_retry_verifying",
            "batch_retry_verified",
        )
        if phase != "verify" or (
            not pending
            and manifest["stage"]
            not in ("batch_drained", "batch_verifying", "batch_verified")
        ):
            raise BatchError("discord_batch_verify_forbidden")
        retried = notification is not None and pending and bool(manifest["fixtures"][0].get("reaction_done"))
        verifying_stage = "batch_pending_verifying" if pending else "batch_verifying"
        verified_stage = "batch_pending_verified" if pending else "batch_verified"
        if retried:
            verifying_stage, verified_stage = "batch_retry_verifying", "batch_retry_verified"
        manifest["stage"] = verifying_stage
        manifest["verification_passed"] = False
        await _save(store, manifest)
        await _read_state(env, store, manifest, pending, google, notification)
        manifest["stage"] = verified_stage
        manifest["verification_passed"] = not pending
        manifest["stages"][
            "batch_pending_readback" if pending else "batch_final_readback"
        ] = 200
        await _save(store, manifest)
        return {"ok": True, "dirty": True, "stage": manifest["stage"]}
    except (BatchError, GoogleBatchError, NotificationError) as error:
        code = str(error)
        if code == "discord_batch_not_ready":
            code = f"{service}_not_ready"
        return {"ok": False, "dirty": True, "error": code}
