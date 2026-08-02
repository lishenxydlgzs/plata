"""ActionRegistry: register and execute action resolvers on entities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal

from .types import Entity


@dataclass
class ActionResult:
    type: Literal["html", "text", "json"]
    content: str


ActionResolver = Callable[[Entity, dict[str, Any] | None], Awaitable[ActionResult]]


class ActionRegistry:
    def __init__(self) -> None:
        self._resolvers: dict[str, ActionResolver] = {}

    def register(self, action_id: str, resolver: ActionResolver) -> None:
        self._resolvers[action_id] = resolver

    async def execute(self, action_id: str, entity: Entity, params: dict[str, Any] | None = None) -> ActionResult:
        resolver = self._resolvers.get(action_id)
        if not resolver:
            raise KeyError(f"No resolver registered for action: {action_id}")
        return await resolver(entity, params)

    def has_resolver(self, action_id: str) -> bool:
        return action_id in self._resolvers

    def list_registered(self) -> list[str]:
        return list(self._resolvers.keys())
