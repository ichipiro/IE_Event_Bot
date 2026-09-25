"""通常cleanupの全件取得・共有KV・回収を外部通信なしで検証する。"""

import pytest

import e2e_entry
import e2e_notion_cleanup_normal as normal
import e2e_notion_cleanup_probe as probe
from state import StateStore
from tests.fakes import Request
from tests.test_e2e_notion_cleanup_probe import (
    DUE_PAGE_ID, FUTURE_PAGE_ID, RUN_ID, install_notion_api_stub, make_env, run,
)


def setup(monkeypatch, **kwargs):
    data = install_notion_api_stub(monkeypatch, **kwargs)
    env = make_env()
    async def delete(key):
        env.STATE_KV.data.pop(key, None)
    env.STATE_KV.delete = delete
    env.INTERNAL_API_TOKEN = "test-token"
    env.CF_VERSION_METADATA = {"tag": RUN_ID, "id": "cleanup-version"}
    env.E2E_NOTION_CLEANUP_ENABLED = "true"
    env.SYNC_DO_LOCK_ENABLED = "true"
    headers = {"Authorization": "Bearer test-token", "X-E2E-Run-ID": RUN_ID,
               "X-E2E-Version-Tag": RUN_ID}
    request = Request("https://e2e.invalid/admin/e2e/notion-cleanup-normal/prepare", method="POST", headers=headers)
    return env, request, data


def phase(env, request, name):
    return run(normal.run(env, StateStore(env), RUN_ID, name, request))


def test_normal_http_full_query_shared_epoch_duplicate_and_cleanup(monkeypatch):
    env, request, (pages, calls, _) = setup(monkeypatch)
    app = e2e_entry.Default()
    app.env = env
    epoch = None
    for name in normal.PHASES:
        for step in (name, "verify"):
            request.url = f"https://e2e.invalid/admin/e2e/notion-cleanup-normal/{step}"
            response = run(app.fetch(request))
            assert response.status == 200, run(response.text())
        if name == "execute":
            assert pages[DUE_PAGE_ID]["archived"] is True
            assert pages[FUTURE_PAGE_ID]["archived"] is False
            epoch = run(StateStore(env).get_text("cleanup:last_epoch"))
            assert epoch and float(epoch) > 0
        if name == "duplicate":
            assert run(StateStore(env).get_text("cleanup:last_epoch")) == epoch
            result = run(StateStore(env).get_last_result("job_cleanup"))
            assert result and result["payload"]["reason"] == "interval_guard"
    # 通常処理の無加工queryは初回の1回。再実行ではquery前にinterval guardが効く。
    assert len([c for c in calls if c["path"].endswith("/query") and c["payload"] == {}]) == 1
    assert [c["path"] for c in calls if c["method"] == "PATCH"] == [f"/pages/{DUE_PAGE_ID}"]
    request.url = "https://e2e.invalid/admin/e2e/notion-cleanup/cleanup"
    response = run(app.fetch(request))
    assert response.status == 200, run(response.text())
    assert all(page["archived"] for page in pages.values())
    assert all(run(StateStore(env).get_text(key)) is None for key in normal.KEYS)
    owner = run(StateStore(env).get_e2e_manifest(normal.SERVICE))
    assert owner and owner["outcome"] == "passed" and owner["dirty"] is False
    assert all(owner["stages"][f"cleanup_normal_verify_{p}"] == 200 for p in normal.PHASES)
    assert run(normal.cleanup(env, StateStore(env), RUN_ID))["ok"]


@pytest.mark.parametrize("key", normal.KEYS)
def test_existing_shared_state_is_never_overwritten(monkeypatch, key):
    env, request, (pages, _, _) = setup(monkeypatch)
    run(env.STATE_KV.put(key, "foreign"))
    with pytest.raises(RuntimeError, match="shared_state_not_empty"):
        phase(env, request, "prepare")
    assert not pages and run(StateStore(env).get_text(key)) == "foreign"


