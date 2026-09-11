"""所有2件の件数制限・別HTTP残件処理・部分回収を外部通信なしで検証する。"""

import json
from copy import deepcopy

import pytest

from e2e_discord_batch_state import SERVICE, BatchDiscordKV, key_prefix
from e2e_discord_kv_state import KEYS
from e2e_entry import Default
from state import StateStore
from tests.fakes import Request
from tests.test_e2e_discord_kv_probe import environment
from tests.test_e2e_discord_notion_probe import GUILD_ID, RUN_ID, install_api_stub, run

PATH = "/admin/e2e/discord-batch"


def env_for_batch():
    env = environment()
    env.E2E_DISCORD_BATCH_ENABLED = "true"
    return env


def request(
    env, suffix="", *, run_id=RUN_ID, version=RUN_ID, method="POST", token="test-token"
):
    worker = Default()
    worker.env = env
    result = run(
        worker.fetch(
            Request(
                "https://bot.test" + PATH + suffix,
                method=method,
                headers={
                    "Authorization": "Bearer " + token,
                    "X-E2E-Run-ID": run_id,
                    "X-E2E-Version-Tag": version,
                },
            )
        )
    )
    text = run(result.text())
    return result.status, json.loads(text) if text.startswith("{") else {"error": text}


def manifest(env):
    value = run(StateStore(env).get_e2e_manifest(SERVICE))
    assert isinstance(value, dict)
    return value


def test_two_events_limit_one_then_restore_and_drain_queue(monkeypatch):
    events, pages, calls, _ = install_api_stub(monkeypatch, multiple=True)
    foreign = {"id": "foreign", "guild_id": GUILD_ID, "name": "Other run"}
    events["foreign"] = deepcopy(foreign)
    env = env_for_batch()
    original = dict(env.STATE_KV.data)
    status, result = request(env)
    assert status == 200, result
    assert len(events) == 3 and len(pages) == 1
    owner = manifest(env)
    assert owner["stage"] == "batch_pending"
    assert "snapshot" not in owner and "queue" not in owner
    adapter = BatchDiscordKV(StateStore(env), owner)
    first, second = [slot["discord_event_id"] for slot in owner["fixtures"]]
    assert set(run(adapter.state().get_discord_snapshot())) == {first, second}
    assert run(adapter.state().get_json(KEYS[1])) == [{"id": second, "op": "upsert"}]
    assert request(env, "/advance")[0] == 409
    assert len(pages) == 1
    assert request(env, "/verify")[0] == 200
    assert manifest(env)["stage"] == "batch_pending_verified"
    assert request(env, "/advance")[0] == 200
    assert len(pages) == 2
    assert request(env, "/advance")[0] == 409
    assert len(pages) == 2
    assert run(adapter.state().get_json(KEYS[1])) == []
    assert request(env, "/verify")[0] == 200
    assert manifest(env)["stage"] == "batch_verified"
    assert request(env, "/cleanup")[0] == 200
    assert request(env, "/cleanup")[0] == 200
    assert env.STATE_KV.data == original
    assert events == {"foreign": foreign} and all(p["archived"] for p in pages.values())
    clean = manifest(env)
    assert clean["outcome"] == "passed" and clean["dirty"] is False
    for name in (
        "batch_first_limit",
        "batch_pending_readback",
        "batch_remaining_applied",
        "batch_final_readback",
        "batch_kv_cleanup",
    ):
        assert clean["stages"][name] == 200
    assert not any(
        v in json.dumps(clean) for v in (first, second, *pages.keys(), GUILD_ID)
    )
    assert (
        sum(method == "POST" and path.endswith("/pages") for method, path in calls) == 2
    )
    assert not any(
        "/channels/" in path or "googleapis.com" in path for _, path in calls
    )
    with pytest.raises(ValueError, match="owner_mismatch"):
        run(adapter.state().get_discord_snapshot())


@pytest.mark.parametrize(
    "phase,key", [("", KEYS[0]), ("", KEYS[1]), ("/advance", KEYS[1])]
)
def test_partial_kv_save_failure_retains_owner_and_cleans_every_fixture(
    monkeypatch, phase, key
):
    events, pages, _, _ = install_api_stub(monkeypatch, multiple=True)
    env = env_for_batch()
    original = dict(env.STATE_KV.data)
    if phase:
        assert request(env)[0] == 200
        assert request(env, "/verify")[0] == 200
    env.STATE_KV.fail_put = key
    assert request(env, phase)[0] != 200
    assert manifest(env)["dirty"] is True
    assert request(env, "/cleanup")[0] == 200
    assert not events and all(p["archived"] for p in pages.values())
    assert env.STATE_KV.data == original
    assert manifest(env)["outcome"] == "failed_clean"


