import type { EngineInterface, Register } from 'claude-code'

import type { Agent, Grades, Job } from '../types'

import { age, claimOnce, ctxColor, kilo, marks, order, pad, point } from './deck'

const PANE = 'g-deck'
const AGENTS = { plugin: 'g-deck', key: 'agents' } as const
const JOBS = { plugin: 'g-deck', key: 'jobs' } as const
const GRADES = { plugin: 'g-deck', key: 'grades' } as const

// A finished row stays this long, then leaves the deck.
const LINGER = 60_000
const TEMP: Record<string, string> = {
  BURN: 'red', HOT: 'redBright', WARM: 'yellow', COOL: 'cyan', COLD: 'blue', FREEZING: 'blueBright',
}

type $ = EngineInterface

// Docked beside a fullscreen transcript the deck takes a quarter of the terminal.
let dockedFor = 0

// Agents whose Luna digest went out. Claimed synchronously before the append, so neither an
// overlapping tick nor a patch() that writes back a stale row without `ended` can send it twice.
const digested = new Set<string>()
let ticking = false
const quarter = (columns: number) => Math.max(40, Math.floor(columns / 4))

async function agents($: $) {
  return (await $.state.get(AGENTS)).value ?? {}
}

async function patch($: $, id: string, fn: (agent: Agent) => Partial<Agent>) {
  const all = await agents($)
  if (!all[id]) return
  await $.state.set(AGENTS, { ...all, [id]: { ...all[id], ...fn(all[id]) } })
}

// The framework checkout: every host links ~/.agents/session-context.py from its context/.
async function framework($: $, home: string) {
  const run = await $.process.run(['readlink', '-f', `${home}/.agents/session-context.py`])
  return run.stdout.trim().replace(/\/context\/session-context\.py$/, '')
}

async function refreshGrades($: $, genv: string) {
  const run = await $.process.run(['python3', `${genv}/context/grades.py`, '--json'])
  if (run.exitCode !== 0) return
  const data = JSON.parse(run.stdout)
  const next: Grades = { grades: data.grades, temps: data.temps }
  const old = (await $.state.get(GRADES)).value
  if (JSON.stringify(old) !== JSON.stringify(next)) await $.state.set(GRADES, next)
}

async function readJobs($: $, home: string, now: number): Promise<Job[]> {
  const root = `${home}/.cache/genv/agent-jobs`
  if (!(await $.fs.exists(root))) return []
  const jobs: Job[] = []
  for (const entry of await $.fs.list(root)) {
    if (entry.kind !== 'dir' || now - entry.mtimeMs > 6 * 3600_000) continue
    const dir = `${root}/${entry.name}`
    const done = await $.fs.exists(`${dir}/exit`)
    const ended = done ? (await $.fs.stat(`${dir}/exit`)).mtimeMs : undefined
    if (ended && now - ended > LINGER) continue
    let err = ''
    try { err = await $.fs.read(`${dir}/err.txt`) } catch {}
    const starts = [...err.matchAll(/^\[g-agent-run\] start (\{.*\})$/gm)]
    const last = starts[starts.length - 1]?.[1]
    const start = last ? JSON.parse(last) : {}
    let status = 'running'
    if (done) status = (await $.fs.read(`${dir}/exit`)).trim() === '0' ? 'completed' : 'failed'
    jobs.push({ id: entry.name, label: start.label ?? entry.name.replace(/^\d+-\d+-/, ''), point: start.point,
                started: (await $.fs.stat(`${dir}/brief.txt`).catch(() => entry)).mtimeMs, ended, status })
  }
  return jobs.sort((a, b) => a.started - b.started)
}

