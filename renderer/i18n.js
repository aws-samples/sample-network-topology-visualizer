// UI string translation (`NT_T`). Keys are the Korean originals. A string that is not in the table is
// returned unchanged — nothing is hidden.
//
// - Language is chosen in this order: `?lang=` > localStorage (`nettopo.lang`) > `<html data-lang>` (installed
//   default) > ko.
// - Sentences with values look like `NT_T('<original with {0}>', [n])`. `{n}` is substituted only when arguments are given.
// - `NT_T.both(s, a)` returns `{ t, ko }`: the display string and the Korean original built from the same
//   arguments (an argument may itself be a `{ t, ko }` pair). Code that classifies text by its wording reads `.ko`,
//   so classification does not depend on the language. There is no reverse lookup from a translation.
// - `NT_T.dom(root)` translates static HTML (flow.html): text nodes, title, placeholder and aria-label. No-op for ko.
// - Tables are added by `i18n-en.js` via `NT_T.add('en', {...})`. This file is the **first** script in flow.html —
//   the language must be settled before the other files are evaluated (module-level constants). That is why
//   switching the language means save + reload.
(() => {
  const KEY = 'nettopo.lang'
  const TABLES = {}            // code → { original: translation }
  const PATTERNS = {}          // code → [[regex, replacement]] — sentences with values that have no exact key (engine f-strings, adapter-built text)
  const HANGUL = /[가-힣]/
  const fill = (s, a) => (a ? s.replace(/\{(\d)\}/g, (m, i) => (a[i] === undefined ? m : String(a[i]))) : s)
  const pick = () => {
    let v = null
    try { v = new URLSearchParams(location.search).get('lang') } catch { /* file:// etc. */ }
    try { v = v || localStorage.getItem(KEY) } catch { /* storage blocked */ }
    v = v || document.documentElement.dataset.lang || 'ko'
    return /^[a-z]{2}$/.test(v) ? v : 'ko'
  }
  let lang = pick()

  function NT_T (s, a) {
    if (typeof s !== 'string') return s
    const tb = TABLES[lang]
    let e = tb ? tb[s] : undefined
    if (e === undefined && tb && HANGUL.test(s)) {
      for (const [re, rep] of PATTERNS[lang] || []) if (re.test(s)) { e = s.replace(re, rep); break }
    }
    if (e === undefined) return fill(s, a)
    return a ? fill(e, a) : e
  }
  const isPair = (x) => !!x && typeof x === 'object' && typeof x.ko === 'string'
  NT_T.both = (s, a) => ({
    t: NT_T(s, a && a.map((x) => (isPair(x) ? x.t : x))),
    ko: fill(s, a && a.map((x) => (isPair(x) ? x.ko : x)))
  })
  NT_T.lang = () => lang
  NT_T.langs = () => ['ko', ...Object.keys(TABLES)]
  NT_T.has = (code) => code === 'ko' || !!TABLES[code]
  NT_T.add = (code, table) => {
    TABLES[code] = table
  }
  NT_T.addPatterns = (code, list) => { PATTERNS[code] = (PATTERNS[code] || []).concat(list) }
  // Only saves. The caller reloads (module-level constants are already fixed in one language).
  NT_T.save = (code) => { lang = NT_T.has(code) ? code : 'ko'; try { localStorage.setItem(KEY, lang) } catch { /* ignore */ } return lang }
  const ATTRS = ['title', 'placeholder', 'aria-label']
  NT_T.dom = (root) => {
    if (lang === 'ko') return
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT)
    for (let n = w.nextNode(); n; n = w.nextNode()) {
      if (n.nodeType === Node.TEXT_NODE) { const v = n.nodeValue; const s = v.trim(); if (s) { const k = s.replace(/\s+/g, ' '); const e = NT_T(k); if (e !== k) n.nodeValue = v.replace(s, e) } } else {
        for (const at of ATTRS) if (n.hasAttribute(at)) { const v = n.getAttribute(at); const e = NT_T(v); if (e !== v) n.setAttribute(at, e) }
      }
    }
  }
  document.documentElement.lang = lang
  globalThis.NT_T = NT_T
  // Static HTML text + the language switch. The table (i18n-en.js) is loaded **after** this file, so this runs
  // again at the end of `add()` — before that the table is empty and nothing changes. Dynamic text is built by
  // flow.js through NT_T.
  function boot () {
    NT_T.dom(document.body)
    if (lang !== 'ko') { const t0 = document.title; const t1 = NT_T(t0); if (t1 !== t0) document.title = t1 }
    const b = document.getElementById('btn-lang')
    if (!b) return
    // KO | EN — the current language is bold (.on). Clicking the other one saves and reloads. A language without a table is hidden.
    const NAME = { ko: '한국어', en: 'English' }
    b.innerHTML = ''
    for (const code of ['ko', 'en']) {
      if (!NT_T.has(code)) continue
      const sp = document.createElement('span'); sp.className = 'lg' + (code === lang ? ' on' : ''); sp.dataset.lang = code
      sp.textContent = code.toUpperCase(); sp.title = NAME[code]; sp.setAttribute('role', 'radio'); sp.setAttribute('aria-checked', String(code === lang)); sp.tabIndex = 0
      const go = (e) => { e.stopPropagation(); if (code !== lang) { NT_T.save(code); location.reload() } }
      sp.onclick = go; sp.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') go(e) }
      b.appendChild(sp)
    }
    b.hidden = b.children.length < 2
  }
  const add0 = NT_T.add
  NT_T.add = (code, table) => { add0(code, table); boot() }
  boot()
})()
