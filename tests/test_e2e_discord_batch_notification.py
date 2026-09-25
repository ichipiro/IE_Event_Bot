"""通知の所有権・別HTTP再試行・部分回収を代替APIで検証する。"""

import json
from copy import deepcopy
from urllib.parse import urlparse

import pytest
from workers import Response

import discord_notion_sync
import e2e_discord_probe
from e2e_discord_batch_state import NOTIFICATION_SERVICE, BatchDiscordKV, key_prefix
from e2e_discord_kv_state import KEYS
from e2e_entry import Default
from state import StateStore
from tests.fakes import Request
from tests.test_e2e_discord_batch_probe import env_for_batch
from tests.test_e2e_discord_notion_probe import GUILD_ID, RUN_ID, install_api_stub, run

CHANNEL = "notification-channel"
ROLE = "notification-role"


def environment():
    env = env_for_batch()
    env.E2E_DISCORD_BATCH_NOTIFICATION_ENABLED = "true"
    env.EVENT_CREATE_CHANNEL_ID = CHANNEL
    env.EVENT_CREATE_ROLE_ID = ROLE
    return env


def owner(env):
    value = run(StateStore(env).get_e2e_manifest(NOTIFICATION_SERVICE))
    assert isinstance(value, dict)
    return value


def call(env, phase="", *, token="test-token", version=RUN_ID, method="POST"):
    worker = Default()
    worker.env = env
    response = run(
        worker.fetch(
            Request(
                "https://bot.test/admin/e2e/discord-batch-notification" + phase,
                method=method,
                headers={
                    "Authorization": "Bearer " + token,
                    "X-E2E-Run-ID": RUN_ID,
                    "X-E2E-Version-Tag": version,
                },
            )
        )
    )
    raw = run(response.text())
    return response.status, json.loads(raw) if raw.startswith("{") else {"error": raw}


def install(monkeypatch, env):
    events, pages, calls, control = install_api_stub(monkeypatch, multiple=True)
    normal_fetch = discord_notion_sync.fetch
    messages = {}
    control.update(
        post_lost=False,
        post_fail=False,
        delete_fail=False,
        search_fail=False,
        reaction_fail=False,
        wrong_channel=False,
    )

    async def fetch(url, options=None):
        request = options or {}
        method = request.get("method", "GET")
        parsed = urlparse(url)
        path = parsed.path.removeprefix("/api/v10")
        if parsed.netloc != "discord.com" or not (
            path.startswith("/channels/") or path.endswith("/roles")
        ):
            return await normal_fetch(url, options)
        calls.append((method, path))
        if path == f"/guilds/{GUILD_ID}/roles":
            return Response(json.dumps([{"id": ROLE, "mentionable": True}]), status=200)
        if path == f"/channels/{CHANNEL}":
            return Response(
                json.dumps(
                    {
                        "id": CHANNEL,
                        "guild_id": "foreign" if control["wrong_channel"] else GUILD_ID,
                        "type": 0,
                    }
                ),
                status=200,
            )
        collection = f"/channels/{CHANNEL}/messages"
        if path == collection and method == "POST":
            manifest = await StateStore(env).get_e2e_manifest(NOTIFICATION_SERVICE)
            assert isinstance(manifest, dict)
            payload = json.loads(request["body"])
            slot = next(
                s for s in manifest["fixtures"] if s["run_id"] in payload["content"]
            )
            assert slot["create_attempted"]["message"] is True
            assert slot["message_content_sha256"]
            assert not slot.get("message_id")
            assert payload["allowed_mentions"] == {
                "parse": [],
                "roles": [ROLE],
                "replied_user": False,
            }
            if control["post_fail"]:
                return Response("{}", status=403)
            message_id = f"message-{len(messages) + 1}"
            messages[message_id] = {
                "id": message_id,
                "channel_id": CHANNEL,
                "content": payload["content"],
                "mention_roles": [ROLE],
                "mention_everyone": False,
                "reactions": [],
            }
            if control["post_lost"]:
                return Response("{}", status=500)
            return Response(json.dumps(messages[message_id]), status=200)
        if path == collection and method == "GET":
            if control["search_fail"]:
                return Response("{}", status=503)
            return Response(json.dumps(list(messages.values())), status=200)
        if path.startswith(collection + "/"):
            message_id = path[len(collection) + 1 :].split("/")[0]
            message = messages.get(message_id)
            if message is None:
                return Response("{}", status=404)
            if "/reactions/" in path and method == "PUT":
                if control["reaction_fail"]:
                    return Response("{}", status=403)
                message["reactions"] = [{"emoji": {"name": "✅"}, "me": True}]
                return Response("", status=204)
            if method == "GET":
                return Response(json.dumps(message), status=200)
            if method == "DELETE":
                if control["delete_fail"]:
                    return Response("{}", status=503)
                del messages[message_id]
                return Response("", status=204)
        raise AssertionError(f"unexpected message request: {method} {path}")

    monkeypatch.setattr(discord_notion_sync, "fetch", fetch)
    monkeypatch.setattr(e2e_discord_probe, "fetch", fetch)
    return events, pages, messages, calls, control


