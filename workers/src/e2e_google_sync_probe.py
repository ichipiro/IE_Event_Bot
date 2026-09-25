"""通常Google同期を隔離2件または専用環境全件へ接続し、読戻し・回収する。"""

import asyncio
import json
import re
from urllib.parse import quote
from uuid import uuid4

from sync_lock_release import _RPC_TIMEOUT_SECONDS, _recover_release, _release_retry_blocked

from e2e_discord_batch_state import slot_run_id
from e2e_discord_delta_probe import _DeltaEnv
from e2e_discord_google_probe import _verify_calendar
from e2e_discord_probe import _event_has_run, _find_event_by_run, _request_stage as discord_request
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
    MAX_BASELINE_DELETED,
    MAX_QUERY_BASELINE_DELETED,
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
from sync_lock_release import _RPC_TIMEOUT_SECONDS, _recover_release, _release_retry_blocked


_PHASE_TIMEOUT_SECONDS = 50
# 全件3件の作成・適用・読戻しは2件モードよりAPI待ちが長い。
# 通常同期ロック120秒と制御ロック300秒より短くし、解放の余地を残す。
_FULL_PHASE_TIMEOUT_SECONDS = 90


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
    if (owner.get("full_apply") or owner.get("http_sync")) and owner.get("dirty"):
        current = await store.get_e2e_manifest(SERVICE)
        if current and current.get("run_id") == owner.get("run_id"):
            owner["shared_writes"] = current.get("shared_writes", {})
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


def _deleted_fingerprint(event):
    return digest(json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=True))


def _baseline_matches(owner, event):
    baseline = owner.get("baseline_deleted", {})
    if (owner.get("notion_query_retry") or owner.get("notion_create_retry") or owner.get("notion_writeback_retry")):
        # fingerprint自体にIDも含むため、既知IDの内容変更も受け入れない。
        return _deleted_fingerprint(event) in baseline
    return baseline.get(digest(event["id"])) == _deleted_fingerprint(event)


def _page_owned(env, page, slot):
    return (
        isinstance(page, dict)
        and _canonical_id(_page_database_id(page))
        == _canonical_id(env.NOTION_EVENT_INTERNAL_ID)
        and _property_text(page, "GoogleイベントID", "rich_text")
        == slot["google_event_id"]
        and _property_text(page, "イベント名", "title") == slot["source"]["summary"]
    )


def _discord_owned(env, event, owner, slot):
    event_id = slot.get("discord_event_id")
    if _discord_event_is_owned(
        event, event_id=event_id, guild_id=env.DISCORD_GUILD_ID,
        run_id=slot["run_id"],
    ):
        return True
    # 旧API拒否試験の空名更新が受理された場合だけ、記録済みIDから回収する。
    # 再探索だけで得たID、別run、別Guild、別marker、他の名前変更は許可しない。
    recovered = (
        owner.get("stage") == "cleanup"
        and owner.get("step") == 4
        and owner.get("api_rejection_enabled") is True
        and slot is owner["fixtures"][1]
        and bool(event_id)
        and event.get("name") == ""
        and _event_has_run(
            event, event_id=event_id, guild_id=env.DISCORD_GUILD_ID,
            run_id=slot["run_id"],
        )
    )
    if recovered:
        owner["stages"]["google_sync_empty_name_recovered"] = 200
    return recovered


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
        # 通常の再探索では、まだmanifestにIDがないことを許容する。
        if status != 200 or not (
            _discord_event_is_owned(
                event, event_id=event_id, guild_id=env.DISCORD_GUILD_ID,
                run_id=slot["run_id"],
            ) or _discord_owned(env, event, owner, slot)
        ):
            raise GoogleStateError("google_sync_discord_owner_mismatch")
        slot["discord_event_id"] = event_id
    await _save(store, owner)


def _discord_path(env, event_id):
    return f"/guilds/{quote(env.DISCORD_GUILD_ID, safe='')}/scheduled-events/{quote(event_id, safe='')}"


