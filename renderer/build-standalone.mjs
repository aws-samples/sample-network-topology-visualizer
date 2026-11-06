// flow.html + 에셋 4개를 파일 하나로 합친다 → flow-standalone.html
// 서버 없이 더블클릭(file://)으로 열리게 하는 것이 목적. flow.* 를 고치면 이걸 다시 돌려라.
import { readFileSync, writeFileSync } from 'node:fs'
import { createHash } from 'node:crypto'

const read = (f) => readFileSync(new URL(f, import.meta.url), 'utf8')
// 인라인 스크립트 안의 </script> 는 문서를 끊는다. 지금은 0건이지만 방어해 둔다.
const safe = (s) => s.replace(/<\/script/gi, '<\\/script')

// ⚠️ 치환값은 **함수**로 넘긴다. 문자열로 넘기면 소스 안의 `$&`·`$1` 이 치환 패턴으로 해석돼
// 파일 일부가 조용히 바뀐다(실측: flow.js 의 `escRe` 안 `'\\$&'` 가 script 태그로 부활했다).
// 픽스처만 갈아끼워 다른 규모의 단일 파일도 만든다(30계정 검증용):
//   FIX=_fx30.js OUT=flow30-standalone.html node build-standalone.mjs
const FIX = process.env.FIX || 'fixture-flow.js'
const OUT = process.env.OUT || 'flow-standalone.html'

// 🔴 `_scale30.html`(30계정 검증 페이지)도 여기서 **생성한다.** 손으로 복사해 둔 사본은 조용히
// 낡는다 — 실측(2026-09-14): 검증 오버레이·배율 표시·힌트 줄이 30계정 페이지에만 없었고,
// 「30계정에서는 확대가 안 된다」는 사용자 신고를 하네스가 재현조차 못 했다. 픽스처 한 줄만
// 갈아끼운 **같은 마크업**을 쓴다. 30계정 산출물을 만들 때(FIX 지정)는 건드리지 않는다.
if (!process.env.FIX) {
  const s30 = read('flow.html')
    .replace('<script src="fixture-flow.js"></script>', () => '<script src="_fx30.js"></script>')
    .replace('<title>', '<!-- 생성물: build-standalone.mjs 가 flow.html 에서 만든다. 직접 고치지 마라. -->\n<title>')
  writeFileSync(new URL('_scale30.html', import.meta.url), s30)
  console.log(`_scale30.html ${Buffer.byteLength(s30, 'utf8').toLocaleString()} bytes (flow.html + _fx30.js)`)
}

// 인라인 대상은 **목록 한 벌**이다. `flow.html` 의 script 태그 순서를 그대로 적는다.
// 🔴 순서가 계약이다: `aws-canvas.js` 는 flow.js **앞**(flow.js 가 `NT_AWS` 를 부른다).
// 🔴 `verify-overlay.js`(§15 판정 장치)는 **넣지 않는다** — 사용자 결정 2026-10-20 「고객 화면에서 뺀다」(대장 82 개정).
//    하네스가 그림이 선 뒤 주입한다(검사 77). 이 목록에 되돌리면 검사 77 이 「제품에 판정 장치가 있다」로 FAIL 한다.
// 🔴 정규식과 파일 배열을 **따로** 적지 마라. 같은 목록의 두 벌이 되고 한쪽만 고치면 조용히
// 갈라진다 — 실측(2026-09-21): 배열에서만 파일 하나(당시 `levels.js`)를 빼도 정규식이 그 태그를 이미 먹어서
// 아래 「잔존 참조」 검사가 통과하고, **13KB 가 사라진 산출물로 exit 0** 이 났다. 그래서
// 정규식을 이 목록에서 만들고, 빌드 뒤에 블록이 전부 있는지 다시 센다.
const TAGS = ['icons.js', 'fixture-flow.js', 'adapt-core.js', 'aws-icons.js', 'aws-canvas.js', 'flow.js']
const FILES = TAGS.map((t) => (t === 'fixture-flow.js' ? FIX : t))
// 태그 사이에 올 수 있는 것은 공백과 HTML 주석뿐이다(`flow.html` 이 실제로 주석을 끼운다).
// `[\s\S]*?` 로 열어 두면 태그 사이에 들어온 **다른 태그까지** 조용히 먹는다.
const GAP = '(?:\\s|<!--[\\s\\S]*?-->)*'
const SCRIPTS = new RegExp(TAGS.map((t) => `<script src="${t.replace(/\./g, '\\.')}"></script>`).join(GAP))

