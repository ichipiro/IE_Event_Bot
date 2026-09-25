"""実Calendarの17件を共有queueへ保存し、通常同期で5件ずつ処理する。"""

import json
from uuid import uuid4
from urllib.parse import quote

from e2e_discord_batch_state import slot_run_id
from e2e_google_boundary_state import COUNT, LIMIT, QUEUE_STEP, LAST_APPLY, LAST_STEP, STATUSES, pending_count
from e2e_google_matrix_probe import _source_matches, _same_time
from e2e_google_sync_state import GoogleKV, GoogleStateError, KEYS, KIND, source_id
from e2e_google_sync_probe import (
    GoogleEnv, _DeltaEnv, _EVENT_SCHEMA, _check_full_empty, _cleanup,
    _deleted_fingerprint, _discord_event_is_owned, _discord_path, _event_collection_url,
    _event_item_url, _event_payload, _google_request, _lock_state, _page_owned,
    _property_text, _save, _targets, _verify_calendar, _verify_database, _verify_guild,
    discord_request, notion_request,
)
from google_auth import get_google_access_token
from google_calendar_sync import run_google_delta_fetch
from state import StateStore


def _new_owner(env, run_id, baseline, stages):
    """外部作成前に固定17件のIDと所有markerを確定する。"""
    slots = []
    for index in range(COUNT):
        subrun = slot_run_id(run_id, index)
        source = _event_payload(subrun, source_id(run_id, index))
        slots.append({"run_id": subrun, "google_event_id": source["id"], "source": source,
                      "source_attempted": False, "apply_attempted": False,
                      "delete_attempted": False, "cleaned": False})
    return {"version": 1, "kind": KIND, "run_id": run_id, "scope_id": uuid4().hex,
            "target_fingerprints": _targets(env), "boundary": True, "full_apply": True,
            "dirty": True, "step": 0, "stage": "working", "hashes": {},
            "shared_writes": {}, "pending_ids": [], "baseline_deleted": list(baseline.values()),
            "fixtures": slots, "stages": stages}


async def _fetch(env, state, owner):
    """通常全ページ取得の結果を、所有17件と開始前の削除履歴に限定する。"""
    result = await run_google_delta_fetch(env, state, commit_cursor=False)
    if not result.get("ok"):
        raise GoogleStateError("google_boundary_fetch_failed")
    slots = {s["google_event_id"]: s for s in owner["fixtures"]}
    items, seen = [], set()
    for event in result.get("items", []):
        event_id = event.get("id") if isinstance(event, dict) else None
        if event_id not in slots:
            if (isinstance(event_id, str) and event.get("status") == "cancelled"
                    and _deleted_fingerprint(event) in owner["baseline_deleted"]):
                continue
            raise GoogleStateError("google_boundary_unowned_source")
        if event_id in seen or not _source_matches(event, slots[event_id]):
            raise GoogleStateError("google_boundary_source_mismatch")
        seen.add(event_id)
        items.append(event)
    if owner["step"] == QUEUE_STEP and seen != set(slots):
        raise GoogleStateError("google_boundary_source_not_visible")
    return {**result, "items": items, "events": len(items)}


async def _load_queue(env, store, owner, token):
    """適用前の17件を保存し、cursor未更新を別HTTPで確認できるようにする。"""
    kv = GoogleKV(store, owner)
    result = await _fetch(GoogleEnv(env, token), kv.state(), owner)
    await kv.state().put_json_if_changed(KEYS[3], result["items"])
    if await kv.get(KEYS[0]) is not None:
        raise GoogleStateError("google_boundary_cursor_changed")
    owner["pending_ids"] = [e["id"] for e in result["items"]]
    owner["hashes"] = kv.hashes
    owner["stages"]["google_boundary_input_17"] = 200
    owner["stages"]["google_boundary_initial_cursor_preserved"] = 200
    await _save(store, owner)


