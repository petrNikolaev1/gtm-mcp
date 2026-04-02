import { useState, useEffect } from 'react'
import { useProject, authHeaders } from '../App'

const API = '/api'
function headers() {
  return {
    'X-MCP-Token': localStorage.getItem('mcp_token') || '',
    'Content-Type': 'application/json',
  }
}

interface UsageEntry {
  tool: string
  action: string
  at: string
  extra?: Record<string, any>
}

interface RunEntry {
  id: number
  source_type: string
  phase: string
  companies: number
  targets: number
  target_rate: string
  credits_used: number
  created_at: string
}

export default function LearningPage() {
  const [tab, setTab] = useState<'actions' | 'analytics'>('actions')
  const { project } = useProject()

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      {/* Tabs */}
      <div style={{ display: 'flex', gap: 0, borderBottom: '1px solid var(--border)', padding: '0 24px', background: 'var(--bg)' }}>
        {([['actions', 'Actions'], ['analytics', 'Analytics']] as const).map(([k, label]) => (
          <button key={k} onClick={() => setTab(k)} style={{
            padding: '10px 16px', fontSize: 13, fontWeight: 500, cursor: 'pointer',
            background: 'transparent', border: 'none',
            color: tab === k ? 'var(--text)' : 'var(--text-muted)',
            borderBottom: tab === k ? '2px solid #3b82f6' : '2px solid transparent',
          }}>{label}</button>
        ))}
      </div>

      {/* Content */}
      <div style={{ flex: 1, overflow: 'auto' }}>
        {tab === 'actions' && <ActionsTab />}
        {tab === 'analytics' && <AnalyticsTab />}
      </div>
    </div>
  )
}

function ActionsTab() {
  const { project } = useProject()
  const [items, setItems] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [page, setPage] = useState(1)
  const [filter, setFilter] = useState('')

  useEffect(() => {
    if (!project) { setLoading(false); return }
    setLoading(true)
    const params = new URLSearchParams({ page: String(page), page_size: '30' })
    if (filter) params.set('action_type', filter)
    fetch(`${API}/projects/${project.id}/learning/corrections?${params}`, { headers: headers() })
      .then(r => r.ok ? r.json() : { items: [], total: 0 })
      .then(d => { setItems(d.items || []); setTotal(d.total || 0) })
      .finally(() => setLoading(false))
  }, [project, page, filter])

  if (!project) return <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)' }}>Select a project</div>
  if (loading) return <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)' }}>Loading...</div>

  return (
    <div style={{ maxWidth: 900, margin: '0 auto', padding: 24 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div style={{ fontSize: 13, color: 'var(--text-muted)' }}>{total} actions for {project.name}</div>
        <div style={{ display: 'flex', gap: 6 }}>
          {['', 'send', 'dismiss', 'pending'].map(f => (
            <button key={f} onClick={() => { setFilter(f); setPage(1) }} style={{
              padding: '4px 10px', borderRadius: 4, fontSize: 11, border: '1px solid var(--border)',
              background: filter === f ? 'var(--text-link)' : 'transparent',
              color: filter === f ? 'white' : 'var(--text-muted)', cursor: 'pointer',
            }}>{f || 'All'}</button>
          ))}
        </div>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {items.map((item: any) => (
          <div key={item.id} style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 16 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
              <div>
                <span style={{ fontWeight: 600, fontSize: 13 }}>{item.lead_email}</span>
                {item.lead_company && <span style={{ color: 'var(--text-muted)', marginLeft: 8, fontSize: 12 }}>{item.lead_company}</span>}
              </div>
              <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                <span style={{ padding: '2px 8px', borderRadius: 4, fontSize: 10, fontWeight: 500,
                  background: item.reply_category === 'interested' ? 'rgba(34,197,94,0.15)' : item.reply_category === 'meeting_request' ? 'rgba(245,158,11,0.15)' : 'var(--bg)',
                  color: item.reply_category === 'interested' ? '#22c55e' : item.reply_category === 'meeting_request' ? '#f59e0b' : 'var(--text-muted)',
                }}>{item.reply_category}</span>
                <span style={{ padding: '2px 8px', borderRadius: 4, fontSize: 10, background: 'var(--bg)', color: 'var(--text-muted)' }}>{item.channel}</span>
                <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>{item.created_at ? new Date(item.created_at).toLocaleDateString() : ''}</span>
              </div>
            </div>
            <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 4 }}>{item.campaign_name}</div>
            {item.ai_draft_preview && (
              <div style={{ background: 'var(--bg)', borderRadius: 6, padding: 12, fontSize: 13, lineHeight: 1.5, whiteSpace: 'pre-wrap' }}>
                <div style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 4 }}>AI Draft</div>
                {item.ai_draft_preview}
              </div>
            )}
          </div>
        ))}
      </div>
      {items.length < total && (
        <div style={{ textAlign: 'center', padding: 16 }}>
          <button onClick={() => setPage(p => p + 1)} style={{ padding: '6px 16px', borderRadius: 6, border: '1px solid var(--border)', background: 'transparent', color: 'var(--text-secondary)', cursor: 'pointer', fontSize: 12 }}>
            Load more ({items.length} of {total})
          </button>
        </div>
      )}
    </div>
  )
}

