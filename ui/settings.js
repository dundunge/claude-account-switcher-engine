'use strict'

// The settings window: six sections down the side, one shown at a time, plus
// the archived profiles, reached from General.

const SECTIONS = [
  { id: 'general', label: 'General', icon: 'sliders', lede: 'How CASE starts, and how your accounts are listed.' },
  { id: 'shortcut', label: 'Shortcut', icon: 'keyboard', lede: 'Summon the launcher from any app.' },
  { id: 'presence', label: IS_WIN ? 'Notification area' : 'Menu bar & Dock', icon: 'window', lede: 'Choose where CASE lives. One place always stays, so there is a way back in.' },
  { id: 'updates', label: 'Updates', icon: 'download', lede: 'Releases come from GitHub and are checked before they install.' },
  { id: 'data', label: 'Sessions & data', icon: 'shield', lede: 'The safety net for Claude’s session list, and where CASE keeps its files.' },
  { id: 'about', label: 'About', icon: 'info', lede: '' }
]

const T = {
  section: window.CASE_SECTION || 'general',
  settings: null,
  archived: null,
  health: null,
  update: null,     // the last check's answer
  upd: 'idle',      // idle | checking | downloading | restarting
  progress: 0
}

const $nav = document.getElementById('nav')
const $content = document.getElementById('content')

function go (section) {
  T.section = section
  render()
  $content.scrollTop = 0
  if (section === 'updates' && !T.update && T.upd === 'idle') check()
}

function render () {
  if (!T.settings) return
  renderNav()
  const id = T.section
  const s = SECTIONS.find(x => x.id === id)
  const head = id === 'archived'
    ? h('header', { class: 'content-head' },
      h('div', {}, h('button', { type: 'button', class: 'btn link', onclick: () => go('general') }, '‹ General')),
      h('h1', { class: 'h1' }, 'Archived profiles'),
      h('p', {}, 'Set aside, not deleted. Restoring moves the folder back and lists the account again.'))
    : h('header', { class: 'content-head' }, h('h1', { class: 'h1' }, s ? s.label : ''), s && s.lede ? h('p', {}, s.lede) : '')
  $content.replaceChildren(head, ...(VIEWS[id] || VIEWS.general)())
}

function issues () {
  if (!T.health) return 0
  return T.health.accounts.reduce((n, a) => n + a.orphans.length + (a.failures && !a.failures.resolved ? 1 : 0), 0)
}

function renderNav () {
  const current = T.section === 'archived' ? 'general' : T.section
  const badge = id => {
    if (id === 'updates' && T.update && T.update.available) return h('span', { class: 'nav-badge', 'aria-label': 'Update available' }, '1')
    if (id === 'data' && issues()) return h('span', { class: 'nav-badge', 'aria-label': `${issues()} to look at` }, String(issues()))
    return ''
  }
  $nav.replaceChildren(
    ...SECTIONS.map(s => h('button', { type: 'button', class: 'nav-item', 'aria-current': s.id === current ? 'page' : null, onclick: () => go(s.id) },
      icon(s.icon), h('span', {}, s.label), badge(s.id))),
    h('span', { class: 'grow' }),
    h('div', { class: 'version' }, `CASE ${T.settings.version}`))
}

const section = (label, ...kids) => h('section', { class: 'stack', 'aria-label': label }, h('h2', { class: 'section-label' }, label), ...kids)

