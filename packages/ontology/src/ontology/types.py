from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class PropertyDefinition:
    type: Literal["string", "number", "boolean", "date", "reference"]
    required: bool
    description: str
    reference_type: str | None = None


@dataclass
class FacetDefinition:
    properties: dict[str, PropertyDefinition]
    description: str | None = None


@dataclass
class EntityType:
    id: str
    name: str
    properties: dict[str, PropertyDefinition]
    system_defined: bool
    created_at: str
    updated_at: str
    description: str | None = None
    icon: str | None = None
    color: str | None = None
    facets: list[str] | None = None
    kind: Literal["entity", "view"] | None = None
    metadata: dict[str, Any] | None = None
    indexable_fields: list[str] | None = None


@dataclass
class LinkType:
    id: str
    name: str
    from_entity_type: str
    to_entity_type: str
    bidirectional: bool
    created_at: str
    description: str | None = None
    from_facet: str | None = None
    to_facet: str | None = None
    reverse_name: str | None = None


@dataclass
class Entity:
    id: str
    entity_type: str
    name: str
    properties: dict[str, Any]
    created_at: str
    updated_at: str
    summary: str | None = None


@dataclass
class EntityIdentifier:
    entity_id: str
    system: str
    external_id: str
    created_at: str


@dataclass
class Link:
    id: str
    relationship_type: str
    from_entity: str
    to_entity: str
    created_at: str
    properties: dict[str, Any] | None = None


@dataclass
class EntityFilter:
    entity_type: str | None = None
    name_contains: str | None = None
    limit: int | None = None
    offset: int | None = None


@dataclass
class EntityGraph:
    entities: list[Entity]
    links: list[Link]
    entity_types: dict[str, EntityType]
    relationship_types: dict[str, LinkType]


@dataclass
class ActionGuard:
    property: str
    equals: str | None = None
    in_values: list[str] | None = None


@dataclass
class ActionParamDef:
    type: Literal["string", "number", "boolean", "enum", "object", "entity_select"]
    enum: list[str] | None = None
    properties: dict[str, dict[str, Any]] | None = None
    facet: str | None = None
    label: str | None = None
    description: str | None = None
    required: bool | None = None


@dataclass
class ActionType:
    id: str
    name: str
    applicable_to: list[str] | Literal["*"]
    description: str | None = None
    icon: str | None = None
    guards: list[ActionGuard] | None = None
    params: dict[str, ActionParamDef] | None = None


@dataclass
class OntologySchema:
    object_types: list[dict[str, Any]]
    relationships: list[dict[str, Any]]
    facets: dict[str, FacetDefinition] | None = None
    actions: list[dict[str, Any]] | None = None
