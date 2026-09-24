"""専用Guildの全件取得・通常リマインドHTTP・共有KVを段階検証する。"""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import e2e_reminder_probe as reminder


SERVICE = reminder.REMINDER_MANIFEST_SERVICE
KEYS = ("reminder_cache", "result:job_reminder")
PHASES = ("prepare", "notify", "duplicate")


def _require(condition, code):
    if not condition:
        raise RuntimeError(code)


def _targets(env):
    return reminder._target_fingerprints(env.DISCORD_GUILD_ID, env.REMINDER_CHANNEL_ID, env.REMINDER_ROLE_ID)


async def _save(store, owner):
    await store.put_e2e_manifest(SERVICE, owner)


async def _events(env, owner):
    status, rows = await reminder._discord_request_stage(
        env, owner["stages"], {}, "reminder_normal_list", "GET",
        f"/guilds/{env.DISCORD_GUILD_ID}/scheduled-events?with_user_count=false",
    )
    _require(status == 200 and isinstance(rows, list), "reminder_normal_list_failed")
    return rows


def _owned(env, owner, event):
    return reminder._event_has_run(event, event_id=str(event.get("id", "")),
                                   guild_id=env.DISCORD_GUILD_ID, run_id=owner["run_id"])


def _matches(env, owner, event, payload):
    return (_owned(env, owner, event) and event.get("status") == 1
            and all(event.get(key) == payload[key] for key in ("name", "description", "entity_metadata", "entity_type", "privacy_level"))
            and all(reminder._parse_rfc3339(event.get(key)) == reminder._parse_rfc3339(payload[key])
                    for key in ("scheduled_start_time", "scheduled_end_time")))


async def _inputs(env, owner):
    rows = await _events(env, owner)
    _require(len(rows) == 4 and {e.get("id") for e in rows} == set(owner["events"])
             and all(_matches(env, owner, e, owner["events"][e["id"]]) for e in rows),
             "reminder_normal_foreign_input")
    return rows


async def _messages(env, owner):
    status, rows = await reminder._discord_request_stage(
        env, owner["stages"], {}, "reminder_normal_messages", "GET",
        f"/channels/{env.REMINDER_CHANNEL_ID}/messages?limit=100",
    )
    _require(status == 200 and isinstance(rows, list) and len(rows) < 100,
             "reminder_normal_messages_unbounded")
    return [m for m in rows if reminder._run_marker(owner["run_id"]) in str(m.get("content", ""))]


class _OwnedKV:
    """通常の固定キーを実KVへ通し、書込みdigestを先にDOへ記録する。"""

    def __init__(self, store, owner):
        self.store, self.owner = store, owner

    async def get(self, key):
        _require(key in KEYS, "reminder_normal_key_forbidden")
        current = await self.store.get_e2e_manifest(SERVICE)
        _require(current and current.get("dirty") is True
                 and current.get("run_id") == self.owner["run_id"], "reminder_normal_owner_mismatch")
        value = await self.store.get_text(key)
        _require((reminder._fingerprint(value) if value is not None else None)
                 == self.owner["hashes"].get(key), "reminder_normal_kv_not_ready")
        return value

    async def put(self, key, value):
        await self.get(key)
        data = json.loads(value)
        if key == "reminder_cache":
            _require(isinstance(data, dict) and set(data) == set(self.owner["eligible"])
                     and all(reminder._parse_rfc3339(v) is not None for v in data.values()),
                     "reminder_normal_cache_invalid")
            self.owner["cache"] = data
        digest = reminder._fingerprint(value)
        self.owner["writes"].setdefault(key, []).append(digest)
        await _save(self.store, self.owner)
        await self.store.env.STATE_KV.put(key, value)
        self.owner["hashes"][key] = digest
        await _save(self.store, self.owner)


class _JobEnv:
    def __init__(self, env, kv):
        self._env = env
        self.STATE_KV = kv
        self.KV_RESULT_MIN_WRITE_SECONDS = "0"

    def __getattr__(self, key):
        return getattr(self._env, key)


def _content(env, event):
    start = reminder._parse_rfc3339(event["scheduled_start_time"])
    return (f"<@&{env.REMINDER_ROLE_ID}>\n🔔 明日開催のイベントがあります\n"
            f"イベント名: {event['name']}\n"
            f"開始日時: {reminder._format_japanese_datetime(start)}\n"
            f"場所: {event['entity_metadata']['location']}\n"
            f"https://discord.com/events/{env.DISCORD_GUILD_ID}/{event['id']}")


