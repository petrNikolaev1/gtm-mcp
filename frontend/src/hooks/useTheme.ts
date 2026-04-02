import { create } from 'zustand'

type ThemeMode = 'dark' | 'light'

function getInitial(): ThemeMode {
  if (typeof window === 'undefined') return 'light'
  const stored = localStorage.getItem('mcp-theme')
  if (stored === 'dark') return 'dark'
  return 'light'  // Default to light theme (matches main app)
}

function apply(mode: ThemeMode) {
  if (mode === 'dark') {
    document.documentElement.classList.add('dark')
  } else {
    document.documentElement.classList.remove('dark')
  }
  localStorage.setItem('mcp-theme', mode)
}

export const useTheme = create<{
  mode: ThemeMode
  isDark: boolean
  toggle: () => void
  setMode: (m: ThemeMode) => void
}>((set, get) => {
  const initial = getInitial()
  if (typeof window !== 'undefined') apply(initial)

  return {
    mode: initial,
    isDark: initial === 'dark',
    toggle: () => {
      const next: ThemeMode = get().mode === 'dark' ? 'light' : 'dark'
      apply(next)
      set({ mode: next, isDark: next === 'dark' })
    },
    setMode: (m: ThemeMode) => {
      apply(m)
      set({ mode: m, isDark: m === 'dark' })
    },
  }
})
