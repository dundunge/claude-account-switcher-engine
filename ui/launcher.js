'use strict'

// The launcher: one window of a fixed size, keyboard first. Views swap inside
// it — the list, an account's sheet, the remove choice, session health, first
// run — and nothing ever resizes the window under the pointer.

const S = {
  accounts: [],       // listed order, with running state and usage folded in once probed
  archivedCount: 0,
  chromeProfiles: [],
  query: '',
  sel: 0,             // index into the filtered list
  view: null,
  arg: null,
  panel: null,        // the open ⌘K panel
  health: null,
  claude: { installed: true, hint: '' },
  discovered: []
}

const $app = document.querySelector('.app')
const $lead = document.getElementById('lead')
const $tools = document.getElementById('tools')
const $view = document.getElementById('view')

let searchInput = null
let rowsEl = null
let rowEls = []
let launching = false
let rerenderEditor = null
const quotaRequests = new Map()
let refreshingAll = false

const chromeName = dir => S.chromeProfiles.find(p => p.dir === dir)?.name || dir
const findAccount = id => S.accounts.find(a => a.id === id)

// ---------------------------------------------------------------- views ---

const VIEWS = {
  list: viewList,
  account: viewAccount,
  remove: viewRemove,
  health: viewHealth,
  usage: viewUsage,
  setup: viewSetup
}

function show (view, arg = null) {
  closePanel()
  S.view = view
  rerenderEditor = null
  S.arg = arg
  searchInput = null
  renderBar()
  VIEWS[view](arg || {})
}

function back () {
  if (S.view === 'remove' && S.arg?.from === 'account') return show('account', { id: S.arg.id })
  show('list')
}

function renderBar () {
  $lead.replaceChildren(S.view !== 'list' && S.view !== 'setup'
    ? h('button', { type: 'button', class: 'btn quiet back', onclick: back }, icon('back', 16, 2), tr("账号", "Accounts"))
    : '')
  $tools.replaceChildren(
    h('button', { type: 'button', class: 'btn quiet', onclick: () => show('usage'), title: tr('查看和刷新账号额度', 'View and refresh account usage') }, icon('refresh', 15), tr('额度', 'Usage')),
    healthChip(),
    h('button', { type: 'button', class: 'icon-btn', 'aria-label': tr("设置", "Settings"), title: tf("设置 ({0})", "Settings ({0})", combo(',')), onclick: () => api.openSettings() }, icon('sliders', 17))
  )
}

function healthChip () {
  // The list only: elsewhere the bar holds a back button and the view's own state.
  const d = S.health
  if (!d || S.view !== 'list') return ''
  const failing = d.accounts.filter(a => a.failures && !a.failures.resolved).length
  const lost = d.accounts.reduce((n, a) => n + a.orphans.length, 0)
  if (!failing && !lost) return ''
  return h('button', { type: 'button', class: `chip${failing ? ' danger' : ''}`, onclick: () => show('health') },
    icon('shieldAlert', 14, 2),
    failing ? tr("会话保存异常", "Not saving sessions") : tf("{0} 待恢复", "{0} to restore", plural(lost, 'session')))
}

// ----------------------------------------------------------------- list ---

function viewList () {
  searchInput = h('input', {
    type: 'text',
    placeholder: tr("选择账号…", "Open an account…"),
    spellcheck: 'false',
    autocomplete: 'off',
    'aria-label': tr("搜索账号", "Search accounts"),
    'aria-controls': 'accounts',
    value: S.query,
    oninput: e => { S.query = e.target.value; S.sel = 0; renderRows() }
  })
  rowsEl = h('div', { class: 'list', id: 'accounts', role: 'listbox', 'aria-label': tr("账号", "Accounts") })
  $view.replaceChildren(
    S.claude.installed ? '' : h('div', { class: 'notice danger', role: 'alert' }, icon('alert', 16, 2), h('span', {}, S.claude.hint)),
    h('label', { class: 'search' }, icon('search', 16, 2), searchInput, h('kbd', {}, combo('K'))),
    rowsEl,
    h('div', { class: 'notice' }, h('span', {}, tr("切号前会正常退出 Claude 并同步本地会话。长任务请先完成文件交接。", "Switching quits Claude normally and syncs local sessions. Write a file handoff before switching a long task."))),
    h('footer', { class: 'footer' },
      h('button', { type: 'button', class: 'btn quiet', onclick: () => show('account', { isNew: true }) }, icon('plus', 15, 2), tr("添加账号", "New account")),
      S.archivedCount
        ? h('button', { type: 'button', class: 'btn quiet', onclick: () => api.openSettings('archived') }, icon('archive', 15), tf("{0} 个已归档", "{0} archived", S.archivedCount))
        : '',
      h('span', { class: 'grow' }),
      h('span', { class: 'hint' }, h('kbd', {}, '↵'), tr("打开", "Open")),
      h('button', { type: 'button', class: 'btn quiet', onclick: openPanel }, h('kbd', {}, combo('K')), tr("操作", "Actions")))
  )
  renderRows()
  searchInput.focus()
  searchInput.setSelectionRange(S.query.length, S.query.length)
}

function filtered () {
  const q = S.query.trim().toLowerCase()
  return q ? S.accounts.filter(a => a.name.toLowerCase().includes(q)) : S.accounts
}

function subtitle (a) {
  if (a.exists === false) return tr("账号目录不存在", "Profile folder is missing")
  const when = a.running ? tr("运行中", "Running") : a.lastUsedAt ? fmtAgo(a.lastUsedAt) : tr("尚未打开", "Not opened yet")
  const bits = [when]
  // Status arrives a moment after the first paint; until then, say only what is known.
  if (a.sessions !== undefined) bits.push(plural(a.sessions, 'session'))
  if (a.chrome?.dir) bits.push(`Chrome · ${chromeName(a.chrome.dir)}`)
  return bits.join('  ·  ')
}

function renderRows () {
  if (!rowsEl) return
  const list = filtered()
  if (S.sel >= list.length) S.sel = Math.max(0, list.length - 1)
  rowEls = list.map((a, i) => row(a, i))
  rowsEl.replaceChildren(...rowEls)
  if (!list.length) rowsEl.append(emptyState())
  paintSel()
}

