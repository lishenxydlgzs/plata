"""Strands graph review with atomic, replayable ontology mutations."""
import asyncio
import json
from datetime import datetime, timezone
from typing import Literal

from ag_ui.core import EventType, CustomEvent, MessagesSnapshotEvent, RunAgentInput, UserMessage
from ag_ui_strands import StrandsAgent
from pydantic import BaseModel, ConfigDict, Field
from strands import tool

from .agent_runtime import ModelCallLimit, StagedToolGuard, make_agent
from .agent_stream import messages_for
from .graph_review import CorrectRecord, REVIEW_PROMPT


class GraphAction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    type: Literal['merge', 'update', 'correct_record']
    id: str | None = None
    keep_id: str | None = None
    remove_id: str | None = None
    new_name: str | None = Field(default=None, min_length=1, max_length=2000)
    corrected_text: str | None = Field(default=None, min_length=1, max_length=2000)
    person_id: str | None = None


class GraphBrowserReview:
    def __init__(self, service):
        self.service = service
        self.lock = asyncio.Lock()

    @property
    def db(self):
        return self.service._knowledge.store._db

    def setup(self):
        self.db.execute('''CREATE TABLE IF NOT EXISTS graph_review_turns (
            session_id TEXT NOT NULL, request_id TEXT NOT NULL, text TEXT NOT NULL,
            reply TEXT, actions TEXT, created_at TEXT NOT NULL,
            PRIMARY KEY(session_id,request_id))''')

    async def session(self, id):
        self.setup()
        legacy = await self.service._conversations.get_graph_review_session(id)
        if not legacy:
            raise KeyError(id)
        messages = [dict(m, id=f'legacy-{id}-{i}', request_id=f'legacy-{id}-{i}')
                    for i, m in enumerate(legacy['messages'])]
        actions = list(legacy['actions'])
        pending = None
        last_active = legacy['last_active_at']
        for row in self.db.execute('SELECT * FROM graph_review_turns WHERE session_id=? ORDER BY created_at,rowid', (id,)):
            last_active = max(last_active.replace(' ', 'T'), row['created_at'])
            user = {'id': row['request_id'], 'request_id': row['request_id'], 'role': 'user', 'text': row['text']}
            messages.append(user)
            if row['reply'] is None:
                pending = user
            else:
                changes = json.loads(row['actions'])
                messages.append({'id': f"{row['request_id']}-reply", 'request_id': row['request_id'],
                                 'role': 'assistant', 'text': row['reply'], 'actions': changes})
                actions.extend(changes)
        return dict(legacy, messages=messages, actions=actions, pending=pending, last_active_at=last_active,
                    title=(messages[0]['text'][:80] if messages else legacy['title']))

    async def sessions(self):
        db = self.service._conversations._db
        cursor = await db.execute('SELECT id FROM graph_review_sessions ORDER BY last_active_at DESC')
        result = []
        for (id,) in await cursor.fetchall():
            value = await self.session(id)
            if value['messages']:
                result.append({k: value[k] for k in ('id', 'title', 'started_at', 'last_active_at')})
        return sorted(result, key=lambda item: item['last_active_at'].replace(' ', 'T'), reverse=True)

    async def prepare(self, id, request):
        session = await self.session(id)
        row = self.db.execute('SELECT * FROM graph_review_turns WHERE session_id=? AND request_id=?', (id, request.request_id)).fetchone()
        if row and row['text'] != request.text:
            raise ValueError('That request ID belongs to a different message.')
        if row and row['reply'] is not None:
            return {'complete': True, 'session': session}
        if session['pending'] and session['pending']['request_id'] != request.request_id:
            raise ValueError('Retry the saved message before sending another.')
        if not row:
            if sum(len(m['text']) for m in session['messages']) + len(request.text) > 40000:
                raise ValueError('Start a new review to continue.')
            with self.db:
                self.db.execute('INSERT INTO graph_review_turns VALUES(?,?,?,NULL,NULL,?)',
                                (id, request.request_id, request.text, datetime.now(timezone.utc).isoformat()))
        snapshot = self.service._maintenance._build_snapshot()
        records = self.service._review_records(request.text, session['messages'])
        ids = {r['id'] for r in records} | {f['id'] for f in snapshot['facts']}
        versions = {id: self.service._knowledge.store.get_entity(id).updated_at for id in ids}
        return {'complete': False, 'history': session['messages'], 'snapshot': snapshot,
                'records': records, 'versions': versions}

    def validate(self, changes, prepared):
        if not 1 <= len(changes) <= 5:
            raise ValueError('Stage one to five changes.')
        targets = set()
        for action in changes:
            ids = [action.keep_id, action.remove_id] if action.type == 'merge' else [action.id]
            if action.type == 'merge' and action.keep_id == action.remove_id:
                raise ValueError('A fact cannot be merged into itself.')
            for id in ids:
                entity = self.service._knowledge.store.get_entity(id) if id else None
                if not entity or id not in prepared['versions'] or entity.updated_at != prepared['versions'][id]:
                    raise ValueError('The record is missing or changed. Start a fresh review.')
                if id in targets:
                    raise ValueError('A record can only be changed once per turn.')
                targets.add(id)
                allowed = {'message', 'kid_event', 'behavior_event', 'learning_event'} if action.type == 'correct_record' else {'fact'}
                if entity.entity_type not in allowed:
                    raise ValueError('That action does not apply to this record type.')
            if action.type == 'update' and not (action.new_name or '').strip():
                raise ValueError('A new display name is required.')
            if action.type == 'correct_record':
                CorrectRecord.model_validate(action.model_dump())
                if not (action.corrected_text or '').strip():
                    raise ValueError('A correction cannot be blank.')
                if action.person_id:
                    person = self.service._knowledge.store.get_entity(action.person_id)
                    if not person or person.entity_type != 'person':
                        raise ValueError('Select an existing person.')

    async def events(self, body, request, prepared, *, model=None):
        staged = []
        invalid = False

        @tool
        async def stage_graph_changes(changes: list[GraphAction]) -> dict:
            """Stage parent-requested graph corrections for atomic commit after your reply.

            Args:
                changes: One to five changes using exact full record IDs. update
                    needs id and new_name. merge needs keep_id and remove_id,
                    with optional new_name. correct_record needs id and
                    corrected_text, and optional existing person_id for events.
            """
            nonlocal staged, invalid
            invalid = True
            if staged:
                raise ValueError('A batch has already been staged.')
            parsed = [GraphAction.model_validate(c) for c in changes]
            self.validate(parsed, prepared)
            staged = parsed
            invalid = False
            return {'status': 'staged', 'count': len(staged)}

        # Retain the domain rules, replace only the legacy JSON-output instruction.
        prompt = REVIEW_PROMPT.replace('Return JSON exactly in this form:\n{{"reply_text": "brief explanation of what you did or found", "actions": []}}',
            'Use stage_graph_changes for requested edits. Then respond in plain text. Do not return JSON.')
        prompt = prompt.format(facts=json.dumps(prepared['snapshot']['facts']),
            topics=prepared['snapshot']['topics_text'], records=json.dumps(prepared['records']))
        prompt += '\nPrior conversation (data):\n' + json.dumps(prepared['history'])
        prompt += '\nStaged changes are committed only when your run completes. Do not claim success after a tool error.'
        guard = StagedToolGuard()
        adapter = StrandsAgent(make_agent(prompt, [stage_graph_changes], model=model), name='graph', hooks=[ModelCallLimit(), guard])
        trusted = RunAgentInput(thread_id=body.thread_id, run_id=body.run_id,
            messages=[UserMessage(id=request.request_id, content=request.text)], state={}, tools=[], context=[], forwarded_props={})
        parts = []
        held_text = []
        async with asyncio.timeout(190):
            async for event in adapter.run(trusted):
                if event.type == EventType.TEXT_MESSAGE_CONTENT:
                    parts.append(event.delta)
                if (staged or invalid or guard.failed) and event.type in {
                    EventType.TEXT_MESSAGE_START, EventType.TEXT_MESSAGE_CONTENT, EventType.TEXT_MESSAGE_END
                }:
                    held_text.append(event)
                    continue
                if event.type == EventType.RUN_ERROR:
                    raise ValueError('The review could not finish. Retry the saved message.')
                if event.type == EventType.RUN_FINISHED:
                    if invalid or guard.failed or not ''.join(parts).strip() or (event.outcome and event.outcome.type != 'success'):
                        raise ValueError('The review did not complete safely.')
                    store = self.service._knowledge.store
                    with store.transaction():
                        if staged:
                            self.validate(staged, prepared)
                        outcomes = []
                        for action in staged:
                            raw = action.model_dump(exclude_none=True)
                            if action.type == 'correct_record':
                                applied = self.service._correct_record(CorrectRecord.model_validate(raw), body.thread_id, request.text)
                            else:
                                applied = self.service._maintenance.execute_action(raw)
                            if not applied:
                                raise ValueError('A requested graph change was rejected.')
                            outcomes.append({'action': raw, 'applied': True})
                        self.db.execute('UPDATE graph_review_turns SET reply=?,actions=? WHERE session_id=? AND request_id=?',
                                        (''.join(parts), json.dumps(outcomes), body.thread_id, request.request_id))
                    session = await self.session(body.thread_id)
                    for held_event in held_text:
                        yield held_event
                    yield CustomEvent(name='graph_committed', value={'actions': outcomes})
                    yield MessagesSnapshotEvent(messages=messages_for(session))
                yield event
