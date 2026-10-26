const test = require('node:test')
const assert = require('node:assert/strict')
const vm = require('node:vm')
const fs = require('node:fs')
const path = require('node:path')

const source = file => fs.readFileSync(path.join(__dirname, '..', file), 'utf8')
const launcher = source('ui/launcher.js')
const tree = (tag, props, ...children) => ({ tag, props, children: children.flat(Infinity).filter(child => child != null && child !== false) })
const nodes = node => node && typeof node === 'object' ? [node, ...node.children.flatMap(nodes)] : []
const text = node => node && typeof node === 'object' ? node.children.map(text).join(' ') : String(node)
const tick = () => new Promise(resolve => setImmediate(resolve))
function account (id, idx = 0) {
  const usage = { at: Date.now(), source: 'claude-cli', fh: 21, sd: 45 }
  return { id, idx, name: id, usage, monitor: { configured: true, version: 1, status: 'success', usage } }
}
function harness (accounts = [account('one'), account('two', 1), account('three', 2)]) {
  const calls = []
  const handlers = {}
  const document = { documentElement: { classList: { toggle () {} } }, getElementById: () => null, addEventListener: (event, cb) => { handlers[event] = cb } }
  const api = { platform: 'darwin', onLanguageChanged () {}, openSettings: section => calls.push(['settings', section]) }
  const scope = vm.createContext({ window: { CASE_LOCALE: 'zh-CN', api }, document, accounts, calls })
  for (const file of ['ui/i18n.js', 'ui/common.js', 'ui/quota.js']) vm.runInContext(source(file), scope)
  Object.assign(scope, { h: tree, icon: () => null, avatar: () => null, toast: (...args) => calls.push(['toast', ...args]) })
  vm.runInContext('controller = createQuotaController({ getAccounts: () => accounts, onChange () {} })', scope)
  return { scope, api, accounts, calls, handlers, run: code => vm.runInContext(code, scope) }
}
function loadRow (h) {
  Object.assign(h.scope, {
    S: { accounts: h.accounts, sel: 0, view: 'list', panel: null }, quota: h.run('controller'),
    launching: false, paintSel () {}, subtitle: () => '',
    launch: account => h.calls.push(['launch', account.id]), openPanel: () => h.calls.push(['panel'])
  })
  h.run(launcher.slice(launcher.indexOf('function row ('), launcher.indexOf('function paintSel (')))
}
const event = () => ({ stopPropagation () {}, preventDefault () {} })

test('clicking or double-clicking an account row only selects; the fixed Switch button launches that account once', async () => {
  const h = harness()
  loadRow(h)
  const row = h.run('row(accounts[1], 1)')
  row.props.onclick(event())
  row.props.onclick(event())
  assert.equal(h.scope.S.sel, 1)
  assert.deepEqual(h.calls, [])
  const button = nodes(row).find(node => node.props?.dataset?.action === 'switch')
  assert.equal(text(button), '切换')
  await button.props.onclick(event())
  assert.deepEqual(h.calls, [['launch', 'two']])
  for (const selected of [0, 1, 2]) {
    h.scope.S.sel = selected
    for (let i = 0; i < 3; i++) {
      h.scope.index = i
      assert.equal(nodes(h.run('row(accounts[index], index)')).filter(node => node.props?.dataset?.action === 'switch').length, 1)
    }
  }
})

test('the adjacent Refresh button queries only its own account and never switches or authorizes', async () => {
  const h = harness()
  h.api.refreshUsage = async id => {
    h.calls.push(['query', id])
    const item = h.accounts.find(account => account.id === id)
    return { ok: true, status: 'success', usage: item.monitor.usage, monitor: { ...item.monitor, version: 2 } }
  }
  loadRow(h)
  const row = h.run('row(accounts[2], 2)')
  await nodes(row).find(node => node.props?.dataset?.action === 'refresh').props.onclick(event())
  assert.deepEqual(h.calls, [['query', 'three']])
  assert.equal(h.accounts[0].monitor.version, 1)
  assert.equal(h.accounts[1].monitor.version, 1)
  assert.equal(h.accounts[2].monitor.version, 2)
})