function row (a, i) {
  const key = a.idx < 9 ? combo(String(a.idx + 1)) : ''
  return h('button', {
    type: 'button',
    class: 'row',
    role: 'option',
    id: `acct-${a.id}`,
    onclick: () => launch(a),
    // Selection follows the pointer, as in any palette; a scroll under a still
    // pointer moves nothing.
    onmousemove: e => {
      if ((e.movementX || e.movementY) && S.sel !== i) { S.sel = i; paintSel(false) }
    },
    oncontextmenu: e => { e.preventDefault(); S.sel = i; paintSel(false); openPanel() }
  },
  avatar(a, 36, a.running),
  h('span', { class: 'row-text' }, h('span', { class: 'row-name' }, a.name), h('span', { class: 'row-sub' }, subtitle(a))),
  usageSummary(a.usage),
  h('kbd', { dataset: { key } }, key))
}

function paintSel (scroll = true) {
  rowEls.forEach((el, i) => {
    const on = i === S.sel
    el.classList.toggle('selected', on)
    el.setAttribute('aria-selected', String(on))
    const k = el.querySelector('kbd')
    k.textContent = on ? '↵' : k.dataset.key
  })
  const cur = rowEls[S.sel]
  if (cur && scroll) cur.scrollIntoView({ block: 'nearest' })
  if (searchInput) searchInput.setAttribute('aria-activedescendant', cur ? cur.id : '')
}

function emptyState () {
  const q = S.query.trim()
  if (!q) {
    return h('div', { class: 'empty' }, h('span', {}, tr("还没有添加账号。", "No accounts yet.")),
      h('button', { type: 'button', class: 'btn primary', onclick: () => show('account', { isNew: true }) }, icon('plus', 15, 2), tr("添加账号", "New account")))
  }
  return h('div', { class: 'empty' }, h('span', {}, tf("没有名为“{0}”的账号。", "No account is called “{0}”.", q)),
    h('button', { type: 'button', class: 'btn primary', onclick: () => show('account', { isNew: true, name: q }) }, tf("创建“{0}”", "Create “{0}”", q)))
}

async function launch (a) {
  if (launching) return toast(tr("正在切换账号，请稍候。", "An account switch is in progress. Please wait."))
  launching = true
  toast(tr("正在保存会话并打开账号…", "Saving sessions and opening the account…"))
  try {
    closePanel()
    const res = await api.launch(a.id)
    if (res?.error) return toast(res.error, 'error')
    if (!res?.ok) return toast(tf("无法打开“{0}”的 Claude 窗口。", "Could not open Claude for “{0}”.", a.name), 'error')
    if (res.syncBootstrap) return toast(tr("请在新窗口完成官方登录并创建一条简短 Code 会话，然后正常退出、从 CASE 重开。", "Sign in in the new Claude window and create a short Code session, then quit normally and reopen it from CASE."))
    toast(res.alreadyRunning ? tf("已显示 {0}", "Showing {0}", a.name) : tf("正在打开 {0}…", "Opening {0}…", a.name))
    // Out of the way, but still running, so the next pick is instant.
    setTimeout(() => api.dismiss(), 450)
  } finally {
    launching = false
  }
}

async function quit (a) {
  const res = await api.quitAccount(a.id)
  if (res.error) return toast(res.error, 'error')
  toast(tf("正在退出 {0}", "{0} is quitting", a.name))
  setTimeout(pullStatus, 1500)
}

// ---------------------------------------------------------------- ⌘K panel ---

function actionsFor (a) {
  return [
    { label: a.running ? tr("显示窗口", "Bring to front") : tr("在 Claude 中打开", "Open in Claude"), icon: 'open', key: '↵', run: () => launch(a) },
    { label: tr("编辑名称、图标和颜色…", "Edit name, icon and colour…"), icon: 'pencil', key: combo('E'), run: () => show('account', { id: a.id }) },
    { label: a.chrome?.dir ? tf("Chrome 配置 · {0}", "Chrome profile · {0}", chromeName(a.chrome.dir)) : tr("关联 Chrome 配置…", "Pair a Chrome profile…"), icon: 'globe', key: combo('G'), run: () => show('account', { id: a.id, focus: 'chrome' }) },
    { label: tf("在{0}中显示", "Show in {0}", fileManager()), icon: 'folder', key: combo('R', { shift: true }), run: () => api.reveal(a.id) },
    a.running && { label: tr("退出此账号", "Quit this account"), icon: 'power', key: combo('Q', { alt: true }), divider: true, run: () => quit(a) },
    !a.isDefault && { label: tr("归档…", "Archive…"), icon: 'archive', divider: !a.running, run: () => show('remove', { id: a.id, choice: 'archive' }) },
    { label: tr("移除…", "Remove…"), icon: 'trash', key: combo(IS_WIN ? 'Backspace' : '⌫'), danger: true, divider: a.isDefault && !a.running, run: () => show('remove', { id: a.id }) }
  ].filter(Boolean)
}

function openPanel () {
  const a = filtered()[S.sel]
  if (!a || S.view !== 'list') return
  closePanel()
  const items = actionsFor(a)
  const buttons = []
  let active = 0

  const setActive = i => {
    active = (i + buttons.length) % buttons.length
    buttons.forEach((b, j) => b.classList.toggle('active', j === active))
    buttons[active].focus()
  }

  const list = h('div', { class: 'menu-items' })
  for (const it of items) {
    if (it.divider) list.append(h('div', { class: 'menu-sep', role: 'separator' }))
    const b = h('button', {
      type: 'button',
      role: 'menuitem',
      class: `menu-item${it.danger ? ' danger' : ''}`,
      onclick: () => { closePanel(); it.run() },
      onmousemove: () => setActive(buttons.indexOf(b))
    }, icon(it.icon), h('span', {}, it.label), it.key ? h('span', { class: 'key' }, it.key) : '')
    buttons.push(b)
    list.append(b)
  }

  const scrim = h('div', { class: 'scrim', onclick: e => { if (e.target === scrim) closePanel() } },
    h('div', { class: 'menu', role: 'menu', 'aria-label': tf("{0} 的操作", "Actions for {0}", a.name) },
      h('div', { class: 'menu-head' }, avatar(a, 28), h('span', { class: 'title' }, a.name),
        h('span', { class: 'help' }, a.running ? tr("运行中", "Running") : a.lastUsedAt ? fmtAgo(a.lastUsedAt) : tr("尚未打开", "Not opened yet"))),
      list))

  S.panel = {
    el: scrim,
    keydown: e => {
      if (e.key === 'ArrowDown' || (e.key === 'Tab' && !e.shiftKey)) { e.preventDefault(); setActive(active + 1) }
      else if (e.key === 'ArrowUp' || (e.key === 'Tab' && e.shiftKey)) { e.preventDefault(); setActive(active - 1) }
      else if (e.key === 'Escape' || (modDown(e) && e.key.toLowerCase() === 'k')) { e.preventDefault(); closePanel() }
    }
  }
  $app.append(scrim)
  setActive(0)
}

