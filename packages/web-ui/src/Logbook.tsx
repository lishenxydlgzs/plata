import { useCallback, useEffect, useState } from 'react';
import { ArrowUpRight, BookOpen, Clock3, LockKeyhole, Plus, Search, Users, X } from 'lucide-react';
import { AgentChat } from './AgentChat';
import { api, date, errorText, type Note, type NoteContent, type Session } from './api';

function NoteSections({ value }: { value: NoteContent }) {
  const sections = [['What happened', value.observations], ['Your reflections', value.author_reflections],
    ['A thought from Plata', value.suggestions], ['For another day', value.parking_lot]];
  return <>{value.original_quotes?.map((q, i) => <blockquote key={i}>{q.text}</blockquote>)}
    {sections.filter(([, text]) => text).map(([title, text]) => <section className="note-section" key={title}><h3>{title}</h3><p>{text}</p></section>)}</>;
}

export function Logbook() {
  const [notes, setNotes] = useState<Note[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [session, setSession] = useState<Session | null>(null);
  const [note, setNote] = useState<Note | null>(null);
  const [query, setQuery] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [showRemoved, setShowRemoved] = useState(false);
  const refresh = useCallback(async () => {
    const [n, s] = await Promise.all([api<Note[]>('/api/journals'), api<Session[]>('/api/logbook/sessions')]);
    setNotes(n); setSessions(s); return s;
  }, []);
  const load = useCallback(async () => {
    setLoading(true); setError('');
    try { const s = await refresh(); if (s.length) setSession(await api<Session>(`/api/logbook/sessions/${s[0].id}`)); }
    catch (e) { setError(errorText(e)); } finally { setLoading(false); }
  }, [refresh]);
  useEffect(() => { void load(); }, [load]);

  async function openSession(id?: string) {
    setBusy(true); setError('');
    try {
      const s = id ? await api<Session>(`/api/logbook/sessions/${id}`) : await api<Session>('/api/logbook/sessions', 'POST', {});
      setSession(s); await refresh();
    } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  async function openNote(id: string) {
    setError('');
    try { setNote(await api<Note>(`/api/journals/${id}`)); } catch (e) { setError(errorText(e)); }
  }
  async function completed(s: Session) {
    setSession(s);
    try { await refresh(); const id = s.messages.at(-1)?.changes?.at(-1)?.note_id ?? note?.id;
      if (id) await openNote(id);
    } catch (e) { setError(errorText(e)); }
  }
  const visible = notes.filter(n => (showRemoved || !n.archived) && n.title.toLowerCase().includes(query.toLowerCase()));
  return <>
    <header className="page-heading"><div><span className="eyebrow">SPACE TO REFLECT</span><h1>The little things, remembered.</h1><p>A place for everyday moments and the meaning you find in them.</p></div>
      <button className="primary" disabled={busy || loading} onClick={() => openSession()}><Plus size={17}/> New conversation</button></header>
    {error && <div className="notice error" role="alert">{error}<button onClick={load}>Try again</button></div>}
    <div className="logbook-grid">
      <section className="card notes-card"><div className="card-heading"><div><BookOpen size={18}/><h2>Your notes</h2></div><span className="count">{notes.filter(n => !n.archived).length}</span></div>
        <div className="notes-search"><Search size={16}/><input aria-label="Search notes" placeholder="Find a moment…" value={query} onChange={e => setQuery(e.target.value)}/></div>
        <label className="small-check"><input type="checkbox" checked={showRemoved} onChange={e => setShowRemoved(e.target.checked)}/> Include removed notes</label>
        <div className="note-list">{loading ? <p className="empty" role="status">Loading your notes…</p> : visible.length ? visible.map(n =>
          <button className={`note-item ${note?.id === n.id ? 'selected' : ''}`} key={n.id} disabled={busy} onClick={() => openNote(n.id)} aria-pressed={note?.id === n.id}>
            <span className="note-item-date">{date(n.updated_at)}{n.archived ? ' · Removed' : ''}</span><strong>{n.title}</strong><span className="note-item-meta">{n.visibility === 'family' ? <Users size={13}/> : <LockKeyhole size={13}/>} {n.visibility === 'family' ? 'Shared with family' : 'Parents only'}<ArrowUpRight size={14}/></span></button>) :
          <div className="empty"><BookOpen size={25}/><h3>{query ? 'No matching notes' : 'Room for your first moment'}</h3><p>{query ? 'Try another word or include removed notes.' : 'Start a conversation. Useful reflections will find a home here.'}</p></div>}</div>
      </section>
      <section className="card reading-card">{note ? <><div className="reading-meta"><span className="badge">{note.archived ? 'Removed' : note.visibility === 'family' ? 'Family shared' : 'Parents only'}</span><button className="icon-button" aria-label="Clear selected note" onClick={() => setNote(null)} disabled={busy}><X size={17}/></button></div><h2 className="note-title">{note.title}</h2><p className="muted small">Updated {date(note.updated_at)}</p>
        {note.current ? <NoteSections value={note.current}/> : <p className="empty">This note is waiting to take shape.</p>}
        {note.connections?.length > 0 && <section className="note-section"><h3>Connected to</h3><div className="chips">{note.connections.map(c => <a key={c.id} href={`#knowledge?record=${encodeURIComponent(c.id)}`} className="chip">{c.title}<ArrowUpRight size={12}/></a>)}</div></section>}
        {note.revisions?.length > 1 && <details className="history"><summary><Clock3 size={15}/> Earlier versions ({note.revisions.length - 1})</summary>{note.revisions.slice(0, -1).reverse().map(r => <details key={r.version}><summary>Version {r.version} · {date(r.created_at)}</summary><NoteSections value={r}/></details>)}</details>}
      </> : <div className="reading-empty"><div className="paper-art"><span/><span/><span/><i/></div><span className="eyebrow">EVERYDAY LIFE, A LITTLE CLEARER</span><h2>Small moments.<br/>Lasting understanding.</h2><p>Choose a note to revisit it, or talk with Plata to make space for something new.</p><div className="quiet-rule"/><p className="small">Your words, preserved.<br/>Your reflections, connected.</p></div>}</section>
      <section className="card conversation-card"><div className="card-heading"><div><span className="presence"/><h2>Reflect with Plata</h2></div><span className="badge">Private</span></div>
        {sessions.length > 0 && <div className="conversation-picker"><label className="sr-only" htmlFor="conversation">Conversation</label><select id="conversation" value={session?.id ?? ''} disabled={busy} onChange={e => openSession(e.target.value)}>{sessions.map(s => <option key={s.id} value={s.id}>{s.title}</option>)}</select></div>}
        {note && <div className="context-note"><BookOpen size={14}/><span>Reflecting on: {note.title}</span></div>}
        {session ? <AgentChat key={session.id} session={session} selectedNoteId={note?.id} onComplete={s => void completed(s)} onBusy={setBusy}/> :
          <div className="empty"><h3>What stood out today?</h3><p>A shared laugh, a tricky moment, a new discovery. Start wherever you are.</p><button className="primary" disabled={loading || busy} onClick={() => openSession()}>Start reflecting<ArrowUpRight size={16}/></button></div>}
      </section>
    </div>
  </>;
}