// 지상 진실은 `flow.html` 의 태그 수다 — `TAGS`·`FILES` 어느 쪽이 줄어들어도 여기와 어긋난다.
const srcHtml = read('flow.html')
const nTags = (srcHtml.match(/<script src=/g) || []).length

const html = srcHtml
  .replace('<link rel="stylesheet" href="flow.css">', () => `<style>\n${read('flow.css')}\n</style>`)
  // 파일이 없으면 여기서 죽는다(ENOENT).
  .replace(SCRIPTS, () => FILES.map((f) =>
    `<script>\n/* ===== ${f} ===== */\n${safe(read(f))}\n</script>`).join('\n'))
  .replace('<title>', '<!-- 생성물: build-standalone.mjs 가 만든다. 직접 고치지 마라. -->\n<title>')

if (/<script src=|<link rel="stylesheet"/.test(html)) {
  console.error('FAIL: 인라인되지 않은 참조가 남았다 — flow.html 의 태그가 바뀌었는지 확인해라')
  process.exit(1)
}
// 잔존 참조 0 은 「전부 인라인됐다」를 뜻하지 않는다(위 실측). 블록 수를 `flow.html` 의 태그
// 수와 맞춘다 — 🔴 `FILES` 를 순회해 세면 `FILES` 자신이 한 칸 줄어든 경우를 못 잡는다(실측:
// 그 형태로 먼저 짰더니 13KB 누락이 그대로 exit 0 이 났다). 세는 기준은 산출물 밖에 둬야 한다.
const blocks = html.match(/\n\/\* ===== [^\n]+ ===== \*\/\n/g) || []
if (blocks.length !== nTags) {
  console.error(`FAIL: 인라인 블록 ${blocks.length}개 ≠ flow.html 의 script 태그 ${nTags}개`
    + ` — 들어간 것: ${blocks.map((b) => b.replace(/[\n/*= ]/g, '')).join(' ')}`)
  process.exit(1)
}
for (const f of FILES) {
  if (html.includes(`/* ===== ${f} ===== */`)) continue
  console.error(`FAIL: ${f} 가 인라인되지 않았다 — TAGS 목록과 flow.html 의 script 태그를 맞춰라`)
  process.exit(1)
}
// 실시간 주소 칸은 **정확히 1개**여야 한다(`infra/state-lambda/signer.py` 의 `SLOT` 과 같은 모양).
// 서명 함수는 0개·2개 이상이면 쓰지 않고 멈춘다 — 그러면 배포된 화면의 실시간 스위치가 조용히
// 영영 비활성이다. 그 상태를 빌드에서 먼저 잡는다(인라인된 JS 가 이 태그 문자열을 품으면 2개가 된다).
const SLOT = /<script id="nettopo-live" type="application\/json">\{[^<]*\}<\/script>/g
const nSlot = (html.match(SLOT) || []).length
if (nSlot !== 1) {
  console.error(`FAIL: 실시간 주소 칸 ${nSlot}개 — 정확히 1개여야 한다(flow.html 의 #nettopo-live)`)
  process.exit(1)
}
// CSP 메타(보안 규칙 · 검사 117). 🔴 스크립트는 **이 산출물에 인라인된 블록의 sha256 만** 허용한다 —
// `'unsafe-inline'` 이면 HTML 에 구워진 리소스 이름·태그가 스크립트로 해석되는 삽입 경로가 열린다.
// 해시는 **모든 치환이 끝난 바이트**로 센다 — 브라우저가 계산하는 값이 그것이다.
// `#nettopo-live` 칸(`type="application/json"`)은 실행되지 않는 데이터 블록이라 해시 대상이 아니다 —
// 서명 함수가 배포 뒤 **그 칸만** 다시 쓰므로(`infra/state-lambda/signer.py`) 해시가 깨지지 않는다.
// `connect-src` = 실시간 채널의 버킷 오리진 1개(불변식 ⑥ 의 예외 3경로 — 상태 읽기·제어 읽기·제어 쓰기 — 가 같은 오리진).
// CodeBuild 가 `CSP_CONNECT` 로 넘긴다(`buildspec.yml` 빌드 줄 · 서명 함수의 가상 호스트 +
// 리전 엔드포인트 주소와 같은 모양). 로컬 빌드는 `'none'` — 칸이 비어 있어 실시간이 없다.
// 🔴 모양을 좁게 검사한다: `*`·`https:` 같은 값이 들어오면 CSP 가 있는데도 아무 데나 보낼 수 있다.
const CONNECT = process.env.CSP_CONNECT || ''
if (CONNECT && !/^https:\/\/[a-z0-9][a-z0-9-]*\.s3\.[a-z0-9-]+\.amazonaws\.com$/.test(CONNECT)) {
  console.error(`FAIL: CSP_CONNECT «${CONNECT}» — https://<버킷>.s3.<리전>.amazonaws.com 하나만 받는다`)
  process.exit(1)
}
// 실행 블록 = 속성 없는 `<script>`. 속성이 붙은 것은 데이터 칸(`type="application/json"`)뿐이어야 한다 —
// 다른 모양(예: `<script defer>`)이 끼면 여기서 멈춘다(해시에서 빠지면 그 블록은 브라우저가 막는다).
// 🔴 인라인된 JS·HTML 주석 안의 글자(`<script src>` 등)를 태그로 세지 않도록 둘을 비운 껍데기에서 센다.
const tags = html.replace(/<script>[\s\S]*?<\/script>/g, '<script></script>').replace(/<!--[\s\S]*?-->/g, '').match(/<script\b[^>]*>/g) || []
const odd = tags.filter((t) => t !== '<script>' && !/\btype="application\/json"/.test(t))
if (odd.length) {
  console.error(`FAIL: 해시를 셀 수 없는 script 태그 ${odd.length}개 — ${odd.slice(0, 3).join(' ')}`)
  process.exit(1)
}
const hashes = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)]
  .map((m) => `'sha256-${createHash('sha256').update(m[1], 'utf8').digest('base64')}'`)