const VIEWS = {
  general () {
    const s = T.settings
    return [
      section('Startup', h('div', { class: 'card' },
        settingRow('Launch at login', 'Starts hidden, ready for your shortcut.',
          toggle('Launch at login', s.openAtLogin, async on => (await api.setLoginItem(on)).openAtLogin)))),
      section('Account list', h('div', { class: 'card' },
        settingRow('Order', `${combo('1')}–${combo('9')} follow what you see, so recent order moves them too.`,
          segmented('Order', [['yours', 'Your order'], ['recent', 'Most recent first']], s.sortByRecent ? 'recent' : 'yours', async v => {
            s.sortByRecent = (await api.setSorting(v === 'recent')).sortByRecent
          })),
        archivedRow()))
    ]
  },

  archived () {
    const list = T.archived || []
    if (!list.length) return [h('p', { class: 'help' }, 'Nothing is archived.')]
    return [h('div', { class: 'card' }, list.map(archivedItem))]
  },

  shortcut () {
    const s = T.settings
    const recorder = h('button', { type: 'button', class: 'recorder' })
    const hint = h('div', { class: 'help', role: 'status' })
    let recording = false
    let held = []

    const paint = (note = null, error = false) => {
      recorder.classList.toggle('recording', recording)
      recorder.classList.toggle('error', error && !recording)
      const caps = recording ? held : (s.hotkeyEnabled ? accelCaps(s.hotkey) : [])
      recorder.replaceChildren(...(caps.length
        ? caps.map(c => h('span', { class: 'keycap' }, c))
        : [h('span', { class: 'waiting' }, recording ? 'Press the keys…' : 'Off')]))
      recorder.setAttribute('aria-label', recording
        ? 'Recording a shortcut. Press the keys, or Escape to cancel.'
        : `Shortcut: ${s.hotkeyEnabled ? prettyAccel(s.hotkey) : 'off'}. Click to record a new one.`)
      hint.className = `help${error ? ' danger' : ''}`
      hint.textContent = note || (recording
        ? 'Press the combination you want. Esc cancels.'
        : s.hotkeyEnabled
          ? 'Click the keys to record a new shortcut.'
          : `The shortcut is off. CASE opens from the ${IS_WIN ? 'notification area or the Start menu' : 'menu bar or the Dock'}.`)
    }

    const save = async (accel, enabled) => {
      const res = await api.setHotkey(accel, enabled)
      if (res.error) return res.error
      s.hotkey = accel
      s.hotkeyEnabled = enabled
      enabledSwitch.setAttribute('aria-checked', String(enabled))
      return null
    }

    recorder.addEventListener('click', () => {
      recording = !recording
      held = []
      paint()
    })
    recorder.addEventListener('blur', () => { if (recording) { recording = false; paint() } })
    recorder.addEventListener('keyup', e => { if (recording) { held = heldCaps(e); paint() } })
    recorder.addEventListener('keydown', async e => {
      if (!recording) return
      e.preventDefault()
      e.stopPropagation()
      if (e.key === 'Escape') { recording = false; return paint() }
      const got = accelFromEvent(e)
      if (!got) { held = heldCaps(e); return paint() }
      if (got.bare) {
        held = accelCaps(got.accel)
        return paint(`Add ${IS_WIN ? 'Ctrl, Alt or Shift' : '⌘, ⌥, ⌃ or ⇧'} — a lone key would fire while you type.`, true)
      }
      recording = false
      const err = await save(got.accel, true)
      if (err) return paint(`${err} ${prettyAccel(s.hotkey)} stays.`, true)
      paint(`Saved. ${prettyAccel(got.accel)} now summons CASE.`)
    })

    const enabledSwitch = toggle('Use a global shortcut', s.hotkeyEnabled, async on => {
      const err = await save(s.hotkey, on)
      if (err) { paint(err, true); return !on }
      paint()
      return on
    })

    const reset = h('button', {
      type: 'button',
      class: 'btn',
      onclick: async () => {
        const err = await save(s.defaultHotkey, true)
        paint(err || `Back to ${prettyAccel(s.defaultHotkey)}.`, Boolean(err))
      }
    }, `Reset to ${prettyAccel(s.defaultHotkey)}`)

    paint()
    return [
      h('section', { class: 'card recorder-card', 'aria-label': 'Summon shortcut' }, recorder, hint,
        h('div', { class: 'identity' },
          h('button', { type: 'button', class: 'btn primary', onclick: () => { recording = true; held = []; paint(); recorder.focus() } }, 'Record new shortcut'),
          reset)),
      h('div', { class: 'card' },
        settingRow('Use a global shortcut', IS_WIN
          ? 'Works from any app. Avoid Ctrl+Alt: on most European layouts that is AltGr, and it would swallow characters you type.'
          : 'Works from any app. Press it again to put CASE away.', enabledSwitch),
        h('div', { class: 'shortcut-hints' },
          h('span', {}, h('kbd', {}, `${combo('1')}–9`), 'open an account'),
          h('span', {}, h('kbd', {}, combo('K')), 'account actions'),
          h('span', {}, h('kbd', {}, combo('N')), 'new account'),
          h('span', {}, h('kbd', {}, combo(',')), 'this window')))
    ]
  },

  presence () {
    const s = T.settings
    const value = !s.menuBar ? 'dock' : s.hideDock ? 'bar' : 'both'
    const choices = IS_WIN
      ? [
          ['both', 'Notification area and taskbar', 'Pick an account from the notification area, or open the window from the taskbar.'],
          ['bar', 'Notification area only', 'No taskbar button while it runs. The notification area is the way in.'],
          ['dock', 'Taskbar only', 'No notification area icon. Without the shortcut, closing the window quits CASE.']
        ]
      : [
          ['both', 'Menu bar and Dock', 'Pick an account from the menu bar, or open the window from the Dock.'],
          ['bar', 'Menu bar only', 'No Dock icon while it runs. The menu bar is the way in.'],
          ['dock', 'Dock only', 'No menu bar icon. Without the shortcut, closing the window quits CASE.']
        ]
    const mapping = { both: [true, false], bar: [true, true], dock: [false, false] }
    const pick = async id => {
      const res = await api.setPresentation(...mapping[id])
      s.menuBar = res.menuBar
      s.hideDock = res.hideDock
      render()
    }
    const pinTitle = IS_WIN ? 'Start menu shortcut' : 'Keep CASE in the Dock'
    const pin = h('div', { class: 'card' }, settingRow(pinTitle, 'Checking…'))
    api.shortcutStatus().then(st => {
      const create = h('button', {
        type: 'button',
        class: 'btn',
        title: st.tip,
        onclick: async () => {
          create.disabled = true
          create.textContent = 'Adding…'
          const r = await api.shortcutCreate()
          if (!r.ok) toast(r.error || 'Could not add it.', 'error')
          render()
        }
      }, st.cta)
      pin.replaceChildren(settingRow(pinTitle, st.blurb, st.present
        ? h('span', { class: 'status' }, h('span', { class: 'help ok' }, IS_WIN ? 'Added' : 'Pinned'))
        : create))
    })
    return [
      h('div', { class: 'presence', role: 'radiogroup', 'aria-label': 'Where CASE lives' },
        choices.map(([id, label, help]) => h('button', {
          type: 'button', role: 'radio', class: 'radio-card', 'aria-checked': String(id === value), onclick: () => pick(id)
        }, scene(mapping[id][0], !(mapping[id][0] && mapping[id][1])),
        h('span', { class: 'radio-head' }, h('span', { class: 'radio' }), label),
        h('span', { class: 'help' }, help)))),
      pin
    ]
  },

  updates () {
    const s = T.settings
    const u = T.update
    let title = `CASE ${s.version}`
    let sub = s.lastUpdateCheck ? `Last checked ${fmtAgo(s.lastUpdateCheck)}` : 'Not checked yet'
    let tone = ''
    let button = ['Check now', check, false]
    let notes = null
    const pct = Math.round(T.progress * 100)
    const releases = () => api.openLink('releases')

    if (T.upd === 'checking') { sub = 'Checking GitHub…'; button = null }
    else if (T.upd === 'downloading') { title = `Downloading CASE ${u.version}`; sub = `${pct}% — checked against its published checksum before anything changes`; button = null }
    else if (T.upd === 'restarting') { title = `Installing CASE ${u.version}`; sub = 'The checksum matched. CASE reopens in a moment.'; tone = 'ok'; button = null }
    else if (u) {
      if (!u.ok) { sub = `Could not reach GitHub — ${u.error}`; tone = 'danger'; button = ['Try again', check, false] }
      else if (u.none) sub = 'No releases are published yet.'
      else if (u.available && !u.packaged) { sub = `CASE ${u.version} is out. This is a development build, so update it from source.`; button = ['Release notes', releases, false] }
      else if (u.available) {
        title = `CASE ${u.version} is ready`
        sub = [u.bytes ? fmtBytes(u.bytes) : '', 'checksum published', u.publishedAt ? `released ${fmtAgo(u.publishedAt)}` : ''].filter(Boolean).join(' · ')
        button = ['Install and restart', install, true]
        notes = u.notes
      } else if (u.unverifiable) { sub = `CASE ${u.version} is out, but has no published checksum, so it is not installed from here.`; tone = 'warn'; button = ['Open releases page', releases, false] }
      else if (u.noBuild) { sub = `CASE ${u.version} is out, but not yet for this platform.`; tone = 'warn'; button = ['Open releases page', releases, false] }
      else sub = 'You are on the latest version · checked just now'
    }

    const busy = T.upd === 'downloading' || T.upd === 'restarting'
    return [
      h('section', { class: 'card version-card', 'aria-label': 'Version' },
        h('div', { class: 'version-row' }, appMark(44),
          h('div', { class: 'card-body' }, h('div', { class: 'label' }, title), h('div', { class: `help ${tone}`, role: 'status' }, sub)),
          button ? h('button', { type: 'button', class: `btn${button[2] ? ' primary' : ''}`, onclick: button[1] }, button[0]) : ''),
        busy ? h('div', { class: 'bar', role: 'progressbar', 'aria-label': 'Download', 'aria-valuenow': pct, 'aria-valuemin': 0, 'aria-valuemax': 100 }, h('span', { style: { width: `${T.upd === 'restarting' ? 100 : pct}%` } })) : '',
        notes ? h('div', { class: 'notes' }, h('strong', {}, `What’s new in ${u.version}`), h('pre', {}, notes.slice(0, 1500)),
          h('div', {}, h('button', { type: 'button', class: 'btn link', onclick: releases }, 'Full release notes'))) : ''),
      h('div', { class: 'card' },
        settingRow('Check automatically', 'Once a day. Nothing installs until you say so, and every download is checked against its published checksum.',
          toggle('Check for updates automatically', s.autoUpdateCheck, async on => (await api.setAutoUpdate(on)).autoUpdateCheck)))
    ]
  },

  data () {
    const s = T.settings
    const n = issues()
    const failing = T.health ? T.health.accounts.some(a => a.failures && !a.failures.resolved) : false
    const state = !T.health
      ? h('span', { class: 'help' }, 'Checking…')
      : n
        ? h('span', { class: `chip${failing ? ' danger' : ''}` }, failing ? 'Not saving' : `${n} to restore`)
        : h('span', { class: 'status' }, h('span', { class: 'dot' }), 'All saved')
    const reveal = dir => h('button', { type: 'button', class: 'btn', onclick: () => api.revealPath(dir) }, `Show in ${FILE_MANAGER}`)
    return [
      h('div', { class: 'card' },
        settingRow('Session health', 'Checked every 2 minutes. You are notified the moment Claude stops saving sessions.',
          state, h('button', { type: 'button', class: 'btn', onclick: () => api.showHealth() }, 'Open')),
        settingRow('Index backups', 'Taken before every launch; the newest 10 per account are kept.', reveal(s.backupsDir)),
        h('div', { class: 'card-row' },
          h('div', { class: 'card-body' }, h('div', { class: 'label' }, 'Data folder'), h('div', { class: 'path', title: s.dataDir }, s.dataDir)),
          reveal(s.dataDir))),
      h('p', { class: 'help' }, 'Your ~/.claude folder — skills, plugins, settings and transcripts — is shared by every account and never touched here.')
    ]
  },

  about () {
    const link = (which, label) => h('button', { type: 'button', class: 'btn link', onclick: () => api.openLink(which) }, label)
    return [
      h('section', { class: 'card about', 'aria-label': 'About CASE' },
        appMark(72),
        h('div', { class: 'h1' }, 'CASE'),
        h('div', { class: 'help' }, `Claude Account Switcher Engine · version ${T.settings.version}`),
        h('div', { class: 'links' }, link('source', 'Source'), link('releases', 'Releases'), link('licence', 'MIT licence')))
    ]
  }
}

