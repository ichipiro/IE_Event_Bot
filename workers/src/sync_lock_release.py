"""所有者を指定したロック解放の、回数・時間を制限した復旧。"""

import asyncio
import json

_RPC_TIMEOUT_SECONDS = 2.0
_RECOVERY_DELAYS = (0.1, 0.2, 0.4)


def _release_retry_blocked(exc):
    # Python WorkersではJSの例外属性がjs_error側にある場合もある。
    try:
        for error in (exc, getattr(exc, "js_error", None)):
            if error is not None and (
                getattr(error, "overloaded", None) is True
                or getattr(error, "retryable", None) is False
            ):
                return True
        return "overloaded" in str(exc).lower()
    except Exception:
        # 例外proxyの読取りにも失敗した場合は、元の処理結果を壊さず打ち切る。
        return True


def _release_error_type(exc):
    name = type(exc).__name__
    return name if name in (
        "TimeoutError", "TypeError", "RuntimeError", "JsException",
        "ValueError", "JSONDecodeError",
    ) else "other"


async def _recover_release(make_stub, rpc, owner, scope, *, blocked=False):
    """別接続で確認し、自分のロックだけ再送する。最後の試行は読取り専用。"""
    result = {
        "ok": False, "status_ok": False, "owner_matches": None,
        "attempts": 0, "release_attempts": 0, "step": "retry_blocked",
        "error_type": "none",
    }
    # 空ownerでのreleaseは強制解除になるため、復旧経路では許可しない。
    blocked = blocked or not isinstance(owner, str) or not owner
    for attempt, delay in enumerate(_RECOVERY_DELAYS, 1):
        if blocked:
            break
        await asyncio.sleep(delay)
        result.update(attempts=attempt, status_ok=False, owner_matches=None, error_type="none")
        try:
            result["step"] = "get_stub"
            stub = make_stub()
            result["step"] = "status_rpc"
            status = await asyncio.wait_for(rpc(stub, "status"), _RPC_TIMEOUT_SECONDS)
            result["step"] = "status_response"
            if not isinstance(status, dict) or status.get("ok") is not True:
                continue
            result["status_ok"] = True
            result["step"] = "lock_response"
            lock = status.get("lock")
            if not isinstance(lock, dict) or not isinstance(lock.get("owner", ""), str):
                continue
            result["owner_matches"] = lock.get("owner", "") == owner
            result["step"] = "owner_check"
            if not result["owner_matches"]:
                result["ok"] = True
                break
            if attempt == len(_RECOVERY_DELAYS):
                break
            result["step"] = "release_rpc"
            result["release_attempts"] += 1
            # 確認後にownerが交代しても、DO側のowner一致条件が新ロックを守る。
            await asyncio.wait_for(rpc(stub, "release", {"owner": owner}), _RPC_TIMEOUT_SECONDS)
            # 成功応答だけでは完了にせず、次の別接続で残留を確認する。
        except Exception as exc:
            result["error_type"] = _release_error_type(exc)
            blocked = _release_retry_blocked(exc)
    print(json.dumps({
        "event": "sync_lock_release_recovered" if result["ok"] else "sync_lock_release_recovery_failed",
        "level": "info" if result["ok"] else "error", "scope": scope,
        "attempts": result["attempts"], "release_attempts": result["release_attempts"],
        "stage": result["step"], "error_type": result["error_type"],
    }))
    return result