async def _apply(env, store, owner, token, invoke):
    """入力を追加せず、別HTTPに残したqueueだけを通常dispatchで消化する。"""
    kv = GoogleKV(store, owner)
    probe_env = GoogleEnv(env, token)
    probe_env.GOOGLE_APPLY_MAX_EVENTS_PER_RUN = str(LIMIT)
    batch = owner["pending_ids"][:LIMIT]
    for slot in owner["fixtures"]:
        if slot["google_event_id"] in batch:
            slot["apply_attempted"] = True
    await _save(store, owner)

    async def fetcher(_env, state, *, commit_cursor):
        # 差分取得は実APIを通すが、適用入力は空にして保存queueの独立性を試す。
        result = await _fetch(probe_env, state, owner)
        return {**result, "items": [], "events": 0}

    response = await invoke(probe_env, kv.state(), fetcher)
    payload = json.loads(await response.text())
    applied = payload.get("google_apply", {})
    queue = json.loads(await kv.get(KEYS[3]) or "[]")
    if (response.status != 200 or payload.get("ok") is not True
            or applied.get("processed") != len(batch) or applied.get("max_events_per_run") != LIMIT
            or applied.get("pending_events") != pending_count(owner["step"])
            or [e.get("id") for e in queue] != owner["pending_ids"][LIMIT:]):
        raise GoogleStateError("google_boundary_apply_mismatch")
    slots = {s["google_event_id"]: s for s in owner["fixtures"]}
    if any(not _source_matches(e, slots[e["id"]]) for e in queue):
        raise GoogleStateError("google_boundary_queue_mismatch")
    owner["expected_cursor"] = payload["google"]["next_updated_min"]
    if await kv.get(KEYS[0]) != owner["expected_cursor"]:
        raise GoogleStateError("google_boundary_cursor_mismatch")
    for key, field in ((KEYS[1], "notion_page_id"), (KEYS[2], "discord_event_id")):
        mapping = kv.references.get(key, {})
        mapping = mapping.get("internal", {}) if key == KEYS[1] else mapping
        for slot in owner["fixtures"]:
            target = mapping.get(slot["google_event_id"])
            if target:
                if slot.get(field) not in (None, target):
                    raise GoogleStateError("google_boundary_reference_changed")
                slot[field] = target
    owner["pending_ids"] = [e["id"] for e in queue]
    owner["hashes"] = kv.hashes
    owner["stages"][f"google_boundary_apply_{owner['step']}_{len(batch)}"] = 200
    await _save(store, owner)


async def _verify_slot(env, owner, slot, token):
    """実APIから所有ID・本文・日時・NotionのDiscord ID書戻しを読み直す。"""
    status, event = await _google_request("GET", _event_item_url(env.GOOGLE_CALENDAR_ID, slot["google_event_id"]), token)
    if status != 200 or not _source_matches(event, slot):
        raise GoogleStateError("google_boundary_source_mismatch")
    if not slot["apply_attempted"]:
        return
    status, page = await notion_request(env, owner["stages"], {}, "google_boundary_page_read", "GET", f"/pages/{quote(slot['notion_page_id'], safe='')}")
    if (status != 200 or not _page_owned(env, page, slot) or page.get("archived") or page.get("in_trash")
            or _property_text(page, "内容", "rich_text") != slot["source"]["description"]
            or _property_text(page, "メッセージID", "rich_text") != slot["discord_event_id"]):
        raise GoogleStateError("google_boundary_page_mismatch")
    status, event = await discord_request(env, owner["stages"], {}, "google_boundary_discord_read", "GET", _discord_path(env, slot["discord_event_id"]))
    if (status != 200 or not _discord_event_is_owned(event, event_id=slot["discord_event_id"], guild_id=env.DISCORD_GUILD_ID, run_id=slot["run_id"])
            or slot["source"]["description"] not in event.get("description", "")):
        raise GoogleStateError("google_boundary_discord_mismatch")
    for key in ("start", "end"):
        date = page.get("properties", {}).get("日時", {}).get("date", {}).get(key)
        expected = slot["source"][key]
        if not _same_time({"dateTime": date}, expected) or not _same_time({"dateTime": event.get(f"scheduled_{key}_time")}, expected):
            raise GoogleStateError("google_boundary_time_mismatch")


