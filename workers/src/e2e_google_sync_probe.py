"""通常Google取得・適用を所有2件へ接続し、段階ごとに読戻し・回収する。"""

import asyncio
import json
from urllib.parse import quote
from uuid import uuid4

from e2e_discord_batch_state import slot_run_id
from e2e_discord_delta_probe import _DeltaEnv
from e2e_discord_google_probe import _verify_calendar
from e2e_discord_probe import _find_event_by_run, _request_stage as discord_request
from e2e_google_discord_probe import (
    _event_payload,
    _discord_event_is_owned,
    _verify_guild,
)
from e2e_google_probe import _event_collection_url, _event_item_url, _google_request
from e2e_google_sync_state import (
    SERVICE,
    KIND,
    KEYS,
    STEPS,
    GoogleKV,
    GoogleStateError,
    digest,
    source_id,
    final_step,
)
from e2e_notion_probe import (
    _EVENT_SCHEMA,
    _canonical_id,
    _find_page_by_marker,
    _page_database_id,
    _property_text,
    _request_stage as notion_request,
    _verify_database,
)
from e2e_sync_lock_probe import RUN_PATTERN, _lock_state
from google_auth import get_google_access_token
from google_calendar_sync import run_google_delta_fetch
from state import StateStore


class GoogleEnv(_DeltaEnv):
    DISCORD_SYNC_ENABLED = "true"
    NOTION_EVENT_ID = ""
    GOOGLE_APPLY_MAX_EVENTS_PER_RUN: str = "1"
    GOOGLE_TOKEN_BROKER_URL = ""

    def __init__(self, env, token):
        super().__init__(env)
        self.GOOGLE_API_BEARER_TOKEN = token


def _targets(env):
    return {
        "calendar_id_sha256": digest(env.GOOGLE_CALENDAR_ID),
        "guild_id_sha256": digest(env.DISCORD_GUILD_ID),
        "notion_database_id_sha256": digest(
            _canonical_id(env.NOTION_EVENT_INTERNAL_ID)
        ),
        "state_scope_sha256": digest(env.E2E_STATE_SCOPE),
    }


async def _save(store, owner):
    await store.put_e2e_manifest(SERVICE, owner)


def _source_owned(event, slot):
    return (
        isinstance(event, dict)
        and event.get("id") == slot["google_event_id"]
        and event.get("summary") == slot["source"]["summary"]
        and (event.get("extendedProperties") or {})
        .get("private", {})
        .get("ie_event_bot_e2e_run")
        == slot["run_id"]
    )


def _page_owned(env, page, slot):
    return (
        isinstance(page, dict)
        and _canonical_id(_page_database_id(page))
        == _canonical_id(env.NOTION_EVENT_INTERNAL_ID)
        and _property_text(page, "GoogleイベントID", "rich_text")
        == slot["google_event_id"]
        and _property_text(page, "イベント名", "title") == slot["source"]["summary"]
    )


async def _discover(env, store, owner, slot):
    """応答喪失時もmarkerを照合し、曖昧な資源を推測で回収しない。"""
    stages = owner["stages"]
    page_id, error = await _find_page_by_marker(
        env,
        env.NOTION_EVENT_INTERNAL_ID,
        "GoogleイベントID",
        slot["google_event_id"],
        stages,
        {},
        "google_sync_page_find",
        "notion_page",
    )
    if error or (page_id and slot.get("notion_page_id") not in (None, page_id)):
        raise GoogleStateError("google_sync_page_ambiguous")
    if page_id:
        status, page = await notion_request(
            env,
            stages,
            {},
            "google_sync_page_owner",
            "GET",
            f"/pages/{quote(page_id, safe='')}",
        )
        if status != 200 or not _page_owned(env, page, slot):
            raise GoogleStateError("google_sync_page_owner_mismatch")
        slot["notion_page_id"] = page_id
    event_id, error = await _find_event_by_run(
        env,
        env.DISCORD_GUILD_ID,
        slot["run_id"],
        stages,
        {},
        "google_sync_discord_find",
    )
    if error or (event_id and slot.get("discord_event_id") not in (None, event_id)):
        raise GoogleStateError("google_sync_discord_ambiguous")
    if event_id:
        status, event = await discord_request(
            env,
            stages,
            {},
            "google_sync_discord_owner",
            "GET",
            _discord_path(env, event_id),
        )
        if status != 200 or not _discord_event_is_owned(
            event,
            event_id=event_id,
            guild_id=env.DISCORD_GUILD_ID,
            run_id=slot["run_id"],
        ):
            raise GoogleStateError("google_sync_discord_owner_mismatch")
        slot["discord_event_id"] = event_id
    await _save(store, owner)


