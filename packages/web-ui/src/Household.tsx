import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { BookHeart, Plus, Users, SlidersHorizontal, History, ArrowUpRight } from 'lucide-react';
import { api, date, errorText } from './api';

type Person = { id: string; name: string; aliases: string[] };
type Guidance = { id: string; document_key: string; title: string; content: string; application_instructions: string; version: number; active: boolean; created_at: string };
type Settings = { behavior_logging: boolean; learning_logging: boolean };
type MemoryEvent = { id: string; child_name: string; summary: string; source_text?: string; created_at: string; occurred_at_description?: string; reporter_claim?: string;
  topic?: string; material?: string; outcome?: string; status?: string; interpretation?: string;
  interpretations?: { document_id: string; section: string; explanation: string }[];
  reviews?: { status: string; note: string; recorded_at: string }[] };
const blankGuidance = { document_key: '', title: '', content: '', application_instructions: 'Apply relevant guidance naturally and warmly, with a concrete next step when useful.', active: true };

export function Household() {
  const [people, setPeople] = useState<Person[]>([]);
  const [documents, setDocuments] = useState<Guidance[]>([]);
  const [settings, setSettings] = useState<Settings>({ behavior_logging: true, learning_logging: true });
  const [section, setSection] = useState('guidance');
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(blankGuidance);
  const [selected, setSelected] = useState('');
  const load = useCallback(async () => {
    const [p, d, s] = await Promise.all([api<Person[]>('/api/people'), api<Guidance[]>('/api/guidance-documents?include_history=true'), api<Settings>('/api/memory-settings')]);
    setPeople(p); setDocuments(d); setSettings(s);
  }, []);
  useEffect(() => { load().catch(e => setError(errorText(e))).finally(() => setLoading(false)); }, [load]);
  async function save(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError(''); setMessage('');
    try { const saved = await api<Guidance>('/api/guidance-documents', 'POST', draft); await load(); setSelected(saved.document_key); setEditing(false); setMessage('Guidance saved as a new revision. Previous wording is preserved.'); }
    catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  async function addPerson(e: FormEvent<HTMLFormElement>) {
    e.preventDefault(); const form = e.currentTarget; const data = new FormData(form); setBusy(true); setError('');
    try { await api('/api/people', 'POST', { name: data.get('name'), aliases: String(data.get('aliases')).split(',').map(s => s.trim()).filter(Boolean) }); await load(); form.reset(); setMessage('Person added.'); }
    catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  async function saveSettings(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError('');
    try { setSettings(await api<Settings>('/api/memory-settings', 'PUT', settings)); setMessage('Memory settings saved.'); }
    catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  const latest = documents.filter((d, i) => documents.findIndex(x => x.document_key === d.document_key) === i);
  const current = latest.find(d => d.document_key === selected);
  return <><header className="page-heading"><div><span className="eyebrow">WHAT MATTERS AT HOME</span><h1>Growing together.</h1><p>Keep your guidance, shared context, and everyday learning connected.</p></div></header>
    <div className="section-tabs" role="tablist" aria-label="Household sections">{[['guidance', 'Guidance', BookHeart], ['people', 'People', Users], ['memory', 'Learning & behavior', History], ['settings', 'Memory settings', SlidersHorizontal]].map(([id, title, Icon]) => { const I = Icon as typeof Users; return <button key={String(id)} role="tab" aria-selected={section === id} disabled={editing || busy} onClick={() => { setSection(String(id)); setMessage(''); setError(''); }}><I size={16}/>{String(title)}</button>; })}</div>
    {error && <div role="alert" className="notice error">{error}<button onClick={() => load().then(() => setError('')).catch(e => setError(errorText(e)))}>Reload</button></div>}
    {message && <div role="status" className="notice">{message}</div>}
    {loading ? <p className="empty" role="status">Loading household context…</p> : <>
      {section === 'guidance' && <div className="split-grid"><section className="card"><div className="card-heading"><div><BookHeart size={18}/><h2>Guidance documents</h2></div><button className="icon-button" disabled={editing} aria-label="New guidance document" onClick={() => { setSelected(''); setDraft(blankGuidance); setEditing(true); }}><Plus size={18}/></button></div>
        {!latest.length && <div className="empty"><h3>Put your values into words.</h3><p>Add guidance Plata can draw on when it is relevant.</p></div>}
        <div className="note-list">{latest.map(d => <button className={`note-item ${selected === d.document_key ? 'selected' : ''}`} key={d.id} disabled={editing} onClick={() => setSelected(d.document_key)}><span className="note-item-date">Version {d.version} · {d.active ? 'Active' : 'Inactive'}</span><strong>{d.title}</strong><span className="small muted">{date(d.created_at)}</span></button>)}</div></section>
        {editing ? <form className="card form-card" onSubmit={save}><h2>{selected ? 'Revise guidance' : 'New guidance'}</h2><p>Keep the document itself separate from instructions for how Plata should use it.</p>
          <label>Document key<input required maxLength={120} readOnly={Boolean(selected)} value={draft.document_key} onChange={e => setDraft({ ...draft, document_key: e.target.value })} placeholder="household-kindness"/></label>
          <label>Title<input required maxLength={120} value={draft.title} onChange={e => setDraft({ ...draft, title: e.target.value })}/></label>
          <label>Document content (Markdown)<textarea required rows={12} maxLength={20000} value={draft.content} onChange={e => setDraft({ ...draft, content: e.target.value })}/></label>
          <label>How Plata should apply it<textarea required rows={3} maxLength={2000} value={draft.application_instructions} onChange={e => setDraft({ ...draft, application_instructions: e.target.value })}/></label>
          <label className="check-label"><input type="checkbox" checked={draft.active} onChange={e => setDraft({ ...draft, active: e.target.checked })}/> Use this guidance in conversations</label>
          <div className="actions"><button className="primary" disabled={busy}>Save new revision</button><button type="button" disabled={busy} onClick={() => setEditing(false)}>Cancel</button></div></form> : current ?
          <article className="card form-card"><div className="row-between"><span className="badge">Version {current.version} · {current.active ? 'Active' : 'Inactive'}</span><button onClick={() => { setDraft(current); setEditing(true); }}>Edit guidance</button></div><h2 className="note-title">{current.title}</h2><div className="prose-document">{current.content}</div><section className="note-section"><h3>Application instructions</h3><p>{current.application_instructions}</p></section>
            <details className="history"><summary>Revision history ({documents.filter(d => d.document_key === selected).length})</summary>{documents.filter(d => d.document_key === selected).map(d => <details key={d.id}><summary>Version {d.version} · {date(d.created_at)}</summary><div className="prose-document">{d.content}</div><p className="small">Application: {d.application_instructions}</p></details>)}</details></article> :
          <section className="card empty"><BookHeart size={35}/><h2>Guidance with room to grow.</h2><p>Select a document to read it, or add one that reflects your household.</p><button onClick={() => { setDraft(blankGuidance); setEditing(true); }}>Add guidance</button></section>}
      </div>}
      {section === 'people' && <div className="split-grid"><section className="card form-card"><h2>People at home</h2><p>Names and aliases help connect reports to the right person.</p>{people.length ? people.map(p => <article className="person-row" key={p.id}><span className="avatar">{p.name.slice(0, 1)}</span><div><h3>{p.name}</h3><p className="small">{p.aliases?.join(' · ') || 'No aliases'}</p><a className="text-link" href={`#knowledge?record=${encodeURIComponent(p.id)}`}>View connections <ArrowUpRight size={12}/></a></div></article>) : <p className="empty">No people added yet.</p>}</section><form className="card form-card" onSubmit={addPerson}><h2>Add a person</h2><label>Name<input name="name" required maxLength={120} placeholder="Sample Person"/></label><label>Aliases, separated by commas<input name="aliases" placeholder="Optional nicknames"/></label><p className="small">Use distinct names where possible. Ambiguous identities are clarified in conversation.</p><button className="primary" disabled={busy}>Add person</button></form></div>}
      {section === 'settings' && <form className="card form-card settings-card" onSubmit={saveSettings}><h2>Remember what helps.</h2><p>Learning and behavior logging work independently. Guidance can still be used with either switch off.</p><label className="setting-row"><span><strong>Learning memory</strong><small>Keep reported practice, recall, and progress linked to people and topics.</small></span><input type="checkbox" checked={settings.learning_logging} onChange={e => setSettings({ ...settings, learning_logging: e.target.checked })}/></label><label className="setting-row"><span><strong>Behavior memory</strong><small>Keep reported events, interpretations, and later review notes distinct.</small></span><input type="checkbox" checked={settings.behavior_logging} onChange={e => setSettings({ ...settings, behavior_logging: e.target.checked })}/></label><button className="primary" disabled={busy}>Save settings</button></form>}
      {section === 'memory' && <Memory people={people} documents={documents}/>}
    </>}
  </>;
}

function Memory({ people, documents }: { people: Person[]; documents: Guidance[] }) {
  const [kind, setKind] = useState('learning'); const [person, setPerson] = useState(''); const [topic, setTopic] = useState('');
  const [events, setEvents] = useState<MemoryEvent[]>([]); const [loading, setLoading] = useState(true); const [error, setError] = useState('');
  const load = useCallback(async () => { const params = new URLSearchParams(); if (person) params.set('person_id', person); if (kind === 'learning' && topic) params.set('topic', topic); return api<MemoryEvent[]>(`/api/${kind}-events?${params}`); }, [kind, person, topic]);
  useEffect(() => { let active = true; setLoading(true); setError(''); load().then(value => { if (active) setEvents(value); }).catch(e => { if (active) setError(errorText(e)); }).finally(() => { if (active) setLoading(false); }); return () => { active = false; }; }, [load]);
  return <section className="card form-card"><div className="form-grid filters"><label>Record type<select value={kind} onChange={e => setKind(e.target.value)}><option value="learning">Learning</option><option value="behavior">Behavior</option></select></label><label>Person<select value={person} onChange={e => setPerson(e.target.value)}><option value="">Everyone</option>{people.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>{kind === 'learning' && <label>Topic<input value={topic} onChange={e => setTopic(e.target.value)} placeholder="All topics"/></label>}</div><p className="small muted">Latest 50 matching reports. These are reported experiences, not verified assessments.</p>
    {error && <p role="alert" className="notice error">{error}</p>}{loading ? <p className="empty" role="status">Loading records…</p> : !events.length ? <p className="empty">No matching reports yet.</p> : events.map(event => <article className="memory-record" key={event.id}><div className="row-between"><h3>{event.child_name}</h3><span className="badge">{event.outcome || event.status}</span></div><p className="record-summary">{event.summary}</p><p className="small">{event.topic ? `${event.topic} · ${event.material} · ` : ''}{event.occurred_at_description || date(event.created_at)}</p>
      <details className="history"><summary>Source and interpretation</summary>{event.source_text && <blockquote>{event.source_text}</blockquote>}{event.reporter_claim && <p className="small">Reported by: {event.reporter_claim} (self-reported)</p>}{event.interpretation && <p>{event.interpretation}</p>}{event.interpretations?.map((i, index) => <div className="note-section" key={index}><h3>{documents.find(d => d.id === i.document_id)?.title || 'Guidance'} · {i.section}</h3><p>{i.explanation}</p></div>)}<a className="text-link" href={`#knowledge?record=${encodeURIComponent(event.id)}`}>Review this record <ArrowUpRight size={12}/></a></details>
      {event.reviews?.map((r, i) => <div className="review-note" key={i}><strong>{r.status}</strong><p>{r.note}</p><small>{date(r.recorded_at)}</small></div>)}
      {kind === 'behavior' && <BehaviorReview event={event} onSaved={value => setEvents(old => old.map(e => e.id === value.id ? value : e))}/>}
    </article>)}
  </section>;
}
function BehaviorReview({ event, onSaved }: { event: MemoryEvent; onSaved: (value: MemoryEvent) => void }) {
  const [error, setError] = useState(''); const [busy, setBusy] = useState(false);
  async function save(e: FormEvent<HTMLFormElement>) { e.preventDefault(); const form = e.currentTarget; const data = new FormData(form); setBusy(true); setError(''); try { onSaved(await api<MemoryEvent>(`/api/behavior-events/${event.id}`, 'PATCH', { status: data.get('status'), note: data.get('note') })); form.reset(); } catch (e) { setError(errorText(e)); } finally { setBusy(false); } }
  return <details className="history"><summary>Add a review update</summary><form onSubmit={save}><label>Status<select name="status" defaultValue={event.status}>{['open', 'repaired', 'closed', 'disputed'].map(s => <option key={s}>{s}</option>)}</select></label><label>Parent review note<textarea name="note" required maxLength={2000} rows={3}/></label><button disabled={busy}>Save review</button>{error && <p role="alert" className="error-text">{error}</p>}</form></details>;
}
