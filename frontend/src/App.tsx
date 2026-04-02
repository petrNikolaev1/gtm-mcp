import { BrowserRouter, Routes, Route, Link, useLocation, useParams, Navigate } from 'react-router-dom'
import { useState, useEffect, createContext, useContext, useRef } from 'react'
import LoginPage from './pages/LoginPage'
import SetupPage from './pages/SetupPage'
import PipelinePage from './pages/PipelinePage'
import PromptsPage from './pages/PromptsPage'
import AccountPage from './pages/AccountPage'
import ProjectsPage from './pages/ProjectsPage'
import LearningPage from './pages/LearningPage'
import ConversationsPage from './pages/ConversationsPage'
import CampaignsPage from './pages/CampaignsPage'
import CampaignDetailPage from './pages/CampaignDetailPage'

import { CRMPage } from './pages/CRMStubPage'
import { TasksPage } from './pages/TasksStubPage'
import { ToastProvider } from './components/ToastProvider'

// MCP-specific CRM defaults: hide useless columns
if (!localStorage.getItem('crm:hiddenColumns:mcp_initialized')) {
  const mcpHidden = ['Done', 'Status', 'Client Status', 'Source', 'Suitable For', 'Location', 'Website', 'Added', 'Project']
  localStorage.setItem('crm:hiddenColumns', JSON.stringify(mcpHidden))
  localStorage.setItem('crm:hiddenColumns:mcp_initialized', '1')
}

// Theme
const ThemeCtx = createContext({ dark: true, toggle: () => {} })
export const useTheme = () => useContext(ThemeCtx)

// Project context
const ProjectCtx = createContext<{ project: any; projects: any[]; setProject: (p: any) => void; reload: () => void }>({ project: null, projects: [], setProject: () => {}, reload: () => {} })
export const useProject = () => useContext(ProjectCtx)

// Auth
export function getToken() { return localStorage.getItem('mcp_token') || '' }
export function authHeaders() { return { 'X-MCP-Token': getToken(), 'Content-Type': 'application/json' } }

const ROUTE_TITLES: Record<string, string> = {
  '/': 'Pipeline — MCP LeadGen',
  '/setup': 'Setup — MCP LeadGen',
  '/pipeline': 'Pipeline — MCP LeadGen',
  '/campaigns': 'Campaigns — MCP LeadGen',
  '/crm': 'CRM — MCP LeadGen',
  '/tasks': 'Tasks — MCP LeadGen',
  '/projects': 'Projects — MCP LeadGen',
  '/learning': 'Learning — MCP LeadGen',
  '/conversations': 'Logs — MCP LeadGen',
  '/account': 'Account — MCP LeadGen',
}

function NavLink({ to, children }: { to: string; children: React.ReactNode }) {
  const loc = useLocation()
  const active = loc.pathname === to || (to !== '/' && to !== '/setup' && loc.pathname.startsWith(to))
  return (
    <Link to={to} style={{
      padding: '4px 10px', borderRadius: 6, fontSize: 13, fontWeight: 500,
      color: active ? 'var(--text)' : 'var(--text-muted)',
      background: active ? 'var(--active-bg)' : 'transparent',
      textDecoration: 'none', whiteSpace: 'nowrap',
    }}>{children}</Link>
  )
}

function PageTitle() {
  const loc = useLocation()
  useEffect(() => {
    const base = '/' + loc.pathname.split('/')[1]
    document.title = ROUTE_TITLES[base] || 'MCP LeadGen'
  }, [loc.pathname])
  return null
}

