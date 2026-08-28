from typing import Any

class Response:
    status: int

    def __init__(
        self,
        body: Any = ...,
        *,
        status: int = ...,
        headers: dict[str, str] | None = ...,
    ) -> None: ...

    async def text(self) -> str: ...
    async def json(self) -> Any: ...

class WorkerEntrypoint:
    env: Any

class DurableObject:
    ctx: Any
    env: Any

async def fetch(url: str, options: dict[str, Any] | None = ..., **kwargs: Any) -> Any: ...