@pytest.mark.parametrize("before_prepare", (True, False))
def test_foreign_page_blocks_job_and_is_never_archived(monkeypatch, before_prepare):
    env, request, (pages, _, _) = setup(monkeypatch)
    if not before_prepare:
        phase(env, request, "prepare")
        phase(env, request, "verify")
    pages["foreign"] = {"id": "foreign", "properties": {}}
    with pytest.raises(RuntimeError, match="database_not_empty|foreign_input"):
        phase(env, request, "prepare" if before_prepare else "execute")
    assert run(StateStore(env).get_text("cleanup:last_epoch")) is None
    if not before_prepare:
        assert run(normal.cleanup(env, StateStore(env), RUN_ID))["ok"]
    assert not pages["foreign"].get("archived")


def test_cleanup_preserves_foreign_kv_and_rejects_wrong_run(monkeypatch):
    env, request, _ = setup(monkeypatch)
    phase(env, request, "prepare")
    with pytest.raises(RuntimeError, match="run_id_mismatch"):
        run(normal.cleanup(env, StateStore(env), "E2E-20260925T000000Z-ffffffff"))
    run(env.STATE_KV.put("cleanup:last_epoch", "foreign"))
    with pytest.raises(RuntimeError, match="foreign_kv"):
        run(normal.cleanup(env, StateStore(env), RUN_ID))
    assert run(StateStore(env).get_text("cleanup:last_epoch")) == "foreign"
    owner = run(StateStore(env).get_e2e_manifest(normal.SERVICE))
    assert owner and owner["dirty"] is True


def test_unverified_or_replayed_phase_rejected(monkeypatch):
    env, request, _ = setup(monkeypatch)
    phase(env, request, "prepare")
    with pytest.raises(RuntimeError, match="previous_unverified"):
        phase(env, request, "execute")
    phase(env, request, "verify")
    phase(env, request, "execute")
    with pytest.raises(RuntimeError, match="phase_mismatch"):
        phase(env, request, "execute")


@pytest.mark.parametrize("header,value,status", (("X-E2E-Version-Tag", "old", 409), ("Authorization", "wrong", 401), ("X-E2E-Run-ID", "bad", 400)))
def test_route_requires_auth_run_revision(monkeypatch, header, value, status):
    env, request, (pages, _, _) = setup(monkeypatch)
    headers = {key: request.headers.get(key) for key in ("Authorization", "X-E2E-Run-ID", "X-E2E-Version-Tag")}
    headers[header] = value
    request = Request(request.url, method="POST", headers=headers)
    app = e2e_entry.Default()
    app.env = env
    response = run(app.fetch(request))
    assert response.status == status
    assert not pages


@pytest.mark.parametrize("claim_first", (True, False))
def test_shared_scenarios_exclude_each_other(monkeypatch, claim_first):
    env, request, _ = setup(monkeypatch)
    store = StateStore(env)
    async def other():
        await store.put_e2e_manifest("notion", {"version": 1, "kind": "notion_pages", "dirty": True, "run_id": RUN_ID})
    if claim_first:
        run(other())
        with pytest.raises(RuntimeError, match="e2e_manifest_write_failed"):
            phase(env, request, "prepare")
    else:
        phase(env, request, "prepare")
        with pytest.raises(RuntimeError, match="e2e_manifest_write_failed"):
            run(other())


def test_lost_create_response_reconciles_without_duplicate(monkeypatch):
    env, request, (pages, calls, _) = setup(monkeypatch, lose_create_for="due")
    phase(env, request, "prepare")
    assert len(pages) == 2
    assert len([c for c in calls if c["method"] == "POST" and c["path"] == "/pages"]) == 2
    assert run(normal.cleanup(env, StateStore(env), RUN_ID))["ok"]
    owner = run(StateStore(env).get_e2e_manifest(normal.SERVICE))
    assert owner and owner["outcome"] == "failed_clean"