async def _apply(env, store, owner, token, invoke):
    if (owner.get("notion_query_retry") or owner.get("notion_create_retry") or owner.get("notion_writeback_retry")) and owner["step"] > 0:
        from e2e_google_notion_query import apply_phase
        return await apply_phase(env, store, owner, token, invoke)
    kv = GoogleKV(store, owner)
    probe_env = GoogleEnv(env, token)
    if owner["step"] >= 1:
        probe_env.GOOGLE_APPLY_MAX_EVENTS_PER_RUN = str(len(owner["fixtures"]))
    for slot in owner["fixtures"]:
        slot["apply_attempted"] = True
    await _save(store, owner)

    async def fetcher(_env, state, *, commit_cursor):
        result = await run_google_delta_fetch(probe_env, state, commit_cursor=False)
        if not result.get("ok"):
            return result
        owned = {s["google_event_id"]: s for s in owner["fixtures"]}
        selected = {}
        current_items = []
        for event in result.get("items", []):
            slot = owned.get(event.get("id")) if isinstance(event, dict) else None
            if owner.get("full_apply") and not slot:
                # 開始前の履歴は内容まで一致する場合だけ除外し、適用・回収へ渡さない。
                if (
                    isinstance(event, dict)
                    and isinstance(event.get("id"), str)
                    and event.get("status") == "cancelled"
                    and _baseline_matches(owner, event)
                ):
                    owner["stages"]["google_sync_baseline_preserved"] = 200
                    continue
                raise GoogleStateError("google_sync_unowned_source")
            current_items.append(event)
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
        if owner.get("full_apply"):
            # 記録済み履歴以外の全入力・順序は維持する。
            result["items"] = current_items
            result["events"] = len(current_items)
            owner["stages"]["google_sync_full_input"] = 200
            owner["pending_ids"] = [e["id"] for e in result["items"][1:]] if owner["step"] == 0 else []
            return result
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
            # 所有権に使う名前・説明を変えず、不正な日時の更新を1回だけ送る。
            status, rejected = await discord_request(
                env, owner["stages"], {}, "google_sync_discord_invalid_update",
                "PATCH", _discord_path(env, event_id), payload={"scheduled_start_time": "not-a-date"},
            )
            # 想定外に受理された場合も、応答statusを回収前の証跡に残す。
            await _save(store, owner)
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
    expected_pending = (
        len(owner.get("pending_ids", [])) if owner.get("full_apply")
        else 1 if owner["step"] in (0, 4) else 0
    )
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
    if owner.get("full_apply"):
        owner["shared_writes"] = kv.shared_writes
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
    if owner.get("full_apply"):
        expected = owner.get("pending_ids", [])
    if [e["id"] for e in queue] != expected:
        raise GoogleStateError("google_sync_queue_mismatch")
    if owner["step"] == 4 and (
        not _source_owned(queue[0], owner["fixtures"][1])
        or queue[0].get("description") != owner["fixtures"][1]["source"]["description"]
    ):
        raise GoogleStateError("google_sync_queue_mismatch")
    result = json.loads(values[KEYS[5]] or "{}").get("payload", {})
    if any(
        result.get(k) is not (owner["step"] != 4 and not ((owner.get("notion_query_retry") or owner.get("notion_create_retry") or owner.get("notion_writeback_retry")) and owner["step"] == 1)) for k in ("ok", "google_apply_ok")
    ):
        raise GoogleStateError("google_sync_result_mismatch")
    notion_map = json.loads(values[KEYS[1]] or "{}")["internal"]
    discord_map = json.loads(values[KEYS[2]] or "{}")
    for index, slot in enumerate(owner["fixtures"]):
        absent = (slot["google_event_id"] in expected and (owner["step"] == 0 or (owner.get("notion_query_retry") or owner.get("notion_create_retry") or owner.get("notion_writeback_retry")))) or (
            owner["step"] >= 3 and index == 0 and not (owner.get("notion_query_retry") or owner.get("notion_create_retry") or owner.get("notion_writeback_retry"))
        )
        if owner.get("notion_writeback_retry") and owner["step"] == 1:
            absent = slot["google_event_id"] in expected[1:]
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
        if owner["step"] >= 3 and index == 0 and not (owner.get("notion_query_retry") or owner.get("notion_create_retry") or owner.get("notion_writeback_retry")):
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
    from e2e_google_sync_state import state_keys
    if owner.get("webhook_sync"):
        from e2e_watch_shared_probe import cleanup_watches
        await cleanup_watches(env, store, owner, token)
    if owner.get("full_apply") or owner.get("http_sync"):
        await _check_shared_cleanup(env, owner, keys=state_keys(owner) if owner.get("http_sync") else KEYS)
    cleaned = 0
    for index, slot in enumerate(owner["fixtures"]):
        if owner.get("boundary") and slot.get("cleaned"):
            continue
        if owner.get("boundary") and cleaned >= 5:
            raise GoogleStateError("google_sync_cleanup_pending")
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
                if status != 200 or not _discord_owned(env, event, owner, slot):
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
        if (owner.get("notion_query_retry") or owner.get("notion_create_retry") or owner.get("notion_writeback_retry")):
            from e2e_google_notion_query import verify_removed
            await verify_removed(env, owner, slot, token)
            owner["stages"][f"{'notion_writeback' if owner.get('notion_writeback_retry') else 'notion_create' if owner.get('notion_create_retry') else 'notion_query'}_cleanup_{index}"] = 200
            await _save(store, owner)
        if owner.get("boundary"):
            # 各資源の消失を読戻してから完了を保存し、次HTTPでは未回収分だけ扱う。
            from e2e_google_boundary_probe import verify_removed
            await verify_removed(env, owner, slot, token)
            slot["cleaned"] = True
            owner["stages"][f"google_boundary_cleanup_{index}"] = 200
            await _save(store, owner)
            cleaned += 1
    prefix = GoogleKV(store, owner).prefix
    for key in state_keys(owner):
        if owner.get("full_apply") or owner.get("http_sync"):
            await _check_shared_cleanup(env, owner, keys=(key,))
            if key not in owner.get("shared_writes", {}):
                continue
        await env.STATE_KV.delete(prefix + key)
    if owner.get("full_apply") or owner.get("http_sync"):
        if any([await _raw_shared(env, key) is not None for key in state_keys(owner)]):
            raise GoogleStateError("google_sync_not_ready")
        owner["stages"]["google_sync_shared_cleanup"] = 200
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


