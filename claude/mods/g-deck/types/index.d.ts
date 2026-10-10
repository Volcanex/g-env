export type Grade = { grade: string; provider: 'claude' | 'codex' }
export type Grades = { grades: Record<string, Grade>; temps: Record<string, string | null> }
export type Agent = {
  id: string
  type: string
  description: string
  model: string
  effort?: string
  parentId?: string
  started: number
  ended?: number
  status: string
  steps: number
  fails: number
  lastTool?: string
  luna: number
  ctx?: number
  scout?: { running: boolean; found: number }
}
export type Job = { id: string; label: string; point?: string; started: number; ended?: number; status: string }

declare module 'claude-code' {
  interface PluginState {
    'g-deck': { agents: Record<string, Agent>; jobs: Job[]; grades: Grades }
  }
}