@pytest.mark.parametrize("key", KEYS)
def test_kv_delete_failure_keeps_both_owners_until_retry(monkeypatch, key):
    events, pages, _, _ = install_api_stub(monkeypatch, multiple=True)
    env = env_for_batch()
    assert request(env)[0] == 200
    env.STATE_KV.fail_delete = key
    assert request(env, "/cleanup")[0] != 200
    dirty = manifest(env)
    assert dirty["dirty"] and len(dirty["fixtures"]) == 2
    assert not events and all(p["archived"] for p in pages.values())
    env.STATE_KV.fail_delete = None
    assert request(env, "/cleanup")[0] == 200
    assert manifest(env)["dirty"] is False


def test_partial_external_cleanup_retries_only_unfinished_slot(monkeypatch):
    events, pages, calls, control = install_api_stub(monkeypatch, multiple=True)
    env = env_for_batch()
    assert request(env)[0] == 200
    assert request(env, "/verify")[0] == 200
    assert request(env, "/advance")[0] == 200
    control["notion_archive_statuses"] = [500] * 4 + [200]
    assert request(env, "/cleanup")[0] == 409
    dirty = manifest(env)
    assert dirty["fixtures"][0]["cleanup_done"] is False
    assert dirty["fixtures"][1]["cleanup_done"] is True
    assert not events
    completed_page = dirty["fixtures"][1]["notion_page_id"]
    before = len(calls)
    assert request(env, "/cleanup")[0] == 200
    assert all(p["archived"] for p in pages.values())
    assert not any(path.endswith(completed_page) for _, path in calls[before:])


def test_stale_pending_queue_blocks_advance_without_second_page_write(monkeypatch):
    _, pages, calls, _ = install_api_stub(monkeypatch, multiple=True)
    env = env_for_batch()
    assert request(env)[0] == 200
    assert request(env, "/verify")[0] == 200
    owner = manifest(env)
    prefix = key_prefix(owner)
    saved = env.STATE_KV.data[prefix + KEYS[1]]
    env.STATE_KV.data[prefix + KEYS[1]] = "[]"
    status, result = request(env, "/advance")
    assert status == 409 and result["error"] == "discord_batch_not_ready"
    assert len(pages) == 1
    env.STATE_KV.data[prefix + KEYS[1]] = saved
    assert request(env, "/advance")[0] == 200
    assert len(pages) == 2
    assert (
        sum(method == "POST" and path.endswith("/pages") for method, path in calls) == 2
    )


@pytest.mark.parametrize("mutate", ["run", "scope", "target", "event", "slot", "count"])
def test_do_rejects_replaced_owner_and_changed_fixture_count(monkeypatch, mutate):
    install_api_stub(monkeypatch, multiple=True)
    env = env_for_batch()
    assert request(env)[0] == 200
    previous = manifest(env)
    changed = deepcopy(previous)
    if mutate == "run":
        changed["run_id"] = "E2E-20260902T000001Z-1234abcd"
    if mutate == "scope":
        changed["scope_id"] = "b" * 32
    if mutate == "target":
        changed["target_fingerprints"]["guild_id_sha256"] = "b" * 64
    if mutate == "event":
        changed["fixtures"][1]["discord_event_id"] = "foreign"
    if mutate == "slot":
        changed["fixtures"].reverse()
    if mutate == "count":
        changed["fixtures"].pop()
    with pytest.raises(RuntimeError):
        run(StateStore(env).put_e2e_manifest(SERVICE, changed))
    assert manifest(env) == previous


@pytest.mark.parametrize(
    "options,expected",
    [({"token": "wrong"}, 401), ({"method": "GET"}, 405), ({"version": "wrong"}, 409)],
)
def test_auth_method_revision_rejected_before_fixture_creation(
    monkeypatch, options, expected
):
    _, _, calls, _ = install_api_stub(monkeypatch, multiple=True)
    assert request(env_for_batch(), **options)[0] == expected
    assert calls == []


def test_stale_second_kv_read_cannot_reapply_first_event(monkeypatch):
    _, pages, calls, _ = install_api_stub(monkeypatch, multiple=True)
    env = env_for_batch()
    assert request(env)[0] == 200
    assert request(env, "/verify")[0] == 200
    original_get = env.STATE_KV.get
    reads = {k: 0 for k in KEYS}

    async def stale(key):
        for suffix in KEYS:
            if key.endswith(suffix):
                reads[suffix] += 1
                if reads[suffix] >= 2:
                    return "{}" if suffix == KEYS[0] else "[]"
        return await original_get(key)

    monkeypatch.setattr(env.STATE_KV, "get", stale)
    before = len(calls)
    status, result = request(env, "/advance")
    assert status == 409 and result["error"] == "discord_batch_apply_failed"
    assert len(pages) == 1
    assert not any(
        method in ("POST", "PATCH") and "/pages" in path
        for method, path in calls[before:]
    )
    assert request(env, "/advance")[0] == 409


