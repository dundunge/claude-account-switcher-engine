'use strict'

// The launcher and Settings share manual-query behavior. Creating a controller
// or rendering a card never starts a query; only explicit button handlers do.
const USAGE_STATUSES = new Set(['success', 'auth_failed', 'unavailable', 'in_progress', 'failed', 'timeout'])

function quotaError (code) {
  const messages = {
    CLI_NOT_INSTALLED: tr('请先安装官方 Claude Code，再为各账号连接独立额度查询。', 'Install official Claude Code, then connect usage queries for each account.'),
    PYTHON_NOT_INSTALLED: tr('未找到系统 Python 3，暂时无法查询。', 'System Python 3 was not found; usage cannot be queried.'),
    CLI_AUTH_REQUIRED: tr('请在官方登录窗口完成该账号的授权，然后点击查询。', 'Complete this account’s authorization in the official login window, then click Query.'),
    CLI_IDENTITY_MISMATCH: tr('额度登录与此账号的 account/org 不一致，已停止查询。请连接正确账号。', 'The usage login does not match this account and organization. Queries stopped; connect the matching account.'),
    CLI_IDENTITY_UNAVAILABLE: tr('无法确认额度登录的账号身份，请重新连接。', 'The usage login identity could not be confirmed. Reconnect this account.'),
    CLI_AUTH_STATUS_INVALID: tr('无法识别官方登录状态，本次额度未获取。', 'The official login status was not recognized; current usage was not obtained.'),
    CLI_SETUP_REQUIRED: tr('请点击“打开独立 CLI”，在终端完成官方 CLI 设置；查看 /usage 后输入 /exit，再点击查询额度。', 'Click Open account CLI, complete the official CLI setup in Terminal, view /usage, enter /exit, then click Query usage.'),
    CLI_WORKSPACE_TRUST_REQUIRED: tr('授权有效，但官方 CLI 需要首次确认查询目录。点击“打开独立 CLI”，核对目录并确认信任，查看 /usage 后输入 /exit，再点击查询额度。', 'Authorization is valid, but the official CLI needs first-time folder trust. Click Open account CLI, verify and trust the query folder, view /usage, enter /exit, then click Query usage.'),
    CLI_RATE_LIMITED: tr('官方查询触发限流，请稍后手动重试。', 'Official usage queries are rate limited. Try again manually later.'),
    CLI_USAGE_UNAVAILABLE: tr('官方 CLI 暂时无法获取额度。可打开独立 CLI 查看 /usage，或稍后手动重试；无需先重新授权。', 'The official CLI cannot fetch usage right now. Open the account CLI to view /usage, or try again manually later; reauthorization is not required first.'),
    CLI_USAGE_UNRECOGNIZED: tr('未识别到官方 /usage 的完整读数，本次额度未获取。', 'A complete official /usage reading was not recognized; current usage was not obtained.'),
    CLI_SCOPE_INVALID: tr('额度配置目录无法确认，已停止查询。', 'The usage configuration directory could not be verified. Queries stopped.'),
    CLI_OUTPUT_LIMIT: tr('官方 CLI 输出异常，本次额度未获取。', 'The official CLI returned unexpected output; current usage was not obtained.'),
    CLI_PROBE_FAILED: tr('额度查询失败，请手动重试。', 'Usage query failed. Try again manually.'),
    CLI_TERMINAL_FAILED: tr('无法打开独立 CLI 终端，请重试。', 'Could not open the account CLI terminal. Try again.'),
    AUTH_BROWSER_NOT_INSTALLED: tr('独立授权窗口的 Electron 组件未安装，请修复本机 CASE 安装。', 'The Electron component for isolated authorization is missing. Repair the local CASE installation.'),
    AUTH_BROWSER_FAILED: tr('无法打开独立授权窗口，请关闭此账号未完成的授权后重试。', 'The isolated authorization window could not open. Close this account’s unfinished authorization and retry.'),
    AUTH_BROWSER_URL_INVALID: tr('官方授权链接无法确认，已停止打开登录窗口。', 'The official authorization URL could not be verified. The login window was not opened.'),
    AUTH_IN_PROGRESS: tr('此账号已有独立授权正在进行，请先完成或关闭该授权。', 'This account already has an authorization in progress. Finish or close it first.'),
    MONITOR_CONFIG_INVALID: tr('额度查询配置无法读取，请检查本地目录。', 'Usage query settings could not be read. Check the local directory.'),
    QUERY_NOT_RUN: tr('尚未查询。完成授权后点击查询。', 'Not queried yet. Complete authorization, then click Query.'),
    REFRESH_FAILED: tr('查询通信失败，本次额度未获取。请重试。', 'The query connection failed; current usage was not obtained. Try again.'),
    REFRESH_IN_PROGRESS: tr('另一次面板读取正在进行，本次额度未获取。请稍后手动重试。', 'Another panel read is in progress; this attempt did not obtain usage. Try again manually later.'),
    REFRESH_TIMEOUT: tr('额度查询超时，本次额度未获取。请稍后重试。', 'The usage query timed out; current usage was not obtained. Try again later.'),
    TIMEOUT: tr('额度查询超时，本次额度未获取。请稍后重试。', 'The usage query timed out; current usage was not obtained. Try again later.'),
    ACCOUNT_NOT_FOUND: tr('未找到该账号，本次额度未获取。请刷新账号列表后重试。', 'The account was not found; current usage was not obtained. Refresh the account list and try again.'),
    ACCESSIBILITY_REQUIRED: tr('请在系统设置 → 隐私与安全性 → 辅助功能中允许 CASE 读取 Claude 界面，然后重试。此权限只需为 CASE 配置一次，各账号不用重复登录。', 'Allow CASE in System Settings → Privacy & Security → Accessibility, then retry. This is one permission for CASE; accounts need no extra sign-in.'),
    ACCOUNT_NOT_OPEN: tr('此账号未打开，当前额度未获取。切换到它后可读取。', 'This account is closed; current usage was not obtained. Open it to read usage.'),
    DESKTOP_IDENTITY_MISSING: tr('请先在这个账号的 Claude 窗口完成登录。', 'Sign in to this account in Claude first.'),
    IDENTITY_CHANGED: tr('读取期间登录账号发生变化，本次读数未保存。请重试。', 'The signed-in account changed during the read. This observation was not saved. Try again.'),
    PROFILE_PROCESS_AMBIGUOUS: tr('无法唯一确认该账号的 Claude 进程，已停止读取。', 'Could not uniquely identify this account’s Claude process. Reading stopped.'),
    PANEL_UNAVAILABLE: tr('未找到 Claude 的额度入口。请先打开 Claude 的 Code 会话及右下角 Usage 面板，再重试。', 'Claude’s usage control was not found. Open a Code session and its Usage panel at the bottom right, then retry.'),
    PANEL_UNRECOGNIZED: tr('未识别到额度面板中的数字，本次额度未获取。请在 Claude 中查看额度。', 'The quota panel’s numbers were not recognized; current usage was not obtained. View usage in Claude.'),
    SAVE_FAILED: tr('无法保存面板读数，请检查本地磁盘。', 'Could not save the panel observation. Check the local disk.'),
    PLATFORM_UNSUPPORTED: tr('Claude 面板读取目前只支持 macOS。', 'Reading Claude’s panel currently supports macOS only.')
  }
  return (messages[code] || tr('未能获取当前额度。', 'Current usage could not be obtained.')) + ` (${code})`
}

