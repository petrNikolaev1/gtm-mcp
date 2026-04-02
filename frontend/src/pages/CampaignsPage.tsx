import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { authHeaders, useProject } from '../App'

const API = '/api'

export default function CampaignsPage() {
  const [campaigns, setCampaigns] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [expanded, setExpanded] = useState<number | null>(null)
  const { project } = useProject()

  useEffect(() => {
    setLoading(true)
    fetch(`${API}/pipeline/campaigns`, { headers: authHeaders() })
      .then(r => r.ok ? r.json() : [])
      .then(setCampaigns)
      .catch(e => console.error('Failed to load campaigns:', e))
      .finally(() => setLoading(false))
  }, [])

  const filtered = project ? campaigns.filter(c => c.project_id === project.id) : campaigns

  const statusColor = (s: string) => {
    if (s === 'active' || s === 'ACTIVE') return '#22c55e'
    if (s === 'mcp_draft') return '#818cf8'
    if (s === 'draft' || s === 'DRAFT' || s === 'DRAFTED') return '#f59e0b'
    if (s === 'paused' || s === 'PAUSED') return '#6b7280'
    return 'var(--text-muted)'
  }

  return (
    <div style={{ maxWidth: 1100, margin: '0 auto', padding: 24 }}>
      <div style={{ fontSize: 11, fontWeight: 500, textTransform: 'uppercase', letterSpacing: 1, color: 'var(--text-muted)', marginBottom: 16 }}>Campaigns</div>

      {loading ? <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)' }}>Loading...</div> :
       filtered.length === 0 ? <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)' }}>No campaigns yet. Run a pipeline to create one.</div> : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {filtered.map((c: any) => (
            <div key={c.id} style={{ border: '1px solid var(--border)', borderRadius: 8, background: 'var(--bg-card)', overflow: 'hidden' }}>
              {/* Campaign header */}
              <div
                onClick={() => setExpanded(expanded === c.id ? null : c.id)}
                style={{ padding: '14px 20px', cursor: 'pointer', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                  <span style={{ width: 8, height: 8, borderRadius: '50%', background: statusColor(c.status), flexShrink: 0 }} />
                  <div>
                    <Link to={`/campaigns/${c.id}`} onClick={e => e.stopPropagation()} style={{ fontSize: 14, fontWeight: 600, color: 'var(--text)', textDecoration: 'none' }}>{c.name}</Link>
                    <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>
                      {c.project_name && <span>{c.project_name} · </span>}
                      <span style={{ textTransform: 'uppercase', fontWeight: 500, color: statusColor(c.status) }}>{c.status}</span>
                      {c.leads_count > 0 && <span> · {c.leads_count} leads</span>}
                      {c.email_accounts?.length > 0 && <span> · {c.email_accounts.length} accounts</span>}
                      {c.timezone && <span> · {c.timezone}</span>}
                      {c.created_by === 'mcp' && (
                        <span style={{ marginLeft: 6, padding: '1px 6px', borderRadius: 4, background: 'rgba(99,102,241,0.15)', color: '#818cf8', fontSize: 10, fontWeight: 600 }}>MCP</span>
                      )}
                      {c.monitoring_enabled && (
                        <span style={{ marginLeft: 6, padding: '1px 6px', borderRadius: 4, background: 'rgba(34,197,94,0.15)', color: '#22c55e', fontSize: 10, fontWeight: 600 }}>LISTENING</span>
                      )}
                    </div>
                  </div>
                </div>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  {c.pipeline_run_id && (
                    <Link to={`/pipeline/${c.pipeline_run_id}`} onClick={e => e.stopPropagation()}
                      style={{ fontSize: 12, padding: '4px 10px', borderRadius: 6, background: 'var(--bg)', color: 'var(--text-secondary)', textDecoration: 'none', border: '1px solid var(--border)' }}>
                      Pipeline #{c.pipeline_run_id}
                    </Link>
                  )}
                  {c.smartlead_url && (
                    <a href={c.smartlead_url} target="_blank" rel="noopener noreferrer" onClick={e => e.stopPropagation()}
                      style={{ fontSize: 12, padding: '4px 10px', borderRadius: 6, background: 'rgba(99,102,241,0.12)', color: '#818cf8', textDecoration: 'none', border: '1px solid rgba(99,102,241,0.25)' }}>
                      SmartLead ↗
                    </a>
                  )}
                  {(c.status === 'draft' || c.status === 'DRAFT' || c.status === 'DRAFTED') && (
                    <button onClick={async (e) => {
                      e.stopPropagation()
                      if (!confirm('Are you sure? This will start sending emails to real leads.')) return
                      const r = await fetch(`${API}/pipeline/campaigns/${c.id}/activate`, { method: 'POST', headers: authHeaders() })
                      if (r.ok) { setCampaigns(prev => prev.map(x => x.id === c.id ? {...x, status: 'active'} : x)) }
                    }} style={{ fontSize: 12, padding: '4px 12px', borderRadius: 6, background: '#22c55e', color: 'white', border: 'none', cursor: 'pointer', fontWeight: 500 }}>
                      Activate
                    </button>
                  )}
                  {c.status === 'active' && (
                    <>
                      <button onClick={async (e) => {
                        e.stopPropagation()
                        const enabled = !c.monitoring_enabled
                        const r = await fetch(`${API}/pipeline/campaigns/${c.id}/monitoring`, {
                          method: 'POST', headers: { ...authHeaders(), 'Content-Type': 'application/json' },
                          body: JSON.stringify({ enabled })
                        })
                        if (r.ok) { setCampaigns(prev => prev.map(x => x.id === c.id ? {...x, monitoring_enabled: enabled} : x)) }
                      }} style={{ fontSize: 11, padding: '4px 10px', borderRadius: 6, background: c.monitoring_enabled ? 'rgba(34,197,94,0.15)' : 'var(--bg)', color: c.monitoring_enabled ? '#22c55e' : 'var(--text-muted)', border: '1px solid var(--border)', cursor: 'pointer' }}>
                        {c.monitoring_enabled ? 'Listening' : 'Not Listening'}
                      </button>
                      <button onClick={async (e) => {
                        e.stopPropagation()
                        const r = await fetch(`${API}/pipeline/campaigns/${c.id}/pause`, { method: 'POST', headers: authHeaders() })
                        if (r.ok) { setCampaigns(prev => prev.map(x => x.id === c.id ? {...x, status: 'paused'} : x)) }
                      }} style={{ fontSize: 12, padding: '4px 12px', borderRadius: 6, background: '#f59e0b', color: 'white', border: 'none', cursor: 'pointer' }}>
                        Pause
                      </button>
                    </>
                  )}
                  <span style={{ fontSize: 14, color: 'var(--text-muted)' }}>{expanded === c.id ? '▲' : '▼'}</span>
                </div>
              </div>

              {/* Expanded: sequence preview */}
              {expanded === c.id && (
                <div style={{ borderTop: '1px solid var(--border)', padding: '16px 20px', background: 'var(--bg)' }}>
                  {/* Sequence steps */}
                  {c.sequence_steps && c.sequence_steps.length > 0 ? (
                    <div>
                      <div style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 10 }}>
                        Email Sequence ({c.sequence_steps.length} steps)
                      </div>
                      {c.sequence_steps.map((step: any, i: number) => (
                        <div key={i} style={{ marginBottom: 12, padding: '10px 14px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--bg-card)' }}>
                          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                            <span style={{ fontSize: 12, fontWeight: 600 }}>Email {step.step || i + 1}</span>
                            <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>Day {step.day || 0}</span>
                          </div>
                          {step.subject && <div style={{ fontSize: 12, fontWeight: 500, marginBottom: 4 }}>Subject: {step.subject}</div>}
                          <div style={{ fontSize: 12, color: 'var(--text-secondary)', maxHeight: 60, overflow: 'hidden' }}>
                            {(step.body || '').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim().substring(0, 200) + '...'}</div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>No sequence data available.</div>
                  )}

                  {/* Settings summary */}
                  <div style={{ marginTop: 12, display: 'flex', gap: 16, fontSize: 11, color: 'var(--text-muted)' }}>
                    {c.timezone && <span>TZ: {c.timezone}</span>}
                    <span>Plain text</span>
                    <span>No tracking</span>
                    <span>Stop on reply</span>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
