import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { authHeaders } from '../App'

const API = '/api'

function ProjectCard({ project }: { project: any }) {
  const [expanded, setExpanded] = useState(false)
  const [runs, setRuns] = useState<any[]>([])
  const [campaigns, setCampaigns] = useState<string[]>([])
  const offer = project.offer_summary

  useEffect(() => {
    if (!expanded) return
    fetch(`${API}/pipeline/iterations`, { headers: authHeaders() })
      .then(r => r.json())
      .then((data: any[]) => setRuns(data.filter((r: any) => r.project_id === project.id)))
      .catch(() => {})
    if (project.campaign_filters && Array.isArray(project.campaign_filters)) {
      setCampaigns(project.campaign_filters)
    }
  }, [expanded, project.id])

  const pid = project.id

  return (
    <div style={{ border: '1px solid var(--border)', borderRadius: 8, background: 'var(--bg-card)', overflow: 'hidden' }}>
      <div
        onClick={() => setExpanded(!expanded)}
        style={{ padding: 16, cursor: 'pointer' }}
      >
        {/* Header */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <div style={{ fontSize: 16, fontWeight: 600, color: 'var(--text)' }}>{project.name}</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {project.offer_approved ? (
              <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 4, background: 'rgba(34,197,94,0.1)', color: '#22c55e', fontWeight: 600 }}>OFFER CONFIRMED</span>
            ) : (
              <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 4, background: 'rgba(245,158,11,0.1)', color: '#f59e0b', fontWeight: 600 }}>APPROVAL PENDING</span>
            )}
            <span style={{ fontSize: 14, color: 'var(--text-muted)' }}>{expanded ? '\u25B2' : '\u25BC'}</span>
          </div>
        </div>

        {/* Structured offer info */}
        <div style={{ marginTop: 8, display: 'grid', gap: 4, fontSize: 13 }}>
          {project.website && (
            <div style={{ color: 'var(--text-muted)' }}>
              <span style={{ fontWeight: 500, color: 'var(--text-secondary)' }}>Website:</span>{' '}
              <a href={project.website} target="_blank" rel="noopener noreferrer" style={{ color: '#3b82f6', textDecoration: 'none' }} onClick={e => e.stopPropagation()}>{project.website}</a>
            </div>
          )}
          {offer?.primary_offer && (
            <div style={{ color: 'var(--text)' }}>
              <span style={{ fontWeight: 500, color: 'var(--text-secondary)' }}>Offer:</span> {offer.primary_offer}
            </div>
          )}
          {offer?.target_audience && (
            <div style={{ color: 'var(--text-muted)' }}>
              <span style={{ fontWeight: 500, color: 'var(--text-secondary)' }}>Target:</span> {offer.target_audience}
            </div>
          )}
          {offer?.value_proposition && (
            <div style={{ color: 'var(--text-muted)' }}>
              <span style={{ fontWeight: 500, color: 'var(--text-secondary)' }}>Value prop:</span> {offer.value_proposition}
            </div>
          )}
          {!offer && project.target_segments && (
            <div style={{ color: 'var(--text-muted)' }}>
              <span style={{ fontWeight: 500, color: 'var(--text-secondary)' }}>ICP:</span> {project.target_segments}
            </div>
          )}
        </div>

        {/* Sender */}
        {(project.sender_name || project.sender_company) && (
          <div style={{ display: 'flex', gap: 16, fontSize: 12, color: 'var(--text-secondary)', marginTop: 6 }}>
            {project.sender_name && <span>Sender: {project.sender_name}</span>}
            {project.sender_company && <span>Company: {project.sender_company}</span>}
          </div>
        )}
      </div>

      {expanded && (
        <div style={{ borderTop: '1px solid var(--border)', padding: 16, background: 'var(--bg)' }}>
          {/* Products list */}
          {offer?.products && offer.products.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <div style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 6 }}>Products ({offer.products.length})</div>
              <div style={{ display: 'grid', gap: 4 }}>
                {offer.products.map((p: any, i: number) => (
                  <div key={i} style={{ fontSize: 12, padding: '4px 8px', borderRadius: 4, background: 'var(--bg-card)', border: '1px solid var(--border)' }}>
                    <span style={{ fontWeight: 500 }}>{p.name}</span>
                    {p.description && <span style={{ color: 'var(--text-muted)' }}> — {p.description}</span>}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Target Segments (from document) */}
          {offer?.segments && offer.segments.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <div style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 6 }}>Target Segments ({offer.segments.length})</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                {offer.segments.map((s: any, i: number) => (
                  <span key={i} style={{ fontSize: 11, padding: '3px 10px', borderRadius: 4, background: 'rgba(59,130,246,0.1)', color: '#3b82f6', fontWeight: 500 }}>
                    {s.name || s}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Target Roles (from document) */}
          {offer?.target_roles?.titles && offer.target_roles.titles.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <div style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 6 }}>Target Roles ({offer.target_roles.titles.length})</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                {offer.target_roles.titles.map((r: string, i: number) => (
                  <span key={i} style={{ fontSize: 11, padding: '3px 8px', borderRadius: 4, background: 'rgba(168,85,247,0.1)', color: '#a855f7', fontWeight: 500 }}>
                    {r}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Sequences (from document) */}
          {offer?.sequences && offer.sequences.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <div style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 6 }}>Sequences ({offer.sequences.length})</div>
              {offer.sequences.map((seq: any, i: number) => (
                <div key={i} style={{ padding: '8px 10px', borderRadius: 6, background: 'var(--bg-card)', border: '1px solid var(--border)', marginBottom: 4 }}>
                  <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text)' }}>{seq.name || `Sequence ${i + 1}`}</div>
                  <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>
                    {(seq.steps || []).map((step: any, j: number) => (
                      <div key={j}>Day {step.day}: {step.subject}</div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Exclusion Rules (from document) */}
          {offer?.exclusion_list && offer.exclusion_list.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <div style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 6 }}>Exclusion Rules ({offer.exclusion_list.length})</div>
              <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                {offer.exclusion_list.map((e: any, i: number) => (
                  <div key={i} style={{ marginBottom: 2 }}>{'\u2022'} <span style={{ fontWeight: 500 }}>{e.type}</span>{e.reason ? ` — ${e.reason}` : ''}</div>
                ))}
              </div>
            </div>
          )}

          {/* Key differentiators */}
          {offer?.key_differentiators && offer.key_differentiators.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <div style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 6 }}>Differentiators</div>
              <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                {offer.key_differentiators.map((d: string, i: number) => (
                  <div key={i} style={{ marginBottom: 2 }}>{'\u2022'} {d}</div>
                ))}
              </div>
            </div>
          )}

          {/* Connected Campaigns */}
          <div style={{ marginBottom: 12 }}>
            <div style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 6 }}>Connected Campaigns ({campaigns.length})</div>
            {campaigns.length > 0 ? (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                {campaigns.map((c: string, i: number) => (
                  <span key={i} style={{ fontSize: 11, padding: '2px 8px', borderRadius: 4, background: 'rgba(99,102,241,0.1)', color: '#6366f1' }}>{c}</span>
                ))}
              </div>
            ) : (
              <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>No campaigns connected.</div>
            )}
          </div>

          {/* Links — all with project filter */}
          <div style={{ marginBottom: 12, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <Link to={`/pipeline?project=${pid}`} style={{ fontSize: 12, padding: '4px 10px', borderRadius: 6, background: 'var(--bg-card)', border: '1px solid var(--border)', color: 'var(--text-link)', textDecoration: 'none' }}>
              Pipeline runs ({runs.length}) {'\u2192'}
            </Link>
            <Link to={`/campaigns?project=${pid}`} style={{ fontSize: 12, padding: '4px 10px', borderRadius: 6, background: 'var(--bg-card)', border: '1px solid var(--border)', color: 'var(--text-link)', textDecoration: 'none' }}>
              Campaigns {'\u2192'}
            </Link>
            <Link to={`/crm?project=${pid}`} style={{ fontSize: 12, padding: '4px 10px', borderRadius: 6, background: 'var(--bg-card)', border: '1px solid var(--border)', color: 'var(--text-link)', textDecoration: 'none' }}>
              CRM contacts {'\u2192'}
            </Link>
            <Link to={`/crm?project=${pid}&reply_category=interested`} style={{ fontSize: 12, padding: '4px 10px', borderRadius: 6, background: 'rgba(34,197,94,0.1)', border: '1px solid rgba(34,197,94,0.3)', color: '#22c55e', textDecoration: 'none' }}>
              Warm leads {'\u2192'}
            </Link>
          </div>

          {/* Pipeline Runs */}
          <div>
            <div style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 6 }}>Pipeline Runs ({runs.length})</div>
            {runs.length > 0 ? (
              <div style={{ display: 'grid', gap: 4 }}>
                {runs.map((r: any) => (
                  <Link key={r.id} to={`/pipeline/${r.id}?project=${pid}`} style={{ textDecoration: 'none', display: 'flex', justifyContent: 'space-between', padding: '6px 10px', borderRadius: 6, background: 'var(--bg-card)', border: '1px solid var(--border)', fontSize: 12 }}>
                    <span style={{ color: 'var(--text)' }}>Run #{r.id} — {r.source_type || 'apollo'}</span>
                    <span style={{ color: 'var(--text-muted)' }}>{r.new_companies || 0} companies {'\u00B7'} {r.current_phase}</span>
                  </Link>
                ))}
              </div>
            ) : (
              <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>No pipeline runs yet.</div>
            )}
          </div>

          {/* Source info */}
          {offer?._source && (
            <div style={{ marginTop: 8, fontSize: 11, color: 'var(--text-muted)', fontStyle: 'italic' }}>
              Offer extracted from: {offer._source === 'document' ? 'strategy document' : offer._source === 'gpt_knowledge' ? 'AI knowledge' : 'website scrape'}
              {offer._analyzed_at && ` on ${new Date(offer._analyzed_at).toLocaleDateString()}`}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default function ProjectsPage() {
  const [projects, setProjects] = useState<any[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch(`${API}/pipeline/projects`, { headers: authHeaders() })
      .then(r => r.json())
      .then(setProjects)
      .catch(e => console.error('Failed to load projects:', e))
      .finally(() => setLoading(false))
  }, [])

  return (
    <div style={{ maxWidth: 960, margin: '0 auto', padding: 24 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div style={{ fontSize: 11, fontWeight: 500, textTransform: 'uppercase', letterSpacing: 1, color: 'var(--text-muted)' }}>Projects</div>
        <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>{projects.length} project{projects.length !== 1 ? 's' : ''}</div>
      </div>
      {loading ? <div style={{ color: 'var(--text-muted)', padding: 40, textAlign: 'center' }}>Loading...</div> :
       projects.length === 0 ? <div style={{ color: 'var(--text-muted)', padding: 40, textAlign: 'center' }}>No projects yet. Create one via MCP.</div> : (
        <div style={{ display: 'grid', gap: 12 }}>
          {projects.map((p: any) => <ProjectCard key={p.id} project={p} />)}
        </div>
      )}
    </div>
  )
}
