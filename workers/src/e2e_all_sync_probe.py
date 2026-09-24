"""所有2件で通常のGoogle→Discord差分処理を連続実行し、往復と回復を検証する。"""

import asyncio
import json
from urllib.parse import quote
from uuid import uuid4

import discord_notion_sync as discord_sync
import e2e_google_sync_probe as google
from discord_retry_state import split_snapshot
from e2e_discord_batch_state import slot_run_id
from e2e_google_sync_state import (
    ALL_KEYS, ALL_STEPS, GoogleKV, GoogleStateError, KIND, final_step, source_id,
)
from google_apply_sync import _build_discord_description, _notion_update_event
from google_calendar_sync import run_google_delta_fetch
from state import StateStore


class AllEnv(google.GoogleEnv):
    SYNC_ALL_INCLUDE_DISCORD_NOTION = "true"
    DISCORD_TO_GOOGLE_SYNC_ENABLED = "true"
    GOOGLE_APPLY_MAX_EVENTS_PER_RUN = "2"
    DISCORD_NOTION_MAX_CHANGES_PER_RUN = "2"
    KV_SYNC_COOLDOWN_ENABLED: str = "false"
    SYNC_INTERVAL_SECONDS = "300"


def _require(condition, code):
    if not condition:
        raise GoogleStateError("all_sync_" + code)


async def _read(env, owner, token):
    """ID・marker・本文を比較し、再探索でIDが変わった資源を採用しない。"""
    for slot in owner["fixtures"]:
        status, event = await google._google_request(
            "GET", google._event_item_url(env.GOOGLE_CALENDAR_ID, slot["google_event_id"]), token,
        )
        _require(status == 200 and google._source_owned(event, slot)
                 and event.get("description") == slot["source"]["description"], "google_content_mismatch")
        if owner["step"] == 0:
            continue
        status, event = await google.discord_request(
            env, owner["stages"], {}, "all_sync_discord_read", "GET",
            google._discord_path(env, slot["discord_event_id"]),
        )
        _require(status == 200 and google._discord_owned(env, event, owner, slot)
                 and event.get("description") == slot["discord_description"], "discord_content_mismatch")
        status, page = await google.notion_request(
            env, owner["stages"], {}, "all_sync_page_read", "GET",
            f"/pages/{quote(slot['notion_page_id'], safe='')}",
        )
        _require(status == 200 and google._page_owned(env, page, slot)
                 and not page.get("archived")
                 and google._property_text(page, "メッセージID", "rich_text") == slot["discord_event_id"]
                 and google._property_text(page, "内容", "rich_text") == slot["page_description"],
                 "page_content_mismatch")


async def _verify(env, store, owner, token):
    kv = GoogleKV(store, owner)
    values = {key: await kv.get(key) for key in ALL_KEYS}
    await _read(env, owner, token)
    if owner["step"]:
        slots = owner["fixtures"]
        expected_google = [slots[0]["google_event_id"]] if owner["step"] == 4 else []
        expected_discord = [slots[0]["discord_event_id"]] if owner["step"] == 6 else []
        queue = json.loads(values["sync:google_apply_queue"] or "[]")
        pending = json.loads(values["sync:discord_notion_queue"] or "[]")
        snapshot, snapshot_pending = split_snapshot(json.loads(values["discord:snapshot"] or "{}"))
        _require([e["id"] for e in queue] == expected_google
                 and [e["id"] for e in pending] == expected_discord
                 and [e["id"] for e in snapshot_pending] == expected_discord
                 and set(snapshot) == {s["discord_event_id"] for s in slots}, "queue_mismatch")
        _require(json.loads(values["map:gcal_discord"] or "{}") == {
            s["google_event_id"]: s["discord_event_id"] for s in slots
        } and json.loads(values["map:gcal_notion"] or "{}").get("internal") == {
            s["google_event_id"]: s["notion_page_id"] for s in slots
        }, "mapping_mismatch")
        result = json.loads(values["result:sync_all"] or "{}").get("payload", {})
        _require(result == {
            "ok": owner["step"] not in (4, 6), "mode": "native", "google_ok": True,
            "google_apply_ok": owner["step"] != 4, "discord_notion_ok": owner["step"] != 6,
        }, "result_mismatch")
    owner["stages"][f"all_sync_step_{owner['step']}"] = 200


