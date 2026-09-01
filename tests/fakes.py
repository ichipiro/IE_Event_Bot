"""Workers KV、Durable Object、HTTP リクエストのテスト用実装。"""

import json
from types import SimpleNamespace
from typing import Any


class Headers:
    """大文字小文字を区別せずヘッダーを取得する。"""

    def __init__(self, values: dict[str, str] | None = None) -> None:
        self._values = {str(key).lower(): value for key, value in (values or {}).items()}

    def get(self, name: str, default: Any = None) -> Any:
        return self._values.get(str(name).lower(), default)


class Request:
    """Worker と Durable Object の fetch に渡す最小リクエスト。"""

    def __init__(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        body: str = "",
    ) -> None:
        self.url = url
        self.method = method
        self.headers = Headers(headers)
        self._body = body

    async def text(self) -> str:
        return self._body


class MemoryKV:
    """Workers KV の get / put をメモリ上で再現する。"""

    def __init__(self, initial: dict[str, str] | None = None) -> None:
        self.data = dict(initial or {})
        self.put_calls: list[tuple[str, str]] = []

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def put(self, key: str, value: str) -> None:
        text = str(value)
        self.data[key] = text
        self.put_calls.append((key, text))


class MemoryStorage:
    """Durable Object storage の get / put / delete を再現する。"""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    async def get(self, key: str) -> Any:
        return self.data.get(key)

    async def put(self, key: str, value: Any) -> None:
        self.data[key] = value

    async def delete(self, key: str) -> None:
        self.data.pop(key, None)


class DurableObjectStub:
    """URL と options を Request へ変換して Durable Object を呼ぶ。"""

    def __init__(self, durable_object: Any) -> None:
        self.durable_object = durable_object
        self.calls: list[dict[str, Any]] = []

    async def fetch(
        self,
        url: str,
        options: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        request_options = dict(options or {})
        request_options.update(kwargs)
        self.calls.append({"url": url, **request_options})
        request = Request(
            url,
            method=str(request_options.get("method") or "GET"),
            headers=request_options.get("headers"),
            body=str(request_options.get("body") or ""),
        )
        return await self.durable_object.fetch(request)

    async def sync_state(self, payload_json: str) -> str:
        self.calls.append({"rpc": "sync_state", "payload_json": payload_json})
        return await self.durable_object.sync_state(payload_json)


class DurableObjectNamespace:
    """名前付き Durable Object stub を1件だけ返す namespace。"""

    def __init__(self, durable_object: Any) -> None:
        self.stub = DurableObjectStub(durable_object)
        self.requested_names: list[str] = []

    def get_by_name(self, name: str) -> DurableObjectStub:
        self.requested_names.append(name)
        return self.stub

    def getByName(self, name: str) -> DurableObjectStub:  # noqa: N802
        return self.get_by_name(name)


class CamelCaseDurableObjectNamespace:
    """実環境と同じ getByName のみを公開する namespace。"""

    def __init__(self, durable_object: Any) -> None:
        self.stub = DurableObjectStub(durable_object)
        self.requested_names: list[str] = []

    def getByName(self, name: str) -> DurableObjectStub:  # noqa: N802
        self.requested_names.append(name)
        return self.stub


class JavaScriptProxyDurableObjectNamespace(CamelCaseDurableObjectNamespace):
    """未定義のPython化メソッドも属性として見せるJS Proxy代替。"""

    def __getattr__(self, name: str) -> object:
        if name == "get_by_name":
            return object()
        raise AttributeError(name)


class FetchTypeErrorDurableObjectStub(DurableObjectStub):
    """fetchのRequestInit変換だけが失敗する実環境stub代替。"""

    async def fetch(
        self,
        url: str,
        options: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        raise TypeError("RequestInit conversion failed")

class FetchTypeErrorDurableObjectNamespace(CamelCaseDurableObjectNamespace):
    """fetchは失敗するがRPCを利用できるDurable Object namespace。"""

    def __init__(self, durable_object: Any) -> None:
        self.stub = FetchTypeErrorDurableObjectStub(durable_object)
        self.requested_names: list[str] = []


class NestedAwaitableDurableObjectStub(DurableObjectStub):
    """RPCラッパーがawaitableを一段追加する実ランタイム代替。"""

    async def sync_state(self, payload_json: str):
        return super().sync_state(payload_json)


class NestedAwaitableDurableObjectNamespace(CamelCaseDurableObjectNamespace):
    """ネストしたawaitableを返すDurable Object namespace。"""

    def __init__(self, durable_object: Any) -> None:
        self.stub = NestedAwaitableDurableObjectStub(durable_object)
        self.requested_names: list[str] = []


def make_durable_object(instance: Any) -> Any:
    """Durable Object にメモリ storage を割り当てる。"""
    instance.ctx = SimpleNamespace(storage=MemoryStorage())
    instance.env = SimpleNamespace()
    return instance


def make_sync_coordinator_namespace(
    initial_manifests: dict[str, dict] | None = None,
) -> CamelCaseDurableObjectNamespace:
    """E2E manifest を任意に初期化した SyncCoordinator namespace を返す。"""
    from sync_lock_do import SyncCoordinator

    coordinator = make_durable_object(SyncCoordinator())
    for service, manifest in (initial_manifests or {}).items():
        coordinator.ctx.storage.data[f"e2e:manifest:{service}"] = json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    return CamelCaseDurableObjectNamespace(coordinator)
