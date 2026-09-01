"""Parent-directed, persisted maintenance conversations for the knowledge graph."""

from typing import Any

from .context import ConversationDB
from .knowledge import KnowledgeStore
from .llm import generate_chat_json
from .maintenance import MaintenanceJob

REVIEW_PROMPT = """\
You assist a parent who is reviewing their family's knowledge graph. The parent can
ask questions about the graph and request the same maintenance actions that run at
night: merge genuinely duplicate facts or improve a fact's display wording.

Current facts:
{facts}

Current topics:
{topics}

Return JSON exactly in this form:
{{"reply_text": "brief explanation of what you did or found", "actions": []}}

Actions may only be:
- {{"type": "merge", "keep_id": "fact ID", "remove_id": "fact ID", "new_name": "optional canonical wording"}}
- {{"type": "update", "id": "fact ID", "new_name": "corrected wording"}}

Only act when the parent explicitly asks you to make a change. Use IDs shown above.
Never delete a fact except as the remove_id of a confirmed duplicate merge. Do not
invent facts or modify structured properties. If a request is ambiguous, explain what
you need rather than returning an action. Be concise.\
"""


class GraphReviewService:
    def __init__(self, knowledge: KnowledgeStore, conversations: ConversationDB, maintenance: MaintenanceJob) -> None:
        self._knowledge = knowledge
        self._conversations = conversations
        self._maintenance = maintenance

    async def handle_message(self, session_id: str, text: str) -> dict[str, Any]:
        session = await self._conversations.get_graph_review_session(session_id)
        if not session:
            raise KeyError(session_id)
        await self._conversations.save_graph_review_message(session_id, "user", text)
        snapshot = self._maintenance._build_snapshot()
        result = await generate_chat_json(
            REVIEW_PROMPT.format(facts=snapshot["facts_text"], topics=snapshot["topics_text"]),
            await self._conversations.get_graph_review_history(session_id),
            text,
        )
        reply_text = str(result.get("reply_text") or "I couldn't determine a graph change.")
        raw_actions = result.get("actions") or []
        applied_actions = []
        for action in raw_actions:
            if not isinstance(action, dict) or action.get("type") not in {"merge", "update"}:
                continue
            applied = self._maintenance.execute_action(action)
            await self._conversations.save_graph_review_action(session_id, action, applied)
            applied_actions.append({"action": action, "applied": applied})
        await self._conversations.save_graph_review_message(session_id, "model", reply_text)
        return {"reply_text": reply_text, "actions": applied_actions}
