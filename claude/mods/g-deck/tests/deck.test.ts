import { expect, test } from 'claude-code/testing'

import { age, ctxColor, kilo, marks, order, point } from '../hooks/deck'
import { tick } from '../hooks/register'
import type { Agent } from '../types'

const agent = (over: Partial<Agent>): Agent => ({
  id: 'a', type: 'general-purpose', description: '', model: 'claude-opus-5-5', started: 0,
  status: 'running', steps: 0, fails: 0, luna: 0, ...over,
})

test('the point comes from the agent name, else its model and effort', () => {
  expect(point(agent({ type: 'sonnet-medium-worker' }))).toBe('sonnet-medium')
  expect(point(agent({ model: 'claude-opus-5-5', effort: 'high' }))).toBe('opus-high')
  expect(point(agent({ model: 'claude-sonnet-5-5' }))).toBe('sonnet-medium')
})

test('marks show turns, failures, Luna and the end state', () => {
  expect(marks(agent({ steps: 9, fails: 2, luna: 1, lastTool: 'Bash' }))).toBe('t9 ✗2 Bash')
  expect(marks(agent({ steps: 3, ended: 5, status: 'completed' }))).toBe('t3 done')
})

test('children sit under their parent, orphans at the top', () => {
  const all = {
    a: agent({ id: 'a', started: 1 }),
    b: agent({ id: 'b', parentId: 'a', started: 2 }),
    c: agent({ id: 'c', parentId: 'gone', started: 3 }),
    d: agent({ id: 'd', parentId: 'b', started: 4 }),
  }
  expect(order(all).map(row => `${row.id}${row.depth}`)).toEqual(['a0', 'b1', 'd2', 'c0'])
})

test('ages read in seconds, minutes, hours', () => {
  expect(age(42_000)).toBe('42s')
  expect(age(14 * 60_000)).toBe('14m')
  expect(age(3_900_000)).toBe('1h5m')
})

test('context is blue to 100k, then 50k bands to the 250k compact', () => {
  expect([99_000, 120_000, 180_000, 249_000].map(ctxColor))
    .toEqual(['#6ab0ff', '#4ade80', '#ffd60a', '#ff5347'])
  expect(kilo(165_982)).toBe('166k')
})

test('a finished agent sends one Luna digest even when its ended mark is clobbered', async () => {
  const row = agent({ id: 'w1', type: 'opus-low-worker', description: 'Diagnose', status: 'running' })
  let store: Record<string, unknown> = { agents: { w1: row } }
  const sent: string[] = []
  let now = 1000
  const fake = {
    clock: { now: async () => now },
    agent: { list: async () => [{ id: 'w1', status: 'completed' }] },
    fs: {
      read: async (path: string) => {
        if (path.endsWith('state.json')) return JSON.stringify({ count: 2, delivered: ['p1', 'p2'] })
        throw new Error('missing')
      },
      exists: async () => false,
      list: async () => [],
      stat: async () => ({ mtimeMs: 0 }),
    },
    state: {
      get: async ({ key }: { key: string }) => ({ value: store[key] }),
      set: async ({ key }: { key: string }, value: unknown) => { store = { ...store, [key]: value } },
    },
    session: { append: async (m: { message: { content: { text: string }[] } }) => { sent.push(m.message.content[0].text) } },
  }
  for (let i = 0; i < 17; i++) {
    await tick(fake as never, '/root')
    // A concurrent patch() wrote back the row it read before `ended` landed.
    store = { ...store, agents: { w1: row } }
    now += 3000
  }
  expect(sent.length).toBe(1)
  expect(sent[0]).toContain('ended completed after 2 pointer(s)')
})
