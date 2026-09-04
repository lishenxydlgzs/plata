"""Nightly maintenance job — agentic loop that inspects and improves the ontology.

Runs at a configured time (default midnight) when interactive usage is low.
Uses LLM calls iteratively to identify and fix issues in the knowledge graph:
  - Merge duplicate facts
  - Normalize spelling inconsistencies
  - Link related entities
  - Prune noise

Budget-capped to avoid exhausting the free-tier RPM limit.
"""

import asyncio
import json
import logging
from datetime import datetime, time, timezone
from typing import Any

from google import genai
from google.genai import types

from .knowledge import KnowledgeStore
from .llm import generate_content_with_fallback

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 5
RUN_AT_HOUR = 0  # midnight local time

MAINTENANCE_PROMPT = """\
You are a knowledge graph maintenance agent. Your job is to inspect the ontology \
and propose improvements. You will be shown the current state of the graph and should \
return structured actions to improve it.

Current facts in the knowledge graph:
{facts}

Current topics:
{topics}

Look for these issues and propose fixes:
1. DUPLICATE facts that say the same thing differently — merge them into one canonical fact
2. SPELLING inconsistencies — normalize names to one spelling (e.g. "Renel" vs "Renelle")
3. AWKWARD phrasing — improve a fact's wording while keeping its meaning

For each issue found, return an action. Available actions:
- merge: combine two facts into one (keep the better phrasing, moves evidence to the kept fact)
- update: change a fact's display name to fix spelling or improve phrasing

Respond with JSON:
{{"actions": [...], "done": true/false}}

Each action is one of:
- {{"type": "merge", "keep_id": "id_to_keep", "remove_id": "id_to_remove", "new_name": "merged phrasing"}}
- {{"type": "update", "id": "fact_id", "new_name": "corrected phrasing"}}

IMPORTANT RULES:
- NEVER delete facts. Every fact represents something a family member explicitly told us. \
Even facts that look isolated or low-value (a pet's name, how many kids, a nickname) are important \
memories. If a fact is not a duplicate and not misspelled, leave it alone.
- Do NOT invent new facts.
- Only use "new_name" in update actions — do not touch the structured properties.

Set "done" to true if there are no more improvements to make. \
Set "done" to false if you made changes and want another pass to check for more issues. \
If nothing needs fixing, return {{"actions": [], "done": true}}.

Be conservative — when in doubt, do nothing.\
"""


