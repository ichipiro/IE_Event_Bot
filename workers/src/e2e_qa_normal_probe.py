"""空の専用Q&A DBで通常HTTPハンドラ・共有KVを段階検証する。"""

import json
from types import SimpleNamespace

from e2e_job_retry import (
    check_failure, install_failure, previous_phase,
    install_list_failure, check_list_failure, list_retry_verified,
)
import e2e_qa_notification_probe as qa
from e2e_notion_probe import _property_number, _property_text, _rich_text, _title


SERVICE = qa.QA_NOTIFICATION_MANIFEST_SERVICE
KEYS = ("qa_cache", "result:job_qa_check")
PHASES = ("prepare", "first", "update", "notify", "duplicate")


def _require(condition, code):
    if not condition:
        raise RuntimeError(code)


def _targets(env):
    return qa._target_fingerprints(env.NOTION_QA_ID, env.DISCORD_GUILD_ID, env.QA_CHANNEL_ID)


async def _save(store, owner):
    await store.put_e2e_manifest(SERVICE, owner)


async def _pages(env, owner):
    status, data = await qa._notion_request_stage(
        env, owner["stages"], {}, "qa_normal_query", "POST",
        f"/databases/{env.NOTION_QA_ID}/query", {"page_size": 100},
    )
    _require(status == 200 and isinstance(data, dict) and data.get("has_more") is False
             and isinstance(data.get("results"), list), "qa_normal_query_failed")
    return data["results"]


def _owned(env, owner, page):
    return qa._qa_page_has_run(page, page_id=str(page.get("id", "")),
                               database_id=env.NOTION_QA_ID, run_id=owner["run_id"])


async def _inputs(env, owner):
    pages = await _pages(env, owner)
    _require(len(pages) == 3 and {p.get("id") for p in pages} == set(owner["pages"])
             and all(_owned(env, owner, p) for p in pages), "qa_normal_foreign_input")
    return pages


async def _messages(env, owner):
    status, rows = await qa._discord_request_stage(
        env, owner["stages"], {}, "qa_normal_messages", "GET",
        f"/channels/{env.QA_CHANNEL_ID}/messages?limit=100",
    )
    _require(status == 200 and isinstance(rows, list) and len(rows) < 100,
             "qa_normal_messages_unbounded")
    return [m for m in rows if qa._run_marker(owner["run_id"]) in str(m.get("content", ""))]


class _OwnedKV:
    """固定キーを実KVへ通し、書込み前に回収可能なdigestをDOへ残す。"""

    def __init__(self, store, owner):
        self.store, self.owner = store, owner

    async def get(self, key):
        _require(key in KEYS, "qa_normal_key_forbidden")
        current = await self.store.get_e2e_manifest(SERVICE)
        _require(current and current.get("dirty") is True
                 and current.get("run_id") == self.owner["run_id"], "qa_normal_owner_mismatch")
        value = await self.store.get_text(key)
        _require((qa._fingerprint(value) if value is not None else None)
                 == self.owner["hashes"].get(key), "qa_normal_kv_not_ready")
        return value

    async def put(self, key, value):
        await self.get(key)
        data = json.loads(value)
        if key == "qa_cache":
            _require(isinstance(data, dict) and set(data) == set(self.owner["pages"]) | {"_first_qa_run"}
                     and data.get("_first_qa_run") is False, "qa_normal_cache_invalid")
        digest = qa._fingerprint(value)
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


