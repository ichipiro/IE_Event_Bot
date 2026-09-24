"""通常watchと実通知を、専用環境の共有状態・通常同期入口へ接続する。"""

import asyncio
import json
from copy import deepcopy
from hashlib import sha256
from hmac import new as hmac_new
from types import SimpleNamespace

from workers import Response

import e2e_google_sync_probe as google
import google_watch
from e2e_all_http_probe import HttpApplication, HttpEnv
from e2e_google_sync_state import GoogleKV, GoogleStateError, WATCH_KEYS, digest
from entry import _header
from state import StateStore


def require(ok, code):
    if not ok:
        raise GoogleStateError("watch_shared_" + code)


def channel(run_id, index):
    return "e2e-watch-" + digest(run_id)[:40] + "-" + str(index)


def initial_watch_owner(env, run_id):
    return {"webhook_sync": True, "watches": [], "webhook_armed": False,
            "watch_url_sha256": digest(env.GCAL_WEBHOOK_URL)}


def valid_watch_owner(previous, owner):
    watches = owner.get("watches")
    if (not isinstance(watches, list) or len(watches) > 6
            or type(owner.get("webhook_armed")) is not bool
            or not isinstance(owner.get("watch_url_sha256"), str)):
        return False
    for i, watch in enumerate(watches):
        if (not isinstance(watch, dict) or watch.get("channel_id") != channel(owner["run_id"], i)
                or type(watch.get("stopped")) is not bool
                or not isinstance(watch.get("resource_id", ""), str)):
            return False
    if previous.get("dirty"):
        if previous.get("watch_url_sha256") != owner["watch_url_sha256"]:
            return False
        old = previous.get("watches", [])
        if len(watches) < len(old):
            return False
        for a, b in zip(old, watches):
            if (a["channel_id"] != b["channel_id"] or (a.get("resource_id") and a["resource_id"] != b.get("resource_id"))
                    or (a["stopped"] and not b["stopped"])):
                return False
    return True


def validate_watch_value(owner, key, value):
    data = json.loads(value)
    require(isinstance(data, dict), "kv_invalid")
    if key == "gcal_watch_state":
        require(not data or any(w["channel_id"] == data.get("channel_id") for w in owner["watches"]), "kv_foreign_watch")


async def rpc(store, owner, **payload):
    result = await store._sync_do_rpc(store._sync_do_stub(store.env.SYNC_COORDINATOR),
                                     "e2e_watch_shared", {"run_id": owner["run_id"], **payload})
    require(result and result.get("ok"), "observation_failed")
    return result


async def watch_rpc(storage, payload):
    """初回syncがwatch応答より先に届いても、回収用resource IDを保持する。"""
    raw = await storage.get("e2e:manifest:google_sync")
    owner = json.loads("{}" if raw is None or str(raw) in ("jsnull", "jsundefined") else str(raw))
    if not owner.get("dirty") or not owner.get("webhook_sync") or owner.get("run_id") != payload.get("run_id"):
        return {"ok": False}, 409
    key = "e2e:watch_shared:" + owner["run_id"]
    raw = await storage.get(key)
    observations = json.loads("{}" if raw is None or str(raw) in ("jsnull", "jsundefined") else str(raw))
    if payload.get("clear"):
        if owner.get("stage") != "cleanup" or not all(w["stopped"] for w in owner["watches"]):
            return {"ok": False}, 409
        await storage.delete(key)
        return {"ok": True}, 200
    if payload.get("channel_id"):
        cid, rid = payload["channel_id"], payload.get("resource_id", "")
        watch = next((w for w in owner["watches"] if w["channel_id"] == cid), None)
        msg, kind = str(payload.get("message_number", "")), payload.get("resource_state")
        if (not watch or watch["stopped"] or not isinstance(rid, str) or not 1 <= len(rid) <= 512
                or not msg.isdigit() or len(msg) > 32 or kind not in ("sync", "exists")):
            return {"ok": False}, 404
        record = observations.setdefault(cid, {"resource_id": rid, "messages": []})
        if record["resource_id"] != rid or (watch.get("resource_id") and watch["resource_id"] != rid):
            return {"ok": False}, 409
        if kind == "sync":
            record["sync"] = msg == "1"
        if payload.get("dedupe") and msg not in record["messages"]:
            if len(record["messages"]) >= 20:
                return {"ok": False}, 409
            record["messages"].append(msg)
        await storage.put(key, json.dumps(observations))
    return {"ok": True, "observations": observations}, 200


