"""Parent-authorized Telegram note tools with staged, source-backed writes."""
import json
import uuid
from datetime import datetime, timezone

from strands import tool
from strands.hooks import AfterToolCallEvent, HookProvider

from .journal import JournalService
from .logbook_chat import LogbookChat, ToolCall, NoteContent
from .telegram_music import music_context


class TelegramNotes(HookProvider):
    def __init__(self, request, knowledge):
        self.request = request
        self.context = music_context.get()
        self.service = LogbookChat(JournalService(knowledge))
        self.allowed, self.snapshots, self.sources = {}, {}, {}
        self.calls = []
        self.failed = False
        self.prepared = False

    def authorized(self):
        c = self.context
        if (c is None or music_context.get() is not c or self.request.source != 'telegram'
                or self.request.person_id != c.person_id or self.request.conversation_id != c.conversation_id):
            return False
        return c.service.is_parent(c.person_id) and bool(c.service.db.execute(
            'SELECT 1 FROM accounts WHERE user_id=? AND chat_id=? AND person_id=? '
            'AND conversation_id=? AND grant_id=?',
            (c.user_id, c.chat_id, c.person_id, c.conversation_id, c.grant_id)).fetchone())

    def register_hooks(self, registry):
        registry.add_callback(AfterToolCallEvent, self.after_tool)

    def after_tool(self, event):
        if event.tool_use['name'] in {'list_notes', 'read_note', 'create_note', 'update_note'}:
            if not isinstance(event.result, dict) or event.result.get('status') == 'error':
                self.failed = True

    def check(self):
        if not self.authorized():
            raise ValueError('Log book access is unavailable. Only confirmed parents may use Telegram notes.')

    @property
    def key(self):
        return f'{self.context.grant_id}:{self.context.update_id}'

    async def prepare(self):
        self.check()
        if self.prepared:
            return
        c = self.context
        entries = await c.service.router._db.note_sources(c.conversation_id)
        self.check()
        entries.append({'id': f'update:{c.update_id}', 'text': self.request.text})
        for entry in entries:
            source_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f'telegram:{c.grant_id}:{c.conversation_id}:{entry["id"]}'))
            self.allowed[source_id] = entry['text']
            self.sources[source_id] = {'text': entry['text'], 'channel': 'telegram',
                                       'conversation_id': c.conversation_id, 'source_key': entry['id']}
            self.source_id = source_id
        self.directory = [dict(id=r['id'], type=r['entity_type'], title=r['name']) for r in
                          self.service.db.execute("SELECT id,entity_type,name FROM entities WHERE entity_type IN ('person','topic','guidance_document') ORDER BY updated_at DESC LIMIT 200")]
        self.service.db.execute('CREATE TABLE IF NOT EXISTS telegram_note_results (request_key TEXT PRIMARY KEY, reply TEXT NOT NULL)')
        self.service.db.commit()
        self.prepared = True

    def tools(self):
        if not self.authorized():
            return []

        @tool
        async def list_notes() -> dict:
            """List Log book titles, IDs and visibility, plus trusted quotation sources and directory IDs for note-taking."""
            try:
                await self.prepare()
                self.check()
                notes = [{k: n[k] for k in ('id', 'title', 'visibility', 'archived')} for n in self.service.journals.list()]
                return {'notes': notes, 'sources': [{'id': id, 'text': text} for id, text in self.allowed.items()],
                        'directory': self.directory}
            except ValueError:
                self.failed = True
                return {'error': 'Log book access is unavailable.'}

        @tool
        async def read_note(note_id: str) -> dict:
            """Read a Log book note and its exact source quotations before updating it."""
            try:
                await self.prepare()
                self.check()
                if self.calls:
                    raise ValueError('Read before staging.')
                return self.service._read(note_id, self.allowed, self.snapshots)
            except (KeyError, ValueError):
                self.failed = True
                return {'error': 'Cannot read this note. Check access and choose a current note ID from list_notes.'}

        def stage(name, content, note_id=None):
            self.failed = True
            try:
                self.check()
                if not self.prepared:
                    raise ValueError('List or read notes first.')
                args = {'content': NoteContent.model_validate(content).model_dump()}
                if note_id is not None:
                    args['note_id'] = note_id
                call = ToolCall(name=name, arguments=args)
                proposed = [*self.calls, call]
                if len(proposed) > 5:
                    raise ValueError('Too many writes.')
                self.service._validate(proposed, self.allowed, self.snapshots, self.directory, self.request.text)
                self.calls = proposed
                self.failed = False
                return {'status': 'staged', 'count': len(self.calls), 'visibility': 'parents' if name == 'create_note' else 'unchanged'}
            except (ValueError, KeyError):
                return {'error': 'Note not staged. List sources first; read before updating; use exact source quotations and directory IDs.'}

        @tool
        async def create_note(content: NoteContent) -> dict:
            """Stage a new parents-only Log book note when requested. Get source IDs from list_notes first.

            Args:
                content: title, observations, author_reflections, suggestions, parking_lot strings;
                    original_quotes [{message_id, text}] using exact source text; person_ids,
                    topic_ids, guidance_ids arrays from the directory. Empty sections use empty strings.
            """
            return stage('create_note', content)

        @tool
        async def update_note(note_id: str, content: NoteContent) -> dict:
            """Stage a requested update to a previously read note; preserve prior observations and quotations.

            Args:
                note_id: Exact ID from read_note.
                content: Complete note with the same fields as create_note, preserving prior relevant material.
            """
            return stage('update_note', content, note_id)

        return [list_notes, read_note, create_note, update_note]

    def commit(self, reply):
        if self.failed:
            raise ValueError('A note tool failed. No note changes were committed.')
        if not self.prepared:
            return reply
        self.check()
        saved = self.service.db.execute('SELECT reply FROM telegram_note_results WHERE request_key=?', (self.key,)).fetchone()
        if saved:
            return saved['reply']
        if not self.calls:
            return reply
        if not isinstance(reply, str) or not reply.strip():
            raise ValueError('A reply is required before committing notes.')
        operations = self.service._validate(self.calls, self.allowed, self.snapshots, self.directory, self.request.text)
        with self.service.db:
            now = datetime.now(timezone.utc).isoformat()
            for id, props in self.sources.items():
                self.service.db.execute('INSERT OR IGNORE INTO entities VALUES (?,?,?,?,?,?,?)',
                    (id, 'journal_entry', 'Telegram source', json.dumps(props), 'Telegram source', now, now))
            self.service._apply(operations, self.source_id)
            self.service.db.execute('INSERT INTO telegram_note_results VALUES (?,?)', (self.key, reply))
        return reply