/** A drawing of where CASE shows up: the menu bar and Dock, or the taskbar. */
function scene (inBar, inDock) {
  const tile = on => h('i', { class: on ? 'case' : 'gone' })
  if (IS_WIN) {
    return h('span', { class: 'scene', 'aria-hidden': 'true' },
      h('span', { class: 'taskbar' }, h('i'), h('i'), tile(inDock), h('i'), h('span', { class: 'grow' }), tile(inBar), h('i', { class: 'clock' })))
  }
  return h('span', { class: 'scene', 'aria-hidden': 'true' },
    h('span', { class: 'menubar' }, h('i'), tile(inBar), h('i')),
    h('span', { class: 'dock' }, h('i'), h('i'), tile(inDock), h('i')))
}

function archivedRow () {
  if (T.archived === null) return settingRow('Archived profiles', 'Looking…')
  const n = T.archived.length
  if (!n) return settingRow('Archived profiles', 'Nothing is archived. Archiving moves a profile aside without deleting it.')
  const total = T.archived.reduce((sum, r) => sum + (Number.isFinite(r.bytes) ? r.bytes : 0), 0)
  return settingRow('Archived profiles', `${plural(n, 'profile')} set aside${total ? ` · ${fmtBytes(total)} on disk` : ''}. Restore one, or delete it for good.`,
    h('button', { type: 'button', class: 'btn', onclick: () => go('archived') }, 'Manage…'))
}

