import type { Agent } from '../types'

const EFFORT = /^(opus|sonnet|fable|haiku)-(low|medium|high|xhigh|max)-/

export function family(model: string) {
  return (model.match(/opus|sonnet|fable|haiku/) ?? ['?'])[0]
}

// The point is model and effort: from the agent's name (opus-low-worker) or what its requests asked for.
export function point(agent: Agent) {
  const named = agent.type.match(EFFORT)
  if (named) return `${named[1]}-${named[2]}`
  return `${family(agent.model)}-${agent.effort ?? 'medium'}`
}

export function age(ms: number) {
  const s = Math.max(0, Math.round(ms / 1000))
  return s < 60 ? `${s}s` : s < 3600 ? `${Math.round(s / 60)}m` : `${Math.floor(s / 3600)}h${Math.round((s % 3600) / 60)}m`
}

export function pad(text: string, width: number) {
  return text.length > width ? text.slice(0, width - 1) + '…' : text.padEnd(width)
}

export function marks(agent: Agent) {
  const out = [`t${agent.steps}`]
  if (agent.fails) out.push(`✗${agent.fails}`)
  if (agent.ended !== undefined) out.push(agent.status === 'completed' ? 'done' : agent.status)
  else if (agent.lastTool) out.push(agent.lastTool)
  return out.join(' ')
}

// Parents first, each child straight under its parent; an orphan (its parent already gone) sits at the top.
export function order(all: Record<string, Agent>) {
  const out: (Agent & { depth: number })[] = []
  const place = (parent: string | undefined, depth: number) => {
    const children = Object.values(all)
      .filter(agent => (parent ? agent.parentId === parent : !agent.parentId || !all[agent.parentId]))
      .sort((a, b) => a.started - b.started)
    for (const agent of children) {
      out.push({ ...agent, depth })
      place(agent.id, depth + 1)
    }
  }
  place(undefined, 0)
  return out
}

// Context bands: blue to 100k, then 50k apart, so red starts 50k before auto-compact at 250k. Same as the statusline ctx.
export const CTX_BANDS: [number, string][] = [
  [100_000, '#6ab0ff'], [150_000, '#4ade80'], [200_000, '#ffd60a'], [Infinity, '#ff5347'],
]

export function ctxColor(tokens: number) {
  return CTX_BANDS.find(([limit]) => tokens < limit)![1]
}

export function kilo(tokens?: number) {
  return tokens === undefined ? '' : `${Math.round(tokens / 1000)}k`
}

// Claims `id` in `seen` once: true the first time, false after. Synchronous, so it is atomic in the hook's loop.
export function claimOnce(seen: Set<string>, id: string) {
  if (seen.has(id)) return false
  seen.add(id)
  return true
}
