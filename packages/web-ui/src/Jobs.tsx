import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { Clock3, Plus, RefreshCw, Play, Music2, Sparkles } from 'lucide-react';
import { api, date, errorText } from './api';

type Job = { id: string; name: string; kind: string; folder: string; state: string; enabled: boolean; interval_hours: number; next_run: number; last_run: number; error?: string };
type Run = { id: number; status: string; trigger: string; started_at: number; finished_at?: number; summary?: string; error?: string;
  logs?: { timestamp: number; level: string; message: string }[] };
const jobPath = (id: string) => `/api/jobs/${encodeURIComponent(id)}`;
function State({ value }: { value: string }) { return <span className={`badge state-${value}`}>{value.replaceAll('_', ' ')}</span>; }

export function Jobs() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selected, setSelected] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);
  const refresh = useCallback(async () => {
    const value = await api<Job[]>('/api/jobs'); setJobs(value); setError('');
    setSelected(old => old || value[0]?.id || '');
  }, []);
  useEffect(() => {
    let active = true;
    async function load() { try { await refresh(); } catch (e) { if (active) setError(errorText(e)); } finally { if (active) setLoading(false); } }
    void load(); const interval = setInterval(() => { if (!document.hidden) void load(); }, 5000);
    return () => { active = false; clearInterval(interval); };
  }, [refresh]);
  useEffect(() => { const warn = (e: BeforeUnloadEvent) => { if (dirty) { e.preventDefault(); e.returnValue = ''; } }; window.addEventListener('beforeunload', warn); return () => window.removeEventListener('beforeunload', warn); }, [dirty]);
  async function add(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = event.currentTarget; const values = new FormData(form); setBusy(true); setError('');
    try { const job = await api<Job>('/media/sync-jobs', 'POST', { name: values.get('name'), url: values.get('url'), interval_hours: Number(values.get('hours')) });
      await refresh(); setSelected(job.id); setAdding(false);
    } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  const current = jobs.find(j => j.id === selected);
  return <><header className="page-heading"><div><span className="eyebrow">A LITTLE HELP IN THE BACKGROUND</span><h1>Keep things growing.</h1><p>Your audio library and household knowledge, gently kept up to date.</p></div><div className="actions"><button aria-label="Refresh jobs" onClick={() => refresh().catch(e => setError(errorText(e)))}><RefreshCw size={16}/></button><button className="primary" disabled={dirty} onClick={() => setAdding(!adding)}><Plus size={16}/> Add playlist</button></div></header>
    {error && <div role="alert" className="notice error">{error}</div>}
    {adding && <form className="card form-card" onSubmit={add}><h2>Add a YouTube playlist</h2><p>New videos are imported as audio into a dedicated playlist folder.</p><div className="form-grid"><label>Playlist name<input name="name" required maxLength={100} placeholder="Sample playlist"/></label><label>YouTube playlist URL<input name="url" type="url" required placeholder="https://www.youtube.com/playlist?list=…"/></label><label>Repeat every (hours)<input name="hours" type="number" min={1} max={8760} defaultValue={24} required/></label></div><div className="actions"><button className="primary" disabled={busy}>Add and queue</button><button type="button" disabled={busy} onClick={() => setAdding(false)}>Cancel</button></div></form>}
    <div className="split-grid"><section className="card"><div className="card-heading"><div><Clock3 size={18}/><h2>Scheduled jobs</h2></div><span className="count">{jobs.length}</span></div>
      {loading ? <p className="empty" role="status">Loading jobs…</p> : !jobs.length ? <p className="empty">No jobs yet. Add a playlist to get started.</p> : <div className="job-list">{jobs.map(job => <button className={`job-item ${selected === job.id ? 'selected' : ''}`} key={job.id} disabled={dirty && selected !== job.id} onClick={() => setSelected(job.id)} aria-pressed={selected === job.id}>
        <div className="row-between"><span className="job-icon">{job.kind === 'maintenance' ? <Sparkles size={18}/> : <Music2 size={18}/>}</span><State value={job.state === 'running' ? 'running' : job.enabled ? job.state : 'paused'}/></div><h3>{job.name}</h3><p>Every {job.interval_hours} hours</p><small>Next: {job.enabled ? job.next_run ? date(job.next_run) : 'Queued' : 'Paused'}</small><small>Last: {date(job.last_run)}</small></button>)}</div>}
    </section>{current ? <JobDetail key={current.id} job={current} refresh={refresh} onDirty={setDirty}/> : <section className="card empty"><h2>Everything in its own time.</h2><p>Select a job to manage its schedule and view recent runs.</p></section>}</div></>;
}

