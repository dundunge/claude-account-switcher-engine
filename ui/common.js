'use strict'

// Shared by the launcher (launcher.js) and the settings window (settings.js):
// building DOM, icons, avatars, formatting, toasts, and the platform's way of
// writing a shortcut.

const api = window.api
const IS_WIN = api.platform === 'win32'
document.documentElement.classList.toggle('win', IS_WIN)

const fileManager = () => IS_WIN ? tr("文件资源管理器", "Explorer") : tr("访达", "Finder")

/** Whether the platform's command key is down: Ctrl on Windows, Command on a Mac. */
const modDown = e => (IS_WIN ? e.ctrlKey : e.metaKey)

/** A shortcut as this platform writes one: ⇧⌘R on a Mac, Ctrl+Shift+R on Windows. */
function combo (key, { shift = false, alt = false } = {}) {
  if (IS_WIN) return ['Ctrl', alt && 'Alt', shift && 'Shift', key].filter(Boolean).join('+')
  return (alt ? '⌥' : '') + (shift ? '⇧' : '') + '⌘' + key
}

// ------------------------------------------------------------------- dom ---

// Properties rather than attributes, for the few where the two differ.
const PROPS = new Set(['value', 'checked', 'disabled', 'selected'])

/**
 * h('div', { class: 'x', onclick }, child, …). Children may be strings, nodes,
 * arrays or nothing. Styles go through the CSSOM — the page's security policy
 * refuses inline style attributes, which is also why `--custom` properties are
 * set with setProperty.
 */
function h (tag, props, ...kids) {
  const el = document.createElement(tag)
  for (const [k, v] of Object.entries(props || {})) {
    if (v === undefined || v === null || v === false) continue
    if (k.startsWith('on')) el.addEventListener(k.slice(2), v)
    else if (k === 'class') el.className = v
    else if (k === 'style') {
      for (const [p, val] of Object.entries(v)) {
        if (p.startsWith('--')) el.style.setProperty(p, String(val))
        else el.style[p] = val
      }
    } else if (k === 'dataset') Object.assign(el.dataset, v)
    else if (PROPS.has(k)) el[k] = v
    else el.setAttribute(k, v === true ? '' : String(v))
  }
  el.append(...kids.flat(Infinity).filter(k => k !== null && k !== undefined && k !== false))
  return el
}

const SVG_NS = 'http://www.w3.org/2000/svg'

function svg (tag, attrs) {
  const el = document.createElementNS(SVG_NS, tag)
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v))
  return el
}

const ICONS = {
  search: 'M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM20 20l-3.5-3.5',
  sliders: 'M4 7h10M20 7h0M4 17h4M14 17h6M18 7a2 2 0 1 1-4 0 2 2 0 0 1 4 0zM12 17a2 2 0 1 1-4 0 2 2 0 0 1 4 0z',
  shield: 'M12 3 5 6v5c0 4.4 3 8.3 7 10 4-1.7 7-5.6 7-10V6z',
  shieldAlert: 'M12 3 5 6v5c0 4.4 3 8.3 7 10 4-1.7 7-5.6 7-10V6zM12 9v4M12 16.5v.01',
  shieldOk: 'M12 3 5 6v5c0 4.4 3 8.3 7 10 4-1.7 7-5.6 7-10V6zM9 12l2 2 4-4',
  plus: 'M12 5v14M5 12h14',
  archive: 'M4 4h16v5H4zM5 9v10a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V9M10 13h4',
  open: 'M9 10 4 15l5 5M20 4v7a4 4 0 0 1-4 4H4',
  globe: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM3 12h18M12 3c2.5 2.5 3.8 5.5 3.8 9s-1.3 6.5-3.8 9c-2.5-2.5-3.8-5.5-3.8-9S9.5 5.5 12 3z',
  power: 'M12 3v8M6.4 6.6a8 8 0 1 0 11.2 0',
  pencil: 'M4 20h4L19 9l-4-4L4 16zM13.5 6.5l4 4',
  folder: 'M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z',
  trash: 'M4 7h16M10 11v6M14 11v6M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12M9 7V4h6v3',
  back: 'm15 18-6-6 6-6',
  check: 'm5 12 5 5L20 7',
  cross: 'M6 6l12 12M18 6 6 18',
  refresh: 'M20 12a8 8 0 1 1-2.3-5.6M20 4v4h-4',
  keyboard: 'M3 7a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM7 9.5h.01M11 9.5h.01M15 9.5h.01M7 13h.01M17 13h.01M8 16h8',
  window: 'M3 5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM3 8h18M8 17h8',
  download: 'M12 3v12m0 0-4-4m4 4 4-4M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2',
  info: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 11v5M12 8v.01',
  alert: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 8v5M12 16v.01',
  // The account icons.
  briefcase: 'M5 7h14a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2zM8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M3 13h18',
  home: 'M3 11 12 4l9 7M5 10v10h14V10M10 20v-5h4v5',
  flask: 'M9 3h6M10 3v6L4.5 18.5A1.5 1.5 0 0 0 5.8 21h12.4a1.5 1.5 0 0 0 1.3-2.5L14 9V3',
  code: 'm8 7-5 5 5 5M16 7l5 5-5 5M13.5 4l-3 16',
  rocket: 'M5 15c-1.5 1.3-2 3.5-2 6 2.5 0 4.7-.5 6-2M9 13l2 2M14.5 4.5C17 2 21 3 21 3s1 4-1.5 6.5L13 16l-5-5zM15 9h.01',
  book: 'M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2zM4 19a2 2 0 0 1 2-2h13',
  lock: 'M6 11h12v10H6zM8 11V7a4 4 0 0 1 8 0v4'
}

