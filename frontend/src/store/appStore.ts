import { create } from 'zustand'

interface AppState {
  currentUser: any
  currentCompany: any
  currentProject: any
  projects: any[]
  companies: any[]
  environments: any[]
  currentEnvironment: any
  setCurrentUser: (u: any) => void
  setCurrentCompany: (c: any) => void
  setCurrentProject: (p: any) => void
  setProjects: (p: any[]) => void
  setCompanies: (c: any[]) => void
  setEnvironments: (e: any[]) => void
  setCurrentEnvironment: (e: any) => void
  addCompany: (c: any) => void
  updateCompany: (c: any) => void
  removeCompany: (id: number) => void
  addEnvironment: (e: any) => void
  updateEnvironment: (e: any) => void
  removeEnvironment: (id: number) => void
  [key: string]: any
}

export const useAppStore = create<AppState>((set) => ({
  currentUser: null,
  currentCompany: { id: 1, name: 'MCP' },
  currentProject: null,
  projects: [],
  companies: [{ id: 1, name: 'MCP' }],
  environments: [],
  currentEnvironment: null,
  setCurrentUser: (u) => set({ currentUser: u }),
  setCurrentCompany: (c) => set({ currentCompany: c }),
  setCurrentProject: (p) => set({ currentProject: p }),
  setProjects: (p) => set({ projects: p }),
  setCompanies: (c) => set({ companies: c }),
  setEnvironments: (e) => set({ environments: e }),
  setCurrentEnvironment: (e) => set({ currentEnvironment: e }),
  addCompany: () => {},
  updateCompany: () => {},
  removeCompany: () => {},
  addEnvironment: () => {},
  updateEnvironment: () => {},
  removeEnvironment: () => {},
}))
