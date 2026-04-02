import { useState } from 'react'

const API = '/api'

export default function LoginPage() {
  const [tab, setTab] = useState<'login' | 'signup'>('login')
  const [email, setEmail] = useState('')
  const [name, setName] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [signupToken, setSignupToken] = useState<string | null>(null)
  const [tokenCopied, setTokenCopied] = useState(false)

  const handleLogin = async () => {
    const trimmedEmail = email.trim()
    if (!trimmedEmail || !password) { setError('Email and password required'); return }
    setLoading(true); setError('')
    try {
      const res = await fetch(`${API}/auth/login`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: trimmedEmail, password }),
      })
      const data = await res.json()
      if (data.api_token) {
        localStorage.setItem('mcp_token', data.api_token)
        window.location.reload()  // Reload preserves the URL user was trying to visit
      } else { setError(data.detail || 'Invalid email or password') }
    } catch { setError('Connection failed') }
    setLoading(false)
  }

  const handleSignup = async () => {
    const trimmedEmail = email.trim()
    const trimmedName = name.trim()
    if (!trimmedEmail || !password) { setError('Email and password required'); return }
    setLoading(true); setError('')
    try {
      const res = await fetch(`${API}/auth/signup`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: trimmedEmail, name: trimmedName || trimmedEmail.split('@')[0], password }),
      })
      const data = await res.json()
      if (data.api_token) {
        localStorage.setItem('mcp_token', data.api_token)
        setSignupToken(data.api_token)
        // Show token for 5 seconds, then redirect to setup
        setTimeout(() => { window.location.href = '/setup' }, 5000)
      } else { setError(data.detail || data.message || 'Signup failed') }
    } catch { setError('Connection failed') }
    setLoading(false)
  }

  const submit = tab === 'login' ? handleLogin : handleSignup

  return (
    <div style={{
      minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
      background: 'var(--bg, #f5f5f5)',
    }}>
      <div style={{
        width: 400, background: 'var(--bg-card, #fff)', borderRadius: 16,
        boxShadow: '0 4px 24px rgba(0,0,0,0.08)', padding: '40px 36px',
      }}>
        {/* Token display after signup */}
        {signupToken && (
          <div style={{ marginBottom: 24, padding: '16px 20px', borderRadius: 12, background: 'rgba(34,197,94,0.08)', border: '1px solid rgba(34,197,94,0.2)' }}>
            <div style={{ fontSize: 14, fontWeight: 600, color: '#22c55e', marginBottom: 8 }}>Account created!</div>
            <div style={{ fontSize: 12, color: 'var(--text-muted, #666)', marginBottom: 8 }}>Save your API token — you'll need it for Claude Code:</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <code style={{ flex: 1, fontSize: 11, padding: '8px 10px', background: 'var(--bg, #f5f5f5)', borderRadius: 6, wordBreak: 'break-all', fontFamily: 'monospace' }}>{signupToken}</code>
              <button onClick={() => { const ta = document.createElement('textarea'); ta.value = signupToken; ta.style.position = 'fixed'; ta.style.opacity = '0'; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); document.body.removeChild(ta); setTokenCopied(true); setTimeout(() => setTokenCopied(false), 2000) }}
                style={{ fontSize: 11, padding: '6px 12px', borderRadius: 6, border: '1px solid var(--border, #e5e5e5)', background: tokenCopied ? '#22c55e' : 'var(--bg, #fff)', color: tokenCopied ? 'white' : 'var(--text, #333)', cursor: 'pointer', whiteSpace: 'nowrap' }}>
                {tokenCopied ? 'Copied!' : 'Copy'}
              </button>
            </div>
            <div style={{ fontSize: 11, color: 'var(--text-muted, #999)', marginTop: 8 }}>Redirecting in 3 seconds...</div>
          </div>
        )}

        {/* Logo */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 32 }}>
          <div style={{
            width: 36, height: 36, borderRadius: 10, display: 'flex', alignItems: 'center', justifyContent: 'center',
            background: 'var(--text, #333)',
          }}>
            <span style={{ fontWeight: 800, fontSize: 16, color: 'var(--bg, #fff)' }}>M</span>
          </div>
          <div>
            <div style={{ fontWeight: 700, fontSize: 18, color: 'var(--text, #333)' }}>MCP LeadGen</div>
            <div style={{ fontSize: 12, color: 'var(--text-muted, #999)' }}>AI-powered lead generation platform</div>
          </div>
        </div>

        {/* Tabs */}
        <div style={{ display: 'flex', marginBottom: 24, borderBottom: '1px solid var(--border, #e5e5e5)' }}>
          {(['login', 'signup'] as const).map(t => (
            <button key={t} onClick={() => { setTab(t); setError('') }} style={{
              flex: 1, padding: '10px 0', fontSize: 14, fontWeight: 500, cursor: 'pointer',
              background: 'transparent', border: 'none',
              color: tab === t ? 'var(--text, #333)' : 'var(--text-muted, #999)',
              borderBottom: tab === t ? '2px solid #3b82f6' : '2px solid transparent',
            }}>
              {t === 'login' ? 'Log In' : 'Sign Up'}
            </button>
          ))}
        </div>

        {/* Form */}
        <form onSubmit={e => { e.preventDefault(); submit() }} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <input
            type="email" placeholder="Email" value={email}
            onChange={e => setEmail(e.target.value)}
            style={{
              width: '100%', padding: '12px 14px', borderRadius: 8, fontSize: 14,
              border: '1px solid var(--border, #e5e5e5)', background: 'var(--bg, #f9f9f9)',
              color: 'var(--text, #333)', outline: 'none', boxSizing: 'border-box',
            }}
          />
          <input
            type="password" placeholder="Password" value={password}
            onChange={e => setPassword(e.target.value)}
            style={{
              width: '100%', padding: '12px 14px', borderRadius: 8, fontSize: 14,
              border: '1px solid var(--border, #e5e5e5)', background: 'var(--bg, #f9f9f9)',
              color: 'var(--text, #333)', outline: 'none', boxSizing: 'border-box',
            }}
          />

          {error && (
            <div style={{ fontSize: 13, color: '#ef4444', padding: '4px 0' }}>{error}</div>
          )}

          <button
            type="submit" disabled={loading}
            style={{
              width: '100%', padding: '12px', borderRadius: 8, fontSize: 14, fontWeight: 600,
              background: '#3b82f6', color: 'white', border: 'none', cursor: loading ? 'wait' : 'pointer',
              opacity: loading ? 0.7 : 1, marginTop: 4,
            }}
          >
            {loading ? '...' : tab === 'login' ? 'Log In' : 'Create Account'}
          </button>
        </form>

        {/* Switch hint */}
        <div style={{ marginTop: 16, textAlign: 'center', fontSize: 13, color: 'var(--text-muted, #999)' }}>
          {tab === 'login' ? (
            <>New here? <button onClick={() => setTab('signup')} style={{ background: 'none', border: 'none', color: '#3b82f6', cursor: 'pointer', fontSize: 13, fontWeight: 500 }}>Sign up</button></>
          ) : (
            <>Already have an account? <button onClick={() => setTab('login')} style={{ background: 'none', border: 'none', color: '#3b82f6', cursor: 'pointer', fontSize: 13, fontWeight: 500 }}>Log in</button></>
          )}
        </div>

        {/* MCP token link */}
        <div style={{ marginTop: 12, textAlign: 'center', fontSize: 12, color: 'var(--text-muted, #999)' }}>
          Have an API token? <button onClick={() => {
            const t = prompt('Paste your mcp_ token:')?.trim()
            if (t?.startsWith('mcp_')) {
              fetch(`${API}/auth/me`, { headers: { 'X-MCP-Token': t } })
                .then(r => r.ok ? r.json() : Promise.reject())
                .then(() => { localStorage.setItem('mcp_token', t); window.location.href = '/' })
                .catch(() => alert('Invalid token'))
            }
          }} style={{ background: 'none', border: 'none', color: '#3b82f6', cursor: 'pointer', fontSize: 12 }}>
            Login with token
          </button>
        </div>
      </div>
    </div>
  )
}
