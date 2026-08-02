"""Tests for ActionRegistry."""

import pytest

from ontology import ActionRegistry, ActionResult
from ontology.types import Entity


@pytest.fixture
def registry():
    return ActionRegistry()


@pytest.mark.asyncio
async def test_register_and_execute(registry: ActionRegistry):
    async def resolver(entity: Entity, params=None) -> ActionResult:
        return ActionResult(type="text", content=f"Hello, {entity.name}")

    registry.register("greet", resolver)
    assert registry.has_resolver("greet")

    entity = Entity(id="1", entity_type="person", name="Alice", properties={}, created_at="", updated_at="")
    result = await registry.execute("greet", entity)
    assert result.type == "text"
    assert result.content == "Hello, Alice"


@pytest.mark.asyncio
async def test_execute_missing_raises(registry: ActionRegistry):
    entity = Entity(id="1", entity_type="person", name="Alice", properties={}, created_at="", updated_at="")
    with pytest.raises(KeyError):
        await registry.execute("nonexistent", entity)


def test_list_registered(registry: ActionRegistry):
    async def noop(e, p=None):
        return ActionResult(type="text", content="")

    registry.register("a", noop)
    registry.register("b", noop)
    assert set(registry.list_registered()) == {"a", "b"}