test('home Refresh usage runs the bulk query directly without opening a second page', async () => {
  const h = harness()
  const rendered = []
  Object.assign(h.scope, {
    S: { view: 'list', accounts: h.accounts },
    quota: { busy: false, refreshingAll: false, refreshAll: () => h.calls.push(['query-all']) },
    $lead: { replaceChildren () {} }, $tools: { replaceChildren: (...children) => rendered.push(...children) },
    healthChip: () => null
  })
  h.run(launcher.slice(launcher.indexOf('function renderBar ('), launcher.indexOf('function healthChip (')))
  h.run('renderBar()')
  const button = rendered.find(node => node?.tag === 'button' && text(node).includes('刷新全部'))
  assert.ok(button)
  await button.props.onclick()
  assert.deepEqual(h.calls, [['query-all']])
})

test('Enter on Refresh, Switch, or other focused buttons is handled by the button instead of launching a selected account', () => {
  const h = harness()
  loadRow(h)
  h.scope.filtered = () => h.accounts
  h.run(launcher.slice(launcher.indexOf("document.addEventListener('keydown'"), launcher.indexOf('// ---------------------------------------------------------------- start ---')))
  for (const action of ['refresh', 'switch', 'settings']) {
    h.handlers.keydown({ ...event(), key: 'Enter', target: { closest: () => ({ action }) } })
  }
  assert.deepEqual(h.calls, [])
  h.handlers.keydown({ ...event(), key: 'Enter', target: { closest: () => null } })
  assert.deepEqual(h.calls, [['launch', 'one']])
})

test('bulk refresh queries a stable set serially, refuses a repeated click, and keeps failed usage empty', async () => {
  const h = harness()
  const releases = new Map()
  h.api.refreshUsage = id => {
    h.calls.push(['query', id])
    return new Promise(resolve => releases.set(id, resolve))
  }
  const pending = h.run('controller.refreshAll()')
  assert.equal(h.run('controller.refreshingAll'), true)
  assert.deepEqual(h.calls, [['query', 'one']])
  assert.equal(h.accounts[0].usage, null)
  await h.run('controller.refreshAll()')
  h.accounts.reverse()
  releases.get('one')({ ok: true, status: 'success', usage: { at: Date.now(), fh: 10, sd: 20 }, monitor: { configured: true, version: 2, status: 'success' } })
  await tick()
  assert.deepEqual(h.calls, [['query', 'one'], ['query', 'two']])
  assert.equal(h.accounts.find(account => account.id === 'two').usage, null)
  releases.get('two')({ ok: false, status: 'unavailable', code: 'CLI_USAGE_UNAVAILABLE', usage: { fh: 99 }, monitor: { configured: true, version: 2, status: 'unavailable', code: 'CLI_USAGE_UNAVAILABLE' } })
  await tick()
  assert.deepEqual(h.calls, [['query', 'one'], ['query', 'two'], ['query', 'three']])
  releases.get('three')({ ok: true, status: 'success', usage: { at: Date.now(), fh: 30, sd: 40 }, monitor: { configured: true, version: 2, status: 'success' } })
  const result = await pending
  assert.equal(result.succeeded, 2)
  assert.equal(result.unavailable, 1)
  assert.equal(h.run('controller.refreshingAll'), false)
  assert.equal(h.accounts.find(account => account.id === 'two').usage, null)
  assert.match(h.calls.at(-1)[1], /2 个成功，1 个未获取/)
})

