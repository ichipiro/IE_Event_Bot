"""run専用の実DOで、解放RPCの前後に固定障害を注入する。"""

import json
from types import SimpleNamespace

from e2e_google_sync_state import GoogleStateError
from entry import Application
from state import StateStore

CASES = tuple(f"{scope}_{fault}" for scope in ("global", "control")
              for fault in ("before", "after", "exhausted", "replaced"))


def probe_name(run_id):
    from e2e_google_sync_probe import RUN_PATTERN
    if not RUN_PATTERN.fullmatch(run_id):
        raise GoogleStateError("release_probe_run_invalid")
    return "e2e:release-recovery:" + run_id


def require(ok):
    if not ok:
        raise GoogleStateError("release_probe_verification_failed")


class FaultNamespace:
    def __init__(self, env, run_id, case):
        self.namespace = env.SYNC_COORDINATOR
        self.name = probe_name(run_id)
        self.case = case
        self.scope, self.fault = case.split("_")
        self.owner = run_id + ":" + case
        self.replacement = self.owner + ":replacement"
        self.releases = 0
        self.reads = 0
        self.connections = 0

    def getByName(self, name):  # noqa: N802
        require(name == ("global" if self.scope == "global" else "e2e:google-sync-control"))
        self.connections += 1
        return FaultStub(self, self.namespace.getByName(self.name))


class FaultStub:
    def __init__(self, fault, real):
        self.fault = fault
        self.real = real

    async def sync_state(self, raw):
        body = json.loads(raw)
        action = body.pop("action")
        require(action in ("release", "status"))
        fault = self.fault
        if action == "status":
            fault.reads += 1
        else:
            require(body == {"owner": fault.owner})
            fault.releases += 1
            if fault.releases == 1 or fault.fault == "exhausted":
                if fault.fault in ("after", "replaced"):
                    require((await StateStore._sync_do_rpc(self.real, "release", body) or {}).get("ok"))
                if fault.fault == "replaced":
                    require((await StateStore._sync_do_rpc(self.real, "acquire", {
                        "owner": fault.replacement, "ttl_seconds": 60,
                    }) or {}).get("ok"))
                raise RuntimeError("e2e_injected_release_failure")
        return json.dumps(await StateStore._sync_do_rpc(self.real, action, body))


async def cleanup(env, run_id):
    from sync_lock_release import _recover_release
    name = probe_name(run_id)
    stub = env.SYNC_COORDINATOR.getByName(name)
    status = await StateStore._sync_do_rpc(stub, "status")
    require(status and status.get("ok") and isinstance(status.get("lock"), dict))
    owner = (status or {})["lock"].get("owner")
    if not owner:
        return
    allowed = {run_id + ":" + case + suffix for case in CASES for suffix in ("", ":replacement")}
    require(owner in allowed)
    result = await _recover_release(lambda: env.SYNC_COORDINATOR.getByName(name),
                                    StateStore._sync_do_rpc, owner, "release_probe_cleanup")
    require(result["ok"])
    status = await StateStore._sync_do_rpc(env.SYNC_COORDINATOR.getByName(name), "status")
    require(status and status.get("ok") and isinstance(status.get("lock"), dict)
            and not status["lock"].get("owner"))


async def run(env, store, owner):
    from e2e_google_sync_probe import _release_control, _save
    run_id = owner["run_id"]
    require(owner.get("dirty") and owner.get("webhook_sync"))
    for case in CASES:
        fault = FaultNamespace(env, run_id, case)
        real = env.SYNC_COORDINATOR.getByName(fault.name)
        acquired = await StateStore._sync_do_rpc(real, "acquire", {"owner": fault.owner, "ttl_seconds": 60})
        require(acquired and acquired.get("ok"))
        try:
            view = SimpleNamespace(SYNC_COORDINATOR=fault)
            if fault.scope == "global":
                await Application(view)._release_sync_lock(fault.owner)
            else:
                result = await _release_control(StateStore(view), fault.getByName("e2e:google-sync-control"), fault.owner)
                require((result is not None) == (fault.fault == "exhausted"))
            status = await StateStore._sync_do_rpc(env.SYNC_COORDINATOR.getByName(fault.name), "status")
            require(status and status.get("ok") and isinstance(status.get("lock"), dict))
            expected = fault.owner if fault.fault == "exhausted" else fault.replacement if fault.fault == "replaced" else ""
            require((status or {})["lock"].get("owner", "") == expected)
            releases, reads = (3, 3) if fault.fault == "exhausted" else (2, 2) if fault.fault == "before" else (1, 1)
            require((fault.releases, fault.reads, fault.connections) == (releases, reads, reads + 1))
            owner["stages"]["watch_shared_release_" + case] = 200
            await _save(store, owner)
            print(json.dumps({"event": "e2e_lock_release_probe_passed", "case": case, "run_id": run_id}))
        finally:
            await cleanup(env, run_id)
    owner["stages"]["watch_shared_release_recovery"] = 200
    await _save(store, owner)