class WatchEnv(HttpEnv):
    def __init__(self, env, token, kv, run_id, index, *, rotated=False):
        super().__init__(env, token, kv)
        self.GCAL_WEBHOOK_URL = env.GCAL_WEBHOOK_URL
        self.WATCH_CHANNEL_ID = channel(run_id, index)
        self.GCAL_WEBHOOK_TOKEN = (hmac_new(env.GCAL_WEBHOOK_TOKEN.encode(), run_id.encode(), sha256).hexdigest()
                                   if rotated else env.GCAL_WEBHOOK_TOKEN)
        self.GCAL_WATCH_RENEW_THRESHOLD_SECONDS: str = "60"


async def stop(env, store, owner, watch, token):
    if watch["stopped"]:
        return
    observed = (await rpc(store, owner))["observations"].get(watch["channel_id"], {})
    rid = watch.get("resource_id") or observed.get("resource_id")
    require(rid, "stop_resource_unknown")
    require(not watch.get("resource_id") or not observed.get("resource_id") or rid == observed["resource_id"], "stop_owner_mismatch")
    watch["resource_id"] = rid
    await google._save(store, owner)
    response = await google_watch.fetch("https://www.googleapis.com/calendar/v3/channels/stop", {
        "method": "POST", "headers": {"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        "body": json.dumps({"id": watch["channel_id"], "resourceId": rid}),
    })
    require(response.status in (200, 204, 404), "stop_failed")
    watch["stopped"] = True
    await google._save(store, owner)


async def maintain(env, store, owner, token):
    require(not any([await google._raw_shared(env, k) is not None for k in WATCH_KEYS]), "shared_not_empty")
    kv = GoogleKV(store, owner)
    previous = None
    actions = ("register_missing", "renew_expiring", "renew_no_expiration", "renew_token_changed", "renew_token_changed", "register_missing")
    for index, action in enumerate(actions):
        if index == 5:
            await stop(env, store, owner, owner["watches"][-1], token)
        watch = {"channel_id": channel(owner["run_id"], index), "resource_id": "", "stopped": False}
        owner["watches"].append(watch)
        await google._save(store, owner)
        kv.owner = deepcopy(owner)
        run_env = WatchEnv(env, token, kv, owner["run_id"], index, rotated=index == 3)
        state = StateStore(run_env)
        if index == 1:
            run_env.GCAL_WATCH_RENEW_THRESHOLD_SECONDS = "999999999"
        if index in (2, 5):
            await asyncio.sleep(1.1)
            current = (await state.get_json("gcal_watch_state", {})) or {}
            if index == 2:
                current.pop("expiration", None)
            else:
                current = {}
            await state.put_json("gcal_watch_state", current)
        await asyncio.sleep(1.1)
        request = SimpleNamespace(url="https://e2e.invalid/admin/gcal/watch/ensure", method="POST",
                                  headers={"Authorization": "Bearer " + env.INTERNAL_API_TOKEN})
        response = await HttpApplication(run_env).fetch(request)
        result = json.loads(await response.text())
        require(response.status == 200 and result.get("ok") and result.get("action") == action, "maintenance_" + action)
        saved = (await state.get_json("gcal_watch_state", {})) or {}
        require(saved.get("channel_id") == watch["channel_id"] and saved.get("resource_id"), "watch_not_saved")
        watch["resource_id"] = saved["resource_id"]
        if previous and index != 5:
            require(result["result"]["stop"].get("ok"), "renew_stop_failed")
            previous["stopped"] = True
        previous = watch
        owner["hashes"] = kv.hashes
        owner["stages"]["watch_shared_" + action + "_" + str(index)] = 200
        await google._save(store, owner)
        if index == 0:
            run_env.GCAL_WATCH_RENEW_THRESHOLD_SECONDS = "60"
            unchanged = dict(kv.hashes)
            noop = await google_watch.ensure_watch_active(run_env, state)
            require(noop.get("action") == "noop_valid" and kv.hashes == unchanged, "noop_failed")
            owner["stages"]["watch_shared_noop"] = 200
    owner["stages"]["watch_shared_maintenance"] = 200
    await google._save(store, owner)


async def trigger(env, store, run_id, owner):
    require(owner and owner.get("dirty") and owner.get("webhook_sync") and owner["run_id"] == run_id
            and owner["stage"] == "verified" and owner["step"] < 3 and not owner.get("webhook_armed"), "trigger_phase")
    require(owner["watch_url_sha256"] == digest(env.GCAL_WEBHOOK_URL), "target_mismatch")
    token = await google.get_google_access_token(env, StateStore(google._DeltaEnv(env)))
    if not token:
        raise GoogleStateError("watch_shared_token_required")
    # 所有確認と異常系の観測は管理HTTPで済ませ、実callbackの応答待ちを短くする。
    from e2e_all_sync_probe import _verify, _change
    from e2e_all_http_probe import _inputs
    await _verify(env, store, owner, token)
    owner["step"] += 1
    owner["stage"] = "working"
    await google._save(store, owner)
    await _change(env, store, owner, token)
    await _inputs(env, store, owner, token)
    for fixture in owner["fixtures"]:
        fixture["apply_attempted"] = True
    await google._save(store, owner)
    if owner["step"] == 1:
        kv = GoogleKV(store, owner)
        await retry_controls(env, store, owner, HttpEnv(env, token, kv), kv)
        owner["hashes"] = kv.hashes
        await google._save(store, owner)
    owner["webhook_armed"] = True
    await google._save(store, owner)
    slot = owner["fixtures"][0]
    status, event = await google._google_request("GET", google._event_item_url(env.GOOGLE_CALENDAR_ID, slot["google_event_id"]), token)
    require(status == 200 and google._source_owned(event, slot), "trigger_owner")
    private = dict(event.get("extendedProperties", {}).get("private", {}))
    private["ie_e2e_webhook_step"] = str(owner["step"])
    status, event = await google._google_request("PATCH", google._event_item_url(env.GOOGLE_CALENDAR_ID, slot["google_event_id"]) + "?sendUpdates=none",
                                                token, {"extendedProperties": {"private": private}})
    require(status == 200 and google._source_owned(event, slot), "trigger_failed")
    return {"ok": True, "dirty": True, "status": ("drained", "updated", "drained")[owner["step"] - 1], "stage": "ready"}


async def finish(env, store, owner, invoke):
    require(owner and owner.get("webhook_sync") and owner.get("webhook_armed")
            and owner["stage"] == "working", "callback_phase")
    token = await google.get_google_access_token(env, StateStore(google._DeltaEnv(env)))
    require(token, "token_required")
    from e2e_all_http_probe import dispatch
    await dispatch(env, store, owner, token, invoke)
    owner["stage"] = "ready"
    await google._save(store, owner)
    return {"ok": True, "dirty": True}


async def callback(env, store, request):
    owner = await store.get_e2e_manifest("google_sync")
    if not owner or not owner.get("dirty") or not owner.get("webhook_sync"):
        return None
    cid, rid, msg, kind = [_header(request, key) for key in ("X-Goog-Channel-ID", "X-Goog-Resource-ID", "X-Goog-Message-Number", "X-Goog-Resource-State")]
    watch = next((w for w in owner["watches"] if w["channel_id"] == cid and not w["stopped"]), None)
    if not watch:
        return Response("", status=404)
    if owner["watch_url_sha256"] != digest(env.GCAL_WEBHOOK_URL):
        return Response("", status=409)
    await rpc(store, owner, channel_id=cid, resource_id=rid, message_number=msg, resource_state=kind, dedupe=True)
    if kind == "sync" or not owner.get("webhook_armed") or owner["stage"] != "working":
        return Response("", status=204)

    async def invoke(run_env, _state, _fetcher, **kwargs):
        return await HttpApplication(run_env).fetch(request)

    # triggerのHTTPがcontrol lockを解放するまで待つ。書込み段階自体は再送しない。
    for _ in range(20):
        result = await google.run_google_sync_probe(env, store, owner["run_id"], "webhook_advance", invoke)
        if result.get("error") != "google_sync_busy":
            return Response("", status=204 if result.get("ok") else 503)
        await asyncio.sleep(0.5)
    return Response("", status=503)


async def dispatch_webhook(env, store, owner, run_env, kv, invoke):
    response = await invoke(run_env, None, None, normal_http=True)
    require(response.status == 204, "dispatch_failed")
    result = (await StateStore(run_env).get_json("result:sync_all", {})) or {}
    require(result.get("payload", {}).get("ok") is True, "result_failed")
    before, epoch = dict(kv.hashes), await store.get_sync_last_epoch()
    duplicate = await invoke(run_env, None, None, normal_http=True)
    require(duplicate.status == 204 and kv.hashes == before and await store.get_sync_last_epoch() == epoch, "duplicate_applied")
    owner["webhook_armed"] = False
    owner["stages"][f"watch_shared_callback_{owner['step']}"] = 204
    owner["stages"][f"watch_shared_duplicate_{owner['step']}"] = 204
    return result["payload"]


async def verify_watch(env, store, owner):
    kv = GoogleKV(store, owner)
    for key in WATCH_KEYS:
        await kv.get(key)
    observations = (await rpc(store, owner))["observations"]
    # token変更中のwatchは専用入口の通常tokenで拒否するため、最終watchを確認する。
    latest = owner["watches"][-1]
    if not observations.get(latest["channel_id"], {}).get("sync"):
        raise GoogleStateError("google_sync_not_ready")
    if owner["step"]:
        require(owner["stages"].get(f"watch_shared_callback_{owner['step']}") == 204, "callback_missing")
    owner["stages"][f"watch_shared_step_{owner['step']}"] = 200


async def cleanup_watches(env, store, owner, token):
    require(owner["watch_url_sha256"] == digest(env.GCAL_WEBHOOK_URL), "cleanup_target")
    for watch in owner["watches"]:
        await stop(env, store, owner, watch, token)
    observations = (await rpc(store, owner))["observations"]
    for cid, record in observations.items():
        for msg in record["messages"]:
            await store.clear_e2e_google_message_seen(cid, msg, owner["run_id"])
    await rpc(store, owner, clear=True)
    owner["stages"]["watch_shared_cleanup"] = 200
    await google._save(store, owner)


async def retry_controls(env, store, owner, run_env, kv):
    """実行中通知とAPI拒否後の再送を観測する。通常コードの結果を改善せず記録する。"""
    watch = owner["watches"][-1]
    app = HttpApplication(run_env)
    before_epoch = await store.get_sync_last_epoch()
    for number, case in (("900000001", "busy"), ("900000002", "failure")):
        await rpc(store, owner, channel_id=watch["channel_id"], resource_id=watch["resource_id"],
                  message_number=number, resource_state="exists", dedupe=True)
        request = SimpleNamespace(url=env.GCAL_WEBHOOK_URL, method="POST", headers={
            "X-Goog-Channel-Token": env.GCAL_WEBHOOK_TOKEN, "X-Goog-Channel-ID": watch["channel_id"],
            "X-Goog-Resource-ID": watch["resource_id"], "X-Goog-Message-Number": number,
            "X-Goog-Resource-State": "exists",
        })
        lock = None
        if case == "busy":
            lock = await app._acquire_sync_lock(source="e2e-watch-contention")
            require(lock.get("ok") and lock.get("owner"), "control_lock_failed")
        else:
            # 不正な固定bearerでGoogle APIの認証拒否を起こす。token値は証跡に残さない。
            run_env.GOOGLE_API_BEARER_TOKEN = "e2e-invalid-bearer"
            run_env.SYNC_ALL_INCLUDE_DISCORD_NOTION = "false"
        try:
            response = await app.fetch(request)
            require(response.status == (204 if case == "busy" else 500), "control_response_" + case)
            if case == "failure":
                result = (await StateStore(run_env).get_json("result:sync_all", {})) or {}
                require(result.get("payload", {}).get("google_ok") is False, "api_rejection_missing")
        finally:
            if lock:
                await app._release_sync_lock(lock["owner"])
            if case == "failure":
                del run_env.GOOGLE_API_BEARER_TOKEN
                del run_env.SYNC_ALL_INCLUDE_DISCORD_NOTION
        hashes = dict(kv.hashes)
        response = await app.fetch(request)
        require(response.status == 204 and kv.hashes == hashes
                and await store.get_sync_last_epoch() == before_epoch, "retry_behavior_changed_" + case)
        owner["stages"]["watch_shared_" + case + "_retry_lost"] = 409
    await google._save(store, owner)


async def reclaim_expired_lock(env, store, owner, run_id):
    require(owner["run_id"] == run_id and owner["target_fingerprints"] == google._targets(env), "cleanup_owner")
    lock = await google._lock_state(store)
    if not lock.get("owner"):
        return
    from uuid import uuid4
    claimant = f"e2e-watch-recovery:{run_id}:{uuid4().hex}"
    stub = store._sync_do_stub(env.SYNC_COORDINATOR)
    # 有効期限の判定はDOの通常acquireへ任せる。ownerなしreleaseは使わない。
    acquired = await store._sync_do_rpc(stub, "acquire", {"owner": claimant, "ttl_seconds": 60})
    require(acquired and acquired.get("ok"), "cleanup_active_lock")
    released = await store._sync_do_rpc(stub, "release", {"owner": claimant})
    require(released and released.get("ok"), "cleanup_lock_release")