function AnalyticsTab() {
  const { project } = useProject()
  const [usage, setUsage] = useState<{ by_tool: Record<string, number>; recent: UsageEntry[] } | null>(null)
  const [runs, setRuns] = useState<RunEntry[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    Promise.all([
      fetch(`${API}/account/usage`, { headers: headers() }).then(r => r.ok ? r.json() : null),
      fetch(`${API}/pipeline/runs`, { headers: headers() }).then(r => r.ok ? r.json() : []),
    ]).then(([usageData, runsData]) => {
      setUsage(usageData)
      setRuns(runsData || [])
    }).finally(() => setLoading(false))
  }, [])

  if (loading) return <div className="max-w-4xl mx-auto p-8 text-sm text-gray-500">Loading...</div>

  const byTool = usage?.by_tool || {}
  const recent = usage?.recent || []
  const sortedTools = Object.entries(byTool).sort((a, b) => b[1] - a[1])
  const totalCalls = sortedTools.reduce((sum, [, count]) => sum + count, 0)

  // Filter runs for current project if one is selected
  const filteredRuns = project ? runs.filter((r: any) => r.project_id === project.id) : runs
  const runsWithTargets = filteredRuns.filter((r: any) => r.targets > 0)
  const avgTargetRate = runsWithTargets.length > 0
    ? (runsWithTargets.reduce((sum: number, r: any) => {
        const rate = parseFloat(String(r.target_rate).replace('%', '')) || 0
        return sum + rate
      }, 0) / runsWithTargets.length).toFixed(1) + '%'
    : 'N/A'

  return (
    <div className="max-w-4xl mx-auto p-8 space-y-8 overflow-y-auto" style={{ height: '100%' }}>
      <div>
        <h1 className="text-lg font-semibold">Learning</h1>
        <p className="text-sm text-gray-500 mt-1">
          Usage analytics and pipeline accuracy history
          {project ? ` for ${project.name}` : ''}
        </p>
      </div>

      {/* Usage overview */}
      <div className="space-y-3">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-gray-400">Usage Overview</h2>
        <div className="grid grid-cols-3 gap-4">
          <StatCard label="Total Tool Calls" value={totalCalls} />
          <StatCard label="Unique Tools Used" value={sortedTools.length} />
          <StatCard label="Pipeline Runs" value={filteredRuns.length} />
        </div>
      </div>

      {/* Tool usage breakdown */}
      {sortedTools.length > 0 && (
        <div className="space-y-3">
          <h2 className="text-sm font-semibold uppercase tracking-wider text-gray-400">Tool Usage</h2>
          <div className="border border-gray-700 rounded-lg divide-y divide-gray-700">
            {sortedTools.map(([tool, count]) => {
              const pct = totalCalls > 0 ? (count / totalCalls * 100) : 0
              return (
                <div key={tool} className="flex items-center gap-3 px-4 py-2.5">
                  <span className="text-sm font-mono flex-1">{tool}</span>
                  <div className="w-32 h-1.5 rounded bg-gray-800 overflow-hidden">
                    <div
                      className="h-full rounded bg-blue-500"
                      style={{ width: `${Math.max(pct, 2)}%` }}
                    />
                  </div>
                  <span className="text-sm font-medium tabular-nums w-12 text-right">{count}</span>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Pipeline accuracy history */}
      {filteredRuns.length > 0 && (
        <div className="space-y-3">
          <h2 className="text-sm font-semibold uppercase tracking-wider text-gray-400">
            Pipeline Accuracy History
          </h2>
          <div className="grid grid-cols-3 gap-4 mb-4">
            <StatCard label="Total Runs" value={filteredRuns.length} />
            <StatCard label="Runs with Targets" value={runsWithTargets.length} />
            <StatCard label="Avg Target Rate" value={avgTargetRate} isText />
          </div>
          <div className="border border-gray-700 rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs uppercase text-gray-500 border-b border-gray-700">
                  <th className="px-4 py-2 text-left">Run</th>
                  <th className="px-4 py-2 text-left">Source</th>
                  <th className="px-4 py-2 text-right">Companies</th>
                  <th className="px-4 py-2 text-right">Targets</th>
                  <th className="px-4 py-2 text-right">Target Rate</th>
                  <th className="px-4 py-2 text-right">Credits</th>
                  <th className="px-4 py-2 text-left">Phase</th>
                  <th className="px-4 py-2 text-left">Date</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-700">
                {filteredRuns.map((r: any) => (
                  <tr key={r.id}>
                    <td className="px-4 py-2">
                      <a href={`/pipeline/${r.id}`} className="text-blue-400 hover:underline">#{r.id}</a>
                    </td>
                    <td className="px-4 py-2 text-gray-400">
                      {(r.source_type || '').replace('companies.', '').replace('.manual', '')}
                    </td>
                    <td className="px-4 py-2 text-right">{r.companies || r.new_companies || 0}</td>
                    <td className="px-4 py-2 text-right">
                      {r.targets > 0 ? (
                        <span className="text-green-400">{r.targets}</span>
                      ) : (
                        <span className="text-gray-600">0</span>
                      )}
                    </td>
                    <td className="px-4 py-2 text-right">
                      {r.target_rate ? (
                        <span className="text-green-400">{r.target_rate}</span>
                      ) : (
                        <span className="text-gray-600">--</span>
                      )}
                    </td>
                    <td className="px-4 py-2 text-right font-mono">
                      {r.credits_used > 0 ? (
                        <span className="text-yellow-400">{r.credits_used}</span>
                      ) : (
                        <span className="text-gray-600">0</span>
                      )}
                    </td>
                    <td className="px-4 py-2">
                      <span className="px-2 py-0.5 rounded text-xs bg-gray-700">{r.phase}</span>
                    </td>
                    <td className="px-4 py-2 text-gray-500 text-xs">
                      {r.created_at ? new Date(r.created_at).toLocaleDateString() : ''}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Recent activity */}
      {recent.length > 0 && (
        <div className="space-y-3">
          <h2 className="text-sm font-semibold uppercase tracking-wider text-gray-400">Recent Activity</h2>
          <div className="border border-gray-700 rounded-lg divide-y divide-gray-700">
            {recent.slice(0, 20).map((r, i) => (
              <div key={i} className="flex items-center justify-between px-4 py-2">
                <div className="flex items-center gap-3">
                  <span className="text-xs font-mono text-gray-400">{r.tool}</span>
                  <span className="text-xs text-gray-500">{r.action}</span>
                </div>
                <span className="text-xs text-gray-600">
                  {r.at ? new Date(r.at).toLocaleString() : ''}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Reply Analysis Summary */}
      <ReplyAnalysisSection />

      {/* Conversation Log */}
      <ConversationLogSection />

      {sortedTools.length === 0 && filteredRuns.length === 0 && (
        <div className="text-center text-gray-500 py-16">
          No usage data yet. Start using MCP tools to see analytics here.
        </div>
      )}
    </div>
  )
}

function ReplyAnalysisSection() {
  const [data, setData] = useState<any>(null)
  useEffect(() => {
    fetch(`${API}/pipeline/reply-analysis-status`, { headers: headers() })
      .then(r => r.ok ? r.json() : null)
      .then(setData)
      .catch(e => console.error('Failed to load reply analysis:', e))
  }, [])
  if (!data || !data.total_replied) return null
  const cats = data.by_category || {}
  return (
    <div className="space-y-3">
      <h2 className="text-sm font-semibold uppercase tracking-wider text-gray-400">Reply Analysis</h2>
      <div className="grid grid-cols-4 gap-4">
        <StatCard label="Total Replied" value={data.total_replied || 0} />
        <StatCard label="Warm Leads" value={data.warm_count || 0} />
        <StatCard label="OOO Filtered" value={data.ooo_skipped || 0} />
        <StatCard label="AI Classified" value={data.ai_classified || 0} />
      </div>
      {Object.keys(cats).length > 0 && (
        <div className="border border-gray-700 rounded-lg divide-y divide-gray-700">
          {Object.entries(cats).sort((a: any, b: any) => b[1] - a[1]).map(([cat, count]: any) => (
            <div key={cat} className="flex items-center justify-between px-4 py-2">
              <span className="text-sm">{cat}</span>
              <span className="text-sm font-medium tabular-nums">{count}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function ConversationLogSection() {
  const [convos, setConvos] = useState<any[]>([])
  useEffect(() => {
    fetch(`${API}/account/conversations?limit=10`, { headers: headers() })
      .then(r => r.ok ? r.json() : { conversations: [] })
      .then(d => setConvos(d.conversations || []))
      .catch(e => console.error('Failed to load conversations:', e))
  }, [])
  if (convos.length === 0) return null
  return (
    <div className="space-y-3">
      <h2 className="text-sm font-semibold uppercase tracking-wider text-gray-400">Recent MCP Conversations</h2>
      <div className="border border-gray-700 rounded-lg divide-y divide-gray-700">
        {convos.map((c: any, i: number) => (
          <div key={i} className="px-4 py-2 flex items-center justify-between">
            <div>
              <span className="text-xs font-mono text-gray-400">{c.method || 'message'}</span>
              <span className="text-xs text-gray-500 ml-2">{c.content_summary || ''}</span>
            </div>
            <span className="text-xs text-gray-600">{c.created_at ? new Date(c.created_at).toLocaleString() : ''}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function StatCard({ label, value, isText }: { label: string; value: number | string; isText?: boolean }) {
  return (
    <div className="border border-gray-700 rounded-lg p-3 text-center">
      <div className={`text-xl font-bold ${isText ? '' : 'tabular-nums'}`}>
        {typeof value === 'number' ? value.toLocaleString() : value}
      </div>
      <div className="text-xs text-gray-500 mt-1">{label}</div>
    </div>
  )
}
