import { useState, useEffect } from 'react'
import { authHeaders } from '../App'

const API = '/api'

export default function ConversationsPage() {
  const [logs, setLogs] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedSession, setSelectedSession] = useState<string | null>(null)
  const [expandedId, setExpandedId] = useState<number | null>(null)

  useEffect(() => {
    setLoading(true)
    const params = new URLSearchParams({ limit: '200' })
    if (selectedSession) params.set('session_id', selectedSession)
    fetch(`${API}/account/conversations?${params}`, { headers: authHeaders() })
      .then(r => r.ok ? r.json() : { conversations: [] })
      .then(d => setLogs(d.conversations || []))
      .catch(e => console.error('Failed to load conversations:', e))
      .finally(() => setLoading(false))
  }, [selectedSession])

  // Extract unique sessions
  const sessions = [...new Set(logs.map(l => l.session_id).filter(Boolean))]

  const dirColor = (dir: string) => dir === 'client_to_server' ? '#3b82f6' : '#22c55e'
  const dirLabel = (dir: string) => dir === 'client_to_server' ? '→ MCP' : '← Response'

  return (
    <div style={{ maxWidth: 1100, margin: '0 auto', padding: 24 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div>
          <div style={{ fontSize: 11, fontWeight: 500, textTransform: 'uppercase', letterSpacing: 1, color: 'var(--text-muted)' }}>Conversations</div>
          <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 2 }}>All messages between your Claude agent and MCP</div>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {sessions.length > 0 && (
            <select
              value={selectedSession || ''}
              onChange={e => setSelectedSession(e.target.value || null)}
              style={{ padding: '4px 8px', borderRadius: 4, border: '1px solid var(--border)', background: 'var(--bg-card)', color: 'var(--text)', fontSize: 12 }}
            >
              <option value="">All sessions</option>
              {sessions.map(s => (
                <option key={s} value={s}>{s?.substring(0, 12)}...</option>
              ))}
            </select>
          )}
          <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>{logs.length} messages</span>
        </div>
      </div>

      {loading ? (
        <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)' }}>Loading...</div>
      ) : logs.length === 0 ? (
        <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)' }}>No conversations yet. Use MCP tools to see messages here.</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          {logs.map(log => (
            <div
              key={log.id}
              onClick={() => setExpandedId(expandedId === log.id ? null : log.id)}
              style={{
                padding: '8px 12px', borderRadius: 6, cursor: 'pointer',
                background: expandedId === log.id ? 'var(--bg-card)' : 'transparent',
                border: expandedId === log.id ? '1px solid var(--border)' : '1px solid transparent',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontSize: 10, fontWeight: 600, color: dirColor(log.direction), minWidth: 70 }}>
                  {dirLabel(log.direction)}
                </span>
                <span style={{ fontSize: 11, fontFamily: 'monospace', color: 'var(--text-secondary)', minWidth: 120 }}>
                  {log.method || 'message'}
                </span>
                <span style={{ fontSize: 12, color: 'var(--text)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {log.summary || '(no summary)'}
                </span>
                <span style={{ fontSize: 10, color: 'var(--text-muted)', minWidth: 140, textAlign: 'right' }}>
                  {log.at ? new Date(log.at).toLocaleString() : ''}
                </span>
              </div>

              {expandedId === log.id && log.raw && (
                <pre style={{
                  marginTop: 8, padding: 10, borderRadius: 4,
                  background: 'var(--bg)', border: '1px solid var(--border)',
                  fontSize: 11, fontFamily: 'monospace', color: 'var(--text-secondary)',
                  overflow: 'auto', maxHeight: 300, whiteSpace: 'pre-wrap',
                }}>
                  {typeof log.raw === 'string' ? log.raw : JSON.stringify(log.raw, null, 2)}
                </pre>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
