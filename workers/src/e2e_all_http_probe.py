"""通常fetchを共有KV・DOへ接続し、同期runnerを差し替えず検証する。"""

import json
import time
from urllib.parse import quote

import e2e_google_sync_probe as google
from e2e_all_sync_probe import AllEnv, _require
from e2e_google_sync_state import GoogleKV, MAX_BASELINE_DELETED
from google_apply_sync import _build_discord_description
from state import StateStore


class HttpEnv:
    def __init__(self, env, token, kv):
        self._settings = AllEnv(env, token)
        self.STATE_KV = kv
        self.SYNC_COORDINATOR = env.SYNC_COORDINATOR
        self.GOOGLE_APPLY_MAX_EVENTS_PER_RUN = str(MAX_BASELINE_DELETED + 2)

    def __getattr__(self, name):
        return getattr(self._settings, name)


async def _inputs(env, store, owner, token):
    """全件の所有確認だけを行う。通常同期へ渡す一覧の加工は行わない。"""
    fetched = await google.run_google_delta_fetch(
        google.GoogleEnv(env, token), StateStore(google._DeltaEnv(env)), commit_cursor=False,
    )
    _require(fetched.get("ok"), "http_input_fetch_failed")
    slots = {s["google_event_id"]: s for s in owner["fixtures"]}
    seen = set()
    for event in fetched.get("items", []):
        event_id = event.get("id", "")
        _require(event_id not in seen, "http_duplicate_input")
        seen.add(event_id)
        if event_id in slots:
            _require(google._source_owned(event, slots[event_id]), "http_google_owner_mismatch")
        else:
            _require(owner["baseline_deleted"].get(google.digest(event_id))
                     == google._deleted_fingerprint(event), "http_foreign_google")
    _require(set(slots) <= seen, "http_google_missing")
    status, events = await google.discord_request(
        env, owner["stages"], {}, "all_http_guild_read", "GET",
        f"/guilds/{quote(env.DISCORD_GUILD_ID, safe='')}/scheduled-events",
    )
    expected = {s["discord_event_id"]: s for s in slots.values() if s.get("discord_event_id")}
    _require(status == 200 and isinstance(events, list) and len(events) == len(expected)
             and {e.get("id") for e in events} == set(expected)
             and all(e.get("id") in expected and google._discord_owned(env, e, owner, expected[e["id"]])
                     for e in events), "http_foreign_discord")
    status, pages = await google.notion_request(
        env, owner["stages"], {}, "all_http_database_read", "POST",
        f"/databases/{quote(env.NOTION_EVENT_INTERNAL_ID, safe='')}/query", {"page_size": 100},
    )
    expected_pages = {s["notion_page_id"]: s for s in slots.values() if s.get("notion_page_id")}
    rows = pages.get("results", []) if isinstance(pages, dict) else []
    _require(status == 200 and pages.get("has_more") is False and len(rows) == len(expected_pages)
             and {p.get("id") for p in rows} == set(expected_pages)
             and all(p.get("id") in expected_pages and google._page_owned(env, p, expected_pages[p["id"]])
                     for p in rows), "http_foreign_notion")


async def verify_epoch(store, owner):
    _require(owner.get("http_epoch", 0) > 0 and await store.get_sync_last_epoch() == owner["http_epoch"],
             "http_epoch_mismatch")


async def dispatch(env, store, owner, token, invoke):
    if not owner.get("webhook_sync"):
        await _inputs(env, store, owner, token)
    kv = GoogleKV(store, owner)
    run_env = HttpEnv(env, token, kv)
    # 空状態で確認した削除履歴も通常適用へ渡す。実行は所有2件と履歴上限で有限にする。
    for slot in owner["fixtures"]:
        slot["apply_attempted"] = True
    await google._save(store, owner)
    before_epoch = await store.get_sync_last_epoch()
    started = time.time()
    if owner.get("webhook_sync"):
        from e2e_watch_shared_probe import dispatch_webhook
        result = await dispatch_webhook(env, store, owner, run_env, kv, invoke)
        response_status = 200
    else:
        response = await invoke(run_env, None, None, normal_http=True)
        result = json.loads(await response.text())
        response_status = response.status
    if owner.get("webhook_sync"):
        _require(result == {"ok": True, "mode": "native", "google_ok": True,
                            "google_apply_ok": True, "discord_notion_ok": True}, "webhook_dispatch_failed")
    else:
        _require(response_status == 200 and result.get("ok") is True
                 and result.get("source") == "manual"
                 and result.get("google", {}).get("ok") is True
                 and result.get("google_apply", {}).get("ok") is True
                 and result.get("discord_notion", {}).get("ok") is True
                 and not result.get("discord_notion", {}).get("skipped"), "http_dispatch_failed")
    _require(not (await google._lock_state(store)).get("owner"), "http_lock_not_released")
    owner["hashes"] = kv.hashes
    owner["http_epoch"] = await store.get_sync_last_epoch()
    _require(owner["http_epoch"] > before_epoch and owner["http_epoch"] >= started, "http_epoch_missing")
    for slot in owner["fixtures"]:
        await google._discover(env, store, owner, slot)
        _require(slot.get("notion_page_id") and slot.get("discord_event_id"), "http_forward_incomplete")
        if owner["step"] == 1:
            slot["discord_description"] = _build_discord_description(run_env, slot["source"]["description"], slot["google_event_id"])
        slot["source"]["description"] = slot["discord_description"]
        slot["page_description"] = slot["discord_description"]
    owner["stages"][f"all_http_dispatch_{owner['step']}"] = 200
    await google._save(store, owner)