test('transport failures in a bulk query do not stop the remaining accounts or relabel every failure as timeout', async () => {
  const h = harness()
  h.api.refreshUsage = async id => {
    h.calls.push(['query', id])
    if (id === 'one') throw new Error('bridge unavailable')
    if (id === 'two') throw Object.assign(new Error('timeout'), { code: 'TIMEOUT' })
    return { ok: true, status: 'success', usage: { fh: 1, sd: 2 }, monitor: { configured: true, version: 2, status: 'success' } }
  }
  const result = await h.run('controller.refreshAll()')
  assert.equal(result.succeeded, 1)
  assert.equal(result.unavailable, 2)
  assert.equal(h.run('controller.state(accounts[0]).status'), 'failed')
  assert.equal(h.run('controller.state(accounts[1]).status'), 'timeout')
  assert.equal(h.accounts[0].usage, null)
  assert.equal(h.accounts[1].usage, null)
  assert.equal(h.accounts[2].usage.fh, 1)
})

test('a failure without a backend snapshot remains empty when another window polls an older successful value', async () => {
  const h = harness()
  const old = structuredClone(h.accounts[0])
  h.api.refreshUsage = async () => ({ ok: false, status: 'failed', code: 'ACCOUNT_NOT_FOUND', usage: null })
  await h.run('controller.refresh("one")')
  h.scope.old = old
  const merged = h.run('mergeQuotaStatus(accounts[0], old, controller.requests.get("one"))')
  assert.equal(merged.usage, null)
  assert.equal(h.run('controller.state(accounts[0]).status'), 'failed')
})

test('a rejected panel-lock attempt clears its reading and remains retryable, not falsely querying', async () => {
  const h = harness()
  h.api.refreshUsage = async () => ({ ok: false, status: 'unavailable', code: 'REFRESH_IN_PROGRESS', usage: null, monitor: { configured: false, version: 2, status: 'unavailable', code: 'REFRESH_IN_PROGRESS', usage: null } })
  const result = await h.run('controller.refresh("one")')
  assert.equal(result.status, 'unavailable')
  assert.equal(result.pending, false)
  assert.equal(h.accounts[0].usage, null)
  h.api.refreshUsage = async () => ({ ok: true, status: 'success', usage: { fh: 7, sd: 8 }, monitor: { configured: false, version: 3, status: 'success' } })
  await h.run('controller.refresh("one")')
  assert.equal(h.accounts[0].usage.fh, 7)
})

test('Refresh all waits for another window’s existing query before counting it or querying the next account', async () => {
  const h = harness()
  let release
  h.api.refreshUsage = async id => {
    h.calls.push(['query', id])
    if (id === 'one') return { ok: false, status: 'in_progress', usage: null, monitor: { configured: true, version: 2, status: 'in_progress' } }
    return { ok: true, status: 'success', usage: { fh: 10, sd: 20 }, monitor: { configured: true, version: 3, status: 'success' } }
  }
  h.api.status = () => new Promise(resolve => { release = resolve })
  const running = h.run('controller.refreshAll()')
  await tick()
  assert.deepEqual(h.calls, [['query', 'one']])
  release([{ id: 'one', usage: { fh: 12, sd: 34 }, monitor: { configured: true, version: 3, status: 'success' } }])
  const result = await running
  assert.equal(result.succeeded, 3)
  assert.equal(result.unavailable, 0)
  assert.equal(result.pending, 0)
  assert.deepEqual(h.calls.filter(([command]) => command === 'query'), [['query', 'one'], ['query', 'two'], ['query', 'three']])
})

test('an unavailable local poll does not count another window’s running query as a failure', async () => {
  const h = harness([account('one')])
  h.api.refreshUsage = async () => ({ ok: false, status: 'in_progress', usage: null, monitor: { configured: true, version: 2, status: 'in_progress' } })
  h.api.status = async () => { throw new Error('status connection unavailable') }
  const result = await h.run('controller.refreshAll()')
  assert.equal(result.succeeded, 0)
  assert.equal(result.unavailable, 0)
  assert.equal(result.pending, 1)
  assert.match(h.calls.at(-1)[1], /1 个仍在查询/)
})