def _discord_path(env, event_id):
    return f"/guilds/{quote(env.DISCORD_GUILD_ID, safe='')}/scheduled-events/{quote(event_id, safe='')}"


async def _apply(env, store, owner, token, invoke):
    kv = GoogleKV(store, owner)
    probe_env = GoogleEnv(env, token)
    if owner["step"] >= 1:
        probe_env.GOOGLE_APPLY_MAX_EVENTS_PER_RUN = "2"
    for slot in owner["fixtures"]:
        slot["apply_attempted"] = True
    await _save(store, owner)

    async def fetcher(_env, state, *, commit_cursor):
        result = await run_google_delta_fetch(probe_env, state, commit_cursor=False)
        if not result.get("ok"):
            return result
        owned = {s["google_event_id"]: s for s in owner["fixtures"]}
        selected = {}
        for event in result.get("items", []):
            slot = owned.get(event.get("id")) if isinstance(event, dict) else None
            if slot:
                # 削除応答ではid・statusだけになる。固定IDと削除着手の両方を必須にする。
                deleted = owner["step"] >= 3 and slot is owner["fixtures"][0]
                if (
                    event.get("id") in selected
                    or (
                        not deleted
                        and (
                            not _source_owned(event, slot)
                            or event.get("status") == "cancelled"
                            or event.get("description") != slot["source"]["description"]
                        )
                    )
                    or (deleted and event.get("status") != "cancelled")
                ):
                    raise GoogleStateError("google_sync_source_mismatch")
                selected[event["id"]] = event
        required = (
            set(owned)
            if owner["step"] < 2
            else {owner["fixtures"][int(owner["step"] >= 4)]["google_event_id"]}
        )
        if not required <= set(selected):
            raise GoogleStateError("google_sync_source_not_visible")
        result["items"] = [
            selected[s["google_event_id"]]
            for s in owner["fixtures"]
            if s["google_event_id"] in selected
        ]
        if owner["step"] == 4:
            result["items"] = [selected[owner["fixtures"][1]["google_event_id"]]]
        elif owner["step"] == 5:
            # 新規取得との重複からの回復ではなく、保存済みqueueだけを消化する。
            result["items"] = []
        # 全Calendarの最大updatedをcursorに採用する通常挙動を保持する。
        return result

    injected = 0

    async def fail_discord(_env, event, page, fallback_page, mapping):
        nonlocal injected
        slot = owner["fixtures"][1]
        if injected or not _source_owned(event, slot) or not page:
            raise GoogleStateError("google_sync_injection_mismatch")
        if owner.get("api_rejection_enabled"):
            event_id = slot.get("discord_event_id")
            if not event_id or mapping.get(event["id"]) != event_id:
                raise GoogleStateError("google_sync_injection_mismatch")
            status, current = await discord_request(
                env, owner["stages"], {}, "google_sync_discord_rejection_owner",
                "GET", _discord_path(env, event_id),
            )
            if status != 200 or not _discord_event_is_owned(
                current, event_id=event_id, guild_id=env.DISCORD_GUILD_ID,
                run_id=slot["run_id"],
            ):
                raise GoogleStateError("google_sync_discord_owner_mismatch")
            # 名前は1文字以上が必須。所有予定への不正な更新1回をAPIで拒否させる。
            status, rejected = await discord_request(
                env, owner["stages"], {}, "google_sync_discord_invalid_update",
                "PATCH", _discord_path(env, event_id), payload={"name": ""},
            )
            if status != 400 or not isinstance(rejected, dict) or rejected.get("code") != 50035:
                raise GoogleStateError("google_sync_discord_rejection_mismatch")
            owner["stages"]["google_sync_discord_rejection_verified"] = 200
        injected += 1
        owner["stages"]["google_sync_discord_failure_injected"] = 200
        await _save(store, owner)
        # API拒否を確認した場合も、通常ラッパーと同じNoneを適用処理へ返す。
        # 旧manifestは従来の固定注入を維持し、同じ所有情報で回収できる。
        return None

    response = await invoke(
        probe_env, kv.state(), fetcher, fail_discord if owner["step"] == 4 else None
    )
    owner["stages"]["google_sync_dispatch"] = int(response.status)
    payload = json.loads(await response.text())
    failing = owner["step"] == 4
    if response.status != (500 if failing else 200) or payload.get("ok") is not (
        not failing
    ):
        raise GoogleStateError("google_sync_apply_failed")
    expected_pending = 1 if owner["step"] in (0, 4) else 0
    if payload.get("google_apply", {}).get("pending_events") != expected_pending:
        raise GoogleStateError("google_sync_pending_mismatch")
    if failing:
        event_id = owner["fixtures"][1]["google_event_id"]
        applied = payload.get("google_apply", {})
        if (
            injected != 1
            or payload.get("google", {}).get("ok") is not True
            or applied.get("ok") is not False
            or applied.get("processed") != 1
            or applied.get("error_count") != 1
            or applied.get("errors") != [f"discord_sync_failed:{event_id}"]
            or any(
                kv.hashes.get(k) != owner["hashes"].get(k) for k in (KEYS[0], KEYS[4])
            )
        ):
            raise GoogleStateError("google_sync_partial_failure_mismatch")
        owner["stages"]["google_sync_partial_failure_dispatch"] = 500
        owner["stages"]["google_sync_cursor_and_last_success_preserved"] = 200
    else:
        owner["expected_cursor"] = payload.get("google", {}).get("next_updated_min")
        if owner["step"] == 5:
            if payload.get("google_apply", {}).get("processed") != 1:
                raise GoogleStateError("google_sync_queue_retry_mismatch")
            owner["stages"]["google_sync_retry_queue_only"] = 200
    if not isinstance(owner["expected_cursor"], str) or not owner["expected_cursor"]:
        raise GoogleStateError("google_sync_cursor_missing")
    owner["hashes"] = kv.hashes
    for slot in owner["fixtures"]:
        event_id = slot["google_event_id"]
        ids = {
            "notion_page_id": kv.references.get(KEYS[1], {})
            .get("internal", {})
            .get(event_id),
            "discord_event_id": kv.references.get(KEYS[2], {}).get(event_id),
        }
        for field, resource_id in ids.items():
            if resource_id:
                if slot.get(field) not in (None, resource_id):
                    raise GoogleStateError("google_sync_reference_mismatch")
                slot[field] = resource_id
        await _save(store, owner)
        await _discover(env, store, owner, slot)
    owner["stage"] = "ready"
    owner["stages"][f"google_sync_{STEPS[owner['step']]}"] = 200
    await _save(store, owner)