async def _verify(env, store, owner):
    for key in KEYS:
        await _OwnedKV(store, owner).get(key)
    pages = await _inputs(env, owner)
    completed = owner["completed"]
    if completed.startswith("list_fail"):
        result = await store.get_last_result("job_qa_check")
        _require((result or {}).get("payload") == owner["list_failure_detail"],
                 "qa_normal_list_result_mismatch")
        _require(await store.get_json("qa_cache", None) == owner.get("cache"),
                 "qa_normal_cache_not_ready")
        _require({p["id"]: p["properties"] for p in pages} == owner["list_properties"],
                 "qa_normal_list_pages_changed")
        _require(not await _messages(env, owner), "qa_normal_message_count_failed")
        owner["stages"][f"qa_normal_verify_{completed}"] = 200
        await _save(store, owner)
        return
    if completed in ("first", "update", "fail", "notify", "duplicate"):
        _require({_property_number(p, "質問番号") for p in pages} == {41, 42, 43},
                 "qa_normal_numbers_failed")
        cache = await store.get_json("qa_cache", {})
        expected = owner["cache"]
        _require(cache == expected, "qa_normal_cache_not_ready")
        result = await store.get_last_result("job_qa_check")
        detail = (result or {}).get("payload", {})
        _require(detail.get("ok") is (completed != "fail") and detail.get("mode") == "native"
                 and detail.get("first_run") is (completed in ("first", "update")),
                 "qa_normal_result_failed")
    messages = await _messages(env, owner)
    if completed == "fail":
        _require(detail == owner["failure_detail"], "qa_normal_failure_result_mismatch")
    expected_count = 1 if completed == "fail" else 2 if completed in ("notify", "duplicate") else 0
    _require(len(messages) == expected_count, "qa_normal_message_count_failed")
    if expected_count:
        expected = {
            f"❓ 質問番号 #{_property_number(p, '質問番号')} に更新があります\n"
            f"質問: {_property_text(p, '質問', 'title')}\n回答: (回答なし)"
            for p in pages if not _property_text(p, "回答", "rich_text")
            and (completed != "fail" or p["id"] not in owner["failure_detail"]["failed_page_ids"])
        }
        _require({m.get("content") for m in messages} == expected
                 and all(str(m.get("channel_id")) == env.QA_CHANNEL_ID
                         and m.get("mention_everyone") is False
                         and not m.get("mention_roles") for m in messages),
                 "qa_normal_message_content_failed")
        ids = sorted(m["id"] for m in messages)
        if owner.get("messages"):
            expected_ids = set(owner["messages"])
            _require(expected_ids.issubset(ids) if completed == "notify" and owner.get("retry")
                     else ids == owner["messages"], "qa_normal_duplicate_messages")
        owner["messages"] = ids
    owner["stages"][f"qa_normal_verify_{completed}"] = 200
    await _save(store, owner)


async def _prepare(env, store, run_id):
    previous = await store.get_e2e_manifest(SERVICE)
    _require(not previous or not previous.get("dirty"), "environment_dirty")
    _require(not qa._configuration_error(env), "qa_normal_configuration_invalid")
    owner = {
        "version": 1, "kind": "qa_notification_job", "normal": True,
        "run_id": run_id, "dirty": True, "target_fingerprints": _targets(env),
        "pages": [], "messages": [], "attempted": [], "hashes": {}, "writes": {},
        "stages": {}, "stage": "preparing", "completed": "",
    }
    _require(not await qa._verify_targets(env, owner["stages"], {}), "qa_normal_targets_failed")
    _require(not await _pages(env, owner), "qa_normal_database_not_empty")
    _require(not await _messages(env, owner), "qa_normal_message_collision")
    for key in KEYS:
        _require(await store.get_text(key) is None, "qa_normal_shared_state_not_empty")
    await _save(store, owner)
    for index in range(3):
        title = f"[E2E] QA normal {index} {qa._run_marker(run_id)}"
        owner["attempted"].append(title)
        await _save(store, owner)
        status, page = await qa._notion_request_stage(
            env, owner["stages"], {}, f"qa_normal_create_{index}", "POST", "/pages",
            {"parent": {"database_id": env.NOTION_QA_ID}, "properties": {
                "質問": _title(title), "回答": _rich_text("回答済み" if index == 0 else ""),
                "質問番号": {"number": 41 if index == 0 else None},
            }},
        )
        _require(status == 200 and isinstance(page, dict) and _owned(env, owner, page),
                 "qa_normal_create_failed")
        owner["pages"].append(page["id"])
        await _save(store, owner)
    owner["completed"] = "prepare"
    owner["stage"] = "prepared"
    owner["stages"]["qa_normal_prepare"] = 200
    await _save(store, owner)
    return owner


async def _job(env, store, owner, phase, request):
    from entry import Application

    pages_before = await _inputs(env, owner)
    if phase.startswith("list_fail"):
        owner["list_properties"] = {p["id"]: p["properties"] for p in pages_before}
    for key in KEYS:
        await _OwnedKV(store, owner).get(key)
    owner["stage"] = "working"
    await _save(store, owner)
    # 認証済みrequestのヘッダを維持し、通常fetchのQ&A分岐をそのまま呼ぶ。
    job_request = SimpleNamespace(url="https://e2e.invalid/jobs/qa-check", method="POST", headers=request.headers)
    job_env = _JobEnv(env, _OwnedKV(store, owner))
    calls = install_failure(job_env, owner, "qa_normal") if phase == "fail" else []
    if phase.startswith("list_fail"):
        calls = install_list_failure(job_env, owner, "qa_normal", phase)
    await _save(store, owner)
    response = await Application(job_env).fetch(job_request)
    detail = json.loads(await response.text())
    if phase.startswith("list_fail"):
        check_list_failure(owner, "qa_normal", phase, response.status, detail, calls)
        return
    if phase == "fail":
        check_failure(owner, "qa_normal", response.status, detail, calls)
        failed = detail.get("failed_page_ids", [])
        _require(len(failed) == 1 and failed[0] in owner["pages"], "qa_normal_failed_id_invalid")
        old_cache = owner["cache"]
        pages = await _inputs(env, owner)
        owner["cache"] = {"_first_qa_run": False, **{
            p["id"]: old_cache[p["id"]] if p["id"] in failed else p["last_edited_time"] for p in pages
        }}
        return
    _require(response.status == 200 and detail.get("ok") is True
             and detail.get("first_run") is (phase == "first"), "qa_normal_job_failed")
    pages = await _inputs(env, owner)
    owner["cache"] = {"_first_qa_run": False, **{p["id"]: p["last_edited_time"] for p in pages}}
    owner["stages"][f"qa_normal_{phase}"] = 200


