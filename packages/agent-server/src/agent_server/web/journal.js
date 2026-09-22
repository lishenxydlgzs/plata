(() => {
  const el = id => document.getElementById(id);
  let selected = null, session = null, busy = false, pendingRequest = null;
  const drafts = new Map();
  const api = async (url, method='GET', body) => {
    const r = await fetch(url, {method, headers:{'Content-Type':'application/json'}, ...(body ? {body:JSON.stringify(body)} : {})});
    const data = await r.json();
    if (!r.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Unable to complete the request.');
    return data;
  };
  const status = text => el('journal-status').textContent = text;
  function view(kind) {
    el('journal-workspace').hidden = kind !== 'journal';
    el('graph-workspace').hidden = kind !== 'graph';
    for (const item of ['journal', 'graph']) {
      if (item === kind) el('view-' + item).setAttribute('aria-current', 'page');
      else el('view-' + item).removeAttribute('aria-current');
    }
  }
  const viewFromHash = () => view(location.hash === '#graph' ? 'graph' : 'journal');
  window.addEventListener('hashchange', viewFromHash);
  function lock(value) {
    busy = value;
    el('journal-workspace').querySelectorAll('button,textarea,select').forEach(e => e.disabled = value);
    el('journal-input').disabled = value || !!session?.pending;
    el('journal-form').querySelector('button').disabled = value || !!session?.pending;
  }
  function section(parent, title, text) {
    if (!text) return;
    const h = document.createElement('h3'); h.textContent = title;
    const p = document.createElement('p'); p.className = 'journal-prose'; p.textContent = text;
    parent.append(h,p);
  }
  function content(parent, note) {
    if (note.original_quotes?.length) {
      const h = document.createElement('h3'); h.textContent = 'Your words'; parent.append(h);
      for (const quote of note.original_quotes) {
        const q = document.createElement('blockquote'); q.className = 'journal-prose';
        q.textContent = quote.text; parent.append(q);
      }
    }
    section(parent, 'Observations', note.observations);
    section(parent, 'Your reflections', note.author_reflections);
    section(parent, 'Plata’s suggestions', note.suggestions);
    section(parent, 'Parking lot', note.parking_lot);
  }
  function renderNote(note) {
    selected = note;
    const box = el('journal-note'); box.replaceChildren();
    section(box, note.title, note.archived ? 'Removed from notes and conversation memory. Ask in chat to restore it.' : note.visibility === 'family' ? 'Available to family conversations' : 'Parents only');
    if (note.current) content(box, note.current);
    const revisions = el('journal-revisions'); revisions.replaceChildren();
    el('journal-history').hidden = note.revisions.length < 2;
    for (const r of note.revisions.slice(0,-1).reverse()) {
      const d = document.createElement('details'), summary = document.createElement('summary');
      summary.textContent = `Version ${r.version} · ${new Date(r.created_at).toLocaleString()}`;
      d.append(summary); content(d,r); revisions.append(d);
    }
    const links = el('journal-connections'); links.replaceChildren();
    for (const c of note.connections) {
      const b = document.createElement('button'); b.className = 'session'; b.textContent = c.title;
      b.onclick = () => { view('graph'); el('type').value=''; el('search').value=c.id; el('search').dispatchEvent(new Event('input')); };
      links.append(b);
    }
  }
  async function notes() {
    const all = await api('/api/journals');
    const list = el('journal-list'); list.replaceChildren();
    const active = all.filter(n => !n.archived);
    if (!active.length) list.textContent = 'Your notes will appear here.';
    for (const n of active) {
      const b = document.createElement('button'); b.className = 'session'; b.textContent = n.title;
      b.setAttribute('aria-current', n.id === selected?.id);
      b.onclick = async () => { if (busy) return; lock(true); try { renderNote(await api('/api/journals/'+n.id)); await notes(); } catch(e) { status(e.message); } finally { lock(false); } };
      list.append(b);
    }
    if (selected) renderNote(await api('/api/journals/'+selected.id));
  }
  async function sessions() {
    const all = await api('/api/logbook/sessions');
    const select = el('journal-sessions'); select.replaceChildren();
    const option = document.createElement('option'); option.value=''; option.textContent='New conversation'; select.append(option);
    for (const s of all) {
      const o=document.createElement('option'); o.value=s.id; o.textContent=s.title; select.append(o);
    }
    select.value=session?.id || '';
  }
  function renderChat(value) {
    session=value;
    const box=el('journal-chat'); box.replaceChildren();
    for (const m of value?.messages || []) {
      const b=document.createElement('div'); b.className='bubble '+(m.role==='user'?'user':'model'); b.textContent=m.text; box.append(b);
      if (m.changes?.length) {
        const p=document.createElement('div'); p.className='meta';
        p.textContent=m.changes.map(c=>({create_note:'Note created',update_note:'Note updated',delete_note:'Note removed',restore_note:'Note restored',set_note_visibility:'Sharing updated'}[c.tool])).join(' · '); box.append(p);
      }
    }
    box.scrollTop=box.scrollHeight;
    el('journal-retry').hidden=!value?.pending;
    status(value?.pending?'Message saved. Retry to finish.':'Write whenever something comes to mind.');
  }
  function saveDraft() { drafts.set(session?.id || '', el('journal-input').value); }
  async function switchChat(id) {
    if (busy) return;
    saveDraft(); lock(true);
    try {
      renderChat(id ? await api('/api/logbook/sessions/'+id) : null);
      pendingRequest=null;
      el('journal-input').value=drafts.get(id)||'';
      await sessions();
    } catch(e) { status(e.message); } finally { lock(false); }
  }
  el('journal-new').onclick=()=>switchChat('');
  el('journal-sessions').onchange=e=>switchChat(e.target.value);
  async function send(request) {
    lock(true); status('Thinking…');
    try {
      if (!session) session=await api('/api/logbook/sessions','POST',{});
      const result=await api('/api/logbook/sessions/'+session.id+'/messages','POST',request);
      renderChat(result); pendingRequest=null; el('journal-input').value=''; drafts.delete(session.id); drafts.delete('');
      const change=result.messages.at(-1)?.changes?.at(-1);
      if (change) renderNote(await api('/api/journals/'+change.note_id));
      await notes(); await sessions(); await loadGraph();
    } catch(e) {
      const error=e.message;
      if (session) {
        try {
          renderChat(await api('/api/logbook/sessions/'+session.id));
          if(session.pending) { el('journal-input').value=''; await sessions(); }
        } catch (_) { /* Keep the unsent draft when the server cannot be reached. */ }
      }
      status(error);
    } finally { lock(false); if(!session?.pending) el('journal-input').focus(); }
  }
  el('journal-form').onsubmit=e=>{
    e.preventDefault(); if(busy) return;
    const text=el('journal-input').value; if(!text.trim())return;
    if(!pendingRequest || pendingRequest.text!==text) pendingRequest={text,selected_note_id:selected?.id||null,request_id:globalThis.crypto?.randomUUID?.()||`${Date.now()}-${Math.random()}`};
    send(pendingRequest);
  };
  el('journal-retry').onclick=()=>{
    if (busy || !session?.pending) return;
    const m=session.pending;
    send({text:m.text,request_id:m.request_id,selected_note_id:m.selected_note_id||null});
  };
  viewFromHash();
  (async()=>{
    await notes();
    const all=await api('/api/logbook/sessions');
    if(all.length) renderChat(await api('/api/logbook/sessions/'+all[0].id));
    await sessions(); lock(false);
  })().catch(e=>status(e.message));
})();
