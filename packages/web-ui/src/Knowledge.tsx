import { useCallback, useEffect, useMemo, useState } from 'react';
import { Network, Search, Plus, ArrowUpRight, RefreshCw, X } from 'lucide-react';
import { AgentChat } from './AgentChat';
import { api, date, errorText, type Session } from './api';

type Node = { id: string; type: string; name: string; properties: Record<string, unknown>; created_at: string; updated_at: string };
type Graph = { nodes: Node[]; links: { id: string; from: string; to: string; type: string }[] };
const colors = ['#668f79', '#bc9865', '#909cb5', '#bc8f87', '#9aab72', '#78a4a9'];
export function Knowledge({ record = '' }: { record?: string }) {
  const [graph, setGraph] = useState<Graph>({ nodes: [], links: [] });
  const [query, setQuery] = useState(record); const [type, setType] = useState('');
  const [selected, setSelected] = useState(record); const [sessions, setSessions] = useState<Session[]>([]);
  const [session, setSession] = useState<Session | null>(null); const [suggested, setSuggested] = useState('');
  const [error, setError] = useState(''); const [loading, setLoading] = useState(true); const [busy, setBusy] = useState(false);
  const load = useCallback(async () => { const [g, s] = await Promise.all([api<Graph>('/api/graph'), api<Session[]>('/api/graph/review-sessions')]); setGraph(g); setSessions(s); return s; }, []);
  useEffect(() => { let active = true; load().then(async s => { if (s.length && active) setSession(await api<Session>(`/api/graph/review-sessions/${s[0].id}`)); }).catch(e => setError(errorText(e))).finally(() => setLoading(false)); return () => { active = false; }; }, [load]);
  useEffect(() => { setQuery(record); setSelected(record); setType(''); }, [record]);
  async function openSession(id?: string) { setBusy(true); setError(''); try { if (!id) id = (await api<Session>('/api/graph/review-sessions', 'POST', {})).id; const value = await api<Session>(`/api/graph/review-sessions/${id}`); setSession(value); setSuggested(''); await load(); } catch (e) { setError(errorText(e)); } finally { setBusy(false); } }
  async function review(node: Node) { if (!session) await openSession(); setSuggested(`Please review record ${node.id}. `); }
  const types = useMemo(() => [...new Set(graph.nodes.map(n => n.type))].sort(), [graph]);
  const filtered = graph.nodes.filter(n => (!type || n.type === type) && JSON.stringify(n).toLowerCase().includes(query.toLowerCase()));
  const visible = filtered.slice(0, 120);
  const positions = useMemo(() => new Map(visible.map((n, i) => { const angle = i * 2.399963; const radius = 22 + 145 * Math.sqrt(i / Math.max(1, visible.length)); return [n.id, { x: 300 + Math.cos(angle) * radius * 1.5, y: 180 + Math.sin(angle) * radius }]; })), [graph, query, type]);
  const current = graph.nodes.find(n => n.id === selected);
  return <><header className="page-heading"><div><span className="eyebrow">THE THREADS THAT CONNECT</span><h1>A clearer picture.</h1><p>Explore what Plata remembers, see where it came from, and refine it together.</p></div><button aria-label="Refresh knowledge graph" disabled={busy} onClick={() => load().catch(e => setError(errorText(e)))}><RefreshCw size={16}/> Refresh</button></header>
    {error && <div role="alert" className="notice error">{error}</div>}
    <div className="knowledge-grid"><div className="stack"><section className="card"><div className="card-heading"><div><Network size={18}/><h2>Your household connections</h2></div><span className="small muted">{graph.nodes.length} records · {graph.links.length} links</span></div>
      <div className="graph-canvas"><svg viewBox="0 0 600 360" role="img" aria-label={`Knowledge graph with ${visible.length} visible records`}>
        {graph.links.filter(l => positions.has(l.from) && positions.has(l.to)).map(l => { const a = positions.get(l.from)!; const b = positions.get(l.to)!; return <line key={l.id} x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="#d7dfd0" strokeWidth="1"/>; })}
        {visible.map(n => { const p = positions.get(n.id)!; return <g key={n.id} role="button" tabIndex={0} aria-label={`${n.type}: ${n.name}`} onClick={() => setSelected(n.id)} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setSelected(n.id); } }}><title>{n.name}</title><circle cx={p.x} cy={p.y} r={selected === n.id ? 10 : 6} fill={colors[types.indexOf(n.type) % colors.length]} stroke={selected === n.id ? '#264d3b' : '#fffefa'} strokeWidth="2"/>{(visible.length < 20 || selected === n.id) && <text x={p.x} y={p.y + 22} textAnchor="middle" fill="#637459" fontSize="10">{n.name.slice(0, 32)}</text>}</g>; })}</svg>
        {!visible.length && <div className="graph-empty">{loading ? 'Loading connections…' : 'No matching connections yet.'}</div>}</div>
      <div className="graph-legend">{types.map((t, i) => <button key={t} onClick={() => setType(type === t ? '' : t)} aria-pressed={type === t}><span style={{ background: colors[i % colors.length] }}/>{t.replaceAll('_', ' ')}</button>)}</div>
      <div className="filters graph-filters"><label className="search-label"><Search size={16}/><input aria-label="Search knowledge" placeholder="Search records, facts, or properties…" value={query} onChange={e => setQuery(e.target.value)}/></label><select aria-label="Record type" value={type} onChange={e => setType(e.target.value)}><option value="">All types</option>{types.map(t => <option key={t} value={t}>{t.replaceAll('_', ' ')}</option>)}</select></div>
      <p className="small muted card-caption">{filtered.length} matching records · diagram shows the first 120 · newest updates first</p>
      <div className="entity-list">{filtered.map(n => <button key={n.id} className={`entity-row ${selected === n.id ? 'selected' : ''}`} onClick={() => setSelected(n.id)} aria-pressed={selected === n.id}><span className="badge">{n.type.replaceAll('_', ' ')}</span><strong>{n.name}</strong><small>Updated {date(n.updated_at)}</small><ArrowUpRight size={14}/></button>)}</div>
    </section>
    {current && <article className="card form-card"><div className="row-between"><span className="badge">{current.type.replaceAll('_', ' ')}</span><button className="icon-button" aria-label="Close record" onClick={() => setSelected('')}><X size={17}/></button></div><h2 className="note-title">{current.name}</h2><p className="small">Created {date(current.created_at)} · Updated {date(current.updated_at)}</p><dl className="record-properties">{Object.entries(current.properties).filter(([, v]) => v !== null && typeof v !== 'object').map(([key, value]) => <div key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd>{String(value)}</dd></div>)}</dl><details className="history"><summary>Full provenance and properties</summary><p className="small">Record ID: {current.id}</p><pre className="record-json">{JSON.stringify(current.properties, null, 2)}</pre></details>{['message', 'kid_event', 'behavior_event', 'learning_event', 'fact'].includes(current.type) && <button className="primary" disabled={busy} onClick={() => review(current)}>Review this record <ArrowUpRight size={14}/></button>}</article>}
    </div><section className="card conversation-card"><div className="card-heading"><div><span className="presence"/><h2>Review with Plata</h2></div><button className="icon-button" aria-label="New graph review" disabled={busy} onClick={() => openSession()}><Plus size={18}/></button></div>
      {sessions.length > 0 && <div className="conversation-picker"><select aria-label="Past graph reviews" disabled={busy} value={session?.id ?? ''} onChange={e => openSession(e.target.value)}>{session && !sessions.some(s => s.id === session.id) && <option value={session.id}>New review</option>}{sessions.map(s => <option key={s.id} value={s.id}>{s.title}</option>)}</select></div>}
      {session ? <AgentChat key={session.id} kind="graph" session={session} suggestedMessage={suggested} onBusy={setBusy} onComplete={s => { setSession(s); void load().catch(e => setError(errorText(e))); }}/> : <div className="empty"><h3>Make sense of a connection.</h3><p>Ask about a fact, correct a transcription, or bring duplicate records together.</p><button className="primary" disabled={busy} onClick={() => openSession()}>Start a review</button></div>}
      {Boolean(session?.actions?.length) && <div className="action-outcomes"><h3>Recorded changes</h3>{session!.actions!.map((a, i) => <p key={i}><span className={`badge ${a.applied ? 'state-succeeded' : 'state-failed'}`}>{a.applied ? 'Applied' : 'Not applied'}</span> {String(a.action.type).replaceAll('_', ' ')}{a.action.new_name ? `: ${a.action.new_name}` : a.action.corrected_text ? `: ${a.action.corrected_text}` : ''}</p>)}</div>}
    </section></div>
  </>;
}