async def _update(env, store, owner):
    pages = await _inputs(env, owner)
    owner["stage"] = "working"
    await _save(store, owner)
    for page in pages:
        status, updated = await qa._notion_request_stage(
            env, owner["stages"], {}, "qa_normal_update_page", "PATCH", f"/pages/{page['id']}",
            {"properties": {"質問": _title(_property_text(page, "質問", "title") + " updated")}},
        )
        _require(status == 200 and isinstance(updated, dict) and _owned(env, owner, updated)
                 and updated.get("last_edited_time") != owner["cache"][page["id"]],
                 "qa_normal_timestamp_unchanged")
    owner["stages"]["qa_normal_update"] = 200


async def cleanup(env, store, run_id):
    owner = await store.get_e2e_manifest(SERVICE)
    _require(owner and owner.get("normal") and owner.get("run_id", owner.get("last_run_id")) == run_id,
             "cleanup_run_id_mismatch")
    if not owner.get("dirty"):
        return {"ok": True, "dirty": False, "run_id": run_id, "stages": owner["stages"]}
    _require(owner.get("target_fingerprints") == _targets(env), "dirty_manifest_target_mismatch")
    owner["stage"] = "cleanup"
    await _save(store, owner)
    # 応答喪失時は全件読取りでmarkerを再発見し、回収対象に加える。
    pages = await _pages(env, owner)
    owned_pages = [p for p in pages if _owned(env, owner, p)]
    for title in owner["attempted"]:
        _require(any(_property_text(p, "質問", "title") in (title, title + " updated") for p in owned_pages)
                 or len(owner["pages"]) == len(owner["attempted"]), "qa_normal_page_ownership_unresolved")
    for page in owned_pages:
        if page["id"] not in owner["pages"]:
            owner["pages"].append(page["id"])
    await _save(store, owner)
    for page_id in owner["pages"]:
        ok, _, _, _ = await qa._archive_qa_page(
            env, env.NOTION_QA_ID, page_id, run_id, owner["stages"], {},
        )
        _require(ok, "qa_normal_archive_failed")
    messages = await _messages(env, owner)
    for message in messages:
        ok, _, _, _ = await qa._delete_message(
            env, env.QA_CHANNEL_ID, message["id"], run_id, owner["stages"], {},
        )
        _require(ok, "qa_normal_delete_message_failed")
    _require(not await _messages(env, owner), "qa_normal_messages_remaining")
    for key in KEYS:
        value = await store.get_text(key)
        if value is not None:
            _require(qa._fingerprint(value) in owner["writes"].get(key, []), "qa_normal_foreign_kv")
            await env.STATE_KV.delete(key)
        _require(await store.get_text(key) is None, "qa_normal_cleanup_not_ready")
    owner["stages"]["qa_normal_cleanup"] = 200
    clean = {
        "version": 1, "kind": "qa_notification_job", "normal": True, "dirty": False,
        "last_run_id": run_id, "outcome": "passed" if owner["completed"] == "duplicate"
        and owner["stages"].get("qa_normal_verify_duplicate") == 200
        and list_retry_verified(owner, "qa_normal")
        and (not owner.get("retry") or owner["stages"].get("qa_normal_verify_fail") == 200) else "failed_clean",
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
                 "qa_normal_owner_mismatch")
        if phase == "verify":
            await _verify(env, store, owner)
        else:
            _require(phase in (*PHASES, "fail", "list_fail", "list_fail_first") and owner.get("completed") == previous_phase(owner, PHASES, phase, "notify")
                     and owner.get("stage") != "working", "qa_normal_phase_mismatch")
            _require(owner["stages"].get(f"qa_normal_verify_{owner['completed']}") == 200,
                     "qa_normal_previous_unverified")
            if phase == "update":
                await _update(env, store, owner)
            else:
                await _job(env, store, owner, phase, request)
            owner["completed"] = phase
            owner["stage"] = "ready"
            await _save(store, owner)
    return {"ok": True, "dirty": True, "run_id": run_id, "stages": owner["stages"]}