function ProjectSelector() {
  const { project, projects, setProject } = useProject()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', h); return () => document.removeEventListener('mousedown', h)
  }, [])

  return (
    <div ref={ref} style={{ position: 'relative', marginRight: 12 }}>
      <button onClick={() => setOpen(!open)} style={{
        display: 'flex', alignItems: 'center', gap: 6, padding: '4px 10px', borderRadius: 6, fontSize: 13, fontWeight: 500,
        background: 'var(--active-bg)', color: 'var(--text-secondary)', border: 'none', cursor: 'pointer',
      }}>
        {project ? project.name : 'All projects'} <span style={{ fontSize: 10, opacity: 0.5 }}>▼</span>
      </button>
      {open && (
        <div style={{ position: 'absolute', top: '100%', left: 0, marginTop: 4, zIndex: 50, background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 8, padding: 4, minWidth: 240, boxShadow: '0 8px 24px rgba(0,0,0,0.3)' }}>
          <div onClick={() => { setProject(null); setOpen(false) }} style={{ padding: '6px 10px', borderRadius: 4, cursor: 'pointer', fontSize: 13, color: !project ? 'var(--text)' : 'var(--text-muted)', background: !project ? 'var(--active-bg)' : 'transparent' }}>All projects</div>
          {projects.map((p: any) => (
            <div key={p.id} onClick={() => { setProject(p); setOpen(false) }} style={{ padding: '6px 10px', borderRadius: 4, cursor: 'pointer', fontSize: 13, color: project?.id === p.id ? 'var(--text)' : 'var(--text-muted)', background: project?.id === p.id ? 'var(--active-bg)' : 'transparent' }}>{p.name}</div>
          ))}
        </div>
      )}
    </div>
  )
}

import { useTheme as useThemeStore } from './hooks/useTheme'
import { useAppStore } from './store/appStore'

/** Watches URL ?project= param and auto-selects the project in the top-left selector */
/** Redirects /projects/:id → /projects?project=:id so project gets auto-selected */
function ProjectRedirect() {
  const { id } = useParams()
  return <Navigate to={`/projects?project=${id}`} replace />
}

function ProjectFromURL() {
  const loc = useLocation()
  const { projects, setProject } = useProject()
  useEffect(() => {
    const params = new URLSearchParams(loc.search)
    const pid = params.get('project')
    if (pid && projects.length) {
      const found = projects.find((p: any) => p.id === +pid)
      if (found) setProject(found)
    }
  }, [loc.search, projects])
  return null
}