async def _verify(env, store, owner, token):
    kv = GoogleKV(store, owner)
    values = {key: await kv.get(key) for key in KEYS}
    if any(value is None for value in values.values()):
        raise GoogleStateError("google_sync_not_ready")
    if values[KEYS[0]] != owner.get("expected_cursor"):
        raise GoogleStateError("google_sync_cursor_mismatch")
    queue = json.loads(values[KEYS[3]] or "[]")
    expected = (
        [owner["fixtures"][1]["google_event_id"]] if owner["step"] in (0, 4) else []
    )
    if [e["id"] for e in queue] != expected:
        raise GoogleStateError("google_sync_queue_mismatch")
    if owner["step"] == 4 and (
        not _source_owned(queue[0], owner["fixtures"][1])
        or queue[0].get("description") != owner["fixtures"][1]["source"]["description"]
    ):
        raise GoogleStateError("google_sync_queue_mismatch")
    result = json.loads(values[KEYS[5]] or "{}").get("payload", {})
    if any(
        result.get(k) is not (owner["step"] != 4) for k in ("ok", "google_apply_ok")
    ):
        raise GoogleStateError("google_sync_result_mismatch")
    notion_map = json.loads(values[KEYS[1]] or "{}")["internal"]
    discord_map = json.loads(values[KEYS[2]] or "{}")
    for index, slot in enumerate(owner["fixtures"]):
        absent = (owner["step"] == 0 and index == 1) or (
            owner["step"] >= 3 and index == 0
        )
        if absent:
            if (
                slot["google_event_id"] in notion_map
                or slot["google_event_id"] in discord_map
            ):
                raise GoogleStateError("google_sync_map_mismatch")
        else:
            if (
                notion_map.get(slot["google_event_id"]) != slot.get("notion_page_id")
                or discord_map.get(slot["google_event_id"])
                != slot.get("discord_event_id")
                or not slot.get("notion_page_id")
                or not slot.get("discord_event_id")
            ):
                raise GoogleStateError("google_sync_map_mismatch")
        if slot.get("notion_page_id"):
            status, page = await notion_request(
                env,
                owner["stages"],
                {},
                "google_sync_page_read",
                "GET",
                f"/pages/{quote(slot['notion_page_id'], safe='')}",
            )
            if (
                status != 200
                or not _page_owned(env, page, slot)
                or bool(page.get("archived") or page.get("in_trash")) != absent
                or (
                    not absent
                    and _property_text(page, "内容", "rich_text")
                    != slot["source"]["description"]
                )
            ):
                raise GoogleStateError("google_sync_page_mismatch")
        if slot.get("discord_event_id"):
            event_id = slot["discord_event_id"]
            status, event = await discord_request(
                env,
                owner["stages"],
                {},
                "google_sync_discord_read",
                "GET",
                _discord_path(env, event_id),
            )
            if (absent and status != 404) or (
                not absent
                and (
                    status != 200
                    or not _discord_event_is_owned(
                        event,
                        event_id=event_id,
                        guild_id=env.DISCORD_GUILD_ID,
                        run_id=slot["run_id"],
                    )
                    or (
                        slot["retry_previous_description"]
                        if owner["step"] == 4 and index == 1
                        else slot["source"]["description"]
                    )
                    not in str(event.get("description") or "")
                    or (
                        owner["step"] == 4
                        and index == 1
                        and "E2E retry update" in str(event.get("description") or "")
                    )
                )
            ):
                raise GoogleStateError("google_sync_discord_mismatch")
        status, event = await _google_request(
            "GET",
            _event_item_url(env.GOOGLE_CALENDAR_ID, slot["google_event_id"]),
            token,
        )
        if owner["step"] >= 3 and index == 0:
            if status not in (404, 410) and not (
                status == 200 and event.get("status") == "cancelled"
            ):
                raise GoogleStateError("google_sync_source_mismatch")
        elif (
            status != 200
            or not _source_owned(event, slot)
            or event.get("description") != slot["source"]["description"]
        ):
            raise GoogleStateError("google_sync_source_mismatch")