function archivedItem (r) {
  const confirmInput = h('input', {
    class: 'text-input small grow', type: 'text', spellcheck: 'false', autocomplete: 'off', 'aria-label': `Type ${r.name} to confirm`,
    oninput: () => { del.disabled = confirmInput.value.trim() !== r.name }
  })
  const del = h('button', {
    type: 'button',
    class: 'btn danger',
    disabled: true,
    onclick: async () => {
      const res = await api.archiveDelete(r.id, confirmInput.value.trim())
      if (res.error) return toast(res.error, 'error')
      toast(`The archive of ${r.name} is deleted`)
      await loadArchived()
    }
  }, Number.isFinite(r.bytes) ? `Delete ${fmtBytes(r.bytes)}` : 'Delete')
  const confirmBox = h('label', { class: 'confirm-box', hidden: true },
    h('span', {}, 'Type ', h('strong', {}, r.name), ' to delete this archive for good. It cannot be undone.'),
    h('div', { class: 'identity' }, confirmInput, del))
  const restore = async () => {
    const res = await api.archiveRestore(r.id)
    if (res.error) return toast(res.error, 'error')
    toast(res.renamed ? `Restored — its old folder was taken, so it went to ${String(res.renamed).split(/[\\/]/).pop()}` : `${r.name} is back in the list`)
    await loadArchived()
  }
  return h('div', { class: 'archived-item' },
    h('div', { class: 'archived-row' }, avatar(r, 32),
      h('div', { class: 'card-body' },
        h('div', { class: 'label' }, r.name),
        h('div', { class: 'help' }, [r.exists ? fmtBytes(r.bytes) : 'the folder is no longer there', r.archivedAt ? `archived ${fmtAgo(r.archivedAt)}` : ''].filter(Boolean).join(' · ')),
        h('div', { class: 'path', title: r.dir }, r.dir)),
      h('button', { type: 'button', class: 'btn', disabled: !r.exists, onclick: restore }, 'Restore'),
      h('button', { type: 'button', class: 'btn text-danger', onclick: () => { confirmBox.hidden = !confirmBox.hidden; if (!confirmBox.hidden) confirmInput.focus() } }, 'Delete…')),
    confirmBox)
}