class MaintenanceJob:
    def __init__(self, knowledge: KnowledgeStore) -> None:
        self._knowledge = knowledge
        self._running = False
        self._task: asyncio.Task | None = None

    def start_scheduler(self) -> None:
        """Start the background scheduler that runs maintenance at the configured hour."""
        if self._task:
            return
        self._task = asyncio.ensure_future(self._schedule_loop())
        logger.info("Maintenance scheduler started (runs at %02d:00)", RUN_AT_HOUR)

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    async def run_now(self) -> dict[str, Any]:
        """Run the maintenance job immediately. Returns a summary."""
        return await self._run()

    async def _schedule_loop(self) -> None:
        """Wait until the target hour each day, then run."""
        while True:
            now = datetime.now()
            target = datetime.combine(now.date(), time(hour=RUN_AT_HOUR))
            if now >= target:
                from datetime import timedelta
                target += timedelta(days=1)
            wait_seconds = (target - now).total_seconds()
            logger.info("Maintenance: next run in %.0f seconds (at %s)", wait_seconds, target)
            await asyncio.sleep(wait_seconds)
            try:
                await self._run()
            except Exception:
                logger.exception("Maintenance job failed")

    async def _run(self) -> dict[str, Any]:
        """Execute the agentic maintenance loop."""
        logger.info("Maintenance: starting")
        total_actions = 0

        for iteration in range(MAX_ITERATIONS):
            snapshot = self._build_snapshot()
            if not snapshot["facts"]:
                logger.info("Maintenance: no facts to process, done")
                break

            prompt = MAINTENANCE_PROMPT.format(
                facts=snapshot["facts_text"],
                topics=snapshot["topics_text"],
            )

            try:
                result = await self._call_llm(prompt)
            except Exception:
                logger.exception("Maintenance: LLM call failed at iteration %d", iteration + 1)
                break

            actions = result.get("actions", [])
            done = result.get("done", True)

            logger.info("Maintenance: iteration %d — %d actions, done=%s", iteration + 1, len(actions), done)

            for action in actions:
                self._execute_action(action)
                total_actions += 1

            if done or not actions:
                break

        logger.info("Maintenance: complete — %d total actions across %d iterations", total_actions, iteration + 1)
        return {"iterations": iteration + 1, "total_actions": total_actions}

    async def _call_llm(self, system_prompt: str) -> dict:
        """Call LLM with higher token limits for maintenance tasks."""
        response = await generate_content_with_fallback(
            contents=[
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text="Inspect the knowledge graph and propose improvements.")],
                )
            ],
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                max_output_tokens=1000,
                temperature=0,
                response_mime_type="application/json",
            ),
        )
        text = response.text
        if not text:
            return {"actions": [], "done": True}
        return json.loads(text)

    def _build_snapshot(self) -> dict[str, Any]:
        """Build a text snapshot of the current ontology state for the LLM."""
        store = self._knowledge.store
        db = store._db

        # Get all facts with evidence counts
        fact_rows = db.execute("""
            SELECT f.id, f.name, f.properties,
                   COUNT(l.id) as evidence_count
            FROM entities f
            LEFT JOIN links l ON l.to_entity = f.id AND l.relationship_type = 'supports'
            WHERE f.entity_type = 'fact'
            GROUP BY f.id
            ORDER BY evidence_count DESC
        """).fetchall()

        facts = []
        facts_lines = []
        for row in fact_rows:
            props = json.loads(row["properties"])
            facts.append({
                "id": row["id"],
                "name": row["name"],
                "confidence": props.get("confidence", 0),
                "evidence_count": row["evidence_count"],
                "subject": props.get("subject", ""),
                "relation": props.get("relation", ""),
                "object": props.get("object", ""),
            })
            facts_lines.append(
                f"- [{row['id'][:8]}] \"{row['name']}\" "
                f"(subject={props.get('subject')}, relation={props.get('relation')}, object={props.get('object')}, "
                f"confidence={props.get('confidence')}, evidence={row['evidence_count']})"
            )

        # Get topics
        topic_rows = db.execute("""
            SELECT t.name, COUNT(l.id) as mention_count
            FROM entities t
            LEFT JOIN links l ON l.to_entity = t.id AND l.relationship_type = 'mentions'
            WHERE t.entity_type = 'topic'
            GROUP BY t.id
            ORDER BY mention_count DESC
            LIMIT 20
        """).fetchall()
        topics_lines = [f"- \"{row['name']}\" (mentions: {row['mention_count']})" for row in topic_rows]

        return {
            "facts": facts,
            "facts_text": "\n".join(facts_lines) if facts_lines else "(no facts)",
            "topics_text": "\n".join(topics_lines) if topics_lines else "(no topics)",
        }

    def execute_action(self, action: dict[str, Any]) -> bool:
        """Apply one validated maintenance action. Returns whether it was applied."""
        store = self._knowledge.store
        action_type = action.get("type")

        if action_type == "merge":
            keep_id = action.get("keep_id", "")
            remove_id = action.get("remove_id", "")
            new_name = action.get("new_name")

            # Resolve short IDs to full IDs
            keep_id = self._resolve_id(keep_id)
            remove_id = self._resolve_id(remove_id)
            if not keep_id or not remove_id:
                logger.warning("Maintenance: merge failed — could not resolve IDs")
                return False

            # Move supports links from remove → keep
            links = store.get_entity_links(remove_id)
            for link in links:
                if link.relationship_type == "supports" and link.to_entity == remove_id:
                    store.create_link("supports", link.from_entity, keep_id)

            # Update name if provided
            if new_name:
                store.update_entity(keep_id, name=new_name)

            # Delete the duplicate
            store.delete_entity(remove_id)
            logger.info("Maintenance: merged %s into %s → %r", remove_id[:8], keep_id[:8], new_name)
            return True

        elif action_type == "update":
            fact_id = self._resolve_id(action.get("id", ""))
            if not fact_id:
                logger.warning("Maintenance: update failed — could not resolve ID")
                return False
            new_name = action.get("new_name")
            if not new_name:
                logger.warning("Maintenance: update skipped — no new_name provided")
                return False
            # Only update the display name — never touch structured properties,
            # which hold subject/relation/object/confidence.
            store.update_entity(fact_id, name=new_name)
            logger.info("Maintenance: updated %s → %r", fact_id[:8], new_name)
            return True

        else:
            logger.warning("Maintenance: unknown action type %r", action_type)
            return False

    def _execute_action(self, action: dict[str, Any]) -> None:
        """Backward-compatible internal action executor."""
        self.execute_action(action)

    def _resolve_id(self, short_id: str) -> str | None:
        """Resolve a short ID prefix to a full entity ID."""
        if not short_id:
            return None
        db = self._knowledge.store._db
        row = db.execute(
            "SELECT id FROM entities WHERE id LIKE ? AND entity_type = 'fact' LIMIT 1",
            (f"{short_id}%",),
        ).fetchone()
        return row["id"] if row else None