function closePanel () {
  if (!S.panel) return
  S.panel.el.remove()
  S.panel = null
  if (searchInput) searchInput.focus()
}

// -------------------------------------------------------------- account ---

// Fifteen, so the colours fill two rows of eight beside "automatic". Saturation
// and lightness are fixed — only the hue is yours — which keeps every badge legible.
const HUES = [8, 24, 40, 56, 88, 112, 145, 168, 186, 202, 220, 246, 272, 300, 330]

function viewAccount ({ id, isNew, name: preset = '', focus, draft }) {
  const a = isNew ? { id: null, name: preset, hue: null, icon: null, emoji: null } : findAccount(id)
  if (!a) return show('list')
  const pick = draft?.pick || { icon: a.icon || null, emoji: a.emoji || null, hue: Number.isFinite(a.hue) ? a.hue : null }

  const nameInput = h('input', {
    class: 'text-input', type: 'text', value: draft?.name ?? a.name, placeholder: tr("工作账号", "Work"), spellcheck: 'false', autocomplete: 'off',
    oninput: paint,
    onkeydown: e => { if (e.key === 'Enter') { e.preventDefault(); save() } }
  })
  const preview = h('span', {})

  const iconChoices = [
    { label: tr("使用名称首字母", "Use the initials"), icon: null, emoji: null, content: () => 'Aa' },
    a.emoji ? { label: tf("保留 {0}", "Keep {0}", a.emoji), icon: null, emoji: a.emoji, content: () => h('span', { class: 'emoji' }, a.emoji) } : null,
    ...GLYPHS.map(g => ({ label: ({ briefcase: tr("工作", "Work"), home: tr("家庭", "Home"), flask: tr("实验", "Lab"), code: tr("代码", "Code"), rocket: tr("火箭", "Rocket"), book: tr("书籍", "Book"), lock: tr("锁", "Lock") })[g], icon: g, emoji: null, content: () => icon(g, 18) }))
  ].filter(Boolean)
  const iconCells = iconChoices.map(c => h('button', {
    type: 'button', class: 'cell', 'aria-label': c.label, title: c.label,
    onclick: () => { pick.icon = c.icon; pick.emoji = c.emoji; paint() }
  }, c.content()))

  const autoSwatch = h('button', { type: 'button', class: 'swatch auto', 'aria-label': tr("根据名称自动配色", "Automatic colour, from the name"), title: tr("自动", "Automatic"), onclick: () => { pick.hue = null; paint() } }, 'A')
  const hueSwatches = HUES.map(hue => h('button', {
    type: 'button', class: 'swatch', 'aria-label': tf("色相 {0}", "Hue {0}", hue), style: { '--h': hue },
    onclick: () => { pick.hue = hue; paint() }
  }))

  function paint () {
    const name = nameInput.value.trim() || a.name || '?'
    preview.replaceChildren(avatar({ name, icon: pick.icon, emoji: pick.emoji, hue: pick.hue }, 60))
    iconChoices.forEach((c, i) => iconCells[i].setAttribute('aria-pressed', String(c.icon === pick.icon && c.emoji === pick.emoji)))
    autoSwatch.setAttribute('aria-pressed', String(pick.hue === null))
    hueSwatches.forEach((s, i) => s.setAttribute('aria-pressed', String(HUES[i] === pick.hue)))
  }

  async function save () {
    const name = nameInput.value.trim()
    if (!name) { nameInput.focus(); return toast(tr("请填写账号名称。", "Give the account a name."), 'error') }
    const look = { name, emoji: pick.emoji || '', icon: pick.icon || '', hue: pick.hue }
    if (isNew) {
      const res = await api.add(name)
      if (res.error) return toast(res.error, 'error')
      const created = res.accounts.find(x => x.name === name)
      if (created && (look.icon || look.hue !== null)) await api.update(created.id, look)
      await refresh()
      show('list')
      if (created) launch(created)
      return
    }
    const res = await api.update(a.id, look)
    if (res.error) return toast(res.error, 'error')
    show('list')
    toast(tr("已保存", "Saved"))
  }

  const parts = [
    h('div', { class: 'identity' }, preview,
      h('label', {}, h('span', { class: 'field-label' }, tr("名称", "Name")), nameInput)),
    h('fieldset', { class: 'picker' }, h('legend', { class: 'field-label' }, tr("图标", "Icon")), h('div', { class: 'cells' }, iconCells)),
    h('fieldset', { class: 'picker' }, h('legend', { class: 'field-label' }, tr("颜色", "Colour")), h('div', { class: 'swatches' }, autoSwatch, hueSwatches))
  ]

  let chromeCard = null
  if (!isNew) {
    parts.push(usageCard(a))
    chromeCard = h('section', { class: 'card', 'aria-label': 'Chrome' }, h('div', { class: 'card-row' }, h('span', { class: 'help' }, tr("正在查找 Chrome 配置…", "Looking for Chrome profiles…"))))
    parts.push(chromeCard, storageCard(a))
    loadChrome(a, chromeCard)
  } else {
    parts.push(h('p', { class: 'help' }, tr("将创建空白账号目录。首次打开后，请在 Claude 中完成官方登录并创建一条简短 Code 会话，然后退出重开以同步历史。", "A blank profile will be created. Sign in officially in Claude, create a short Code session, then quit and reopen it to import local history.")))
  }

  if (!isNew && a.running) {
    $tools.prepend(h('span', { class: 'status' }, h('span', { class: 'dot' }), tr("运行中", "Running")),
      h('button', { type: 'button', class: 'btn', onclick: () => quit(a) }, tr("退出", "Quit")))
  }

  const scroller = h('div', { class: 'scroll' }, parts)
  $view.replaceChildren(scroller,
    h('div', { class: 'sheet-foot' },
      isNew ? '' : h('button', { type: 'button', class: 'btn text-danger', onclick: () => show('remove', { id: a.id, from: 'account' }) }, tr("移除…", "Remove…")),
      h('span', { class: 'grow' }),
      h('button', { type: 'button', class: 'btn tall', onclick: back }, tr("取消", "Cancel")),
      h('button', { type: 'button', class: 'btn tall primary', onclick: save }, isNew ? tr("创建并打开", "Create and open") : tr("保存", "Save"))))
  paint()
  rerenderEditor = () => viewAccount({ id, isNew, name: preset, focus, draft: { name: nameInput.value, pick: { ...pick } } })
  if (focus === 'chrome' && chromeCard) chromeCard.scrollIntoView({ block: 'center' })
  else { nameInput.focus(); nameInput.select() }
}