async def _change(env, store, owner, token):
    slot, step = owner["fixtures"][0], owner["step"]
    if step in (2, 6):
        slot["discord_description"] += f"\nE2E all sync Discord {step}"
        await google._save(store, owner)
        status, event = await google.discord_request(
            env, owner["stages"], {}, "all_sync_discord_change", "PATCH",
            google._discord_path(env, slot["discord_event_id"]),
            {"description": slot["discord_description"]},
        )
        _require(status == 200 and google._discord_owned(env, event, owner, slot), "change_failed")
    if step == 4:
        slot["source"]["description"] += "\nE2E all sync Google failure"
        await google._save(store, owner)
        status, event = await google._google_request(
            "PATCH", google._event_item_url(env.GOOGLE_CALENDAR_ID, slot["google_event_id"]) + "?sendUpdates=none",
            token, {"description": slot["source"]["description"]},
        )
        _require(status == 200 and google._source_owned(event, slot), "change_failed")


async def _dispatch(env, store, owner, token, invoke):
    kv, probe_env = GoogleKV(store, owner), AllEnv(env, token)
    state = kv.state()
    before_cursor = await state.get_text("sync:updated_min")
    before_epoch = await state.get_text("sync:last_epoch")
    step = owner["step"]
    injected, reverse_applied = [], []

    async def fetcher(_env, state, *, commit_cursor):
        result = await run_google_delta_fetch(probe_env, state, commit_cursor=False)
        _require(result.get("ok"), "fetch_failed")
        slots = {s["google_event_id"]: s for s in owner["fixtures"]}
        selected = []
        for event in result.get("items", []):
            slot = slots.get(event.get("id"))
            if slot is None:
                _require(owner["baseline_deleted"].get(google.digest(event.get("id", "")))
                         == google._deleted_fingerprint(event), "foreign_google_input")
                continue
            _require(google._source_owned(event, slot) and event["id"] not in {e["id"] for e in selected},
                     "google_input_mismatch")
            selected.append(event)
        # 取得後の適用は通常処理のqueue優先順序を維持する。
        return {**result, "items": selected}

    async def notion_updater(call_env, page_id, **kwargs):
        if step == 4 and page_id == owner["fixtures"][0].get("notion_page_id"):
            injected.append("google")
            return False
        return await _notion_update_event(call_env, page_id, **kwargs)

    async def select(events):
        expected = {s["discord_event_id"]: s for s in owner["fixtures"]}
        _require(len(events) == len(expected) and {e.get("id") for e in events} == set(expected),
                 "discord_input_mismatch")
        for event in events:
            _require(google._discord_owned(env, event, owner, expected[event["id"]]), "foreign_discord_input")
        return events

    async def upsert(call_env, event, google_token):
        slot = next(s for s in owner["fixtures"] if s["discord_event_id"] == event["id"])
        if step == 6 and slot is owner["fixtures"][0]:
            injected.append("discord")
            return False
        # Notion照会失敗を新規資源作成へ切り替えず、所有済みページだけを更新する。
        ok = await discord_sync._sync_discord_event_upsert(
            call_env, event, google_token, expected_internal_page_id=slot["notion_page_id"],
        )
        if ok:
            reverse_applied.append(slot["google_event_id"])
        return ok

    async def no_delete(*args):
        raise GoogleStateError("all_sync_unexpected_delete")

    async def reverse(_env, state):
        # Google側で作成されたIDをDOへ記録してから逆方向へ渡す。
        for slot in owner["fixtures"]:
            await google._discover(env, store, owner, slot)
            _require(slot.get("notion_page_id") and slot.get("discord_event_id"), "forward_incomplete")
        return await discord_sync.run_discord_notion_poll_sync(
            probe_env, state, event_selector=select, upsert_runner=upsert, delete_runner=no_delete,
        )

    for slot in owner["fixtures"]:
        slot["apply_attempted"] = True
    await google._save(store, owner)
    response = await invoke(probe_env, state, fetcher, notion_updater=notion_updater, discord_runner=reverse,
                            source=("manual", "webhook", "cron")[(step - 1) % 3])
    result = json.loads(await response.text())
    expected_failure = step in (4, 6)
    _require(response.status == (500 if expected_failure else 200)
             and result.get("ok") is not expected_failure
             and result.get("google", {}).get("ok") is True
             and result.get("google_apply", {}).get("ok") is (step != 4)
             and result.get("discord_notion", {}).get("ok") is (step != 6), "dispatch_failed")
    _require(injected == (["google"] if step == 4 else ["discord"] if step == 6 else []), "injection_missing")
    if expected_failure:
        _require(await state.get_text("sync:last_epoch") == before_epoch, "last_success_changed")
        if step == 4:
            _require(await state.get_text("sync:updated_min") == before_cursor, "cursor_changed")
        owner["stages"][f"all_sync_failure_{step}"] = 200
    else:
        _require(await state.get_text("sync:last_epoch") is not None, "last_success_missing")
    for slot in owner["fixtures"]:
        if step == 1:
            slot["discord_description"] = _build_discord_description(probe_env, slot["source"]["description"], slot["google_event_id"])
        if slot["google_event_id"] in reverse_applied:
            slot["source"]["description"] = slot["discord_description"]
            slot["page_description"] = slot["discord_description"]
        elif step == 5:
            slot["page_description"] = slot["source"]["description"]
    if step in (1, 2, 7):
        _require(owner["fixtures"][0]["google_event_id"] in reverse_applied, "reverse_missing")
    owner["hashes"] = kv.hashes
    await google._save(store, owner)