async def _cleanup(env, store, owner, token):
    for slot in owner["fixtures"]:
        if slot["apply_attempted"]:
            await _discover(env, store, owner, slot)
        if slot["apply_attempted"] and slot.get("discord_event_id"):
            path = _discord_path(env, slot["discord_event_id"])
            status, event = await discord_request(
                env,
                owner["stages"],
                {},
                "google_sync_discord_cleanup_read",
                "GET",
                path,
            )
            if status != 404:
                if status != 200 or not _discord_event_is_owned(
                    event,
                    event_id=slot["discord_event_id"],
                    guild_id=env.DISCORD_GUILD_ID,
                    run_id=slot["run_id"],
                ):
                    raise GoogleStateError("google_sync_cleanup_owner_mismatch")
                status, _ = await discord_request(
                    env,
                    owner["stages"],
                    {},
                    "google_sync_discord_delete",
                    "DELETE",
                    path,
                )
                if status not in (200, 204, 404):
                    raise GoogleStateError("google_sync_cleanup_failed")
        if slot["apply_attempted"] and slot.get("notion_page_id"):
            path = f"/pages/{quote(slot['notion_page_id'], safe='')}"
            status, page = await notion_request(
                env, owner["stages"], {}, "google_sync_page_cleanup_read", "GET", path
            )
            if status != 200 or not _page_owned(env, page, slot):
                raise GoogleStateError("google_sync_cleanup_owner_mismatch")
            if not (page.get("archived") or page.get("in_trash")):
                status, _ = await notion_request(
                    env,
                    owner["stages"],
                    {},
                    "google_sync_page_archive",
                    "PATCH",
                    path,
                    {"archived": True},
                )
                if status != 200:
                    raise GoogleStateError("google_sync_cleanup_failed")
        if slot["source_attempted"]:
            path = _event_item_url(env.GOOGLE_CALENDAR_ID, slot["google_event_id"])
            status, event = await _google_request("GET", path, token)
            if status not in (404, 410):
                if status != 200 or not (
                    _source_owned(event, slot)
                    or (
                        slot.get("delete_attempted")
                        and event.get("id") == slot["google_event_id"]
                        and event.get("status") == "cancelled"
                    )
                ):
                    raise GoogleStateError("google_sync_cleanup_owner_mismatch")
                slot["delete_attempted"] = True
                await _save(store, owner)
                status, _ = await _google_request(
                    "DELETE", path + "?sendUpdates=none", token
                )
                if status not in (200, 204, 404, 410):
                    raise GoogleStateError("google_sync_cleanup_failed")
                owner["stages"]["google_sync_source_delete"] = status
    prefix = GoogleKV(store, owner).prefix
    for key in KEYS:
        await env.STATE_KV.delete(prefix + key)
    return {
        "version": 1,
        "kind": KIND,
        "dirty": False,
        "last_run_id": owner["run_id"],
        "outcome": "passed" if owner.get("passed") else "failed_clean",
        "resource_fingerprints": owner["target_fingerprints"],
        "scope_sha256": digest(owner["scope_id"]),
        "stages": owner["stages"],
    }


