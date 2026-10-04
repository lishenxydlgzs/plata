import { StrictMode, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { BookOpen, Network, House, Workflow } from 'lucide-react';
import { Logbook } from './Logbook';
import { Knowledge } from './Knowledge';
import { Household } from './Household';
import { Jobs } from './Jobs';
import '@copilotkit/react-core/v2/styles.css';
import './styles.css';
import plataIcon from '../../../assets/brand/plata-icon.png';

function App() {
  const [hash, setHash] = useState(location.hash);
  useEffect(() => { const changed = () => setHash(location.hash); window.addEventListener('hashchange', changed); return () => window.removeEventListener('hashchange', changed); }, []);
  const route = hash.slice(1).split('?')[0];
  const page = ({ graph: 'knowledge', journal: 'logbook' } as Record<string, string>)[route] ?? (route || 'logbook');
  const title = ({knowledge:'Knowledge', household:'Household', jobs:'Background jobs'} as Record<string,string>)[page] || 'Logbook';
  const record = new URLSearchParams(hash.split('?')[1] ?? '').get('record') ?? '';
  return <div className="workspace"><a className="skip-link" href="#main" onClick={event => { event.preventDefault(); document.getElementById('main')?.focus(); }}>Skip to content</a>
    <aside className="sidebar"><a className="brand" href="/workspace/"><img className="brand-mark" src={plataIcon} alt="" width={56} height={56}/><span>Plata<span className="brand-caption">Play. Teach. Along.</span></span></a>
      <span className="nav-label">YOUR HOUSEHOLD</span><nav aria-label="Main navigation">
        <a href="#logbook" aria-current={page === 'logbook' ? 'page' : undefined}><BookOpen size={19}/>Logbook</a>
        <a href="#knowledge" aria-current={page === 'knowledge' ? 'page' : undefined}><Network size={19}/>Knowledge</a>
        <a href="#household" aria-current={page === 'household' ? 'page' : undefined}><House size={19}/>Household</a>
        <a href="#jobs" aria-current={page === 'jobs' ? 'page' : undefined}><Workflow size={19}/>Background jobs</a>
      </nav><div className="sidebar-bottom"><House size={17}/><span>Play comes first.<small>Learning comes along.</small></span></div>
    </aside><main id="main" tabIndex={-1}><div className="topbar"><span>Household workspace <span className="slash">/</span> <strong>{title}</strong></span><span className="local-label"><span className="presence"/> Your private workspace</span></div><div className="page">{page === "knowledge" ? <Knowledge record={record}/> : page === "household" ? <Household/> : page === "jobs" ? <Jobs/> : <Logbook/>}</div></main>
  </div>;
}

createRoot(document.getElementById('root')!).render(<StrictMode><App/></StrictMode>);
