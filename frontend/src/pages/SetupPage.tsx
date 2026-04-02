import { useState, useEffect } from 'react'

const API = '/api'
const ALL_SERVICES = ['smartlead', 'apollo', 'openai', 'apify', 'getsales']

const SERVICE_LABELS: Record<string, { label: string; hint: string }> = {
  smartlead: { label: 'SmartLead', hint: 'Email outreach platform' },
  apollo: { label: 'Apollo', hint: 'Company & people search' },
  openai: { label: 'OpenAI', hint: 'AI for sequence generation' },
  apify: { label: 'Apify Proxy', hint: 'Residential proxy for website scraping (30% more companies scraped)' },
  getsales: { label: 'GetSales', hint: 'LinkedIn outreach platform' },
}

const inputStyle: React.CSSProperties = {
  width: '100%', background: 'var(--bg)', border: '1px solid var(--border)',
  borderRadius: 8, padding: '10px 14px', color: 'var(--text)', outline: 'none', fontSize: 14,
  boxSizing: 'border-box' as const,
}

export default function SetupPage() {
  const [token] = useState(localStorage.getItem('mcp_token') || '')
  const [userName, setUserName] = useState('')
  const [userEmail, setUserEmail] = useState('')
  const [integrations, setIntegrations] = useState<any[]>([])
  const [editingService, setEditingService] = useState<string | null>(null)
  const [newKey, setNewKey] = useState('')
  const [saving, setSaving] = useState(false)
  const [result, setResult] = useState('')
  const [tokenCopied, setTokenCopied] = useState(false)
  const [showToken, setShowToken] = useState(false)

  const copyToken = () => {
    // Fallback for HTTP (navigator.clipboard requires HTTPS)
    const ta = document.createElement('textarea')
    ta.value = token
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    document.body.removeChild(ta)
    setTokenCopied(true)
    setTimeout(() => setTokenCopied(false), 2000)
  }

  useEffect(() => {
    if (!token) { window.location.href = '/'; return }
    fetch(`${API}/auth/me`, { headers: { 'X-MCP-Token': token } })
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (d) { setUserName(d.name); setUserEmail(d.email) }
        else { localStorage.removeItem('mcp_token'); window.location.href = '/' }
      })
    loadIntegrations()
  }, [token])

  const loadIntegrations = () => {
    if (!token) return
    fetch(`${API}/setup/integrations`, { headers: { 'X-MCP-Token': token } })
      .then(r => r.ok ? r.json() : [])
      .then(setIntegrations)
  }

  const saveKey = async (service: string) => {
    const trimmed = newKey.trim()
    if (!trimmed) return
    setSaving(true); setResult('')
    const res = await fetch(`${API}/setup/integrations`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-MCP-Token': token },
      body: JSON.stringify({ integration_name: service, api_key: trimmed }),
    })
    const data = await res.json()
    setResult(data.message || JSON.stringify(data))
    setSaving(false); setNewKey(''); setEditingService(null)
    loadIntegrations()
  }

  const logout = () => {
    localStorage.removeItem('mcp_token')
    window.location.href = '/'
  }

  // Build service list: all services with their connection status
  const serviceMap = new Map(integrations.map((i: any) => [i.name, i]))

  return (
    <div style={{ maxWidth: 600, margin: '0 auto', padding: '32px 24px', display: 'flex', flexDirection: 'column', gap: 32 }}>

      {/* Account */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 12, padding: '20px 24px' }}>
        <div>
          <div style={{ fontWeight: 600, fontSize: 16 }}>{userName || 'Loading...'}</div>
          <div style={{ fontSize: 13, color: 'var(--text-muted)', marginTop: 2 }}>{userEmail}</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 6 }}>
            <code style={{ fontSize: 11, color: 'var(--text-muted)', fontFamily: 'monospace', opacity: 0.6, wordBreak: 'break-all' }}>
              {showToken ? token : `${token.slice(0, 24)}...`}
            </code>
            <button onClick={() => setShowToken(!showToken)} style={{ fontSize: 10, color: 'var(--text-muted)', background: 'transparent', border: 'none', cursor: 'pointer', padding: '2px 4px', opacity: 0.6 }}>
              {showToken ? 'Hide' : 'Show'}
            </button>
            <button onClick={() => { copyToken() }}
              style={{ fontSize: 10, padding: '2px 8px', borderRadius: 4, border: '1px solid var(--border)', background: tokenCopied ? '#22c55e' : 'transparent', color: tokenCopied ? 'white' : 'var(--text-muted)', cursor: 'pointer', whiteSpace: 'nowrap' }}>
              {tokenCopied ? 'Copied!' : 'Copy'}
            </button>
          </div>
        </div>
        <button onClick={logout} style={{ fontSize: 13, color: '#ef4444', background: 'transparent', border: '1px solid rgba(239,68,68,0.3)', borderRadius: 6, padding: '6px 14px', cursor: 'pointer' }}>Logout</button>
      </div>

      {/* API Keys */}
      <div>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
          <h2 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>API Keys</h2>
          <button onClick={loadIntegrations} style={{ fontSize: 12, color: '#3b82f6', background: 'transparent', border: 'none', cursor: 'pointer' }}>Refresh</button>
        </div>

        <div style={{ border: '1px solid var(--border)', borderRadius: 12, overflow: 'hidden' }}>
          {ALL_SERVICES.map((service, idx) => {
            const info = serviceMap.get(service)
            const connected = info?.connected
            const isEditing = editingService === service

            return (
              <div key={service} style={{
                padding: '14px 20px',
                borderTop: idx > 0 ? '1px solid var(--border)' : 'none',
                display: 'flex', alignItems: 'center', gap: 12,
              }}>
                {/* Status dot */}
                <div style={{
                  width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
                  background: connected ? '#22c55e' : 'var(--border)',
                }} />

                {/* Service name + status */}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 14, fontWeight: 500 }}>{SERVICE_LABELS[service]?.label || service}</div>
                  <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 1 }}>
                    {connected ? (info?.info || 'Connected') : (SERVICE_LABELS[service]?.hint || 'Not connected')}
                  </div>
                </div>

                {/* Action */}
                {isEditing ? (
                  <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                    <input
                      style={{ ...inputStyle, width: 220, fontSize: 12, padding: '6px 10px', fontFamily: 'monospace' }}
                      placeholder="Paste API key"
                      type="password"
                      value={newKey}
                      onChange={e => setNewKey(e.target.value)}
                      onPaste={e => { e.preventDefault(); setNewKey((e.clipboardData.getData('text') || '').trim()) }}
                      onKeyDown={e => e.key === 'Enter' && saveKey(service)}
                      autoFocus
                    />
                    <button onClick={() => saveKey(service)} disabled={saving} style={{
                      fontSize: 12, padding: '6px 12px', borderRadius: 6,
                      background: '#3b82f6', color: 'white', border: 'none', cursor: 'pointer',
                      opacity: saving ? 0.5 : 1,
                    }}>{saving ? '...' : 'Save'}</button>
                    <button onClick={() => { setEditingService(null); setNewKey('') }} style={{
                      fontSize: 12, padding: '6px 8px', background: 'transparent', border: 'none',
                      color: 'var(--text-muted)', cursor: 'pointer',
                    }}>Cancel</button>
                  </div>
                ) : (
                  <button onClick={() => { setEditingService(service); setResult('') }} style={{
                    fontSize: 12, padding: '5px 12px', borderRadius: 6,
                    background: 'transparent', border: '1px solid var(--border)',
                    color: 'var(--text-secondary)', cursor: 'pointer',
                  }}>
                    {connected ? 'Update' : 'Connect'}
                  </button>
                )}
              </div>
            )
          })}
        </div>

        {result && <div style={{ fontSize: 13, color: 'var(--text-muted)', marginTop: 8, textAlign: 'center' }}>{result}</div>}
      </div>

      {/* Telegram Notifications */}
      <div>
        <h2 style={{ fontSize: 16, fontWeight: 600, marginBottom: 12 }}>Telegram Notifications</h2>
        <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 12, padding: '16px 20px' }}>
          {(() => {
            const tg = serviceMap.get('telegram')
            if (tg?.connected) {
              return (
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <div style={{ width: 8, height: 8, borderRadius: '50%', background: '#22c55e' }} />
                  <div>
                    <div style={{ fontSize: 14, fontWeight: 500 }}>Connected</div>
                    <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>{tg.info || 'Receiving reply notifications'}</div>
                  </div>
                </div>
              )
            }
            return (
              <div>
                <div style={{ fontSize: 13, color: 'var(--text-muted)', marginBottom: 12 }}>
                  Get notified on Telegram when leads reply to your campaigns.
                </div>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  <a
                    href="https://t.me/YOUR_BOT_HERE?start=connect"
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{
                      display: 'inline-flex', alignItems: 'center', gap: 6,
                      padding: '8px 16px', borderRadius: 8,
                      background: '#0088cc', color: 'white',
                      fontSize: 13, fontWeight: 500, textDecoration: 'none',
                    }}
                  >
                    Connect Telegram
                  </a>
                  <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                    Open the bot, then paste your MCP token to link accounts.
                  </span>
                </div>
              </div>
            )
          })()}
        </div>
      </div>

      {/* MCP Connection Info */}
      <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 12, padding: '16px 20px' }}>
        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>Connect via Claude Code</div>
        <code style={{ fontSize: 12, color: 'var(--text-muted)', display: 'block', background: 'var(--bg)', padding: '10px 14px', borderRadius: 6, overflowX: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
{`{
  "mcpServers": {
    "leadgen": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://gtm-mcp.com/mcp/sse?token=${showToken ? token : token.slice(0, 16) + '...'}"]
    }
  }
}`}
        </code>
        <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 8, display: 'flex', alignItems: 'center', gap: 6 }}>
          <button onClick={() => { navigator.clipboard.writeText(JSON.stringify({mcpServers:{leadgen:{command:"npx",args:["-y","mcp-remote",`https://gtm-mcp.com/mcp/sse?token=${token}`]}}}, null, 2)); copyToken() }}
            style={{ fontSize: 11, padding: '4px 12px', borderRadius: 4, border: '1px solid var(--border)', background: tokenCopied ? '#22c55e' : 'transparent', color: tokenCopied ? 'white' : '#3b82f6', cursor: 'pointer', fontWeight: 500 }}>
            {tokenCopied ? 'Copied!' : 'Copy .mcp.json'}
          </button>
          <span>Paste into your project's <code style={{ fontSize: 11 }}>.mcp.json</code> file. Requires Node.js.</span>
        </div>
      </div>
    </div>
  )
}
