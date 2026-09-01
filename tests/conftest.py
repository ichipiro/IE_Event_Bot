"""Cloudflare Python Workers の最小ローカルランタイムを用意する。"""

import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


SOURCE_DIR = Path(__file__).resolve().parents[1] / "workers" / "src"
sys.path.insert(0, str(SOURCE_DIR))


class Response:
    """テストで必要な Workers Response の最小実装。"""

    def __init__(
        self,
        body: Any = "",
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._body = body
        self.status = status
        self.headers = headers or {}

    async def text(self) -> str:
        if isinstance(self._body, bytes):
            return self._body.decode("utf-8")
        return str(self._body or "")

    async def json(self) -> Any:
        return json.loads(await self.text())


class WorkerEntrypoint:
    """本番クラスを import するための基底クラス。"""

    env: Any


class DurableObject:
    """本番クラスを import するための基底クラス。"""

    ctx: Any
    env: Any


async def blocked_fetch(url: str, *args: Any, **kwargs: Any) -> Any:
    """意図しない外部 API 接続をテスト失敗にする。"""
    raise AssertionError(f"外部通信は禁止されています: {url}")


workers_runtime = ModuleType("workers")
setattr(workers_runtime, "Response", Response)
setattr(workers_runtime, "WorkerEntrypoint", WorkerEntrypoint)
setattr(workers_runtime, "DurableObject", DurableObject)
setattr(workers_runtime, "fetch", blocked_fetch)
sys.modules["workers"] = workers_runtime