async function loadArchived () {
  const res = await api.list()
  T.archived = res.archived || []
  if (T.section === 'general' || T.section === 'archived') render()
}

// --------------------------------------------------------------- updates ---

async function check () {
  T.upd = 'checking'
  if (T.section === 'updates') render()
  const res = await api.updateCheck()
  T.update = res
  T.upd = 'idle'
  if (res.ok) T.settings.lastUpdateCheck = Date.now()
  render()
}

async function install () {
  T.upd = 'downloading'
  T.progress = 0
  render()
  const off = api.onUpdateProgress(p => {
    T.progress = p
    if (T.upd === 'downloading' && T.section === 'updates') render()
  })
  const res = await api.updateInstall()
  off()
  if (res && res.error) {
    T.upd = 'idle'
    render()
    return toast(res.error, 'error')
  }
  T.upd = 'restarting'
  render()
}

// ----------------------------------------------------------------- start ---

api.onSettingsSection(s => go(s))
api.onAccountsChanged(() => loadArchived())
api.onHealthUpdate(d => {
  T.health = d
  if (!T.settings) return
  renderNav()
  if (T.section === 'data') render()
})
api.onUpdateAvailable(info => {
  // Raised by the daily check, which only runs in an installed build.
  T.update = { ...info, packaged: true }
  if (!T.settings) return
  renderNav()
  if (T.section === 'updates' && T.upd === 'idle') render()
})

;(async () => {
  T.settings = await api.getSettings()
  render()
  loadArchived()
  api.scanHealth().then(d => {
    T.health = d
    renderNav()
    if (T.section === 'data') render()
  }).catch(() => {})
  if (T.section === 'updates') check()
})()