function quotaError (code) {
  const messages = {
    REFRESH_IN_PROGRESS: tr('正在查询，请稍候。', 'A query is already running. Please wait.'),
    REFRESH_COOLDOWN: tr('请间隔 60 秒再查询这个账号。', 'Wait 60 seconds before querying this account again.'),
    KEYCHAIN_UNAVAILABLE: tr('无法读取钥匙串授权，请在 Claude 内查看额度。', 'Keychain access is unavailable. View usage in Claude.'),
    KEYCHAIN_TIMEOUT: tr('钥匙串授权未完成，请在 Claude 内查看。', 'Keychain authorization did not finish. View usage in Claude.'),
    NO_SESSION: tr('这个目录没有有效登录，请在 Claude 中查看登录状态。', 'This profile has no valid session. Check its sign-in in Claude.'),
    NO_ORGANIZATION: tr('无法确定账号组织，请在 Claude 中查看。', 'The organization could not be determined. View usage in Claude.'),
    HTTP_401: tr('官方登录已失效，请在 Claude 中处理登录。', 'The official session has expired. Sign in again in Claude.'),
    HTTP_403: tr('官方接口拒绝查询，请在 Claude 中查看额度。', 'The official endpoint refused this query. View usage in Claude.'),
    BROWSER_VERIFICATION_REQUIRED: tr('官方接口要求浏览器验证，CASE 无法直接查询。请在 Claude 的设置 → 用量中查看。', 'The official endpoint requires browser verification. View Settings → Usage in Claude.'),
    HTTP_429: tr('官方接口暂时限流，请稍后再试。', 'The official endpoint is rate limiting requests. Try later.'),
    NETWORK_ERROR: tr('查询未连通，请检查网络；旧读数已保留。', 'Could not reach Claude. Check the connection; the previous reading is retained.'),
    PLATFORM_UNSUPPORTED: tr('官方额度查询目前只支持 macOS。', 'Official usage queries currently support macOS only.')
  }
  return (messages[code] || tr('未能更新额度，旧读数已保留。', 'Usage could not be updated. The previous reading is retained.')) + ` (${code})`
}

function usageCard (account, named = false) {
  const u = account.usage
  const request = quotaRequests.get(account.id) || {}
  const reset = (label, at) => at ? h('p', { class: 'help quota-reset' }, tf('{0}重置：{1}', '{0} resets: {1}', label, fmtWhen(at))) : ''
  return h('section', { class: 'card usage-card', dataset: { usageId: account.id, named: String(named) }, 'aria-label': named ? account.name : tr('订阅额度', 'Plan usage') },
    h('div', { class: 'usage-line' }, h('div', { class: 'top' }, h('b', {}, named ? account.name : tr('订阅额度', 'Plan usage')), h('span', { class: 'help' }, usageAge(u)))),
    quotaLine(tr('5 小时额度', '5-hour limit'), u?.fh), reset(tr('5 小时', '5-hour limit'), u?.fhResetAt),
    quotaLine(tr('周额度', 'Weekly limit'), u?.sd), reset(tr('周额度', 'Weekly limit'), u?.sdResetAt),
    h('p', { class: 'help' }, tr('百分比表示已使用的额度；查询失败时保留旧读数。本地采样可能延迟，未返回的重置时间不作推算。', 'Percentages show used quota. A failed query keeps the previous reading. Local samples may lag; reset times are shown only when returned by Claude.')),
    h('div', { class: 'quota-actions' },
      h('button', { type: 'button', class: 'btn', disabled: request.pending, onclick: () => refreshQuota(account.id, false), title: tr('重新读取本地文件，不会促使 Claude 生成新采样', 'Re-read local files; this does not make Claude record a new sample') }, icon('refresh', 14), tr('刷新本地额度', 'Refresh local usage')),
      h('button', { type: 'button', class: 'btn quiet', disabled: request.pending, onclick: () => refreshQuota(account.id), title: tr('尝试官方接口；可能要求浏览器验证', 'Try the official endpoint; browser verification may be required') }, request.pending ? tr('查询中…', 'Querying…') : tr('尝试官方查询', 'Try official query'))),
    request.code ? h('p', { class: 'help warn', role: 'status' }, quotaError(request.code)) : '',
    request.message ? h('p', { class: 'help', role: 'status' }, request.localOnly ? tr('已重读本地文件；采样时间未必改变。', 'Local files re-read; their sample time may be unchanged.') : tr('已查询官方接口。', 'Queried the official endpoint.')) : '')
}

function paintQuota (id) {
  if (S.view === 'list') return renderRows()
  document.querySelectorAll('[data-usage-id]').forEach(card => {
    const account = findAccount(card.dataset.usageId)
    if (account && (!id || account.id === id)) card.replaceWith(usageCard(account, card.dataset.named === 'true'))
  })
}

async function refreshQuota (id, official = true) {
  if (quotaRequests.get(id)?.pending) return
  quotaRequests.set(id, { pending: true })
  paintQuota(id)
  try {
    const result = await api.refreshUsage(id, official)
    const account = findAccount(id)
    if (account && result.usage) account.usage = result.usage
    quotaRequests.set(id, { pending: false, code: result.ok ? null : result.code || 'REFRESH_FAILED', message: result.ok, localOnly: result.localOnly })
  } catch {
    quotaRequests.set(id, { pending: false, code: 'HELPER_FAILED' })
  }
  paintQuota(id)
}