async def _phase(env, store, run_id, phase, invoke):
    owner = await store.get_e2e_manifest(SERVICE)
    if (await _lock_state(store)).get("owner"):
        raise GoogleStateError("google_sync_busy")
    if phase == "cleanup" and (not owner or not owner.get("dirty")):
        if owner and owner.get("last_run_id") != run_id:
            raise GoogleStateError("google_sync_run_mismatch")
        return {"ok": True, "dirty": False}
    if owner and owner.get("dirty"):
        if owner.get("run_id") != run_id or owner.get(
            "target_fingerprints"
        ) != _targets(env):
            raise GoogleStateError("google_sync_owner_mismatch")
    elif phase != "prepare" or (owner and owner.get("last_run_id") == run_id):
        raise GoogleStateError("google_sync_phase_invalid")
    token = await get_google_access_token(env, StateStore(_DeltaEnv(env)))
    if not token:
        raise GoogleStateError("google_sync_token_required")
    if phase == "prepare":
        if owner and owner.get("dirty"):
            raise GoogleStateError("google_sync_phase_invalid")
        stages = {}
        if (
            await _verify_calendar(env.GOOGLE_CALENDAR_ID, token, stages)
            or await _verify_guild(env, env.DISCORD_GUILD_ID, stages, {})
            or await _verify_database(
                env,
                env.NOTION_EVENT_INTERNAL_ID,
                _EVENT_SCHEMA,
                stages,
                {},
                "google_sync_database",
                "notion_event",
            )
        ):
            raise GoogleStateError("google_sync_target_mismatch")
        slots = []
        for index in range(2):
            subrun = slot_run_id(run_id, index)
            event_id = source_id(run_id, index)
            slots.append(
                {
                    "run_id": subrun,
                    "google_event_id": event_id,
                    "source": _event_payload(subrun, event_id),
                    "source_attempted": False,
                    "apply_attempted": False,
                }
            )
        owner = {
            "version": 1,
            "kind": KIND,
            "run_id": run_id,
            "scope_id": uuid4().hex,
            "target_fingerprints": _targets(env),
            "dirty": True,
            "step": 0,
            "retry_enabled": True,
            "api_rejection_enabled": True,
            "stage": "working",
            "hashes": {},
            "fixtures": slots,
            "stages": stages,
        }
        await _save(store, owner)
        for slot in slots:
            status, _ = await _google_request(
                "GET",
                _event_item_url(env.GOOGLE_CALENDAR_ID, slot["google_event_id"]),
                token,
            )
            await _discover(env, store, owner, slot)
            if (
                status != 404
                or slot.get("notion_page_id")
                or slot.get("discord_event_id")
            ):
                raise GoogleStateError("google_sync_source_collision")
            slot["source_attempted"] = True
            await _save(store, owner)
            status, event = await _google_request(
                "POST",
                _event_collection_url(env.GOOGLE_CALENDAR_ID) + "?sendUpdates=none",
                token,
                slot["source"],
            )
            if status != 200 or not _source_owned(event, slot):
                raise GoogleStateError("google_sync_source_create_failed")
        owner["stages"]["google_sync_sources_created"] = 200
        await _save(store, owner)
        await _apply(env, store, owner, token, invoke)
    elif phase == "verify":
        if owner["stage"] not in ("ready", "verified"):
            raise GoogleStateError("google_sync_phase_invalid")
        if owner["stage"] == "verified":
            owner["stage"] = "ready"
            await _save(store, owner)
        await _verify(env, store, owner, token)
        owner["stage"] = "verified"
        await _save(store, owner)
    elif phase == "advance":
        if owner["stage"] != "verified" or owner["step"] >= final_step(owner):
            raise GoogleStateError("google_sync_phase_invalid")
        await _verify(env, store, owner, token)
        owner["step"] += 1
        owner["stage"] = "working"
        await _save(store, owner)
        slot = owner["fixtures"][int(owner["step"] >= 4)]
        path = _event_item_url(env.GOOGLE_CALENDAR_ID, slot["google_event_id"])
        if owner["step"] in (2, 4):
            if owner["step"] == 4:
                slot["retry_previous_description"] = slot["source"]["description"]
            slot["source"]["description"] += (
                "\nE2E updated" if owner["step"] == 2 else "\nE2E retry update"
            )
            await _save(store, owner)
            status, event = await _google_request(
                "PATCH",
                path + "?sendUpdates=none",
                token,
                {"description": slot["source"]["description"]},
            )
            if status != 200 or not _source_owned(event, slot):
                raise GoogleStateError("google_sync_source_update_failed")
        if owner["step"] == 3:
            slot["delete_attempted"] = True
            await _save(store, owner)
            status, _ = await _google_request(
                "DELETE", path + "?sendUpdates=none", token
            )
            if status != 204:
                raise GoogleStateError("google_sync_source_delete_failed")
        await _apply(env, store, owner, token, invoke)
    else:
        owner["passed"] = (
            owner.get("passed", False)
            if owner["stage"] == "cleanup"
            else owner["stage"] == "verified" and owner["step"] == final_step(owner)
        )
        owner["stage"] = "cleanup"
        await _save(store, owner)
        clean = await _cleanup(env, store, owner, token)
        return {"ok": True, "dirty": False, "_clean_manifest": clean}
    return {
        "ok": True,
        "dirty": True,
        "status": STEPS[owner["step"]],
        "stage": f"google_{STEPS[owner['step']]}_verified"
        if phase == "verify"
        else "ready",
    }