if (hashes.length < nTags) {
  console.error(`FAIL: 해시 ${hashes.length}개 < 인라인 블록 ${nTags}개`)
  process.exit(1)
}
const CSP = [
  "default-src 'none'",
  `script-src ${hashes.join(' ')}`,
  "style-src 'unsafe-inline'",
  'img-src data: blob:',
  `connect-src ${CONNECT || "'none'"}`,
  "base-uri 'none'",
  "form-action 'none'",
].join('; ')
// 🔴 메타는 **첫 script 앞**이어야 한다 — 뒤에 두면 그 앞 블록은 정책 없이 돈다. charset 바로 뒤에 둔다.
if ((html.match(/<meta charset="utf-8">/g) || []).length !== 1 || html.indexOf('<meta charset="utf-8">') > html.indexOf('<script')) {
  console.error('FAIL: <meta charset="utf-8"> 가 정확히 1개, 첫 script 앞에 있어야 CSP 메타를 그 뒤에 둔다')
  process.exit(1)
}
const out = html.replace('<meta charset="utf-8">',
  () => `<meta charset="utf-8">\n<meta http-equiv="Content-Security-Policy" content="${CSP}">`)

writeFileSync(new URL(OUT, import.meta.url), out)
// String#length 는 UTF-16 코드 단위다 — 한글이 섞인 파일에서 실제 바이트와 다르다(실측 77,145 vs 84,156).
console.log(`${OUT} ${Buffer.byteLength(out, 'utf8').toLocaleString()} bytes` +
  ` (${out.length.toLocaleString()} UTF-16 코드 단위) · CSP 스크립트 해시 ${hashes.length} · connect-src ${CONNECT || "'none'"}`)