async def _raw_shared(env, key):
    value = await env.STATE_KV.get(key)
    return None if value is None or str(value) in ("jsnull", "jsundefined") else str(value)


async def _check_shared_cleanup(env, owner, *, keys=KEYS):
    for key in keys:
        value = await _raw_shared(env, key)
        if value is not None and digest(value) not in owner.get("shared_writes", {}).get(key, []):
            raise GoogleStateError("google_sync_shared_owner_mismatch")


async def _check_full_empty(env, token, stages, *, origin_maps=None, compact=False):
    if any([await _raw_shared(env, key) is not None for key in KEYS]):
        raise GoogleStateError("google_sync_shared_not_empty")
    result = await run_google_delta_fetch(GoogleEnv(env, token), StateStore(_DeltaEnv(env)), commit_cursor=False)
    if not result.get("ok") or not isinstance(result.get("items"), list):
        raise GoogleStateError("google_sync_calendar_fetch_failed")
    baseline = {}
    for event in result["items"]:
        if (
            not isinstance(event, dict)
            or event.get("status") != "cancelled"
            or not isinstance(event.get("id"), str)
            or not event["id"]
        ):
            raise GoogleStateError("google_sync_calendar_not_empty")
        key = digest(event["id"])
        if key in baseline:
            raise GoogleStateError("google_sync_calendar_duplicate")
        baseline[key] = _deleted_fingerprint(event)
        if origin_maps is not None:
            from google_apply_sync import _google_origin_discord_event_id
            origin = _google_origin_discord_event_id(event)
            if origin:
                origin_maps[key] = digest(origin)
        if len(baseline) > (MAX_QUERY_BASELINE_DELETED if compact else MAX_BASELINE_DELETED):
            raise GoogleStateError("google_sync_baseline_limit")
    status, events = await discord_request(
        env, stages, {}, "google_sync_empty_guild", "GET",
        f"/guilds/{quote(env.DISCORD_GUILD_ID, safe='')}/scheduled-events",
    )
    if status != 200 or events != []:
        raise GoogleStateError("google_sync_guild_not_empty")
    status, pages = await notion_request(
        env, stages, {}, "google_sync_empty_database", "POST",
        f"/databases/{quote(env.NOTION_EVENT_INTERNAL_ID, safe='')}/query", {"page_size": 1},
    )
    if status != 200 or pages.get("results") != [] or pages.get("has_more") is not False:
        raise GoogleStateError("google_sync_database_not_empty")
    stages["google_sync_shared_empty"] = 200
    return list(baseline.values()) if compact else baseline


