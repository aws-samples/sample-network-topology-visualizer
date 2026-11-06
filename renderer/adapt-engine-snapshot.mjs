// 엔진 스냅샷(schema v2.0.0) → 캔버스 입력(`window.TOPO`) 파일. 프로토타입용 어댑터의 **CLI** 다.
// 변환 규칙은 전부 `adapt-core.js`(순수 함수 `NT_ADAPT`)에 있다 — 이 파일은 읽고, 그 함수에 넘기고, 굽고, 보고한다.
// 페이지의 이력 비교 화면(§3j J5)이 같은 함수를 쓰므로 규칙을 여기에 더하지 마라(두 번째 구현이 된다).
//
// 🔴 이 스크립트는 실계정 산출물을 다룬다 — 출력은 `out/` 로만 쓴다(리포 규칙: 스냅샷·다이어그램
//    자체가 민감 자료다. `renderer/` 안에는 실계정ID·CIDR 이 든 파일을 두지 마라).
// 🔴 경로는 모듈 기준 상대경로만 쓴다(검사 78: 이 폴더의 코드 줄에 절대경로 금지).
//
// 실행:
//   node adapt-engine-snapshot.mjs                        # IN=../out/live-run5.json
//   IN=../out/x.json IN2=../out/y.json OUT=../out/fixture-real.js node adapt-engine-snapshot.mjs
//   STRUCT=1 … node adapt-engine-snapshot.mjs              # 엔진 구조 문서도 같이 굽는다(배포 빌드 — §3j J2)
//
// 계약의 근거는 `adapt-core.js` 머리 주석에 있다.
import { readFileSync, writeFileSync } from 'node:fs'
import './adapt-core.js'

const IN = process.env.IN || '../out/live-run5.json'
const IN2 = process.env.IN2 || ''
// 관측 문서(`schema/observation.schema.json`) — 주면 `window.OBS` 를 같이 굽는다. 없으면 굽지 않고,
// 그때 화면은 「상태를 한 번도 읽지 않았다」로 서 있어야 한다(불변식 ④ · flow.js:221 의 주석).
// 🔴 구조와 짝이 아니면 굽지 않는다 — 아래 `bad` 참고.
const OBS = process.env.OBS || ''
const OUT = process.env.OUT || '../out/fixture-real.js'

const load = (f) => JSON.parse(readFileSync(new URL(f, import.meta.url), 'utf8'))
// 🔴 엔진 구조 문서를 **그대로** 굽는다(`window.NT_STRUCT`) — 이력 비교 화면이 이 문서에서 「그때」를 되짚는다
//    (§3j J2). 없으면 화면은 없어진 것을 그리지 못하고 그 사실을 말한다. 기본은 끔 — 픽스처·하네스 산출물의
//    바이트를 바꾸지 않는다. 실계정 문서이므로 출력은 여전히 `out/` 로만 간다.
const STRUCT = process.env.STRUCT === '1'

const snap = load(IN)
const r = globalThis.NT_ADAPT(snap, OBS ? load(OBS) : null)
const { bad, obsStat } = r
const { skipped, unknown, unknownKinds, flows } = r.stats

let body = 'window.TOPO=' + JSON.stringify(r.topo) + ';\n'
if (IN2) {
  // 하네스의 라이브/스냅샷 게이트(검사 63 · 실계정 축 R17)는 **두 시각의 같은 계정**을 요구한다.
  // 🔴 델타가 0 이면 그 검사는 여전히 미측정이다 — 두 수집 사이에 실제 변화가 있어야 한다.
  // 🔴 둘째 시각도 **같은 어댑터로** 굽는다(W4 2026-10-02). 그 전까지는 첫 시각의 노드를 그대로 베끼고
  //    `collected_at` 만 바꿔서 델타가 **늘 0** 이었다 — 실계정 두 시각을 넣어도 잴 수 없는 질문이었다.
  const s2 = load(IN2)
  body += 'window.TOPO_T2=' + JSON.stringify(globalThis.NT_ADAPT(s2, null).topo) + ';\n'
}