export async function tick($: $, home: string) {
  const now = await $.clock.now()
  const all = await agents($)
  const live = new Map((await $.agent.list()).map(info => [info.id, info.status]))
  const next: Record<string, Agent> = {}
  for (const agent of Object.values(all)) {
    const status = live.get(agent.id) ?? (agent.status === 'running' ? 'completed' : agent.status)
    const isOver = !['pending', 'running', 'waiting', 'idle'].includes(status)
    const ended = isOver ? agent.ended ?? now : undefined
    if (ended && now - ended > LINGER) continue
    let luna = agent.luna
    let delivered: string[] = []
    let scout = agent.scout
    const dir = `${home}/.cache/genv/scout/${agent.id}`
    try {
      const state = JSON.parse(await $.fs.read(`${dir}/state.json`))
      luna = state.count ?? luna
      delivered = state.delivered ?? []
      let found = 0
      try { found = (await $.fs.read(`${dir}/findings.jsonl`)).split('\n').filter(Boolean).length } catch {}
      scout = { running: !(await $.fs.exists(`${dir}/stop`)), found }
    } catch {}
    // The orchestrator hears what Luna fed a worker once that worker is done, so it can weigh the result.
    if (isOver && agent.ended === undefined && delivered.length && claimOnce(digested, agent.id)) {
      const text = `Luna boost digest: ${agent.type} (${agent.description}) ended ${status} after ${delivered.length} pointer(s): ${delivered.join(' ')}`
      void $.session.append({ message: { type: 'user', content: [{ type: 'text', text }] } }).catch(() => {})
    }
    next[agent.id] = { ...agent, status, ended, luna, scout }
  }
  if (JSON.stringify(next) !== JSON.stringify(all)) await $.state.set(AGENTS, next)
  const jobs = await readJobs($, home, now)
  const old = (await $.state.get(JOBS)).value ?? []
  if (JSON.stringify(jobs) !== JSON.stringify(old)) await $.state.set(JOBS, jobs)
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const home = (await $.env.get('HOME')) ?? '/root'
    const genv = (await $.env.get('GENV')) ?? (await framework($, home))
    await $.command.register({ name: 'deck', description: 'Show running agents and their grades' })
    void refreshGrades($, genv)
    // One tick at a time: a slow tick must not overlap the next one reading the same stale rows.
    $.clock.every(3000, () => {
      if (ticking) return
      ticking = true
      void tick($, home).finally(() => { ticking = false })
    })
    $.clock.every(300_000, () => void refreshGrades($, genv))
    void $.ui.open({ id: PANE, title: 'Agents' })
    return next(e)
  })

  on('command.run', { command: 'deck' }, async ($, e) => {
    dockedFor = e.presentation.columns
    await $.ui.open({ id: PANE, title: 'Agents', columns: quarter(dockedFor) })
    return { text: 'Agent deck opened.' }
  })

  on('agent.spawn', async ($, e, next) => {
    const result = await next(e)
    // Bookkeeping never touches the call itself: a failure here leaves the spawn as it was.
    if (result.agentId) try {
      const agent: Agent = {
        id: result.agentId, type: e.subagentType, description: e.description, model: result.model,
        parentId: e.parentAgentId, started: await $.clock.now(), status: 'running', steps: 0, fails: 0, luna: 0,
      }
      await $.state.set(AGENTS, { ...(await agents($)), [agent.id]: agent })
    } catch {}
    return result
  })

  on('turn.step', async function* ($, e, next) {
    if (e.agentId) try {
      const effort = typeof e.effort === 'string' ? e.effort : undefined
      await patch($, e.agentId, agent => ({ steps: agent.steps + 1, model: e.model, effort: effort ?? agent.effort }))
    } catch {}
    const result = yield* next(e)
    // The request's whole input is what the context holds now.
    const usage = result.usage
    if (usage) try {
      const ctx = usage.input_tokens + usage.cache_read_input_tokens + usage.cache_creation_input_tokens
      if (e.agentId) await patch($, e.agentId, () => ({ ctx }))
    } catch {}
    return result
  })

  on('tool.call', async ($, e, next) => {
    const result = await next(e)
    if (e.agentId) try {
      const failed = Boolean('isError' in result && result.isError)
      await patch($, e.agentId, agent => ({ lastTool: String(e.tool), fails: failed ? agent.fails + 1 : 0 }))
    } catch {}
    return result
  })

  // A stacked, narrow window never seats an unasked pane, so the deck draws as the band above the prompt.
  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.surface !== 'terminal') return next(e)
    if ((await $.ui.panes()).some(pane => pane.id === PANE && pane.isPlaced)) return next(e)
    const hasAny = Object.keys(await agents($)).length > 0 || ((await $.state.get(JOBS)).value ?? []).length > 0
    if (!hasAny) return next(e)
    return deck($, e, (e.viewport?.columns ?? 80) - 6, false)
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const columns = e.viewport?.columns
    if (e.props.placement === 'dock' && columns && columns !== dockedFor) {
      dockedFor = columns
      void $.ui.open({ id: PANE, title: 'Agents', columns: quarter(columns) }).catch(() => {})
    }
    return deck($, e, e.props.bodyColumns ?? 60, true)
  })
}

