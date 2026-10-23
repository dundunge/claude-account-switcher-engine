'use strict'

// Explicit bilingual strings. User names, paths and conversation titles are
// ordinary data: no DOM scanning or automatic replacement is performed.
const I18N = {
  language: window.CASE_LOCALE === 'en' ? 'en' : 'zh-CN',
  set (language) {
    this.language = language === 'en' ? 'en' : 'zh-CN'
    document.documentElement.lang = this.language
    document.title = document.getElementById('nav') ? tr('CASE 设置', 'CASE Settings') : 'CASE'
    document.getElementById('nav')?.setAttribute('aria-label', tr('设置分类', 'Settings sections'))
  }
}

function tr (zh, en) {
  return I18N.language === 'en' ? en : zh
}

function tf (zh, en, ...values) {
  return tr(zh, en).replace(/\{(\d+)\}/g, (_, index) => String(values[Number(index)] ?? ''))
}

I18N.set(I18N.language)
