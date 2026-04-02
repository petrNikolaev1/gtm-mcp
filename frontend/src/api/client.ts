/**
 * MCP API client — axios instance, compatible with main app's client.ts
 * Main app components import { api } from '../api/client' and call api.get(), api.post(), etc.
 * This file provides the same interface but with MCP-specific auth.
 */
import axios from 'axios'

const API_BASE_URL = import.meta.env.VITE_API_URL || ''

export const api = axios.create({
  baseURL: `${API_BASE_URL}/api`,
  headers: { 'Content-Type': 'application/json' },
})

// MCP auth: X-MCP-Token from localStorage
api.interceptors.request.use(config => {
  const token = localStorage.getItem('mcp_token')
  if (token) config.headers['X-MCP-Token'] = token
  config.headers['X-Company-ID'] = '1'
  return config
})

// Error handling (same pattern as main app)
api.interceptors.response.use(
  response => response,
  error => {
    let userMessage = 'An unexpected error occurred'
    if (error.response?.data?.detail) {
      userMessage = typeof error.response.data.detail === 'string' ? error.response.data.detail : JSON.stringify(error.response.data.detail)
    } else if (error.response?.data?.message) {
      userMessage = error.response.data.message
    }
    error.userMessage = userMessage
    console.error('API Error:', { url: error.config?.url, status: error.response?.status, message: userMessage })
    return Promise.reject(error)
  }
)

export const withCompanyId = (_companyId: number) => ({ headers: { 'X-Company-ID': '1' } })
export default api
