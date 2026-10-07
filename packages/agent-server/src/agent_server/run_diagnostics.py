"""Content-free diagnostics for note runs, including SDK tool failures."""

import json
import logging
import time

from pydantic import ValidationError
from strands.hooks import AfterToolCallEvent, BeforeModelCallEvent, HookProvider

logger = logging.getLogger(__name__)

REASONS = {
    'Invalid tool arguments.': 'invalid_tool_arguments',
    'Read the current note before changing it.': 'note_not_read_or_changed',
    'Only one operation per note in a turn.': 'duplicate_note_operation',
    'Restore the note before editing it.': 'note_archived',
    'A quote did not exactly match an original user message.': 'quote_mismatch',
    'Unknown note association.': 'unknown_association',
    'Invalid visibility.': 'invalid_visibility',
    'Sharing needs an explicit request in your message.': 'sharing_not_authorized',
    'Read notes before staging changes.': 'read_after_staging',
    'Changes have already been staged for this turn.': 'already_staged',
    'Stage one to five write operations.': 'invalid_write_batch',
    'A note change failed validation. Retry your saved message.': 'tool_validation_failed',
    'The assistant needed too many tool rounds. Retry your saved message.': 'round_limit',
    'A brief assistant reply is required.': 'empty_reply',
    'Retry the saved message before sending another.': 'pending_request',
}
FIELDS = set('changes name arguments content note_id visibility title observations '
             'author_reflections suggestions parking_lot original_quotes message_id '
             'text person_ids topic_ids guidance_ids reply'.split())
TOOLS = {'list_notes', 'read_note', 'stage_note_changes'}
OPERATIONS = {'create_note', 'update_note', 'delete_note', 'restore_note', 'set_note_visibility',
              'read_note', 'list_notes'}


def input_shape(value, depth=0):
    """Bounded structure only; string values and unknown keys never enter logs."""
    if depth > 4:
        return type(value).__name__
    if isinstance(value, dict):
        return {'fields': {k: input_shape(v, depth + 1) for k, v in value.items() if k in FIELDS},
                'unknown_fields': sum(k not in FIELDS for k in value)}
    if isinstance(value, list):
        return {'count': len(value), 'items': [input_shape(v, depth + 1) for v in value[:5]]}
    return type(value).__name__


def error_details(error):
    """Never emit exception text, input values, or arbitrary field names."""
    if error is None:
        return {'reason': 'tool_error_without_exception'}
    details = {'error_type': type(error).__name__}
    # Strands wraps its Pydantic input error in ValueError before invoking tools.
    if isinstance(error.__cause__, ValidationError):
        error = error.__cause__
    if isinstance(error, ValidationError):
        details['reason'] = 'schema_validation'
        details['validation'] = [
            {'type': e['type'], 'path': [p if isinstance(p, int) or p in FIELDS else '<field>'
                                       for p in e['loc']]}
            for e in error.errors(include_url=False, include_context=False, include_input=False)[:20]
        ]
    else:
        details['reason'] = REASONS.get(str(error), 'unclassified')
    return details


class RunDiagnostics(HookProvider):
    def __init__(self, session_id, request_id, run_id):
        self.context = dict(session_id=session_id, request_id=request_id, run_id=run_id)
        self.rounds = 0
        self.started = time.monotonic()

    def emit(self, event, **fields):
        logger.info('logbook_run %s', json.dumps(self.context | {'event': event} | fields,
                                                ensure_ascii=True))

    def register_hooks(self, registry):
        registry.add_callback(BeforeModelCallEvent, self.before_model)
        registry.add_callback(AfterToolCallEvent, self.after_tool)

    def before_model(self, event):
        self.rounds += 1
        self.emit('model_round', round=self.rounds)

    def after_tool(self, event):
        failed = not isinstance(event.result, dict) or event.result.get('status') == 'error'
        name = event.tool_use.get('name')
        arguments = event.tool_use.get('input', {})
        changes = arguments.get('changes', []) if isinstance(arguments, dict) else []
        operations = [c.get('name') if isinstance(c.get('name'), str) and c['name'] in OPERATIONS else '<unknown>'
                      for c in changes[:5] if isinstance(c, dict)] if isinstance(changes, list) else []
        self.emit('tool_completed', tool=name if name in TOOLS else '<unknown>',
                  round=self.rounds, status='error' if failed else 'success',
                  operations=operations,
                  duration_ms=round(event.duration * 1000) if event.duration is not None else None,
                  **(error_details(event.exception) | {'input_shape': input_shape(arguments)} if failed else {}))

    def finish(self, **fields):
        self.emit('run_finished', rounds=self.rounds,
                  duration_ms=round((time.monotonic() - self.started) * 1000), **fields)