// 관측 문서는 짝일 때만 `r.obs` 로 돌아온다(짝 규칙·자리 세기 = `adapt-core.js` 「관측 문서」 절).
// 🔴 **문서를 그대로 굽는다 — 고치지 않는다**(화면이 나중에 `fetch` 로 같은 모양을 받아 같은 코드로 소비한다).
if (r.obs) body += 'window.OBS=' + JSON.stringify(r.obs) + ';\n'
if (STRUCT) body += 'window.NT_STRUCT=' + JSON.stringify(snap) + ';\n'
// 「수집 시각 기준」 터널 상태(`NT_ADAPT.snapObs` · 사용자 결정 2026-10-24) — 배포 빌드가 `collect` 직후 1층 `observe` 를 돌려 준다.
//   🔴 SNAPOBS 를 줬는데 파일이 없다 = 그 observe 가 실패했다(빌드는 `|| rm -f` 로 구조 화면을 살린다) → `ok:false` 를 굽는다
//      (굽지 않으면 화면은 「이 기능이 없는 수집본」과 「읽다가 실패」를 가르지 못한다 · 불변식 ④). 주지 않으면 굽지 않는다(픽스처 바이트 그대로).
const SNAPOBS = process.env.SNAPOBS || ''
if (SNAPOBS) {
  let so
  try { so = globalThis.NT_ADAPT.snapObs(snap, load(SNAPOBS)) } catch (e) { so = globalThis.NT_ADAPT.snapObs(snap, null, '구조 수집 직후 상태 읽기에 실패했습니다') }
  body += 'window.NT_SNAPOBS=' + JSON.stringify(so) + ';\n'
  console.log(`  수집 시각 기준 상태 — ${so.ok ? `VPN 연결 ${Object.keys(so.tunnels).length}개의 터널` : '없음: ' + so.why}`)
}
writeFileSync(new URL(OUT, import.meta.url), body)

console.log(`${OUT} — 캔버스 노드 ${r.stats.nodes} · 엣지 ${r.stats.edges}(그중 flow 재투영 ${flows}) · ` +
  `표 ${r.stats.list} · 부분실패 ${r.stats.partial}`)
console.log('  선을 그리지 않은 엔진 엣지:', JSON.stringify(skipped))
if (obsStat) {
  console.log(`  관측 — 카드에 앉음 ${obsStat.card} · 표로 뺀 자원 ${obsStat.listOnly} · ` +
    `선에 앉음 ${obsStat.edge}(그중 live ${obsStat.edgeLive} · 못 앉은 선 ${obsStat.edgeMiss}) · ` +
    `어디에도 못 앉음 ${obsStat.miss}`)
  console.log('  관측 판정 분포:', JSON.stringify(obsStat.byStatus))
}
console.log('  티어 분포:', JSON.stringify(r.stats.tiers))
// 🔴 미지 타입은 **죽이지 않지만 조용히 넘어가지도 않는다.** 이 줄이 개발자에게 「할 일이 생겼다」를
//    말하고, 페이지에서는 §3i 배너·표가 사용자에게 같은 사실을 말한다(같은 값, 두 청중).
if (unknown.length) {
  const byType = Object.entries(unknown.reduce((m, t) => ((m[t] = (m[t] || 0) + 1), m), {}))
    .sort((x, y) => x[0].localeCompare(y[0])).map(([t, c]) => `${t} ${c}`)
  console.log(`  🔴 규칙을 모르는 타입 ${unknown.length}개(${byType.join(' · ')}) — 잔차로 넘겼다` +
    ' (페이지의 표·배너에 나온다). 이 파일의 switch 에 규칙을 더해라(이음매 1).')
}
if (unknownKinds.length) {
  const byKind = Object.entries(unknownKinds.reduce((m, t) => ((m[t] = (m[t] || 0) + 1), m), {}))
    .sort((x, y) => x[0].localeCompare(y[0])).map(([t, c]) => `${t} ${c}`)
  console.log(`  🔴 규칙을 모르는 엣지 종류 ${unknownKinds.length}개(${byKind.join(' · ')}) — 잔차로 넘겼다` +
    ' (그리지 않고 표·배너에 신고한다). 이 파일의 EDGE_KIND 에 규칙을 더해라(이음매 1).')
}
if (bad.length) {
  console.error('FAIL: 계약 위반 ' + bad.length + '건')
  for (const b of bad.slice(0, 20)) console.error('  ' + b)
  process.exit(1)
}
console.log('  자기검사 OK — 사라진 노드 0 · 부모·티어·az·attachedTo·표 계수 일치')