/** The line icons an account can wear, in the picker's order. */
const GLYPHS = ['briefcase', 'home', 'flask', 'code', 'rocket', 'book', 'lock']

function icon (name, size = 16, stroke = 1.8) {
  const s = svg('svg', {
    width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor',
    'stroke-width': stroke, 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true'
  })
  s.append(svg('path', { d: ICONS[name] }))
  return s
}

/** The CASE mark: two tiles, as on the app icon. */
function appMark (size) {
  const box = h('span', { class: 'app-mark', 'aria-hidden': 'true', style: { width: `${size}px`, height: `${size}px`, borderRadius: `${Math.round(size * 0.25)}px` } })
  const s = svg('svg', { width: Math.round(size * 0.64), height: Math.round(size * 0.64), viewBox: '0 0 32 32', fill: 'none', stroke: '#D95F32', 'stroke-width': 2.2, 'stroke-linejoin': 'round' })
  s.append(svg('rect', { x: 4, y: 4, width: 15, height: 15, rx: 3.6, fill: '#FFF4EC' }), svg('rect', { x: 13, y: 13, width: 15, height: 15, rx: 3.6, fill: '#FFF4EC' }))
  box.append(s)
  return box
}

// --------------------------------------------------------------- avatars ---

function nameHue (name) {
  let hue = 0
  for (let i = 0; i < name.length; i++) hue = (hue * 31 + name.charCodeAt(i)) % 360
  return hue
}

const hueOf = a => (Number.isFinite(a.hue) ? a.hue : nameHue(a.name || ''))

function initials (name) {
  const parts = String(name).trim().split(/[\s\-_·]+/).filter(Boolean)
  if (!parts.length) return '?'
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase()
  return (parts[0][0] + parts[1][0]).toUpperCase()
}

/** An account's badge: its line icon, a legacy emoji, or its initials, on its colour. */
function avatar (a, size = 36, live = false) {
  const el = h('span', {
    class: 'avatar',
    'aria-hidden': 'true',
    style: { width: `${size}px`, height: `${size}px`, borderRadius: `${Math.round(size * 0.28)}px`, fontSize: `${Math.round(size * 0.36)}px`, '--h': hueOf(a) }
  })
  if (GLYPHS.includes(a.icon)) el.append(icon(a.icon, Math.round(size * 0.5)))
  else if (a.emoji) el.append(h('span', { style: { fontSize: `${Math.round(size * 0.5)}px` } }, a.emoji))
  else el.append(initials(a.name || ''))
  if (live) el.append(h('span', { class: 'live' }))
  return el
}

// ----------------------------------------------------------------- usage ---

const level = p => (p >= 90 ? 'danger' : p >= 75 ? 'warn' : 'accent')
const usageAgeMs = u => u ? Math.max(0, Date.now() - u.at) : 0
const usageDisplayKey = u => u ? [u.at, u.fh, u.sd, u.source, u.fhResetAt, u.sdResetAt, u.fhResetText, u.sdResetText, Math.floor(usageAgeMs(u) / 60000)].join('/') : ''
const usageIsOld = u => u && usageAgeMs(u) > 10 * 60000
const usageAge = u => u ? (u.source === 'claude-ui'
  ? tf('{0}读取 · Claude 面板', 'Claude panel · read {0}', fmtAgo(u.at))
  : tf('{0}采样 · 本地', 'Local · {0}', fmtAgo(u.at))) + (usageIsOld(u) ? tr(' · 旧数据', ' · stale') : '') : tr('暂无额度记录', 'No usage data')

