"""終日・繰返し2回を含む5件と共有queueの段階別E2E。"""

import json
from copy import deepcopy
from datetime import datetime, timedelta
from urllib.parse import quote
from uuid import uuid4

from e2e_discord_batch_state import slot_run_id
from e2e_google_matrix_state import STATUSES
from e2e_google_sync_state import GoogleKV, GoogleStateError, KEYS, KIND, digest, source_id
from e2e_google_sync_probe import (
    GoogleEnv, _DeltaEnv, _EVENT_SCHEMA, _check_full_empty, _cleanup,
    _deleted_fingerprint, _discord_event_is_owned, _discord_path, _event_collection_url,
    _event_item_url, _event_payload, _google_request, _lock_state, _page_owned,
    _property_text, _save, _source_owned, _targets, _verify_calendar,
    _verify_database, _verify_guild, discord_request, notion_request,
)
from google_apply_sync import _sync_to_discord
from google_auth import get_google_access_token
from google_calendar_sync import run_google_delta_fetch
from state import StateStore


def _instant(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _same_time(actual, expected):
    if "date" in expected:
        return actual.get("date") == expected["date"] and not actual.get("dateTime")
    try:
        return _instant(actual["dateTime"]) == _instant(expected["dateTime"])
    except (KeyError, TypeError, ValueError):
        return False


def _source_matches(event, slot):
    source = slot["source"]
    return (
        _source_owned(event, slot) and event.get("status") != "cancelled"
        and event.get("description") == source["description"]
        and all(_same_time(event.get(k, {}), source[k]) for k in ("start", "end"))
        and (not source.get("recurringEventId") or (
            event.get("recurringEventId") == source["recurringEventId"]
            and _same_time(event.get("originalStartTime", {}), source["originalStartTime"])
        ))
    )


def _new_owner(env, run_id, baseline, stages):
    slots = []
    series_source = _event_payload(slot_run_id(run_id, 3), source_id(run_id, 3))
    series_source["recurrence"] = ["RRULE:FREQ=DAILY;COUNT=2"]
    for index in range(5):
        subrun = slot_run_id(run_id, index)
        source = _event_payload(subrun, source_id(run_id, index))
        if index == 0:
            day = _instant(source["start"]["dateTime"]).date()
            source["start"], source["end"] = {"date": day.isoformat()}, {"date": (day + timedelta(days=1)).isoformat()}
        if index >= 3:
            source["id"] = None
            for key in ("start", "end"):
                source[key] = {"dateTime": (_instant(series_source[key]["dateTime"]) + timedelta(days=index - 3)).isoformat(),
                               "timeZone": "Asia/Tokyo"}
            source["recurringEventId"] = series_source["id"]
            source["originalStartTime"] = deepcopy(source["start"])
        slots.append({"run_id": subrun, "google_event_id": source["id"], "source": source,
                      "source_attempted": False, "apply_attempted": False, "delete_attempted": False})
    return {"version": 1, "kind": KIND, "run_id": run_id, "scope_id": uuid4().hex,
            "target_fingerprints": _targets(env), "matrix": True, "full_apply": True,
            "retry_enabled": True, "dirty": True, "step": 0, "stage": "working",
            "hashes": {}, "shared_writes": {}, "pending_ids": [], "baseline_deleted": baseline,
            "fixtures": slots, "stages": stages,
            "series": {"source": series_source, "source_attempted": False, "delete_attempted": False}}


async def _seed(env, store, owner, token):
    step = owner["step"]
    slot = owner["fixtures"][step - 1] if step < 4 else owner["series"]
    source = slot["source"]
    status, _ = await _google_request("GET", _event_item_url(env.GOOGLE_CALENDAR_ID, source["id"]), token)
    if status != 404:
        raise GoogleStateError("google_matrix_source_collision")
    slot["source_attempted"] = True
    await _save(store, owner)
    status, event = await _google_request("POST", _event_collection_url(env.GOOGLE_CALENDAR_ID) + "?sendUpdates=none", token, source)
    root_slot = {"source": source, "google_event_id": source["id"],
                 "run_id": source["extendedProperties"]["private"]["ie_event_bot_e2e_run"]}
    if status != 200 or not _source_matches(event, root_slot):
        raise GoogleStateError("google_matrix_source_create_failed")
    if step != 4:
        return
    if event.get("recurrence") != source["recurrence"]:
        raise GoogleStateError("google_matrix_series_mismatch")
    status, data = await _google_request("GET", _event_item_url(env.GOOGLE_CALENDAR_ID, source["id"]) + "/instances?maxResults=10", token)
    items = data.get("items") if isinstance(data, dict) else None
    if status != 200 or not isinstance(items, list) or len(items) != 2 or data.get("nextPageToken"):
        raise GoogleStateError("google_matrix_instances_invalid")
    ids = set()
    for child in owner["fixtures"][3:]:
        matches = [e for e in items if isinstance(e, dict) and
                   _same_time(e.get("originalStartTime", {}), child["source"]["originalStartTime"])]
        if len(matches) != 1:
            raise GoogleStateError("google_matrix_instances_invalid")
        event = matches[0]
        event_id = event.get("id")
        expected = {**child, "google_event_id": event_id,
                    "source": {**child["source"], "summary": source["summary"], "description": source["description"]},
                    "run_id": root_slot["run_id"]}
        if (not isinstance(event_id, str) or not event_id or event_id in ids
                or event_id in {s["google_event_id"] for s in owner["fixtures"][:3]}
                or not _source_matches(event, expected)):
            raise GoogleStateError("google_matrix_instances_invalid")
        ids.add(event_id)
        child["google_event_id"] = child["source"]["id"] = event_id
    # Googleが発行したinstance IDを保存してから、回ごとの所有markerを付ける。
    await _save(store, owner)
    for child in owner["fixtures"][3:]:
        patch = {key: child["source"][key] for key in ("summary", "description", "extendedProperties")}
        status, event = await _google_request("PATCH", _event_item_url(env.GOOGLE_CALENDAR_ID, child["google_event_id"]) + "?sendUpdates=none", token, patch)
        if status != 200 or not _source_matches(event, child):
            raise GoogleStateError("google_matrix_instance_mark_failed")


async def _change(env, store, owner, token):
    step = owner["step"]
    index = {8: 0, 9: 3, 10: 0, 11: 3, 12: 1}[step]
    slot = owner["fixtures"][index]
    path = _event_item_url(env.GOOGLE_CALENDAR_ID, slot["google_event_id"])
    status, event = await _google_request("GET", path, token)
    if status != 200 or not _source_matches(event, slot):
        raise GoogleStateError("google_matrix_change_owner_mismatch")
    if step in (10, 11):
        slot["delete_attempted"] = True
        await _save(store, owner)
        status, _ = await _google_request("DELETE", path + "?sendUpdates=none", token)
        if status != 204:
            raise GoogleStateError("google_matrix_delete_failed")
        return
    slot["previous_description"] = slot["source"]["description"]
    slot["source"]["description"] += "\nE2E matrix updated"
    patch = {"description": slot["source"]["description"]}
    if step in (8, 9):
        for key in ("start", "end"):
            old = slot["source"][key]
            slot["source"][key] = ({"date": (datetime.fromisoformat(old["date"]) + timedelta(days=1)).date().isoformat()}
                                   if step == 8 else {**old, "dateTime": (_instant(old["dateTime"]) + timedelta(hours=1)).isoformat()})
            patch[key] = slot["source"][key]
    await _save(store, owner)
    status, event = await _google_request("PATCH", path + "?sendUpdates=none", token, patch)
    if status != 200 or not _source_matches(event, slot):
        raise GoogleStateError("google_matrix_update_failed")


async def _apply(env, store, owner, token, invoke):
    kv = GoogleKV(store, owner)
    probe_env = GoogleEnv(env, token)
    probe_env.GOOGLE_APPLY_MAX_EVENTS_PER_RUN = "2" if owner["step"] <= 7 else "5"
    for slot in owner["fixtures"]:
        slot["apply_attempted"] = True
    await _save(store, owner)
    fetched_ids = []

    async def fetcher(_env, state, *, commit_cursor):
        result = await run_google_delta_fetch(probe_env, state, commit_cursor=False)
        if not result.get("ok"):
            raise GoogleStateError("google_matrix_fetch_failed")
        slots = {s["google_event_id"]: s for s in owner["fixtures"]}
        current, seen = [], set()
        for event in result.get("items", []):
            event_id = event.get("id") if isinstance(event, dict) else None
            slot = slots.get(event_id)
            if not slot:
                if (isinstance(event_id, str) and event.get("status") == "cancelled"
                        and owner["baseline_deleted"].get(digest(event_id)) == _deleted_fingerprint(event)):
                    continue
                raise GoogleStateError("google_matrix_unowned_source")
            if event_id in seen or (slot["delete_attempted"] and event.get("status") != "cancelled") or (
                not slot["delete_attempted"] and not _source_matches(event, slot)
            ):
                raise GoogleStateError("google_matrix_source_mismatch")
            seen.add(event_id)
            current.append(event)
        required = set(slots) if owner["step"] == 5 else (
            {owner["fixtures"][{8: 0, 9: 3, 10: 0, 11: 3, 12: 1}[owner["step"]]]["google_event_id"]}
            if 8 <= owner["step"] <= 12 else set()
        )
        if not required <= seen:
            raise GoogleStateError("google_matrix_source_not_visible")
        # 繰越・失敗再試行の呼出しでは、保存済みqueueだけの消化を検証する。
        result["items"] = [] if owner["step"] in (6, 7, 13) else current
        result["events"] = len(result["items"])
        fetched_ids.extend(e["id"] for e in result["items"])
        owner["stages"]["google_matrix_full_input"] = 200
        return result

    rejected = 0

    async def discord_runner(call_env, event, page, fallback, mapping):
        nonlocal rejected
        slot = owner["fixtures"][1]
        if event.get("id") != slot["google_event_id"]:
            return await _sync_to_discord(call_env, event, page, fallback, mapping)
        event_id = slot.get("discord_event_id")
        if rejected or not _source_matches(event, slot) or not page or mapping.get(event["id"]) != event_id or not event_id:
            raise GoogleStateError("google_matrix_rejection_owner_mismatch")
        status, current = await discord_request(env, owner["stages"], {}, "google_matrix_rejection_owner", "GET", _discord_path(env, event_id))
        if status != 200 or not _discord_event_is_owned(current, event_id=event_id, guild_id=env.DISCORD_GUILD_ID, run_id=slot["run_id"]):
            raise GoogleStateError("google_matrix_rejection_owner_mismatch")
        status, data = await discord_request(env, owner["stages"], {}, "google_matrix_api_rejection", "PATCH",
                                             _discord_path(env, event_id), {"scheduled_start_time": "not-a-date"})
        if status != 400 or data.get("code") != 50035:
            raise GoogleStateError("google_matrix_rejection_mismatch")
        rejected += 1
        return None

    response = await invoke(probe_env, kv.state(), fetcher, discord_runner if owner["step"] == 12 else None)
    payload = json.loads(await response.text())
    failed = owner["step"] == 12
    applied = payload.get("google_apply", {})
    if response.status != (500 if failed else 200) or payload.get("ok") is not (not failed):
        raise GoogleStateError("google_matrix_apply_failed")
    queue = json.loads(await kv.get(KEYS[3]) or "[]")
    expected_count = {5: 3, 6: 1, 12: 1}.get(owner["step"], 0)
    if applied.get("pending_events") != expected_count or len(queue) != expected_count:
        raise GoogleStateError("google_matrix_queue_mismatch")
    expected_ids = (fetched_ids[2:] if owner["step"] == 5 else
                    owner["pending_ids"][2:] if owner["step"] == 6 else
                    [owner["fixtures"][1]["google_event_id"]] if failed else [])
    slots = {slot["google_event_id"]: slot for slot in owner["fixtures"]}
    if ([e.get("id") for e in queue] != expected_ids
            or any(not _source_matches(e, slots[e["id"]]) for e in queue)):
        raise GoogleStateError("google_matrix_queue_mismatch")
    if failed:
        event_id = owner["fixtures"][1]["google_event_id"]
        if (rejected != 1 or applied.get("ok") is not False or applied.get("error_count") != 1
                or applied.get("errors") != [f"discord_sync_failed:{event_id}"]
                or [e.get("id") for e in queue] != [event_id]
                or any(kv.hashes.get(k) != owner["hashes"].get(k) for k in (KEYS[0], KEYS[4]))):
            raise GoogleStateError("google_matrix_partial_failure_mismatch")
        owner["stages"]["google_matrix_cursor_preserved"] = 200
    else:
        owner["expected_cursor"] = payload["google"]["next_updated_min"]
    if owner["step"] == 13 and applied.get("processed") != 1:
        raise GoogleStateError("google_matrix_retry_count_mismatch")
    owner["pending_ids"] = [e["id"] for e in queue]
    owner["hashes"] = kv.hashes
    owner["shared_writes"] = kv.shared_writes
    for slot in owner["fixtures"]:
        for key, field in ((KEYS[1], "notion_page_id"), (KEYS[2], "discord_event_id")):
            mapping = kv.references.get(key, {})
            event_id = (mapping.get("internal", {}) if key == KEYS[1] else mapping).get(slot["google_event_id"])
            if event_id:
                if slot.get(field) not in (None, event_id):
                    raise GoogleStateError("google_matrix_reference_changed")
                slot[field] = event_id
    await _save(store, owner)


async def _verify(env, store, owner, token):
    notion_map, discord_map = {}, {}
    if owner["step"] >= 5:
        kv = GoogleKV(store, owner)
        values = {key: await kv.get(key) for key in KEYS}
        if any(v is None for v in values.values()) or values[KEYS[0]] != owner["expected_cursor"]:
            raise GoogleStateError("google_sync_not_ready")
        if [e["id"] for e in json.loads(values[KEYS[3]] or "[]")] != owner["pending_ids"]:
            raise GoogleStateError("google_matrix_queue_mismatch")
        result = json.loads(values[KEYS[5]] or "{}")["payload"]
        if any(result.get(k) is not (owner["step"] != 12) for k in ("ok", "google_apply_ok")):
            raise GoogleStateError("google_matrix_result_mismatch")
        notion_map, discord_map = json.loads(values[KEYS[1]] or "{}")["internal"], json.loads(values[KEYS[2]] or "{}")
    for index, slot in enumerate(owner["fixtures"]):
        if not slot["source_attempted"] and (index < 3 or owner["step"] < 4):
            continue
        status, event = await _google_request("GET", _event_item_url(env.GOOGLE_CALENDAR_ID, slot["google_event_id"]), token)
        deleted = slot["delete_attempted"]
        if not ((deleted and (status in (404, 410) or (status == 200 and event.get("id") == slot["google_event_id"] and event.get("status") == "cancelled")))
                or (not deleted and status == 200 and _source_matches(event, slot))):
            raise GoogleStateError("google_matrix_source_mismatch")
        if owner["step"] < 5:
            continue
        pending = slot["google_event_id"] in owner["pending_ids"] and owner["step"] < 7
        absent = deleted or pending
        if absent:
            if slot["google_event_id"] in notion_map or slot["google_event_id"] in discord_map:
                raise GoogleStateError("google_matrix_map_mismatch")
        elif (not slot.get("notion_page_id") or not slot.get("discord_event_id")
              or notion_map.get(slot["google_event_id"]) != slot["notion_page_id"]
              or discord_map.get(slot["google_event_id"]) != slot["discord_event_id"]):
            raise GoogleStateError("google_matrix_map_mismatch")
        if slot.get("notion_page_id"):
            status, page = await notion_request(env, owner["stages"], {}, "google_matrix_page_read", "GET", f"/pages/{quote(slot['notion_page_id'], safe='')}")
            if status != 200 or not _page_owned(env, page, slot) or bool(page.get("archived") or page.get("in_trash")) != deleted:
                raise GoogleStateError("google_matrix_page_mismatch")
            if not deleted and _property_text(page, "内容", "rich_text") != slot["source"]["description"]:
                raise GoogleStateError("google_matrix_page_mismatch")
            if not deleted:
                dates = (page.get("properties", {}).get("日時", {}) or {}).get("date") or {}
                for key in ("start", "end"):
                    expected = slot["source"][key]
                    if not _same_time({"date" if "date" in expected else "dateTime": dates.get(key)}, expected):
                        raise GoogleStateError("google_matrix_notion_time_mismatch")
        if slot.get("discord_event_id"):
            event_id = slot["discord_event_id"]
            status, event = await discord_request(env, owner["stages"], {}, "google_matrix_discord_read", "GET", _discord_path(env, event_id))
            if deleted:
                if status != 404:
                    raise GoogleStateError("google_matrix_discord_mismatch")
                continue
            if status != 200 or not _discord_event_is_owned(event, event_id=event_id, guild_id=env.DISCORD_GUILD_ID, run_id=slot["run_id"]):
                raise GoogleStateError("google_matrix_discord_mismatch")
            description = slot["previous_description"] if owner["step"] == 12 and index == 1 else slot["source"]["description"]
            if description not in event.get("description", "") or (owner["step"] == 12 and index == 1 and slot["source"]["description"] in event.get("description", "")):
                raise GoogleStateError("google_matrix_discord_mismatch")
            for key in ("start", "end"):
                expected = slot["source"][key]
                # 通常実装の終日変換（開始09:00・終了01:00 JST）を独立に照合する。
                value = expected.get("dateTime") or expected["date"] + ("T09:00:00+09:00" if key == "start" else "T01:00:00+09:00")
                if not _same_time({"dateTime": event.get(f"scheduled_{key}_time")}, {"dateTime": value}):
                    raise GoogleStateError("google_matrix_discord_time_mismatch")


async def run_phase(env, store, run_id, phase, invoke, owner):
    if (await _lock_state(store)).get("owner"):
        raise GoogleStateError("google_sync_busy")
    if owner and owner.get("dirty"):
        if owner.get("run_id") != run_id or owner.get("target_fingerprints") != _targets(env) or not owner.get("matrix"):
            raise GoogleStateError("google_sync_owner_mismatch")
    elif phase != "prepare_matrix" or (owner and owner.get("last_run_id") == run_id):
        raise GoogleStateError("google_sync_phase_invalid")
    token = await get_google_access_token(env, StateStore(_DeltaEnv(env)))
    if not token:
        raise GoogleStateError("google_sync_token_required")
    if phase == "prepare_matrix":
        if owner and owner.get("dirty"):
            raise GoogleStateError("google_sync_phase_invalid")
        stages = {}
        if (await _verify_calendar(env.GOOGLE_CALENDAR_ID, token, stages)
                or await _verify_guild(env, env.DISCORD_GUILD_ID, stages, {})
                or await _verify_database(env, env.NOTION_EVENT_INTERNAL_ID, _EVENT_SCHEMA, stages, {}, "google_matrix_database", "notion_event")):
            raise GoogleStateError("google_sync_target_mismatch")
        baseline = await _check_full_empty(env, token, stages)
        owner = _new_owner(env, run_id, baseline, stages)
        await _save(store, owner)
    elif phase == "verify":
        if owner["stage"] not in ("ready", "verified"):
            raise GoogleStateError("google_sync_phase_invalid")
        await _verify(env, store, owner, token)
        owner["stage"] = "verified"
        owner["stages"][f"google_matrix_step_{owner['step']}"] = 200
        await _save(store, owner)
        return {"ok": True, "dirty": True, "status": STATUSES[owner["step"]], "stage": f"google_{STATUSES[owner['step']]}_verified"}
    elif phase == "advance":
        if owner["stage"] != "verified" or owner["step"] >= len(STATUSES) - 1:
            raise GoogleStateError("google_sync_phase_invalid")
        owner["step"] += 1
        owner["stage"] = "working"
        await _save(store, owner)
        if owner["step"] <= 4:
            await _seed(env, store, owner, token)
        else:
            if 8 <= owner["step"] <= 12:
                await _change(env, store, owner, token)
            await _apply(env, store, owner, token, invoke)
    elif phase == "cleanup":
        owner["passed"] = owner.get("passed", False) if owner["stage"] == "cleanup" else owner["stage"] == "verified" and owner["step"] == len(STATUSES) - 1
        owner["stage"] = "cleanup"
        await _save(store, owner)
        series = owner["series"]
        if series["source_attempted"]:
            path = _event_item_url(env.GOOGLE_CALENDAR_ID, series["source"]["id"])
            status, event = await _google_request("GET", path, token)
            root_slot = {"source": series["source"], "google_event_id": series["source"]["id"], "run_id": slot_run_id(run_id, 3)}
            if status not in (404, 410):
                if status != 200 or not (_source_matches(event, root_slot) or (series["delete_attempted"] and event.get("id") == series["source"]["id"] and event.get("status") == "cancelled")):
                    raise GoogleStateError("google_matrix_series_owner_mismatch")
                if event.get("status") != "cancelled":
                    if event.get("recurrence") != series["source"]["recurrence"]:
                        raise GoogleStateError("google_matrix_series_owner_mismatch")
                    status, data = await _google_request("GET", path + "/instances?showDeleted=true&maxResults=10", token)
                    items = data.get("items") if isinstance(data, dict) else None
                    if status != 200 or not isinstance(items, list) or len(items) > 2 or data.get("nextPageToken"):
                        raise GoogleStateError("google_matrix_series_owner_mismatch")
                    for item in items:
                        children = [s for s in owner["fixtures"][3:] if
                                    _same_time(item.get("originalStartTime", {}), s["source"]["originalStartTime"])]
                        if len(children) != 1 or item.get("recurringEventId") != series["source"]["id"]:
                            raise GoogleStateError("google_matrix_series_owner_mismatch")
                        child = children[0]
                        if child["google_event_id"] not in (None, item.get("id")):
                            raise GoogleStateError("google_matrix_series_owner_mismatch")
                        before_mark = {**root_slot, "google_event_id": item.get("id")}
                        if not (_source_owned(item, child) or _source_owned(item, before_mark)
                                or (child["delete_attempted"] and item.get("id") == child["google_event_id"] and item.get("status") == "cancelled")):
                            raise GoogleStateError("google_matrix_series_owner_mismatch")
                series["delete_attempted"] = True
                await _save(store, owner)
                status, _ = await _google_request("DELETE", path + "?sendUpdates=none", token)
                if status not in (204, 404, 410):
                    raise GoogleStateError("google_matrix_series_cleanup_failed")
            status, event = await _google_request("GET", path, token)
            if status not in (404, 410) and not (status == 200 and event.get("status") == "cancelled"):
                raise GoogleStateError("google_matrix_series_cleanup_failed")
            owner["stages"]["google_matrix_series_cleanup"] = 200
        clean = await _cleanup(env, store, owner, token)
        return {"ok": True, "dirty": False, "_clean_manifest": clean}
    else:
        raise GoogleStateError("google_sync_phase_invalid")
    owner["stage"] = "ready"
    await _save(store, owner)
    return {"ok": True, "dirty": True, "status": STATUSES[owner["step"]], "stage": "ready"}
