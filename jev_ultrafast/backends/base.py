"""The small interface shared by browser backends."""

from __future__ import annotations

from typing import Any, Protocol


class BrowserBackend(Protocol):
    """The browser surface consumed by the Jev loop."""

    backend_name: str

    def observe(self, screenshot: bool = False) -> dict[str, Any]: ...

    def fresh(self, page: dict[str, Any], action: dict[str, Any] | None = None) -> bool: ...

    def act(self, action: dict[str, Any], page: dict[str, Any], text: str | None = None) -> Any: ...

    def close(self) -> None: ...
