"""Read-only, visibility-filtered ontology search for shared conversation channels."""
import json
import logging
import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError
from strands import tool

from .journal import JournalService
from .household import MemorySettings

logger = logging.getLogger(__name__)

# Deliberate projections: new/private properties never become readable by accident.
FIELDS = {
    'person': ('aliases',),
    'fact': ('subject', 'relation', 'object', 'confidence'),
    'topic': (),
    'media': ('media_content_type', 'title', 'tags', 'topics'),
    'playlist': ('playlist_id', 'track_count'),
    'curriculum_subject': (), 'curriculum_cycle': (), 'curriculum_week': (),
    'media_tag': (), 'learning_entity': (),
    'family_value': ('description', 'guidance'),
    'guidance_document': ('document_key', 'content', 'application_instructions', 'version'),
    'learning_event': ('person_id', 'child_name', 'summary', 'topic', 'material', 'outcome',
                       'occurred_at_description', 'reporter_claim', 'reporter_verification'),
    'behavior_event': ('person_id', 'child_name', 'summary', 'status', 'interpretation',
                       'occurred_at_description', 'reporter_claim', 'reporter_verification'),
    'kid_event': ('child_name', 'event_type', 'summary', 'status'),
}
SCOPE = ('Shared household records and current family-visible, non-archived notes only. '
         'Parent-only notes, raw conversations, old note revisions, and disabled event memory '
         'are not searchable. Empty results do not prove no records exist outside this scope.')


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    query: Annotated[StrictStr, Field(max_length=300)] = ''
    entity_types: list[StrictStr] = Field(default_factory=list, max_length=20)
    person_ids: list[StrictStr] = Field(default_factory=list, max_length=20)
    entity_ids: list[StrictStr] = Field(default_factory=list, max_length=20)
    limit: Annotated[StrictInt, Field(ge=1, le=20)] = 10
    offset: Annotated[StrictInt, Field(ge=0, le=10000)] = 0


def bounded(value):
    """Only publish bounded primitive property values; arbitrary nested data is private."""
    if isinstance(value, str):
        return value[:2000]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, list):
        return [v[:200] for v in value[:20] if isinstance(v, str)]
    return None


