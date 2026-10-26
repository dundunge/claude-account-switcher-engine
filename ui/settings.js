'use strict'

// The settings window: one section shown at a time, plus
// the archived profiles, reached from General.

const sections = () => [
  { id: 'general', label: tr("通用", "General"), icon: 'sliders', lede: tr("设置启动方式和账号列表顺序。", "How CASE starts, and how your accounts are listed.") },
  { id: 'usage', label: tr('额度查询', 'Usage queries'), icon: 'refresh', lede: tr('主页可手动刷新全部账号。这里可设置定时刷新、逐账号查询、管理授权和打开独立 CLI。', 'Manually refresh all accounts from home. Set scheduled refresh, query individual accounts, manage authorization and open account CLIs here.') },
  { id: 'shortcut', label: tr("快捷键", "Shortcut"), icon: 'keyboard', lede: tr("在任意应用中调出账号列表。", "Summon the launcher from any app.") },
  { id: 'presence', label: IS_WIN ? tr("通知区域", "Notification area") : tr("菜单栏与程序坞", "Menu bar & Dock"), icon: 'window', lede: tr("选择 CASE 的显示位置，始终保留一个可打开的入口。", "Choose where CASE lives. One place always stays, so there is a way back in.") },
  { id: 'updates', label: tr("更新", "Updates"), icon: 'download', lede: tr("更新仅检查 dundunge/claude-account-switcher-engine。", "Updates are checked only in dundunge/claude-account-switcher-engine.") },
  { id: 'data', label: tr("会话与数据", "Sessions & data"), icon: 'shield', lede: tr("查看会话索引、备份和 CASE 数据位置。", "The safety net for Claude’s session list, and where CASE keeps its files.") },
  { id: 'about', label: tr("关于", "About"), icon: 'info', lede: '' }
]

const T = {
  section: window.CASE_SECTION || 'general',
  settings: null,
  archived: null,
  accounts: null,
  health: null,
  schedule: null,
  savingSchedule: false,
  update: null,     // the last check's answer
  upd: 'idle',      // idle | checking | downloading | restarting
  progress: 0
}

const $nav = document.getElementById('nav')
const $content = document.getElementById('content')
const quota = createQuotaController({ getAccounts: () => T.accounts || [], onChange: paintQuota })

function go (section) {
  T.section = section
  render()
  $content.scrollTop = 0
  if (section === 'updates' && !T.update && T.upd === 'idle') check()
  if (section === 'usage') pullQuotaStatus()
}

function render () {
  if (!T.settings) return
  renderNav()
  const id = T.section
  const s = sections().find(x => x.id === id)
  const head = id === 'archived'
    ? h('header', { class: 'content-head' },
      h('div', {}, h('button', { type: 'button', class: 'btn link', onclick: () => go('general') }, tr("‹ 通用", "‹ General"))),
      h('h1', { class: 'h1' }, tr("已归档账号", "Archived profiles")),
      h('p', {}, tr("归档会保留文件。恢复后目录移回原处，账号重新显示。", "Set aside, not deleted. Restoring moves the folder back and lists the account again.")))
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
    if (id === 'updates' && T.update && T.update.available) return h('span', { class: 'nav-badge', 'aria-label': tr("有可用更新", "Update available") }, '1')
    if (id === 'data' && issues()) return h('span', { class: 'nav-badge', 'aria-label': tf("{0} 项待检查", "{0} to look at", issues()) }, String(issues()))
    return ''
  }
  $nav.replaceChildren(
    ...sections().map(s => h('button', { type: 'button', class: 'nav-item', 'aria-current': s.id === current ? 'page' : null, onclick: () => go(s.id) },
      icon(s.icon), h('span', {}, s.label), badge(s.id))),
    h('span', { class: 'grow' }),
    h('div', { class: 'version' }, `CASE ${T.settings.version}`))
}

const section = (label, ...kids) => h('section', { class: 'stack', 'aria-label': label }, h('h2', { class: 'section-label' }, label), ...kids)

