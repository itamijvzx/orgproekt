import { create } from 'zustand'
import { localToday } from './api'
export type Selection = { kind: 'department' | 'position' | 'seat'; id: string } | null
type UIState = {
  page: string
  asOf: string
  query: string
  coverage: string
  view: 'graph' | 'table'
  department: string
  overviewQuery: string
  overviewDepartment: string
  overviewCoverage: 'all' | 'uncovered' | 'unknown'
  selection: Selection
  expanded: string[]
  positions: Record<string, { x: number; y: number }>
  viewport: { x: number; y: number; zoom: number } | null
  set: (patch: Partial<Omit<UIState, 'set' | 'reset'>>) => void
  reset: () => void
}
const initial = () => ({
  page: 'overview',
  asOf: localToday(),
  query: '',
  coverage: 'all',
  department: '',
  overviewQuery: '',
  overviewDepartment: '',
  overviewCoverage: 'all' as const,
  view: 'table' as const,
  selection: null,
  expanded: [],
  positions: {},
  viewport: null,
})
export const useUI = create<UIState>((set) => ({
  ...initial(),
  set: (patch) => set(patch),
  reset: () => set(initial()),
}))