def complete(env):
    for phase in ("", "/verify", "/advance", "/verify", "/advance", "/verify"):
        status, result = call(env, phase)
        assert status == 200, result


def test_notify_retry_uses_same_message_then_delivers_deferred_event(monkeypatch):
    env = environment()
    events, pages, messages, calls, _ = install(monkeypatch, env)
    original = dict(env.STATE_KV.data)
    status, result = call(env)
    assert status == 200, result
    first, second = owner(env)["fixtures"]
    first_id = first["message_id"]
    assert len(pages) == len(messages) == 1
    adapter = BatchDiscordKV(StateStore(env), owner(env))
    assert run(adapter.state().get_json(KEYS[1])) == [
        {
            "op": "notify",
            "id": first["discord_event_id"],
            "notification": {"channel_id": CHANNEL, "message_id": first_id},
        },
        {
            "op": "upsert",
            "id": second["discord_event_id"],
            "notification": {"channel_id": CHANNEL},
        },
    ]
    assert not any(method == "PUT" for method, _ in calls)
    assert call(env, "/advance")[0] == 409
    assert call(env, "/verify")[0] == 200
    assert call(env, "/advance")[1]["status"] == "retry_drained"
    assert owner(env)["fixtures"][0]["message_id"] == first_id
    assert len(pages) == len(messages) == 1
    assert messages[first_id]["reactions"][0]["me"] is True
    assert call(env, "/advance")[0] == 409
    assert call(env, "/verify")[1]["stage"] == "batch_retry_verified"
    assert call(env, "/advance")[1]["status"] == "drained"
    assert len(pages) == len(messages) == 2
    assert run(adapter.state().get_json(KEYS[1])) == []
    assert call(env, "/verify")[0] == 200
    assert call(env, "/verify")[0] == 200
    assert (
        sum(method == "POST" and path.endswith("/messages") for method, path in calls)
        == 2
    )
    assert (
        sum(method == "POST" and path.endswith("/pages") for method, path in calls) == 2
    )
    assert call(env, "/cleanup")[0] == 200
    assert call(env, "/cleanup")[0] == 200
    assert not events and not messages and all(p["archived"] for p in pages.values())
    assert env.STATE_KV.data == original
    clean = owner(env)
    assert clean["outcome"] == "passed" and clean["dirty"] is False
    for key in (
        "notification_reaction_deferred",
        "notification_retry_applied",
        "notification_final_readback",
    ):
        assert clean["stages"][key] == 200
    for index in range(2):
        assert clean["stages"][f"notification_{index}_delete"] == 204
        assert clean["stages"][f"notification_{index}_cleanup_read"] == 404
    assert all(
        value not in json.dumps(clean)
        for value in (CHANNEL, ROLE, first_id, first["discord_event_id"])
    )


def test_lost_post_response_is_reconciled_only_for_cleanup(monkeypatch):
    env = environment()
    events, pages, messages, calls, control = install(monkeypatch, env)
    control["post_lost"] = True
    assert call(env)[0] == 409
    assert len(messages) == 1
    assert not owner(env)["fixtures"][0].get("message_id")
    assert call(env, "/cleanup")[0] == 200
    assert not messages and not events and all(p["archived"] for p in pages.values())
    assert owner(env)["outcome"] == "failed_clean"
    assert (
        sum(method == "POST" and path.endswith("/messages") for method, path in calls)
        == 1
    )


@pytest.mark.parametrize("failure", ["delete_fail", "search_fail", "post_fail"])
def test_unresolved_cleanup_remains_dirty_and_retry_preserves_completed_resources(
    monkeypatch, failure
):
    env = environment()
    events, pages, messages, calls, control = install(monkeypatch, env)
    if failure in ("search_fail", "post_fail"):
        control["post_lost" if failure == "search_fail" else failure] = True
    call(env)
    control[failure] = True
    assert call(env, "/cleanup")[0] == 409
    assert owner(env)["dirty"] is True
    assert not events and all(p["archived"] for p in pages.values())
    control[failure] = False
    if failure == "post_fail":
        # 作成を試みたがIDも検索結果もない場合、未回収のまま保持する。
        assert call(env, "/cleanup")[0] == 409
    else:
        assert call(env, "/cleanup")[0] == 200
        assert not messages