async def _phase(env, store, run_id, phase, invoke):
    notion_query_retry = phase == "prepare_notion_query"
    notion_create_retry = phase == "prepare_notion_create"
    notion_writeback_retry = phase == "prepare_notion_writeback"
    full_apply = phase in ("prepare_full", "prepare_notion_query", "prepare_notion_create", "prepare_notion_writeback")
    if full_apply:
        phase = "prepare"
    owner = await store.get_e2e_manifest(SERVICE)
    if phase in ("prepare_all", "prepare_http", "prepare_webhook", "webhook_trigger", "webhook_advance", "http_advance") or (owner and owner.get("dirty") and owner.get("all_sync")):
        from e2e_all_sync_probe import run_phase
        return await run_phase(env, store, run_id, phase, invoke, owner)
    if phase == "prepare_boundary" or (owner and owner.get("dirty") and owner.get("boundary")):
        from e2e_google_boundary_probe import run_phase
        return await run_phase(env, store, run_id, phase, invoke, owner)
    if phase == "prepare_matrix" or (owner and owner.get("dirty") and owner.get("matrix")):
        from e2e_google_matrix_probe import run_phase
        return await run_phase(env, store, run_id, phase, invoke, owner)
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
        baseline = await _check_full_empty(env, token, stages, compact=notion_query_retry or notion_create_retry or notion_writeback_retry) if full_apply else {}
        slots = []
        for index in range(3 if full_apply else 2):
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
            "retry_enabled": not full_apply,
            "api_rejection_enabled": not full_apply,
            "full_apply": full_apply,
            "notion_query_retry": notion_query_retry,
            "notion_create_retry": notion_create_retry,
            "notion_writeback_retry": notion_writeback_retry,
            "shared_writes": {},
            "baseline_deleted": baseline,
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
        if (owner.get("notion_query_retry") or owner.get("notion_create_retry") or owner.get("notion_writeback_retry")):
            from e2e_google_notion_query import verify_phase
            await verify_phase(env, store, owner)
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
        if owner["step"] in (2, 4) and not (owner.get("notion_query_retry") or owner.get("notion_create_retry") or owner.get("notion_writeback_retry")):
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
        if owner["step"] == 3 and not (owner.get("notion_query_retry") or owner.get("notion_create_retry") or owner.get("notion_writeback_retry")):
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
    steps = ("pending", "retry_pending", "retried", "drained") if (owner.get("notion_query_retry") or owner.get("notion_create_retry") or owner.get("notion_writeback_retry")) else STEPS
    return {
        "ok": True,
        "dirty": True,
        "status": steps[owner["step"]],
        "stage": f"google_{steps[owner['step']]}_verified"
        if phase == "verify"
        else "ready",
    }


