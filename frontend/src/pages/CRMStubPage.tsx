import { useState, useEffect } from 'react'
import { useProject } from '../App'

export function CRMPage() {
  const [contacts, setContacts] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const { project } = useProject()

  useEffect(() => {
    const t = localStorage.getItem('mcp_token')
    const params = new URLSearchParams(window.location.search)
    if (project) params.set('project_id', String(project.id))
    fetch(`/api/contacts?${params}`, { headers: t ? { 'X-MCP-Token': t } : {} })
      .then(r => r.ok ? r.json() : [])
      .then(data => { setContacts(Array.isArray(data) ? data : data.contacts || []); setLoading(false) })
      .catch(() => setLoading(false))
  }, [project])

  if (loading) return <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-muted)' }}>Loading contacts...</div>

  return (
    <div style={{ maxWidth: 1200, margin: '0 auto', padding: 24 }}>
      <div style={{ fontSize: 11, fontWeight: 500, textTransform: 'uppercase', letterSpacing: 1, color: 'var(--text-muted)', marginBottom: 12 }}>
        CRM — {contacts.length} contacts
      </div>
      {contacts.length === 0 ? (
        <div style={{ color: 'var(--text-muted)', padding: '40px 0', textAlign: 'center' }}>
          No contacts yet. Run a pipeline to gather leads.
        </div>
      ) : (
        <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ textAlign: 'left', fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.5, color: 'var(--text-muted)' }}>
              <th style={{ paddingBottom: 8 }}>Name</th>
              <th style={{ paddingBottom: 8 }}>Email</th>
              <th style={{ paddingBottom: 8 }}>Title</th>
              <th style={{ paddingBottom: 8 }}>Company</th>
              <th style={{ paddingBottom: 8 }}>Status</th>
            </tr>
          </thead>
          <tbody>
            {contacts.map((c: any, i: number) => (
              <tr key={c.id || i} style={{ borderTop: '1px solid var(--border)' }}>
                <td style={{ padding: '8px 8px 8px 0' }}>{c.first_name} {c.last_name}</td>
                <td style={{ padding: '8px 8px 8px 0', color: 'var(--text-link)' }}>{c.email}</td>
                <td style={{ padding: '8px 8px 8px 0', color: 'var(--text-secondary)', fontSize: 12 }}>{c.job_title || '—'}</td>
                <td style={{ padding: '8px 8px 8px 0', fontSize: 12 }}>{c.company_name || '—'}</td>
                <td style={{ padding: '8px 8px 8px 0' }}>
                  <span style={{ padding: '2px 6px', borderRadius: 4, fontSize: 11, background: 'var(--active-bg)' }}>{c.status || 'new'}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