@pytest.mark.parametrize(
    "field,value",
    [
        ("channel_id", "foreign"),
        ("content", "foreign"),
        ("mention_roles", []),
        ("mention_everyone", True),
    ],
)
def test_changed_message_is_never_deleted(monkeypatch, field, value):
    env = environment()
    _, _, messages, calls, _ = install(monkeypatch, env)
    assert call(env)[0] == 200
    message = next(iter(messages.values()))
    original = deepcopy(message)
    message[field] = value
    before = len(calls)
    assert call(env, "/cleanup")[0] == 409
    assert not any(
        method == "DELETE" and "/messages/" in path for method, path in calls[before:]
    )
    messages[message["id"]] = original
    assert call(env, "/cleanup")[0] == 200


@pytest.mark.parametrize("key", KEYS)
def test_kv_save_failure_after_post_still_cleans_message(monkeypatch, key):
    env = environment()
    _, _, messages, _, _ = install(monkeypatch, env)
    env.STATE_KV.fail_put = key
    assert call(env)[0] != 200
    assert len(messages) == 1 and owner(env)["dirty"] is True
    assert call(env, "/cleanup")[0] == 200
    assert not messages and owner(env)["outcome"] == "failed_clean"


@pytest.mark.parametrize("field", ["channel_id", "message_id", "id", "duplicate"])
def test_foreign_or_duplicate_queue_is_rejected_before_retry(monkeypatch, field):
    env = environment()
    _, _, _, calls, _ = install(monkeypatch, env)
    assert call(env)[0] == 200
    key = key_prefix(owner(env)) + KEYS[1]
    queue = json.loads(env.STATE_KV.data[key])
    if field == "duplicate":
        queue[1] = queue[0]
    elif field == "id":
        queue[0][field] = "foreign"
    else:
        queue[0]["notification"][field] = "foreign"
    env.STATE_KV.data[key] = json.dumps(queue)
    before = len(calls)
    assert call(env, "/verify")[0] != 200
    assert not any(
        method in ("POST", "PUT", "PATCH") and not path.endswith("/query")
        for method, path in calls[before:]
    )
    assert call(env, "/cleanup")[0] == 200


@pytest.mark.parametrize(
    "field", ["message_id", "message_content_sha256", "reaction_deferred", "channel"]
)
def test_do_rejects_message_owner_replacement_and_attempt_reversal(monkeypatch, field):
    env = environment()
    install(monkeypatch, env)
    assert call(env)[0] == 200
    previous = owner(env)
    changed = deepcopy(previous)
    if field == "channel":
        changed["target_fingerprints"]["channel_id_sha256"] = "a" * 64
    else:
        changed["fixtures"][0][field] = (
            False if field == "reaction_deferred" else "foreign"
        )
    with pytest.raises(RuntimeError):
        run(StateStore(env).put_e2e_manifest(NOTIFICATION_SERVICE, changed))
    assert owner(env) == previous


@pytest.mark.parametrize(
    "setting,value",
    [
        ("EVENT_CREATE_CHANNEL_ID", "other"),
        ("EVENT_CREATE_ROLE_ID", "other"),
        ("E2E_DISCORD_BATCH_NOTIFICATION_ENABLED", "false"),
    ],
)
def test_changed_notification_target_or_gate_stops_before_api(
    monkeypatch, setting, value
):
    env = environment()
    _, _, _, calls, _ = install(monkeypatch, env)
    assert call(env)[0] == 200
    setattr(env, setting, value)
    before = len(calls)
    assert call(env, "/verify")[0] in (404, 409)
    assert len(calls) == before


@pytest.mark.parametrize(
    "options,status",
    [({"token": "wrong"}, 401), ({"version": "wrong"}, 409), ({"method": "GET"}, 405)],
)
def test_entry_rejects_auth_version_and_method_before_api(monkeypatch, options, status):
    env = environment()
    _, _, _, calls, _ = install(monkeypatch, env)
    assert call(env, **options)[0] == status
    assert not calls


def test_reverification_failure_revokes_passed_outcome(monkeypatch):
    env = environment()
    _, _, messages, _, _ = install(monkeypatch, env)
    complete(env)
    next(iter(messages.values()))["reactions"] = []
    assert call(env, "/verify")[0] == 409
    assert owner(env)["verification_passed"] is False
    assert call(env, "/cleanup")[0] == 200
    assert owner(env)["outcome"] == "failed_clean"


