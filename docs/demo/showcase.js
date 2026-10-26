'use strict'
const views = {
  overview: { width: 1600, height: 900, lang: 'zh-CN', theme: 'dark', page: 'index',
    eyebrow: '为习惯 Claude 桌面端的你', headline: '多个账号，\n一处掌控。',
    lede: '继续使用官方桌面端。\n切号、查额度、同步本地会话索引。',
    features: ['独立登录目录', '单号 / 全部手动刷新', '5 小时 / 周额度与重置时间'] },
  launcher: { width: 1000, height: 1400, lang: 'zh-CN', theme: 'dark', page: 'index',
    eyebrow: '01 / 账号主页', headline: '', lede: '', features: [] },
  usage: { width: 1200, height: 1280, lang: 'zh-CN', theme: 'dark', page: 'settings',
    eyebrow: '02 / 独立额度管理', headline: '', lede: '', features: [] },
  english: { width: 1600, height: 900, lang: 'en', theme: 'light', page: 'index',
    eyebrow: 'BUILT FOR CLAUDE DESKTOP · macOS', headline: 'Your accounts.\nOne clear view.',
    lede: 'Keep the official desktop app.\nSwitch locally. Check usage when you choose.',
    features: ['Separate login profiles', 'Manual usage queries', '5-hour & weekly limits, with reset times'] },
  portrait: { width: 1080, height: 1440, lang: 'zh-CN', theme: 'dark', page: 'index',
    eyebrow: 'CLAUDE 桌面端多账号工具', headline: '继续用桌面端，\n多个账号也从容。',
    lede: '本地切号  /  手动查额度  /  不使用反代', features: [] }
}
const params = new URLSearchParams(location.search)
const name = Object.hasOwn(views, params.get('view')) ? params.get('view') : 'overview'
const view = views[name]
document.documentElement.lang = view.lang
document.body.dataset.view = name
document.body.style.setProperty('--canvas-width', view.width + 'px')
document.body.style.setProperty('--canvas-height', view.height + 'px')
document.getElementById('eyebrow').textContent = view.eyebrow
document.getElementById('headline').textContent = view.headline
document.getElementById('lede').textContent = view.lede
for (const label of view.features) {
  const span = document.createElement('span')
  span.textContent = label
  document.getElementById('features').append(span)
}
document.getElementById('footer-note').textContent = view.lang === 'en'
  ? 'SIMULATED DEMO · Fictional accounts and usage · macOS'
  : '模拟演示 · 账号与额度均为虚构数据 · macOS'
const iframe = document.getElementById('product')
iframe.src = '/app/' + view.page + '.html?' + new URLSearchParams({ lang: view.lang, theme: view.theme, section: 'usage' })
iframe.addEventListener('load', () => { document.body.dataset.ready = 'true' })
