import { api } from './client'
export { api }
export type { Contact, ContactListResponse, ContactStats, FilterOptions, Project, AISDRProject } from './contacts'

export type ImportResult = { success: boolean; imported: number; errors: string[]; skipped?: number; not_found?: number; created?: number; sample_created?: any[]; [key: string]: any }
export type EnrichResult = { success: boolean; enriched: number; skipped?: number; not_found?: number; errors?: string[]; [key: string]: any }

export const contactsApi = {
  async list(params: Record<string, any> = {}) {
    const qs = new URLSearchParams()
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null && v !== '') qs.set(k, String(v))
    }
    const { data } = await api.get(`/contacts?${qs}`)
    return data
  },

  async getStats(projectId?: number) {
    const qs = projectId ? `?project_id=${projectId}` : ''
    const { data } = await api.get(`/contacts/stats${qs}`)
    return data
  },

  async getFilterOptions() {
    const { data } = await api.get('/contacts/filters')
    return data
  },

  async listProjectNames(_opts?: any) {
    const { data } = await api.get('/contacts/projects/names')
    return data
  },

  async listProjects() {
    const { data } = await api.get('/contacts/projects/list')
    return data
  },

  async listCampaigns() {
    const { data } = await api.get('/contacts/campaigns')
    return data
  },

  async updateStatus(id: number, status: string) { const { data } = await api.patch(`/contacts/${id}`, { status }); return data },
  async exportCsv(_filters: any) { return new Blob() },
  async exportGoogleSheet(_filters: any): Promise<any> { return { rows: 0, url: '' } },
  async deleteMany(_ids: number[]) { return {} },
  async pushToSmartlead(_ids: number[], _name?: any, _opts?: any): Promise<any> { return { leads_added: 0, campaign_url: '', campaign_name: '' } },
  async importCsv(_file: File, _opts?: any): Promise<ImportResult> { return { success: true, imported: 0, errors: [] } },
  async enrichCsv(_file: File, _opts?: any): Promise<EnrichResult> { return { success: true, enriched: 0 } },
  async generateReply(_id: number) { return { has_reply: false } },
  async crmSpotlightGTM(_projectId: number, _question: string, _filters: any) { return {} },
  async getProject(_id: number): Promise<any> { return {} },
  async deleteProject(_id: number): Promise<any> { return {} },
  async getProjectAISDR(_id: number): Promise<any> { return { tam_analysis: null, gtm_plan: null, pitch_templates: null } },
  async generateTAM(_id: number): Promise<any> { return { tam_analysis: '' } },
  async generateGTM(_id: number): Promise<any> { return { gtm_plan: '' } },
  async generatePitches(_id: number): Promise<any> { return { pitch_templates: '' } },
  async generateAllAISDR(_id: number): Promise<any> { return { tam_analysis: '', gtm_plan: '', pitch_templates: '' } },
  async getImportTemplate(): Promise<any> { return new Blob() },
  async importCsvBulk(_file: File, _opts: any): Promise<ImportResult> { return { success: true, imported: 0, errors: [] } },
  async create(_data: any): Promise<any> { return {} },
  async update(_id: number, _data: any): Promise<any> { return {} },
  async delete(_id: number): Promise<any> { return {} },
  async bulkCreate(_data: any): Promise<any> { return {} },
  async createProject(_data: any): Promise<any> { return {} },
  async updateProject(_id: number, _data: any): Promise<any> { return {} },
  async verifyCampaigns(_names: string[]): Promise<any> { return {} },
}