async def _inspect_calendar(env, run_id):
    """予定本文を要求せず、全ページのstatusだけを読む。状態は保存しない。"""
    token = await get_google_access_token(env, StateStore(_DeltaEnv(env)))
    if not token:
        raise GoogleStateError("google_sync_token_required")
    url = _event_collection_url(env.GOOGLE_CALENDAR_ID) + (
        "?singleEvents=true&showDeleted=true&maxResults=2500&fields=items(status),nextPageToken"
    )
    statuses = set()
    seen = set()
    page = ""
    while True:
        status, data = await _google_request("GET", url + page, token)
        if status != 200:
            code = status if type(status) is int and 100 <= status <= 599 else 0
            raise GoogleStateError(f"google_sync_calendar_http_{code}")
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise GoogleStateError("google_sync_calendar_response_invalid")
        for event in data["items"]:
            value = event.get("status") if isinstance(event, dict) else None
            if value not in ("confirmed", "tentative", "cancelled"):
                raise GoogleStateError("google_sync_calendar_response_invalid")
            statuses.add("deleted" if value == "cancelled" else "active")
        page_token = data.get("nextPageToken")
        if not page_token:
            break
        if not isinstance(page_token, str) or page_token in seen:
            raise GoogleStateError("google_sync_calendar_page_invalid")
        seen.add(page_token)
        page = "&pageToken=" + quote(page_token, safe="")
    classification = "mixed" if len(statuses) == 2 else next(iter(statuses), "empty")
    return {"ok": True, "dirty": False, "run_id": run_id, "status": f"calendar_{classification}"}


def _release_exception_cause(exc):
    # 任意の例外本文は分類にだけ使用し、応答・ログ・manifestへコピーしない。
    message = str(exc)
    patterns = (
        ("connection_limit", r"connection limit|too many (?:open )?connections"),
        ("object_reset", r"durable object.*reset|object.*reset.*code.*updated"),
        ("subrequest_limit", r"too many subrequests|subrequest.*limit"),
        ("storage_timeout", r"storage operation exceeded timeout"),
        ("overloaded", r"overloaded|too many (?:requests|concurrent|queued)|queued for too long"),
        ("cpu_limit", r"cpu.*(?:limit|exceeded)"),
        ("memory_limit", r"memory.*(?:limit|exceeded)"),
        ("python_proxy", r"borrowed proxy|pyproxy|jsproxy"),
        ("io_context", r"different request|different.*I/O context|I/O.*context"),
        ("request_cancelled", r"I/O.*cancel|request.*cancel|context.*cancel"),
        ("disconnected", r"disconnected|broken pipe|network connection lost"),
        ("internal_error", r"internal error"),
        ("data_clone", r"DataCloneError|could not be cloned"),
    )
    return next((kind for kind, pattern in patterns if re.search(pattern, message, re.I)), "unknown")


async def _release_control(store, stub, control):
    """解放失敗から限定再試行し、復旧できない場合だけ安全な診断を返す。"""
    diagnostic = {
        "step": "release_rpc", "exception": "none",
        "release_ok": None, "status_ok": None, "owner_matches": None,
    }
    blocked = False
    try:
        released = await asyncio.wait_for(
            store._sync_do_rpc(stub, "release", {"owner": control}), _RPC_TIMEOUT_SECONDS,
        )
        diagnostic["release_ok"] = bool(released and released.get("ok"))
        diagnostic["step"] = "status_rpc"
        status = await asyncio.wait_for(store._sync_do_rpc(stub, "status"), _RPC_TIMEOUT_SECONDS)
        diagnostic["status_ok"] = bool(status and status.get("ok"))
        lock = status.get("lock") if status else None
        if isinstance(lock, dict):
            diagnostic["owner_matches"] = lock.get("owner") == control
        if not diagnostic["release_ok"]:
            diagnostic["step"] = "release_response"
        elif not diagnostic["status_ok"]:
            diagnostic["step"] = "status_response"
        elif not isinstance(lock, dict):
            diagnostic["step"] = "lock_response"
        elif diagnostic["owner_matches"]:
            diagnostic["step"] = "owner_check"
        else:
            return None
    except Exception as exc:
        diagnostic["exception"] = (
            "timeout" if isinstance(exc, TimeoutError)
            else "type_error" if isinstance(exc, TypeError)
            else "runtime_error" if isinstance(exc, RuntimeError)
            else "js_exception" if type(exc).__name__ == "JsException"
            else "other"
        )
        diagnostic["cause"] = _release_exception_cause(exc)
        blocked = _release_retry_blocked(exc)
    recovered = await _recover_release(
        lambda: store.env.SYNC_COORDINATOR.getByName("e2e:google-sync-control"),
        store._sync_do_rpc, control, "google_sync_control", blocked=blocked,
    )
    if recovered["ok"]:
        return None
    diagnostic["fresh_status_ok"] = recovered["status_ok"]
    diagnostic["fresh_owner_matches"] = recovered["owner_matches"]
    return diagnostic


