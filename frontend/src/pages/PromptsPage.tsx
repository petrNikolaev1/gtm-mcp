import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'

const API = '/api'

const stepTypeBadge = (type: string) => {
  const styles: Record<string, { bg: string; color: string; label: string }> = {
    analysis: { bg: 'rgba(34,197,94,0.15)', color: '#22c55e', label: 'system' },
    tool_call: { bg: 'rgba(59,130,246,0.15)', color: '#3b82f6', label: 'tool' },
    user_feedback: { bg: 'rgba(245,158,11,0.15)', color: '#f59e0b', label: 'feedback' },
    classify: { bg: 'rgba(168,85,247,0.15)', color: '#a855f7', label: 'classify' },
    filter: { bg: 'rgba(239,68,68,0.15)', color: '#ef4444', label: 'filter' },
    custom: { bg: 'rgba(99,102,241,0.15)', color: '#818cf8', label: 'custom' },
  }
  const s = styles[type] || styles.analysis
  return (
    <span style={{ marginLeft: 4, padding: '1px 4px', borderRadius: 3, fontSize: 9, background: s.bg, color: s.color }}>
      {s.label}
    </span>
  )
}

export default function PromptsPage() {
  const { runId } = useParams()
  const [prompts, setPrompts] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [expanded, setExpanded] = useState<number | null>(null)

  useEffect(() => {
    setLoading(true)
    fetch(`${API}/pipeline/runs/${runId}/prompts`)
      .then(r => r.ok ? r.json() : [])
      .then(setPrompts)
      .catch(e => console.error('Failed to load prompts:', e))
      .finally(() => setLoading(false))
  }, [runId])

  // Filter out tool_call rows without prompt text (tam_blacklist_check, tam_scrape, tam_analyze etc.)
  const visiblePrompts = prompts.filter(p => p.type !== 'tool_call' || p.prompt_text)

  // Group prompts by chain (parent_prompt_id)
  const chains: any[][] = []
  const standalone: any[] = []
  const byId: Record<number, any> = {}
  visiblePrompts.forEach(p => { byId[p.id] = p })

  visiblePrompts.forEach(p => {
    if (p.parent_prompt_id && byId[p.parent_prompt_id]) {
      // Part of a chain — find or create chain
      let found = false
      for (const chain of chains) {
        if (chain.some(c => c.id === p.parent_prompt_id || c.parent_prompt_id === p.parent_prompt_id)) {
          chain.push(p)
          found = true
          break
        }
      }
      if (!found) {
        chains.push([byId[p.parent_prompt_id], p])
      }
    } else if (!visiblePrompts.some(other => other.parent_prompt_id === p.id)) {
      standalone.push(p)
    }
  })

  const renderPromptRow = (p: any, stepNum?: number) => (
    <>
      <tr key={p.id} onClick={() => setExpanded(expanded === p.id ? null : p.id)}
          style={{ borderTop: '1px solid var(--border)', cursor: 'pointer' }}>
        <td style={{ padding: '8px 8px 8px 0', color: 'var(--text-muted)' }}>
          {p.created_at ? new Date(p.created_at).toLocaleDateString() : '—'}
        </td>
        <td style={{ padding: '8px 8px 8px 0' }}>
          {stepNum != null && (
            <span style={{ padding: '1px 5px', borderRadius: 3, background: 'rgba(99,102,241,0.08)', color: 'var(--text-muted)', marginRight: 4, fontSize: 10, fontWeight: 600 }}>
              Step {stepNum}
            </span>
          )}
          #{p.id}
          {stepTypeBadge(p.type || p.category || 'analysis')}
        </td>
        <td style={{ padding: '8px 8px 8px 0' }}>
          <Link to={`/pipeline/${runId}`} style={{ color: 'var(--text-link)' }}>#{runId}</Link>
        </td>
        <td style={{ padding: '8px 8px 8px 0', color: 'var(--text-secondary)', maxWidth: 400, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {p.segment && <span style={{ padding: '1px 5px', borderRadius: 3, background: 'rgba(99,102,241,0.12)', color: '#818cf8', marginRight: 4, fontSize: 11 }}>{p.segment}</span>}
          <span style={{ fontWeight: 500, color: 'var(--text)' }}>{p.name || (p.prompt_text ? p.prompt_text.split('\n')[0].slice(0, 50) : p.tool || '...')}</span>
          {p.prompt_text && <span style={{ color: 'var(--text-muted)', marginLeft: 6, fontSize: 11 }}>{p.prompt_text.slice(0, 80).replace(/\n/g, ' ')}...</span>}
        </td>
        <td style={{ padding: '8px 8px 8px 0' }}>{p.total_analyzed || p.total_companies || 0}</td>
        <td style={{ padding: '8px 8px 8px 0', color: 'var(--success)' }}>{p.targets_found || 0}</td>
        <td style={{ padding: '8px 0' }}>
          {p.total_analyzed && p.targets_found
            ? `${((p.targets_found / p.total_analyzed) * 100).toFixed(0)}%`
            : '—'}
        </td>
      </tr>
      {expanded === p.id && (
        <tr key={`${p.id}-detail`}>
          <td colSpan={7} style={{ paddingBottom: 12 }}>
            <div style={{ marginLeft: stepNum ? 32 : 16, padding: 16, borderRadius: 8, background: 'var(--bg-card)', border: '1px solid var(--border)', fontSize: 12 }}>
              {p.name && (
                <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8, color: 'var(--text-primary)' }}>{p.name}</div>
              )}
              {p.output_column && (
                <div style={{ marginBottom: 8 }}>
                  <span style={{ color: 'var(--text-muted)' }}>Output column:</span>{' '}
                  <code style={{ padding: '1px 4px', borderRadius: 3, background: 'var(--bg-surface)', fontSize: 11 }}>{p.output_column}</code>
                </div>
              )}
              <div style={{ fontSize: 11, fontWeight: 500, textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 8 }}>Full Prompt</div>
              <pre style={{ whiteSpace: 'pre-wrap', color: 'var(--text-secondary)', lineHeight: 1.5, maxHeight: 300, overflow: 'auto' }}>{p.prompt_text}</pre>
              {p.avg_confidence != null && (
                <div style={{ marginTop: 8 }}>
                  <span style={{ color: 'var(--text-muted)' }}>Avg confidence:</span> {(p.avg_confidence * 100).toFixed(1)}%
                </div>
              )}
              {p.filter_condition && (
                <div style={{ marginTop: 8 }}>
                  <span style={{ color: 'var(--text-muted)' }}>Filter:</span>{' '}
                  <code style={{ padding: '1px 4px', borderRadius: 3, background: 'rgba(239,68,68,0.08)', color: '#ef4444', fontSize: 11 }}>{p.filter_condition}</code>
                </div>
              )}
              {p.prompt_text && (
                <div style={{ marginTop: 12, display: 'flex', gap: 8 }}>
                  <button onClick={(e) => { e.stopPropagation(); navigator.clipboard.writeText(p.prompt_text); }}
                    style={{ padding: '4px 12px', borderRadius: 4, fontSize: 11, background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--text-muted)', cursor: 'pointer' }}>
                    Copy prompt
                  </button>
                </div>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  )

  return (
    <div style={{ maxWidth: 1100, margin: '0 auto', padding: 24 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
        <div>
          <div style={{ fontSize: 11, fontWeight: 500, textTransform: 'uppercase', letterSpacing: 1, color: 'var(--text-muted)' }}>
            Prompts — Run #{runId}
            {chains.length > 0 && (
              <span style={{ marginLeft: 8, padding: '2px 6px', borderRadius: 4, background: 'rgba(168,85,247,0.12)', color: '#a855f7', fontSize: 10, fontWeight: 600, letterSpacing: 0 }}>
                {chains.length} chain{chains.length > 1 ? 's' : ''}
              </span>
            )}
          </div>
        </div>
        <Link to={`/pipeline/${runId}`} style={{ padding: '4px 12px', borderRadius: 6, fontSize: 12, border: '1px solid var(--border)', color: 'var(--text-secondary)', textDecoration: 'none' }}>
          ← Back to pipeline
        </Link>
      </div>

      {loading ? (
        <div style={{ padding: '40px 0', textAlign: 'center', color: 'var(--text-muted)' }}>Loading...</div>
      ) : visiblePrompts.length === 0 ? (
        <div style={{ padding: '40px 0', textAlign: 'center', color: 'var(--text-muted)' }}>
          No prompts used yet. Run the analyze phase to see GPT prompts.
        </div>
      ) : (
        <>
          {/* Prompt chains */}
          {chains.map((chain, ci) => (
            <div key={`chain-${ci}`} style={{ marginBottom: 24 }}>
              <div style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: 0.5, color: 'var(--text-muted)', marginBottom: 8, display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ width: 3, height: 14, borderRadius: 2, background: '#a855f7', display: 'inline-block' }} />
                Prompt Chain ({chain.length} steps)
              </div>
              <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
                <thead>
                  <tr style={{ textAlign: 'left', fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.5, color: 'var(--text-muted)' }}>
                    <th style={{ paddingBottom: 8 }}>Created</th>
                    <th style={{ paddingBottom: 8 }}>Step</th>
                    <th style={{ paddingBottom: 8 }}>Run</th>
                    <th style={{ paddingBottom: 8 }}>Prompt</th>
                    <th style={{ paddingBottom: 8 }}>Companies</th>
                    <th style={{ paddingBottom: 8 }}>Targets</th>
                    <th style={{ paddingBottom: 8 }}>Rate</th>
                  </tr>
                </thead>
                <tbody>
                  {chain.map((p: any, si: number) => renderPromptRow(p, si + 1))}
                </tbody>
              </table>
            </div>
          ))}

          {/* Standalone prompts */}
          {standalone.length > 0 && (
            <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ textAlign: 'left', fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.5, color: 'var(--text-muted)' }}>
                  <th style={{ paddingBottom: 8 }}>Created</th>
                  <th style={{ paddingBottom: 8 }}>Prompt ID</th>
                  <th style={{ paddingBottom: 8 }}>Run</th>
                  <th style={{ paddingBottom: 8 }}>Prompt Body</th>
                  <th style={{ paddingBottom: 8 }}>Companies</th>
                  <th style={{ paddingBottom: 8 }}>Targets</th>
                  <th style={{ paddingBottom: 8 }}>Accuracy</th>
                </tr>
              </thead>
              <tbody>
                {standalone.map((p: any) => renderPromptRow(p))}
              </tbody>
            </table>
          )}
        </>
      )}
    </div>
  )
}
