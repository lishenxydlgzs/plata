import { useEffect, useMemo, useRef, useState } from 'react';
import { HttpAgent } from '@ag-ui/client';
import { CopilotKitProvider, CopilotChatView, useAgent } from '@copilotkit/react-core/v2';
import { RefreshCw, Sparkles } from 'lucide-react';
import { api, errorText, newId, type Session } from './api';

function transcript(session: Session) {
  return session.messages.map(m => ({ id: m.role === 'user' ? m.request_id : m.id,
    role: m.role === 'user' ? 'user' as const : 'assistant' as const, content: m.text }));
}

const drafts = new Map<string, string>();
const NoAttachments = () => null;

type Props = { kind?: 'logbook' | 'graph'; suggestedMessage?: string; session: Session; selectedNoteId?: string; onComplete: (session: Session) => void; onBusy: (busy: boolean) => void };
export function AgentChat(props: Props) {
  const kind = props.kind ?? 'logbook';
  const agent = useMemo(() => new HttpAgent({ url: `/api/agents/${kind}`, agentId: kind,
    threadId: props.session.id, initialMessages: transcript(props.session),
    initialState: { selected_note_id: props.selectedNoteId ?? null } }), [props.session.id, kind]);
  const agents = useMemo(() => ({ [kind]: agent }), [agent]);
  return <CopilotKitProvider selfManagedAgents={agents} enableInspector={false}>
    <Chat {...props} />
  </CopilotKitProvider>;
}

function Chat({ kind = 'logbook', suggestedMessage, session, selectedNoteId, onComplete, onBusy }: Props) {
  const { agent } = useAgent({ agentId: kind });
  const [draft, setDraft] = useState(drafts.get(session.id) ?? '');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState(session.pending);
  const inFlight = useRef(false);
  const unsent = useRef<{ id: string; text: string; selectedNoteId: string | null } | null>(null);
  useEffect(() => () => { agent.abortRun(); }, [agent]);
  useEffect(() => { agent.setState({ selected_note_id: selectedNoteId ?? null }); }, [agent, selectedNoteId]);

  useEffect(() => { if (suggestedMessage) setDraft(suggestedMessage); }, [suggestedMessage]);
  useEffect(() => { drafts.set(session.id, draft); }, [session.id, draft]);
  const sessionPath = kind === 'graph' ? '/api/graph/review-sessions' : '/api/logbook/sessions';

  async function send(text: string, retry = false) {
    if (inFlight.current || !text.trim() || (pending && !retry)) return;
    inFlight.current = true; setBusy(true); onBusy(true); setError('');
    if (retry && pending) unsent.current = { id: pending.request_id, text: pending.text, selectedNoteId: pending.selected_note_id ?? null };
    else if (!unsent.current || unsent.current.text !== text) unsent.current = {
      id: newId(), text, selectedNoteId: selectedNoteId ?? null };
    const request = unsent.current;
    agent.setState({ selected_note_id: request.selectedNoteId });
    const previous = agent.messages.findIndex(m => m.id === request.id);
    if (previous >= 0) agent.setMessages(agent.messages.slice(0, previous));
    agent.addMessage({ id: request.id, role: 'user', content: request.text });
    setDraft('');
    try {
      let runError = '';
      await agent.runAgent({}, { onRunErrorEvent: ({ event }) => { runError = event.message; } });
      if (runError) throw new Error(runError);
      const saved = await api<Session>(`${sessionPath}/${session.id}`);
      agent.setMessages(transcript(saved)); setPending(saved.pending);
      if (saved.pending) throw new Error('Your message is saved. Retry to finish the response.');
      unsent.current = null; onComplete(saved);
    } catch (e) {
      setError(errorText(e));
      try {
        const saved = await api<Session>(`${sessionPath}/${session.id}`);
        agent.setMessages(transcript(saved)); setPending(saved.pending);
        if (saved.messages.some(m => m.role !== 'user' && m.request_id === request.id)) {
          setError(''); unsent.current = null; onComplete(saved);
        } else if (!saved.pending) setDraft(request.text);
      } catch { setDraft(request.text); }
    } finally { inFlight.current = false; setBusy(false); onBusy(false); }
  }

  return <div className="agent-chat">
    {agent.messages.length === 0 && <div className="chat-welcome"><span className="sparkle"><Sparkles size={23}/></span>
      <h3>{kind === 'graph' ? 'A clearer understanding.' : 'A little at a time.'}</h3><p>{kind === 'graph' ? 'Ask about a connection, review its source, or describe a correction.' : 'Share a moment, explore a thought, or pick up where you left off. Your notes take shape as we talk.'}</p></div>}
    {error && <div className="notice error" role="alert">{error}</div>}
    {pending && <div className="notice"><p>Your message is saved and waiting for a response.</p><button disabled={busy} onClick={() => send(pending.text, true)}><RefreshCw size={15}/> Retry saved message</button></div>}
    <CopilotChatView className="copilot-view" messages={agent.messages} isRunning={busy} welcomeScreen={false}
      inputValue={draft} onInputChange={setDraft} onSubmitMessage={text => { void send(text); }}
      onStop={() => agent.abortRun()} input={{ autoFocus: false, addMenuButton: NoAttachments, sendButton: { 'aria-label': busy ? 'Stop response' : 'Send message' }, textArea: { placeholder: kind === 'graph' ? 'Ask about a record or describe a correction…' : 'What’s on your mind?', 'aria-label': 'Message Plata', maxLength: 8000, disabled: Boolean(pending) } }} />
    <p className="chat-footnote">{kind === 'graph' ? 'Changes keep a record of their source and correction.' : 'Notes start private. Your original words stay intact.'}</p>
  </div>;
}