function viewUsage () {
  $view.replaceChildren(h('div', { class: 'scroll usage-page' },
    h('div', { class: 'quota-heading' }, h('h1', { class: 'h1' }, tr('账号额度', 'Account usage')),
      h('button', { type: 'button', class: 'btn', disabled: refreshingAll, onclick: async () => {
        refreshingAll = true
        viewUsage()
        for (const account of S.accounts) await refreshQuota(account.id, false)
        refreshingAll = false
        if (S.view === 'usage') viewUsage()
      } }, tr('刷新全部本地记录', 'Refresh all local samples'))),
    h('p', { class: 'help' }, tr('本地刷新只重读 Claude 已保存的采样。官方查询不发送模型消息，但可能要求浏览器验证；此时请在 Claude 的设置 → 用量中查看。首次读取登录状态时，macOS 可能要求钥匙串授权。官方查询间隔至少 60 秒。', 'Local refresh re-reads samples already saved by Claude. Official queries use no model messages, but may require browser verification; view Settings → Usage in Claude in that case. macOS may request Keychain access. Official queries have a 60-second cooldown.')),
    S.accounts.map(account => usageCard(account, true))))
}

function storageCard (a) {
  const size = h('span', {}, '…')
  api.size(a.id).then(r => { size.textContent = fmtBytes(r.bytes) })
  return h('section', { class: 'card', 'aria-label': tr("存储", "Storage") },
    h('div', { class: 'card-row' },
      h('div', { class: 'card-body' },
        h('div', { class: 'label' }, size, a.sessions !== undefined ? ` · ${plural(a.sessions, 'session')}` : ''),
        h('div', { class: 'path', title: a.dir }, a.dir)),
      h('button', { type: 'button', class: 'btn', onclick: () => api.reveal(a.id) }, tf("在{0}中显示", "Show in {0}", fileManager()))))
}

async function loadChrome (a, card) {
  const info = await api.chromeList(a.id)
  if (!info.installed) {
    card.replaceChildren(h('div', { class: 'card-row' }, icon('globe', 18), h('div', { class: 'card-body' },
      h('div', { class: 'label' }, 'Chrome'), h('div', { class: 'help' }, tr("未安装 Google Chrome。", "Google Chrome is not installed.")))))
    return
  }
  let current = info.current
  const render = () => {
    const dir = current?.dir || ''
    const profile = info.profiles.find(p => p.dir === dir)
    const suggestion = !dir && info.suggestion ? info.profiles.find(p => p.dir === info.suggestion) : null
    const select = h('select', { class: 'select', 'aria-label': tr("Chrome 配置", "Chrome profile"), onchange: e => choose(e.target.value) },
      h('option', { value: '', selected: !dir }, tr("不关联", "None")),
      info.profiles.map(p => h('option', { value: p.dir, selected: p.dir === dir }, p.userName ? `${p.name} — ${p.userName}` : p.name)),
      dir && !profile ? h('option', { value: dir, selected: true }, dir) : '',
      h('option', { value: '__new__' }, tr("新建 Chrome 配置…", "New Chrome profile…")))
    const status = !dir
      ? h('div', { class: 'help' }, suggestion ? tf("建议：{0}", "Suggested: {0}", suggestion.name) : tr("打开 Claude 时不同时启动 Chrome。", "Opens nothing alongside Claude."))
      : profile && !profile.hasExtension
        ? h('div', { class: 'help warn' }, tr("此 Chrome 配置未安装 Claude 扩展。", "No Claude extension in this profile."))
        : profile
          ? h('div', { class: 'help ok' }, tr("已安装 Claude 扩展", "Claude extension installed"))
          : h('div', { class: 'help' }, tr("首次打开时由 Chrome 创建此配置。", "Chrome creates this profile the first time it opens."))
    const rows = [h('div', { class: 'card-row' }, icon('globe', 18),
      h('div', { class: 'card-body' }, h('div', { class: 'label' }, tr("Chrome 配置", "Chrome profile")), status), select)]
    if (dir && !(profile && profile.hasExtension)) {
      rows.push(settingRow(tr("Chrome 中的 Claude", "Claude in Chrome"), tr("将在该 Chrome 配置中打开扩展页面，需自行安装。", "Chrome only installs extensions you add yourself; this opens its page in that profile."),
        h('button', { type: 'button', class: 'btn', onclick: () => api.chromeExtension(dir) }, tr("获取扩展", "Get the extension"))))
    }
    if (dir) {
      rows.push(settingRow(tr("打开此账号时同时启动 Chrome", "Open Chrome with this account"), '',
        toggle(tr("打开此账号时同时启动 Chrome", "Open Chrome with this account"), current.openOnLaunch !== false, async on => {
          const res = await api.chromePair(a.id, dir, on)
          if (res.error) { toast(res.error, 'error'); return !on }
          current = { dir, openOnLaunch: on }
          return on
        })))
    }
    card.replaceChildren(...rows)
  }
  const choose = async value => {
    let dir = value
    if (value === '__new__') dir = (await api.chromeNewProfile()).dir
    const res = await api.chromePair(a.id, dir || null, true)
    if (res.error) return toast(res.error, 'error')
    current = dir ? { dir, openOnLaunch: true } : null
    render()
  }
  render()
}

// --------------------------------------------------------------- remove ---