async def _verify(env, store, owner, token):
    """全KV・対応IDを照合し、外部読戻しは最大5件ずつに分ける。"""
    step = owner["step"]
    if step < QUEUE_STEP:
        if step:
            await _verify_slot(env, owner, owner["fixtures"][step - 1], token)
        return
    kv = GoogleKV(store, owner)
    values = {key: await kv.get(key) for key in KEYS}
    queue = json.loads(values[KEYS[3]] or "[]")
    if [e.get("id") for e in queue] != owner["pending_ids"] or len(queue) != pending_count(step):
        raise GoogleStateError("google_boundary_queue_mismatch")
    slots = {s["google_event_id"]: s for s in owner["fixtures"]}
    if any(not _source_matches(e, slots[e["id"]]) for e in queue):
        raise GoogleStateError("google_boundary_queue_mismatch")
    if step == QUEUE_STEP:
        if any(values[k] is not None for k in KEYS if k != KEYS[3]):
            raise GoogleStateError("google_boundary_cursor_changed")
        return
    if any(v is None for v in values.values()) or values[KEYS[0]] != owner["expected_cursor"]:
        raise GoogleStateError("google_sync_not_ready")
    result = json.loads(values[KEYS[5]] or "{}")["payload"]
    if result.get("ok") is not True or result.get("google_apply_ok") is not True:
        raise GoogleStateError("google_boundary_result_mismatch")
    for key, field in ((KEYS[1], "notion_page_id"), (KEYS[2], "discord_event_id")):
        mapping = json.loads(values[key] or "{}")
        mapping = mapping.get("internal", {}) if key == KEYS[1] else mapping
        expected = {s["google_event_id"]: s.get(field) for s in owner["fixtures"] if s["apply_attempted"]}
        if mapping != expected or any(not v for v in expected.values()) or len(set(expected.values())) != len(expected):
            raise GoogleStateError("google_boundary_map_mismatch")
    if step <= LAST_APPLY:
        # 作成順とAPI列挙順は同じとは限らないため、今回反映されたIDから選ぶ。
        selected = [s for s in owner["fixtures"] if s["apply_attempted"]
                    and owner["stages"].get(f"google_boundary_slot_{owner['fixtures'].index(s)}") != 200]
    else:
        start = (step - LAST_APPLY - 1) * LIMIT
        selected = owner["fixtures"][start:start + LIMIT]
    for slot in selected:
        await _verify_slot(env, owner, slot, token)
        owner["stages"][f"google_boundary_slot_{owner['fixtures'].index(slot)}"] = 200
    owner["stages"][f"google_boundary_cursor_queue_{step}"] = 200


