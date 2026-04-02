import { useState, useEffect } from 'react'
import { authHeaders } from '../App'

const API = '/api'

export default function AccountPage() {
  const [account, setAccount] = useState<any>(null)
  const [usage, setUsage] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')

  const loadData = () => {
    setLoading(true)
    const params = new URLSearchParams()
    if (dateFrom) params.set('from', dateFrom)
    if (dateTo) params.set('to', dateTo)
    const qs = params.toString() ? `?${params}` : ''
    Promise.all([
      fetch(`${API}/account${qs}`, { headers: authHeaders() }).then(r => r.json()),
      fetch(`${API}/account/usage${qs}`, { headers: authHeaders() }).then(r => r.json()),
    ]).then(([acc, use]) => {
      setAccount(acc)
      setUsage(use)
    }).finally(() => setLoading(false))
  }

  useEffect(() => { loadData() }, [dateFrom, dateTo])

  if (loading) return <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)' }}>Loading...</div>
  if (!account?.authenticated) {
    window.location.href = '/setup'
    return null
  }

  const costs = account.costs || {}
  const stats = account.stats || {}
  const byTool = usage?.by_tool || {}
  const recent = usage?.recent || []
  const runs = account.pipeline_runs || []
  const apollo = costs.apollo || {}
  const openai = costs.openai || {}
  const apify = costs.apify || {}
  const mcp = costs.mcp || {}
  const openaiModels = openai.by_model || {}

  const cardStyle: React.CSSProperties = { border: '1px solid var(--border)', borderRadius: 8, padding: 16 }
  const labelStyle: React.CSSProperties = { fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: 0.5, color: 'var(--text-muted)', marginBottom: 12 }
  const bigNum: React.CSSProperties = { fontSize: 24, fontWeight: 700, fontVariantNumeric: 'tabular-nums' }

  return (
    <div style={{ maxWidth: 800, margin: '0 auto', padding: 24, display: 'flex', flexDirection: 'column', gap: 28 }}>
      {/* User */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <div style={{ fontSize: 18, fontWeight: 700 }}>{account.user?.name}</div>
          <div style={{ fontSize: 13, color: 'var(--text-muted)', marginTop: 2 }}>{account.user?.email}</div>
        </div>
        <button onClick={() => { localStorage.removeItem('mcp_token'); window.location.href = '/' }}
          style={{ fontSize: 13, color: '#ef4444', background: 'transparent', border: '1px solid rgba(239,68,68,0.3)', borderRadius: 6, padding: '6px 14px', cursor: 'pointer' }}>
          Logout
        </button>
      </div>

      {/* Date filter */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, fontSize: 13 }}>
        <label style={{ color: 'var(--text-muted)', fontSize: 12 }}>From</label>
        <input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)}
          style={{ padding: '4px 8px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--bg)', color: 'var(--text)', fontSize: 12 }} />
        <label style={{ color: 'var(--text-muted)', fontSize: 12 }}>To</label>
        <input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)}
          style={{ padding: '4px 8px', borderRadius: 6, border: '1px solid var(--border)', background: 'var(--bg)', color: 'var(--text)', fontSize: 12 }} />
        {(dateFrom || dateTo) && (
          <button onClick={() => { setDateFrom(''); setDateTo('') }}
            style={{ fontSize: 11, color: 'var(--text-muted)', background: 'transparent', border: 'none', cursor: 'pointer' }}>Clear</button>
        )}
        <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>{dateFrom || dateTo ? `${dateFrom || 'start'} → ${dateTo || 'now'}` : 'All time'}</span>
      </div>

      {/* Total spend */}
      <div style={{ ...cardStyle, background: 'var(--bg-card)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span style={{ fontSize: 14, color: 'var(--text-muted)' }}>Total Spend</span>
        <span style={{ ...bigNum }}>${(costs.total_usd || 0).toFixed(2)}</span>
      </div>

      {/* Cost breakdown */}
      <div>
        <div style={labelStyle}>Costs {dateFrom || dateTo ? `(${dateFrom || 'start'} → ${dateTo || 'now'})` : '(All Time)'}</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
          {/* Apollo */}
          <div style={cardStyle}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
              <div style={{ width: 8, height: 8, borderRadius: '50%', background: '#6366f1' }} />
              <span style={{ fontSize: 13, fontWeight: 600 }}>Apollo</span>
            </div>
            <div style={bigNum}>${(apollo.cost_usd || 0).toFixed(2)}</div>
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 6, lineHeight: 1.6 }}>
              {apollo.credits || 0} credits<br />
              Search: {apollo.gathering_credits || 0}<br />
              Enrich: {apollo.enrichment_credits || 0}
            </div>
          </div>

          {/* OpenAI */}
          <div style={cardStyle}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
              <div style={{ width: 8, height: 8, borderRadius: '50%', background: '#10b981' }} />
              <span style={{ fontSize: 13, fontWeight: 600 }}>OpenAI</span>
            </div>
            <div style={bigNum}>${(openai.total_cost_usd || 0).toFixed(4)}</div>
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 6, lineHeight: 1.6 }}>
              {(openai.total_tokens || 0).toLocaleString()} tokens
              {Object.entries(openaiModels).map(([model, d]: any) => (
                <div key={model} style={{ display: 'flex', justifyContent: 'space-between', gap: 4, marginTop: 2 }}>
                  <span style={{ color: 'var(--text-muted)', fontSize: 10 }}>{model}</span>
                  <span style={{ fontSize: 10, fontVariantNumeric: 'tabular-nums' }}>${d.cost_usd?.toFixed(4)}</span>
                </div>
              ))}
            </div>
          </div>

          {/* Apify */}
          <div style={cardStyle}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
              <div style={{ width: 8, height: 8, borderRadius: '50%', background: '#f59e0b' }} />
              <span style={{ fontSize: 13, fontWeight: 600 }}>Apify</span>
            </div>
            <div style={bigNum}>${(apify.cost_usd || 0).toFixed(2)}</div>
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 6, lineHeight: 1.6 }}>
              {apify.websites_scraped || 0} websites<br />
              {apify.gb_used || 0} GB proxy
            </div>
          </div>

          {/* MCP */}
          <div style={cardStyle}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
              <div style={{ width: 8, height: 8, borderRadius: '50%', background: '#f43f5e' }} />
              <span style={{ fontSize: 13, fontWeight: 600 }}>MCP</span>
            </div>
            <div style={bigNum}>{((mcp.total_tokens || 0) / 1000).toFixed(1)}K</div>
            <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 6, lineHeight: 1.6 }}>
              {(mcp.input_tokens || 0).toLocaleString()} in<br />
              {(mcp.output_tokens || 0).toLocaleString()} out<br />
              {mcp.tool_calls || 0} calls
            </div>
          </div>
        </div>
      </div>

      {/* Stats */}
      <div>
        <div style={labelStyle}>Your Stats</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
          {[
            { label: 'Contacts', value: stats.total_contacts },
            { label: 'Companies', value: stats.total_companies },
            { label: 'Campaigns', value: stats.total_campaigns },
            { label: 'Tool Calls', value: stats.total_tool_calls },
          ].map(s => (
            <div key={s.label} style={{ ...cardStyle, textAlign: 'center' as const }}>
              <div style={{ fontSize: 22, fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>{(s.value || 0).toLocaleString()}</div>
              <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>{s.label}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Pipeline Runs */}
      {runs.length > 0 && (
        <div>
          <div style={labelStyle}>Pipeline Runs</div>
          <div style={{ border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
            <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border)' }}>
                  {['Run', 'Source', 'Companies', 'Targets', 'Credits', 'Phase', 'Date'].map(h => (
                    <th key={h} style={{ padding: '8px 12px', textAlign: h === 'Companies' || h === 'Targets' || h === 'Credits' ? 'right' : 'left', fontSize: 10, textTransform: 'uppercase', letterSpacing: 0.5, color: 'var(--text-muted)', fontWeight: 500 }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {runs.map((r: any) => (
                  <tr key={r.id} style={{ borderBottom: '1px solid var(--border)' }}>
                    <td style={{ padding: '8px 12px' }}><a href={`/pipeline/${r.id}`} style={{ color: 'var(--text-link)' }}>#{r.id}</a></td>
                    <td style={{ padding: '8px 12px', color: 'var(--text-muted)' }}>{r.source_type?.replace('companies.', '').replace('.manual', '').replace('apollo.', '')}</td>
                    <td style={{ padding: '8px 12px', textAlign: 'right' }}>{r.companies}</td>
                    <td style={{ padding: '8px 12px', textAlign: 'right' }}>
                      {r.targets > 0 ? <><span style={{ color: '#22c55e', fontWeight: 600 }}>{r.targets}</span><span style={{ color: 'var(--text-muted)', marginLeft: 4, fontSize: 11 }}>{r.target_rate}</span></> : ''}
                    </td>
                    <td style={{ padding: '8px 12px', textAlign: 'right', fontFamily: 'monospace' }}>
                      {r.credits_used > 0 ? <span style={{ color: '#f59e0b' }}>{r.credits_used}</span> : <span style={{ color: 'var(--text-muted)' }}>0</span>}
                    </td>
                    <td style={{ padding: '8px 12px' }}>
                      <span style={{ padding: '2px 8px', borderRadius: 4, fontSize: 11, background: 'var(--bg)', color: 'var(--text-muted)' }}>{r.phase}</span>
                    </td>
                    <td style={{ padding: '8px 12px', color: 'var(--text-muted)', fontSize: 11 }}>
                      {r.created_at ? new Date(r.created_at).toLocaleDateString() : ''}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Integrations */}
      <div>
        <div style={labelStyle}>Connected Services</div>
        <div style={{ border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
          {(account.integrations || []).length === 0 ? (
            <div style={{ padding: '12px 16px', fontSize: 13, color: 'var(--text-muted)' }}>No integrations. <a href="/setup" style={{ color: 'var(--text-link)' }}>Set up API keys</a></div>
          ) : (
            (account.integrations || []).map((i: any, idx: number) => (
              <div key={i.name} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 16px', borderTop: idx > 0 ? '1px solid var(--border)' : 'none' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <div style={{ width: 8, height: 8, borderRadius: '50%', background: i.connected ? '#22c55e' : 'var(--border)' }} />
                  <span style={{ fontSize: 13, fontWeight: 500, textTransform: 'capitalize' }}>{i.name}</span>
                </div>
                <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>{i.info || (i.connected ? 'Connected' : 'Not connected')}</span>
              </div>
            ))
          )}
        </div>
      </div>

      {/* Usage by Tool */}
      {Object.keys(byTool).length > 0 && (
        <div>
          <div style={labelStyle}>Usage by Tool</div>
          <div style={{ border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
            {Object.entries(byTool).sort((a: any, b: any) => b[1] - a[1]).map(([tool, count]: any, idx: number) => (
              <div key={tool} style={{ display: 'flex', justifyContent: 'space-between', padding: '8px 16px', borderTop: idx > 0 ? '1px solid var(--border)' : 'none' }}>
                <span style={{ fontSize: 12, fontFamily: 'monospace' }}>{tool}</span>
                <span style={{ fontSize: 12, fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{count}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
