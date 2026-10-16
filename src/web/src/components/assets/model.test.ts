import { describe, expect, it } from 'vitest'
import { assetTitle, relationFor, relationText, describeAsset, chooseAgent, revealDetails } from './model'

const agent: any = { name: 'reader', availability: 'ready', relations: { 'knowledge:a': 'conditional', 'skill:x': 'required', 'skill:y': 'denied' } }
const labels: any = { unmentioned: { label: '未确认' }, denied: { label: '明确限制' } }
describe('explicit content source', () => {
  it('does not interpret inherited object keys as explicit relations', () => {
    for (const id of ['constructor', 'toString', '__proto__']) {
      expect(relationFor({ ...agent, relations: {} }, { id, availability: 'ready' } as any)).toBe('unmentioned')
    }
  })
  it('does not replace updated descriptions or titles with built-in mappings', () => {
    expect(describeAsset({ name: 'extract-constraints', kind: 'skill', description: '新版用途说明' } as any)).toBe('新版用途说明')
    expect(assetTitle({ name: 'extract-constraints', title: '新版标题' } as any)).toBe('新版标题')
    expect(describeAsset({ name: 'dimensions', module: 'dimensions', kind: 'knowledge', description: '新版知识说明' } as any)).toBe('新版知识说明')
  })
  it('distinguishes unmentioned capabilities from explicit denial using content labels', () => {
    expect(relationFor(agent, { id: 'skill:other', availability: 'ready' } as any)).toBe('unmentioned')
    expect(relationFor(agent, { id: 'skill:y', availability: 'ready' } as any)).toBe('denied')
    expect(relationText('unmentioned', labels)).toBe('未确认')
    expect(relationText('denied', labels)).toBe('明确限制')
  })
  it('shows each knowledge family its own relations without inventing relationships', () => {
    const item: any = { id: 'knowledge:a', kind: 'knowledge', family: 'third', availability: 'ready' }
    expect(relationFor(agent, item)).toBe('conditional')
    expect(relationFor({ ...agent, relations: {} }, item)).toBe('unmentioned')
  })
  it('does not highlight broken assets or unavailable definitions as usable', () => {
    expect(relationFor(agent, { id: 'skill:x', availability: 'unavailable' } as any)).toBe('unavailable')
    expect(relationFor({ ...agent, availability: 'missing' }, { id: 'skill:x', availability: 'ready' } as any)).toBe('unmentioned')
  })
  it('uses the configured default and handles missing selections', () => {
    const list: any = [{ name: 'first', availability: 'ready' }, { name: 'preferred', availability: 'ready' }]
    expect(chooseAgent(list, 'preferred')).toBe('preferred')
    expect(chooseAgent(list, 'missing')).toBe('first')
    expect(chooseAgent([], 'preferred')).toBe('')
  })
  it('reveals clicked details on narrow screens without moving the desktop page', () => {
    const calls: string[] = []
    const panel = { scrollIntoView: () => calls.push('scroll'), focus: () => calls.push('focus') } as unknown as HTMLElement
    revealDetails(panel, 390)
    expect(calls).toEqual(['scroll', 'focus'])
    calls.length = 0
    revealDetails(panel, 1400)
    expect(calls).toEqual([])
  })
})
