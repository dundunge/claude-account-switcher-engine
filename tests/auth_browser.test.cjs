'use strict'
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const { EventEmitter } = require('node:events')
const source = fs.readFileSync(path.join(__dirname, '../tools/claude_auth_browser.cjs'), 'utf8')
const url = 'https://claude.com/cai/oauth/authorize?redirect_uri=http%3A%2F%2Flocalhost%3A12345%2Fcallback&state=synthetic'

async function browser (profile = '/synthetic/second/browser', startUrl = url) {
  const app = new EventEmitter()
  const state = { paths: {}, windows: [], output: '', opened: [], exits: [], mkdirs: [] }
  Object.assign(app, {
    setName (name) { state.name = name },
    setPath (key, value) { state.paths[key] = value },
    requestSingleInstanceLock () { return true },
    async whenReady () { state.pathsAtReady = { ...state.paths } },
    quit () { state.quit = true }, exit (code) { state.exits.push(code) }
  })
  const session = { setPermissionRequestHandler (fn) { state.permission = fn }, setPermissionCheckHandler (fn) { state.check = fn } }
  class BrowserWindow extends EventEmitter {
    constructor (options) {
      super()
      this.options = options
      this.webContents = new EventEmitter()
      this.webContents.setWindowOpenHandler = fn => { this.popup = fn }
      app.emit('web-contents-created', {}, this.webContents)
      state.windows.push(this)
    }
    setMenu () {}
    async loadURL (value) { state.opened.push(value) }
    show () {}
    focus () {}
  }
  const input = JSON.stringify({ url: startUrl, userData: profile, label: '第二个账号' })
  const context = vm.createContext({ URL, process: { stdout: { write (value) { state.output += value } } },
    require (name) {
      if (name === 'electron') return { app, BrowserWindow, session: { fromPartition (partition) { state.partition = partition; return session } } }
      if (name === 'node:path') return path
      if (name === 'node:fs') return {
        readFileSync: () => input, realpathSync: value => value, existsSync: () => false,
        mkdirSync (value, options) { state.mkdirs.push({ value, options }) }
      }
      throw new Error(`Unexpected module ${name}`)
    }
  })
  vm.runInContext(source, context)
  await new Promise(resolve => setImmediate(resolve))
  return state
}

test('each account sets its isolated browser and session data before Electron becomes ready', async () => {
  const accounts = await Promise.all(['first', 'second', 'third'].map(id => browser(`/synthetic/${id}/browser`)))
  assert.equal(new Set(accounts.map(state => state.pathsAtReady.sessionData)).size, 3)
  for (const state of accounts) {
    assert.equal(state.paths.userData, state.paths.sessionData)
    assert.match(state.windows[0].options.title, /第二个账号/)
    assert.equal(state.output, 'CASE_AUTH_READY\n')
    assert.equal(state.windows[0].options.webPreferences.nodeIntegration, false)
    assert.equal(state.windows[0].options.webPreferences.sandbox, true)
    assert.equal(state.windows[0].options.webPreferences.contextIsolation, true)
    assert.equal(state.windows[0].options.webPreferences.webSecurity, true)
  }
})

test('OAuth popups stay in the same account session and never invoke an external browser', async () => {
  const state = await browser()
  const win = state.windows[0]
  for (const target of ['https://accounts.google.com/signin', 'https://appleid.apple.com/auth', 'about:blank']) {
    const result = win.popup({ url: target })
    assert.equal(result.action, 'allow')
    assert.equal(result.overrideBrowserWindowOptions.webPreferences.session, win.options.webPreferences.session)
  }
  for (const target of ['file:///tmp/login', 'javascript:alert(1)', 'claude://login', 'http://localhost:9999/callback']) {
    assert.equal(win.popup({ url: target }).action, 'deny')
  }
})

test('navigation permits only HTTPS and the exact official CLI callback endpoint', async () => {
  const state = await browser()
  const contents = state.windows[0].webContents
  for (const event of ['will-navigate', 'will-redirect']) {
    for (const [target, allowed] of [['https://claude.ai/login', true], ['http://localhost:12345/callback?code=synthetic', true],
      ['http://localhost:12345/other', false], ['http://localhost:9999/callback', false], ['file:///etc/passwd', false]]) {
      let blocked = false
      contents.emit(event, { preventDefault () { blocked = true } }, target)
      assert.equal(blocked, !allowed)
    }
  }
  let allowed
  state.permission(contents, 'media', value => { allowed = value })
  assert.equal(allowed, false)
  assert.equal(state.check(), false)
})

test('an unrecognized authorization origin fails before opening any browser window', async () => {
  const state = await browser('/synthetic/second/browser', url.replace('claude.com', 'claude.com.example.org'))
  assert.deepEqual(state.exits, [1])
  assert.equal(state.windows.length, 0)
})
