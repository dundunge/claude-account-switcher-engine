const test = require('node:test')
const assert = require('node:assert/strict')
const vm = require('node:vm')
const fs = require('node:fs')
const path = require('node:path')

const source = file => fs.readFileSync(path.join(__dirname, '..', file), 'utf8')
function context () {
  const document = { documentElement: { classList: { toggle () {} } }, getElementById: () => null }
  const scope = vm.createContext({ window: { CASE_LOCALE: 'zh-CN', api: { platform: 'darwin', onLanguageChanged () {} } }, document })
  for (const file of ['ui/i18n.js', 'ui/common.js']) vm.runInContext(source(file), scope)
  return scope
}
const reading = { at: 1000, fh: 21, sd: 45, source: 'claude-cli' }
function fold (previous, incoming, request) {
  const scope = context()
  Object.assign(scope, { previous, incoming, request })
  return vm.runInContext('mergeQuotaStatus(previous, incoming, request)', scope)
}

test('late status response cannot restore percentages cleared by a failed query', () => {
  const previous = { usage: null, monitor: { version: 3, status: 'timeout', lastAttemptAt: 3000 } }
  const next = fold(previous, { usage: reading, running: false, monitor: { version: 2, status: 'success', lastAttemptAt: 1000 } })
  assert.equal(next.usage, null)
  assert.equal(next.monitor.status, 'timeout')
  assert.equal(next.running, false)
})

test('transport failures stay empty until a newer backend attempt exists', () => {
  const previous = { usage: null, monitor: { version: 2, status: 'success', lastAttemptAt: 1000 } }
  const request = { bridgeFailed: true, startedAt: 2000 }
  assert.equal(fold(previous, { usage: reading, monitor: previous.monitor }, request).usage, null)
  const fresh = { usage: reading, monitor: { version: 3, status: 'success', lastAttemptAt: 2001 } }
  assert.equal(fold(previous, fresh, request).usage.fh, 21)
})

test('every unsuccessful state refuses a cached value even if a malformed payload includes one', () => {
  for (const status of ['auth_failed', 'unavailable', 'in_progress', 'failed', 'timeout']) {
    const next = fold({}, { usage: reading, monitor: { version: 3, status } })
    assert.equal(next.usage, null, status)
  }
})

test('pending user query and account list refresh do not restore an old value', () => {
  const previous = { usage: reading, monitor: { version: 2, status: 'success' } }
  assert.equal(fold(previous, { name: 'three' }, { pending: true }).usage, null)
})

test('official CLI has a distinct source label in Chinese and English', () => {
  const scope = context()
  scope.reading = reading
  assert.match(vm.runInContext('usageAge(reading)', scope), /CLI/)
})

test('UI timers only poll local state; optional scheduling belongs to the backend', () => {
  const main = source('src/main.rs')
  const watch = main.slice(main.indexOf('fn start_watching'), main.indexOf('// -------------------------------------------------------------------- main'))
  assert.doesNotMatch(watch, /usage_monitor|usage_refresh/)
  assert.match(main, /usage_schedule::start\(\)/)
  const pages = source('ui/launcher.js') + source('ui/settings.js')
  const intervals = [...pages.matchAll(/setInterval\(\(\) => \{([\s\S]*?)\}, \d+\)/g)]
  assert.ok(intervals.length > 0)
  for (const match of intervals) assert.doesNotMatch(match[1], /refreshUsage|refreshQuota|connectUsageMonitor|setupUsageMonitor|quota\.(refresh|refreshAll|configure)/)
})

test('opening a failed account CLI uses its existing login and preserves the query failure', async () => {
  const scope = context()
  const calls = []
  const account = { id: 'one', usage: null, monitor: { version: 1, configured: true, status: 'unavailable', code: 'CLI_USAGE_UNAVAILABLE' } }
  Object.assign(scope, {
    toast () {}
  })
  Object.assign(scope.window.api, {
    async setupUsageMonitor (id) { calls.push(['setup', id]); return { ok: true, monitor: account.monitor } },
    async connectUsageMonitor (id) { calls.push(['auth', id]); throw new Error('unexpected authorization') }
  })
  vm.runInContext(source('ui/quota.js'), scope)
  scope.accounts = [account]
  vm.runInContext('controller = createQuotaController({ getAccounts: () => accounts, onChange () {} })', scope)
  await vm.runInContext('controller.configure("one", true)', scope)
  assert.deepEqual(calls, [['setup', 'one']])
  assert.equal(vm.runInContext('controller.requests.size', scope), 0)
  assert.equal(account.usage, null)
  assert.equal(account.monitor.code, 'CLI_USAGE_UNAVAILABLE')
})

test('each configured account offers CLI opening separately from authorization in every query state', async () => {
  const scope = context()
  const calls = []
  Object.assign(scope, {
    h: (tag, props, ...children) => ({ tag, props, children: children.flat(Infinity) }),
    quotaWindow: () => null,
    toast () {}
  })
  vm.runInContext(source('ui/quota.js'), scope)
  vm.runInContext('controller = createQuotaController({ getAccounts: () => [account], onChange () {} })', scope)
  scope.window.api.setupUsageMonitor = id => { calls.push([id, true]); return Promise.resolve({ ok: false }) }
  scope.window.api.connectUsageMonitor = id => { calls.push([id]); return Promise.resolve({ ok: false }) }
  const buttons = tree => tree && typeof tree === 'object'
    ? [tree.tag === 'button' ? tree : null, ...tree.children.flatMap(buttons)].filter(Boolean)
    : []
  for (const id of ['one', 'two', 'three']) {
    for (const status of ['success', 'auth_failed', 'unavailable', 'failed', 'timeout']) {
      scope.account = { id, name: id, usage: null, monitor: { configured: true, status } }
      const actions = buttons(vm.runInContext('controller.card(account, true)', scope))
      const open = actions.find(button => button.children.includes('打开独立 CLI'))
      const auth = actions.find(button => button.children.includes('重新授权'))
      assert.ok(open, `${id}: ${status}`)
      assert.ok(auth, `${id}: ${status}`)
      assert.equal(open.props.disabled, false)
      await open.props.onclick()
      assert.deepEqual(calls.pop(), [id, true])
      await auth.props.onclick()
      assert.deepEqual(calls.pop(), [id])
    }
  }
  scope.account = { id: 'new', monitor: { configured: false } }
  const actions = buttons(vm.runInContext('controller.card(account, true)', scope))
  assert.equal(actions.some(button => button.children.includes('打开独立 CLI')), false)
  assert.equal(actions.some(button => button.children.includes('授权独立查询')), true)
})