def test_changed_google_setting_blocks_advance_before_external_calls(monkeypatch):
    _, _, calls, _ = install_api_stub(monkeypatch, multiple=True)
    env = env_for_batch()
    assert request(env)[0] == 200
    assert request(env, "/verify")[0] == 200
    env.DISCORD_TO_GOOGLE_SYNC_ENABLED = "true"
    before = len(calls)
    assert request(env, "/advance")[0] == 409
    assert len(calls) == before


def test_failed_reverification_cannot_reuse_passed_outcome(monkeypatch):
    install_api_stub(monkeypatch, multiple=True)
    env = env_for_batch()
    for phase in ("", "/verify", "/advance", "/verify"):
        assert request(env, phase)[0] == 200
    owner = manifest(env)
    env.STATE_KV.data[key_prefix(owner) + KEYS[1]] = json.dumps(
        [
            {"op": "upsert", "id": owner["fixtures"][0]["discord_event_id"]},
        ]
    )
    assert request(env, "/verify")[0] == 409
    assert request(env, "/cleanup")[0] == 200
    assert manifest(env)["outcome"] == "failed_clean"


@pytest.mark.parametrize(
    "missing", ["E2E_DISCORD_BATCH_ENABLED", "E2E_STATE_SCOPE", "SYNC_COORDINATOR"]
)
def test_missing_enable_flag_or_binding_stops_before_external_calls(
    monkeypatch, missing
):
    _, _, calls, _ = install_api_stub(monkeypatch, multiple=True)
    env = env_for_batch()
    setattr(env, missing, None)
    assert request(env)[0] in (404, 503)
    assert calls == []


def test_poll_reads_listing_and_filters_valid_foreign_event_in_reverse_order(
    monkeypatch,
):
    import discord_notion_sync

    events, pages, calls, _ = install_api_stub(monkeypatch, multiple=True)
    original_fetch = discord_notion_sync.fetch
    poll_reads = []

    async def reordered_listing(url, options=None):
        response = await original_fetch(url, options)
        if str(url).endswith("/scheduled-events?with_user_count=false"):
            listed = json.loads(await response.text())
            foreign = {**deepcopy(listed[0]), "id": "valid-foreign-event"}
            poll_reads.append(len(listed))
            from workers import Response

            return Response(json.dumps([foreign, *reversed(listed)]), status=200)
        return response

    monkeypatch.setattr(discord_notion_sync, "fetch", reordered_listing)
    env = env_for_batch()
    for phase in ("", "/verify", "/advance", "/verify", "/cleanup"):
        assert request(env, phase)[0] == 200
    assert poll_reads == [2, 2]
    assert len(pages) == 2 and not events
    owner = manifest(env)
    assert owner["stages"]["batch_first_poll"] == 200
    assert owner["stages"]["batch_remaining_poll"] == 200
    assert all("valid-foreign-event" not in json.dumps(page) for page in pages.values())
    assert not any("valid-foreign-event" in path for _, path in calls)


@pytest.mark.parametrize("phase", ["", "/advance"])
@pytest.mark.parametrize(
    "fault", ["missing", "duplicate", "changed", "invalid", "unavailable"]
)
def test_invalid_poll_listing_stops_before_state_or_page_writes(
    monkeypatch, phase, fault
):
    import discord_notion_sync
    from workers import Response

    _, pages, calls, _ = install_api_stub(monkeypatch, multiple=True)
    env = env_for_batch()
    if phase:
        assert request(env)[0] == 200
        assert request(env, "/verify")[0] == 200
    original_fetch = discord_notion_sync.fetch

    async def bad_listing(url, options=None):
        response = await original_fetch(url, options)
        if not str(url).endswith("/scheduled-events?with_user_count=false"):
            return response
        listed = json.loads(await response.text())
        if fault == "missing":
            listed.pop()
        elif fault == "duplicate":
            listed.append(deepcopy(listed[0]))
        elif fault == "changed":
            listed[0]["description"] += " changed"
        elif fault == "invalid":
            listed.append(None)
        else:
            return Response("{}", status=403)
        return Response(json.dumps(listed), status=200)

    monkeypatch.setattr(discord_notion_sync, "fetch", bad_listing)
    original_kv = dict(env.STATE_KV.data)
    before = len(calls)
    assert request(env, phase)[0] == 409
    assert env.STATE_KV.data == original_kv
    assert len(pages) == (1 if phase else 0)
    assert not any(
        method in ("POST", "PATCH") and "/pages" in path
        for method, path in calls[before:]
    )
    assert manifest(env)["dirty"] is True
    assert request(env, "/cleanup")[0] == 200
    assert manifest(env)["outcome"] == "failed_clean"
