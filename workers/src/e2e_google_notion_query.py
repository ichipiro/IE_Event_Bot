"""Notion照会の実API拒否、共有queue再試行、重複検査を段階実行する。"""

import json
from urllib.parse import quote

import google_apply_sync
from e2e_google_sync_state import GoogleKV, GoogleStateError, KEYS
from e2e_google_sync_probe import (
    GoogleEnv, _save, _source_owned, _deleted_fingerprint, _page_owned,
    _discord_event_is_owned, _property_text, notion_request, discord_request,
)
from google_calendar_sync import run_google_delta_fetch


async def apply_phase(env, store, owner, token, invoke):
    kv = GoogleKV(store, owner)
    probe_env = GoogleEnv(env, token)
    failing = owner["step"] == 1
    probe_env.GOOGLE_APPLY_MAX_EVENTS_PER_RUN = "1" if failing else "3"
    before = {key: await kv.get(key) for key in KEYS}
    queue = json.loads(before[KEYS[3]] or "[]")
    rejected = 0

    async def query_fetch(url, options):
        nonlocal rejected
        body = json.loads(options["body"])
        if (rejected or url != f"https://api.notion.com/v1/databases/{env.NOTION_EVENT_INTERNAL_ID}/query"
                or options.get("method") != "POST"
                or body.get("filter", {}).get("rich_text", {}).get("equals") != queue[0]["id"]):
            raise GoogleStateError("notion_query_injection_mismatch")
        # DB・filter・認証を保持し、読取り要求の型だけを意図的に壊す。
        response = await google_apply_sync.fetch(url, {
            **options, "body": json.dumps({**body, "page_size": "invalid"}),
        })
        owner["stages"]["notion_query_api_rejection"] = int(response.status)
        await _save(store, owner)
        if int(response.status) != 400:
            raise GoogleStateError("notion_query_rejection_mismatch")
        data = json.loads(await response.text())
        if data.get("code") != "validation_error":
            raise GoogleStateError("notion_query_rejection_mismatch")
        rejected += 1
        owner["stages"]["notion_query_validation_error"] = 200
        await _save(store, owner)
        # 実応答を通常照会へ返し、既存の例外・queue保持処理を通す。
        return response

    if failing:
        setattr(probe_env, "_google_notion_query_fetch", query_fetch)

    async def fetcher(_env, state, *, commit_cursor):
        result = await run_google_delta_fetch(probe_env, state, commit_cursor=False)
        if not result.get("ok"):
            raise GoogleStateError("notion_query_google_fetch_failed")
        slots = {s["google_event_id"]: s for s in owner["fixtures"]}
        for event in result.get("items", []):
            slot = slots.get(event.get("id"))
            if slot and _source_owned(event, slot) and event.get("description") == slot["source"]["description"]:
                continue
            from e2e_google_sync_state import digest
            if (event.get("status") == "cancelled"
                    and owner["baseline_deleted"].get(digest(event["id"])) == _deleted_fingerprint(event)):
                continue
            raise GoogleStateError("google_sync_unowned_source")
        # 次HTTPの回復元は保存queueだけ。最後の段階は全所有入力を再適用する。
        items = [] if owner["step"] < 3 else [e for e in result["items"] if e["id"] in slots]
        if owner["step"] == 3 and {e["id"] for e in items} != set(slots):
            raise GoogleStateError("google_sync_source_not_visible")
        return {**result, "items": items, "events": len(items)}

    response = await invoke(probe_env, kv.state(), fetcher)
    payload = json.loads(await response.text())
    applied = payload.get("google_apply", {})
    after = {key: await kv.get(key) for key in KEYS}
    if (response.status != (500 if failing else 200) or payload.get("ok") is not (not failing)
            or applied.get("ok") is not (not failing)
            or applied.get("processed") != (1 if failing else 2 if owner["step"] == 2 else 3)
            or applied.get("pending_events") != (2 if failing else 0)):
        raise GoogleStateError("notion_query_dispatch_mismatch")
    if failing:
        if (rejected != 1 or applied.get("error_count") != 1
                or applied.get("errors") != [f"exception:{queue[0]['id']}:RuntimeError"]
                or any(before[k] != after[k] for k in KEYS[:-1])):
            raise GoogleStateError("notion_query_failure_state_mismatch")
        owner["stages"]["notion_query_failed_dispatch"] = 500
        owner["stages"]["notion_query_cursor_preserved"] = 200
    else:
        if after[KEYS[3]] != "[]" or applied.get("error_count") != 0:
            raise GoogleStateError("notion_query_retry_mismatch")
        owner["expected_cursor"] = payload["google"]["next_updated_min"]
        owner["pending_ids"] = []
        owner["stages"]["notion_query_queue_only_retry" if owner["step"] == 2 else "notion_query_reapply"] = 200
    for slot in owner["fixtures"]:
        for key, field in ((KEYS[1], "notion_page_id"), (KEYS[2], "discord_event_id")):
            mapping = json.loads(after[key] or "{}")
            value = (mapping.get("internal", {}) if key == KEYS[1] else mapping).get(slot["google_event_id"])
            if value:
                if slot.get(field) not in (None, value):
                    raise GoogleStateError("google_sync_reference_mismatch")
                slot[field] = value
    owner["hashes"] = kv.hashes
    owner["stage"] = "ready"
    await _save(store, owner)


async def verify_phase(env, store, owner):
    """別HTTPの一覧取得で、未作成・正確な件数・同じID・書戻しを確認する。"""
    status, data = await notion_request(env, owner["stages"], {}, "notion_query_pages", "POST",
        f"/databases/{quote(env.NOTION_EVENT_INTERNAL_ID, safe='')}/query", {"page_size": 100})
    if status != 200 or data.get("has_more") is not False or not isinstance(data.get("results"), list):
        raise GoogleStateError("google_sync_not_ready")
    pages = data["results"]
    status, events = await discord_request(env, owner["stages"], {}, "notion_query_events", "GET",
        f"/guilds/{quote(env.DISCORD_GUILD_ID, safe='')}/scheduled-events")
    slots = [s for s in owner["fixtures"] if s.get("notion_page_id")]
    expected = 1 if owner["step"] <= 1 else 3
    if status != 200 or not isinstance(events, list) or len(pages) != expected or len(events) != expected or len(slots) != expected:
        raise GoogleStateError("google_sync_not_ready")
    if ({p.get("id") for p in pages} != {s["notion_page_id"] for s in slots}
            or {e.get("id") for e in events} != {s["discord_event_id"] for s in slots}):
        raise GoogleStateError("notion_query_duplicate_or_foreign")
    for slot in slots:
        page = next(p for p in pages if p["id"] == slot["notion_page_id"])
        event = next(e for e in events if e["id"] == slot["discord_event_id"])
        if (not _page_owned(env, page, slot)
                or _property_text(page, "メッセージID", "rich_text") != slot["discord_event_id"]
                or not _discord_event_is_owned(event, event_id=slot["discord_event_id"], guild_id=env.DISCORD_GUILD_ID, run_id=slot["run_id"])):
            raise GoogleStateError("notion_query_reference_mismatch")
    owner["stages"][f"notion_query_step_{owner['step']}"] = 200
    owner["stages"][f"notion_query_unique_{owner['step']}"] = 200


async def verify_removed(env, owner, slot, token):
    from e2e_google_boundary_probe import verify_removed as verify
    await verify(env, owner, slot, token)