def test_unresolved_create_can_be_recovered_after_query_returns(monkeypatch):
    env, request, (pages, _, control) = setup(monkeypatch, lose_create_for="future", hide_query_results=True)
    with pytest.raises(RuntimeError, match="ownership_unresolved"):
        phase(env, request, "prepare")
    with pytest.raises(RuntimeError, match="ownership_unresolved"):
        run(normal.cleanup(env, StateStore(env), RUN_ID))
    control["hide_query_results"] = False
    assert run(probe.cleanup_notion_cleanup_probe(env, StateStore(env), RUN_ID))["ok"]
    assert all(page["archived"] for page in pages.values())


def test_foreign_page_marker_blocks_recovery(monkeypatch):
    env, request, (pages, _, _) = setup(monkeypatch)
    phase(env, request, "prepare")
    pages[DUE_PAGE_ID]["properties"]["GoogleイベントID"] = {"rich_text": [{"plain_text": "foreign"}]}
    with pytest.raises(RuntimeError, match="cleanup_target_mismatch"):
        run(normal.cleanup(env, StateStore(env), RUN_ID))
    assert not pages[DUE_PAGE_ID]["archived"]


def test_stale_shared_epoch_prevents_reexecution(monkeypatch):
    env, request, (_, calls, _) = setup(monkeypatch)
    for name in ("prepare", "execute"):
        phase(env, request, name)
        phase(env, request, "verify")
    env.STATE_KV.data.pop("cleanup:last_epoch")
    before = len([c for c in calls if c["method"] == "PATCH"])
    with pytest.raises(RuntimeError, match="kv_not_ready"):
        phase(env, request, "duplicate")
    assert len([c for c in calls if c["method"] == "PATCH"]) == before


def test_partial_archive_failure_is_failed_clean(monkeypatch):
    import jobs
    env, request, (pages, _, _) = setup(monkeypatch)
    phase(env, request, "prepare")
    phase(env, request, "verify")
    async def fail(*args):
        return False
    monkeypatch.setattr(jobs, "_notion_archive_page", fail)
    with pytest.raises(RuntimeError, match="job_failed"):
        phase(env, request, "execute")
    assert run(normal.cleanup(env, StateStore(env), RUN_ID))["ok"]
    owner = run(StateStore(env).get_e2e_manifest(normal.SERVICE))
    assert owner and owner["outcome"] == "failed_clean"
    assert all(page["archived"] for page in pages.values())


def test_kv_write_response_loss_can_be_cleaned(monkeypatch):
    env, request, _ = setup(monkeypatch)
    phase(env, request, "prepare")
    phase(env, request, "verify")
    put = env.STATE_KV.put
    async def lost(key, value):
        await put(key, value)
        raise RuntimeError("write_response_lost")
    env.STATE_KV.put = lost
    with pytest.raises(RuntimeError, match="cleanup_normal_job_failed"):
        phase(env, request, "execute")
    env.STATE_KV.put = put
    assert run(normal.cleanup(env, StateStore(env), RUN_ID))["ok"]
    assert not env.STATE_KV.data


def test_expired_run_is_never_executed(monkeypatch):
    env, request, (pages, _, _) = setup(monkeypatch)
    phase(env, request, "prepare")
    phase(env, request, "verify")
    store = StateStore(env)
    owner = run(store.get_e2e_manifest(normal.SERVICE))
    assert owner
    owner["deadline"] = "2020-01-01T00:00:00+00:00"
    run(store.put_e2e_manifest(normal.SERVICE, owner))
    with pytest.raises(RuntimeError, match="deadline_exceeded"):
        phase(env, request, "execute")
    assert not any(page["archived"] for page in pages.values())


@pytest.mark.parametrize("enabled,method,status", (("false", "POST", 404), ("true", "GET", 405)))
def test_route_rejects_disabled_or_non_post(monkeypatch, enabled, method, status):
    env, request, (pages, _, _) = setup(monkeypatch)
    env.E2E_NOTION_CLEANUP_ENABLED = enabled
    request.method = method
    app = e2e_entry.Default()
    app.env = env
    assert run(app.fetch(request)).status == status
    assert not pages