async def run_phase(env, store, run_id, phase, invoke, owner):
    """既存Google同期の認証・version・制御ロック内で固定段階を進める。"""
    if (await _lock_state(store)).get("owner"):
        raise GoogleStateError("google_sync_busy")
    if owner and owner.get("dirty"):
        if owner.get("run_id") != run_id or owner.get("target_fingerprints") != _targets(env) or not owner.get("boundary"):
            raise GoogleStateError("google_sync_owner_mismatch")
    elif phase != "prepare_boundary" or (owner and owner.get("last_run_id") == run_id):
        raise GoogleStateError("google_sync_phase_invalid")
    token = await get_google_access_token(env, StateStore(_DeltaEnv(env)))
    if not token:
        raise GoogleStateError("google_sync_token_required")
    if phase == "prepare_boundary":
        if owner and owner.get("dirty"):
            raise GoogleStateError("google_sync_phase_invalid")
        stages = {}
        if (await _verify_calendar(env.GOOGLE_CALENDAR_ID, token, stages)
                or await _verify_guild(env, env.DISCORD_GUILD_ID, stages, {})
                or await _verify_database(env, env.NOTION_EVENT_INTERNAL_ID, _EVENT_SCHEMA, stages, {}, "google_boundary_database", "notion_event")):
            raise GoogleStateError("google_sync_target_mismatch")
        baseline = await _check_full_empty(env, token, stages)
        owner = _new_owner(env, run_id, baseline, stages)
        await _save(store, owner)
    elif phase == "verify":
        if owner["stage"] not in ("ready", "verified"):
            raise GoogleStateError("google_sync_phase_invalid")
        await _verify(env, store, owner, token)
        owner["stage"] = "verified"
        owner["stages"][f"google_boundary_step_{owner['step']}"] = 200
        if owner["step"] >= QUEUE_STEP:
            owner["stages"][f"google_boundary_queue_{pending_count(owner['step'])}"] = 200
        await _save(store, owner)
        return {"ok": True, "dirty": True, "status": STATUSES[owner["step"]], "stage": "verified"}
    elif phase == "advance":
        if owner["stage"] != "verified" or owner["step"] >= LAST_STEP:
            raise GoogleStateError("google_sync_phase_invalid")
        owner["step"] += 1
        owner["stage"] = "working"
        await _save(store, owner)
        if owner["step"] < QUEUE_STEP:
            slot = owner["fixtures"][owner["step"] - 1]
            status, _ = await _google_request("GET", _event_item_url(env.GOOGLE_CALENDAR_ID, slot["google_event_id"]), token)
            if status != 404:
                raise GoogleStateError("google_boundary_source_collision")
            slot["source_attempted"] = True
            await _save(store, owner)
            status, event = await _google_request("POST", _event_collection_url(env.GOOGLE_CALENDAR_ID) + "?sendUpdates=none", token, slot["source"])
            if status != 200 or not _source_matches(event, slot):
                raise GoogleStateError("google_boundary_source_create_failed")
        elif owner["step"] == QUEUE_STEP:
            await _load_queue(env, store, owner, token)
        elif owner["step"] <= LAST_APPLY:
            await _apply(env, store, owner, token, invoke)
    elif phase == "cleanup":
        owner["passed"] = owner.get("passed", False) if owner["stage"] == "cleanup" else owner["stage"] == "verified" and owner["step"] == LAST_STEP
        owner["stage"] = "cleanup"
        await _save(store, owner)
        clean = await _cleanup(env, store, owner, token)
        return {"ok": True, "dirty": False, "_clean_manifest": clean}
    else:
        raise GoogleStateError("google_sync_phase_invalid")
    owner["stage"] = "ready"
    await _save(store, owner)
    return {"ok": True, "dirty": True, "status": STATUSES[owner["step"]], "stage": "ready"}


async def verify_removed(env, owner, slot, token):
    """回収の応答だけでなく、各所有資源が消えた状態を別GETで確認する。"""
    if slot.get("discord_event_id"):
        status, _ = await discord_request(env, owner["stages"], {}, "google_boundary_discord_absent", "GET", _discord_path(env, slot["discord_event_id"]))
        if status != 404:
            raise GoogleStateError("google_boundary_cleanup_not_ready")
    if slot.get("notion_page_id"):
        status, page = await notion_request(env, owner["stages"], {}, "google_boundary_page_archived", "GET", f"/pages/{quote(slot['notion_page_id'], safe='')}")
        if status != 200 or not _page_owned(env, page, slot) or not (page.get("archived") or page.get("in_trash")):
            raise GoogleStateError("google_boundary_cleanup_not_ready")
    if slot["source_attempted"]:
        status, event = await _google_request("GET", _event_item_url(env.GOOGLE_CALENDAR_ID, slot["google_event_id"]), token)
        if status not in (404, 410) and not (status == 200 and event.get("id") == slot["google_event_id"] and event.get("status") == "cancelled"):
            raise GoogleStateError("google_boundary_cleanup_not_ready")
