const test = require('node:test')
const assert = require('node:assert/strict')
const vm = require('node:vm')
const fs = require('node:fs')
const path = require('node:path')
const source = file => fs.readFileSync(path.join(__dirname, '..', file), 'utf8')

function harness () {
  const calls = []
  const document = { documentElement: { classList: { toggle () {} } }, getElementById: () => null }
  const scope = vm.createContext({ window: { CASE_LOCALE: 'zh-CN', api: { platform: 'darwin', onLanguageChanged () {} } }, document })
  for (const file of ['ui/i18n.js', 'ui/common.js', 'ui/quota.js']) vm.runInContext(source(file), scope)
  const tree = (tag, props = {}, ...children) => {
    const node = { tag, props, children: children.flat(Infinity).filter(x => x != null), value: props.value }
    if (tag === 'select') node.value = node.children.find(x => x.props.selected)?.props.value || node.children[0].props.value
    return node
  }
  Object.assign(scope, { h: tree, toggle: (label, enabled, onchange, options) => ({ tag: 'switch', props: { label, enabled, onchange, ...options }, children: [] }), toast: (...args) => calls.push(['toast', ...args]) })
  const run = code => vm.runInContext(code, scope)
  const card = (schedule = { enabled: false, intervalMinutes: 120 }, saving = false) => {
    scope.schedule = schedule
    scope.saving = saving
    scope.save = async (enabled, minutes) => { calls.push(['save', enabled, minutes]); return { enabled, intervalMinutes: minutes } }
    return run('quotaScheduleCard(schedule, saving, save)')
  }
  return { scope, run, card, calls }
}
const nodes = tree => tree && typeof tree === 'object' ? [tree, ...tree.children.flatMap(nodes)] : []
const event = () => ({ preventDefault () {} })

test('custom intervals convert hours/minutes and reject invalid or excessive request frequencies', () => {
  const h = harness()
  for (const [value, unit, result] of [['2', 'hours', 120], ['5', 'hours', 300], ['1.5', 'hours', 90], ['95', 'minutes', 95], ['168', 'hours', 10080], ['5', 'minutes', 5], ['', 'hours', null], ['Infinity', 'hours', null], ['-1', 'hours', null], ['4', 'minutes', null], ['5.5', 'minutes', null], ['169', 'hours', null], ['2', 'seconds', null]]) {
    Object.assign(h.scope, { value, unit })
    assert.equal(h.run('quotaScheduleMinutes(value, unit)'), result, `${value} ${unit}`)
  }
})

test('saving a custom interval preserves the off state and enabling uses the entered interval', async () => {
  const h = harness()
  const all = nodes(h.card())
  all.find(x => x.tag === 'input').value = '95'
  all.find(x => x.tag === 'select').value = 'minutes'
  await all.find(x => x.tag === 'form').props.onsubmit(event())
  assert.deepEqual(h.calls, [['save', false, 95]])
  assert.equal(await all.find(x => x.tag === 'switch').props.onchange(true), true)
  assert.deepEqual(h.calls.at(-1), ['save', true, 95])
})

test('an invalid draft cannot enable queries but cannot prevent disabling the timer', async () => {
  const h = harness()
  const all = nodes(h.card({ enabled: true, intervalMinutes: 300 }))
  all.find(x => x.tag === 'input').value = '0'
  await all.find(x => x.tag === 'form').props.onsubmit(event())
  assert.equal(h.calls.some(x => x[0] === 'save'), false)
  assert.equal(await all.find(x => x.tag === 'switch').props.onchange(false), false)
  assert.deepEqual(h.calls.at(-1), ['save', false, 300])
})

test('quick intervals preserve enabled state and a failed save restores the existing toggle', async () => {
  const h = harness()
  const all = nodes(h.card({ enabled: false, intervalMinutes: 95 }))
  for (const minutes of [120, 300]) {
    const label = `${minutes / 60} 小时`
    await all.find(x => x.tag === 'button' && x.children.includes(label)).props.onclick()
    assert.deepEqual(h.calls.at(-1), ['save', false, minutes])
  }
  h.scope.save = async () => ({ enabled: false, intervalMinutes: 95 })
  const failed = nodes(h.run('quotaScheduleCard(schedule, false, save)'))
  assert.equal(await failed.find(x => x.tag === 'switch').props.onchange(true), false)
})

test('schedule status distinguishes disabled, running, persistence failure and skipped accounts in both languages', () => {
  const h = harness()
  for (const language of ['zh-CN', 'en']) {
    h.run(`I18N.language = '${language}'`)
    h.scope.schedule = { enabled: false, running: false }
    assert.match(h.run('quotaScheduleStatus(schedule)'), language === 'en' ? /off/ : /已关闭/)
    h.scope.schedule = { enabled: true, intervalMinutes: 95, nextAt: 1800000000000, running: true }
    assert.match(h.run('quotaScheduleStatus(schedule)'), language === 'en' ? /95 minutes.*running/ : /95 分钟.*正在/)
    h.scope.schedule.code = 'USAGE_SCHEDULE_SAVE_FAILED'
    assert.match(h.run('quotaScheduleStatus(schedule)'), language === 'en' ? /could not be saved/ : /无法保存/)
    h.scope.schedule.lastRun = { succeeded: 1, failed: 1, skipped: 1, interrupted: true }
    assert.match(h.run('quotaScheduleLastRun(schedule)'), language === 'en' ? /1 skipped.*Settings changed/ : /1 个跳过.*设置已变更/)
  }
})