function viewRemove ({ id, choice: initial }) {
  const a = findAccount(id)
  if (!a) return show('list')
  let choice = a.isDefault ? 'forget' : (initial || 'forget')
  let bytes = null
  const keep = text => h('span', { class: 'consequence' }, h('span', { class: 'keep' }, icon('check', 13, 2.4)), text)
  const lose = text => h('span', { class: 'consequence' }, h('span', { class: 'lose' }, icon('cross', 13, 2.4)), text)
  const sessions = a.sessions !== undefined ? plural(a.sessions, 'session') : tr("此账号的会话", "its sessions")

  const options = [
    { id: 'forget', title: tr("仅从列表移除", "Remove from list"), tag: tr("保留本地文件", "nothing on disk changes"), lines: () => [keep(tr("账号目录原样保留", "The profile folder stays exactly where it is")), keep(tr("可随时重新添加", "Add it back any time"))] },
    !a.isDefault && { id: 'archive', title: tr("归档", "Archive"), tag: tr("立即完成，无需复制", "instant, whatever its size"), lines: () => [keep(tr("仅重命名账号目录并移出列表，保留全部文件", "The folder is renamed aside — not copied, not deleted")), keep(tr("可在“设置 › 通用”中恢复", "Restore it from Settings › General"))] },
    !a.isDefault && { id: 'delete', title: tr("删除全部数据", "Delete everything"), danger: true, tag: () => (bytes ? tf("释放 {0}", "frees {0}", fmtBytes(bytes)) : ''), lines: () => [lose(tf("删除账号目录、{0}及登录状态", "Erases the profile, {0} and the sign-in", sessions)), keep(tr("保留 ~/.claude 中共用的技能、设置和正文", "Your ~/.claude skills, settings and transcripts stay"))] }
  ].filter(Boolean)

  const radios = h('div', { class: 'stack', role: 'radiogroup', 'aria-label': tf("如何处理 {0}", "What to do with {0}", a.name) })
  const confirmInput = h('input', { class: 'text-input small', type: 'text', spellcheck: 'false', autocomplete: 'off', 'aria-label': tf("输入 {0} 以确认", "Type {0} to confirm", a.name), oninput: paint })
  const confirmBox = h('label', { class: 'confirm-box' }, h('span', {}, tr("输入 ", "Type "), h('strong', {}, a.name), tr(" 以确认，此操作不可撤销。", " to confirm. This cannot be undone.")), confirmInput)
  const go = h('button', { type: 'button', class: 'btn tall primary', onclick: run })
  const runningNote = h('div', { class: 'notice warn' }, icon('alert', 16, 2),
    h('span', {}, tf("{0} 正在运行，请先正常退出，再移动或删除目录。", "{0} is open. Quit it first — a profile cannot be moved or erased while Claude is using it.", a.name)))

  function paint () {
    radios.replaceChildren(...options.map(o => h('button', {
      type: 'button', role: 'radio', class: `radio-card${o.danger ? ' danger' : ''}`, 'aria-checked': String(o.id === choice),
      onclick: () => { choice = o.id; confirmInput.value = ''; paint() }
    }, h('span', { class: 'radio' }), h('span', { class: 'radio-body' },
      h('span', { class: 'radio-title' }, h('strong', {}, o.title), h('span', { class: 'help' }, typeof o.tag === 'function' ? o.tag() : o.tag)),
      o.lines()))))
    confirmBox.hidden = choice !== 'delete'
    const blocked = (choice === 'delete' && confirmInput.value.trim() !== a.name) || (choice !== 'forget' && a.running)
    runningNote.hidden = !(choice !== 'forget' && a.running)
    go.disabled = blocked
    go.className = `btn tall ${choice === 'delete' ? 'danger' : 'primary'}`
    go.textContent = { forget: tr("仅从列表移除", "Remove from list"), archive: tr("归档", "Archive"), delete: bytes ? tf("删除 {0}", "Delete {0}", fmtBytes(bytes)) : tr("删除", "Delete") }[choice]
  }

  async function run () {
    let res
    if (choice === 'archive') res = await api.archiveAccount(a.id)
    else res = await api.remove(a.id, choice === 'delete', confirmInput.value.trim())
    if (res.error) return toast(res.error, 'error')
    await refresh()
    show('list')
    toast(choice === 'archive' ? tf("已归档至 {0}，保留全部文件。", "Archived as {0}. Nothing was deleted.", res.movedTo) : choice === 'delete' ? tf("已删除 {0}", "{0} is deleted", a.name) : tf("已从列表移除 {0}", "{0} is off the list", a.name))
  }

  $view.replaceChildren(
    h('div', { class: 'scroll' },
      h('div', { class: 'identity' }, avatar(a, 40), h('div', {},
        h('h1', { class: 'h1 small' }, tf("移除“{0}”？", "Remove “{0}”?", a.name)),
        h('div', { class: 'help' }, a.isDefault ? tr("这是 Claude 默认目录，只能从列表移除。", "This is Claude’s own profile, so it can only leave the list.") : [a.running ? tr("运行中", "Running") : tr("未运行", "Not running"), a.sessions !== undefined ? plural(a.sessions, 'saved session') : ''].filter(Boolean).join(' · ')))),
      radios, runningNote, confirmBox),
    h('div', { class: 'sheet-foot' }, h('span', { class: 'grow' }),
      h('button', { type: 'button', class: 'btn tall', onclick: back }, tr("取消", "Cancel")), go))
  paint()
  if (!a.isDefault) api.size(a.id).then(r => { bytes = r.bytes; paint() })
}

// --------------------------------------------------------------- health ---

function viewHealth () {
  const d = S.health
  const body = h('div', { class: 'scroll' })
  const rescan = async () => { setHealth(await api.scanHealth()); if (S.view === 'health') viewHealth() }

  const head = h('div', { class: 'identity' }, h('h1', { class: 'h1 grow' }, tr("会话状态", "Session health")),
    h('span', { class: 'help' }, d?.at ? tf("{0}检查", "checked {0}", fmtAgo(d.at)) : ''),
    h('button', { type: 'button', class: 'icon-btn', 'aria-label': tr("重新检查", "Check again"), title: tr("重新检查", "Check again"), onclick: rescan }, icon('refresh', 16, 2)))

  if (!d) {
    body.append(head, h('p', { class: 'help' }, tr("正在检查…", "Checking…")))
    $view.replaceChildren(body)
    rescan()
    return
  }

  const failing = d.accounts.filter(a => a.failures && !a.failures.resolved)
  const lost = d.accounts.reduce((n, a) => n + a.orphans.length, 0)
  const summary = failing.length
    ? h('div', { class: 'notice danger', role: 'status' }, icon('shieldAlert', 18, 2), h('div', {},
      h('strong', {}, tf("以下账号无法保存会话：{0}", "Claude is not saving sessions for {0}", failing.map(a => a.name).join('、'))),
      tr("修复前，新会话可能只保存在内存中，重启后会丢失。", "Until this is fixed, new sessions live only in memory and vanish when Claude restarts.")))
    : lost
      ? h('div', { class: 'notice warn', role: 'status' }, icon('shieldAlert', 18, 2), h('div', {},
        h('strong', {}, tf("Claude 列表缺少 {0}", "{0} missing from Claude’s list", plural(lost, 'session'))),
        tr("正文仍在。恢复索引后，重启对应账号即可重新显示。", "The conversations survived. Restore the entries, then restart those accounts to see them again.")))
      : h('div', { class: 'notice ok', role: 'status' }, icon('shieldOk', 18, 2), h('div', {},
        h('strong', {}, tr("会话保存正常", "Everything is being saved")), tr("每 2 分钟检查一次，发现异常会提醒。", "Checked every 2 minutes. You are told the moment that changes.")))

  const sections = d.accounts.map(healthAccount)
  const unlogged = d.accounts.some(x => !x.logExists && !findAccount(x.id)?.isDefault)
  body.append(head, summary, ...sections,
    unlogged ? h('p', { class: 'help' }, tr("通过 Claude 日志查找遗失会话。新版通常仅默认目录有独立日志，其他目录主要检查备份和索引写入状态。", "Lost sessions are found through Claude’s log. Since Claude 1.34493.1 only the default profile keeps one of its own, so the others are checked for backups and a writable index only.")) : '')
  $view.replaceChildren(body)
}