function usageTip (u) {
  if (!u) return tr("暂无额度记录，待 Claude 生成本地采样。", "No quota sample has been recorded by Claude yet.")
  return tf('5 小时已用 {0}；周额度已用 {1}。{2}。百分比表示已使用的额度。', '5-hour usage: {0}; weekly usage: {1}. {2}. Percentages show used quota.',
    Number.isFinite(u.fh) ? `${u.fh}%` : tr('未知', 'unknown'), Number.isFinite(u.sd) ? `${u.sd}%` : tr('未知', 'unknown'), usageAge(u))
}

function quotaLine (label, value) {
  const known = Number.isFinite(value)
  const tone = known && value >= 75 ? level(value) : ''
  return h('span', { class: 'quota-line' },
    h('span', { class: 'quota-top' }, h('span', {}, label),
      h('b', { class: tone }, known ? tf("已用 {0}%", "{0}% used", value) : tr("未获取", "Unknown"))),
    h('span', { class: `bar ${tone}`, role: 'progressbar', 'aria-label': tf("{0}已用", "{0} used", label),
      'aria-valuenow': known ? value : null, 'aria-valuemin': 0, 'aria-valuemax': 100,
      'aria-valuetext': known ? tf("已用 {0}%，剩余 {1}%", "{0}% used, {1}% remaining", value, 100 - value) : tr("未获取", "Unknown") },
    h('span', { style: { width: `${known ? value : 0}%` } })))
}

/** Explicit labels and values: neither limit is hidden behind a combined peak. */
function usageSummary (u) {
  return h('span', { class: `usage-summary${usageIsOld(u) ? ' old' : ''}`, title: usageTip(u) },
    quotaLine(tr("5 小时", "5 hours"), u?.fh), quotaLine(tr("周额度", "Weekly"), u?.sd),
    h('span', { class: 'usage-age' }, usageAge(u)))
}

// ------------------------------------------------------------ formatting ---

const plural = (n, word) => I18N.language === 'en' ? `${n} ${word}${n === 1 ? '' : 's'}` : `${n} ${{ session: tr("条会话", "sessions"), 'saved session': tr("条已保存会话", "saved sessions"), account: tr("个账号", "accounts"), profile: tr("个目录", "profiles"), 'failed save': tr("次保存失败", "failed saves"), 'index backup': tr("份索引备份", "index backups") }[word] || word}`

/** Decimal units, to match what Finder reports for the same folder. */
function fmtBytes (bytes) {
  if (!Number.isFinite(bytes)) return tr("大小未知", "unknown size")
  const units = ['bytes', 'KB', 'MB', 'GB', 'TB']
  let n = bytes
  let i = 0
  while (n >= 1000 && i < units.length - 1) { n /= 1000; i++ }
  return `${i === 0 ? n : n.toFixed(n < 10 ? 1 : 0)} ${units[i]}`
}

/** Coarse on purpose: "when did I last use this" needs no more than this. */
function fmtAgo (ms) {
  const mins = (Date.now() - ms) / 60000
  if (!(mins >= 0)) return ''
  if (mins < 1) return tr("刚刚", "just now")
  if (mins < 60) return tf("{0} 分钟前", "{0} min ago", Math.round(mins))
  if (mins < 60 * 24) return tf("{0} 小时前", "{0} h ago", Math.round(mins / 60))
  if (mins < 60 * 24 * 7) return tf("{0} 天前", "{0} d ago", Math.round(mins / 1440))
  return new Date(ms).toLocaleDateString(I18N.language, { month: 'short', day: 'numeric' })
}

function fmtWhen (ms) {
  const d = new Date(ms)
  return d.toLocaleDateString(I18N.language, { month: 'short', day: 'numeric' }) + ', ' +
    d.toLocaleTimeString(I18N.language, { hour: '2-digit', minute: '2-digit' })
}

// ------------------------------------------------------------- controls ---

/** A switch. `onchange(next)` may answer the state that actually took hold. */
function toggle (label, on, onchange, { disabled = false } = {}) {
  const b = h('button', { type: 'button', class: 'switch', role: 'switch', 'aria-checked': String(Boolean(on)), 'aria-label': label, disabled },
    h('span', { class: 'track' }, h('span', { class: 'knob' })))
  b.addEventListener('click', async () => {
    const next = b.getAttribute('aria-checked') !== 'true'
    b.setAttribute('aria-checked', String(next))
    const res = await onchange(next)
    if (typeof res === 'boolean') b.setAttribute('aria-checked', String(res))
  })
  return b
}