const VIEWS = {
  usage () {
    return [h('div', { class: 'usage-page' }, quotaScheduleCard(T.schedule, T.savingSchedule, saveQuotaSchedule), quotaClockHint(),
      T.accounts === null ? h('p', { class: 'help' }, tr('正在读取账号列表…', 'Loading accounts…'))
        : T.accounts.length ? T.accounts.map(account => quota.card(account, true))
          : h('p', { class: 'help' }, tr('请先在主页添加账号。', 'Add an account on the home page first.')))]
  },

  general () {
    const s = T.settings
    return [
      section(tr('界面语言', 'Interface language'), h('div', { class: 'card' },
        settingRow(tr('语言', 'Language'), tr('立即切换，关闭 CASE 后仍会保留。', 'Applies immediately and is remembered after CASE closes.'),
          segmented(tr('语言', 'Language'), [['zh-CN', '中文'], ['en', 'English']], s.language || 'zh-CN', async language => {
            const result = await api.setLanguage(language)
            if (result.error) toast(result.error, 'error')
          })))),
      section(tr("启动", "Startup"), h('div', { class: 'card' },
        settingRow(tr("登录 Mac 时启动", "Launch at login"), tr("启动后隐藏窗口，可随时通过快捷键调出。", "Starts hidden, ready for your shortcut."),
          toggle(tr("登录 Mac 时启动", "Launch at login"), s.openAtLogin, async on => (await api.setLoginItem(on)).openAtLogin)))),
      section(tr("账号列表", "Account list"), h('div', { class: 'card' },
        settingRow(tr("排序", "Order"), tf("{0}–{1} 按列表顺序选择账号；排序改变时，数字快捷键也随之改变。", "{0}–{1} follow what you see, so recent order moves them too.", combo('1'), combo('9')),
          segmented(tr("排序", "Order"), [['yours', tr("手动顺序", "Your order")], ['recent', tr("最近使用优先", "Most recent first")]], s.sortByRecent ? 'recent' : 'yours', async v => {
            s.sortByRecent = (await api.setSorting(v === 'recent')).sortByRecent
          })),
        archivedRow()))
    ]
  },

  archived () {
    const list = T.archived || []
    if (!list.length) return [h('p', { class: 'help' }, tr("暂无归档账号。", "Nothing is archived."))]
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
        : [h('span', { class: 'waiting' }, recording ? tr("请按快捷键…", "Press the keys…") : tr("已关闭", "Off"))]))
      recorder.setAttribute('aria-label', recording
        ? tr("正在录制快捷键，按 Esc 取消。", "Recording a shortcut. Press the keys, or Escape to cancel.")
        : tf("快捷键：{0}。点击可重新录制。", "Shortcut: {0}. Click to record a new one.", s.hotkeyEnabled ? prettyAccel(s.hotkey) : tr("已关闭", "Off")))
      hint.className = `help${error ? ' danger' : ''}`
      hint.textContent = note || (recording
        ? tr("请按所需组合键，Esc 取消。", "Press the combination you want. Esc cancels.")
        : s.hotkeyEnabled
          ? tr("点击上方按键区域可录制新快捷键。", "Click the keys to record a new shortcut.")
          : tf("快捷键已关闭，请从{0}打开 CASE。", "The shortcut is off. CASE opens from the {0}.", IS_WIN ? tr("通知区域或开始菜单", "notification area or the Start menu") : tr("菜单栏或程序坞", "menu bar or the Dock")))
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
        return paint(tf("请搭配 {0}，避免普通输入误触。", "Add {0} — a lone key would fire while you type.", IS_WIN ? tr("Ctrl、Alt 或 Shift", "Ctrl, Alt or Shift") : tr("⌘、⌥、⌃ 或 ⇧", "⌘, ⌥, ⌃ or ⇧")), true)
      }
      recording = false
      const err = await save(got.accel, true)
      if (err) return paint(tf("{0} 保留原快捷键 {1}。", "{0} {1} stays.", err, prettyAccel(s.hotkey)), true)
      paint(tf("已保存，可用 {0} 打开 CASE。", "Saved. {0} now summons CASE.", prettyAccel(got.accel)))
    })

    const enabledSwitch = toggle(tr("启用全局快捷键", "Use a global shortcut"), s.hotkeyEnabled, async on => {
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
        paint(err || tf("已恢复为 {0}。", "Back to {0}.", prettyAccel(s.defaultHotkey)), Boolean(err))
      }
    }, tf("恢复 {0}", "Reset to {0}", prettyAccel(s.defaultHotkey)))

    paint()
    return [
      h('section', { class: 'card recorder-card', 'aria-label': tr("打开 CASE 的快捷键", "Summon shortcut") }, recorder, hint,
        h('div', { class: 'identity' },
          h('button', { type: 'button', class: 'btn primary', onclick: () => { recording = true; held = []; paint(); recorder.focus() } }, tr("录制新快捷键", "Record new shortcut")),
          reset)),
      h('div', { class: 'card' },
        settingRow(tr("启用全局快捷键", "Use a global shortcut"), IS_WIN
          ? tr("可在任意应用使用。部分键盘布局中 Ctrl+Alt 会影响正常输入。", "Works from any app. Avoid Ctrl+Alt: on most European layouts that is AltGr, and it would swallow characters you type.")
          : tr("可在任意应用中使用，再按一次隐藏 CASE。", "Works from any app. Press it again to put CASE away."), enabledSwitch),
        h('div', { class: 'shortcut-hints' },
          h('span', {}, h('kbd', {}, `${combo('1')}–9`), tr("打开账号", "open an account")),
          h('span', {}, h('kbd', {}, combo('K')), tr("账号操作", "account actions")),
          h('span', {}, h('kbd', {}, combo('N')), tr("添加账号", "New account")),
          h('span', {}, h('kbd', {}, combo(',')), tr("设置窗口", "this window"))))
    ]
  },

  presence () {
    const s = T.settings
    const value = !s.menuBar ? 'dock' : s.hideDock ? 'bar' : 'both'
    const choices = IS_WIN
      ? [
          ['both', tr("通知区域和任务栏", "Notification area and taskbar"), tr("通过通知区域选账号，或从任务栏打开窗口。", "Pick an account from the notification area, or open the window from the taskbar.")],
          ['bar', tr("仅通知区域", "Notification area only"), tr("运行时隐藏任务栏图标，通过通知区域打开。", "No taskbar button while it runs. The notification area is the way in.")],
          ['dock', tr("仅任务栏", "Taskbar only"), tr("隐藏通知区域图标；未开启快捷键时，关闭窗口会退出 CASE。", "No notification area icon. Without the shortcut, closing the window quits CASE.")]
        ]
      : [
          ['both', tr("菜单栏和程序坞", "Menu bar and Dock"), tr("从菜单栏选账号，或从程序坞打开窗口。", "Pick an account from the menu bar, or open the window from the Dock.")],
          ['bar', tr("仅菜单栏", "Menu bar only"), tr("运行时隐藏程序坞图标，从菜单栏打开。", "No Dock icon while it runs. The menu bar is the way in.")],
          ['dock', tr("仅程序坞", "Dock only"), tr("隐藏菜单栏图标；未开启快捷键时，关闭窗口会退出 CASE。", "No menu bar icon. Without the shortcut, closing the window quits CASE.")]
        ]
    const mapping = { both: [true, false], bar: [true, true], dock: [false, false] }
    const pick = async id => {
      const res = await api.setPresentation(...mapping[id])
      s.menuBar = res.menuBar
      s.hideDock = res.hideDock
      render()
    }
    const pinTitle = IS_WIN ? tr("开始菜单快捷方式", "Start menu shortcut") : tr("将 CASE 保留在程序坞", "Keep CASE in the Dock")
    const pin = h('div', { class: 'card' }, settingRow(pinTitle, tr("正在检查…", "Checking…")))
    api.shortcutStatus().then(st => {
      const create = h('button', {
        type: 'button',
        class: 'btn',
        title: st.tip,
        onclick: async () => {
          create.disabled = true
          create.textContent = tr("正在添加…", "Adding…")
          const r = await api.shortcutCreate()
          if (!r.ok) toast(r.error || tr("添加失败。", "Could not add it."), 'error')
          render()
        }
      }, st.cta)
      pin.replaceChildren(settingRow(pinTitle, st.blurb, st.present
        ? h('span', { class: 'status' }, h('span', { class: 'help ok' }, IS_WIN ? tr("已添加", "Added") : tr("已固定", "Pinned")))
        : create))
    })
    return [
      h('div', { class: 'presence', role: 'radiogroup', 'aria-label': tr("CASE 显示位置", "Where CASE lives") },
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
    let sub = s.lastUpdateCheck ? tf("{0}检查", "checked {0}", fmtAgo(s.lastUpdateCheck)) : tr("尚未检查", "Not checked yet")
    let tone = ''
    let button = [tr("立即检查", "Check now"), check, false]
    let notes = null
    const pct = Math.round(T.progress * 100)
    const releases = () => api.openLink('releases')

    if (T.upd === 'checking') { sub = tr("正在检查 GitHub…", "Checking GitHub…"); button = null }
    else if (T.upd === 'downloading') { title = tf("正在下载 CASE {0}", "Downloading CASE {0}", u.version); sub = tf("{0}% · 安装前校验发布的哈希值", "{0}% — checked against its published checksum before anything changes", pct); button = null }
    else if (T.upd === 'restarting') { title = tf("正在安装 CASE {0}", "Installing CASE {0}", u.version); sub = tr("校验通过，CASE 即将重新打开。", "The checksum matched. CASE reopens in a moment."); tone = 'ok'; button = null }
    else if (u) {
      if (!u.ok) { sub = tf("无法连接 GitHub：{0}", "Could not reach GitHub — {0}", u.error); tone = 'danger'; button = [tr("重试", "Try again"), check, false] }
      else if (u.none) sub = tr("暂无发布版本。", "No releases are published yet.")
      else if (u.available && !u.packaged) { sub = tf("CASE {0} 已发布，此构建需从源码更新。", "CASE {0} is out. This is a development build, so update it from source.", u.version); button = [tr("发行说明", "Release notes"), releases, false] }
      else if (u.available) {
        title = tf("CASE {0} 已发布", "CASE {0} is ready", u.version)
        sub = [u.bytes ? fmtBytes(u.bytes) : '', tr("已发布校验值", "checksum published"), u.publishedAt ? tf("{0}发布", "released {0}", fmtAgo(u.publishedAt)) : ''].filter(Boolean).join(' · ')
        sub += tr(" · 独立维护版，请按发行说明升级", " · independent build; see the release instructions")
        button = [tr("发行说明", "Release notes"), releases, false]
        notes = u.notes
      } else if (u.unverifiable) { sub = tf("CASE {0} 已发布，但缺少校验值，无法在此安装。", "CASE {0} is out, but has no published checksum, so it is not installed from here.", u.version); tone = 'warn'; button = [tr("打开发行页面", "Open releases page"), releases, false] }
      else if (u.noBuild) { sub = tf("CASE {0} 已发布，暂未提供此平台安装包。", "CASE {0} is out, but not yet for this platform.", u.version); tone = 'warn'; button = [tr("打开发行页面", "Open releases page"), releases, false] }
      else sub = tr("已是当前最新版本 · 刚刚检查", "You are on the latest version · checked just now")
    }

    const busy = T.upd === 'downloading' || T.upd === 'restarting'
    return [
      h('section', { class: 'card version-card', 'aria-label': tr("版本", "Version") },
        h('div', { class: 'version-row' }, appMark(44),
          h('div', { class: 'card-body' }, h('div', { class: 'label' }, title), h('div', { class: `help ${tone}`, role: 'status' }, sub)),
          button ? h('button', { type: 'button', class: `btn${button[2] ? ' primary' : ''}`, onclick: button[1] }, button[0]) : ''),
        busy ? h('div', { class: 'bar', role: 'progressbar', 'aria-label': tr("下载", "Download"), 'aria-valuenow': pct, 'aria-valuemin': 0, 'aria-valuemax': 100 }, h('span', { style: { width: `${T.upd === 'restarting' ? 100 : pct}%` } })) : '',
        notes ? h('div', { class: 'notes' }, h('strong', {}, tf("{0} 更新内容", "What’s new in {0}", u.version)), h('pre', {}, notes.slice(0, 1500)),
          h('div', {}, h('button', { type: 'button', class: 'btn link', onclick: releases }, tr("完整发行说明", "Full release notes")))) : ''),
      h('div', { class: 'card' },
        settingRow(tr("自动检查更新", "Check automatically"), tr("每天只检查自有仓库。请按发行说明更新应用和本地同步引擎。", "Check our repository once a day. Follow the release instructions to update the app and local sync engine."),
          toggle(tr("自动检查更新", "Check automatically"), s.autoUpdateCheck, async on => (await api.setAutoUpdate(on)).autoUpdateCheck)))
    ]
  },

  data () {
    const s = T.settings
    const n = issues()
    const failing = T.health ? T.health.accounts.some(a => a.failures && !a.failures.resolved) : false
    const state = !T.health
      ? h('span', { class: 'help' }, tr("正在检查…", "Checking…"))
      : n
        ? h('span', { class: `chip${failing ? ' danger' : ''}` }, failing ? tr("无法保存", "Not saving") : tf("{0} 项待恢复", "{0} to restore", n))
        : h('span', { class: 'status' }, h('span', { class: 'dot' }), tr("保存正常", "Saving normally"))
    const reveal = dir => h('button', { type: 'button', class: 'btn', onclick: () => api.revealPath(dir) }, tf("在{0}中显示", "Show in {0}", fileManager()))
    return [
      h('div', { class: 'card' },
        settingRow(tr("会话状态", "Session health"), tr("每 2 分钟检查一次，发现无法保存会话时提醒。", "Checked every 2 minutes. You are notified the moment Claude stops saving sessions."),
          state, h('button', { type: 'button', class: 'btn', onclick: () => api.showHealth() }, tr("打开", "Open"))),
        settingRow(tr("索引备份", "Index backups"), tr("每次打开前备份，每个账号保留最近 10 份。", "Taken before every launch; the newest 10 per account are kept."), reveal(s.backupsDir)),
        h('div', { class: 'card-row' },
          h('div', { class: 'card-body' }, h('div', { class: 'label' }, tr("数据目录", "Data folder")), h('div', { class: 'path', title: s.dataDir }, s.dataDir)),
          reveal(s.dataDir))),
      h('p', { class: 'help' }, tr("所有账号共用 ~/.claude 中的技能、插件、设置和正文，此处的账号管理不会删除它们。", "Your ~/.claude folder — skills, plugins, settings and transcripts — is shared by every account and never touched here."))
    ]
  },

  about () {
    const link = (which, label) => h('button', { type: 'button', class: 'btn link', onclick: () => api.openLink(which) }, label)
    return [
      h('section', { class: 'card about', 'aria-label': tr("关于 CASE", "About CASE") },
        appMark(72),
        h('div', { class: 'h1' }, 'CASE'),
        h('div', { class: 'help' }, tf("Claude 多账号管理 · {0} · 独立维护版", "Claude Account Switcher Engine · {0} · independent edition", T.settings.version)),
        h('p', { class: 'help' }, tr('基于 Patryk Starostka 的 CASE 项目开发，遵循 MIT 许可。', 'Based on CASE by Patryk Starostka, under the MIT licence.')),
        h('div', { class: 'links' }, link('source', tr("源代码", "Source")), link('upstream', tr('原项目', 'Original project')), link('releases', tr("发行版本", "Releases")), link('licence', tr("MIT 许可证", "MIT licence"))))
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
  if (T.archived === null) return settingRow(tr("已归档账号", "Archived profiles"), tr("正在查找…", "Looking…"))
  const n = T.archived.length
  if (!n) return settingRow(tr("已归档账号", "Archived profiles"), tr("暂无归档。归档仅移开账号目录，不会删除文件。", "Nothing is archived. Archiving moves a profile aside without deleting it."))
  const total = T.archived.reduce((sum, r) => sum + (Number.isFinite(r.bytes) ? r.bytes : 0), 0)
  return settingRow(tr("已归档账号", "Archived profiles"), tf("已归档 {0}{1}。可恢复或永久删除。", "{0} set aside{1}. Restore one, or delete it for good.", plural(n, 'profile'), total ? tf(" · 占用 {0}", " · {0} on disk", fmtBytes(total)) : ''),
    h('button', { type: 'button', class: 'btn', onclick: () => go('archived') }, tr("管理…", "Manage…")))
}

function archivedItem (r) {
  const confirmInput = h('input', {
    class: 'text-input small grow', type: 'text', spellcheck: 'false', autocomplete: 'off', 'aria-label': tf("输入 {0} 以确认", "Type {0} to confirm", r.name),
    oninput: () => { del.disabled = confirmInput.value.trim() !== r.name }
  })
  const del = h('button', {
    type: 'button',
    class: 'btn danger',
    disabled: true,
    onclick: async () => {
      const res = await api.archiveDelete(r.id, confirmInput.value.trim())
      if (res.error) return toast(res.error, 'error')
      toast(tf("已删除 {0} 的归档", "The archive of {0} is deleted", r.name))
      await loadArchived()
    }
  }, Number.isFinite(r.bytes) ? tf("删除 {0}", "Delete {0}", fmtBytes(r.bytes)) : tr("删除", "Delete"))
  const confirmBox = h('label', { class: 'confirm-box', hidden: true },
    h('span', {}, tr("输入 ", "Type "), h('strong', {}, r.name), tr(" 以永久删除此归档，此操作不可撤销。", " to delete this archive for good. It cannot be undone.")),
    h('div', { class: 'identity' }, confirmInput, del))
  const restore = async () => {
    const res = await api.archiveRestore(r.id)
    if (res.error) return toast(res.error, 'error')
    toast(res.renamed ? tf("已恢复，原目录已被占用，新目录为 {0}", "Restored — its old folder was taken, so it went to {0}", String(res.renamed).split(/[\\/]/).pop()) : tf("{0} 已重新加入列表", "{0} is back in the list", r.name))
    await loadArchived()
  }
  return h('div', { class: 'archived-item' },
    h('div', { class: 'archived-row' }, avatar(r, 32),
      h('div', { class: 'card-body' },
        h('div', { class: 'label' }, r.name),
        h('div', { class: 'help' }, [r.exists ? fmtBytes(r.bytes) : tr("目录已不存在", "the folder is no longer there"), r.archivedAt ? tf("{0}归档", "archived {0}", fmtAgo(r.archivedAt)) : ''].filter(Boolean).join(' · ')),
        h('div', { class: 'path', title: r.dir }, r.dir)),
      h('button', { type: 'button', class: 'btn', disabled: !r.exists, onclick: restore }, tr("恢复", "Restore")),
      h('button', { type: 'button', class: 'btn text-danger', onclick: () => { confirmBox.hidden = !confirmBox.hidden; if (!confirmBox.hidden) confirmInput.focus() } }, tr("删除…", "Delete…"))),
    confirmBox)
}

async function loadArchived () {
  const res = await api.list()
  T.archived = res.archived || []
  const previous = new Map((T.accounts || []).map(account => [account.id, account]))
  T.accounts = (res.accounts || []).map(account => mergeQuotaStatus(previous.get(account.id) || {}, account, quota.requests.get(account.id)))
  if (['general', 'archived', 'usage'].includes(T.section)) render()
  await pullQuotaStatus()
}

function paintQuota (id) {
  if (T.section !== 'usage') return
  $content.querySelectorAll('[data-usage-id]').forEach(card => {
    const account = T.accounts?.find(account => account.id === card.dataset.usageId)
    if (account && (!id || account.id === id)) card.replaceWith(quota.card(account, true))
  })
}

function paintQuotaSchedule (controls = false) {
  if (T.section !== 'usage') return
  const card = $content.querySelector('[data-quota-schedule]')
  if (!card) return
  if (controls) card.replaceWith(quotaScheduleCard(T.schedule, T.savingSchedule, saveQuotaSchedule))
  else {
    card.querySelector('[data-schedule-status]').textContent = quotaScheduleStatus(T.schedule)
    card.querySelector('[data-schedule-last-run]').textContent = quotaScheduleLastRun(T.schedule)
  }
}

async function saveQuotaSchedule (enabled, intervalMinutes) {
  if (T.savingSchedule) return T.schedule
  T.savingSchedule = true
  paintQuotaSchedule(true)
  try {
    const result = await api.setUsageSchedule(enabled, intervalMinutes)
    if (!result?.ok) throw new Error('save failed')
    T.schedule = result.schedule
  } catch {
    toast(tr('定时刷新设置未保存，请检查本地配置后重试。', 'Scheduled refresh settings were not saved. Check the local configuration and retry.'), 'error')
  } finally {
    T.savingSchedule = false
    paintQuotaSchedule(true)
  }
  return T.schedule
}

let pullingQuotaStatus = false
async function pullQuotaStatus () {
  if (pullingQuotaStatus || T.accounts === null) return
  pullingQuotaStatus = true
  try {
    const [accounts, schedule] = await Promise.all([api.status(), api.getUsageSchedule()])
    if (!T.savingSchedule && (!T.schedule || schedule.version >= T.schedule.version)) {
      T.schedule = schedule
      paintQuotaSchedule()
    }
    const status = new Map(accounts.map(account => [account.id, account]))
    const changed = []
    T.accounts = T.accounts.map(account => {
      const incoming = status.get(account.id)
      if (!incoming) return account
      const request = quota.requests.get(account.id)
      const next = mergeQuotaStatus(account, incoming, request)
      if (request?.bridgeFailed && next.monitor?.lastAttemptAt >= request.startedAt) quota.requests.delete(account.id)
      if (account.running !== next.running || account.monitor?.version !== next.monitor?.version || usageDisplayKey(account.usage) !== usageDisplayKey(next.usage)) changed.push(account.id)
      return next
    })
    changed.forEach(id => paintQuota(id))
  } catch {
    // Polling local status is not a new usage attempt or a usage failure.
  } finally {
    pullingQuotaStatus = false
  }
}

// These read CASE's local state and repaint age/reset text. They never issue a
// usage query, authorize an account, or open a CLI.
setInterval(() => {
  if (!document.hidden && T.section === 'usage') pullQuotaStatus()
}, 2500)
setInterval(() => {
  if (!document.hidden && T.section === 'usage') paintQuota()
}, 30000)

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
  T.schedule = T.settings.usageSchedule || { enabled: false, intervalMinutes: 120, version: 0 }
  render()
  loadArchived()
  api.scanHealth().then(d => {
    T.health = d
    renderNav()
    if (T.section === 'data') render()
  }).catch(() => {})
  if (T.section === 'updates') check()
})()

document.addEventListener('case-language-changed', () => {
  if (T.settings) T.settings.language = I18N.language
  render()
})
