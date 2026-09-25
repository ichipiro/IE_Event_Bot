"""実Cronと別HTTPの共通同期ロックをrun専用DO・KVで検査する。"""

import asyncio
import json
import time
from hashlib import sha256
from urllib.parse import urlparse

from workers import Response

from e2e_cron_entry import CRON_FLAGS, Default as DeliveryWorker
from entry import Application
from state import StateStore


class OwnedState(StateStore):
    writes = 0

    def key(self, key):
        if key != "result:sync_discord_notion":
            raise RuntimeError("cron_unexpected_state_key")
        return f"e2e:real-cron:{self.env.E2E_CRON_RUN_ID}:result"

    async def get_text(self, key):
        return await StateStore.get_text(self, self.key(key))

    async def put_text(self, key, value):
        await StateStore.put_text(self, self.key(key), value)
        self.writes += 1


class ProbeApplication(Application):
    def __init__(self, env, mode):
        super().__init__(env)
        self.mode = mode
        self.calls = 0
        self.writes = 0
        self.status = 0
        self.body_owner = ""

    def _get_sync_stub(self, do_ns):
        return do_ns.getByName(f"e2e:real-cron:{self.env.E2E_CRON_RUN_ID}")

    async def _run_discord_sync(self, state, *, source, **kwargs):
        owned = OwnedState(self.env)

        async def body(env, state):
            self.calls += 1
            self.body_owner = (await lock_status(self))["owner_sha256"]
            await asyncio.sleep({"cron": 35, "hold": 85}.get(self.mode, 0))
            return {"ok": True, "probe_mode": self.mode,
                    "run_id": str(self.env.E2E_CRON_RUN_ID),
                    "version_id": str(self.env.CF_VERSION_METADATA.id),
                    "version_tag": str(self.env.CF_VERSION_METADATA.tag),
                    "commit": str(self.env.E2E_CRON_COMMIT)}

        result, status = await super().__getattr__("_run_discord_sync")(
            owned, source=source, poll_runner=body, lock_ttl_seconds=150,
        )
        self.writes = owned.writes
        self.status = status
        return result, status


def identity(env):
    return {"run_id": str(env.E2E_CRON_RUN_ID),
            "version_id": str(env.CF_VERSION_METADATA.id),
            "version_tag": str(env.CF_VERSION_METADATA.tag),
            "commit": str(env.E2E_CRON_COMMIT)}


def scoped(env):
    import re
    run = str(getattr(env, "E2E_CRON_RUN_ID", ""))
    return (re.fullmatch(r"E2E-\d{8}T\d{6}Z-[a-f0-9]{8}", run)
            and str(env.CF_VERSION_METADATA.tag) == run
            and 0 < int(env.E2E_CRON_DEADLINE_MS) - int(env.E2E_CRON_START_MS) <= 1_200_000
            and all(str(getattr(env, flag, "")).lower() == (
                "true" if flag == "CRON_ENABLE_DISCORD_NOTION_SYNC" else "false"
            ) for flag in CRON_FLAGS)
            and getattr(env, "SYNC_COORDINATOR", None) is not None
            and str(getattr(env, "SYNC_DO_LOCK_ENABLED", "")).lower() == "true")


async def lock_status(app):
    status = await app._sync_lock_status()
    if status.get("ok") is not True:
        raise RuntimeError("cron_lock_status_failed")
    lock = status["lock"]
    owner = str(lock.get("owner") or "")
    return {"owner_sha256": sha256(owner.encode()).hexdigest() if owner else "",
            "holder": "cron" if owner.startswith("cron-") else "manual" if owner else "",
            "locked": bool(owner), "expires_at": float(lock.get("expires_at") or 0),
            "now": float(lock["now"])}


class Default(DeliveryWorker):
    async def fetch(self, request):
        path = urlparse(str(request.url)).path
        if path not in ("/admin/cron-lock", "/sync/discord-notion"):
            return Response("not_found", status=404)
        app = ProbeApplication(self.env, "probe")
        if not app._authorized(request):
            return Response("unauthorized", status=401)
        if not scoped(self.env) or request.headers.get("X-E2E-Run-ID") != str(self.env.E2E_CRON_RUN_ID):
            return Response("scope_mismatch", status=409)
        if path == "/admin/cron-lock" and request.method == "GET":
            return Response(json.dumps({**identity(self.env), **await lock_status(app)}))
        if path != "/sync/discord-notion" or request.method != "POST":
            return Response("method_not_allowed", status=405)
        now = int(time.time() * 1000)
        if not int(self.env.E2E_CRON_START_MS) <= now < int(self.env.E2E_CRON_DEADLINE_MS) - 90_000:
            return Response("expired", status=409)
        mode = request.headers.get("X-E2E-Probe-Mode")
        if mode not in ("probe", "hold", "after"):
            return Response("invalid_mode", status=400)
        app.mode = mode
        before = await lock_status(app)
        if mode == "probe" and (before["holder"] != "cron" or before["owner_sha256"] != request.headers.get("X-E2E-Owner")):
            return Response("owner_changed", status=412)
        if mode != "probe" and before["locked"]:
            return Response("owner_changed", status=412)
        response = await app.fetch(request)
        after = await lock_status(app)
        return Response(json.dumps({**identity(self.env), "mode": mode,
            "status": int(response.status), "body_owner": app.body_owner, "body_calls": app.calls, "result_writes": app.writes,
            "before": before, "after": after}), status=int(response.status))

    async def scheduled(self, controller, env, ctx):
        now = int(time.time() * 1000)
        scheduled = int(controller.scheduledTime)
        if (not scoped(self.env) or str(controller.cron) != "* * * * *"
                or not int(self.env.E2E_CRON_START_MS) <= scheduled <= now < int(self.env.E2E_CRON_DEADLINE_MS) - 40_000):
            return
        app = ProbeApplication(self.env, "cron")
        before = await lock_status(app)
        results = await app.scheduled(controller, env, ctx)
        after = await lock_status(app)
        if len(results) != 1:
            raise RuntimeError("cron_unexpected_job_execution")
        receipt = {**identity(self.env), "source": "scheduled", "cron": str(controller.cron),
                   "scheduled_time_ms": scheduled, "observed_time_ms": now,
                   "completed_time_ms": int(time.time() * 1000),
                   "status": app.status, "body_owner": app.body_owner, "body_calls": app.calls, "result_writes": app.writes,
                   "before": before, "after": after}
        await self.env.STATE_KV.put(
            f"e2e:real-cron:{self.env.E2E_CRON_RUN_ID}:{scheduled}", json.dumps(receipt),
        )