class OntologySearch:
    def __init__(self, knowledge):
        self.knowledge = knowledge

    def snapshot(self):
        db = self.knowledge.store._db
        # Reading settings must not create a settings table or write default state.
        row = None
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='household_settings'").fetchone():
            row = db.execute('SELECT settings FROM household_settings WHERE id=1').fetchone()
        settings = MemorySettings.model_validate_json(row[0]) if row else MemorySettings()
        records = {}
        revision_aliases = {}
        journals = JournalService(self.knowledge)
        kinds = (*FIELDS, 'journal')
        placeholders = ','.join('?' for _ in kinds)
        for row in db.execute(f'SELECT * FROM entities WHERE entity_type IN ({placeholders})', kinds):
            kind = row['entity_type']
            props = json.loads(row['properties'])
            if kind == 'learning_event' and not settings.learning_logging:
                continue
            if kind in {'behavior_event', 'kid_event'} and not settings.behavior_logging:
                continue
            if kind == 'guidance_document' and not props.get('active'):
                continue
            if kind == 'family_value' and not props.get('enabled', True):
                continue
            name = row['name']
            if kind == 'journal':
                # Apply privacy before reading note contents, revisions, or connections.
                if props.get('visibility') != 'family' or props.get('archived', False):
                    continue
                journal = journals.get(row['id'])
                current = journal['current']
                if not current or journal['pending']:
                    continue
                name = current['title']
                properties = {key: bounded(current.get(key, '')) for key in
                              ('observations', 'author_reflections', 'suggestions', 'parking_lot')}
                properties['visibility'] = 'family'
                properties['version'] = current.get('version')
                revision_aliases[current['id']] = row['id']
            else:
                properties = {key: bounded(props[key]) for key in FIELDS[kind] if key in props}
                if kind == 'behavior_event':
                    properties['reviews'] = [{key: bounded(review.get(key)) for key in
                                              ('status', 'note', 'recorded_at')}
                                             for review in props.get('reviews', [])[-5:] if isinstance(review, dict)]
            records[row['id']] = {'id': row['id'], 'type': kind, 'name': name[:200],
                                   'properties': properties, 'created_at': row['created_at'],
                                   'updated_at': row['updated_at']}
        links = []
        for row in db.execute('SELECT from_entity,to_entity,relationship_type FROM links'):
            source = revision_aliases.get(row['from_entity'], row['from_entity'])
            target = revision_aliases.get(row['to_entity'], row['to_entity'])
            if source in records and target in records and source != target:
                links.append({'source': source, 'target': target, 'relationship': row['relationship_type']})
        return records, links

    def search(self, **arguments):
        try:
            request = SearchRequest.model_validate(arguments)
        except ValidationError:
            return {'error': 'Invalid search arguments. Use strings/list-of-strings, limit 1–20, and offset 0–10000.', 'scope': SCOPE}
        unknown = set(request.entity_types) - {*FIELDS, 'journal'}
        if unknown:
            return {'error': 'Unsupported entity type. Choose from searchable_types.',
                    'searchable_types': [*FIELDS, 'journal'], 'scope': SCOPE}
        records, links = self.snapshot()
        people = {id for id in request.person_ids if id in records and records[id]['type'] == 'person'}
        linked = set(people)
        for edge in links:
            if edge['target'] in people:
                linked.add(edge['source'])
            if edge['source'] in people:
                linked.add(edge['target'])
        terms = re.findall(r'\w+', request.query.casefold())[:30]
        matches = []
        for id, record in records.items():
            if request.entity_types and record['type'] not in request.entity_types:
                continue
            if request.entity_ids and id not in request.entity_ids:
                continue
            if request.person_ids and id not in linked and record['properties'].get('person_id') not in people:
                continue
            searchable = json.dumps({'name': record['name'], 'properties': record['properties']}, ensure_ascii=False).casefold()
            score = sum(term in searchable for term in terms)
            if terms and not score:
                continue
            matches.append((score, record['updated_at'], id, record))
        matches.sort(key=lambda item: item[:3], reverse=True)
        page, size = [], 0
        for item in matches[request.offset:request.offset + request.limit]:
            length = len(json.dumps(item[3], ensure_ascii=False))
            if page and size + length > 16000:
                break
            page.append(item[3])
            size += length
        selected = {record['id'] for record in page}
        edges = [edge for edge in links if edge['source'] in selected or edge['target'] in selected]
        next_offset = request.offset + len(page)
        return {'records': page, 'links': edges[:40], 'links_truncated': len(edges) > 40,
                'total': len(matches), 'next_offset': next_offset if next_offset < len(matches) else None,
                'scope': SCOPE, 'searchable_types': [*FIELDS, 'journal'],
                'note': 'Text fields are limited to 2,000 characters. Reports are not verified facts.'}


def search_tool(knowledge):
    """One read-only tool instance and budget per user turn, never a global counter."""
    search = OntologySearch(knowledge)
    calls = 0

    @tool
    async def search_ontology(query: str = '', entity_types: list[str] | None = None,
                        person_ids: list[str] | None = None, entity_ids: list[str] | None = None,
                        limit: int = 10, offset: int = 0) -> dict:
        """Search accessible ontology records and graph links; this tool cannot write.

        Args:
            query: Literal search terms (any match); empty string browses filtered records.
            entity_types: Optional types, e.g. person, fact, learning_event, behavior_event, journal.
            person_ids: Optional exact person IDs; matches any person's records/direct graph links.
            entity_ids: Optional exact returned entity IDs to read/follow links. Visibility still applies.
            limit: Page size 1–20. Properties are bounded excerpts, not entire source transcripts.
            offset: Pagination offset; use next_offset from the previous result.
        """
        nonlocal calls
        calls += 1
        if calls > 6:
            return {'error': 'Search budget exhausted. Answer using gathered evidence and disclose remaining gaps.', 'scope': SCOPE}
        result = search.search(query=query, entity_types=entity_types or [], person_ids=person_ids or [],
                               entity_ids=entity_ids or [], limit=limit, offset=offset)
        logger.info('Ontology search completed: records=%d error=%s', len(result.get('records', [])), 'error' in result)
        return result

    return search_ontology