/** A segmented choice between a few values. */
function segmented (label, options, value, onpick) {
  const seg = h('div', { class: 'seg', role: 'radiogroup', 'aria-label': label })
  for (const [v, text] of options) {
    seg.append(h('button', {
      type: 'button',
      role: 'radio',
      'aria-checked': String(v === value),
      onclick: () => {
        for (const b of seg.children) b.setAttribute('aria-checked', 'false')
        seg.children[options.findIndex(o => o[0] === v)].setAttribute('aria-checked', 'true')
        onpick(v)
      }
    }, text))
  }
  return seg
}

/** One row of a settings card: what it is on the left, its control on the right. */
function settingRow (label, help, ...controls) {
  return h('div', { class: 'card-row' },
    h('div', { class: 'card-body' }, h('div', { class: 'label' }, label), help ? h('div', { class: 'help' }, help) : null),
    ...controls)
}

// ----------------------------------------------------------------- toast ---

let toastEl = null
let toastTimer = null

/** A short confirmation at the bottom of the window. Never moves anything else. */
function toast (msg, kind = 'info') {
  if (!toastEl) {
    toastEl = h('div', { class: 'toast', role: 'status', 'aria-live': 'polite' })
    document.body.append(toastEl)
  }
  toastEl.className = `toast ${kind}`
  toastEl.replaceChildren(h('span', { class: 'dot' }), h('span', {}, msg))
  requestAnimationFrame(() => toastEl.classList.add('show'))
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => toastEl.classList.remove('show'), kind === 'error' ? 6000 : 2600)
}

// -------------------------------------------------------------- shortcuts ---

const KEY_SYMBOL = { Control: '⌃', Alt: '⌥', Shift: '⇧', Command: '⌘' }
const KEY_WORD = { Control: 'Ctrl', Alt: 'Alt', Shift: 'Shift', Super: 'Win' }

/** A stored accelerator ("Alt+Command+C") as the keycaps this platform draws. */
function accelCaps (accel) {
  if (!accel) return []
  const parts = accel.split('+')
  const key = parts.pop()
  const shown = key.length === 1 ? key.toUpperCase() : key
  if (IS_WIN) {
    return [...['Control', 'Alt', 'Shift', 'Super'].filter(m => parts.includes(m)).map(m => KEY_WORD[m]), shown]
  }
  return [...['Control', 'Alt', 'Shift', 'Command'].filter(m => parts.includes(m)).map(m => KEY_SYMBOL[m]), shown]
}

const prettyAccel = accel => (IS_WIN ? accelCaps(accel).join('+') : accelCaps(accel).join(''))

/** The modifiers held down in a keyboard event, as caps. */
function heldCaps (e) {
  const held = []
  if (e.ctrlKey) held.push(IS_WIN ? 'Ctrl' : '⌃')
  if (e.altKey) held.push(IS_WIN ? 'Alt' : '⌥')
  if (e.shiftKey) held.push(IS_WIN ? 'Shift' : '⇧')
  if (e.metaKey) held.push(IS_WIN ? 'Win' : '⌘')
  return held
}

/**
 * The accelerator a keydown spells, or null until a usable one lands. Meta is
 * the Windows key there, which the shortcut library calls Super; Command would
 * be refused.
 */
function accelFromEvent (e) {
  const mods = []
  if (e.ctrlKey) mods.push('Control')
  if (e.altKey) mods.push('Alt')
  if (e.shiftKey) mods.push('Shift')
  if (e.metaKey) mods.push(IS_WIN ? 'Super' : 'Command')
  if (['Meta', 'Alt', 'Shift', 'Control'].includes(e.key)) return null

  const code = e.code || ''
  let key = null
  if (code.startsWith('Key')) key = code.slice(3)
  else if (code.startsWith('Digit')) key = code.slice(5)
  else if (e.key === ' ') key = 'Space'
  else if (e.key.startsWith('Arrow')) key = e.key.slice(5)
  else if (/^F[0-9]{1,2}$/.test(e.key)) key = e.key
  if (!key) return null
  return { accel: [...mods, key].join('+'), bare: mods.length === 0 }
}

api.onLanguageChanged(language => {
  I18N.set(language)
  document.dispatchEvent(new Event('case-language-changed'))
})