function quotaClockHint () {
  return h('p', { class: 'help quota-hint' }, tf('百分比为已用额度；时间按 {0} 显示。悬停可查看完整重置日期和剩余比例。', 'Percentages show used quota; times use {0}. Hover for the full reset date and remaining quota.', Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'))
}

function createQuotaController ({ getAccounts, onChange }) {
  const quotaRequests = new Map()
  let refreshingAll = false
  const findAccount = id => getAccounts().find(account => account.id === id)
  const paintQuota = id => onChange(id)

  function state (account) {
    const request = quotaRequests.get(account.id) || {}
    const monitor = account.monitor || {}
    const pending = Boolean(request.pending || (monitor.status === 'in_progress' && monitor.code !== 'REFRESH_IN_PROGRESS'))
    const status = pending ? 'in_progress' : request.status || monitor.status || 'unavailable'
    const code = pending ? null : request.code || monitor.code
    const label = code === 'QUERY_NOT_RUN' && !pending ? tr('尚未查询', 'Not queried yet') : {
      success: tr('已更新', 'Updated'), auth_failed: tr('需要授权', 'Authorization required'),
      unavailable: tr('暂不可用', 'Unavailable'), in_progress: tr('查询中…', 'Querying…'),
      failed: tr('查询失败', 'Query failed'), timeout: tr('查询超时', 'Query timed out')
    }[status] || tr('暂不可用', 'Unavailable')
    return { pending, status, code, label }
  }

  function usageCard (account, named = false) {
    const u = account.usage
    const monitor = account.monitor || {}
    const { pending, status, code, label: statusText } = state(account)
    const blocked = pending || refreshingAll
    return h('section', { class: 'card usage-card', dataset: { usageId: account.id, named: String(named) }, 'aria-label': named ? account.name : tr('订阅额度', 'Plan usage') },
      h('div', { class: 'usage-line' }, h('div', { class: 'top' }, h('b', {}, named ? account.name : tr('订阅额度', 'Plan usage')), h('span', { class: 'help' }, usageAge(u)))),
      quotaWindow(tr('5 小时额度', 'Session limit · 5 hr'), u, 'fh'),
      quotaWindow(tr('每周 · 所有模型', 'Weekly · all models'), u, 'sd'),
      !named ? quotaClockHint() : null,
      h('p', { class: 'help', role: 'status', title: status }, statusText, !u && monitor.lastAttemptAt ? ` · ${tf('上次查询 {0}', 'Last attempt {0}', fmtAgo(monitor.lastAttemptAt))}` : ''),
      !monitor.configured ? h('p', { class: 'help' }, tr('授权后可查询未打开的账号；也可读取已打开账号的面板。', 'Connect usage queries for closed accounts, or read an open account’s panel.')) : null,
      h('div', { class: 'quota-actions' },
        h('button', { type: 'button', class: 'btn primary', disabled: blocked || (!monitor.configured && !account.running), onclick: () => refreshQuota(account.id) }, pending ? tr('查询中…', 'Querying…') : monitor.configured ? tr('查询额度', 'Query usage') : tr('从面板读取', 'Read panel')),
        monitor.configured ? h('button', { type: 'button', class: 'btn', disabled: blocked, onclick: () => configureMonitor(account.id, true) }, tr('打开独立 CLI', 'Open account CLI')) : '',
        h('button', { type: 'button', class: 'btn', disabled: blocked, onclick: () => configureMonitor(account.id) }, monitor.configured ? tr('重新授权', 'Reconnect') : tr('授权独立查询', 'Connect usage queries')),
        !monitor.configured ? h('button', { type: 'button', class: 'btn', disabled: blocked, onclick: () => refreshQuota(account.id, false) }, tr('重读本地记录', 'Read saved records')) : '',
        code === 'ACCESSIBILITY_REQUIRED' ? h('button', { type: 'button', class: 'btn quiet', onclick: () => api.openLink('accessibility') }, tr('打开辅助功能设置', 'Open Accessibility settings')) : '',
        code === 'CLI_NOT_INSTALLED' ? h('button', { type: 'button', class: 'btn quiet', onclick: () => api.openLink('claude-code') }, tr('官方安装说明', 'Official installation guide')) : ''),
      code ? h('p', { class: `help ${code === 'QUERY_NOT_RUN' ? '' : 'warn'}`, role: 'status' }, quotaError(code)) : '')
  }

  async function configureMonitor (id, openCli = false) {
    if (quotaRequests.get(id)?.pending) return
    quotaRequests.set(id, { pending: true })
    paintQuota(id)
    try {
      const result = await (openCli ? api.setupUsageMonitor(id) : api.connectUsageMonitor(id))
      if (!result?.ok) {
        quotaRequests.set(id, { code: result?.code || 'REFRESH_FAILED', status: result?.status || 'failed' })
        toast(quotaError(result?.code || 'REFRESH_FAILED'), 'error')
      } else {
        const account = findAccount(id)
        if (account) Object.assign(account, mergeQuotaStatus(account, { monitor: result.monitor, usage: result.monitor.usage }))
        quotaRequests.delete(id)
        toast(openCli
          ? tr('已打开此账号的独立 CLI，复用现有授权。可查看 /usage；完成后输入 /exit。', 'Opened this account’s CLI with its existing authorization. View /usage, then enter /exit when finished.')
          : tr('官方登录已启动，将打开此账号的独立 Electron 授权窗口。完成登录及 /usage 设置后，请手动点击查询。', 'Official login started and will open this account’s isolated Electron authorization window. Finish login and /usage setup, then click Query manually.'))
      }
    } catch {
      quotaRequests.set(id, { code: 'REFRESH_FAILED', status: 'failed' })
    }
    paintQuota(id)
  }

  async function refreshQuota (id, native = true) {
    if (quotaRequests.get(id)?.pending) return
    const startedAt = Date.now()
    quotaRequests.set(id, { pending: true, status: 'in_progress', startedAt })
    const before = findAccount(id)
    if (before) before.usage = null
    paintQuota(id)
    try {
      const result = await api.refreshUsage(id, native)
      const account = findAccount(id)
      const status = USAGE_STATUSES.has(result?.status)
        ? result.status
        : (result?.ok === true ? 'success' : 'failed')
      const ok = result?.ok === true && status === 'success'
      if (account) Object.assign(account, mergeQuotaStatus(account, { usage: ok ? (result.usage || null) : null, ...(result?.monitor ? { monitor: result.monitor } : {}) }))
      const code = ok
        ? null
        : result?.code || (status === 'timeout' ? 'REFRESH_TIMEOUT' : 'REFRESH_FAILED')
      if (result?.monitor) quotaRequests.delete(id)
      else quotaRequests.set(id, { pending: false, status, code, bridgeFailed: !ok, startedAt })
    } catch (error) {
      const account = findAccount(id)
      if (account) account.usage = null
      const timeout = error?.code === 'TIMEOUT' || error?.code === 'REFRESH_TIMEOUT'
      quotaRequests.set(id, { pending: false, code: timeout ? 'REFRESH_TIMEOUT' : 'REFRESH_FAILED', status: timeout ? 'timeout' : 'failed', bridgeFailed: true, startedAt })
    }
    paintQuota(id)
    return state(findAccount(id) || { id })
  }

  async function refreshAll () {
    if (refreshingAll || getAccounts().some(account => state(account).pending)) return
    // Snapshot IDs once so editing or sorting the list cannot query an account
    // twice. Each account is queried serially; failures do not trigger retries.
    const ids = getAccounts().map(account => account.id)
    if (!ids.length) return
    refreshingAll = true
    paintQuota()
    let succeeded = 0
    let unavailable = 0
    let pending = 0
    try {
      for (const id of ids) {
        if (!findAccount(id)) continue
        let result = await refreshQuota(id)
        // A different window may have started this query after our last
        // status poll. Join its existing attempt without issuing another one.
        if (result?.status === 'in_progress') result = await waitForQuota(id)
        if (!findAccount(id)) continue
        if (result?.status === 'success') succeeded++
        else if (result?.status === 'in_progress') pending++
        else unavailable++
      }
    } finally {
      refreshingAll = false
      paintQuota()
    }
    toast(pending
      ? tf('额度刷新：{0} 个成功，{1} 个未获取，{2} 个仍在查询。请查看账号状态。', 'Usage refresh: {0} succeeded, {1} unavailable, {2} still querying. Check account status.', succeeded, unavailable, pending)
      : unavailable
      ? tf('额度刷新完成：{0} 个成功，{1} 个未获取。详情见设置 → 额度查询。', 'Usage refresh finished: {0} succeeded, {1} unavailable. See Settings › Usage queries.', succeeded, unavailable)
      : tf('已刷新 {0} 个账号的额度。', 'Refreshed usage for {0} accounts.', succeeded), unavailable ? 'error' : 'info')
    return { succeeded, unavailable, pending }
  }

  async function waitForQuota (id) {
    const deadline = Date.now() + 60000
    while (findAccount(id)) {
      try {
        const incoming = (await api.status()).find(account => account.id === id)
        const account = findAccount(id)
        if (!account) return
        if (!incoming) return { status: 'unavailable' }
        Object.assign(account, mergeQuotaStatus(account, incoming))
        paintQuota(id)
      } catch {
        // A failed local status read does not terminate another window's
        // query. Report it as still pending instead of inventing a failure.
        return state(findAccount(id) || { id })
      }
      const current = state(findAccount(id) || { id })
      if (current.status !== 'in_progress' || Date.now() >= deadline) return current
      await new Promise(resolve => setTimeout(resolve, 500))
    }
  }

  return {
    requests: quotaRequests, state, card: usageCard,
    refresh: refreshQuota, refreshAll, configure: configureMonitor,
    get refreshingAll () { return refreshingAll },
    get busy () { return refreshingAll || getAccounts().some(account => state(account).pending) }
  }
}
