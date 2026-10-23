const test = require('node:test')
const assert = require('node:assert/strict')
const vm = require('node:vm')
const fs = require('node:fs')
const path = require('node:path')

test('language switching updates interface metadata without scanning user content', () => {
  const attributes = {}
  const nav = { setAttribute: (name, value) => { attributes[name] = value } }
  const document = { documentElement: {}, getElementById: id => id === 'nav' ? nav : null }
  const context = vm.createContext({ window: { CASE_LOCALE: 'en' }, document })
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../ui/i18n.js'), 'utf8'), context)
  assert.equal(document.documentElement.lang, 'en')
  assert.equal(document.title, 'CASE Settings')
  assert.equal(attributes['aria-label'], 'Settings sections')
  assert.equal(vm.runInContext("tf('已用 {0}%', '{0}% used', 0)", context), '0% used')
  const name = '中文 Work {0} <name>'
  context.userName = name
  assert.equal(vm.runInContext("tf('账号 {0}', 'Account {0}', userName)", context), `Account ${name}`)
  vm.runInContext("I18N.set('zh-CN')", context)
  assert.equal(document.documentElement.lang, 'zh-CN')
  assert.equal(document.title, 'CASE 设置')
  assert.equal(vm.runInContext("tf('账号 {0}', 'Account {0}', userName)", context), `账号 ${name}`)
})