async def _controls(env, store, owner, token, invoke):
    kv, probe_env = GoogleKV(store, owner), AllEnv(env, token)
    state = kv.state()
    original = dict(kv.hashes)

    async def forbidden(*args, **kwargs):
        raise GoogleStateError("all_sync_rejected_body_called")

    # 実時間を待たず、所有KVの最終成功時刻を現在へ設定して通常判定を通す。
    await state.set_sync_last_epoch_now()
    owner["hashes"] = kv.hashes
    await google._save(store, owner)
    unchanged = dict(kv.hashes)
    probe_env.KV_SYNC_COOLDOWN_ENABLED = "true"
    for source in ("manual", "webhook", "cron"):
        response = await invoke(probe_env, state, forbidden, discord_runner=forbidden, source=source)
        result = json.loads(await response.text())
        _require(response.status == 200 and result.get("status") == "cooldown_skip"
                 and kv.hashes == unchanged, "cooldown_failed")
    owner["stages"]["all_sync_cooldown"] = 200
    probe_env.KV_SYNC_COOLDOWN_ENABLED = "false"
    entered, proceed = asyncio.Event(), asyncio.Event()

    async def holding(*args, **kwargs):
        entered.set()
        await proceed.wait()
        raise GoogleStateError("all_sync_expected_holder_failure")

    for source in ("manual", "webhook", "cron"):
        task = asyncio.create_task(invoke(probe_env, state, holding, discord_runner=forbidden, source=source))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            lock = await google._lock_state(store)
            _require(bool(lock.get("owner")), "lock_missing")
            for contender in ("manual", "webhook", "cron"):
                response = await invoke(probe_env, state, forbidden, discord_runner=forbidden, source=contender)
                result = json.loads(await response.text())
                _require(response.status == 200 and result.get("status") == "in_progress_skip"
                         and (await google._lock_state(store)).get("owner") == lock["owner"]
                         and kv.hashes == unchanged, "contention_failed")
            proceed.set()
            try:
                await task
            except GoogleStateError as exc:
                _require(str(exc) == "all_sync_expected_holder_failure", "holder_failed")
            else:
                raise GoogleStateError("all_sync_holder_did_not_fail")
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        _require(not (await google._lock_state(store)).get("owner"), "lock_not_released")
        entered.clear()
        proceed.clear()
    _require(all(kv.hashes.get(k) == v for k, v in original.items() if k != "sync:last_epoch"), "control_state_changed")
    owner["stages"]["all_sync_contention"] = 200


