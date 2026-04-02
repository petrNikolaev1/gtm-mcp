import { useState } from 'react'
import { useParams } from 'react-router-dom'

const TABS = ['replies', 'follow-ups', 'meetings'] as const

export function TasksPage() {
  const { tab: urlTab } = useParams()
  const [active, setActive] = useState<string>(urlTab || 'replies')

  return (
    <div style={{ maxWidth: 1100, margin: '0 auto', padding: 24 }}>
      <div style={{ fontSize: 11, fontWeight: 500, textTransform: 'uppercase', letterSpacing: 1, color: 'var(--text-muted)', marginBottom: 16 }}>Tasks</div>
      <div style={{ display: 'flex', gap: 4, marginBottom: 24 }}>
        {TABS.map(t => (
          <button
            key={t}
            onClick={() => setActive(t)}
            style={{
              padding: '6px 14px', borderRadius: 6, fontSize: 13, fontWeight: 500, border: 'none', cursor: 'pointer',
              background: active === t ? 'var(--active-bg)' : 'transparent',
              color: active === t ? 'var(--text)' : 'var(--text-muted)',
              textTransform: 'capitalize',
            }}
          >
            {t}
          </button>
        ))}
      </div>
      <div style={{ color: 'var(--text-muted)', padding: '40px 0', textAlign: 'center' }}>
        {active === 'replies' && 'Reply queue will appear here when campaigns receive responses.'}
        {active === 'follow-ups' && 'Follow-up tasks will appear here.'}
        {active === 'meetings' && 'Booked meetings will appear here.'}
      </div>
    </div>
  )
}
