// 엔진 스냅샷(schema v2.0.0) → 캔버스 입력(`window.TOPO` 모양). **순수 함수**다 — 파일·환경 변수·노드 API 를
// 모른다. 두 소비자가 **같은 함수**를 쓴다(§3j J5):
//   ① `adapt-engine-snapshot.mjs` — CLI. 파일을 읽어 이 함수에 넘기고, 결과를 `window.TOPO=` 로 굽고 보고한다.
//   ② 페이지의 이력 비교 화면 — 지금 구조에서 되짚은 엔진 문서를 **같은 규칙으로** 캔버스 입력으로 바꾼다.
// 🔴 두 번째 구현을 만들지 마라. 오른쪽 그림이 왼쪽과 다른 규칙으로 그려지면 「지금 | 그때」 비교가 거짓이 된다
//    (서브넷 티어·흐름 선 재투영이 여기서만 나온다).
// 🔴 classic script 다 — 페이지에 인라인되고(`build-standalone.mjs` TAGS), 노드는 부작용 import 로 싣는다
//    (`package.json` "type": "commonjs"). `export`·`import`·`require` 를 쓰지 마라. 출구는 `globalThis.NT_ADAPT` 하나다.
//
// 계약의 근거(값을 바꾸기 전에 읽어라):
//   - `tier` 는 아래 `TIERS` 중 하나여야 한다(밖이면 그릴 자리가 없다 — 잔차 표 §3i) → 여기서 전수 검사한다.
//   - 리전 노드 id 는 `r-<계정>-<리전>`(아래 `rid`), 인터넷 노드 id 는 `ext-*`(검사 26).
//   - SG·NACL·라우트테이블은 노드가 아니라 `listOnly` 다(canvas-principles §13-55). 그리면 선이
//     폭발하고, 빼면 조사가 끊긴다 → 캔버스 밖 표.
//   - 계정 노드는 `attrs.account` 가 있어야 범례가 마스킹된 라벨을 만든다(aws-canvas.js 가 읽는다).
;(function () {
  'use strict'

  // ---------------------------------------------------------------- 매핑 규칙
  // 캔버스 밖 표로 빼는 종류(그림에 위치가 없는 규칙 자원).
  const LIST_TYPES = { security_group: 'sg', network_acl: 'acl', route_table: 'rtb' }
  // 엔진 엣지 → 캔버스 `kind`. 값이 null 이면 **선을 그리지 않는다**(관계를 위치로 이미 보여준 것).
  // 🔴 키가 **없는** 것과 값이 `null` 인 것은 다른 사건이다. `null` = 「안 그리기로 정했다」(정상),
  //    키 없음 = 「이 종류를 아직 모른다」(이음매 1 의 엣지 쪽). 뒤엣것을 `undefined` 로 흘리면
  //    페이지가 정적 관계선으로 **아는 척** 그리고 「연결」 탭 종류 칸에 `undefined` 를 적었다
  //    (실측 2026-09-20). 그래서 아래 루프가 `hasOwnProperty` 로 둘을 갈라 `fallback: true` 로 넘긴다 —
  //    노드 쪽의 「티어를 주지 않는다」와 같은 판단이다(모르면 그리지 말고 신고한다).
  const EDGE_KIND = {
    peering: 'peering',
    endpoint_connection: 'service',
    load_balancer_placement: 'logical',
    attachment: 'attachment',
    origin: 'origin',               // CloudFront → 원본 LB(#43 · ADR 0002 §7) — 새 도식이 전역 줄에서 LB 로 화살표 선을 긋는다
    load_balancer_target: 'lbtarget', // LB → 등록 대상(2.7.0 · ADR 0002 §8 · §17.27) — 새 도식이 LB → 대상 아이콘(외부 IP = 출구 포트) 화살표 선을 긋는다
    endpoint_provider: 'epprov',      // GWLB 엔드포인트 → 그 서비스를 제공하는 GWLB(2.8.0 · ADR 0002 §9 · §17.28) — 새 도식이 엔드포인트 칸 → 제공자 아이콘 화살표 선을 긋는다
    dns_alias: 'dnsalias',            // Route 53 별칭 → LB·CloudFront(2.8.0 · ADR 0002 §11 · §17.30) — 새 도식이 전역 줄 영역 → LB 선을 긋는다(CloudFront 는 같은 줄이라 정보창)
    zone_association: null,           // 사설 영역 ↔ VPC(§17.30) — 선이 아니라 두 정보창(영역 「연결된 VPC」 · VPC 「프라이빗 호스팅 영역」 줄)이 말한다
    // 배치 D(2.10.0 · ADR 0002 §12 · §17.34~37)
    eip_association: null,            // 탄력적 IP → 주인 — 선이 아니라 주인 정보창의 「탄력적 IP」 줄이다(아래 `eips` · §17.34)
    vpc_link: 'vpclink',              // API Gateway → LB(VPC 링크 · §17.35) — 새 도식이 리전 자원 줄 API → LB 화살표 선을 긋는다
    proxy_target: 'dbproxy',          // RDS 프록시 → DB 인스턴스·클러스터(§17.36) — 같은 VPC 안 선
    accelerator_endpoint: 'gaep',     // Global Accelerator → 엔드포인트(§17.37) — 전역 줄 → LB·EC2 선(CloudFront 원본 선과 같은 길)
    // #129(2.11.0 · ADR 0002 §13 · §17.38~40)
    connect_transport: null,          // TGW Connect → 전송 어태치먼트 — 선이 아니라 그 전송 선(VPC 포트 · DX 선) 위의 오버레이(아래 `cnxOf` → 노드 `connect.on`)
    core_network_attachment: 'cwanatt', // Cloud WAN 코어 네트워크 ↔ VPC(§17.39) — 새 도식이 전역 줄 → VPC 위 변 포트 선을 긋는다
    lattice_vpc_association: 'lsnvpc',  // Lattice 서비스 네트워크 ↔ VPC(§17.40) — 리전 자원 줄 → VPC 위 변 포트(TGW 어태치먼트와 같은 길)
    lattice_service_association: null,  // 서비스 네트워크 ↔ 서비스 — 선이 아니라 두 정보창(아래 `lsnSvcs`·`lsvcNets`)
    lattice_target: 'lsvctgt',          // Lattice 서비스 → EC2·ALB·Lambda(§17.40) — 리전 자원 줄 → 대상(API Gateway VPC 링크와 같은 길)
    // 2.12.0(ADR 0014 · §17.41)
    waf_protection: 'wafp',             // WAF 웹 ACL → 보호 대상(CloudFront · ALB · API Gateway) — 새 도식이 WAF 아이콘 → 대상 화살표 선을 긋는다
    association: null,              // 출발점(RT·NACL)이 캔버스 밖이다
    route: null,                    // 라우트는 서브넷→대상 'flow' 로 재투영한다(아래)
    security_group_reference: null  // SG 는 캔버스 밖이다
  }

  // 계정·리전 캔버스 id(아래 `fallbackParent`·`put` 이 쓴다). 🔴 비교 칸의 카메라(`flow.js` `cmpNear`)도 엔진 사슬의 계정·리전을
  //   지금 그림의 상자로 찾을 때 **이 규칙**을 쓴다(`NT_ADAPT.canvasIdOf`) — 두 벌로 적으면 한쪽만 바뀌는 날 카메라가 빈 곳을 본다
  //   (§3j J3 · QA B 2차 N-01: 엔진 id `region/…` 로 찾아 왼쪽 칸이 통째로 비었다).
  const rid = (acct, region) => 'r-' + acct + '-' + region
  const canvasIdOf = (n) => (n.type === 'account' ? 'acct-' + n.account_id : n.type === 'region' ? rid(n.account_id, n.region) : n.id)

  // 엔진 스냅샷 1장(+ 그 구조의 관측 문서) → 캔버스 입력. 🔴 `snap` 을 고치지 않는다(페이지가 같은 문서를 되짚는다).
  //   반환 = { topo, obs, obsStat, bad, stats }. `obs` = 짝이 맞을 때만 문서 그대로(아니면 null · 사유는 `bad`).
  function adapt (snap, doc) {
    const N = snap.nodes
    const byId = new Map(N.map((n) => [n.id, n]))
    const kidsOf = new Map()
    for (const n of N) {
      if (!n.parent_id) continue
      if (!kidsOf.has(n.parent_id)) kidsOf.set(n.parent_id, [])
      kidsOf.get(n.parent_id).push(n)
    }
    const kids = (id, type) => (kidsOf.get(id) || []).filter((n) => !type || n.type === type)
    const typeOfId = (id) => String(id).split('/')[0]
    const nameOf = (n) => n.name || n.native_id || n.id
    const edgesOfType = (t) => snap.edges.filter((e) => e.type === t)

    // ---- 서브넷 티어: 「인터넷 게이트웨이로 가는 라우트가 있는가」로 public 을 가른다 -------------
    // 🔴 명시적 연관이 없는 서브넷은 VPC 의 main 라우트 테이블을 따른다(엔진은 main 연관을 엣지로
    //    만들지 않는다 — 서브넷이 없는 연관이기 때문이다. `attributes.main` 이 정본이다).
    const rtOfSubnet = new Map()
    for (const e of edgesOfType('association')) {
      if (typeOfId(e.source) === 'route_table') rtOfSubnet.set(e.target, e.source)
    }
    const mainRtOfVpc = new Map()
    for (const n of N) if (n.type === 'route_table' && n.attributes.main) mainRtOfVpc.set(n.parent_id, n.id)
    const routeTargets = new Map()  // rt id → Set(대상 노드 id)
    // 🔴 (라우트 테이블, 대상) → **엔진 라우트 엣지 id**. 아래 재투영이 만드는 캔버스 선의 id 는
    //    합성(`e1,e2,…`)이라 관측 문서의 엣지 id 와 원리적으로 겹치지 않는다 → 이 표가 **유일한 끈**이다.
    //    (2026-09-24: 이것이 없어서 라우트 관측 24건이 「캔버스가 선을 그리지 않는 관계」로 잘못 보고됐다.)
    const routeEdgeIds = new Map()
    const routeDest = new Map(); const routeBh = new Set()   // `rt|target` → 목적지들 · 블랙홀
    const routeDefault = new Set()   // `rt|target` — 기본 라우트(0.0.0.0/0 · ::/0, blackhole 아님)가 이 대상으로 간다
    for (const e of edgesOfType('route')) {
      if (!routeTargets.has(e.source)) routeTargets.set(e.source, new Set())
      routeTargets.get(e.source).add(e.target)
      // 엔진 노드 id 는 `/` 구획이므로 `|` 는 키 구분자로 안전하다(엣지 id 의 구분자와 같은 문자다).
      const k = e.source + '|' + e.target
      if (!routeEdgeIds.has(k)) routeEdgeIds.set(k, [])
      if (e.id && !routeEdgeIds.get(k).includes(e.id)) routeEdgeIds.get(k).push(e.id)
      // 기본 라우트(§17.24 인터넷 칸) — 엔진은 대상별로 목적지를 묶어 `destinations` 로 싣는다(route_table.py).
      //   손으로 쓴 픽스처(minimal·multi-account)는 옛 모양 `destination_cidr_block` 하나다 — 둘 다 읽는다.
      const ra = e.attributes || {}
      const ds = [].concat(ra.destinations || [], ra.destination_cidr_block || [], ra.destination_ipv6_cidr_block || [])
      if (!ra.blackhole && ds.some((d) => d === '0.0.0.0/0' || d === '::/0')) routeDefault.add(k)
      // 목적지·블랙홀 — 서브넷 정보창의 「라우트 표」가 목적지 → 대상을 적는다(없으면 「목적지 미상」)
      if (!routeDest.has(k)) routeDest.set(k, new Set())
      for (const d of ds) routeDest.get(k).add(String(d))
      if (ra.blackhole === true) routeBh.add(k)
    }
    const rtFor = (subnet) => rtOfSubnet.get(subnet.id) || mainRtOfVpc.get(subnet.parent_id) || null
    // 콘솔식 라우팅 표의 재료(2.13.0 · §17.11 「콘솔 표현 원칙」 · 검사 166) — 엔진 `routes` = 「<목적지> <대상> <상태> <라우팅 오리진>」(local 포함 ·
    //   줄 수 = `route_count`). 🔴 키가 없으면 `null` — 옛 수집본(그 키를 모으기 전 판) · 화면은 옛 대상별 엣지로 대신 그린다. 정보창 글자가 아니라 구조라 attrs 밖에 둔다
    const rtRoutes = (a) => (Array.isArray(a.routes)
      ? a.routes.map((x) => { const [d, t, st, orig] = String(x).split(' '); return { d: d || '', t: t || '', st: st || '', orig: orig || '' } })
      : null)
    const assocSubsOf = new Map()   // 라우팅 테이블 → 명시적으로 연결된 서브넷(엔진 association 엣지)
    for (const e of edgesOfType('association')) {
      if (typeOfId(e.source) !== 'route_table' || typeOfId(e.target) !== 'subnet') continue
      if (!assocSubsOf.has(e.source)) assocSubsOf.set(e.source, [])
      assocSubsOf.get(e.source).push(e.target)
    }
    const targetsFor = (rt) => [...(routeTargets.get(rt) || [])]
    const tierOfSubnet = (subnet) => {
      const t = targetsFor(rtFor(subnet)).map(typeOfId)
      return t.includes('internet_gateway') ? 'public' : 'private'
    }

    // 못 이은 라우트 ENI(2.8.0 · P2-2 · 라우트 테이블 `unresolved_route_targets` = 「<eni> <까닭> <목적지>…」) — 그 테이블을 쓰는 서브넷의 라우트 표 줄로
    const routeUnresOfRt = new Map()
    for (const n of N) {
      if (n.type !== 'route_table' || !Array.isArray((n.attributes || {}).unresolved_route_targets)) continue
      routeUnresOfRt.set(n.id, n.attributes.unresolved_route_targets.flatMap((x) => { const [t, why, ...ds] = String(x).split(' '); return (ds.length ? ds : ['']).map((d) => ({ d, t, why })) }))
    }

    // 라우트 목적지 접두사 목록(2.10.0 · 배치 D · §17.33) — 테이블 속성 `prefix_lists`(「<id> <aws|customer> <이름>」) · `prefix_list_entries`(「<id> <CIDR>」) ·
    //   `prefix_list_entries_unread`(항목을 못 읽은 고객 관리형 id). 🔴 키가 없고 그 테이블이 `DescribeManagedPrefixLists` 로 일부 못 읽음이면 `unread` —
    //   「이름 없는 목록」과 「이름을 못 읽은 목록」을 가른다. 목적지에 `pl-` 가 하나도 없는 테이블은 만들지 않는다(옛 화면 입력·바이트 그대로)
    const plOfRt = new Map()
    for (const n of N) {
      if (n.type !== 'route_table') continue
      const ra = n.attributes || {}
      const usesPl = snap.edges.some((e) => e.type === 'route' && e.source === n.id && ((e.attributes || {}).destinations || []).some((d) => String(d).startsWith('pl-')))
      if (!usesPl) continue
      const unread = !Array.isArray(ra.prefix_lists) && (n.partial_reasons || []).some((r) => r && r.api === 'ec2:DescribeManagedPrefixLists')
      const lists = {}
      for (const x of ra.prefix_lists || []) { const [id, kind, ...nm] = String(x).split(' '); lists[id] = { aws: kind === 'aws', name: nm.join(' '), cidrs: kind === 'aws' ? undefined : [] } }
      for (const x of ra.prefix_list_entries || []) { const i = String(x).indexOf(' '); const id = String(x).slice(0, i); if (lists[id] && lists[id].cidrs) lists[id].cidrs.push(String(x).slice(i + 1)) }
      for (const id of ra.prefix_list_entries_unread || []) if (lists[id]) lists[id].cidrs = null
      plOfRt.set(n.id, { lists, unread })
    }

    // 탄력적 IP 의 주인(2.10.0 · 배치 D · §17.34) — 엔진 `eip_association`(EIP → 주인)을 주인 노드의 `eips`(정보창 줄 재료)와 EIP 노드의 `eipOwner` 로 옮긴다
    const eipsOf = new Map(); const eipOwner = new Map()
    for (const e of edgesOfType('eip_association')) {
      const x = byId.get(e.source); if (!x) continue
      const a = x.attributes || {}
      eipOwner.set(e.source, e.target)
      if (!eipsOf.has(e.target)) eipsOf.set(e.target, [])
      eipsOf.get(e.target).push({ id: e.source, ip: a.public_ip || '?', alloc: x.native_id || '?', svc: a.service_managed || null, name: x.name || null })
    }
    for (const v of eipsOf.values()) v.sort((p, q) => (p.ip < q.ip ? -1 : p.ip > q.ip ? 1 : p.alloc < q.alloc ? -1 : 1))
    // 까닭 줄(배치 D) — 「<ref> <까닭> [리전]」 → { ref, code, region }
    const whyLines = (v) => (Array.isArray(v) ? v.map((x) => { const [ref, code, region] = String(x).split(' '); return { ref, code: code || 'not_collected', ...(region ? { region } : {}) } }) : null)
    const noUnres = (at) => { const o = { ...at }; delete o['미해결 대상']; return o }   // 못 이은 것은 그 칸이 까닭과 함께 말한다(같은 이름을 두 번 적지 않는다)

    // #129(2.11.0) — Connect 가 올라탄 전송 어태치먼트(엔진 `connect_transport` · 선이 아니다) · Lattice 서비스 네트워크 ↔ 서비스(두 정보창)
    const cnxOf = new Map(edgesOfType('connect_transport').map((e) => [e.source, e.target]))
    const lsnSvcs = new Map(); const lsvcNets = new Map()
    for (const e of edgesOfType('lattice_service_association')) {
      if (!lsnSvcs.has(e.source)) lsnSvcs.set(e.source, [])
      lsnSvcs.get(e.source).push(e.target)
      if (!lsvcNets.has(e.target)) lsvcNets.set(e.target, [])
      lsvcNets.get(e.target).push(e.source)
    }
    // Connect 피어 「<id> <프로토콜> <TGW 주소> <피어 주소> <내부 CIDR,…> <TGW ASN> <피어 ASN> [이름]」(엔진 `transit_gateway_attachment.py` `_peer_line`)
    const peerOf = (x) => { const [id, proto, tgw, peer, cidrs, tasn, pasn, ...nm] = String(x).split(' '); return { id, proto, tgw, peer, cidrs: cidrs === '?' ? [] : cidrs.split(','), tasn, pasn, name: nm.join(' ') || null } }

    // ---------------------------------------------------------------- 노드 변환
    const out = []
    const list = []
    const unknown = []               // 어댑터가 규칙을 모르는 타입 — 버리지 않고 잔차로 넘긴다(이음매 1)
    const canvasId = new Map()       // 엔진 id → 캔버스 id
    // 미지 타입의 부모: 해소되는 만큼만 붙인다. 계정·리전은 캔버스에서 id 가 바뀌므로 환산하고,
    // 엔진이 가리키는 부모가 모델에 없으면 **그 값을 그대로 남긴다** — flow.js 의 잔차 행이
    // 「부모 — 모델에 없다」로 신고한다. 리전으로 슬쩍 옮기면 그 진단이 사라진다.
    // 🔴 리전 캔버스 id 는 **(계정, 리전) 쌍**이다 — `'r-' + region` 으로는 다계정 스냅샷에서 id 가
    //    겹친다(같은 리전을 쓰는 계정이 둘이면 `byId` 가 하나로 접히고 뒤 계정의 VPC 가 앞 계정 레인
    //    아래로 들어간다 = 남의 계정 색을 입는다, `flow.js` `acctIndexOf`). 엔진 노드는 `account_id` 가
    //    **required** 라(`schema/topology.schema.json` node.required) 항상 만들 수 있다.
    //    id 를 바꿔도 화면은 그대로다 — 계정 귀속은 `parent_id` 사슬로 풀고 **id 를 파싱하지 않는다**.
    // `rid` 는 함수 밖(위)에 있다 — 비교 칸 카메라(`canvasIdOf`)와 한 벌이다
    const fallbackParent = (n) => {
      const p = n.parent_id ? byId.get(n.parent_id) : null
      if (p) return p.type === 'account' ? 'acct-' + p.account_id : p.type === 'region' ? rid(p.account_id, p.region) : p.id
      if (n.parent_id) return n.parent_id
      return n.region ? rid(n.account_id, n.region) : 'acct-' + n.account_id
    }
    const vpcOfIgw = new Map()
    for (const e of edgesOfType('attachment')) {
      if (typeOfId(e.source) === 'internet_gateway' && typeOfId(e.target) === 'vpc') vpcOfIgw.set(e.source, e.target)
    }
    // 🔴 노드 출구는 **여기 하나뿐이고, 엔진 노드를 인자로 요구한다** — 그래서 지금 이 어댑터가
    // 내놓는 노드는 전부 엔진 근거가 있다(계정·리전 컨테이너도 엔진의 `account`·`region` 노드에서
    // 나온다. id 만 `acct-*`·`r-*` 로 바꾼다). `verify-real.mjs` R2 역방향이 이것을 잰다(대장 89).
    // 근거 없는 노드를 **새로 합성해야 한다면**(예: 인터넷 카드 — §16.8) 두 가지를 같이 해라:
    //   ① `synthetic: true` 를 달아 스스로 신고한다  ② `attrs.id`(native id)를 **넣지 마라**
    //      (합성은 AWS 에 대응 자원이 없다는 뜻이다. 둘을 같이 들면 R2 가 «면제 남용» 으로 FAIL 한다)
    // 안 달면 R2 가 「근거 없는 노드」로 FAIL 한다 — 그것이 의도한 동작이다(조용히 통과하지 않는다).
    const put = (engineNode, o) => { canvasId.set(engineNode.id, o.id); out.push(o); return o }
    const svcShort = (s) => {
      const m = /^com\.amazonaws\.[a-z0-9-]+\.(.+)$/.exec(String(s || ''))
      return m ? m[1] : String(s || '').replace(/^com\.amazonaws\.vpce\./, '')
    }

    // 서브넷 native id → 엔진 서브넷 노드(같은 계정·리전 안에서만 — 서브넷 id 는 계정 사이에 겹칠 수 있다고 본다)
    const subnetByNat = new Map()
    for (const x of N) if (x.type === 'subnet') subnetByNat.set(x.account_id + '|' + x.region + '|' + x.native_id, x)
    const spansOf = (n) => [...new Set((n.attributes || {}).subnet_ids || [])].map((s) => subnetByNat.get(n.account_id + '|' + n.region + '|' + s)).filter(Boolean)
    const cnt = (v) => (Array.isArray(v) ? v.length + '개' : undefined)
    const yn = (v) => (v === true ? '예' : v === false ? '아니요' : undefined)
    const onOff = (v) => (/^(enabled)$/i.test(String(v)) ? '켜짐' : /^(disabled)$/i.test(String(v)) ? '꺼짐' : v || undefined)
    // 서브넷 native id 목록 → 같은 계정·리전 서브넷 노드 id(못 찾은 것은 native id 그대로 — 정보창이 「이 화면에 없다」로 적는다)
    const natSubnets = (n, ids) => ids.map((s) => { const x = subnetByNat.get(n.account_id + '|' + n.region + '|' + s); return x ? x.id : s })
    const sgOf = (a) => (Array.isArray(a.security_group_ids) ? { sgIds: [...a.security_group_ids] } : {})

    for (const n of N) {
      const a = n.attributes || {}
      // 🔴 태그를 실어야 한다 — `flow.js` 의 `tagsOf` 가 **필터 `tag:키=값` 쿼리와 태그 묶음**을 이것으로 만든다.
      //    안 실으면 설명서 4.6 이 예로 든 `tag:environment=prod` 가 **원리적으로 0건**이고 묶음은 전부 Untagged 다
      //    (실계정 284노드 전수 Untagged 로 실측 · QA 기능개발 7).
      const base = { id: n.id, type: n.type, name: nameOf(n), region: n.region || undefined,
        tags: n.tags && Object.keys(n.tags).length ? n.tags : undefined }
      const attrs = { id: n.native_id || undefined }
      if (a.state) attrs.state = a.state
      if (a.owner_account_id) attrs['소유 계정'] = a.owner_account_id
      if (Array.isArray(a.unresolved_refs)) attrs['미해결 대상'] = a.unresolved_refs.join(', ')

      switch (n.type) {
        case 'account':
          // 계정 이름(§17.5 「계정은 이름으로 부른다」 · 2026-10-28) = 엔진이 실은 AWS 계정 이름 그대로 · 없으면 **null**.
          //   🔴 옛 상수 「계정」을 넣지 않는다 — 화면이 「이름 없음」을 알 수 없어 `계정 계정 (…)` 이 된다. 꼴은 화면의 일(`flow.js` `acctText`)
          put(n, { ...base, id: 'acct-' + n.account_id, name: typeof n.name === 'string' && n.name.trim() ? n.name : null,
            parent_id: null, attrs: { ...attrs, account: n.account_id, id: n.account_id } })
          break
        case 'region':
          put(n, { ...base, id: rid(n.account_id, n.region), name: n.region, parent_id: 'acct-' + n.account_id,
            attrs: { ...attrs, label: n.region } })
          break
        case 'vpc': {
          const subnets = kids(n.id, 'subnet')
          const azs = [...new Set(subnets.map((s) => s.attributes.availability_zone).filter(Boolean))]
          // 콘솔 「세부 정보」 재료(2.13.0 · §17.11 · 검사 166) — 키가 없으면(못 읽음·옛 판) 그 키를 만들지 않는다(정보창이 「읽지 못함」/줄 없음을 가른다)
          const con = {}
          for (const [k, v] of [['dnsSupport', a.enable_dns_support], ['dnsHostnames', a.enable_dns_hostnames], ['tenancy', a.instance_tenancy],
            ['dhcpId', a.dhcp_options_id], ['dhcp', a.dhcp_options], ['ipv6', a.ipv6_cidr_blocks]]) if (v !== undefined && v !== null) con[k] = Array.isArray(v) ? [...v] : v
          put(n, { ...base, parent_id: rid(n.account_id, n.region), con, attrs: { ...attrs,
            cidr: (a.cidr_blocks || []).join(', '), AZ: azs.length + '개',
            'default VPC': a.is_default ? '예' : '아니요',
            'security group': kids(n.id, 'security_group').length,
            'network ACL': kids(n.id, 'network_acl').length,
            'route table': kids(n.id, 'route_table').length } })
          break
        }
        case 'subnet': {
          // 못 이은 라우트 ENI 줄(2.8.0 · P2-2) — 없으면 키를 만들지 않는다(옛 화면 입력·바이트 그대로)
          const ru = routeUnresOfRt.get(rtFor(n)); const pl = plOfRt.get(rtFor(n))
          // 이 서브넷이 쓰는 라우팅 테이블(§17.11 서브넷 「라우팅 테이블」 칸 머리 · 검사 166) — 명시적 연결이 없으면 VPC 의 기본(main) 테이블(암시)
          const rtE = rtFor(n)
          put(n, { ...base, parent_id: n.parent_id, tier: tierOfSubnet(n), az: a.availability_zone, ...(ru && ru.length ? { routesUnres: ru } : {}), ...(pl ? { pls: pl } : {}),
            ...(rtE && byId.get(rtE) ? { rtId: byId.get(rtE).native_id, rtImplicit: !rtOfSubnet.has(n.id) } : {}),
            // AZ ID 는 계정 간 비교의 유일한 기준이다 — AZ 이름은 계정마다 다른 물리 AZ 를 가리킨다
            //   (`docs/resource-catalog.md:185`). 다계정 도구라 버리면 안 된다(QA 기능개발 7).
            attrs: { ...attrs, cidr: a.cidr_block, 'AZ ID': a.availability_zone_id || undefined,
              'public IP 자동할당': a.map_public_ip_on_launch ? '예' : '아니요' } })
          break
        }
        case 'internet_gateway': {
          const vpc = vpcOfIgw.get(n.id)
          // 부착된 IGW 는 VPC 왼쪽 경계에 그린다(ATTACH_AT 'vpc-l'). 떼어낸 IGW 는 VPC 가 없으므로
          // 리전 외부 열에 남긴다 — 버리면 「없다」로 보인다.
          put(n, vpc
            ? { ...base, parent_id: vpc, tier: 'edge', con: { vpcNat: (byId.get(vpc) || {}).native_id || null }, attrs: { ...attrs, vpc: nameOf(byId.get(vpc)) } }
            : { ...base, parent_id: rid(n.account_id, n.region), tier: 'external', attrs: { ...attrs, vpc: '미부착' } })
          break
        }
        case 'nat_gateway': {
          // 🔴 엔진 부모는 서브넷 → VPC → 리전 순으로 「있는 첫 것」이다(`services/nat_gateway.py`). 서브넷이 먼저 지워진
          //    `deleted` NAT(AWS 가 약 1시간 돌려준다) · 서브넷 조회 거부(불변식 ④)면 부모가 VPC 나 리전이다 — 그것을 서브넷처럼
          //    읽으면 계약 위반으로 **빌드 전체가 멈춘다**(QA B B-17 실측 2026-10-14: 계약 위반 9건 · 화면 1시간 갱신 안 됨).
          //    서브넷을 지어내지 않는다: VPC 면 VPC 가장자리(IGW 와 같은 edge) · 리전이면 VPC 밖(external).
          const p = byId.get(n.parent_id)
          const subnet = p && p.type === 'subnet' ? p : null
          const nat = { ...attrs, EIP: (a.public_ips || []).join(', '), 연결: a.connectivity_type }
          if (subnet) {
            put(n, { ...base, parent_id: subnet.parent_id, tier: tierOfSubnet(subnet),
              az: subnet.attributes.availability_zone, attachedTo: subnet.id, attrs: nat })
          } else if (p && p.type === 'vpc') {
            put(n, { ...base, parent_id: p.id, tier: 'edge', attrs: { ...nat, '자리': '서브넷 모름 — 서브넷이 없거나 읽지 못했습니다' } })
          } else {
            put(n, { ...base, parent_id: rid(n.account_id, n.region), tier: 'external', attrs: { ...nat, '자리': 'VPC 밖 — VPC 가 없거나 읽지 못했습니다' } })
          }
          break
        }
        case 'transit_gateway': {
          // 콘솔 「세부 정보」 옵션(2.13.0 · §17.11 · 검사 166) — 엔진 값 그대로(enable/disable) · 키가 없으면 만들지 않는다.
          //   🔴 옛 「연결 N개」 줄은 버린다 — 실제로는 어태치먼트 수인데 아래 라우팅 테이블 「연결」 탭(association)과 같은 낱말이었다(🔴 R-02 · 예외 E3) →
          //   정보창 「어태치먼트 (N)」 칸이 그 수를 말한다
          const con = {}
          for (const [k, v] of [['assocRt', a.association_default_route_table_id], ['propRt', a.propagation_default_route_table_id],
            ['defAssoc', a.default_route_table_association], ['defProp', a.default_route_table_propagation], ['autoAccept', a.auto_accept_shared_attachments],
            ['vpnEcmp', a.vpn_ecmp_support], ['dns', a.dns_support], ['sgRef', a.security_group_referencing_support], ['multicast', a.multicast_support],
            ['cidrs', a.transit_gateway_cidr_blocks], ['encryption', a.encryption_support], ['desc', a.description]]) if (v !== undefined && v !== null && v !== '') con[k] = Array.isArray(v) ? [...v] : v
          put(n, { ...base, parent_id: rid(n.account_id, n.region), tier: 'spine', con, attrs: { ...attrs,
            'Amazon 측 ASN': a.amazon_side_asn } })
          break
        }
        case 'transit_gateway_attachment': {
          // VPC 어태치먼트는 그 VPC 오른쪽 경계에 붙는다. 피어링·VPN 어태치먼트는 VPC 가 없으므로
          // 스파인에 남긴다(리전 자식은 vpc 이거나 external/spine/stub 여야 한다).
          const vpc = a.resource_type === 'vpc'
            ? N.find((x) => x.type === 'vpc' && x.native_id === a.resource_id) : null
          // #129(2.11.0 · §17.38) — Connect(GRE 오버레이)는 그것이 올라탄 전송 어태치먼트를 든다(`on` = 엔진 노드 id · 못 이었으면 null) ·
          //   피어 = 키가 있을 때만(못 읽었으면 null — 「피어 없음」 [] 과 가른다 · 불변식 ④)
          const cnx = a.resource_type === 'connect'
            ? { connect: { on: cnxOf.get(n.id) || null, transport: a.transport_attachment_id || a.resource_id || null, proto: a.connect_protocol || null,
                peers: Array.isArray(a.connect_peers) ? a.connect_peers.map(peerOf) : null } }
            : {}
          // 콘솔 「세부 정보」 재료(2.13.0 · §17.11 · 검사 166) — 연결 라우팅 테이블·연결 상태·리소스 소유자 · VPC 어태치먼트 옵션과 서브넷.
          //   🔴 키가 없으면 만들지 않는다 — `subnets` 가 없으면 정보창은 「읽지 못함」(거부) 또는 줄 없음(옛 판)을 가른다(「수집 안 함」 🔴 R-03 을 되살리지 않는다)
          const con = {}
          for (const [k, v] of [['assocRt', a.association_route_table_id], ['assocState', a.association_state], ['owner', a.resource_owner_id],
            ['tgwId', a.transit_gateway_id], ['resType', a.resource_type], ['resId', a.resource_id], ['subnets', a.subnet_ids], ['dns', a.dns_support],
            ['ipv6', a.ipv6_support], ['appliance', a.appliance_mode_support], ['sgRef', a.security_group_referencing_support]]) if (v !== undefined && v !== null && v !== '') con[k] = Array.isArray(v) ? [...v] : v
          if (Array.isArray(a.subnet_ids)) con.subnetNodes = a.subnet_ids.map((s) => { const x = subnetByNat.get(n.account_id + '|' + n.region + '|' + s); return x ? x.id : null })
          if (a.resource_type === 'vpc' && !Array.isArray(a.subnet_ids) && (n.partial_reasons || []).some((r) => r && r.api === 'ec2:DescribeTransitGatewayVpcAttachments')) con.subnetsDenied = true
          put(n, vpc
            ? { ...base, parent_id: vpc.id, tier: 'attach', con, attrs }
            : { ...base, parent_id: rid(n.account_id, n.region), tier: 'spine', ...cnx, con,
                attrs: { ...(cnx.connect && cnx.connect.on ? noUnres(attrs) : attrs), 종류: a.resource_type, 대상: a.resource_id,
                  ...(cnx.connect ? { '프로토콜': a.connect_protocol ? String(a.connect_protocol).toUpperCase() : undefined } : {}) } })
          break
        }
        case 'vpc_peering_connection':
          put(n, { ...base, parent_id: rid(n.account_id, n.region), tier: 'spine', attrs: { ...attrs,
            요청자: `${a.requester_vpc_id || '?'} · ${a.requester_region || '?'}`,
            수락자: `${a.accepter_vpc_id || '?'} · ${a.accepter_region || '?'}`,
            CIDR: [...(a.requester_cidr_blocks || []), ...(a.accepter_cidr_blocks || [])].join(', ') },
            // 두 끝의 사실(새 도식이 수집 범위 밖 상대 VPC 를 흐린 칸으로 세운다 · §17.1) — 정보창 글자가 아니라 구조라 attrs 밖에 둔다
            peerSides: [{ vpc: a.requester_vpc_id, acct: a.requester_account_id, region: a.requester_region },
              { vpc: a.accepter_vpc_id, acct: a.accepter_account_id, region: a.accepter_region }],
            // 콘솔 「세부 정보」 요청자/수락자 두 묶음(2.13.0 · §17.11 · 검사 166) — 엔진 값 그대로 · DNS 키는 못 읽었으면 없다
            con: { req: { vpc: a.requester_vpc_id || null, acct: a.requester_account_id || null, region: a.requester_region || null, cidrs: [...(a.requester_cidr_blocks || [])],
              ipv6: [...(a.requester_ipv6_cidr_blocks || [])], ...(typeof a.requester_allow_dns_resolution_from_remote_vpc === 'boolean' ? { dns: a.requester_allow_dns_resolution_from_remote_vpc } : {}) },
            acc: { vpc: a.accepter_vpc_id || null, acct: a.accepter_account_id || null, region: a.accepter_region || null, cidrs: [...(a.accepter_cidr_blocks || [])],
              ipv6: [...(a.accepter_ipv6_cidr_blocks || [])], ...(typeof a.accepter_allow_dns_resolution_from_remote_vpc === 'boolean' ? { dns: a.accepter_allow_dns_resolution_from_remote_vpc } : {}) },
            ...(a.expiration_time ? { exp: a.expiration_time } : {}) } })
          break
        case 'vpc_endpoint':
          // 제공자를 못 찾은 엔드포인트(2.8.0 · `unresolved_refs` = 서비스 이름) — 정보창 「제공자」 칸이 말한다(§17.28 · 「미해결 대상」 줄은 뺀다).
          //   GWLB 엔드포인트 전부 + 고객·파트너 서비스(`…vpce-svc-…`)의 인터페이스 엔드포인트(P2-1) · `epOwner` = 그 서비스의 주인 계정(소비자 쪽)
          const epKind = a.endpoint_type === 'GatewayLoadBalancer' || (a.endpoint_type === 'Interface' && /\.vpce-svc-/.test(String(a.service_name || '')))
          if (epKind) delete attrs['미해결 대상']
          // 🆕 Bedrock(#114 · §17.32) — 엔진이 정확한 이름 + 소유자 amazon 을 확인했을 때만 `aws_service: 'bedrock'` 이다. 🔴 여기서 이름을 다시
          //   가르지 않는다(부분 문자열 금지 · 목록은 엔진 한 곳 — `services/vpc_endpoint.py`) — 키를 그대로 옮길 뿐이다
          put(n, { ...base, parent_id: n.parent_id, tier: 'service', name: svcShort(a.service_name), ...(a.aws_service === 'bedrock' ? { bedrock: true } : {}),
            ...(epKind ? { epUnres: Array.isArray(a.unresolved_refs) ? [...a.unresolved_refs] : [], ...(a.service_owner ? { epOwner: String(a.service_owner) } : {}) } : {}),
            attrs: { ...attrs, type: a.endpoint_type, service: svcShort(a.service_name), '서비스 소유자': a.aws_service === 'bedrock' ? a.service_owner : undefined,
              '프라이빗 DNS 이름 활성화됨': a.private_dns_enabled ? '예' : '아니요',   // 한국어 콘솔 엔드포인트 「세부 정보」 낱말(C-ko-vpce-if)
              'route table': (a.route_table_ids || []).join(', ') || undefined } })
          break
        case 'load_balancer': {
          // 🔴 리스너·타깃 그룹은 별 API 다(`services/elb.py`). 못 읽으면 엔진이 **키를 만들지
          // 않는다** — 여기서 키를 세우면 카드가 「리스너 0」이라 말하고 그건 「없다」는 거짓이 된다.
          // 읽었는데 0개면 `0` 을 넣는다(카드의 `HAS(0)` 는 참이다 — `flow.js:391`).
          const lb = { ...attrs,
            type: a.load_balancer_type === 'application' ? 'ALB' : a.load_balancer_type === 'network' ? 'NLB' : a.load_balancer_type === 'gateway' ? 'GWLB' : a.load_balancer_type,
            scheme: a.scheme, '구성된 AZ': (a.availability_zones || []).join(', ') }
          if (Array.isArray(a.listeners)) {
            lb.listeners = a.listeners.length                       // 카드 = 개수
            if (a.listeners.length) lb['리스너 포트'] = a.listeners.join(', ')  // 개요 탭 = 프로토콜:포트
          }
          if (Array.isArray(a.target_groups)) {
            lb['target group'] = a.target_groups.length ? a.target_groups.join(', ') : 0
          }
          // 이 LB 를 뒤에 둔 엔드포인트 서비스(2.8.0 · GWLB·NLB · §17.28) — 서비스 id 만(`vpce-svc-…` · 이름 앞부분은 리전 접두라 뺀다).
          //   키 없음 = 묻지 않는 종류(ALB)이거나 못 읽었다(그때 LB 가 「일부 못 읽음」) — 줄을 세우지 않는다 · [] = 「없음」
          if (Array.isArray(a.endpoint_service_names)) lb['엔드포인트 서비스'] = a.endpoint_service_names.length ? a.endpoint_service_names.map((x) => String(x).split('.').pop()).join(', ') : '없음'
          // web ACL(#46 · ALB 만) — CloudFront 와 같은 규칙: 키가 있으면 읽었다(값 null = 「없음」), 키가 없으면
          //    못 읽었거나 붙일 수 없는 종류다 → 줄을 세우지 않는다(「없음」은 「보호 안 됨」이라는 사실이다).
          if ('web_acl_arn' in a) {
            const aclName = /\/webacl\/([^/]+)\//.exec(String(a.web_acl_arn || ''))  // ARN → 이름만(계정ID 를 싣지 않는다)
            lb['웹 ACL'] = a.web_acl_arn ? (aclName ? aclName[1] : '이름 모름') : '없음'
          }
          // 등록 대상 수(구조 · 사용자 결정 2026-10-16 · §17.11 LB 「대상」 줄 꺼짐). 헬스는 구조에 없다.
          //   `reg.n` = LB 합 | 'unread'(그룹 하나라도 못 읽음) · `reg.tg` = 그룹 이름 → 수 | 'unread'(엔진 = 같은 자리의 null).
          //   🔴 기록이 없는 옛 스냅샷이면 `reg` 를 만들지 않는다 — 「못 읽음」(사유가 있다)과 「모으기 전 판」은 다른 말이다
          const per = a.target_group_registered_target_counts
          const thDenied = (n.partial_reasons || []).some((r) => r && r.api === 'elasticloadbalancing:DescribeTargetHealth')
          let reg = null
          if (Array.isArray(per) && Array.isArray(a.target_groups)) {
            const tg = {}
            a.target_groups.forEach((g, i) => { tg[g] = Number.isInteger(per[i]) ? per[i] : 'unread' })
            reg = { n: Number.isInteger(a.registered_target_count) ? a.registered_target_count : 'unread', tg }
          } else if (thDenied && Array.isArray(a.target_groups)) reg = { n: 'unread', tg: {} }
          // 못 이은 대상(2.7.0 · `unresolved_targets` = 「<대상 id> <까닭 코드>」 · ADR 0002 §8) — 정보창 「대상」 칸이 까닭을 사람 말로 적는다.
          //   🔴 「미해결 대상」 줄은 뺀다 — 같은 id 를 까닭 없이 한 번 더 적는다(CloudFront 「원본」 줄과 같은 처분)
          const unres = Array.isArray(a.unresolved_targets)
            ? a.unresolved_targets.map((x) => { const i = String(x).lastIndexOf(' '); return i > 0 ? { id: String(x).slice(0, i), code: String(x).slice(i + 1) } : { id: String(x), code: 'not_collected' } }) : null
          if (unres) delete lb['미해결 대상']
          put(n, { ...base, parent_id: n.parent_id, tier: 'lb', ...sgOf(a), attrs: lb, ...(reg ? { reg } : {}), ...(unres ? { lbUnres: unres } : {}) })
          break
        }
        // ---- Route 53 호스팅 영역(2.8.0 · P2-4) — 글로벌(부모 = 계정) · 새 도식이 전역 줄에 세운다(CloudFront 와 같다 · 옛 화면은 티어가 없다)
        case 'route53_hosted_zone':
          unknown.push(n.type)
          // 못 이은 별칭(`unresolved_refs`)은 「별칭」 칸이 주의색 줄로 말한다 — 「미해결 대상」 줄은 같은 이름을 까닭 없이 한 번 더 적어 뺀다(CloudFront 와 같은 처분)
          put(n, { ...base, fallback: true, parent_id: fallbackParent(n), ...(Array.isArray(a.vpc_ids) ? { zoneVpcs: [...a.vpc_ids] } : {}),
            ...(Array.isArray(a.unresolved_refs) ? { zoneUnres: [...a.unresolved_refs] } : {}),
            attrs: { ...Object.fromEntries(Object.entries(attrs).filter(([k]) => k !== '미해결 대상')), '영역': a.zone_name || undefined, '유형': a.private === true ? '프라이빗' : a.private === false ? '퍼블릭' : undefined,
              '레코드 수': a.record_count ?? undefined } })
          break
        case 'cloudfront': {
          // 글로벌(리전 없음 · 부모 = 계정, #39). 새 도식은 계정 상자 맨 위 전역 줄에 세운다(§17.1).
          // 🔴 **티어를 주지 않는다** — 옛 화면은 계정 바로 아래 자식을 놓을 자리가 없다(외부 카드는 리전 레인
          //    안에서만 선다). 리전으로 옮겨 그리면 위치 날조라 옛 화면은 「못 그린 리소스」 표로 신고한다.
          // WAF 는 종류가 아니라 이 한 줄이다(#39 · §3c d). 없음도 글로 쓴다 — 빈 값은 정보창이 줄을 지워
          //    「보호 안 됨」과 「못 읽음」이 같은 모양(줄 없음)이 된다.
          const acl = a.web_acl_id ? String(a.web_acl_id) : ''
          const aclName = /\/webacl\/([^/]+)\//.exec(acl)       // WAFv2 = ARN → 이름만(계정ID 를 싣지 않는다) · Classic = id
          const list = (v) => (Array.isArray(v) && v.length ? v.join(', ') : '없음')
          // 원본 중 LB 주소인데 엔진이 잇지 못한 것(`unresolved_refs` = 엔진이 접은 DNS 이름 · ADR 0002 §7) — 그 원본 옆에 적는다.
          //   🔴 줄을 빼면 「LB 가 아닌 원본」과 같은 모양(선 없음)이 된다 · 따로 적던 「미해결 대상」 줄은 이 말과 겹쳐서 뺀다
          const fold = (d) => String(d).trim().toLowerCase().replace(/\.+$/, '').replace(/^dualstack\./, '')
          const unres = new Set(Array.isArray(a.unresolved_refs) ? a.unresolved_refs : [])
          const origins = Array.isArray(a.origin_domain_names) ? a.origin_domain_names.map((d) => (unres.has(fold(d)) ? `${d} (수집하지 않은 로드밸런서)` : d)) : a.origin_domain_names
          const cfAttrs = { ...attrs }; delete cfAttrs['미해결 대상']
          put(n, { ...base, parent_id: fallbackParent(n), attrs: { ...cfAttrs,
            '도메인': a.domain_name || undefined, '대체 도메인': list(a.aliases), '원본': list(origins),
            '웹 ACL': acl ? (aclName ? aclName[1] : acl) : '없음',
            '상태': a.enabled === true ? '활성화됨' : a.enabled === false ? '비활성화됨' : undefined,   // 한국어 콘솔 배포 목록 「상태」(C-ko-cf-list)
            '요금 클래스': a.price_class || undefined } })
          break
        }
        // ---- 워크로드·DX(#26 이 모은다) — 새 도식(§17.12)이 칸을 정할 사실만 넘긴다 ----
        // 🔴 이 종류에는 티어 규칙이 없다 → `fallback` 을 그대로 달아 **티어를 주지 않는다**(아래 자기 검사가
        //    「미지 타입인데 tier」를 막는다). 도식은 `attachedTo`
        //    (EC2 = 부모 서브넷)·`spans`(EKS·ASG·NFW = 속성의 서브넷 id 를 같은 계정·리전 서브넷 노드로 찾은 것)로 칸을 정한다.
        case 'ec2_instance': {
          const p = byId.get(n.parent_id)
          unknown.push(n.type)
          // 그 인스턴스를 지금 가진 ASG(2.8.0 · 엔진 `lb_targets.memberships`) — 새 도식이 LB → 인스턴스 선을 LB → ASG 선으로 접는 재료(§17.27 「ASG」)
          put(n, { ...base, fallback: true, parent_id: fallbackParent(n), attachedTo: p && p.type === 'subnet' ? p.id : undefined,
            az: a.availability_zone || undefined, ...sgOf(a), ...(a.auto_scaling_group_id ? { asg: String(a.auto_scaling_group_id) } : {}),
            attrs: { ...attrs, type: a.instance_type || undefined, AZ: a.availability_zone || undefined,
              '프라이빗 IP': a.private_ip_address || undefined, '보안 그룹': cnt(a.security_group_ids) } })
          break
        }
        case 'eks_cluster':
        case 'auto_scaling_group':
        case 'network_firewall': {
          const sp = spansOf(n)
          // ASG 는 엔진 부모가 리전뿐이다(ASG API 에 VPC 가 없다 · `services/auto_scaling_group.py:100`). 서브넷이 VPC 를
          //   말하므로 그 VPC 를 부모로 삼는다 — 서브넷이 VPC 둘 이상에 걸치거나 안 찾아지면 엔진 부모 그대로(새 도식 = 잔차)
          const vs = [...new Set(sp.map((s) => s.parent_id))]
          const parent = n.type === 'auto_scaling_group' && vs.length === 1 ? vs[0] : fallbackParent(n)
          const ex = n.type === 'eks_cluster'
            ? { '버전': a.version || undefined, '엔드포인트': [a.endpoint_public_access ? '퍼블릭' : null, a.endpoint_private_access ? '프라이빗' : null].filter(Boolean).join(' · ') || undefined,
                '퍼블릭 액세스 CIDR': Array.isArray(a.public_access_cidrs) && a.public_access_cidrs.length ? a.public_access_cidrs.join(', ') : undefined,
                '보안 그룹': cnt([...(a.security_group_ids || []), ...(a.cluster_security_group_id ? [a.cluster_security_group_id] : [])]) }
            : n.type === 'auto_scaling_group'
              ? { '최소 · 원하는 · 최대': [a.min_size, a.desired_capacity, a.max_size].some((v) => v != null) ? [a.min_size, a.desired_capacity, a.max_size].map((v) => v ?? '?').join(' · ') : undefined,
                  '구성된 AZ': Array.isArray(a.availability_zones) ? a.availability_zones.join(', ') : undefined }
              // 정책은 이름만 — ARN 은 계정ID 를 싣는다(CloudFront web ACL 과 같은 처분)
              : { '정책': a.firewall_policy_arn ? String(a.firewall_policy_arn).split('/').pop() : undefined,
                  '엔드포인트': cnt(a.firewall_endpoint_ids), '삭제 방지': a.delete_protection === true ? '켜짐' : a.delete_protection === false ? '꺼짐' : undefined }
          unknown.push(n.type)
          // EKS 보안 그룹 = 추가 SG + 클러스터 SG(엔진은 둘을 따로 싣는다) · 위 「보안 그룹」 개수 줄과 같은 합
          const sg = n.type === 'eks_cluster' && Array.isArray(a.security_group_ids)
            ? { sgIds: [...a.security_group_ids, ...(a.cluster_security_group_id ? [a.cluster_security_group_id] : [])] } : n.type === 'eks_cluster' ? {} : null
          // ASG 의 대상 그룹(2.8.0) — 이름만(ARN 은 계정ID 를 싣는다 · ECS 와 같은 처분). 정보창 「LB 대상 그룹」 칸 + 선 접기의 재료
          const asgTg = n.type === 'auto_scaling_group' && Array.isArray(a.target_group_arns)
            ? { targetGroups: a.target_group_arns.map((t) => (/:targetgroup\/([^/]+)\//.exec(String(t)) || [])[1] || '?') } : {}
          // 방화벽 엔드포인트 id(2.8.0 · §17.28) — 정보창 「엔드포인트」 칸이 엔드포인트마다 그 칸 · 선을 말한다(못 읽었으면 키 없음)
          const fwEps = n.type === 'network_firewall' && Array.isArray(a.firewall_endpoint_ids) ? { fwEps: [...a.firewall_endpoint_ids] } : {}
          put(n, { ...base, fallback: true, parent_id: parent, spans: Array.isArray(a.subnet_ids) ? sp.map((s) => s.id) : undefined, ...(sg || {}), ...asgTg, ...fwEps, attrs: { ...attrs, ...ex } })
          break
        }
        // ---- W2 워크로드(§17.12) — 서브넷 하나에 서는 것(RDS·EIC·노트북 = 엔진 부모가 서브넷이면 그 칸) ----
        //   RDS 의 엔진 부모가 VPC 면 「같은 AZ 서브넷이 그룹에 둘 이상」이라 엔진이 고르지 않은 것이다 → 서브넷을 지어내지 않는다
        //   (`services/rds_db_instance.py`). `sgIds` 는 정보창 「보안 그룹」 칸의 재료다 — 못 읽었으면 키째 없다(빈 배열 = 읽었고 없다).
        case 'rds_db_instance':
        case 'ec2_instance_connect_endpoint':
        case 'sagemaker_notebook_instance': {
          const p = byId.get(n.parent_id)
          const ex = n.type === 'rds_db_instance'
            ? { '엔진': a.engine || undefined, '인스턴스 클래스': a.instance_class || undefined, '다중 AZ': yn(a.multi_az), AZ: a.availability_zone || undefined,
                '보조 AZ': a.secondary_availability_zone || undefined, '퍼블릭 액세스': yn(a.publicly_accessible), '서브넷 그룹': a.subnet_group_name || undefined,
                '클러스터': a.db_cluster_identifier || undefined,
                '자리': p && p.type === 'vpc' ? '서브넷 특정 못함 — 그룹에 같은 AZ 서브넷이 둘 이상' : p && p.type === 'region' ? 'VPC 밖 — 서브넷 그룹 없음' : undefined }
            : n.type === 'ec2_instance_connect_endpoint'
              ? { AZ: a.availability_zone || undefined, '클라이언트 IP 보존': yn(a.preserve_client_ip) }
              : { type: a.instance_type || undefined, '직접 인터넷 접근': onOff(a.direct_internet_access) }
          unknown.push(n.type)
          put(n, { ...base, fallback: true, parent_id: fallbackParent(n), attachedTo: p && p.type === 'subnet' ? p.id : undefined,
            az: a.availability_zone || undefined, ...sgOf(a),
            ...(n.type === 'rds_db_instance' && Array.isArray(a.subnet_group_subnet_ids) ? { groupSubnets: natSubnets(n, a.subnet_group_subnet_ids) } : {}),
            attrs: { ...attrs, ...ex } })
          break
        }
        // ---- #114(2.9.0 · §17.32) ElastiCache — RDS 와 같다(엔진 부모가 서브넷이면 그 칸) · 다중 AZ Memcached = EKS 와 같다(부모 VPC + `subnet_ids`).
        //   `cacheRg` = 복제 그룹(정보창 「복제 그룹」 칸 재료) — id 가 없으면 키째 없다(= 들지 않는다) · 멤버를 못 읽었으면 `members: null`
        case 'elasticache_cluster': {
          const p = byId.get(n.parent_id)
          const sp = Array.isArray(a.subnet_ids) ? spansOf(n) : null
          const gs = Array.isArray(a.subnet_group_subnet_ids) ? natSubnets(n, a.subnet_group_subnet_ids) : null
          unknown.push(n.type)
          put(n, { ...base, fallback: true, parent_id: fallbackParent(n), attachedTo: p && p.type === 'subnet' ? p.id : undefined,
            ...(sp ? { spans: sp.map((s) => s.id) } : {}), ...sgOf(a),
            ...(gs ? { groupSubnets: gs } : {}),
            ...(a.replication_group_id ? { cacheRg: { id: a.replication_group_id, members: Array.isArray(a.replication_group_member_ids) ? [...a.replication_group_member_ids] : null } } : {}),
            attrs: { ...attrs, '엔진': a.engine || undefined, '버전': a.engine_version || undefined, '노드 유형': a.cache_node_type || undefined,
              '노드 수': a.num_cache_nodes ?? undefined, AZ: Array.isArray(a.availability_zones) && a.availability_zones.length ? a.availability_zones.join(', ') : undefined,
              '포트': a.port ?? undefined, '복제 그룹': a.replication_group_id || undefined, '클러스터 모드': yn(a.replication_group_cluster_mode),
              '다중 AZ': onOff(a.replication_group_multi_az), '자동 장애 조치': onOff(a.replication_group_automatic_failover),
              '전송 중 암호화': yn(a.transit_encryption_enabled), '저장 시 암호화': yn(a.at_rest_encryption_enabled), '서브넷 그룹': a.subnet_group_name || undefined,
              '표시 위치': p && p.type === 'vpc' && !sp ? '서브넷 특정 불가 — 서브넷 그룹에 같은 AZ 의 서브넷이 둘 이상' : p && p.type === 'region' ? 'VPC 확인 불가 — 서브넷 그룹을 읽지 못함' : undefined } })
          break
        }
        // ---- #114(2.9.0 · §17.32) Aurora / RDS DB 클러스터 — 자기 ENI 가 없다 → VPC 에 산다(엔진 부모 = VPC · 걸침 없음 = VPC 오른쪽 열).
        //   `dbMembers` = 멤버 인스턴스 native id(정보창 「멤버 인스턴스」 칸이 그 RDS 카드를 누르게 한다) · 쓰기/읽기 역할은 엔진이 싣지 않는다
        case 'rds_db_cluster': {
          const p = byId.get(n.parent_id)
          unknown.push(n.type)
          const acu = a.serverless_v2_min_capacity != null || a.serverless_v2_max_capacity != null ? `${a.serverless_v2_min_capacity ?? '?'} – ${a.serverless_v2_max_capacity ?? '?'} ACU` : undefined
          put(n, { ...base, fallback: true, parent_id: fallbackParent(n), ...sgOf(a),
            ...(Array.isArray(a.subnet_group_subnet_ids) ? { groupSubnets: natSubnets(n, [...a.subnet_group_subnet_ids]) } : {}),
            ...(Array.isArray(a.member_instance_ids) ? { dbMembers: [...a.member_instance_ids] } : {}),
            attrs: { ...attrs, '엔진': a.engine || undefined, '엔진 모드': a.engine_mode || undefined, '버전': a.engine_version || undefined,
              '라이터 엔드포인트': a.endpoint || undefined, '리더 엔드포인트': a.reader_endpoint || undefined, '포트': a.port ?? undefined,
              '다중 AZ': yn(a.multi_az), AZ: Array.isArray(a.availability_zones) && a.availability_zones.length ? a.availability_zones.join(', ') : undefined,
              'Serverless v2 용량': acu, '저장 시 암호화': yn(a.storage_encrypted), '퍼블릭 액세스': yn(a.publicly_accessible), '서브넷 그룹': a.subnet_group_name || undefined,
              '표시 위치': p && p.type === 'vpc' ? 'VPC — 클러스터는 서브넷에 네트워크 인터페이스를 두지 않습니다(멤버 인스턴스는 각자의 서브넷에 표시됩니다)' : p && p.type === 'region' ? 'VPC 확인 불가 — 서브넷 그룹을 읽지 못함' : undefined } })
          break
        }
        // ---- #114(2.9.0 · §17.32) OpenSearch — VPC 도메인 = Studio 도메인과 같다(엔진 부모 VPC + 걸친 서브넷) · 공개 도메인 = 서브넷이 비었다(읽었다)
        //   → `noSubnets` = 리전 줄(§17.12 ①) · 상세를 못 읽었으면(키 없음) 걸침도 없다 → 잔차(「구성 일부를 읽지 못해」)
        case 'opensearch_domain': {
          const sp = spansOf(n)
          unknown.push(n.type)
          put(n, { ...base, fallback: true, parent_id: fallbackParent(n), spans: Array.isArray(a.subnet_ids) ? sp.map((s) => s.id) : undefined,
            ...(Array.isArray(a.subnet_ids) && !a.subnet_ids.length ? { noSubnets: true } : {}), ...sgOf(a),
            attrs: { ...attrs, '엔진 버전': a.engine_version || undefined, '인스턴스 유형': a.instance_type || undefined, '인스턴스 수': a.instance_count ?? undefined,
              '전용 마스터 노드': yn(a.dedicated_master_enabled), '영역 인식': yn(a.zone_awareness_enabled), '엔드포인트': a.endpoint || undefined,
              'AZ': Array.isArray(a.availability_zones) && a.availability_zones.length ? a.availability_zones.join(', ') : undefined,
              '저장 시 암호화': yn(a.encryption_at_rest_enabled), '노드 간 암호화': yn(a.node_to_node_encryption_enabled), 'HTTPS 필요': yn(a.enforce_https) } })
          break
        }
        // ---- #114(2.9.0 · §17.32) MSK — ASG 와 같다(엔진 부모 = 리전 · API 에 VPC 가 없다 → 걸친 서브넷들의 VPC 를 부모로 삼는다 ·
        //   VPC 둘 이상에 걸치면(서버리스 다중 VPC) 또는 하나도 안 찾아지면 엔진 부모 그대로 = 잔차)
        case 'msk_cluster': {
          const sp = spansOf(n); const vs = [...new Set(sp.map((s) => s.parent_id))]
          unknown.push(n.type)
          put(n, { ...base, fallback: true, parent_id: vs.length === 1 ? vs[0] : fallbackParent(n), spans: Array.isArray(a.subnet_ids) ? sp.map((s) => s.id) : undefined, ...sgOf(a),
            attrs: { ...attrs, '종류': a.cluster_type === 'PROVISIONED' ? '프로비저닝' : a.cluster_type === 'SERVERLESS' ? '서버리스' : a.cluster_type || undefined,
              'Kafka 버전': a.kafka_version || undefined, '브로커 유형': a.broker_instance_type || undefined, '브로커 수': a.number_of_broker_nodes ?? undefined,
              '클라이언트-브로커 간 암호화': a.client_broker_encryption || undefined, '브로커 네트워크 인터페이스': cnt(a.broker_eni_ids) } })
          break
        }
        // ---- 서브넷에 걸치는 것(ECS 서비스·SageMaker 도메인·엔드포인트 = EKS·ASG 와 같은 `spans`) ----
        //   ECS·엔드포인트는 API 에 VPC 가 없다 → ASG 처럼 서브넷들의 VPC 를 부모로 삼는다.
        //   `noSubnets` = 구성을 **읽었고** 서브넷이 비었다(ECS 가 awsvpc 가 아님 · 엔드포인트에 VPC 설정 없음). 못 읽은 것(키 없음)과 다르다.
        case 'ecs_service':
        case 'sagemaker_domain':
        case 'sagemaker_endpoint': {
          const sp = spansOf(n)
          const vs = [...new Set(sp.map((s) => s.parent_id))]
          const parent = n.type !== 'sagemaker_domain' && vs.length === 1 ? vs[0] : fallbackParent(n)
          const ex = n.type === 'ecs_service'
            ? { '클러스터': a.cluster_name || undefined, '시작 유형': a.launch_type || undefined,
                '용량 공급자': Array.isArray(a.capacity_providers) && a.capacity_providers.length ? a.capacity_providers.join(', ') : undefined,
                '퍼블릭 IP 자동 할당': onOff(a.assign_public_ip) }
            : n.type === 'sagemaker_domain'
              ? { '네트워크 접근': a.app_network_access_type === 'VpcOnly' ? 'VPC 만' : a.app_network_access_type === 'PublicInternetOnly' ? '인터넷 직접' : a.app_network_access_type || undefined }
              : { '엔드포인트 설정': a.endpoint_config_name || undefined, '모델': Array.isArray(a.model_names) ? a.model_names.join(', ') || '없음' : undefined }
          unknown.push(n.type)
          put(n, { ...base, fallback: true, parent_id: parent, spans: Array.isArray(a.subnet_ids) ? sp.map((s) => s.id) : undefined,
            ...(Array.isArray(a.subnet_ids) && !a.subnet_ids.length ? { noSubnets: true } : {}), ...sgOf(a),
            ...(n.type === 'ecs_service' && Array.isArray(a.target_group_arns)
              // 대상 그룹은 이름만 — ARN 은 계정ID 를 싣는다(NFW 정책과 같은 처분)
              ? { targetGroups: a.target_group_arns.map((t) => (/:targetgroup\/([^/]+)\//.exec(String(t)) || [])[1] || '?') } : {}),
            attrs: { ...attrs, ...ex } })
          break
        }
        // ---- TGW 라우트 테이블 — 그림의 아이콘이 아니다(VPC 라우트 표와 같은 판단 · §13-55). 새 화면은 TGW 정보창의
        //   「라우트 테이블」 칸으로 보인다(§17.11). 🔴 못 읽은 것은 `null` 로 넘긴다 — 빈 배열로 접으면 「연결 없음」과
        //   「권한 부족으로 못 읽음」이 같은 화면이 된다(엔진은 키를 만들지 않는다 · `services/transit_gateway_route_table.py`).
        //   경로를 읽었는지 = `blackhole_destinations` 가 있는지(경로 호출 하나에서 같이 나온다).
        case 'transit_gateway_route_table': {
          const routes = []
          for (const e of snap.edges) {
            if (e.type !== 'route' || e.source !== n.id) continue
            const t = byId.get(e.target); const ra = e.attributes || {}; const st = new Set(ra.static_destinations || [])
            for (const d of ra.destinations || []) routes.push({ d, att: t ? t.native_id : String(e.target).split('/').pop(), stat: st.has(d) })
          }
          routes.sort((x, y) => (x.d < y.d ? -1 : x.d > y.d ? 1 : x.att < y.att ? -1 : x.att > y.att ? 1 : 0))
          const read = (v) => (Array.isArray(v) ? v : null)
          // 2.13.0(§17.11 콘솔 4탭 · 검사 166) — 엔진 줄을 칸으로 쪼갠 행: 연결·전파 「<어태치먼트> <리소스 유형> <리소스 ID> <상태>」 ·
          //   경로 「<CIDR|pl-> <어태치먼트> <리소스 유형> <리소스 ID> <경로 유형> <경로 상태>」(빈 칸 `-`) · 접두사 목록 참조 7칸.
          //   키가 없으면 `undefined`(옛 판 — 화면은 옛 id 목록으로 그린다) · 접두사 목록 참조를 못 읽었으면 `null`(「읽지 못함」)
          const cut = (v) => (Array.isArray(v) ? v.map((x) => String(x).split(' ').map((c) => (c === '-' ? '' : c))) : undefined)
          const plrDenied = (n.partial_reasons || []).some((r) => r && r.api === 'ec2:GetTransitGatewayPrefixListReferences')
          unknown.push(n.type)
          put(n, { ...base, fallback: true, parent_id: fallbackParent(n),
            tgwRt: { state: a.state || null, assoc: read(a.association_attachment_ids), prop: read(a.propagation_attachment_ids), bh: read(a.blackhole_destinations),
              routes: Array.isArray(a.blackhole_destinations) ? routes : null,
              assocRows: cut(a.associations), propRows: cut(a.propagations), routeRows: cut(a.routes),
              plr: Array.isArray(a.prefix_list_references) ? cut(a.prefix_list_references) : plrDenied ? null : undefined,
              denied: (n.partial_reasons || []).map((r) => r.api).filter(Boolean) },
            attrs: { ...attrs, '기본 연결 표': yn(a.default_association), '기본 전파 표': yn(a.default_propagation) } })
          break
        }
        // ---- 파생 노드 2종(스키마 2.7.0 · 엔진 `lb_targets.py` 가 LB 대상에서 짓는다 · §17.27) ----
        //   Lambda = 리전 자원 줄(VPC 를 모른다) · 외부 IP = 아이콘 없음(LB → 출구 포트 선이 대표한다) — 둘 다 티어가 없다(새 도식만 그린다)
        case 'lambda_function': {
          // 2.8.0(P2-6) — 수집한 함수는 `subnet_ids` 를 든다: VPC 에 붙었으면 그 서브넷들의 VPC 가 부모 · 걸친 서브넷 = `spans`(ECS 와 같다) ·
          //   `[]` = 읽었고 VPC 밖 = 리전 자원 줄 · 키 없음 = LB 대상에서 지은 파생 노드(VPC 를 모른다 — 리전 자원 줄 · 2.7.0 그대로)
          const sp = Array.isArray(a.subnet_ids) ? spansOf(n) : []
          const vs = [...new Set(sp.map((s) => s.parent_id))]
          unknown.push(n.type)
          put(n, { ...base, fallback: true, parent_id: vs.length === 1 ? vs[0] : fallbackParent(n), ...(Array.isArray(a.subnet_ids) ? { fnRead: true } : {}),
            ...(Array.isArray(a.subnet_ids) && a.subnet_ids.length ? { spans: sp.map((s) => s.id), ...sgOf(a) } : {}),
            attrs: { ...attrs, '함수 이름': a.function_name || undefined, '런타임': a.runtime || undefined,
              '별칭·버전': Array.isArray(a.qualifiers) && a.qualifiers.length ? a.qualifiers.join(', ') : undefined } })
          break
        }
        // ---- 배치 D(스키마 2.10.0 · §17.34~37) — 넷 다 티어가 없다(새 도식만 그린다 · 옛 화면 = 잔차)
        //   탄력적 IP = 카드가 아니다(주인 정보창 줄 · 주인이 없으면 왼쪽 아래 「!」 목록 · §17.34)
        case 'elastic_ip':
          unknown.push(n.type)
          put(n, { ...base, fallback: true, parent_id: fallbackParent(n), ...(eipOwner.has(n.id) ? { eipOwner: eipOwner.get(n.id) } : {}),
            ...(a.associated === false ? { eipIdle: true } : {}), ...(a.association_unresolved ? { eipWhy: String(a.association_unresolved) } : {}),
            attrs: { ...noUnres(attrs), '퍼블릭 IPv4 주소': a.public_ip || undefined, '네트워크 경계 그룹': a.network_border_group || undefined,
              '서비스 관리형': a.service_managed || undefined, '연결': a.associated === true ? '예' : a.associated === false ? '아니요' : undefined } })
          break
        //   API Gateway = 리전 자원 줄(§17.35) · `apiUnres` = 못 이은 VPC 링크(까닭) · `apiRead` = 통합을 읽었다(키가 있다)
        case 'api_gateway':
          unknown.push(n.type)
          put(n, { ...base, fallback: true, parent_id: fallbackParent(n), ...(Array.isArray(a.vpc_link_ids) ? { apiRead: true, apiLinks: [...a.vpc_link_ids] } : {}),
            ...(Array.isArray(a.unresolved_vpc_links) ? { apiUnres: whyLines(a.unresolved_vpc_links) } : {}),
            attrs: { ...noUnres(attrs), '프로토콜': a.protocol === 'WEBSOCKET' ? 'WebSocket' : a.protocol || undefined,
              '엔드포인트 유형': Array.isArray(a.endpoint_types) && a.endpoint_types.length ? a.endpoint_types.join(', ') : undefined } })
          break
        //   RDS 프록시 = VPC(엔진 부모) + 걸친 서브넷 `spans`(ECS 와 같다 · §17.36) · `proxyUnres` = 못 이은 대상 · `proxyMembers` = 클러스터 대상의 멤버
        case 'rds_db_proxy': {
          unknown.push(n.type)
          const sp = spansOf(n)
          put(n, { ...base, fallback: true, parent_id: fallbackParent(n), spans: Array.isArray(a.subnet_ids) ? sp.map((x) => x.id) : undefined, ...sgOf(a),
            ...(Array.isArray(a.unresolved_proxy_targets) ? { proxyUnres: whyLines(a.unresolved_proxy_targets) } : {}),
            ...(Array.isArray(a.target_instance_ids) ? { proxyMembers: [...a.target_instance_ids] } : {}), ...('target_count' in a ? { proxyRead: true } : {}),
            attrs: { ...noUnres(attrs), '엔진 계열': a.engine_family || undefined, '엔드포인트': a.endpoint || undefined, 'TLS 필요': yn(a.require_tls),
              '유휴 클라이언트 연결 제한 시간': a.idle_client_timeout != null ? `${a.idle_client_timeout}초` : undefined, '대상 수': a.target_count ?? undefined } })
          break
        }
        //   Global Accelerator = 전역 줄(CloudFront 옆 · §17.37) · `gaUnres` = 못 이은 엔드포인트(까닭 · 리전) · `gaEmpty` = 빈 엔드포인트 그룹 리전
        case 'global_accelerator':
          unknown.push(n.type)
          put(n, { ...base, fallback: true, parent_id: fallbackParent(n), ...(Array.isArray(a.endpoint_regions) ? { gaRead: true } : {}),
            ...(Array.isArray(a.unresolved_endpoints) ? { gaUnres: whyLines(a.unresolved_endpoints) } : {}),
            ...(Array.isArray(a.empty_endpoint_regions) ? { gaEmpty: [...a.empty_endpoint_regions] } : {}),
            // 엔드포인트 그룹마다 등록된 대상(「<리전> <종류> <이름>」 · 2026-10-21) — 정보창이 리전마다 한 줄로 말하는 재료
            ...(Array.isArray(a.endpoint_targets) ? { gaTargets: a.endpoint_targets.map((x) => { const [region, kind, ...ref] = String(x).split(' '); return { region, kind, ref: ref.join(' ') } }) } : {}),
            attrs: { ...noUnres(attrs), '사용': a.enabled === true ? '켜짐' : a.enabled === false ? '꺼짐' : undefined, 'IP 주소 유형': a.ip_address_type || undefined,
              '고정 IP': Array.isArray(a.static_ips) && a.static_ips.length ? a.static_ips.join(', ') : undefined, 'DNS 이름': a.dns_name || undefined,
              '리스너': Array.isArray(a.listener_ports) && a.listener_ports.length ? a.listener_ports.join(', ') : undefined } })
          break
        // ---- #129(스키마 2.11.0 · §17.39~40) — 셋 다 티어가 없다(새 도식만 그린다 · 옛 화면 = 잔차)
        //   Cloud WAN 코어 네트워크 = 전역 줄(Global Accelerator 옆 · §17.39) · `cwanUnres` = 못 이은 VPC 연결(까닭 · 리전) · `cwanOther` = 선으로 그리지 않는 연결 종류
        case 'core_network':
          unknown.push(n.type)
          put(n, { ...base, fallback: true, parent_id: fallbackParent(n), ...('attachment_count' in a ? { cwanRead: true } : {}),
            ...(Array.isArray(a.unresolved_attachments) ? { cwanUnres: whyLines(a.unresolved_attachments) } : {}),
            ...(Array.isArray(a.other_attachments) ? { cwanOther: a.other_attachments.map((x) => { const [id, kind, loc] = String(x).split(' '); return { id, kind, loc: loc || null } }) } : {}),
            attrs: { ...noUnres(attrs), '글로벌 네트워크': a.global_network_id || undefined, '설명': a.description || undefined,
              '엣지 위치': Array.isArray(a.edge_locations) && a.edge_locations.length ? a.edge_locations.join(', ') : undefined, '연결 수': a.attachment_count ?? undefined } })
          break
        //   Lattice 서비스 네트워크 = 리전 자원 줄(§17.40) · `lsnRead` = VPC 연관을 읽었다 · 서비스 연관 = 정보창 · 못 이은 것 = 까닭 줄
        case 'lattice_service_network':
          unknown.push(n.type)
          put(n, { ...base, fallback: true, parent_id: fallbackParent(n), ...('vpc_association_count' in a ? { lsnRead: true } : {}),
            ...('service_association_count' in a ? { lsnSvcRead: true, lsnSvcs: [...(lsnSvcs.get(n.id) || [])].sort() } : {}),
            ...(Array.isArray(a.unresolved_vpc_associations) ? { lsnUnres: whyLines(a.unresolved_vpc_associations) } : {}),
            ...(Array.isArray(a.unresolved_service_associations) ? { lsnSvcUnres: whyLines(a.unresolved_service_associations) } : {}),
            attrs: { ...noUnres(attrs), '연결된 VPC 수': a.vpc_association_count ?? undefined, '연결된 서비스 수': a.service_association_count ?? undefined } })
          break
        //   Lattice 서비스 = 리전 자원 줄(서비스 네트워크 다음 · §17.40) · `lsvcRead` = 대상 그룹을 읽었다 · `lsvcUnres` = 못 이은 대상(IP · 이 수집본에 없음)
        case 'lattice_service':
          unknown.push(n.type)
          put(n, { ...base, fallback: true, parent_id: fallbackParent(n), lsvcNets: [...(lsvcNets.get(n.id) || [])].sort(),
            ...(Array.isArray(a.target_groups) ? { lsvcRead: true, lsvcTgs: a.target_groups.map((x) => { const [name, kind, proto, port] = String(x).split(' '); return { name, kind, proto, port } }) } : {}),
            ...(Array.isArray(a.empty_target_groups) ? { lsvcEmpty: [...a.empty_target_groups] } : {}),
            ...(Array.isArray(a.unresolved_lattice_targets) ? { lsvcUnres: whyLines(a.unresolved_lattice_targets) } : {}),
            attrs: { ...noUnres(attrs), 'DNS 이름': a.dns_name || undefined, '사용자 지정 도메인': a.custom_domain_name || undefined } })
          break
        // ---- WAF 웹 ACL(2.12.0 · ADR 0014 · §17.41) — CloudFront 범위 = 전역 줄(부모 = 계정 · 리전 없음) · 리전 범위 = 리전 자원 줄 ·
        //   보호 대상 = `waf_protection` 선(아래 EDGE_KIND) · `wafUnres` = 이 수집본에 없는 보호 대상(「<id> <까닭>」) · `wafRead` = 구성을 읽었다(규칙 수가 있다)
        case 'waf_web_acl':
          unknown.push(n.type)
          put(n, { ...base, fallback: true, parent_id: fallbackParent(n), wafScope: a.scope === 'cloudfront' ? 'cloudfront' : 'regional',
            ...('rule_count' in a ? { wafRead: true } : {}),
            ...(Array.isArray(a.unresolved_protected) ? { wafUnres: whyLines(a.unresolved_protected) } : {}),
            attrs: { ...noUnres(attrs), '범위': a.scope === 'cloudfront' ? 'CloudFront(전역)' : '리전',
              '기본 동작': a.default_action === 'block' ? '차단' : a.default_action === 'allow' ? '허용' : a.default_action || undefined,
              '규칙 수': a.rule_count ?? undefined,
              '규칙': Array.isArray(a.rule_names) && a.rule_names.length ? a.rule_names.join(', ') : undefined,
              '관리형 규칙 그룹': Array.isArray(a.managed_rule_groups) && a.managed_rule_groups.length ? a.managed_rule_groups.join(', ') : undefined,
              'Firewall Manager 관리': yn(a.firewall_manager), '용량(WCU)': a.capacity ?? undefined } })
          break
        case 'external_ip':
          unknown.push(n.type)
          put(n, { ...base, fallback: true, parent_id: fallbackParent(n), ...(Array.isArray(a.route_exits) ? { exits: [...a.route_exits] } : {}),
            attrs: { ...attrs, IP: a.ip || undefined } })
          break
        // ---- S3 버킷(2.8.0 · P2-3 · §17.31) — 엔진이 CloudFront S3 원본 도메인에서 짓는 파생 노드 · 글로벌(부모 = 배포의 계정) · 새 도식이 전역 줄에 세운다
        //   🔴 S3 를 수집하지 않는다 — 주인 계정·정책은 모른다(그 줄을 만들지 않는다) · 「출처」 줄이 그렇게 말한다
        case 's3_bucket':
          unknown.push(n.type)
          put(n, { ...base, fallback: true, parent_id: fallbackParent(n), attrs: { ...attrs, '버킷': a.bucket_name || undefined,
            '버킷 엔드포인트': a.website === true ? '정적 웹사이트' : a.website === false ? 'REST' : undefined,
            '리전': Array.isArray(a.regions) && a.regions.length ? a.regions.join(', ') : undefined,
            '출처': 'CloudFront 오리진 도메인에서 확인한 이름 — S3 는 수집하지 않습니다(소유 계정·정책은 알 수 없음)' } })
          break
        case 'direct_connect_connection':
          unknown.push(n.type)
          put(n, { ...base, fallback: true, parent_id: fallbackParent(n), attrs: { ...attrs, '포트 속도': a.bandwidth || undefined, VLAN: a.vlan ?? undefined,
            '파트너 · 제공자': [a.partner_name, a.provider_name].filter(Boolean).join(' · ') || undefined,
            '논리 이중화': a.has_logical_redundancy === 'yes' ? '예' : a.has_logical_redundancy === 'no' ? '아니요' : a.has_logical_redundancy || undefined, '위치': a.location || undefined, LAG: a.lag_id || undefined } })
          break
        case 'direct_connect_gateway':
          unknown.push(n.type)
          // VGW 연결 = 속성의 id 그대로(엔진에 VGW 노드는 없다). 못 읽었으면 키가 없다(`direct_connect_gateway.py`)
          put(n, { ...base, fallback: true, parent_id: fallbackParent(n), ...(Array.isArray(a.associated_virtual_private_gateway_ids) ? { vgwIds: [...a.associated_virtual_private_gateway_ids] } : {}),
            attrs: { ...attrs, 'Amazon 측 ASN': a.amazon_side_asn ?? undefined } })
          break
        // ---- Site-to-Site VPN 세 종류(스키마 2.6.0) — 새 도식이 리전 자원 줄에 세운다(§17.1). 옛 화면에는 티어가 없다(DX 와 같은 처분).
        //   🔴 사전 공유 키·장비 설정은 엔진이 싣지 않는다(`services/vpn_connection.py`) — 여기도 그런 키를 만들지 않는다.
        //   VGW 가 붙은 VPC = 엔진 `attachment` VGW → VPC(아래 엣지 변환이 IGW 처럼 건너뛴다) → 새 도식이 그 VPC 위 변에 포트를 세우는 재료
        case 'vpn_gateway':
          unknown.push(n.type)
          put(n, { ...base, fallback: true, parent_id: fallbackParent(n),
            vpcIds: snap.edges.filter((e) => e.type === 'attachment' && e.source === n.id && typeOfId(e.target) === 'vpc').map((e) => e.target).sort(),
            attrs: { ...attrs, '종류': a.type || undefined, 'Amazon 측 ASN': a.amazon_side_asn ?? undefined } })
          break
        case 'customer_gateway':
          unknown.push(n.type)
          put(n, { ...base, fallback: true, parent_id: fallbackParent(n), attrs: { ...attrs, '종류': a.type || undefined, 'IP 주소': a.ip_address || undefined,
            'BGP ASN': a.bgp_asn ?? undefined, '디바이스 이름': a.device_name || undefined, '인증서 사용': yn(a.uses_certificate) } })
          break
        case 'vpn_connection':
          unknown.push(n.type)
          put(n, { ...base, fallback: true, parent_id: fallbackParent(n),
            vpn: { cgw: a.customer_gateway_id || null, vgw: a.vpn_gateway_id || null, tgw: a.transit_gateway_id || null,
              routes: Array.isArray(a.static_route_destinations) ? [...a.static_route_destinations] : null },
            attrs: { ...attrs, 'VPN 유형': a.type || undefined, '카테고리': a.category || undefined, '라우팅': a.static_routes_only === true ? '정적' : a.static_routes_only === false ? '동적' : undefined,
              '터널 수': a.tunnel_count ?? undefined, '외부 터널 IP': Array.isArray(a.tunnel_outside_ips) && a.tunnel_outside_ips.length ? a.tunnel_outside_ips.join(', ') : undefined,
              'Cloud WAN 코어 네트워크': a.core_network_arn ? '연결됨' : undefined } })
          break
        default:
          if (LIST_TYPES[n.type]) {
            const vpc = byId.get(n.parent_id)
            // detail 은 「그림에서 빠진 대신 표에서 무엇을 볼 수 있나」다 — 각 종류의 조사 단서를 적는다.
            const detail = n.type === 'route_table'
              ? `라우트 ${a.route_count ?? '?'} · 연관 ${a.association_count ?? '?'}${a.main ? ' · main' : ''}`
              : n.type === 'network_acl'
                ? `규칙 ${a.entry_count ?? '?'} · 연관 ${a.association_count ?? '?'}${a.is_default ? ' · 기본' : ''}`
                : `인바운드 ${a.ingress_rule_count ?? '?'} · 아웃바운드 ${a.egress_rule_count ?? '?'}` +
                  (a.self_referencing ? ' · 자기참조' : '')
            // 라우팅 테이블 = 콘솔 상세 화면의 재료(2.13.0 · §17.11 「라우팅 테이블」 · 검사 166) — 탭 넷(라우팅 · 서브넷 연결 · 엣지 연결 · 라우팅 전파).
            //   `routes` 키가 없는 옛 판 = 대상별 엣지에서 되짚는다(local 없음 · 상태는 블랙홀만 · `old: true` — 정보창이 「옛 수집본」이라 말한다)
            const rt = n.type !== 'route_table' ? null : (() => {
              let routes = rtRoutes(a); let old = false
              if (!routes) {
                old = true; routes = []
                for (const t of [...(routeTargets.get(n.id) || [])].sort()) {
                  const k = n.id + '|' + t
                  for (const d of [...(routeDest.get(k) || [])].sort()) routes.push({ d, t: (byId.get(t) || {}).native_id || String(t).split('/').pop(), st: routeBh.has(k) ? 'blackhole' : '', orig: '' })
                }
              }
              return { main: !!a.main, routes, old, count: a.route_count ?? null, unres: routeUnresOfRt.get(n.id) || [],
                assoc: (assocSubsOf.get(n.id) || []).slice().sort(),
                edge: Array.isArray(a.edge_association_gateway_ids) ? [...a.edge_association_gateway_ids] : null,
                vgw: Array.isArray(a.propagating_vgw_ids) ? [...a.propagating_vgw_ids] : null }
            })()
            // 콘솔 「인바운드 규칙 / 아웃바운드 규칙」 표의 재료(2.13.0 · 사용자 결정 2026-10-24) — 엔진 줄(탭 구분) 그대로 · 못 읽었으면 키가 없다(「0」이 아니다).
            //   셈 = 콘솔 셈법(SG = 소스 하나가 한 줄 · NACL = 기본 `*` 거부 포함) — 엔진이 센 줄 수가 곧 콘솔 「(N)」이다
            const rules = n.type === 'security_group' && (Array.isArray(a.ingress_rules) || Array.isArray(a.egress_rules))
              ? { rin: Array.isArray(a.ingress_rules) ? [...a.ingress_rules] : null, rout: Array.isArray(a.egress_rules) ? [...a.egress_rules] : null, desc: a.description || undefined }
              : n.type === 'network_acl' && (Array.isArray(a.ingress_entries) || Array.isArray(a.egress_entries))
                ? { rin: Array.isArray(a.ingress_entries) ? [...a.ingress_entries] : null, rout: Array.isArray(a.egress_entries) ? [...a.egress_entries] : null, dflt: a.is_default === true }
                : {}
            list.push({ id: n.native_id, type: n.type, name: n.type === 'security_group' ? (a.group_name || nameOf(n)) : nameOf(n),
              vpc: vpc ? nameOf(vpc) : '(VPC 밖)', vpcId: n.parent_id, region: n.region, detail, ...rules,
              ...(n.type === 'network_acl' ? { subs: snap.edges.filter((e) => e.type === 'association' && e.source === n.id).map((e) => e.target).sort() } : {}),
              ...(rt ? { rt, eid: n.id } : {}) })
          } else {
            // ---- 이음매 1: 「스키마가 먼저 늘고 어댑터가 아직 모르는 타입」은 **정상 경로**다 ----
            // (`docs/contract-seams.md` 「이음매 1」) 개발자 B 가 `node_type` 에 타입을 더하고 수집기를
            // 얹으면, 렌더러가 규칙을 배우기 전까지 이 분기로 들어온다. 선택지는 셋이고 둘은 틀렸다:
            //   ① 버린다 → 사용자는 「없다」로 읽는다(불변식 ④ 위반, 최악)
            //   ② 어댑터가 죽는다 → 페이지가 **아예 안 나온다**. 부분 실패가 정상 경로인 툴에서
            //      모르는 타입 하나가 전체를 막는 것은 ① 과 같은 종류의 오답이다(2026-09-20 까지 이것)
            //   ③ 넘기고 신고한다 ← 이것. **티어를 주지 않는다** → flow.js 가 세우지 못하고 §3i 잔차로
            //      판정해 표·배너에 내놓는다(대장 97). 위치를 날조하지 않으면서 존재는 보존된다.
            // 🔴 여기서 티어·부모를 추측해 그리면 안 된다 — 모르는 타입을 아는 척 그리는 것이 «날조»다.
            //    부모는 **해소되면** 붙이고(그 VPC·리전 소속이라는 사실은 엔진이 말했다), 안 되면
            //    엔진 값을 그대로 남긴다(flow.js 가 「부모 — 모델에 없다」로 신고한다).
            unknown.push(n.type)
            put(n, { ...base, fallback: true, parent_id: fallbackParent(n), attrs })
          }
      }
    }

    // 탄력적 IP 주인 정보창 줄(배치 D · §17.34) — 주인 노드에 `eips` 를 싣는다(엔진 엣지 `eip_association` 은 선이 아니다)
    for (const o of out) if (eipsOf.has(o.id)) o.eips = eipsOf.get(o.id)
    // 콘솔식 정보창의 재료(§17.11 「콘솔 표현 원칙」 · 2.13.0 · 사용자 결정 2026-10-24 「콘솔이 보여 주는 값이면 우리도 보여 준다」) —
    //   엔진 속성을 **고치지 않고** `con` 에 옮긴다(표 한 줄 = 탭 구분 문자열 · 칸 순서 정본 = `schema/topology.schema.json` 「콘솔식 정보창의 재료」 `$comment`).
    //   못 읽은 값은 엔진이 키를 만들지 않는다 → 여기도 키가 없다(정보창이 「읽지 못함」과 「(0)」을 가른다 · 불변식 ④)
    const CON_KEYS = {
      load_balancer: ['dns_name', 'availability_zones', 'subnet_ids', 'listener_details', 'target_group_details', 'ip_address_type'],
      vpn_connection: ['tunnel_inside_cidrs', 'tunnel_outside_ips', 'local_ipv4_network_cidr', 'remote_ipv4_network_cidr', 'enable_acceleration', 'outside_ip_address_type',
        'gateway_association_state', 'tunnel_bandwidth', 'type', 'category', 'customer_gateway_id', 'transit_gateway_id', 'vpn_gateway_id', 'static_routes_only'],
      customer_gateway: ['bgp_asn', 'ip_address', 'device_name', 'type', 'uses_certificate'],
      vpn_gateway: ['amazon_side_asn', 'type', 'attached_vpc_ids'],
      direct_connect_connection: ['bandwidth', 'location', 'aws_device', 'aws_logical_device_id', 'jumbo_frame_capable', 'macsec_capable', 'encryption_mode', 'vlan',
        'partner_name', 'provider_name', 'has_logical_redundancy', 'lag_id'],
      direct_connect_gateway: ['amazon_side_asn', 'gateway_associations'],
      direct_connect_virtual_interface: ['virtual_interface_name', 'virtual_interface_type', 'vlan', 'asn', 'amazon_side_asn', 'mtu', 'jumbo_frame_capable', 'connection_id',
        'direct_connect_gateway_id', 'virtual_gateway_id', 'bgp_peer_details', 'route_filter_prefixes', 'address_family', 'site_link_enabled', 'region'],
      cloudfront: ['comment', 'origins', 'behaviors', 'http_version', 'is_ipv6_enabled'],
      waf_web_acl: ['rules'],
      vpc_endpoint: ['service_name', 'security_groups', 'subnet_addresses', 'network_interface_ids', 'ip_address_type'],
      nat_gateway: ['addresses']
    }
    const outOf = new Map(out.map((o) => [o.id, o]))
    for (const n of N) {
      const ks = CON_KEYS[n.type]; if (!ks) continue
      const o = outOf.get(canvasId.get(n.id)); if (!o) continue
      const a = n.attributes || {}; const c = {}
      for (const k of ks) if (k in a && a[k] !== null && a[k] !== undefined) c[k] = Array.isArray(a[k]) ? [...a[k]] : a[k]
      if (n.type === 'direct_connect_virtual_interface' && n.region && !('region' in c)) c.region = n.region
      o.con = c
    }

    // ---------------------------------------------------------------- 엣지 변환
    const E = []
    let eid = 0
    const link = (source, target, kind, attrs, fallback) => {
      if (!canvasNodeIds.has(source) || !canvasNodeIds.has(target)) return false
      // 🔴 `fallback` 이 아닐 때 키를 만들지 않는다 — 불변식 ②(바이트 동일)를 지키려면 정상 경로의
      //    출력이 이 개정 전과 같아야 한다.
      E.push({ id: 'e' + (++eid), source, target, kind, attrs: attrs || {}, ...(fallback ? { fallback: true } : {}) })
      return true
    }
    const canvasNodeIds = new Set(out.map((n) => n.id))
    const skipped = { association: 0, route: 0, security_group_reference: 0, attachment: 0, dangling: 0 }
    const unknownKinds = []

    for (const e of snap.edges) {
      const known = Object.prototype.hasOwnProperty.call(EDGE_KIND, e.type)
      // 🔴 모르는 종류는 **엔진의 말을 그대로** 들고 간다(캔버스 kind 로 번역하지 않는다 — 번역하면
      //    그것이 아는 척이다). 페이지는 `fallback` 을 보고 그리지 않고 잔차로 신고한다.
      const kind = known ? EDGE_KIND[e.type] : e.type
      if (kind === null) { skipped[e.type] = (skipped[e.type] || 0) + 1; continue }
      // IGW→VPC · TGW어태치먼트→VPC 는 부모·부착 관계로 이미 그려졌다 — 선을 또 그리지 않는다.
      //   VGW→VPC 도 같다 — 노드의 `vpcIds` 가 싣고 새 도식이 포트 + 어태치먼트 선으로 그린다(§17.1)
      if (e.type === 'attachment' && typeOfId(e.target) === 'vpc') { skipped.attachment++; continue }
      const src = canvasId.get(e.source) || e.source
      const tgt = canvasId.get(e.target) || e.target
      // 🔴 **관측을 이 선에 붙일 끈(`obs_edges`)을 라우트 재투영뿐 아니라 여기서도 실어 보낸다**
      //    (결함 수정 2026-09-25). 그 전까지 끈은 아래 라우트 루프(`:298`)만 달았고, 엔진이 지표 근거를
      //    붙이는 종류는 `attachment`·`endpoint_connection`·`load_balancer_placement` 3종인데
      //    (`engine/src/nettopo/metrics.py:181-217`) 그 3종은 전부 이 루프를 지나므로 **지표를 가진
      //    관계에는 끈이 없고, 끈이 있는 선(라우트)에는 지표가 없었다** → 「측정으로 초록이 된 선」이
      //    구조적으로 0개였다(실계정 실측 2026-09-24: 흐름선 57개 전부 회색).
      // 🔴 **종류로 걸러 달지 않는다.** 어느 종류가 관측되는지는 엔진의 `EDGE_PROBES` 가 정하고, 그
      //    표를 어댑터가 베끼면 두 곳이 어긋나는 순간 다시 조용히 끊긴다(불변식 ③: 렌더 쪽이 엔진의
      //    판단을 복제하지 않는다). 끈은 **정체성**이다 — 「이 선은 엔진 엣지 e.id 다」. 관측 행이
      //    없으면 `obsEdgeIndex()` 에서 못 찾고 아무 일도 일어나지 않는다.
      //    모르는 종류(`!known`)에는 달지 않는다 — 그 선은 그려지지 않고 잔차로 신고된다.
      // LB → 대상(§17.27) — 선이 묶음·칩·정보창을 짓는 재료(엔진 속성 그대로 · 스칼라 배열뿐이라 계약 모양 안이다)
      const ea = e.attributes || {}
      const lbt = e.type === 'load_balancer_target'
        ? { target_groups: [...(ea.target_groups || [])], ports: [...(ea.ports || [])], target_types: [...(ea.target_types || [])],
            target_count: Number.isInteger(ea.target_count) ? ea.target_count : 0, ...(Array.isArray(ea.route_exits) ? { route_exits: [...ea.route_exits] } : {}) }
        : null
      // 엔드포인트 → 제공자(§17.28) — 서비스 이름 · 어떻게 이었나(엔진 속성 그대로 · 스칼라)
      const epp = e.type === 'endpoint_provider' ? { service_name: ea.service_name, resolved_by: ea.resolved_by } : null
      // Route 53 별칭(§17.30) = 레코드 이름 — 🔴 P2-4 가 이 줄을 빠뜨려 영역 「별칭」 칸·별칭 선 정보창이 이름 대신 「?」였다(검사 150 이 이제 글자를 잰다)
      const dns = e.type === 'dns_alias' ? { record_names: [...(ea.record_names || [])] } : null
      // CloudFront 원본(§17.31) = 어떻게 이었나(`via` = vpc_origin · dns_alias · s3 · 없음 = 주소 꼴) · 별칭 이름
      const org = e.type === 'origin' && ea.via ? { via: ea.via, ...(ea.alias ? { alias: ea.alias } : {}) } : null
      // 배치 D(2.10.0) — VPC 링크(id·이름) · 프록시 대상(종류·포트) · 가속기 엔드포인트(종류·리전·리스너·거친 EIP) · 엔진 속성 그대로(스칼라 배열)
      const bd = e.type === 'vpc_link' ? { vpc_link_ids: [...(ea.vpc_link_ids || [])], vpc_link_names: [...(ea.vpc_link_names || [])] }
        : e.type === 'proxy_target' ? { target_type: ea.target_type, ports: [...(ea.ports || [])] }
          : e.type === 'accelerator_endpoint' ? { endpoint_type: ea.endpoint_type, endpoint_region: ea.endpoint_region, listener_ports: [...(ea.listener_ports || [])], ...(ea.via_eip ? { via_eip: ea.via_eip } : {}) }
            : null
      // #129(2.11.0) — Cloud WAN 연결(id · 세그먼트 · 엣지 위치) · Lattice 대상(대상 그룹 · 포트 · 종류) · 엔진 속성 그대로(스칼라 배열)
      const ov = e.type === 'core_network_attachment' ? { attachment_ids: [...(ea.attachment_ids || [])], segments: [...(ea.segments || [])], edge_locations: [...(ea.edge_locations || [])] }
        : e.type === 'lattice_target' ? { target_groups: [...(ea.target_groups || [])], ports: [...(ea.ports || [])], target_type: ea.target_type }
          : null
      const at = { state: ea.state, ...(known ? { obs_edges: [e.id] } : {}), ...(lbt || {}), ...(epp || {}), ...(dns || {}), ...(org || {}), ...(bd || {}), ...(ov || {}) }
      if (!link(src, tgt, kind, at, !known)) skipped.dangling++
      else if (!known) unknownKinds.push(e.type)
    }

    // 라우트를 **서브넷 → 대상**으로 재투영한다. 라우트 테이블은 캔버스 밖이라 그 선을 그대로 쓰면
    // 출발점이 없는 선이 된다. 실제 트래픽 경로(public 서브넷 → IGW, private → NAT/TGW)는 이렇게만 보인다.
    let flows = 0
    const seen = new Set()
    for (const n of out) {
      if (n.type !== 'subnet') continue
      const rt = rtFor(byId.get(n.id))
      for (const t of targetsFor(rt)) {
        const key = n.id + '|' + t
        if (seen.has(key)) continue
        seen.add(key)
        // 🔴 관측을 이 선에 붙일 끈을 같이 실어 보낸다(`obs_edges` = 엔진 라우트 엣지 id 배열).
        //    여기 담는 곳은 렌더러 입력의 엣지 `attrs` 이고, 계약의 `attributes` 와 **같은 규칙**
        //    (스칼라 또는 스칼라 배열 — `schema/topology.schema.json` `$defs/attributes`)을 지킨다 →
        //    계약 변경이 아니다. 없으면 키를 만들지 않는다 —
        //    빈 배열을 넣으면 「관측이 있는데 비었다」와 「끈이 없다」가 구별되지 않는다.
        const oe = routeEdgeIds.get(rt + '|' + t) || []
        // `default_route` = 이 서브넷의 기본 라우트가 이 대상으로 간다(§17.24 — 새 화면이 인터넷 칸을 그릴지 정한다).
        //   없으면 키를 만들지 않는다(옛 화면 입력은 그대로).
        const at = oe.length ? { via: '라우트', obs_edges: oe } : { via: '라우트' }
        if (routeDefault.has(rt + '|' + t)) at.default_route = true
        const dst = routeDest.get(rt + '|' + t)
        if (dst && dst.size) at.destinations = [...dst].sort()
        if (routeBh.has(rt + '|' + t)) at.blackhole = true
        if (link(n.id, canvasId.get(t) || t, 'flow', at)) flows++
      }
    }

    // ---------------------------------------------------------------- 부분 실패
    // 🔴 아래 접기는 `coverage` 를 **대체하지 않는다.** 두 소비자가 서로 다른 것을 필요로 한다:
    //    ① `partial` = 리전 마커(§13-54) — 「이번엔 확인하지 못했다」를 throttle·오류까지 포함해 말한다.
    //    ② `coverage` **원문** = §3h 권한 축 — 해치의 단위가 **(계정, 리전)** 이므로 `account_id` 가
    //       살아 있어야 한다(`flow.js:535 denyOf`). 여기서 접으면서 `account_id` 를 버리면 실계정
    //       스냅샷의 해치가 **0개**가 되고, 화면은 못 읽은 계정을 「없다」로 그린다(불변식 ④).
    //    그래서 아래 접기는 그대로 두고 `mk` 가 원문을 **그대로 통과**시킨다(계약 필드이므로 스키마
    //    변경이 아니다 — `schema/topology.schema.json:9` 가 `coverage` 를 required 로 이미 요구한다).
    const partial = []
    for (const c of snap.coverage) {
      if (c.status === 'ok') continue
      // §3k K5: 종류·계정·리전을 따로 넘긴다 — 사유 원문 안에만 두면 한 곳의 여러 종류가 화면에서 **같은 줄**이
      // 된다(실측 2026-09-27: 8종 → 똑같은 8줄). 이 대상의 리전 노드가 없어도(아무것도 못 읽은 계정) 곳을 말한다.
      partial.push({ target: rid(c.account_id, c.region), scope: 'region', code: (c.reason || {}).code || c.status,
        reason: `${c.resource_type}: ${(c.reason || {}).message || c.status}`,
        rtype: c.resource_type, acct: c.account_id, region: c.region })
    }
    for (const n of N) {
      if (!n.partial) continue
      for (const r of n.partial_reasons || []) {
        partial.push({ target: canvasId.get(n.id) || n.id, scope: 'vpc', code: r.code, reason: r.message || r.api })
      }
    }

    // ---------------------------------------------------------------- 자기 검사
    // 「조용히 사라지는 것」이 이 어댑터의 유일한 치명적 실패 양식이다. 그래서 여기서 센다.
    const TIERS = ['edge', 'public', 'lb', 'compute', 'private', 'service', 'attach']
    const REGION_TIERS = ['external', 'spine', 'stub']
    const bad = []
    // 🔴 **캔버스 id 중복**은 「조용히 사라지는 것」의 가장 안 보이는 형태다 — 상자 수는 맞고 콘솔도
    //    깨끗한데 `byId` 가 뒤 노드로 앞 노드를 덮어 **부모가 남의 계정으로 바뀐다**(17차 (g)3 이
    //    발견한 `'r-' + region`: 같은 리전을 쓰는 계정이 둘이면 두 번째 계정의 VPC 전부가 첫 계정
    //    레인 아래로 들어간다). 위 절들은 부모가 **풀리는지**만 재므로 이것을 못 잡는다.
    {
      const seen = new Map()
      for (const n of out) {
        if (seen.has(n.id)) bad.push(`${n.id}: 캔버스 id 중복 (${seen.get(n.id)} ↔ ${n.type})`)
        else seen.set(n.id, n.type)
      }
    }
    for (const n of out) {
      const parent = n.parent_id === null ? null : out.find((x) => x.id === n.parent_id)
      // 🔴 미지 타입(`fallback`)은 이 절들의 **대상이 아니다** — 티어가 없는 것이 의도이고, 부모가
      //    안 풀리는 것도 신고 대상이다(flow.js 잔차 행이 그 사실을 적는다). 여기서 죽이면 위의 ② 로
      //    되돌아간다. 대신 **잔차가 되는 조건**을 거꾸로 못 박는다: 티어를 들면 그려지므로 금지다.
      if (n.fallback) {
        if (n.tier) bad.push(`${n.id}: 미지 타입인데 tier=${n.tier}(잔차가 아니라 날조가 된다)`)
        if (!n.name) bad.push(`${n.id}: 이름 없음`)
        continue
      }
      if (n.parent_id !== null && !parent) bad.push(`${n.id}: 부모 없음(${n.parent_id})`)
      // 계정만 예외 — 이름 없음(`null`)이 정상 값이다(§17.5 계정 이름: 화면이 마스킹 ID 로 부른다 · 2026-10-28)
      if (!n.name && n.type !== 'account') bad.push(`${n.id}: 이름 없음`)
      if (parent && parent.type === 'vpc' && !TIERS.includes(n.tier)) bad.push(`${n.id}: VPC 자식인데 tier=${n.tier}`)
      if (parent && parent.type === 'region' && n.type !== 'vpc' && !REGION_TIERS.includes(n.tier)) {
        bad.push(`${n.id}: 리전 자식인데 tier=${n.tier}`)
      }
      if (['public', 'private'].includes(n.tier) && !n.az) bad.push(`${n.id}: ${n.tier} 인데 az 없음`)
      if (n.attachedTo) {
        const host = out.find((x) => x.id === n.attachedTo)
        if (!host || host.parent_id !== n.parent_id) bad.push(`${n.id}: attachedTo 가 같은 VPC 밖`)
      }
    }
    // 🔴 엣지 쪽 대칭(2026-09-20). 모르는 «종류»에 캔버스 kind 를 주면 페이지가 그것을 그리므로
    //    금지다 — 노드의 「미지 타입인데 tier」와 같은 조항이다. 목록은 이 파일의 어휘에서 **뽑는다**
    //    (손으로 적으면 EDGE_KIND 에 종류가 늘 때 조용히 어긋난다).
    const CANVAS_KINDS = [...new Set(Object.values(EDGE_KIND).filter(Boolean)), 'flow']
    for (const e of E) {
      if (!e.fallback) continue
      if (CANVAS_KINDS.includes(e.kind)) {
        bad.push(`${e.id}: 미지 종류인데 캔버스 kind=${e.kind}(잔차가 아니라 날조가 된다)`)
      }
    }
    const listSum = out.filter((n) => n.type === 'vpc')
      .reduce((s, v) => s + v.attrs['security group'] + v.attrs['network ACL'] + v.attrs['route table'], 0)
    if (listSum !== list.length) bad.push(`listOnly ${list.length} ≠ VPC 계수 합 ${listSum}`)
    // 🔴 「어디에도 담기지 않은 노드」 절은 지웠다 — 담지 않는 경로가 없어졌기 때문이다(미지 타입은
    //    잔차로 넘어간다). 보존의 오라클은 아래 한 줄로 충분하고, 그것이 **진짜** 손실을 잡는다.
    if (out.length + list.length !== N.length) {
      bad.push(`노드 보존 실패: 캔버스 ${out.length} + 표 ${list.length} ≠ 엔진 ${N.length}`)
    }

    const lbtVersion = (v) => { const m = /^(\d+)\.(\d+)/.exec(String(v || '')); return !!m && (+m[1] > 2 || (+m[1] === 2 && +m[2] >= 7)) }
    const mk = (s, nodes, edges) => ({
      run: { account: (nodes.find((n) => n.type === 'account') || {}).attrs.account,
        collected_at: s.run.collected_at, scenario: 'live-' + s.scope.account_ids.length + 'acct',
        // 짝의 1순위(`flow.js` `pairOf`). 옛 구조 문서에는 없다 → 키째 뺀다(화면이 시각으로 되돌아간다).
        ...(s.run.content_digest ? { content_digest: s.run.content_digest } : {}),
        // 이 스냅샷이 LB 대상 정체를 싣는 판인가(스키마 2.7.0 ↑ · §17.27) — 정보창 「LB 대상 그룹」 칸이 「(없음)」과 「수집 안 함」을 가른다.
        //   옛 문서에는 키를 만들지 않는다(그 화면들의 바이트가 그대로다)
        ...(lbtVersion(s.schema_version) ? { lbt: true } : {}) },
      nodes, edges, partial, listOnly: list,
      // 🔴 원문 통과(위 「부분 실패」의 🔴). 마스킹하지 않는다 — 계정ID 마스킹은 **화면의 일**이고
      //    (`flow.js` `acctMask`), 어댑터가 접으면 (계정, 리전) 키가 깨져 해치를 칠할 수 없다.
      //    산출물(`out/fixture-real.js`)은 커밋 대상이 아니다(`.gitignore:24` · 실계정 산출물은 민감 자료).
      coverage: Array.isArray(s.coverage) ? s.coverage : []
    })
    const topo = mk(snap, out, E)

    // ---------------------------------------------------------------- 관측 문서(ADR 0006)
    // 🔴 **문서를 그대로 굽는다 — 고치지 않는다.** `window.OBS` 는 계약(`schema/observation.schema.json`)
    //    그 자체이고, 화면이 나중에 `fetch` 로 같은 모양을 받아 **같은 코드로** 소비해야 한다(1-E).
    //    어댑터가 필드를 접거나 이름을 바꾸면 구운 HTML 과 폴링 경로가 서로 다른 것을 먹게 된다.
    // 🔴 짝이 아니면 굽지 않는다. 관측은 「그 구조를 기준으로」 판정된 것이므로, 다른 시각의 구조에
    //    얹으면 화면은 **없는 자원에 상태를 붙이고 있는 자원의 상태를 빼먹는다** — 그리고 조용하다.
    let obsStat = null
    let obs = null
    if (doc) {
      const tca = (doc.run || {}).topology_collected_at || null
      // 짝 규칙은 화면(`flow.js` `pairOf`)과 같다 — 지문이 양쪽에 다 있으면 지문, 아니면 시각.
      //   🔴 여기만 시각을 고집하면 화면은 받아 줄 짝을 어댑터가 버린다(구운 파일에만 상태가 없다).
      const od = (doc.run || {}).topology_content_digest || null
      const td = snap.run.content_digest || null
      if (od && td ? od !== td : tca !== snap.run.collected_at) {
        bad.push(od && td
          ? `관측 지문 ${od.slice(0, 19)}… ≠ 구조 지문 ${td.slice(0, 19)}… — 구성이 바뀐 뒤의 관측이다(굽지 않았다)`
          : `관측 topology_collected_at=${tca} ≠ 구조 collected_at=${snap.run.collected_at}` +
            ' — 짝이 아닌 문서다(굽지 않았다)')
      } else {
        obs = doc
        // 붙는 자리를 센다. 🔴 「관측 N건」은 아무것도 말하지 않는다 — **화면의 어느 id 에 앉나**가
        //    질문이다. 표로 뺀 자원(SG·NACL·RT)과 아예 매치 안 되는 id 를 갈라 둬야 나중에 「상태가
        //    안 보인다」를 보고받을 때 원인이 어댑터인지 화면인지 가른다.
        // 🔴 `list` 행의 `id` 는 `native_id` 다(`:225`) — 엔진 노드 id 와 다른 공간이므로 그걸로
        //    맞추면 「표에도 없다」가 되어 miss 가 부풀려진다. 엔진 쪽에서 종류로 갈라 센다.
        const listIds = new Set(N.filter((n) => LIST_TYPES[n.type]).map((n) => n.id))
        const cardIds = new Set(out.map((n) => n.id))
        // 🔴 **엣지 관측이 앉는 자리는 캔버스 엣지의 `id` 가 아니라 `attrs.obs_edges` 다**(고침
        //    2026-09-26). 캔버스 엣지 id 는 `link()` 가 만드는 다른 id 공간이므로 엔진 엣지 id 와
        //    절대 같아지지 않는다 → 옛 식은 **실계정에서 「선에 앉음 0 · 못 앉은 선 251」** 이라고
        //    보고했고, 실제로는 17개 선이 `live` 행을 받고 있었다(같은 스냅샷 실측). 감사가 제품보다
        //    비관적으로 틀리면 사람은 **있는 기능을 없다고 읽고** 고칠 것을 찾아 헤맨다 — 자기보고는
        //    제품이 쓰는 것과 **같은 경로**로 세야 한다(§4 「실패할 수 없는 어서션」의 거울상).
        const oeIds = new Set()
        for (const e of E) {
          const oe = e.attrs && e.attrs.obs_edges
          if (oe) for (const id of [].concat(oe)) oeIds.add(id)
        }
        obsStat = { card: 0, listOnly: 0, edge: 0, edgeLive: 0, edgeMiss: 0, miss: 0, byStatus: {} }
        for (const o of doc.observations || []) {
          obsStat.byStatus[o.status] = (obsStat.byStatus[o.status] || 0) + 1
          const id = canvasId.get(o.target_id) || o.target_id
          if (o.target_kind === 'edge') {
            // 🔴 `live` 를 따로 센다 — 초록이 될 수 있는 선의 상한이고, 이 수가 0 이면 화면의
            //    「초록 0」은 **제품이 거절한 것이 아니라 근거가 없어서**다(두 사실은 다른 일을 요구한다).
            if (oeIds.has(o.target_id)) { obsStat.edge++; if (o.status === 'live') obsStat.edgeLive++ } else obsStat.edgeMiss++
          } else if (cardIds.has(id)) obsStat.card++
          else if (listIds.has(o.target_id)) obsStat.listOnly++
          else obsStat.miss++
        }
      }
    }

    const tiers = {}
    for (const n of out) tiers[n.tier || '(컨테이너)'] = (tiers[n.tier || '(컨테이너)'] || 0) + 1
    return { topo, obs, obsStat, bad,
      stats: { nodes: out.length, edges: E.length, flows, list: list.length, partial: partial.length,
        skipped, tiers, unknown, unknownKinds } }
  }

  adapt.canvasIdOf = canvasIdOf

  // 「수집 시각 기준」 터널 상태(사용자 결정 2026-10-24 — 콘솔의 터널 세부 정보 · 모니터링을 꺼도 Down 이 보인다 · ADR 0006 O13).
  //   구조 빌드가 `collect` 직후 1층 `observe` 를 한 번 돌린 문서에서 **VPN 터널 줄만** 뽑는다 — 관측 문서 전체를 굽지 않는다
  //   (전체를 구우면 모니터링을 끈 화면에 상태색이 서서 §17.11 「꺼짐 = 상태 칸 없음」이 깨진다 · 그 판단은 그대로다).
  //   🔴 못 얻으면 `ok:false` + 사유 — 정보창은 「확인하지 못했습니다」를 쓴다(빈 표로 두면 「터널 없음」으로 읽힌다 · 불변식 ④).
  //   🔴 짝 규칙은 `window.OBS` 와 같다(지문 → 시각) — 다른 시각의 구조에 얹으면 없는 연결에 상태가 붙는다.
  //   결정론: 키·줄 순서를 정렬한다(같은 두 문서 → 같은 바이트).
  adapt.snapObs = function snapObs (snap, doc, why) {
    if (!doc) return { ok: false, why: why || 'observe 산출물이 없습니다' }
    const run = doc.run || {}
    const od = run.topology_content_digest || null
    const td = (snap.run || {}).content_digest || null
    if (od && td ? od !== td : run.topology_collected_at !== (snap.run || {}).collected_at) return { ok: false, why: '구조와 짝이 아닌 관측입니다' }
    const byId = new Map((snap.nodes || []).map((n) => [n.id, n]))
    const tunnels = {}
    for (const o of doc.observations || []) {
      if (o.target_kind === 'edge' || !Array.isArray(o.tunnels)) continue
      const n = byId.get(o.target_id)
      if (!n || n.type !== 'vpn_connection') continue
      tunnels[canvasIdOf(n)] = o.tunnels.map((t) => ({ outside_ip: t.outside_ip ?? null, status: t.status ?? null, last_status_change: t.last_status_change ?? null, status_message: t.status_message ?? null }))
        .sort((a, b) => String(a.outside_ip).localeCompare(String(b.outside_ip)))
    }
    const keys = Object.keys(tunnels).sort()
    return { ok: true, at: run.observed_at || null, tunnels: Object.fromEntries(keys.map((k) => [k, tunnels[k]])) }
  }
  globalThis.NT_ADAPT = adapt
})()