async function deck($: $, e: Parameters<$['ui']['resolve']>[0], width: number, showEmpty: boolean) {
    const { Box, Text } = $.ui.resolve(e)
    const all = await agents($)
    const jobs = (await $.state.get(JOBS)).value ?? []
    const { grades = {}, temps = {} } = (await $.state.get(GRADES)).value ?? {}
    const now = await $.clock.now()
    const descWidth = Math.max(0, width - 49)

    const gradeOf = (key?: string) => (key && grades[key]) || undefined
    const tempColor = (provider?: string) => (provider && TEMP[temps[provider] ?? '']) || undefined

    const ordered = order(all)

    const row = (key: string, indent: number, gradeKey: string | undefined, name: string, desc: string,
                 elapsed: string, ctx: number | undefined, extra: string, isOver: boolean, isFailed: boolean) => {
      const grade = gradeOf(gradeKey)
      return (
        <Text key={key} wrap="truncate-end">
          <Text dimColor={isOver}>{' '.repeat(indent * 2)}</Text>
          <Text color={isOver ? undefined : tempColor(grade?.provider)} dimColor={isOver} bold={!isOver}>
            {(grade?.grade ?? '????').padEnd(5)}
          </Text>
          <Text dimColor={isOver}>{pad(name, 22 - indent * 2)} {descWidth ? pad(desc, descWidth) + ' ' : ''}{elapsed.padStart(6)} </Text>
          <Text color={ctx === undefined || isOver ? undefined : ctxColor(ctx)} dimColor={isOver}>{kilo(ctx).padStart(4)} </Text>
          <Text color={isFailed ? 'red' : undefined} dimColor={isOver && !isFailed}>{extra}</Text>
        </Text>
      )
    }

    return (
      <Box flexDirection="column">
        {showEmpty && ordered.length === 0 && jobs.length === 0 && <Text dimColor>No agents running.</Text>}
        {ordered.map(agent => {
          const isOver = agent.ended !== undefined
          const scout = agent.scout
          return [
            row(agent.id, agent.depth, point(agent), agent.type, agent.description,
                age((agent.ended ?? now) - agent.started), agent.ctx, marks(agent), isOver, agent.fails > 1 || agent.status === 'failed'),
            // The Luna scout searching alongside this agent: what it found, and what it passed on.
            scout && row(`${agent.id}-scout`, agent.depth + 1, 'luna-medium', 'luna scout', `found ${scout.found}, gave ${agent.luna}`,
                         '', undefined, scout.running ? 'searching' : 'done', !scout.running || isOver, false),
          ]
        })}
        {jobs.map(job => {
          const isOver = job.ended !== undefined
          const family = job.point?.split('-')[0] ?? 'codex'
          return row(job.id, 0, job.point, `${family} job`, job.label, age((job.ended ?? now) - job.started), undefined,
                     isOver ? (job.status === 'completed' ? 'done' : job.status) : 'running', isOver, job.status === 'failed')
        })}
      </Box>
    )
}