function healthAccount (x) {
  const acct = findAccount(x.id) || { name: x.name }
  const failing = x.failures && !x.failures.resolved
  const notOpened = !x.storage.ok && x.storage.reason === 'no session index yet'
  const state = failing
    ? [h('span', { class: 'dot danger' }), tr("无法保存", "Not saving")]
    : notOpened ? [h('span', { class: 'dot off' }), tr("尚未打开", "Not opened yet")]
      : !x.storage.ok ? [h('span', { class: 'dot warn' }), tr("索引异常", "Index problem")]
        : [h('span', { class: 'dot' }), x.logExists ? tr("保存正常", "Saving normally") : tr("索引可写", "Index writable")]

  const notes = []
  if (failing) notes.push(tf("{0}，最近时间 {1}。原因：{2}", "{0}, the last at {1}. Cause: {2}", plural(x.failures.count, 'failed save'), x.failures.last, x.failures.reason || tr("未知", "unknown")))
  else if (x.failures) notes.push(tf("此前有 {0}，最近时间 {1}；现已恢复可写。", "Previously {0}, last seen {1}; writable again.", plural(x.failures.count, 'failed save'), x.failures.last))
  if (!x.storage.ok && !notOpened) notes.push(tf("索引状态：{0}。", "The {0}.", ({ 'the storage directory is a symlink, which the app refuses to write into': tr("目录为符号链接，已拒绝写入", "Refused to write to a symlinked folder"), 'the storage directory is not writable': tr("目录不可写", "Folder is not writable") })[x.storage.reason] || x.storage.reason))
  if (notOpened) notes.push(tr("此目录首次运行 Claude 后开始检查。", "Checks start once Claude has run on this profile."))

  const backups = x.backups.count
    ? h('span', { class: 'health-note' }, `${plural(x.backups.count, 'index backup')}`,
      h('button', { type: 'button', class: 'btn link', onclick: () => api.revealPath(x.backups.dir) }, tf("在{0}中显示", "Show in {0}", fileManager())))
    : ''

  return h('section', { class: 'health-account', 'aria-label': x.name },
    h('div', { class: 'health-head' }, avatar(acct, 24), h('span', { class: 'name grow' }, x.name), h('span', { class: 'status' }, state)),
    x.orphans.map(o => orphanCard(x, o)),
    notes.map(n => h('div', { class: 'health-note' }, n)),
    backups)
}

function orphanCard (x, o) {
  const btn = h('button', { type: 'button', class: 'btn primary', title: tr("重建遗失的会话索引，使会话重新显示", "Rebuild the entry Claude lost, so the session is listed again") }, tr("恢复", "Restore"))
  const card = h('div', { class: 'card orphan' },
    h('div', { class: 'card-body' },
      h('div', { class: 'label' }, o.title),
      h('div', { class: 'help' }, tf("{0} MB · 最近活动 {1}", "{0} MB · last active {1}", (o.bytes / 1e6).toFixed(1), fmtWhen(o.lastActivityAt))),
      h('div', { class: 'path' }, o.cwd || tr("未知目录", "unknown folder"))),
    btn)
  btn.addEventListener('click', async () => {
    btn.disabled = true
    btn.textContent = tr("正在恢复…", "Restoring…")
    const res = await api.rebuildSession(x.id, o)
    if (res.error) {
      btn.disabled = false
      btn.textContent = tr("恢复", "Restore")
      return toast(res.error, 'error')
    }
    card.classList.add('restored')
    btn.textContent = tr("已恢复", "Restored")
    toast(tf("已恢复，重启 {0} 即可显示", "Restored — restart {0} to see it", x.name))
  })
  return card
}

function setHealth (d) {
  S.health = { ...d, at: Date.now() }
  renderBar()
}

// ---------------------------------------------------------------- setup ---