async def run_google_sync_probe(env, store, run_id, phase, invoke):
    if not RUN_PATTERN.fullmatch(run_id) or phase not in (
        "prepare",
        "prepare_full",
        "prepare_notion_query",
        "prepare_notion_create",
        "prepare_notion_writeback",
        "prepare_matrix",
        "prepare_boundary",
        "prepare_all",
        "prepare_http",
        "prepare_webhook",
        "webhook_trigger",
        "webhook_advance",
        "http_advance",
        "inspect",
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
    if phase == "inspect":
        try:
            return await asyncio.wait_for(_inspect_calendar(env, run_id), 50)
        except Exception as exc:
            return {"ok": False, "dirty": False, "run_id": run_id,
                    "error": str(exc) if isinstance(exc, GoogleStateError) else "google_sync_calendar_inspect_failed"}
    existing = await store.get_e2e_manifest(SERVICE)
    full_mode = phase in ("prepare_full", "prepare_notion_query", "prepare_notion_create", "prepare_notion_writeback", "prepare_boundary", "prepare_matrix", "prepare_all", "prepare_http", "prepare_webhook", "webhook_trigger", "webhook_advance", "http_advance") or bool(existing and existing.get("dirty") and (existing.get("full_apply") or existing.get("all_sync")))
    if full_mode:
        # 共有状態を通常routeやCronから同時に変更できる構成では開始・続行しない。
        disabled = (
            "E2E_ORCHESTRATED_WRITES_ENABLED", "CRON_ENABLE_SYNC",
            "CRON_ENABLE_DISCORD_NOTION_SYNC", "CRON_ENABLE_GCAL_WATCH_ENSURE",
            "CRON_ENABLE_QA", "CRON_ENABLE_REMINDER", "CRON_ENABLE_AUTO_CLEAN",
        )
        if any(str(getattr(env, key, "false")).lower() != "false" for key in disabled):
            return {"ok": False, "error": "google_sync_full_configuration_invalid"}
    stub = env.SYNC_COORDINATOR.getByName("e2e:google-sync-control")
    control = f"{run_id}-{uuid4().hex}"
    lock = await store._sync_do_rpc(
        stub, "acquire", {"owner": control, "ttl_seconds": 300}
    )
    if not lock or lock.get("ok") is not True:
        return {"ok": False, "error": "google_sync_busy"}
    try:
        result = await asyncio.wait_for(
            _phase(env, store, run_id, phase, invoke),
            _FULL_PHASE_TIMEOUT_SECONDS if full_mode else _PHASE_TIMEOUT_SECONDS,
        )
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
    diagnostic = await _release_control(store, stub, control)
    if diagnostic is not None:
        return {"ok": False, "dirty": True, "error": "google_sync_release_failed",
                "release_diagnostic": diagnostic}
    clean = result.pop("_clean_manifest", None)
    if clean:
        await store.put_e2e_manifest(SERVICE, clean)
    result["run_id"] = run_id
    return result
