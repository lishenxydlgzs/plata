import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { MessageCircle, CheckCircle, Link, Users } from 'lucide-react';
import { api, date, errorText } from './api';

type Person = { id: string; name: string };
type Account = { user_id: number; person_id: string; label: string; first_reply: number | null; delivery_error: string | null };
type Invitation = { id: string; person_id: string; expires: number; state: string; user_id: number | null; label: string | null };
type Status = { configured: boolean; username: string | null; error: string | null; last_poll: number | null;
  memory_notice: string; accounts: Account[]; invitations: Invitation[] };
type InviteLink = { id: string; url: string; expires: number };

export async function telegramApi<T>(path = '', method = 'GET', body?: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api/telegram${path}`, { method, headers: { 'Content-Type': 'application/json', 'X-Plata-Workspace': '1' },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
  } catch { throw new Error('Cannot reach Plata. Keep your home server online and check your connection.'); }
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new Error(typeof data?.detail === 'string' ? data.detail : 'Could not complete this step. Please try again.');
  if (!data) throw new Error('Plata returned an unreadable response. Please try again.');
  return data as T;
}

export function TelegramCard() {
  return <section className="card telegram-discovery"><MessageCircle size={24}/><div><h2>Talk to Plata on Telegram</h2>
    <p>Connect your household once, then invite family members to chat from their phones.</p></div><a className="text-link" href="#connections">Set up Telegram →</a></section>;
}

export function Telegram() {
  const [status, setStatus] = useState<Status | null>(null);
  const [people, setPeople] = useState<Person[]>([]);
  const [person, setPerson] = useState('');
  const [newName, setNewName] = useState('');
  const [token, setToken] = useState('');
  const [editingBot, setEditingBot] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);
  const [link, setLink] = useState<InviteLink | null>(null);
  const [qr, setQr] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [loadError, setLoadError] = useState('');
  const requestVersion = useRef(0);
  const invitationCard = useRef<HTMLElement>(null);
  const reload = useCallback(async () => {
    const version = ++requestVersion.current;
    const [s, p] = await Promise.all([telegramApi<Status>(), api<Person[]>('/api/people')]);
    if (version === requestVersion.current) { setStatus(s); setPeople(p); setLoadError(''); }
  }, []);
  useEffect(() => {
    let active = true;
    const poll = () => reload().catch(e => { if (active) setLoadError(errorText(e)); });
    void poll(); const timer = window.setInterval(() => { if (!document.hidden) void poll(); }, 3000);
    return () => { active = false; ++requestVersion.current; clearInterval(timer); };
  }, [reload]);
  useEffect(() => {
    setQr(''); if (!link) return;
    let active = true; let objectUrl = '';
    fetch('/api/telegram/qr', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Plata-Workspace': '1' }, body: JSON.stringify({ url: link.url }) })
      .then(async response => { if (!response.ok) throw new Error('QR unavailable'); return response.blob(); })
      .then(blob => { if (active) { objectUrl = URL.createObjectURL(blob); setQr(objectUrl); } })
      .catch(() => { /* The open link and copy field remain usable if QR rendering fails. */ });
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [link]);
  async function action(work: () => Promise<void>) {
    setBusy(true); setError(''); setNotice('');
    try { await work(); await reload(); } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  }
  async function connect(event: FormEvent) {
    event.preventDefault();
    if (status?.configured && !window.confirm('Update the bot connection? Choosing a different bot will remove existing Telegram account links and invitations.')) return;
    await action(async () => { await telegramApi('/bot', 'PUT', { token }); setToken(''); setEditingBot(false); setLink(null); setNotice('Bot connected. Next, connect your own account.'); });
  }
  async function invite(event: FormEvent) {
    event.preventDefault();
    await action(async () => {
      const result = await telegramApi<InviteLink>('/invitations', 'POST', { person_id: person, shared_memory_acknowledged: acknowledged });
      setLink(result); setNotice('Invitation created. Open it yourself or share it directly with this person.');
    });
  }
  async function share() {
    if (!link) return;
    try {
      if (navigator.share) await navigator.share({ title: 'Connect to our household’s Plata', text: 'Open in Telegram and tap Start. Our household owner will confirm your account. If needed, install Telegram and create an account first.', url: link.url });
      else if (navigator.clipboard) { await navigator.clipboard.writeText(link.url); setNotice('Invitation copied. Send it directly to the intended person.'); }
      else setNotice('Select and copy the invitation link below, then send it directly to the intended person.');
    } catch (e) { if (!(e instanceof DOMException && e.name === 'AbortError')) setNotice('Select and copy the invitation link below to share it.'); }
  }
  const personName = (id: string) => people.find(p => p.id === id)?.name || 'Removed household profile';
  const connected = status?.configured;
  const hasAccount = !!status?.accounts.length;
  const hasReply = !!status?.accounts.some(a => a.first_reply);
  const currentInvite = status?.invitations.find(i => i.id === link?.id);
  const showLink = link && currentInvite?.state === 'waiting' && link.expires * 1000 > Date.now();
  const invitationVisible = Boolean(showLink);
  useEffect(() => {
    if (invitationVisible) {
      invitationCard.current?.focus({ preventScroll: true });
      invitationCard.current?.scrollIntoView({ block: 'start' });
    }
  }, [invitationVisible]);
  return <>
    <header className="page-heading"><div><span className="eyebrow">CONNECTIONS · TELEGRAM</span><h1>Plata, wherever you are.</h1><p>One household setup. A personal connection for each family member.</p></div><MessageCircle size={32}/></header>
    <ol className="telegram-steps" aria-label="Setup progress">{[[connected, 'Connect household bot'], [hasAccount, 'Confirm your account'], [hasReply, 'Have your first conversation']].map(([done, label], index) => <li key={String(label)} className={done ? 'complete' : ''}>{done ? <CheckCircle size={18}/> : <span>{index + 1}</span>}{String(label)}</li>)}</ol>
    {loadError && <div role="alert" className="notice error">{loadError}<button onClick={() => void reload().catch(e => setLoadError(errorText(e)))}>Retry</button></div>}
    {error && <div role="alert" className="notice error">{error}</div>}
    {notice && <div role="status" className="notice">{notice}</div>}
    {!status ? <p className="empty" role="status">Loading Telegram settings…</p> : <>
      {status.error && <div role="alert" className="notice error">{status.error}</div>}
      <div className="telegram-grid">
        <div className="telegram-column">
          {(!connected || editingBot) ? <form className="card form-card" onSubmit={connect}>
            <h2>1. Create your household’s bot</h2><p>You only do this once. Each person needs a Telegram account; only the household owner creates the bot.</p>
            <ol className="setup-instructions"><li><a className="text-link" href="https://t.me/BotFather" target="_blank" rel="noreferrer">Open official BotFather ↗</a> in Telegram.</li>
              <li>Send <code>/newbot</code>. Choose a display name, such as <strong>Plata</strong>, and an available username ending in <code>bot</code>, such as <code>sample_household_plata_bot</code>.</li>
              <li>Copy the token BotFather gives you and paste it below. Keep it secret; it controls your bot.</li></ol>
            <label>Bot token<input type="password" autoComplete="off" required value={token} onChange={e => setToken(e.target.value)} placeholder="Paste the complete BotFather token"/></label>
            <p className="small">Saved only on your home server. No terminal commands or router changes needed. Keep the server online so Plata can reply.</p>
            <div className="telegram-actions"><button className="primary" disabled={busy || !token.trim()}>{busy ? 'Connecting…' : 'Connect bot'}</button>{connected && <button type="button" disabled={busy} onClick={() => { setEditingBot(false); setToken(''); }}>Cancel</button>}</div>
          </form> : <section className="card form-card"><h2><CheckCircle size={17}/> Household bot connected</h2><p><strong>@{status.username}</strong></p>
            <p>{status.last_poll ? `Last connected to Telegram: ${date(status.last_poll)}` : 'Establishing the Telegram connection…'}</p><p className="small">Your home server must stay online. If it is offline, the bot cannot send an outage message.</p>
            <div className="telegram-actions"><button disabled={busy} onClick={() => setEditingBot(true)}>Update token or bot</button><button disabled={busy} onClick={() => { if (window.confirm('Disconnect the household bot and revoke all Telegram accounts and invitations? Shared memories will remain.')) void action(async () => { await telegramApi('/bot', 'DELETE'); setLink(null); }); }}>Disconnect bot</button></div>
          </section>}
          <section className="card form-card"><h2>What is shared?</h2><p>{status.memory_notice}</p><p className="small">Use this settings page only on your trusted home network. Anyone with access to the household workspace can manage connections.</p></section>
          {connected && <form className="card form-card" onSubmit={invite}><h2>2. {hasAccount ? 'Invite a family member' : 'Connect your own account'}</h2><p>Choose who will use this invitation. After they tap Start, confirm their Telegram account below.</p>
            <label>Household person<select required value={person} onChange={e => { setPerson(e.target.value); setAcknowledged(false); }}><option value="">Choose a person</option>{people.map(p => <option key={p.id} value={p.id} disabled={status.accounts.some(a => a.person_id === p.id)}>{p.name}{status.accounts.some(a => a.person_id === p.id) ? ' — connected' : ''}</option>)}</select></label>
            <details><summary>Add a household person</summary><label>Name<input value={newName} maxLength={120} onChange={e => setNewName(e.target.value)} placeholder="Sample Person"/></label><button type="button" disabled={busy || !newName.trim()} onClick={() => void action(async () => { const p = await api<Person>('/api/people', 'POST', { name: newName.trim() }); setPerson(p.id); setAcknowledged(false); setNewName(''); })}>Add person</button></details>
            <label className="telegram-check"><input type="checkbox" checked={acknowledged} onChange={e => setAcknowledged(e.target.checked)}/><span>I understand that messages can enter shared household memory, and will explain this to anyone I invite.</span></label>
            <button className="primary" disabled={busy || !person || !acknowledged}><Link size={16}/>Create invitation</button><p className="small">Single use. Expires in 15 minutes. A new invitation replaces any earlier invitation for this person.</p>
          </form>}
        </div>
        <div className="telegram-column">
          {showLink && <section ref={invitationCard} tabIndex={-1} className="card form-card"><h2>Open Telegram and tap Start</h2><p>Invitation for {personName(currentInvite.person_id)} · expires {date(link.expires)}.</p><a className="primary telegram-link" href={link.url} target="_blank" rel="noreferrer">Open in Telegram ↗</a>
            {qr && <img className="telegram-qr" src={qr} width={220} height={220} alt="Scan this invitation with your phone to open Plata in Telegram"/>}
            <button onClick={() => void share()}>Share invitation / copy link</button><label>Invitation link<input readOnly value={link.url} onFocus={e => e.target.select()}/></label>
            <p className="small">On a computer? Scan the QR code with your phone. If Telegram is not installed, install it and create an account, then open this invitation again.</p><p>After tapping Start, return here to confirm the account. A family member can stay in Telegram while you confirm.</p>
          </section>}
          {connected && <section className="card form-card"><h2>Connection requests</h2>{!status.invitations.length && <p>No pending invitations. Create one to connect an account.</p>}
            {status.invitations.map(i => <article className="telegram-request" key={i.id}><h3>{personName(i.person_id)}</h3><span className="badge">{i.state === 'pending' ? 'Needs your confirmation' : i.state === 'expired' ? 'Expired' : 'Waiting for Start'}</span>
              {i.state === 'pending' ? <><p>Telegram: <strong>{i.label}</strong></p><p className="small">Account ID: {i.user_id}. Confirm with the intended person that this is their account. Display names alone are not proof of identity.</p><button className="primary" disabled={busy} onClick={() => void action(async () => { await telegramApi(`/invitations/${i.id}/approve`, 'POST'); setLink(null); setPerson(''); setAcknowledged(false); setNotice('Account connected. Send a message to Plata in Telegram.'); })}>Confirm this account</button></> : <p className="small">{i.state === 'expired' ? 'Create a new invitation to try again.' : 'Open the invitation and tap Start. Lost the link? Create a new invitation.'}</p>}
              <button disabled={busy} onClick={() => void action(async () => { await telegramApi(`/invitations/${i.id}`, 'DELETE'); if (link?.id === i.id) setLink(null); })}>{i.state === 'pending' ? 'Reject request' : 'Remove invitation'}</button>
            </article>)}
          </section>}
          {connected && <section className="card form-card"><h2><Users size={17}/> Connected accounts</h2>{!hasAccount && <p>Your confirmed accounts will appear here.</p>}{status.accounts.map(a => <article className="telegram-request" key={a.user_id}><h3>{personName(a.person_id)}</h3><p>{a.label}</p><p className="small">{a.first_reply ? `First conversation complete · ${date(a.first_reply)}` : 'Connected. Send a message in Telegram to complete your first conversation.'}</p>{a.delivery_error && <p role="alert" className="error-text">{a.delivery_error}</p>}
            <button disabled={busy} onClick={() => { if (window.confirm(`Disconnect ${personName(a.person_id)} from Telegram? Existing shared memory will remain.`)) void action(async () => { await telegramApi(`/accounts/${a.user_id}`, 'DELETE'); }); }}>Disconnect account</button></article>)}</section>}
          <section className="card form-card"><h2>3. Say hello</h2><p>Once connected, send a normal message to Plata in Telegram.</p><blockquote>Help me think of a rainy-day activity.</blockquote><p>Use <code>/help</code> for capabilities, <code>/new</code> for a fresh chat, or <code>/disconnect</code> to remove access. Starting a fresh chat does not delete shared household memory.</p>{hasReply && <div className="notice"><CheckCircle size={17}/> First conversation complete. You can now invite another family member.</div>}</section>
        </div>
      </div>
    </>}
  </>;
}