function JobDetail({ job, refresh, onDirty }: { job: Job; refresh: () => Promise<void>; onDirty: (value: boolean) => void }) {
  const [hours, setHours] = useState(job.interval_hours);
  const [enabled, setEnabled] = useState(Boolean(job.enabled));
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [runs, setRuns] = useState<Run[]>([]);
  const [detail, setDetail] = useState<Run | null>(null);
  const [older, setOlder] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(true);
  const exhausted = useRef(false);
  useEffect(() => { if (!dirty) { setHours(job.interval_hours); setEnabled(Boolean(job.enabled)); } }, [job.interval_hours, job.enabled, dirty]);
  const loadHistory = useCallback(async () => {
    const value = await api<Run[]>(`${jobPath(job.id)}/runs`);
    setRuns(old => old.length > 20 ? [...value, ...old.filter(r => r.id < (value.at(-1)?.id ?? Infinity))].slice(0, 100) : value);
    setOlder(!exhausted.current && value.length === 20); setHistoryLoading(false);
  }, [job.id]);
  useEffect(() => { let active = true; const load = () => loadHistory().catch(e => { if (active) { setError(errorText(e)); setHistoryLoading(false); } }); void load(); const interval = setInterval(() => { if (!document.hidden) void load(); }, 5000); return () => { active = false; clearInterval(interval); }; }, [loadHistory]);
  useEffect(() => { if (!detail) return; const id = detail.id; const interval = setInterval(() => { if (!document.hidden) api<Run>(`${jobPath(job.id)}/runs/${id}`).then(setDetail).catch(e => setError(errorText(e))); }, 5000); return () => clearInterval(interval); }, [detail?.id, job.id]);
  function changed() { setDirty(true); onDirty(true); }
  function discard() { setHours(job.interval_hours); setEnabled(Boolean(job.enabled)); setDirty(false); onDirty(false); }
  async function save(e: FormEvent) { e.preventDefault(); setBusy(true); setError(''); try { await api(jobPath(job.id), 'PUT', { interval_hours: hours, enabled }); setDirty(false); onDirty(false); setMessage('Schedule saved.'); await refresh(); } catch (e) { setError(errorText(e)); } finally { setBusy(false); } }
  async function run() { setBusy(true); setError(''); try { await api(`${jobPath(job.id)}/run`, 'POST'); setMessage('Run queued. It will start when the worker is free.'); await refresh(); await loadHistory(); } catch (e) { setError(errorText(e)); } finally { setBusy(false); } }
  async function loadOlder() { setHistoryLoading(true); try { const next = await api<Run[]>(`${jobPath(job.id)}/runs?before=${runs.at(-1)?.id}`); setRuns(old => [...old, ...next.filter(n => !old.some(r => r.id === n.id))]); exhausted.current = next.length < 20; setOlder(!exhausted.current); } catch (e) { setError(errorText(e)); } finally { setHistoryLoading(false); } }
  return <div className="stack"><section className="card form-card"><div className="row-between"><h2>{job.name}</h2><State value={job.enabled ? job.state : 'paused'}/></div><p>{job.kind === 'maintenance' ? 'Reviews household knowledge for duplicate facts and clearer wording.' : 'Imports new videos as audio and preserves the existing playlist library.'}</p>{job.kind === 'playlist' && <p className="small">Folder: {job.folder}</p>}
    <form onSubmit={save}><div className="form-grid"><label>Repeat every (hours)<input type="number" required min={1} max={8760} value={hours} onChange={e => { setHours(Number(e.target.value)); changed(); }}/></label><label className="check-label"><input type="checkbox" checked={enabled} onChange={e => { setEnabled(e.target.checked); changed(); }}/> Enabled</label></div><p className="small">24 hours = daily · 168 hours = weekly. Saving resets the next scheduled run.</p><div className="actions"><button className="primary" disabled={busy || !dirty}>Save schedule</button>{dirty && <button type="button" onClick={discard}>Discard changes</button>}<button type="button" disabled={busy || dirty || !job.enabled || ['running', 'queued'].includes(job.state)} onClick={run}><Play size={14}/> Run now</button></div></form>
    {dirty && <p role="status" className="small">Unsaved changes. Save or discard before selecting another job.</p>}{message && <p role="status" className="success">{message}</p>}{error && <div role="alert" className="notice error">{error}</div>}{job.error && <p className="error-text">Last run: {job.error}</p>}</section>
    <section className="card"><div className="card-heading"><div><Clock3 size={17}/><h2>Execution history</h2></div><span className="muted small">Latest 100 runs</span></div>{!runs.length ? <p className="empty" role="status">{historyLoading ? 'Loading history…' : 'No executions recorded yet.'}</p> : <div className="run-list">{runs.map(r => <div className="run-row" key={r.id}><div><State value={r.status}/><p>{date(r.started_at)}</p><span className="small muted">{r.trigger}{r.finished_at ? ` · ${Math.max(0, Math.round(r.finished_at - r.started_at))}s` : ''}</span></div><button onClick={() => api<Run>(`${jobPath(job.id)}/runs/${r.id}`).then(setDetail).catch(e => setError(errorText(e)))}>View logs #{r.id}</button></div>)}</div>}{older && <div className="card-footer"><button disabled={historyLoading} onClick={loadOlder}>Load older runs</button></div>}</section>
    {detail && <section className="card form-card"><h2>Execution #{detail.id}</h2><p>{detail.error || detail.summary || 'Execution is in progress.'}</p><p className="small">{detail.status} · {date(detail.started_at)}</p><pre className="log-output" tabIndex={0} aria-label="Execution logs">{detail.logs?.map(l => `${date(l.timestamp)}  ${l.level}  ${l.message}`).join('\n') || 'No log entries recorded.'}</pre><p className="small">Most recent 500 log entries.</p></section>}
  </div>;
}