async def _verify(env, store, owner):
    for key in KEYS:
        await _OwnedKV(store, owner).get(key)
    events = await _inputs(env, owner)
    completed = owner["completed"]
    if completed in ("notify", "duplicate"):
        cache = await store.get_json("reminder_cache", {})
        _require(cache == owner.get("cache") and set(cache) == set(owner["eligible"]),
                 "reminder_normal_cache_not_ready")
        result = await store.get_last_result("job_reminder")
        detail = (result or {}).get("payload", {})
        _require(detail.get("ok") is True and detail.get("mode") == "native"
                 and detail.get("failed_count") == 0, "reminder_normal_result_failed")
        _require(len(owner["writes"].get("reminder_cache", [])) == 1,
                 "reminder_normal_cache_rewritten")
    messages = await _messages(env, owner)
    expected_count = 0 if completed == "prepare" else 2
    _require(len(messages) == expected_count, "reminder_normal_message_count_failed")
    if expected_count:
        expected = {_content(env, e) for e in events if e["id"] in owner["eligible"]}
        _require({m.get("content") for m in messages} == expected
                 and all(str(m.get("channel_id")) == env.REMINDER_CHANNEL_ID
                         and m.get("mention_everyone") is False
                         and m.get("mention_roles") == [env.REMINDER_ROLE_ID] for m in messages),
                 "reminder_normal_message_content_failed")
        ids = sorted(m["id"] for m in messages)
        if owner.get("messages"):
            _require(ids == owner["messages"], "reminder_normal_duplicate_messages")
        owner["messages"] = ids
    owner["stages"][f"reminder_normal_verify_{completed}"] = 200
    await _save(store, owner)


async def _prepare(env, store, run_id):
    previous = await store.get_e2e_manifest(SERVICE)
    _require(not previous or not previous.get("dirty"), "environment_dirty")
    _require(not reminder._configuration_error(env) and reminder._reminder_window_minutes(env) == 15,
             "reminder_normal_configuration_invalid")
    owner = {
        "version": 1, "kind": "day_before_reminder", "normal": True,
        "run_id": run_id, "dirty": True, "target_fingerprints": _targets(env),
        "events": {}, "eligible": [], "messages": [], "attempted": [], "hashes": {}, "writes": {},
        "stages": {}, "stage": "preparing", "completed": "",
    }
    _require(not await reminder._verify_targets(env, owner["stages"], {}), "reminder_normal_targets_failed")
    _require(not await _events(env, owner), "reminder_normal_guild_not_empty")
    _require(not await _messages(env, owner), "reminder_normal_message_collision")
    for key in KEYS:
        _require(await store.get_text(key) is None, "reminder_normal_shared_state_not_empty")
    await _save(store, owner)
    now = datetime.now(timezone.utc)
    # 実時刻を使う。通知・重複確認が遅れたら成功扱いにせず期限超過として止める。
    owner["deadline"] = (now + timedelta(minutes=6)).isoformat()
    for index, minutes in enumerate((8, 10, -60, 60)):
        payload = reminder._event_payload(run_id, now, 15)
        payload["name"] += f" {index}"
        start = now + timedelta(hours=24, minutes=minutes)
        payload["scheduled_start_time"] = reminder._discord_iso(start)
        payload["scheduled_end_time"] = reminder._discord_iso(start + timedelta(minutes=30))
        owner["attempted"].append(payload)
        await _save(store, owner)
        status, event = await reminder._discord_request_stage(
            env, owner["stages"], {}, f"reminder_normal_create_{index}", "POST",
            f"/guilds/{env.DISCORD_GUILD_ID}/scheduled-events", payload,
        )
        _require(status == 200 and isinstance(event, dict) and _matches(env, owner, event, payload),
                 "reminder_normal_create_failed")
        owner["events"][event["id"]] = payload
        if index < 2:
            owner["eligible"].append(event["id"])
        await _save(store, owner)
    owner["completed"] = "prepare"
    owner["stage"] = "prepared"
    owner["stages"]["reminder_normal_prepare"] = 200
    await _save(store, owner)
    return owner


