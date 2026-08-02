"""CLI: exercise ontology and pipeline functionality."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

from .schema import initialize_database
from .store import OntologyStore, TypeRegistry, build_stub_summary
from .types import EntityType, LinkType


class DefaultTypeRegistry:
    def get_entity_types(self) -> list[EntityType]:
        return []

    def get_entity_type(self, id: str) -> EntityType | None:
        return None

    def get_link_types(self) -> list[LinkType]:
        return []

    def get_link_type(self, id: str) -> LinkType | None:
        return None


def _get_home(args: argparse.Namespace) -> str:
    home = getattr(args, "home", None) or os.path.expanduser("~/.ontology-core")
    os.makedirs(home, exist_ok=True)
    return home


def _get_store(home: str) -> OntologyStore:
    db_path = os.path.join(home, "ontology.db")
    db = sqlite3.connect(db_path)
    initialize_database(db)
    return OntologyStore(db, DefaultTypeRegistry())


def cmd_init(args: argparse.Namespace) -> None:
    home = _get_home(args)
    _get_store(home)
    print(f"Initialized ontology at: {home}")


def cmd_entity_list(args: argparse.Namespace) -> None:
    from .types import EntityFilter

    home = _get_home(args)
    store = _get_store(home)
    entities = store.query_entities(EntityFilter(
        entity_type=args.type,
        limit=args.limit,
    ))
    for e in entities:
        print(f"  [{e.entity_type}] {e.name} (id={e.id[:8]}...)")
    if not entities:
        print("  (no entities found)")


def cmd_entity_get(args: argparse.Namespace) -> None:
    home = _get_home(args)
    store = _get_store(home)
    entity = store.get_entity(args.id)
    if not entity:
        print(f"Entity not found: {args.id}")
        sys.exit(1)
    print(json.dumps({
        "id": entity.id,
        "type": entity.entity_type,
        "name": entity.name,
        "properties": entity.properties,
        "summary": entity.summary,
        "created_at": entity.created_at,
        "updated_at": entity.updated_at,
    }, indent=2))


def cmd_link_list(args: argparse.Namespace) -> None:
    home = _get_home(args)
    store = _get_store(home)
    links = store.get_all_links()
    for link in links[:args.limit]:
        print(f"  {link.from_entity[:8]}... --[{link.relationship_type}]--> {link.to_entity[:8]}...")
    if not links:
        print("  (no links found)")


def main() -> None:
    parser = argparse.ArgumentParser(prog="ontology", description="Ontology Core CLI")
    parser.add_argument("--home", help="Data directory (default: ~/.ontology-core)")

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("init", help="Initialize a new home directory")

    entity_parser = sub.add_parser("entity", help="Entity operations")
    entity_sub = entity_parser.add_subparsers(dest="entity_command")

    entity_list = entity_sub.add_parser("list", help="List entities")
    entity_list.add_argument("--type", help="Filter by entity type")
    entity_list.add_argument("--limit", type=int, default=20, help="Max results")

    entity_get = entity_sub.add_parser("get", help="Get entity by ID")
    entity_get.add_argument("id", help="Entity ID")

    link_parser = sub.add_parser("link", help="Link operations")
    link_sub = link_parser.add_subparsers(dest="link_command")

    link_list = link_sub.add_parser("list", help="List links")
    link_list.add_argument("--limit", type=int, default=20, help="Max results")

    args = parser.parse_args()

    if args.command == "init":
        cmd_init(args)
    elif args.command == "entity":
        if args.entity_command == "list":
            cmd_entity_list(args)
        elif args.entity_command == "get":
            cmd_entity_get(args)
        else:
            entity_parser.print_help()
    elif args.command == "link":
        if args.link_command == "list":
            cmd_link_list(args)
        else:
            link_parser.print_help()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
