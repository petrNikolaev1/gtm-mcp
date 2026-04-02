import { useState, useEffect } from 'react'
import { useParams, Link, useLocation } from 'react-router-dom'
import { authHeaders } from '../App'

const API = '/api'

const statusColor = (s: string) => {
  if (s === 'active' || s === 'ACTIVE') return '#22c55e'
  if (s === 'draft' || s === 'DRAFT') return '#f59e0b'
  if (s === 'mcp_draft') return '#818cf8'
  if (s === 'paused' || s === 'PAUSED') return '#6b7280'
  return 'var(--text-muted)'
}

export default function CampaignDetailPage() {
  const { id } = useParams()
  const [campaign, setCampaign] = useState<any>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch(`${API}/pipeline/campaigns/${id}`, { headers: authHeaders() })
      .then(r => r.ok ? r.json() : null)
      .then(setCampaign)
      .catch(e => console.error('Failed to load campaign:', e))
      .finally(() => setLoading(false))
  }, [id])

  // Scroll to hash fragment (#accounts) after load
  const { hash } = useLocation()
  useEffect(() => {
    if (campaign && hash) {
      const el = document.getElementById(hash.slice(1))
      if (el) el.scrollIntoView({ behavior: 'smooth' })
    }
  }, [campaign, hash])

  if (loading) return <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)' }}>Loading...</div>
  if (!campaign) return <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)' }}>Campaign not found.</div>

  const accounts = campaign.email_accounts || []
  const steps = campaign.sequence_steps || []

  return (
    <div style={{ maxWidth: 900, margin: '0 auto', padding: 24 }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        <Link to="/campaigns" style={{ fontSize: 12, color: 'var(--text-muted)', textDecoration: 'none' }}>Campaigns</Link>
        <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>/</span>
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 24 }}>
        <div>
          <div style={{ fontSize: 18, fontWeight: 700 }}>{campaign.name}</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 4, fontSize: 12, color: 'var(--text-muted)' }}>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
              <span style={{ width: 8, height: 8, borderRadius: '50%', background: statusColor(campaign.status) }} />
              <span style={{ textTransform: 'uppercase', fontWeight: 600, color: statusColor(campaign.status) }}>{campaign.status}</span>
            </span>
            {campaign.project_name && <span>· {campaign.project_name}</span>}
            {campaign.leads_count > 0 && <span>· {campaign.leads_count} leads</span>}
            {campaign.created_by === 'mcp' && <span style={{ padding: '1px 6px', borderRadius: 4, background: 'rgba(99,102,241,0.15)', color: '#818cf8', fontSize: 10, fontWeight: 600 }}>MCP</span>}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          {campaign.pipeline_run_id && (
            <Link to={`/pipeline/${campaign.pipeline_run_id}`} style={{ fontSize: 12, padding: '6px 12px', borderRadius: 6, background: 'var(--bg)', color: 'var(--text-secondary)', textDecoration: 'none', border: '1px solid var(--border)' }}>
              Pipeline #{campaign.pipeline_run_id}
            </Link>
          )}
          {campaign.smartlead_url && (
            <a href={campaign.smartlead_url} target="_blank" rel="noopener noreferrer" style={{ fontSize: 12, padding: '6px 12px', borderRadius: 6, background: 'rgba(99,102,241,0.12)', color: '#818cf8', textDecoration: 'none', border: '1px solid rgba(99,102,241,0.25)' }}>
              SmartLead ↗
            </a>
          )}
        </div>
      </div>

      {/* Email Sequence */}
      <div style={{ marginBottom: 32 }}>
        <div style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: 0.5, color: 'var(--text-muted)', marginBottom: 12 }}>
          Email Sequence {steps.length > 0 ? `(${steps.length} steps)` : ''}
        </div>
        {steps.length > 0 ? steps.map((step: any, i: number) => (
          <div key={i} style={{ marginBottom: 10, padding: '12px 16px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--bg-card)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
              <span style={{ fontSize: 13, fontWeight: 600 }}>Email {step.step || i + 1}</span>
              <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>Day {step.day || 0}</span>
            </div>
            {step.subject && <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 6 }}>Subject: {step.subject}</div>}
            <div style={{ fontSize: 12, color: 'var(--text-secondary)', lineHeight: 1.5, whiteSpace: 'pre-wrap' }}>
              {(step.body || '').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim()}
            </div>
          </div>
        )) : (
          <div style={{ padding: 20, textAlign: 'center', color: 'var(--text-muted)', fontSize: 13, border: '1px solid var(--border)', borderRadius: 8 }}>
            {campaign.status === 'mcp_draft' ? 'Sequence will be generated when pipeline KPI is hit.' : 'No sequence data.'}
          </div>
        )}
      </div>

      {/* Email Accounts */}
      <div id="accounts">
        <div style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: 0.5, color: 'var(--text-muted)', marginBottom: 12 }}>
          Email Accounts {accounts.length > 0 ? `(${accounts.length})` : ''}
        </div>
        {accounts.length > 0 ? (
          <div style={{ border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
            {accounts.map((a: any, i: number) => (
              <div key={a.id || i} style={{ padding: '10px 16px', borderTop: i > 0 ? '1px solid var(--border)' : 'none', display: 'flex', alignItems: 'center', gap: 12 }}>
                <span style={{ width: 8, height: 8, borderRadius: '50%', background: '#22c55e', flexShrink: 0 }} />
                <div>
                  <div style={{ fontSize: 13 }}>{a.email}</div>
                  {a.name && <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>{a.name}</div>}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div style={{ padding: 20, textAlign: 'center', color: 'var(--text-muted)', fontSize: 13, border: '1px solid var(--border)', borderRadius: 8 }}>
            No email accounts selected yet.
          </div>
        )}
      </div>

      {/* Settings */}
      <div style={{ marginTop: 24, display: 'flex', gap: 16, fontSize: 11, color: 'var(--text-muted)' }}>
        <span>Plain text</span>
        <span>No tracking</span>
        <span>Stop on reply</span>
        {campaign.timezone && <span>TZ: {campaign.timezone}</span>}
      </div>
    </div>
  )
}