export default function App() {
  const { isDark: dark, toggle: toggleTheme } = useThemeStore()
  const [project, setPS] = useState<any>(null)
  const [projects, setProjects] = useState<any[]>([])

  // No token = show login page (full screen, no header)
  const hasToken = !!getToken()
  if (!hasToken) {
    return (
      <BrowserRouter>
        <LoginPage />
      </BrowserRouter>
    )
  }

  const loadProjects = () => {
    const t = getToken(); if (!t) return
    fetch('/api/pipeline/projects', { headers: { 'X-MCP-Token': t } }).then(r => r.ok ? r.json() : []).then(setProjects)
  }
  useEffect(() => { loadProjects() }, [])

  // Sync MCP project to Zustand appStore (for @main components like OperatorActionsPage)
  const syncAppStore = (p: any, pList: any[]) => {
    useAppStore.getState().setCurrentProject(p)
    useAppStore.getState().setProjects(pList)
  }

  const setProject = (p: any) => {
    setPS(p)
    p ? localStorage.setItem('mcp-project', String(p.id)) : localStorage.removeItem('mcp-project')
    syncAppStore(p, projects)
  }
  useEffect(() => {
    if (!projects.length) return
    // Priority 1: ?project=ID from URL query string
    const url = new URL(window.location.href)
    const qp = url.searchParams.get('project')
    if (qp) { const f = projects.find((p: any) => p.id === +qp); if (f) { setPS(f); localStorage.setItem('mcp-project', String(f.id)); syncAppStore(f, projects); return } }
    // Priority 2: localStorage
    const s = localStorage.getItem('mcp-project')
    if (s) { const f = projects.find((p: any) => p.id === +s); if (f) { setPS(f); syncAppStore(f, projects) } }
    // Priority 3: first project (auto-select for single-project users)
    if (!s && !qp && projects.length === 1) { setPS(projects[0]); syncAppStore(projects[0], projects) }
  }, [projects])

  return (
    <ThemeCtx.Provider value={{ dark, toggle: toggleTheme }}>
      <ToastProvider>
      <ProjectCtx.Provider value={{ project, projects, setProject, reload: loadProjects }}>
        <BrowserRouter>
          <ProjectFromURL />
          <PageTitle />
          <div style={{ height: '100vh', display: 'flex', flexDirection: 'column', background: dark ? '#1e1e1e' : '#f5f5f5', color: dark ? '#d4d4d4' : '#333' }}>
            <header style={{ height: 48, borderBottom: `1px solid ${dark ? '#333' : '#e0e0e0'}`, display: 'flex', alignItems: 'center', padding: '0 16px', background: dark ? '#252526' : '#ffffff' }}>
              <Link to="/" style={{ display: 'flex', alignItems: 'center', gap: 8, marginRight: 16, textDecoration: 'none' }}>
                <div style={{ width: 24, height: 24, borderRadius: 6, display: 'flex', alignItems: 'center', justifyContent: 'center', background: dark ? '#d4d4d4' : '#333' }}>
                  <span style={{ fontWeight: 700, fontSize: 11, color: dark ? '#1e1e1e' : 'white' }}>M</span>
                </div>
              </Link>
              <ProjectSelector />
              <nav aria-label="Main navigation" style={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                <NavLink to="/pipeline">Pipeline</NavLink>
                <NavLink to="/projects">Projects</NavLink>
                <NavLink to="/campaigns">Campaigns</NavLink>
                <NavLink to="/crm">CRM</NavLink>
                <NavLink to="/tasks">Tasks</NavLink>
                <NavLink to="/learning">Learning</NavLink>
                <NavLink to="/conversations">Logs</NavLink>
                <NavLink to="/setup">Setup</NavLink>
                <NavLink to="/account">Account</NavLink>
              </nav>
              <div style={{ marginLeft: 'auto' }}>
                <button onClick={toggleTheme} style={{ padding: '4px 8px', borderRadius: 4, fontSize: 12, border: 'none', cursor: 'pointer', background: 'transparent', color: 'var(--text-muted)' }}>{dark ? '☀' : '☾'}</button>
              </div>
            </header>
            <div className="mcp-content-area" style={{ flex: 1, overflowY: 'auto', height: 'calc(100vh - 48px)' }}>
              <Routes>
                <Route path="/" element={<PipelineRunsPage />} />
                <Route path="/setup" element={<SetupPage />} />
                <Route path="/pipeline" element={<PipelineRunsPage />} />
                <Route path="/pipeline/:runId" element={<PipelinePage />} />
                <Route path="/pipeline/:runId/prompts" element={<PromptsPage />} />
                <Route path="/campaigns/:id" element={<CampaignDetailPage />} />
                <Route path="/campaigns" element={<CampaignsPage />} />
                <Route path="/crm" element={<CRMPage />} />
                <Route path="/tasks" element={<TasksPage />} />
                <Route path="/tasks/:tab" element={<TasksPage />} />
                <Route path="/projects" element={<ProjectsPage />} />
                <Route path="/projects/:id" element={<ProjectRedirect />} />
                <Route path="/learning" element={<LearningPage />} />
                <Route path="/conversations" element={<ConversationsPage />} />
                <Route path="/account" element={<AccountPage />} />
              </Routes>
            </div>
          </div>
        </BrowserRouter>
      </ProjectCtx.Provider>
      </ToastProvider>
    </ThemeCtx.Provider>
  )
}

// Source type → human label
const SOURCE_LABELS: Record<string, string> = {
  'apollo.companies.api': 'Apollo',
  'apollo.people.emulator': 'Apollo People',
  'apollo.companies.emulator': 'Apollo Free',
  'clay.companies.emulator': 'Clay',
  'google_sheets.companies.manual': 'Google Sheet',
  'csv.companies.manual': 'CSV Import',
  'manual.companies.manual': 'Manual',
}

// Pipeline runs list
function PipelineRunsPage() {
  const [runs, setRuns] = useState<any[]>([])
  const { project } = useProject()
  useEffect(() => {
    const t = localStorage.getItem('mcp_token')
    fetch('/api/pipeline/runs', { headers: t ? { 'X-MCP-Token': t } : {} }).then(r => r.json()).then(setRuns).catch(e => console.error('Failed to load runs:', e))
  }, [])

  // Filter by selected project (top-left dropdown)
  const filtered = project ? runs.filter(r => r.project_id === project.id) : runs

  return (
    <div style={{ maxWidth: 1100, margin: '0 auto', padding: 24 }}>
      <div style={{ fontSize: 11, fontWeight: 500, textTransform: 'uppercase', letterSpacing: 1, color: 'var(--text-muted)', marginBottom: 12 }}>Pipeline Runs</div>
      {filtered.length === 0 ? <div style={{ color: 'var(--text-muted)', padding: '40px 0', textAlign: 'center' }}>{runs.length > 0 ? 'No runs for this project.' : 'No runs yet.'}</div> : (
        <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
          <thead><tr style={{ textAlign: 'left', fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.5, color: 'var(--text-muted)' }}>
            <th style={{ paddingBottom: 8 }}>Run</th>
            <th style={{ paddingBottom: 8 }}>Project</th>
            <th style={{ paddingBottom: 8 }}>Segment</th>
            <th style={{ paddingBottom: 8 }}>Source</th>
            <th style={{ paddingBottom: 8 }}>Found</th>
            <th style={{ paddingBottom: 8 }}>Targets</th>
            <th style={{ paddingBottom: 8 }}>People</th>
            <th style={{ paddingBottom: 8 }}>Credits</th>
            <th style={{ paddingBottom: 8 }}>Destination</th>
            <th style={{ paddingBottom: 8 }}>Phase</th>
            <th style={{ paddingBottom: 8 }}>Created</th>
          </tr></thead>
          <tbody>{filtered.map((r: any) => (
            <tr key={r.id} style={{ borderTop: '1px solid var(--border)' }}>
              <td style={{ padding: '8px 8px 8px 0' }}><Link to={`/pipeline/${r.id}`} style={{ color: 'var(--text-link)' }}>#{r.id}</Link></td>
              <td style={{ padding: '8px 8px 8px 0', fontSize: 12, color: 'var(--text-secondary)' }}>{r.project_name || '—'}</td>
              <td style={{ padding: '8px 8px 8px 0', fontSize: 11 }}>
                {r.segment ? (
                  <span style={{ padding: '1px 5px', borderRadius: 3, background: 'rgba(99,102,241,0.12)', color: '#818cf8' }}>{r.segment}</span>
                ) : '—'}
              </td>
              <td style={{ padding: '8px 8px 8px 0', fontSize: 12 }}>{SOURCE_LABELS[r.source_type] || r.source_type}</td>
              <td style={{ padding: '8px 8px 8px 0' }}>
                {r.raw_companies > 0 ? <Link to={`/pipeline/${r.id}`} style={{ color: 'var(--text-link)' }}>{r.raw_companies}</Link> : <span style={{ color: 'var(--text-muted)' }}>0</span>}
              </td>
              <td style={{ padding: '8px 8px 8px 0' }}>
                {r.targets > 0 ? <Link to={`/pipeline/${r.id}?status=target`} style={{ color: 'var(--success)', fontWeight: 600 }}>{r.targets}</Link> : <span style={{ color: 'var(--text-muted)' }}>0</span>}
              </td>
              <td style={{ padding: '8px 8px 8px 0' }}>
                {r.people > 0 ? <Link to={`/crm?pipeline=${r.id}`} style={{ color: 'var(--text-link)' }}>{r.people}</Link> : <span style={{ color: 'var(--text-muted)' }}>0</span>}
              </td>
              <td style={{ padding: '8px 8px 8px 0', color: 'var(--text-secondary)', fontSize: 12 }}>{r.credits_used || 0}</td>
              <td style={{ padding: '8px 8px 8px 0', fontSize: 11 }}>
                <span style={{ padding: '2px 6px', borderRadius: 4, background: 'rgba(99,102,241,0.1)', color: '#818cf8' }}>SmartLead</span>
              </td>
              <td style={{ padding: '8px 8px 8px 0' }}><span style={{ padding: '2px 6px', borderRadius: 4, fontSize: 11, background: 'var(--active-bg)' }}>{r.phase}</span></td>
              <td style={{ padding: '8px 0', color: 'var(--text-muted)', fontSize: 12 }}>{r.created_at ? new Date(r.created_at).toLocaleDateString() : ''}</td>
            </tr>
          ))}</tbody>
        </table>
      )}
    </div>
  )
}
