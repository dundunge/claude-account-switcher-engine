const test = require('node:test')
const assert = require('node:assert/strict')
const vm = require('node:vm')
const fs = require('node:fs')
const path = require('node:path')

process.env.TZ = 'America/Los_Angeles'
const observed = Date.UTC(2026, 9, 25, 13, 52)

function context (language = 'zh-CN') {
  const clock = { now: observed }
  class ClockDate extends Date { static now () { return clock.now } }
  const document = { documentElement: { classList: { toggle () {} } }, getElementById: () => null }
  const scope = vm.createContext({ window: { CASE_LOCALE: language, api: { platform: 'darwin', onLanguageChanged () {} } }, document, Date: ClockDate, Intl })
  for (const file of ['i18n.js', 'common.js']) vm.runInContext(fs.readFileSync(path.join(__dirname, '../ui', file), 'utf8'), scope)
  vm.runInContext("h = (tag, props, ...children) => ({ tag, props, children: children.flat(Infinity).filter(c => c !== null && c !== undefined && c !== false) })", scope)
  return { scope, clock, run: code => vm.runInContext(code, scope) }
}
function text (node) { return typeof node === 'object' ? node.children.map(text).join(' ') : String(node) }
const reading = () => ({ at: observed, source: 'claude-cli', fh: 66, sd: 9,
  fhResetAt: Date.UTC(2026, 9, 25, 17, 30), sdResetAt: Date.UTC(2026, 9, 28, 3),
  fhResetEstimated: false, sdResetEstimated: false })

test('compact rows show each limit, reset notice and used percentage with remaining quota in the tooltip', () => {
  const { scope, run } = context()
  scope.u = reading()
  const row = run("quotaWindow('5 小时额度', u, 'fh')")
  assert.match(text(row), /5 小时额度.*3 小时 38 分钟后重置.*66%/)
  assert.match(JSON.stringify(row), /已用 66%，剩余 34%/)
  assert.match(JSON.stringify(row), /2026\/10\/25 10:30.*America\/Los_Angeles.*GMT-7/)
  assert.match(text(run("quotaWindow('每周 · 所有模型', u, 'sd')")), /每周 · 所有模型.*周二.*20:00.*9%/)
})

test('relative native notices stay anchored to observation time as the display clock advances', () => {
  const { scope, clock, run } = context()
  scope.u = { at: observed, fh: 66, fhResetText: 'Resets in 3 hr 38 min' }
  const first = run("quotaResetInfo(u, 'fh').at")
  assert.equal(first, Date.UTC(2026, 9, 25, 17, 30))
  assert.match(run("quotaResetShort(u, 'fh')"), /预计 3 小时 38 分钟/)
  clock.now += 60000
  assert.equal(run("quotaResetInfo(u, 'fh').at"), first)
  assert.match(run("quotaResetShort(u, 'fh')"), /预计 3 小时 37 分钟/)
})

test('missing and failed readings show unavailable instead of 0% or another window reset', () => {
  const { scope, run } = context()
  scope.u = { ...reading(), sd: null }
  assert.equal(run("quotaResetInfo(u, 'sd').at"), null)
  assert.match(text(run("quotaWindow('周额度', u, 'sd')")), /重置时间未获取.*未获取/)
  const empty = text(run("quotaWindow('5 小时额度', null, 'fh')"))
  assert.doesNotMatch(empty, /0%|重置于/)
})

test('elapsed reset notices request a fresh manual query without changing the sampled percentage', () => {
  const { scope, clock, run } = context()
  scope.u = reading()
  clock.now = scope.u.fhResetAt + 60000
  assert.match(run("quotaResetShort(u, 'fh')"), /时间已过.*请查询/)
  assert.match(text(run("quotaWindow('5 小时', u, 'fh')")), /66%/)
})

test('full numeric native dates are accepted but invalid dates and unknown notices are not guessed', () => {
  const { scope, run } = context()
  scope.u = { at: observed, fh: 56, fhResetText: '2026/10/25 10:30:00' }
  assert.equal(run("quotaResetInfo(u, 'fh').at"), Date.UTC(2026, 9, 25, 17, 30))
  for (const raw of ['Resets 2026/10/32 10:30:00', 'Resets whenever', 'Resets in about 2 hr', 'Resets in 6 hr',
    'Resets 2026/11/01 01:30:00', 'Resets 2026/03/08 02:30:00']) {
    scope.u.fhResetText = raw
    assert.equal(run("quotaResetInfo(u, 'fh').at"), null, raw)
  }
})

test('formatting uses the offset at the reset date across daylight saving time', () => {
  const { scope, run } = context('en')
  scope.u = { at: observed, sd: 9, sdResetAt: Date.UTC(2026, 10, 3, 4) }
  assert.match(run("quotaResetLabel(u, 'sd')"), /2026\/11\/02 20:00.*America\/Los_Angeles.*GMT-8/)
  assert.match(run("quotaResetShort(u, 'sd')"), /Resets Mon.*20:00/)
})

test('native compact clock and weekday notices resolve to independent full dates', () => {
  const { scope, run } = context()
  scope.u = { at: observed, fh: 66, sd: 9, fhResetText: 'Resets 10:30am', sdResetText: 'Resets Tue 8:00 PM' }
  assert.equal(run("quotaResetInfo(u, 'fh').at"), Date.UTC(2026, 9, 25, 17, 30))
  assert.equal(run("quotaResetInfo(u, 'sd').at"), Date.UTC(2026, 9, 28, 3))
  scope.u.fhResetText = 'Resets 10:30am (Unknown/Zone)'
  assert.equal(run("quotaResetInfo(u, 'fh').at"), null)
  scope.u.at = Date.UTC(2026, 10, 1, 7, 30)
  scope.u.fhResetText = 'Resets 1:30am'
  assert.equal(run("quotaResetInfo(u, 'fh').at"), null)
})

test('estimated timestamps remain labelled in both languages and participate in repaint detection', () => {
  for (const language of ['zh-CN', 'en']) {
    const { scope, run } = context(language)
    scope.u = reading()
    const before = run('usageDisplayKey(u)')
    scope.u.fhResetEstimated = true
    assert.notEqual(run('usageDisplayKey(u)'), before)
    assert.match(run("quotaResetLabel(u, 'fh')"), language === 'en' ? /Estimated reset/ : /预计重置/)
    assert.match(run("quotaResetShort(u, 'fh')"), language === 'en' ? /Est\. reset in/ : /预计/)
  }
})