def test_channel_in_different_guild_is_rejected_before_creation(monkeypatch):
    env = environment()
    events, pages, messages, calls, control = install(monkeypatch, env)
    control["wrong_channel"] = True
    assert call(env)[0] != 200
    assert not events and not pages and not messages
    assert not any(method != "GET" for method, _ in calls)


def test_manifest_save_failure_after_post_recovers_by_marker(monkeypatch):
    env = environment()
    _, _, messages, _, _ = install(monkeypatch, env)
    save = StateStore.put_e2e_manifest
    failed = False

    async def fail_message_id(self, service, manifest):
        nonlocal failed
        if (
            service == NOTIFICATION_SERVICE
            and not failed
            and manifest.get("fixtures", [{}])[0].get("message_id")
        ):
            failed = True
            raise RuntimeError("injected_manifest_save_failure")
        return await save(self, service, manifest)

    monkeypatch.setattr(StateStore, "put_e2e_manifest", fail_message_id)
    assert call(env)[0] != 200
    assert len(messages) == 1 and failed
    assert not owner(env)["fixtures"][0].get("message_id")
    assert call(env, "/cleanup")[0] == 200
    assert not messages and owner(env)["outcome"] == "failed_clean"


def test_ambiguous_lost_response_keeps_messages_dirty(monkeypatch):
    env = environment()
    _, _, messages, calls, control = install(monkeypatch, env)
    control["post_lost"] = True
    assert call(env)[0] == 409
    original = next(iter(messages.values()))
    messages["duplicate"] = {**deepcopy(original), "id": "duplicate"}
    before = len(calls)
    assert call(env, "/cleanup")[0] == 409
    assert owner(env)["dirty"] is True
    assert not any(
        method == "DELETE" and "/messages/" in path for method, path in calls[before:]
    )
    del messages["duplicate"]
    assert call(env, "/cleanup")[0] == 200


def test_real_reaction_error_is_not_confused_with_expected_injection(monkeypatch):
    env = environment()
    _, _, messages, calls, control = install(monkeypatch, env)
    assert call(env)[0] == 200
    assert call(env, "/verify")[0] == 200
    control["reaction_fail"] = True
    assert call(env, "/advance")[0] == 409
    assert owner(env)["stage"] == "batch_applying"
    assert not owner(env)["fixtures"][0].get("reaction_done")
    assert call(env, "/advance")[0] == 409
    assert (
        sum(method == "POST" and path.endswith("/messages") for method, path in calls)
        == 1
    )
    assert call(env, "/cleanup")[0] == 200
    assert not messages and owner(env)["outcome"] == "failed_clean"


def test_stale_second_queue_read_cannot_repeat_completed_notification(monkeypatch):
    env = environment()
    _, pages, _, calls, _ = install(monkeypatch, env)
    assert call(env)[0] == 200
    key = key_prefix(owner(env)) + KEYS[1]
    stale = env.STATE_KV.data[key]
    for phase in ("/verify", "/advance", "/verify"):
        assert call(env, phase)[0] == 200
    get = env.STATE_KV.get
    reads = 0

    async def stale_get(name):
        nonlocal reads
        if name == key:
            reads += 1
            if reads == 2:
                return stale
        return await get(name)

    env.STATE_KV.get = stale_get
    before = len(calls)
    assert call(env, "/advance")[0] == 409
    assert reads == 2 and len(pages) == 1
    assert not any(
        method in ("POST", "PUT", "PATCH") and not path.endswith("/query")
        for method, path in calls[before:]
    )
    assert call(env, "/cleanup")[0] == 200


@pytest.mark.parametrize("key", KEYS)
def test_kv_cleanup_failure_retains_finished_message_cleanup(monkeypatch, key):
    env = environment()
    _, _, messages, calls, _ = install(monkeypatch, env)
    complete(env)
    env.STATE_KV.fail_delete = key
    assert call(env, "/cleanup")[0] != 200
    assert not messages and owner(env)["dirty"] is True
    assert all(s["message_cleanup_done"] for s in owner(env)["fixtures"])
    env.STATE_KV.fail_delete = None
    before = len(calls)
    assert call(env, "/cleanup")[0] == 200
    assert not any("/messages" in path for _, path in calls[before:])


def test_do_cannot_mark_clean_while_message_cleanup_is_pending(monkeypatch):
    env = environment()
    install(monkeypatch, env)
    assert call(env)[0] == 200
    changed = deepcopy(owner(env))
    changed["fixtures"][0]["cleanup_done"] = True
    with pytest.raises(RuntimeError):
        run(StateStore(env).put_e2e_manifest(NOTIFICATION_SERVICE, changed))