async function viewSetup () {
  const found = S.discovered.map(f => ({ ...f, selected: true }))
  const go = h('button', { type: 'button', class: 'btn tall primary', onclick: () => finish(found.filter(f => f.selected && f.name.trim())) })
  const paintGo = () => {
    const n = found.filter(f => f.selected && f.name.trim()).length
    go.textContent = n ? tf("添加 {0}", "Add {0}", plural(n, 'account')) : tr("继续", "Continue")
  }
  const rows = found.map(f => h('div', { class: 'discovered' },
    h('input', { type: 'checkbox', checked: true, 'aria-label': tf("添加 {0}", "Add {0}", f.name), onchange: e => { f.selected = e.target.checked; paintGo() } }),
    h('div', { class: 'card-body' },
      h('input', { class: 'name-input', type: 'text', value: f.name, 'aria-label': tr("账号名称", "Account name"), spellcheck: 'false', oninput: e => { f.name = e.target.value; paintGo() } }),
      h('span', { class: 'path' }, f.dir + (f.isDefault ? tr(" · 默认目录", " · default") : '')))))
  const hotkey = (await api.getSettings()).hotkey

  $view.replaceChildren(
    h('div', { class: 'scroll' },
      appMark(52),
      h('h1', { class: 'h1' }, tr("一个入口，管理多个 Claude 账号", "Every Claude account, one keystroke away")),
      h('p', { class: 'help' }, tr("每个账号独立保存登录，所有账号共用 ~/.claude 中的技能、插件、设置和正文。", "Each account keeps its own sign-in. Your skills, plugins, settings and transcripts in ~/.claude are shared by all of them.")),
      found.length
        ? h('section', { class: 'stack', 'aria-label': tr("已发现账号目录", "Profiles found") },
          h('h2', { class: 'section-label' }, IS_WIN ? tr("在此电脑上发现", "Found on this PC") : tr("在此 Mac 上发现", "Found on this Mac")),
          h('div', { class: 'card' }, rows),
          h('p', { class: 'help' }, tr("名称仅用于显示，修改名称不会移动本地目录。", "Names are only labels — click one to rename it. Nothing on disk moves.")))
        : h('p', { class: 'help' }, tr("未发现 Claude 目录，可以先进入再添加账号。", "No Claude profiles here yet. Start empty and add one.")),
      h('div', { class: 'notice' }, h('span', { class: 'status' }, accelCaps(hotkey).map(c => h('span', { class: 'keycap small' }, c))),
        h('span', {}, tr("可在任意应用中调出 CASE，可在设置中修改。", "brings CASE up from any app. Change it in Settings.")))),
    h('div', { class: 'sheet-foot' },
      h('button', { type: 'button', class: 'btn quiet', onclick: () => finish([]) }, tr("暂不添加", "Start empty")),
      h('span', { class: 'grow' }), go))
  paintGo()
}

async function finish (picked) {
  await api.adopt(picked.map(f => ({ name: f.name.trim(), dir: f.dir })))
  await refresh()
}

// ---------------------------------------------------------------- data ---

/** Fold a fresh list into what was already probed, so nothing flickers. */
function mergeStatus (next) {
  const by = new Map(S.accounts.map(a => [a.id, a]))
  return next.map((a, idx) => {
    const old = by.get(a.id)
    return old
      ? { running: old.running, sessions: old.sessions, exists: old.exists, usage: old.usage, ...a, idx }
      : { ...a, idx }
  })
}

async function refresh () {
  const res = await api.list()
  if (res.needsSetup) {
    S.discovered = res.discovered
    if (S.view !== 'setup') show('setup')
    return
  }
  const archived = (res.archived || []).length
  const footerChanged = archived !== S.archivedCount
  S.archivedCount = archived
  S.chromeProfiles = res.chromeProfiles || []
  S.accounts = mergeStatus(res.accounts)
  if (S.view === 'setup' || S.view === null) show('list')
  else if (S.view === 'list') { if (footerChanged) viewList(); else renderRows() }
  pullStatus()
}

let pulling = false

async function pullStatus () {
  if (pulling) return
  pulling = true
  try {
    const status = await api.status()
    const by = new Map(status.map(s => [s.id, s]))
    let changed = false
    S.accounts = S.accounts.map(a => {
      const s = by.get(a.id)
      if (!s) return a
      if (a.running !== s.running || a.sessions !== s.sessions || a.exists !== s.exists || usageDisplayKey(a.usage) !== usageDisplayKey(s.usage)) changed = true
      return { ...a, ...s }
    })
    if (changed && S.view === 'list' && !S.panel) renderRows()
  } finally {
    pulling = false
  }
}

// ------------------------------------------------------------- keyboard ---

document.addEventListener('keydown', e => {
  if (S.panel) return S.panel.keydown(e)
  if (modDown(e) && e.key === ',') { e.preventDefault(); return api.openSettings() }
  if (S.view !== 'list') {
    if (e.key === 'Escape' && S.view !== 'setup') { e.preventDefault(); back() }
    return
  }
  const list = filtered()
  const a = list[S.sel]
  const key = e.key.toLowerCase()

  if (modDown(e) && /^[1-9]$/.test(e.key)) {
    const target = S.accounts[Number(e.key) - 1]
    if (target) { e.preventDefault(); launch(target) }
  } else if (modDown(e) && key === 'k') { e.preventDefault(); openPanel() }
  else if (modDown(e) && key === 'n') { e.preventDefault(); show('account', { isNew: true }) }
  else if (a && modDown(e) && e.shiftKey && e.code === 'KeyR') { e.preventDefault(); api.reveal(a.id) }
  else if (a && modDown(e) && e.altKey && e.code === 'KeyQ') { e.preventDefault(); if (a.running) quit(a) }
  else if (a && modDown(e) && key === 'e') { e.preventDefault(); show('account', { id: a.id }) }
  else if (a && modDown(e) && key === 'g') { e.preventDefault(); show('account', { id: a.id, focus: 'chrome' }) }
  else if (a && modDown(e) && e.key === 'Backspace') { e.preventDefault(); show('remove', { id: a.id }) }
  else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault()
    if (list.length) { S.sel = (S.sel + (e.key === 'ArrowDown' ? 1 : -1) + list.length) % list.length; paintSel() }
  } else if (e.key === 'Enter' && a) { e.preventDefault(); launch(a) }
  else if (e.key === 'Escape') {
    e.preventDefault()
    if (S.query) { S.query = ''; searchInput.value = ''; renderRows() } else api.hide()
  }
})

// ---------------------------------------------------------------- start ---

api.onAccountsChanged(() => refresh())
api.onHealthUpdate(d => { setHealth(d); if (S.view === 'health') viewHealth() })
api.onShowHealth(() => show('health'))
api.onNewAccount(() => show('account', { isNew: true }))
api.onUpdateAvailable(info => toast(tf("自有仓库已发布 CASE {0}，请查看设置 → 更新。", "CASE {0} is available in our repository. See Settings › Updates.", info.version)))

document.addEventListener('case-language-changed', () => {
  if (!S.view) return
  renderBar()
  if (rerenderEditor) rerenderEditor()
  else VIEWS[S.view](S.arg || {})
})

;(async () => {
  S.claude = await api.claudeInstalled()
  await refresh()
  api.scanHealth().then(d => { setHealth(d); if (S.view === 'health') viewHealth() }).catch(() => {})
  // Keep running state honest while the window is in front.
  setInterval(() => {
    if (!document.hidden && S.view === 'list' && !S.panel) pullStatus()
  }, 2500)
})()
