const test = require('node:test')
const assert = require('node:assert/strict')
const vm = require('node:vm')
const fs = require('node:fs')
const path = require('node:path')

function context (language) {
  const document = { documentElement: { classList: { toggle () {} } }, getElementById: () => null }
  const scope = vm.createContext({ window: { CASE_LOCALE: language, api: { platform: 'darwin', onLanguageChanged () {} } }, document })
  for (const file of ['i18n.js', 'common.js']) vm.runInContext(fs.readFileSync(path.join(__dirname, '../ui', file), 'utf8'), scope)
  return scope
}

test('native panel observations show their source and retain stale status in both languages', () => {
  for (const language of ['zh-CN', 'en']) {
    const scope = context(language)
    scope.observation = { at: Date.now() - 3600000, age: 3600000, fh: 61, sd: 89, source: 'claude-ui' }
    const label = vm.runInContext('usageAge(observation)', scope)
    assert.match(label, /Claude/)
    assert.match(label, language === 'en' ? /stale/ : /旧数据/)
    scope.observation.source = 'local'
    assert.doesNotMatch(vm.runInContext('usageAge(observation)', scope), /Claude/)
  }
})

test('status repaint tracks changed reset notices without replacing the observation time', () => {
  const scope = context('en')
  scope.observation = { at: 123, age: 1000, fh: 61, sd: 89, source: 'claude-ui', fhResetText: 'Resets in 2 hr' }
  const first = vm.runInContext('usageDisplayKey(observation)', scope)
  scope.observation.fhResetText = 'Resets in 1 hr'
  assert.notEqual(vm.runInContext('usageDisplayKey(observation)', scope), first)
  assert.equal(scope.observation.at, 123)
})
