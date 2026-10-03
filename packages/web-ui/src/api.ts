export async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, { method, headers: { 'Content-Type': 'application/json' },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}) });
  } catch { throw new Error('Cannot reach Plata. Check your connection and try again.'); }
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new Error(typeof data?.detail === 'string' ? data.detail : `The request could not be completed (${response.status}).`);
  if (data === null) throw new Error('Plata returned an unreadable response. Please try again.');
  return data as T;
}

export type ChatMessage = { id: string; role: string; text: string; request_id: string; selected_note_id?: string | null;
  changes?: { tool: string; note_id: string }[] };
export type Session = { id: string; title: string; messages: ChatMessage[]; pending: ChatMessage | null; actions?: { action: Record<string, unknown>; applied: boolean }[] };
export type NoteContent = { version: number; created_at: string; observations: string; author_reflections: string;
  suggestions: string; parking_lot: string; original_quotes?: { text: string; message_id: string }[] };
export type Note = { id: string; title: string; visibility: string; archived: boolean; updated_at: string;
  current: NoteContent | null; revisions: NoteContent[]; connections: { id: string; title: string }[] };
export function date(value?: string | number): string {
  if (!value) return 'Not yet';
  return new Date(typeof value === 'number' ? value * 1000 : value).toLocaleString(undefined,
    { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}
export function errorText(error: unknown) { return error instanceof Error ? error.message : 'Something went wrong. Please try again.'; }

export function newId(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, b => b.toString(16).padStart(2, '0')).join('');
}