async def _job(env, store, owner, phase, request):
    from entry import Application

    await _inputs(env, owner)
    for key in KEYS:
        await _OwnedKV(store, owner).get(key)
    _require(datetime.now(timezone.utc) < datetime.fromisoformat(owner["deadline"]),
             "reminder_normal_deadline_exceeded")
    owner["stage"] = "working"
    await _save(store, owner)
    job_request = SimpleNamespace(url="https://e2e.invalid/jobs/reminder", method="POST", headers=request.headers)
    response = await Application(_JobEnv(env, _OwnedKV(store, owner))).fetch(job_request)
    detail = json.loads(await response.text())
    error = str(detail.get("error") or "reminder_normal_job_failed")
    _require(response.status == 200 and detail.get("ok") is True
             and detail.get("failed_count") == 0, error)
    _require(detail.get("listed_count") == 4, "reminder_normal_list_count_failed")
    _require(set(owner.get("cache", {})) == set(owner["eligible"]), "reminder_normal_cache_missing")
    owner["stages"][f"reminder_normal_{phase}"] = 200


async def cleanup(env, store, run_id):
    owner = await store.get_e2e_manifest(SERVICE)
    _require(owner and owner.get("normal") and owner.get("run_id", owner.get("last_run_id")) == run_id,
             "cleanup_run_id_mismatch")
    if not owner.get("dirty"):
        return {"ok": True, "dirty": False, "run_id": run_id, "stages": owner["stages"]}
    _require(owner.get("target_fingerprints") == _targets(env), "dirty_manifest_target_mismatch")
    owner["stage"] = "cleanup"
    await _save(store, owner)
    # 作成応答を失った資源も、保存済みpayloadとrun markerの両方で再発見する。
    for event in await _events(env, owner):
        if _owned(env, owner, event):
            payload = next((p for p in owner["attempted"] if _matches(env, owner, event, p)), None)
            _require(payload is not None, "reminder_normal_event_ownership_unresolved")
            owner["events"][event["id"]] = payload
    _require(all(p in owner["events"].values() for p in owner["attempted"]),
             "reminder_normal_event_ownership_unresolved")
    await _save(store, owner)
    for event_id in owner["events"]:
        ok, _, _, _ = await reminder._delete_event(env, env.DISCORD_GUILD_ID, event_id, run_id, owner["stages"], {})
        _require(ok, "reminder_normal_delete_event_failed")
    messages = await _messages(env, owner)
    for message in messages:
        ok, _, _, _ = await reminder._delete_message(env, env.REMINDER_CHANNEL_ID, message["id"], run_id, owner["stages"], {})
        _require(ok, "reminder_normal_delete_message_failed")
    _require(not any(_owned(env, owner, e) for e in await _events(env, owner)), "reminder_normal_events_remaining")
    _require(not await _messages(env, owner), "reminder_normal_messages_remaining")
    for key in KEYS:
        value = await store.get_text(key)
        if value is not None:
            _require(reminder._fingerprint(value) in owner["writes"].get(key, []), "reminder_normal_foreign_kv")
            await env.STATE_KV.delete(key)
        _require(await store.get_text(key) is None, "reminder_normal_cleanup_not_ready")
    owner["stages"]["reminder_normal_cleanup"] = 200
    clean = {
        "version": 1, "kind": "day_before_reminder", "normal": True, "dirty": False,
        "last_run_id": run_id, "outcome": "passed" if owner["completed"] == "duplicate"
        and owner["stages"].get("reminder_normal_verify_duplicate") == 200 else "failed_clean",
        "stages": owner["stages"], "resource_fingerprints": _targets(env),
    }
    await _save(store, clean)
    return {"ok": True, "dirty": False, "run_id": run_id, "stages": clean["stages"]}


async def run(env, store, run_id, phase, request):
    if phase == "prepare":
        owner = await _prepare(env, store, run_id)
    else:
        owner = await store.get_e2e_manifest(SERVICE)
        _require(owner and owner.get("normal") and owner.get("dirty") is True
                 and owner.get("run_id") == run_id and owner.get("target_fingerprints") == _targets(env),
                 "reminder_normal_owner_mismatch")
        if phase == "verify":
            await _verify(env, store, owner)
        else:
            _require(phase in PHASES and owner.get("completed") == PHASES[PHASES.index(phase) - 1]
                     and owner.get("stage") != "working", "reminder_normal_phase_mismatch")
            _require(owner["stages"].get(f"reminder_normal_verify_{owner['completed']}") == 200,
                     "reminder_normal_previous_unverified")
            await _job(env, store, owner, phase, request)
            owner["completed"] = phase
            owner["stage"] = "ready"
            await _save(store, owner)
    return {"ok": True, "dirty": True, "run_id": run_id, "stages": owner["stages"]}