async def run_phase(env, store, run_id, phase, invoke, owner):
    _require(not (await google._lock_state(store)).get("owner"), "busy")
    if owner and owner.get("dirty"):
        _require(owner["run_id"] == run_id and owner["target_fingerprints"] == google._targets(env), "owner_mismatch")
        _require(phase != "prepare_all", "already_prepared")
    else:
        _require(phase == "prepare_all" and (not owner or owner.get("last_run_id") != run_id), "phase_invalid")
    token = await google.get_google_access_token(env, StateStore(google._DeltaEnv(env)))
    if not token:
        raise GoogleStateError("all_sync_token_required")
    if phase == "prepare_all":
        stages = {}
        _require(not (await google._verify_calendar(env.GOOGLE_CALENDAR_ID, token, stages)
                      or await google._verify_guild(env, env.DISCORD_GUILD_ID, stages, {})
                      or await google._verify_database(env, env.NOTION_EVENT_INTERNAL_ID, google._EVENT_SCHEMA,
                                                      stages, {}, "all_sync_database", "notion_event")), "target_mismatch")
        baseline = await google._check_full_empty(env, token, stages)
        slots = [{"run_id": slot_run_id(run_id, i), "google_event_id": source_id(run_id, i),
                  "source": google._event_payload(slot_run_id(run_id, i), source_id(run_id, i)),
                  "source_attempted": False, "apply_attempted": False} for i in range(2)]
        owner = {"kind": KIND, "version": 1, "dirty": True, "run_id": run_id, "scope_id": uuid4().hex,
                 "target_fingerprints": google._targets(env), "step": 0, "stage": "working",
                 "hashes": {}, "fixtures": slots, "stages": stages, "all_sync": True,
                 "baseline_deleted": baseline}
        await google._save(store, owner)
        for slot in slots:
            status, _ = await google._google_request("GET", google._event_item_url(env.GOOGLE_CALENDAR_ID, slot["google_event_id"]), token)
            _require(status == 404, "source_collision")
            slot["source_attempted"] = True
            await google._save(store, owner)
            status, event = await google._google_request("POST", google._event_collection_url(env.GOOGLE_CALENDAR_ID) + "?sendUpdates=none", token, slot["source"])
            _require(status == 200 and google._source_owned(event, slot), "seed_failed")
    elif phase == "verify":
        _require(owner["stage"] in ("ready", "verified"), "phase_invalid")
        if owner["stage"] == "verified":
            owner["stage"] = "ready"
            await google._save(store, owner)
        await _verify(env, store, owner, token)
        owner["stage"] = "verified"
        await google._save(store, owner)
    elif phase == "advance":
        _require(owner["stage"] == "verified" and owner["step"] < final_step(owner), "phase_invalid")
        await _verify(env, store, owner, token)
        owner["step"] += 1
        owner["stage"] = "working"
        await google._save(store, owner)
        await _change(env, store, owner, token)
        if owner["step"] == final_step(owner):
            await _controls(env, store, owner, token, invoke)
        else:
            await _dispatch(env, store, owner, token, invoke)
    elif phase == "cleanup":
        owner["passed"] = owner.get("passed", False) if owner["stage"] == "cleanup" else (
            owner["stage"] == "verified" and owner["step"] == final_step(owner)
        )
        owner["stage"] = "cleanup"
        await google._save(store, owner)
        return {"ok": True, "dirty": False, "_clean_manifest": await google._cleanup(env, store, owner, token)}
    else:
        raise GoogleStateError("all_sync_phase_invalid")
    if phase != "verify":
        owner["stage"] = "ready"
        await google._save(store, owner)
    return {"ok": True, "dirty": True, "status": ALL_STEPS[owner["step"]],
            "stage": f"google_{ALL_STEPS[owner['step']]}_verified" if phase == "verify" else "ready"}