async def run_google_sync_probe(env, store, run_id, phase, invoke):
    if not RUN_PATTERN.fullmatch(run_id) or phase not in (
        "prepare",
        "advance",
        "verify",
        "cleanup",
    ):
        return {"ok": False, "error": "google_sync_request_invalid"}
    if not (
        store.enabled()
        and store.e2e_manifest_enabled()
        and all(
            str(getattr(env, k, "") or "").strip()
            for k in (
                "E2E_STATE_SCOPE",
                "GOOGLE_CALENDAR_ID",
                "NOTION_EVENT_INTERNAL_ID",
                "DISCORD_GUILD_ID",
                "NOTION_TOKEN",
                "DISCORD_TOKEN",
            )
        )
        and env.GOOGLE_CALENDAR_ID.lower() != "primary"
        and str(getattr(env, "SYNC_DO_LOCK_ENABLED", "true")).lower() == "true"
        and str(getattr(env, "SYNC_ALL_INCLUDE_DISCORD_NOTION", "false")).lower()
        == "false"
        and not StateStore.is_kv_sync_cooldown_enabled(env)
    ):
        return {"ok": False, "error": "google_sync_configuration_invalid"}
    stub = env.SYNC_COORDINATOR.getByName("e2e:google-sync-control")
    control = f"{run_id}-{uuid4().hex}"
    lock = await store._sync_do_rpc(
        stub, "acquire", {"owner": control, "ttl_seconds": 300}
    )
    if not lock or lock.get("ok") is not True:
        return {"ok": False, "error": "google_sync_busy"}
    try:
        result = await asyncio.wait_for(_phase(env, store, run_id, phase, invoke), 50)
    except TimeoutError:
        result = {"ok": False, "dirty": True, "error": "google_sync_timeout"}
    except Exception as exc:
        result = {
            "ok": False,
            "dirty": True,
            "error": str(exc)
            if isinstance(exc, GoogleStateError)
            else "google_sync_failed",
        }
    try:
        released = await store._sync_do_rpc(stub, "release", {"owner": control})
        status = await store._sync_do_rpc(stub, "status")
        if (
            not released
            or not released.get("ok")
            or not status
            or not status.get("ok")
            or not isinstance(status.get("lock"), dict)
            or status.get("lock", {}).get("owner") == control
        ):
            raise GoogleStateError("google_sync_release_failed")
    except Exception:
        return {"ok": False, "dirty": True, "error": "google_sync_release_failed"}
    clean = result.pop("_clean_manifest", None)
    if clean:
        await store.put_e2e_manifest(SERVICE, clean)
    result["run_id"] = run_id
    return result
