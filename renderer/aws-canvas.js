// AWS 도식 캔버스(§17, `docs/canvas-principles.md`) — 계정 > 리전 > VPC > AZ > 서브넷 > 아이콘으로 중첩한
// AWS 아키텍처 모양. 화면의 유일한 그림이다 — flow.js 가 `window.NT_AWS` 로 부른다.
// 🔴 이 파일은 **배치·그림·정보창**만 한다. 팬·줌·필터·범위·검색·잔차 표는 flow.js 의 것을 그대로 쓴다 —
//    숨김은 flow.js 의 한 문(`goneByHand`)만 지난다. 여기서 따로 거르면 목록·배너와 그림이 서로 다른 것을 센다.
// 🔴 첫 화면에는 상자·아이콘·선·이름표만(§17.2). CIDR·ID 는 누르면 나오는 정보창에만 있다.
// 🔴 색 = 서비스(§17.7). 계정 틀은 중립 — 두 계정의 같은 서비스는 같은 색이다.
// 🔴 그릴 규칙이 없는 종류는 **날조하지 않는다** — `awsWhy` 사유와 함께 flow.js 의 잔차 표(§3i)로 간다.
;(function () {
  'use strict'
  const ICON = globalThis.NT_AWS_ICONS || {}
  const BG = '#0a0d12'
  // 색 = 서비스 기준. 상태색(초록 #3fb950 · 빨강 #f85149)은 피한다 — 감시 색과 겹치지 않게(§17.7)
  // 서브넷은 가라앉힌 청회색 — 대비가 VPC 테두리·어느 서비스 아이콘보다 낮다(§17.9, 검사 127)
  const COL = { vpc: '#8C4FFF', pub: '#5d7189', priv: '#4b5d74', ec2: '#FF9900', ecs: '#FFD43B', eks: '#B8E04A', sm: '#26C6A6',
    vpce: '#3FD8E8', nat: '#4D8DFF', lb: '#D9B45A', rds: '#E64FD8', tgw: '#FF5C9D', igw: '#A98BFF', eic: '#F5A97F',
    dxcon: '#9FB4CC', dxgw: '#9FB4CC', cf: '#FFAAEE', asg: '#8A8AFF', nfw: '#D277FF', vgw: '#5FC8FF', vpn: '#5FC8FF', cgw: '#5FC8FF', ghost: '#8b949e', xvpc: '#8b949e', inet: '#D0D7DE',
    lambda: '#FF7F50',    // Lambda(2.7.0 · §17.27 · 🔶 §17.18-12 — 비어 있던 색상환 자리)
    r53: '#6BB5FF',       // Route 53(2.8.0 · §17.30 · 🔶 §17.18-12 — 비어 있던 하늘색 자리 · VGW 계열과 가르는 밝은 쪽)
    s3: '#C2A383',        // S3 버킷(2.8.0 · §17.31 · 🔶 §17.18-12 — 덱의 저장소 초록 = 상태색 초록과 겹친다 → 채도 낮은 황갈색)
    ec: '#7DDC6B',        // #114(2.9.0 · §17.32 · 🔶 §17.18-13) — ElastiCache 잎 초록(EKS 라임과 상태 초록 사이의 빈 자리)
    rdsc: '#E64FD8',      // Aurora · RDS DB 클러스터 = RDS 와 같은 서비스 = 같은 색(§17.7)
    os: '#C9B79C',        // #114 OpenSearch — 채도를 낮춘 모래색(DX 의 낮은 채도 파랑과 같은 처분 · 🔶 §17.18-13)
    msk: '#D07BB0',       // #114 MSK — 채도를 낮춘 난초색(CloudFront 연분홍·TGW 진분홍과 채도로 갈린다 · 🔶 §17.18-13)
    brk: '#3FD8E8',       // #114 Bedrock 엔드포인트 = VPC 엔드포인트다(PrivateLink) → VPC 엔드포인트 색 · 모양에 별 표시만 더한다(§17.32)
    apigw: '#A090D0',     // 배치 D(2.10.0 · §17.35 · 🔶 §17.18-14) — API Gateway 흐린 라벤더(≈255° · 팔레트·상태색과 ΔE ≥ 26 · 배경 대비 ≥ 6:1)
    rdsp: '#E64FD8',      // RDS 프록시 = RDS 와 같은 서비스 = 같은 색(§17.7 · §17.36) · 모양으로 가른다
    ga: '#D0E090',        // 배치 D(§17.37 · 🔶 §17.18-14) — Global Accelerator 흐린 올리브(≈68° · 상태 초록·EKS 라임과 ΔE ≥ 26)
    cwan: '#B8FFE2',      // #129(2.11.0 · §17.39 · 🔶 §17.18-15) — Cloud WAN 옅은 민트(≈155° · 팔레트·상태색과 ΔE ≥ 29 · 배경 대비 ≥ 6:1 · 상태 초록과 채도로 갈린다)
    lsn: '#E07B80',       // #129(§17.40 · 🔶 §17.18-15) — VPC Lattice 흐린 장미(≈357° · ΔE ≥ 27) · 서비스 네트워크·서비스 = 같은 서비스 = 같은 색(모양으로 가른다)
    lsvc: '#E07B80',
    waf: '#889828' }      // 2.12.0(§17.41 · ✅ §17.18-16) — WAF 짙은 올리브(≈68° · 팔레트·상태색과 ΔE ≥ 28 · 배경 대비 ≥ 6:1 · 가속기 연올리브와 명도로 갈린다)
  const SNF = { pub: 'rgba(93,113,137,.10)', priv: 'rgba(75,93,116,.08)' }
  const SVC = { vpc: 'VPC', subnet: 'Subnet', igw: 'Internet Gateway', nat: 'NAT Gateway', tgw: 'Transit Gateway', vpce: 'VPC Endpoint',
    ec2: 'EC2', rds: 'RDS', ecs: 'ECS', eks: 'EKS', sm: 'SageMaker', eic: 'EIC Endpoint', dxcon: 'Direct Connect', dxgw: 'DX Gateway', cf: 'CloudFront', asg: 'Auto Scaling Group', nfw: 'Network Firewall', vgw: 'Virtual Private GW', vpn: 'VPN Connection', cgw: 'Customer Gateway', xvpc: 'VPC', inet: '인터넷', lambda: 'Lambda', ec: 'ElastiCache', rdsc: 'DB Cluster', os: 'OpenSearch', msk: 'MSK', r53: 'Route 53', s3: 'S3', brk: 'Bedrock Endpoint', apigw: 'API Gateway', rdsp: 'RDS Proxy', ga: 'Global Accelerator', cwan: 'Cloud WAN', lsn: 'VPC Lattice', lsvc: 'Lattice Service', waf: 'WAF' }
  const TYPE_KO = { igw: '인터넷 게이트웨이', nat: 'NAT 게이트웨이', vpce: 'VPC 엔드포인트', ec2: 'EC2 인스턴스', eks: 'EKS 클러스터',
    tgw: 'Transit Gateway', dxcon: 'Direct Connect 연결', dxgw: 'Direct Connect 게이트웨이', lb: '로드밸런서', ecs: 'ECS 서비스', rds: 'RDS DB 인스턴스',
    sm: 'SageMaker', eic: 'EC2 Instance Connect 엔드포인트', cf: 'CloudFront 배포', asg: 'Auto Scaling 그룹', nfw: 'Network Firewall', vgw: '가상 프라이빗 게이트웨이(VGW)', vpn: 'Site-to-Site VPN 연결', cgw: '고객 게이트웨이(CGW)', xvpc: 'VPC · 수집 범위 밖', inet: '인터넷 · 이 도구가 그린 칸(수집한 리소스가 아닙니다)', lambda: 'Lambda 함수', r53: 'Route 53 호스팅 영역', s3: 'S3 버킷 · CloudFront 오리진', ec: 'ElastiCache 캐시 클러스터', rdsc: 'RDS DB 클러스터(Aurora · 다중 AZ DB 클러스터)', os: 'OpenSearch Service 도메인', msk: 'Amazon MSK(Kafka) 클러스터', brk: 'Bedrock 엔드포인트 · VPC 인터페이스 엔드포인트(PrivateLink)', apigw: 'API Gateway API', rdsp: 'RDS 프록시', ga: 'Global Accelerator', cwan: 'Cloud WAN 코어 네트워크', lsn: 'VPC Lattice 서비스 네트워크', lsvc: 'VPC Lattice 서비스', waf: 'WAF 웹 ACL' }
  // 모델 타입 → 그림 종류. SageMaker 세 종류는 한 그림(같은 색·아이콘)이고 종류 글자만 다르다(SM_KO).
  //   TGW 라우트 테이블은 여기 없다 — 아이콘이 아니라 TGW 정보창의 칸이다(아래 model 의 마지막 루프).
  const KIND = { internet_gateway: 'igw', nat_gateway: 'nat', load_balancer: 'lb', vpc_endpoint: 'vpce', ec2_instance: 'ec2',
    eks_cluster: 'eks', transit_gateway: 'tgw', direct_connect_connection: 'dxcon', dx_connection: 'dxcon', direct_connect_gateway: 'dxgw',
    cloudfront: 'cf', route53_hosted_zone: 'r53', s3_bucket: 's3', auto_scaling_group: 'asg', network_firewall: 'nfw', rds_db_instance: 'rds', ecs_service: 'ecs',
    sagemaker_notebook_instance: 'sm', sagemaker_domain: 'sm', sagemaker_endpoint: 'sm', ec2_instance_connect_endpoint: 'eic',
    vpn_gateway: 'vgw', customer_gateway: 'cgw', vpn_connection: 'vpn', lambda_function: 'lambda',
    elasticache_cluster: 'ec', rds_db_cluster: 'rdsc', opensearch_domain: 'os', msk_cluster: 'msk',   // #114(2.9.0 · §17.32)
    api_gateway: 'apigw', rds_db_proxy: 'rdsp', global_accelerator: 'ga',
    core_network: 'cwan', lattice_service_network: 'lsn', lattice_service: 'lsvc', waf_web_acl: 'waf' }   // #129(2.11.0 · §17.39~40) · TGW Connect 는 아이콘이 아니다(전송 어태치먼트 선 위의 오버레이 · §17.38)   // 배치 D(2.10.0 · §17.35~37) · 탄력적 IP 는 아이콘이 아니다(주인 정보창 줄 · §17.34)
  const SM_KO = { sagemaker_notebook_instance: 'SageMaker 노트북 인스턴스', sagemaker_domain: 'SageMaker Studio 도메인', sagemaker_endpoint: 'SageMaker 추론 엔드포인트' }
  // 구성을 읽었는데 서브넷이 비었다(어댑터 `noSubnets`) — 종류마다 그 뜻이 다르다. 「걸친 서브넷이 이 화면에 없다」와 섞지 않는다.
  //   여기 있는 종류는 서브넷에 ENI 를 두지 않는 리전 단위 자원이다 → 리전 줄에 선다(§17.12 ①) · 정보창 「서브넷」 칸이 이 글로 이유를 말한다
  const NOSUB_WHY = { ecs_service: '이 서비스는 서브넷을 지정하지 않습니다(awsvpc 네트워크 모드가 아닙니다 — 태스크는 컨테이너 인스턴스(EC2)에서 실행되고, 그 EC2 는 자기 서브넷에 표시됩니다)',
    sagemaker_endpoint: 'VPC 설정이 없습니다 — 고객 VPC 밖(SageMaker 관리 네트워크)에서 실행됩니다',
    opensearch_domain: 'VPC 설정이 없습니다 — 퍼블릭 엔드포인트 도메인입니다(고객 VPC 밖에 있어 이 VPC 에 네트워크 인터페이스가 없습니다)' }
  // Lambda(2.7.0 · §17.27) = 리전 자원 줄 — VPC 에 붙었는지 모른다(`lambda:GetFunctionConfiguration` 을 더하지 않았다)
  // API Gateway(2.10.0 · §17.35) = 리전 자원 줄 — API 는 VPC 에 살지 않는다(VPC 링크가 LB 로 들어간다)
  // VPC Lattice(2.11.0 · §17.40) = 리전 자원 줄 — 서비스 네트워크는 붙은 VPC 위 변 포트 가운데(TGW 와 같은 자리) · 서비스는 줄의 맨 끝
  // WAF 웹 ACL(2.12.0 · §17.41) = 리전 범위는 리전 자원 줄 · CloudFront 범위(리전 없음)는 전역 줄(아래 `model` 의 전역 갈래)
  const HUB = new Set(['tgw', 'dxcon', 'vgw', 'vpn', 'cgw', 'lambda', 'apigw', 'lsn', 'lsvc'])
  const WL = ['ec2', 'asg', 'ecs', 'eks', 'sm', 'eic', 'rds', 'nfw', 'ec', 'rdsc', 'os', 'msk', 'lambda', 'rdsp']
  const ORDER = { lb: 0, nfw: 1, ec2: 2, asg: 3, ecs: 4, eks: 5, sm: 6, eic: 7, rdsp: 8, rds: 8, ec: 9, rdsc: 10, os: 11, msk: 12, lambda: 9 }
  const IC = 36, CW = 98, CH = 66, SUB_HDR = 26, AZ_HDR = 22, VPC_HDR = 32, REG_HDR = 32
  const HW = 150, HH = 44, XP_GAP = 44, PEER_LANE = 6
  // 인터넷 칸(§17.24) — VPC 왼쪽 바깥. 중심 = 틀 왼쪽 변(IGW 중심)에서 84 · 이름표 반폭 ≈23 이 IGW 이름표(±48)와 띄도록.
  //   칸이 있는 VPC 는 배치할 때 왼쪽에 INET_PAD 를 비운다(칸 쪽 이름표·누르는 자리 끝 = -114 + 여백)
  const INET_DX = 84, INET_HW = 30, INET_PAD = 120
  const SP = 8, SGAP = 14, AGG_T = 4, ROWN = 6
  const MAXW = 2100, X0 = 60, GXV = 64, AG = 48, GAPX = 96, TOP = 38, OX = 20, OY = 20
  // 계정 줄바꿈(§17.15): 기준 화면(1600×1000)에서 「화면 맞춤」 배율(100% 상한)이 가장 커지게 한 줄의 계정 수를 고른다.
  // 한 줄보다 WRAP_GAIN 배 이상 커질 때만 내린다 — 한 줄로도 100% 로 보이는 몇 개짜리는 지금처럼 한 줄이다
  const REF_W = 1600, REF_H = 1000, WRAP_GAIN = 1.5
  const NC = '이 도구는 수집하지 않습니다'
  // 규칙 공백(§3i) — 표 사유 열의 어휘는 flow.js `WHY_NONE` 과 **같은 말로 시작한다**. 「그리지 않는 종류」(§7)와 갈라야 개발이 할 일이 보인다.
  //   옛 화면을 걷어낸 뒤라 「새 도식에 아직」은 대비할 화면이 없는 말이다(W5 2026-10-03)
  const WHY_NEW = '아직 그리지 못하는 종류'
  const REGION_KO = { 'us-east-1': '버지니아 북부', 'us-east-2': '오하이오', 'us-west-1': '캘리포니아 북부', 'us-west-2': '오리건',
    'ca-central-1': '캐나다 중부', 'sa-east-1': '상파울루', 'eu-central-1': '프랑크푸르트', 'eu-west-1': '아일랜드', 'eu-west-2': '런던',
    'eu-west-3': '파리', 'eu-north-1': '스톡홀름', 'eu-south-1': '밀라노', 'ap-northeast-1': '도쿄', 'ap-northeast-2': '서울',
    'ap-northeast-3': '오사카', 'ap-southeast-1': '싱가포르', 'ap-southeast-2': '시드니', 'ap-south-1': '뭄바이', 'ap-east-1': '홍콩',
    'me-south-1': '바레인', 'af-south-1': '케이프타운' }
  // 정보창에 넣지 않는 값(§17.11) — BGP 인증 키 · 고객 라우터 설정 · EKS CA · SG 짝 계정 · 흐름 로그 목적지
  const SECRET = /auth.?key|router.?config|certificate|ca.?data|userid|flow.?log|bucket|arn/i

  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))
  const tw = (s, f = 10.5) => [...String(s)].reduce((a, ch) => a + (ch.charCodeAt(0) > 0x2e80 ? f : f * 0.6), 0)
  const cut = (s, W, f) => { s = String(s ?? ''); if (tw(s, f) <= W) return s; let o = ''; for (const ch of s) { if (tw(o + ch + '…', f) > W) break; o += ch } return o + '…' }
  const paren = (s, W, f = 10.5) => `(${cut(s, W - tw('()', f), f)})`
  // 틀 이름표 줄임(§17.5 「칸에 안 들어가는 이름」 · QA B P5 2026-10-15) — 끝을 자르면 같은 머리를 쓰는 형제
  //   (`qa-single-app-private-a`·`qa-single-egress-tgw-c` …)가 전부 `qa-single-…` 로 같아진다(QA B B-06 · 서브넷 17개).
  //   ① 형제 공통 머리(낱말 경계까지) 안에서 앞 낱말부터 하나씩 `…` 로 줄인다 ② 다 줄여도 안 들어가면 나머지의
  //   가운데를 줄이고 끝 낱말을 남긴다 ③ 형제끼리 같아지면 그 이름들만 꼬리를 한 글자씩 늘린다. 폭 예산은 그대로다
  const NSEP = /[-_./: ]/
  const sharedHead = (names) => {
    const L = names.map((s) => [...String(s)]); if (L.length < 2) return 0
    let n = Math.min(...L.map((a) => a.length))
    for (const a of L) { let i = 0; while (i < n && a[i] === L[0][i]) i++; n = i }
    while (n > 0 && !NSEP.test(L[0][n - 1])) n--
    return n
  }
  const fitName = (s, W, f, pre, more = 0) => {
    s = String(s ?? ''); if (tw(s, f) <= W) return s
    const ch = [...s]; const fits = (x) => tw(x, f) <= W
    const rest = pre > 0 && pre < ch.length ? ch.slice(pre) : null
    if (rest) for (let b = 1; b <= pre; b++) { if (!NSEP.test(ch[b - 1])) continue; const c = '…' + ch.slice(b).join(''); if (fits(c)) return c }
    const body = rest || ch; const lead = rest ? '…' : ''
    let j = body.length - 1; while (j > 0 && !NSEP.test(body[j])) j--
    const t = Math.min(body.length - 1, (j > 0 && body.length - j <= 4 ? body.length - j : 3) + more)
    for (let k = body.length - t - 1; k >= 1; k--) { const c = lead + body.slice(0, k).join('') + '…' + body.slice(body.length - t).join(''); if (fits(c)) return c }
    return cut(lead + body.join(''), W, f)
  }
  // 형제 묶음 → key 별 괄호 속 글자. it = { key, name, W(괄호 속 예산) }
  const fitNames = (its, f) => {
    const pre = sharedHead(its.map((x) => x.name)); const more = new Map(its.map((x) => [x.key, 0])); const out = new Map()
    for (let round = 0; round < 24; round++) {
      for (const x of its) out.set(x.key, fitName(x.name, x.W, f, pre, more.get(x.key)))
      const by = new Map(); for (const x of its) { const v = out.get(x.key); if (!by.has(v)) by.set(v, []); by.get(v).push(x) }
      const dup = [...by.values()].filter((g) => new Set(g.map((x) => x.name)).size > 1).flat()
      if (!dup.length) break
      for (const x of dup) more.set(x.key, more.get(x.key) + 1)
    }
    return out
  }
  // 아이콘 이름표(둘째 줄 `(고객 이름)`)도 같은 규칙(§17.5 · QA B 2차 N-02 2026-10-17) — 끝을 자르면 VPN 둘이 다 `VPN (qa-single-vpn…)`,
  //   NAT 둘이 다 `NAT Gateway (qa-single-e…)` 였다. 형제 = 같은 상자(VPC 상자 안 = 서브넷 칸·가장자리 · 리전 줄 · 전역 줄)의
  //   **같은 서비스**(첫 줄이 같은 것 — 첫 줄이 다르면 이미 갈린다). 다른 AZ 서브넷의 NAT 둘도 같은 VPC 라 형제다
  const fitIcons = (cells) => {
    const by = new Map(); for (const c of cells) if (c && c.nm != null) push(by, c.l1, c)
    for (const g of by.values()) { const o = fitNames(g.map((c) => ({ key: c.key, name: c.nm, W: c.nmW })), 10.5); for (const c of g) c.l2 = `(${o.get(c.key)})` }
  }
  const P2 = (n) => Math.round(n * 10) / 10
  const natId = (n) => (n && n.attrs && n.attrs.id) || String(n && n.id).split('/').pop()
  // 「-」·「–」·공백만 적힌 이름은 이름 없음이다(§17.5) — 콘솔의 빈칸 표시와 헷갈린다
  const BLANK_NAME = /^[\s\u002d\u2013]*$/
  const named = (n) => !!(n && n.name && !BLANK_NAME.test(n.name) && n.name !== natId(n))
  const disp = (n) => (named(n) ? n.name : natId(n))
  const shortSvc = (s) => {
    s = String(s || ''); if (!s) return '?'
    if (/vpce-svc-/.test(s)) return 'PrivateLink'
    const p = s.split('.'); return p[0] === 'com' ? p.slice(3).join('.') || s : s
  }
  const lbKind = (t) => { t = String(t || ''); return /gw|gateway/i.test(t) ? 'GWLB' : /nlb|network/i.test(t) ? 'NLB' : /clb|classic/i.test(t) ? 'CLB' : 'ALB' }
  const push = (m, k, v) => { (m.get(k) || m.set(k, []).get(k)).push(v) }

  let X = null          // flow.js 가 넘긴 문맥(`awsCtx()`) — 마지막 배치의 것
  let M = null          // 모델(그릴 것)
  let O = { az: true, sn: true }
  let B = null          // 배치 한 벌의 산출(선·칸·사각형·정보창)
  const VT_W = new Map() // VPC 이름 → 처음 잰 글자 폭(정수) — 다시 그려도 같은 바이트(아래 「VPC 이름 옆 ▸」)
  let SC = null         // 마지막 장면(= B + 크기)
  let anchorPt = null; let openV = null; let cardKey = null; let cardBack = null; let wired = false; let downAt = null; let HL = null

  // ======================================================================== 모델
  function model () {
    const { T, mById } = X
    const xg = new Set()   // 보기 범위 밖인데 흐린 TGW 로 세운 것(§17.22) — 이 화면에서는 「사라진 것」이 아니다
    const gone = (id) => X.goneByHand(id) && !xg.has(id)
    const up = (n, t) => { let c = n; let g = 0; while (c && g++ < 40) { if (c.type === t) return c; c = c.parent_id ? mById.get(c.parent_id) : null } return null }
    const aiOf = (n) => { const a = up(n, 'account'); return a ? X.accounts.indexOf(a) : -1 }
    const Mo = { accts: [], regs: new Map(), vpcs: new Map(), subnets: new Map(), items: new Map(), hubs: new Map(), globs: new Map(),
      ports: new Map(), portOf: new Map(), peers: [], xpeers: [], tgwPeers: [], dxtgw: [], stubLines: [], hidden: new Map(), why: new Map(),
      partial: new Set(), list: new Map(), rel: new Map(), vifOf: new Map(), attOf: new Map(), tgwRt: new Map(), pp: new Map(), ppOf: new Map(), cfo: [], vpnl: [], cnx: [], ltg: [], wfl: [], wfg: [] }
    const why = (n, w) => Mo.why.set(n.id, w)
    const natIx = new Map(); for (const n of T.nodes) natIx.set(X.acctOf(n) + '|' + natId(n), n.id)
    const byNat = (n, v) => (v ? natIx.get(X.acctOf(n) + '|' + v) || null : null)
    const eOf = new Map(); for (const e of T.edges) { push(eOf, e.source, e); if (e.target !== e.source) push(eOf, e.target, e) }
    const eo = (id) => eOf.get(id) || []
    const other = (e, id) => (e.source === id ? e.target : e.source)
    for (const p of T.partial || []) if (p && p.target) Mo.partial.add(p.target)
    for (const r of T.listOnly || []) if (r && r.vpcId) push(Mo.list, r.vpcId, r)

    for (const a of X.accounts) if (!gone(a.id)) Mo.accts.push(X.accounts.indexOf(a))
    const live = T.nodes.filter((n) => !gone(n.id))
    for (const n of live) {
      if (n.type !== 'region') continue
      const i = aiOf(n); if (i < 0 || !Mo.accts.includes(i)) { why(n, '들어갈 계정 상자가 없다'); continue }
      push(Mo.regs, i, n)
    }
    for (const rs of Mo.regs.values()) rs.sort((a, b) => X.regionRank(a.name) - X.regionRank(b.name) || a.name.localeCompare(b.name))
    const regIds = new Set([...Mo.regs.values()].flat().map((r) => r.id))
    const regOk = (n) => { const r = up(n, 'region'); return r && regIds.has(r.id) ? r : null }

    for (const n of live) {
      if (n.type !== 'vpc') continue
      const r = regOk(n); if (!r) { why(n, '들어갈 리전 상자가 없다'); continue }
      const a = n.attrs || {}
      Mo.vpcs.set(n.id, { id: n.id, n, acct: aiOf(n), region: r.id, code: r.name, name: disp(n), named: named(n), cidr: a.cidr || '',
        subnets: [], items: [], ports: [] })
    }
    for (const n of live) {
      if (n.type !== 'subnet') continue
      const vn = up(mById.get(n.parent_id), 'vpc'); const v = vn && Mo.vpcs.get(vn.id)
      if (!v) { why(n, '들어갈 VPC 상자가 없다'); continue }
      const s = { id: n.id, n, vpc: v.id, acct: v.acct, az: n.az || (n.attrs || {}).az || '?', name: disp(n), named: named(n),
        tier: n.tier === 'public' ? 'public' : 'private', items: [], exits: new Set(), routes: [] }
      Mo.subnets.set(n.id, s); v.subnets.push(s)
    }
    // 아이콘이 되는 자원
    for (const n of live) {
      // Bedrock 엔드포인트(§17.32) = 엔진이 확인한 것만(어댑터 `bedrock`) — 일반 엔드포인트 묶음에 넣지 않고 제 칸에 선다
      const k = n.type === 'vpc_endpoint' && n.bedrock ? 'brk' : KIND[n.type]; if (!k) continue
      const a = n.attrs || {}
      const base = { id: n.id, n, kind: k, acct: aiOf(n), name: disp(n), named: named(n), state: a.state, subnets: [] }
      // CloudFront 범위 WAF(2.12.0 · §17.41) = 리전이 없다 → 전역 줄(아래 다른 전역 자원과 같은 자리) · 리전 범위 WAF = 리전 자원 줄(아래 `hubK`)
      if (k === 'waf' && n.wafScope === 'cloudfront') {
        if (base.acct < 0 || !Mo.accts.includes(base.acct)) { why(n, '들어갈 계정 상자가 없다'); continue }
        Mo.globs.set(n.id, base); continue
      }
      if (k === 'dxgw' || k === 'cf' || k === 'r53' || k === 's3' || k === 'ga' || k === 'cwan') { // 리전이 없는 자원 → 계정 상자의 전역 줄(§17.1 · Route 53 = 2.8.0 §17.30 · S3 = §17.31 · 가속기 = 2.10.0 §17.37)
        if (base.acct < 0 || !Mo.accts.includes(base.acct)) { why(n, '들어갈 계정 상자가 없다'); continue }
        // S3 버킷(§17.31) — 버킷 이름이 native id 이자 사람이 알아보는 이름이다(Name 태그가 없다) → 이름표 `S3 (버킷 이름)`
        if (k === 's3') base.named = true
        Mo.globs.set(n.id, base); continue
      }
      const vn = up(n, 'vpc'); const v = vn && Mo.vpcs.get(vn.id)
      // 서브넷을 정하지 않는 리전 단위 자원(§17.12 ①) = 리전 줄 — 서브넷 칸은 거짓이고 잔차 표는 「못 그렸다」로 읽힌다
      const regional = !v && n.noSubnets && !!NOSUB_WHY[n.type]
      // VPC 에 붙은 Lambda(2.8.0 · P2-6 · 서브넷을 아는 수집한 함수)는 리전 줄이 아니라 그 서브넷 칸 — 나머지 Lambda 는 리전 자원 줄 그대로
      const inVpcFn = k === 'lambda' && v && Array.isArray(n.spans) && n.spans.length
      const hubK = HUB.has(k) || k === 'waf'   // 리전 범위 WAF(2.12.0 · §17.41) = 리전 자원 줄
      if ((hubK && !inVpcFn) || (k === 'igw' && !v) || regional) {
        const r = regOk(n); if (!r) { why(n, '들어갈 리전 상자가 없다'); continue }
        Mo.hubs.set(n.id, { ...base, region: r.id, detached: k === 'igw', ...(regional ? { regional: true } : {}) }); continue
      }
      // ASG·ECS·엔드포인트는 VPC 를 서브넷으로만 안다(어댑터) — 그 서브넷이 이 화면에 없으면 VPC 상자가 아니라 서브넷이 없는 것이다.
      //   서브넷이 **비었다**(읽었고 없다)는 다른 일이다 · 구성을 못 읽어(권한 부족) 자리를 모르는 것도 다른 일이다
      if (!v) {
        why(n, n.noSubnets ? NOSUB_WHY[n.type] || '구성에 서브넷이 없다 — 들어갈 VPC 를 말하지 않는다'
          : Array.isArray(n.spans) ? '걸친 서브넷이 이 화면에 없다'
            : Mo.partial.has(n.id) ? '구성 일부를 읽지 못해(권한 부족) 들어갈 자리를 모른다' : '들어갈 VPC 상자가 없다')
        continue
      }
      const it = { ...base, region: v.region, vpc: v.id }
      if (k === 'lb') { it.lbType = lbKind(a.type); it.internet = a.scheme === 'internet-facing' }
      if (k === 'vpce' || k === 'brk') { it.epType = a.type; it.svc = shortSvc(a.service || n.name); it.named = true; it.name = it.svc }
      if (k === 'ec2') it.itype = a.type || a.instance_type
      Mo.items.set(n.id, it); v.items.push(it)
    }
    // 보기 범위 밖 TGW(§17.22 · §3g G2) — 범위 안 TGW 와 피어링하거나 범위 안 어태치먼트가 붙은 것만 흐린 TGW 로 **범위 안 쪽
    //   리전 상자**에 세운다(범위 밖 리전은 화면에 없다 → 실제 리전은 정보창이 말한다). 선을 지우면 「아무 데도 안 이어졌다」는 거짓이 된다
    if (X.SCOPE && X.outScope) {
      const at = (o) => { // 범위 안 어태치먼트 → 그 쪽 리전 상자(VPC 어태치먼트 = VPC · 피어링 어태치먼트 = 범위 안 TGW)
        const par = mById.get(o.parent_id); const v = par && par.type === 'vpc' ? Mo.vpcs.get(par.id) : null
        if (v) return { region: v.region, acct: v.acct }
        const h = eo(o.id).filter((e) => e.kind === 'attachment').map((e) => Mo.hubs.get(other(e, o.id))).find((h) => h && h.kind === 'tgw' && !h.ghost)
        return h ? { region: h.region, acct: h.acct } : null
      }
      for (const n of T.nodes) {
        if (n.type !== 'transit_gateway' || !X.outScope(n.id) || Mo.hubs.has(n.id)) continue
        let pl = null
        for (const e of [...eo(n.id)].sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))) {
          const o = mById.get(other(e, n.id)); if (!o || X.goneByHand(o.id)) continue
          const h = Mo.hubs.get(o.id)
          if (e.kind === 'peering' && h && h.kind === 'tgw' && !h.ghost) pl = { region: h.region, acct: h.acct }
          else if (e.kind === 'attachment' && o.type === 'transit_gateway_attachment') pl = at(o)
          if (pl) break
        }
        if (!pl) continue
        xg.add(n.id)
        Mo.hubs.set(n.id, { id: n.id, n, kind: 'tgw', ghost: true, xscope: true, acct: pl.acct, xacct: aiOf(n), region: pl.region, name: '범위 밖', named: false, subnets: [] })
      }
    }
    Mo.xg = xg
    // 자원이 걸친 서브넷 = attachedTo ∪ 같은 VPC 서브넷과 잇는 엣지(서브넷에서 나가는 라우트는 뺀다)
    for (const it of Mo.items.values()) {
      const S = new Set()
      const at = it.n.attachedTo; if (at && Mo.subnets.has(at) && Mo.subnets.get(at).vpc === it.vpc) S.add(at)
      // 걸친 서브넷(EKS·ASG·NFW — 어댑터가 속성의 서브넷 id 를 서브넷 노드로 찾은 것, §17.12)
      for (const sid of it.n.spans || []) if (Mo.subnets.has(sid) && Mo.subnets.get(sid).vpc === it.vpc) S.add(sid)
      if (it.kind !== 'nat' && it.kind !== 'igw') {
        for (const e of eo(it.id)) {
          const o = other(e, it.id); const s = Mo.subnets.get(o); if (!s || s.vpc !== it.vpc) continue
          if (e.kind === 'flow' && e.source === o) continue
          S.add(o)
        }
      }
      it.subnets = [...S].sort()
      // 걸친 서브넷을 말하는 종류인데 이 화면에 하나도 없으면 놓을 곳이 없다 — 오른쪽 열에 세우면 「어느 서브넷에도 없다」는 거짓이 된다
      if (Array.isArray(it.n.spans) && !it.subnets.length) {
        why(it.n, it.n.noSubnets ? NOSUB_WHY[it.n.type] || '구성에 서브넷이 없다 — 들어갈 VPC 를 말하지 않는다' : '걸친 서브넷이 이 화면에 없다'); Mo.items.delete(it.id); const v = Mo.vpcs.get(it.vpc); v.items.splice(v.items.indexOf(it), 1); continue
      }
      for (const sid of it.subnets) Mo.subnets.get(sid).items.push(it)
      // NAT 은 서브넷 하나에만 선다(AWS 규칙 · 엔진 = 부모 서브넷) — 그 서브넷이 이 화면에 없으면 놓을 곳이 없다(#45 리뷰)
      if (it.kind === 'nat' && it.subnets.length !== 1) why(it.n, 'NAT 게이트웨이가 선 서브넷이 이 화면에 없다')
    }
    // CloudFront → 원본 LB(#43 · §17.1) — 전역 줄의 배포와 VPC 안의 LB 가 둘 다 이 화면에 있을 때만 선이 된다(아니면 정보창 · 잔차)
    //   2.8.0(§17.31) — VPC 원본은 EC2 인스턴스도 원본이다(같은 길 · 끝 = 그 아이콘·묶음 칸) · `via`(VPC 원본 · Route 53 별칭)는 정보창 「오리진 유형」 줄.
    //   S3 버킷은 같은 전역 줄이라 선이 아니다(배포 「원본」 칸 · 버킷 「이 버킷을 사용하는 배포」 칸 · 회계 = 「선 없이 이어진 것」)
    for (const e of T.edges) {
      if (e.kind !== 'origin' || gone(e.source) || gone(e.target)) continue
      const g = Mo.globs.get(e.source); const lb = Mo.items.get(e.target)
      if (g && g.kind === 'cf' && lb && (lb.kind === 'lb' || lb.kind === 'ec2') && !Mo.cfo.some((p) => p.cf === e.source && p.lb === e.target)) Mo.cfo.push({ cf: e.source, lb: e.target, via: (e.attrs || {}).via || null, alias: (e.attrs || {}).alias || null })
    }
    // Route 53 별칭 → LB(2.8.0 · §17.30) — CloudFront 원본 선과 **같은 길**(전역 줄 아이콘 밑 → 계정 왼쪽 틈 → LB 리전 차선 → LB) · 선 종류만 다르다.
    //   별칭 → CloudFront 는 같은 전역 줄이라 선을 긋지 않는다(두 정보창 · 회계 = 「선 없이 이어진 것」)
    for (const e of T.edges) {
      if (e.kind !== 'dnsalias' || gone(e.source) || gone(e.target)) continue
      const g = Mo.globs.get(e.source); const lb = Mo.items.get(e.target)
      if (g && g.kind === 'r53' && lb && lb.kind === 'lb' && !Mo.cfo.some((p) => p.cf === e.source && p.lb === e.target)) Mo.cfo.push({ cf: e.source, lb: e.target, dns: true, rec: ((e.attrs || {}).record_names || []).slice() })
    }
    // Global Accelerator → 엔드포인트(2.10.0 · §17.37) — 전역 줄 → LB·EC2. CloudFront 원본 선과 **같은 길**(선 종류 · 색만 다르다).
    //   다른 리전·계정의 엔드포인트는 그 리전 상자로 간다(계정 사이 길) · 이 화면에 없는 엔드포인트는 가속기 정보창 「엔드포인트」 칸이 말한다
    for (const e of T.edges) {
      if (e.kind !== 'gaep' || gone(e.source) || gone(e.target)) continue
      const g = Mo.globs.get(e.source); const t = Mo.items.get(e.target)
      if (g && g.kind === 'ga' && t && (t.kind === 'lb' || t.kind === 'ec2') && !Mo.cfo.some((p) => p.cf === e.source && p.lb === e.target)) Mo.cfo.push({ cf: e.source, lb: e.target, ga: true, ea: { ...(e.attrs || {}) } })
    }
    Mo.cfo.sort((a, b) => (a.cf < b.cf ? -1 : a.cf > b.cf ? 1 : a.lb < b.lb ? -1 : a.lb > b.lb ? 1 : 0))
    // 서브넷의 출구 = 서브넷에서 나가는 flow 엣지 ∪ 서브넷 라우트(local 제외)
    for (const s of Mo.subnets.values()) {
      for (const e of eo(s.id)) {
        if (e.kind !== 'flow' || e.source !== s.id || gone(e.target)) continue
        s.exits.add(e.target)
        const ds = (e.attrs && e.attrs.destinations) || []; const bh = !!(e.attrs && e.attrs.blackhole)
        if (ds.length) for (const d of ds) s.routes.push({ d, t: e.target, bh }); else s.routes.push({ d: '', t: e.target, bh })
      }
      for (const r of s.n.routesUnres || []) s.routes.push({ d: r.d || '', t: null, raw: r.t, why: r.why, bh: false })
      for (const r of (s.n.attrs && s.n.attrs.routes) || []) {
        if (!r || r.t === 'local') continue
        const t = byNat(s.n, r.t)
        if (t && !gone(t)) s.exits.add(t)
        s.routes.push({ d: r.d || '', t, raw: r.t, bh: !!r.blackhole })
      }
    }
    // 기본 라우트를 받는 IGW(§17.24 인터넷 칸) → 그 라우트를 보내는 서브넷. 근거 = 어댑터가 실은 `flow` 엣지의
    //   `default_route`(실계정) · 서브넷 `attrs.routes` 의 목적지(픽스처 — 그 모양에는 blackhole 이 없다). 같은 VPC 의 IGW 만 — 남의 IGW 로는 라우트가 못 간다
    const DFLT = new Set(['0.0.0.0/0', '::/0'])
    Mo.dflt = new Map()
    const dfl = (s, t, d) => {
      const it = t && Mo.items.get(t); if (!it || it.kind !== 'igw' || it.vpc !== s.vpc) return
      if (!Mo.dflt.has(t)) Mo.dflt.set(t, new Map()); const m = Mo.dflt.get(t); if (!m.has(s.id)) m.set(s.id, new Set()); if (d) m.get(s.id).add(d)
    }
    for (const s of Mo.subnets.values()) {
      for (const e of eo(s.id)) if (e.kind === 'flow' && e.source === s.id && e.attrs && e.attrs.default_route === true && !gone(e.target)) dfl(s, e.target, '')
      for (const r of (s.n.attrs && s.n.attrs.routes) || []) if (r && DFLT.has(r.d)) { const t = byNat(s.n, r.t); if (t && !gone(t)) dfl(s, t, r.d) }
    }
    // TGW 어태치먼트 — VPC 쪽 = 포트, 리전 쪽 = 허브가 대표(DX·피어링은 선을 더한다)
    for (const n of live) {
      if (n.type !== 'transit_gateway_attachment') continue
      const a = n.attrs || {}
      const tg = new Set(eo(n.id).filter((e) => e.kind === 'attachment').map((e) => other(e, n.id)).filter((id) => Mo.hubs.has(id) && Mo.hubs.get(id).kind === 'tgw'))
      const t2 = byNat(n, a['transit gateway']); if (t2 && Mo.hubs.has(t2)) tg.add(t2)
      const tgs = [...tg]
      const par = mById.get(n.parent_id); const v = par && par.type === 'vpc' ? Mo.vpcs.get(par.id) : null
      if (!tgs.length) { why(n, '연결된 Transit Gateway 가 이 화면에 없습니다(수집 범위 밖일 수 있습니다)'); continue }
      for (const t of tgs) push(Mo.attOf, t, n)
      if (v) {
        const t = tgs[0]; const k = v.id + '|' + t
        if (Mo.portOf.has(k)) { Mo.hidden.set(n.id, Mo.portOf.get(k)); continue }
        Mo.portOf.set(k, n.id); Mo.ports.set(n.id, { id: n.id, n, vpc: v.id, tgw: t, acct: v.acct }); v.ports.push(t)
        continue
      }
      Mo.hidden.set(n.id, tgs[0])
      // TGW Connect(2.11.0 · §17.38) — 아이콘·선이 아니라 **전송 어태치먼트 선 위의 오버레이**다. 전송 쪽(VPC 포트 · DX 선)은 이 루프가 다 돈 뒤에야 안다
      if (n.connect) { Mo.cnx.push({ id: n.id, n, tgw: tgs[0], on: n.connect.on }); continue }
      const kd = String(a['종류'] || a.resource_type || '')
      if (/direct.?connect/i.test(kd)) {
        const g = byNat(n, a['대상'])
        if (g && Mo.globs.has(g)) Mo.dxtgw.push({ tgw: tgs[0], dxgw: g, att: n.id })
        else {
          const gid = 'aws:ghost:' + n.id; const t = Mo.hubs.get(tgs[0])
          Mo.globs.set(gid, { id: gid, n: { id: gid, type: 'aws-ghost-dxgw', parent_id: null, region: null, attrs: {} }, kind: 'dxgw', ghost: true,
            acct: t.acct, name: 'DX 게이트웨이', named: false, subnets: [], att: n.id })
          Mo.dxtgw.push({ tgw: tgs[0], dxgw: gid, att: n.id })
        }
      } else if (/peering/i.test(kd) && tgs.length === 2) Mo.tgwPeers.push({ a: tgs[0], b: tgs[1], att: n.id })
      else if (/^vpn$/i.test(kd)) { // TGW ↔ VPN 연결(§17.1) — 어태치먼트는 TGW 가 대표하고 선은 TGW ↔ VPN 연결 하나
        for (const e of eo(n.id)) { const h = e.kind === 'attachment' && Mo.hubs.get(other(e, n.id)); if (h && h.kind === 'vpn' && h.region === Mo.hubs.get(tgs[0]).region) Mo.vpnl.push({ a: h.id, b: tgs[0], att: n.id }) }
      }
    }
    // VGW 포트(§17.1) — 붙은 VPC 마다 위쪽 변의 작은 원 하나. 엔진 노드가 없다(어댑터가 VGW → VPC 엣지를 `vpcIds` 로 실었다) → 합성 키
    for (const h of [...Mo.hubs.values()].filter((x) => x.kind === 'vgw').sort((a, b) => (a.id < b.id ? -1 : 1))) {
      for (const vid of h.n.vpcIds || []) {
        const v = Mo.vpcs.get(vid); if (!v || v.region !== h.region) continue
        const key = `aws:vgwp:${vid}|${h.id}`
        Mo.portOf.set(vid + '|' + h.id, key); Mo.ports.set(key, { id: key, n: { id: key, type: 'aws-vgwport', parent_id: vid, region: v.n.region, attrs: {} }, vpc: vid, tgw: h.id, acct: v.acct, vgw: true }); v.ports.push(h.id)
      }
    }
    // Cloud WAN 코어 네트워크 ↔ VPC(2.11.0 · §17.39) — VPC 위 변의 포트(TGW 포트와 같은 원 · Cloud WAN 색) + 전역 줄 → 그 포트 선(CloudFront 원본 선과 같은 길 ·
    //   `Mo.cfo`). 라우트 `CoreNetworkArn` 대상(엔진이 이 노드로 풀었다)은 이 포트로 나간다(아래 `routeExitKey`) · 이 화면에 없는 VPC = 정보창 「연결」 칸
    for (const e of [...T.edges].filter((x) => x.kind === 'cwanatt').sort((a, b) => (a.id < b.id ? -1 : 1))) {
      if (gone(e.source) || gone(e.target)) continue
      const g = Mo.globs.get(e.source); const v = Mo.vpcs.get(e.target)
      if (!g || g.kind !== 'cwan' || !v || Mo.portOf.has(v.id + '|' + g.id)) continue
      const key = `aws:cwanp:${v.id}|${g.id}`
      Mo.portOf.set(v.id + '|' + g.id, key); v.ports.push(g.id)
      Mo.ports.set(key, { id: key, n: { id: key, type: 'aws-cwanport', parent_id: v.id, region: v.n.region, attrs: {} }, vpc: v.id, tgw: g.id, acct: v.acct, cwan: true, ea: { ...(e.attrs || {}) } })
      Mo.cfo.push({ cf: g.id, lb: key, cwan: true, ea: { ...(e.attrs || {}) } })
    }
    Mo.cfo.sort((a, b) => (a.cf < b.cf ? -1 : a.cf > b.cf ? 1 : a.lb < b.lb ? -1 : a.lb > b.lb ? 1 : 0))
    // VPC Lattice 서비스 네트워크 ↔ VPC(2.11.0 · §17.40) — TGW 어태치먼트와 **같은 모양**(VPC 위 변 포트 → 제 차선 → 리전 자원 줄 아이콘 밑) · Lattice 색
    for (const e of [...T.edges].filter((x) => x.kind === 'lsnvpc').sort((a, b) => (a.id < b.id ? -1 : 1))) {
      if (gone(e.source) || gone(e.target)) continue
      const h = Mo.hubs.get(e.source); const v = Mo.vpcs.get(e.target)
      if (!h || h.kind !== 'lsn' || !v || Mo.portOf.has(v.id + '|' + h.id)) continue
      const key = `aws:lsnp:${v.id}|${h.id}`
      Mo.portOf.set(v.id + '|' + h.id, key); v.ports.push(h.id)
      Mo.ports.set(key, { id: key, n: { id: key, type: 'aws-lsnport', parent_id: v.id, region: v.n.region, attrs: {} }, vpc: v.id, tgw: h.id, acct: v.acct, lsn: true, ea: { ...(e.attrs || {}) } })
    }
    // TGW Connect(§17.38) — 올라탄 전송 어태치먼트가 이 화면의 VPC 포트(같은 짝 어태치먼트면 그 포트)거나 DX 선이면 그 선 위에 겹쳐 긋는다(아래 `cnxLines`).
    //   🔴 전송이 이 화면에 없으면 지어내지 않는다 — 왼쪽 아래 「!」 목록(대표로 숨기지 않는다 · 불변식 ④)
    for (const c of Mo.cnx) {
      const on = c.on; const pk = on && (Mo.ports.has(on) ? on : Mo.ports.has(Mo.hidden.get(on)) ? Mo.hidden.get(on) : null)
      const dx = on && !pk ? Mo.dxtgw.find((d) => d.att === on) : null
      if (pk) { c.port = pk; Mo.hidden.set(c.id, pk) } else if (dx) c.dx = dx
      else {
        Mo.hidden.delete(c.id)
        why(c.n, on ? `TGW Connect 어태치먼트 — 올라탄 어태치먼트(${natId(mById.get(on) || { id: on })})가 이 화면에 없습니다` : `TGW Connect 어태치먼트 — 올라탄 어태치먼트(${(c.n.connect || {}).transport || '?'})가 이 수집본에 없습니다`)
      }
    }
    // VPN 연결 ↔ CGW · VGW(엔진 `attachment` · TGW 쪽은 위 어태치먼트 루프)
    for (const h of [...Mo.hubs.values()].filter((x) => x.kind === 'vpn').sort((a, b) => (a.id < b.id ? -1 : 1))) {
      for (const e of eo(h.id)) {
        const o = e.kind === 'attachment' && Mo.hubs.get(other(e, h.id))
        if (o && (o.kind === 'cgw' || o.kind === 'vgw') && o.region === h.region && !Mo.vpnl.some((l) => l.a === h.id && l.b === o.id)) Mo.vpnl.push({ a: h.id, b: o.id })
      }
    }
    Mo.vpnl.sort((x, y) => (x.a < y.a ? -1 : x.a > y.a ? 1 : x.b < y.b ? -1 : x.b > y.b ? 1 : 0))
    // 스텁(수집 범위 밖 계정의 TGW) = 흐린 허브
    for (const n of live) {
      if (n.type !== 'stub') continue
      const r = regOk(n); if (!r) { why(n, '들어갈 리전 상자가 없다'); continue }
      Mo.hubs.set(n.id, { id: n.id, n, kind: 'tgw', ghost: true, stub: true, acct: aiOf(n), region: r.id, name: '다른 계정', named: false, subnets: [] })
    }
    // TGW ↔ TGW · TGW → 스텁 (엣지로 온 피어링)
    for (const e of T.edges) {
      if (e.kind !== 'peering' || gone(e.source) || gone(e.target)) continue
      const A = Mo.hubs.get(e.source); const Bh = Mo.hubs.get(e.target)
      if (A && Bh && A.kind === 'tgw' && Bh.kind === 'tgw') {
        const sa = A.stub || A.xscope; const sb = Bh.stub || Bh.xscope
        if (sa || sb) Mo.stubLines.push({ a: sa ? e.target : e.source, b: sa ? e.source : e.target, e })
        else if (!Mo.tgwPeers.some((p) => (p.a === e.source && p.b === e.target) || (p.a === e.target && p.b === e.source))) Mo.tgwPeers.push({ a: e.source, b: e.target, e })
      }
    }
    // VPC 피어링 · VIF · 대표로만 있는 것 · 규칙 없는 것
    for (const n of live) {
      const t = n.type
      if (t === 'account' || t === 'region' || t === 'vpc' || t === 'subnet' || t === 'transit_gateway_attachment' || t === 'stub' || KIND[t]) continue
      if (Mo.why.has(n.id)) continue
      if (t === 'vpc_peering_connection') {
        const vs = [...new Set(eo(n.id).filter((e) => e.kind === 'peering').map((e) => other(e, n.id)).filter((id) => Mo.vpcs.has(id)))]
        // 상대 VPC 가 수집 범위 밖(다른 계정·리전) — 흐린 「VPC (범위 밖)」 칸을 범위 안 VPC 의 리전 상자 밑줄에 세우고 잇는다(§17.1).
        //   어댑터가 엔진 속성의 양쪽(요청자·수락자)을 넘긴다 — 범위 안 VPC 가 아닌 쪽이 상대다
        const vn = vs.length === 1 && natId(Mo.vpcs.get(vs[0]).n)
        const side = vn && (n.peerSides || []).find((x) => x && x.vpc && x.vpc !== vn)
        const mine = vn && (n.peerSides || []).find((x) => x && x.vpc === vn)
        if (vs.length === 2) { Mo.peers.push({ a: vs[0], b: vs[1], pcx: n.id, n }); Mo.hidden.set(n.id, 'aws:rep') } else if (side) {
          const v = Mo.vpcs.get(vs[0]); const gid = 'aws:xvpc:' + n.id
          Mo.xpeers.push({ a: vs[0], pcx: n.id, n, it: { id: gid, n: { id: gid, type: 'aws-xvpc', parent_id: null, region: null, attrs: {} }, kind: 'xvpc', ghost: true, outside: true,
            side, other: mine && side.acct && mine.acct === side.acct ? '다른 리전' : '다른 계정', acct: v.acct, region: v.region, name: '범위 밖', named: false, subnets: [] } })
          Mo.hidden.set(n.id, 'aws:rep')
        } else why(n, 'VPC 피어링의 두 VPC 가 이 화면에 다 있지 않다')
      } else if (t === 'direct_connect_virtual_interface') {
        const p = Mo.hubs.get(n.parent_id)
        if (p && p.kind === 'dxcon') { Mo.hidden.set(n.id, p.id); push(Mo.vifOf, p.id, n) } else why(n, '이 VIF 의 Direct Connect 연결이 이 화면에 없다')
      } else if (t === 'internet') {
        // 픽스처의 인터넷 노드(§16.8) — 칸이 있는 IGW 중 첫째의 칸이 대표한다(§17.24). 칸이 없으면 전처럼 IGW 가
        const gs = eo(n.id).map((e) => other(e, n.id)).filter((id) => Mo.items.has(id) && Mo.items.get(id).kind === 'igw').sort()
        const gc = gs.find((id) => Mo.dflt.has(id))
        if (gc) Mo.hidden.set(n.id, 'aws:inet:' + gc); else if (gs[0]) Mo.hidden.set(n.id, gs[0]); else why(n, '이 인터넷 노드에 닿는 인터넷 게이트웨이가 이 화면에 없다')
      } else if (t === 'external_ip') {
        // 아이콘이 없다(§17.27) — LB → 출구 포트 선이 대표한다(모델 끝의 LB 대상 절이 숨긴다). 그 LB 가 이 화면에 없으면 놓을 곳이 없다
        if (!eo(n.id).some((e) => e.kind === 'lbtarget' && e.target === n.id && Mo.items.has(e.source) && !gone(e.source))) why(n, '이 외부 IP 를 대상으로 하는 로드밸런서가 이 화면에 없습니다')
      } else if (t === 'aws_service' || t === 'endpoint_service') {
        const g = eo(n.id).map((e) => other(e, n.id)).find((id) => Mo.items.has(id) && Mo.items.get(id).kind === 'vpce')
        if (g) Mo.hidden.set(n.id, g); else why(n, '이 서비스에 닿는 VPC 엔드포인트가 이 화면에 없다')
      } else if (t === 'transit_gateway_route_table') {
        // TGW 정보창의 「라우트 테이블」 칸이 대표한다(§17.11) — 흐린 TGW(범위 밖·스텁)에는 정보창 칸이 없으므로 사유로 남긴다
        const h = Mo.hubs.get(n.parent_id)
        if (h && h.kind === 'tgw' && !h.ghost) { Mo.hidden.set(n.id, h.id); push(Mo.tgwRt, h.id, n) } else why(n, '이 라우트 테이블의 Transit Gateway 가 이 화면에 없다')
      } else if (t === 'route_table' || t === 'security_group' || t === 'network_acl') {
        const v = up(n, 'vpc'); if (v && Mo.vpcs.has(v.id)) Mo.hidden.set(n.id, v.id); else why(n, '이 리소스의 VPC 가 이 화면에 없습니다')
      } else if (t === 'elastic_ip') {
        // 탄력적 IP(2.10.0 · §17.34) = 아이콘이 아니라 **주인의 정보창 줄**이다 — 주인이 이 화면에 있으면 주인이 대표한다(규칙상 그리지 않는다).
        //   🔁 연결이 없는 EIP 는 대리 상자가 대표한다 — 「!」 에 없다(사용자 결정 2026-10-21 오전 · 「!」 = 도구가 못 읽거나 못 그린 것만 · 쓰지 않는 EIP 는 AWS 쪽 사실)
        //   연결이 있는데 주인이 없으면 왼쪽 아래 「!」 목록 — 주인을 못 찾음(까닭) · 주인이 캔버스에 없음을 가른다
        const o = n.eipOwner
        if (o && (Mo.items.has(o) || Mo.hubs.has(o) || Mo.globs.has(o))) Mo.hidden.set(n.id, o)
        else if (n.eipIdle) Mo.hidden.set(n.id, 'aws:rep')
        else if (n.eipWhy) why(n, `연결 대상을 찾지 못한 탄력적 IP입니다 — ${eipWhyWord(n.eipWhy)}`)
        else why(n, o ? '이 탄력적 IP 가 연결된 리소스가 이 화면에 없습니다' : '연결 대상을 찾지 못한 탄력적 IP입니다')
      } else why(n, `${WHY_NEW} — 타입 «${t}»`)
    }
    // VPC 피어링 포트(§17.1 · §17.18-11) — VPC 한쪽마다 키 하나. 그 VPC 서브넷이 이 피어링으로 라우트를 보낼 때만 그린다(layoutVpc)
    const ppAdd = (vid, pcx, n, other) => { const key = `aws:pp:${vid}|${pcx}`; Mo.ppOf.set(vid + '|' + pcx, key); Mo.pp.set(key, { key, vpc: vid, pcx, n, other }) }
    for (const p of Mo.peers) { ppAdd(p.a, p.pcx, p.n, p.b); ppAdd(p.b, p.pcx, p.n, p.a) }
    for (const p of Mo.xpeers) ppAdd(p.a, p.pcx, p.n, p.it.id)
    // LB → 대상(§17.27 · 스키마 2.7.0) — 그릴 짝만 모은다. 묶음 칸·포트로 접는 일과 길은 배치 뒤(`build`)에 한다.
    //   외부 IP 는 아이콘이 없다 → 그 IP 가 나가는 이 VPC 의 포트(엔진 `route_exits` → TGW·VGW·피어링 포트)가 대표하고,
    //   포트를 못 찾으면 LB 의 VPC 가 대표한다(선은 VPC 오른쪽 변에서 끝난다). 🔴 못 그린 짝(대상이 이 화면에 없다)은 모으지 않는다 —
    //   회계(`account`)가 「못 그린 관계」·「선 없이 이어진 것」으로 센다(조용히 사라지지 않는다).
    Mo.lbt = []
    const exitOf = (lb, ex) => {
      for (const nat of [...(ex || [])].sort()) {
        const t = byNat(lb.n, nat); if (!t) continue
        const k = Mo.portOf.get(lb.vpc + '|' + t) || Mo.ppOf.get(lb.vpc + '|' + t)
        if (k) return { key: k, hub: t, nat }
      }
      return null
    }
    for (const e of T.edges) {
      if (e.kind !== 'lbtarget' || gone(e.source) || gone(e.target)) continue
      const lb = Mo.items.get(e.source); const tn = mById.get(e.target)
      if (!lb || lb.kind !== 'lb' || !tn) continue
      if (tn.type === 'external_ip') {
        const ex = exitOf(lb, (e.attrs || {}).route_exits || tn.exits)
        Mo.lbt.push({ e, lb: e.source, ext: tn.id, ip: (tn.attrs || {}).IP || natId(tn), exit: ex, raw: (e.attrs || {}).route_exits || tn.exits || null })
        if (!Mo.hidden.has(tn.id)) Mo.hidden.set(tn.id, ex ? ex.key : lb.vpc)
      } else if (Mo.items.has(tn.id) || Mo.hubs.has(tn.id)) Mo.lbt.push({ e, lb: e.source, id: tn.id })
    }
    Mo.lbtKnown = !!(T.run && T.run.lbt)
    // 엔드포인트 → 제공자(§17.28 · 2.8.0) — 두 끝이 다 이 화면의 아이콘일 때만(아니면 회계가 「선 없이 이어진 것」으로 센다)
    Mo.epp = []
    for (const e of T.edges) {
      if (e.kind !== 'epprov' || gone(e.source) || gone(e.target)) continue
      const v = Mo.items.get(e.source); const t = Mo.items.get(e.target)
      if (v && v.kind === 'vpce' && t) Mo.epp.push({ e, lb: e.source, id: e.target, ep: true })
    }
    // API Gateway → LB(VPC 링크 · 2.10.0 · §17.35) — 리전 자원 줄의 API 와 VPC 안 LB 가 둘 다 이 화면에 있을 때만(같은 리전)
    Mo.vlk = []
    for (const e of T.edges) {
      if (e.kind !== 'vpclink' || gone(e.source) || gone(e.target)) continue
      const h = Mo.hubs.get(e.source); const lb = Mo.items.get(e.target)
      if (h && h.kind === 'apigw' && lb && lb.kind === 'lb' && h.region === lb.region) Mo.vlk.push({ e, api: e.source, lb: e.target })
    }
    // RDS 프록시 → DB(2.10.0 · §17.36) — 같은 VPC(또는 같은 리전의 다른 VPC) 안의 RDS · 클러스터 카드
    Mo.prx = []
    for (const e of T.edges) {
      if (e.kind !== 'dbproxy' || gone(e.source) || gone(e.target)) continue
      const x = Mo.items.get(e.source); const t = Mo.items.get(e.target)
      if (x && x.kind === 'rdsp' && t && (t.kind === 'rds' || t.kind === 'rdsc')) Mo.prx.push({ e, px: e.source, id: e.target })
    }
    // VPC Lattice 서비스 → 대상(2.11.0 · §17.40) — EC2·ALB = API Gateway VPC 링크와 같은 길(리전 줄 밑 → VPC 위 변 → 대상) · 리전 줄의 Lambda = VPN 선과 같은
    //   리전 줄 위 차선(아이콘 위 → 제 차선 → 함수 위) · 같은 리전일 때만(Lattice 는 리전 서비스다)
    for (const e of [...T.edges].filter((x) => x.kind === 'lsvctgt').sort((a, b) => (a.id < b.id ? -1 : 1))) {
      if (gone(e.source) || gone(e.target)) continue
      const h = Mo.hubs.get(e.source); if (!h || h.kind !== 'lsvc') continue
      const t = Mo.items.get(e.target); const f = Mo.hubs.get(e.target)
      if (t && (t.kind === 'ec2' || t.kind === 'lb') && t.region === h.region) Mo.ltg.push({ e, svc: h.id, id: e.target })
      else if (f && f.kind === 'lambda' && f.region === h.region && !Mo.vpnl.some((l) => l.a === h.id && l.b === f.id)) Mo.vpnl.push({ a: h.id, b: f.id, kind: 'lsvctgt', e })
    }
    // WAF 웹 ACL → 보호 대상(2.12.0 · §17.41) — 셋 다 이미 있는 길을 쓴다(선 종류 · 색만 WAF):
    //   CloudFront 범위 WAF → 배포 = 같은 전역 줄 → 전역 줄 위 차선(`wfg` · 아이콘 위 → 제 차선 → 배포 위)
    //   리전 WAF → ALB = API Gateway VPC 링크와 같은 길(`wfl` · 리전 줄 밑 → VPC 위 변 → LB) · 리전 WAF → API = VPN 선과 같은 리전 줄 위 차선(`vpnl`)
    //   같은 리전·계정일 때만(WAF 는 그 리전의 자원만 지킨다) · 그 밖(대상이 이 화면에 없다) = 회계가 「그림에 없는 연결」로 센다
    const wafE = [...T.edges].filter((x) => x.kind === 'wafp' && !gone(x.source) && !gone(x.target)).sort((a, b) => (a.id < b.id ? -1 : 1))
    for (const e of wafE) {
      const g = Mo.globs.get(e.source); const h = Mo.hubs.get(e.source)
      const tg = Mo.globs.get(e.target); const ti = Mo.items.get(e.target); const th = Mo.hubs.get(e.target)
      if (g && g.kind === 'waf' && tg && tg.kind === 'cf' && tg.acct === g.acct) Mo.wfg.push({ e, waf: g.id, id: tg.id })
      else if (h && h.kind === 'waf' && ti && ti.kind === 'lb' && ti.region === h.region) Mo.wfl.push({ e, waf: h.id, id: ti.id })
      else if (h && h.kind === 'waf' && th && th.kind === 'apigw' && th.region === h.region && !Mo.vpnl.some((l) => l.a === h.id && l.b === th.id)) Mo.vpnl.push({ a: h.id, b: th.id, kind: 'wafp', e })
    }
    Mo.vpnl.sort((x, y) => (x.a < y.a ? -1 : x.a > y.a ? 1 : x.b < y.b ? -1 : x.b > y.b ? 1 : 0))
    // DX 길(🔁 사용자 결정 2026-10-26 — 「DX 연결 → DX 게이트웨이 → VGW」가 아이콘은 다 있는데 선이 없어 끊겨 보였다):
    //   ① DX 연결 → DX 게이트웨이 = 그 연결의 VIF 가 붙은 게이트웨이(엔진 `attachment` VIF → DXGW · VIF 는 연결 아이콘 안 — 선 하나가 VIF 들을 대표)
    //   ② DX 게이트웨이 → VGW = 게이트웨이의 `vgwIds`(엔진 `associated_virtual_private_gateway_ids` · 같은 계정의 리전 자원 줄 VGW)
    //   선 모양은 TGW ↔ DX 게이트웨이(`dxtgw`)와 같다 — 리전 자원 줄 아이콘 위 → 리전 머리 밑 차선 → 계정 왼쪽 틈 → 전역 줄 밑 → 게이트웨이 밑.
    //   상대가 이 화면에 없으면(다른 계정 VGW · 수집 안 한 리전) 선은 없고 게이트웨이 정보창의 「게이트웨이 연결」 표가 말한다
    Mo.dxl = []
    for (const [cid, vifs] of Mo.vifOf) {
      const gs = new Map()
      for (const v of vifs) for (const e of X.T.edges) if (e.kind === 'attachment' && e.source === v.id && Mo.globs.has(e.target) && Mo.globs.get(e.target).kind === 'dxgw') push(gs, e.target, v.id)
      for (const [g, vs] of [...gs.entries()].sort((x, y) => (x[0] < y[0] ? -1 : 1))) Mo.dxl.push({ hub: cid, dxgw: g, kind: 'dxvif', vifs: vs.sort() })
    }
    for (const g of [...Mo.globs.values()].filter((x) => x.kind === 'dxgw' && !x.ghost && Array.isArray(x.n.vgwIds)).sort((x, y) => (x.id < y.id ? -1 : 1))) {
      for (const vid of [...g.n.vgwIds].sort()) {
        const h = [...Mo.hubs.values()].find((x) => x.kind === 'vgw' && natId(x.n) === vid)
        if (h) Mo.dxl.push({ hub: h.id, dxgw: g.id, kind: 'dxvgw' })
      }
    }
    Mo.dxl.sort((x, y) => (x.kind < y.kind ? -1 : x.kind > y.kind ? 1 : 0) || (x.hub < y.hub ? -1 : x.hub > y.hub ? 1 : 0) || (x.dxgw < y.dxgw ? -1 : x.dxgw > y.dxgw ? 1 : 0))
    // 선이 닿는 전역 줄·리전 자원 줄 자원(§17.1 「줄 묶음」) — 묶음에 넣지 않고 따로 세운다(선은 낱개 아이콘에 닿는다 · 검사 36 「일부 연결」과 같은 원리).
    //   관계 목록에 있으면 선이 닿는 것으로 친다(상대가 이 화면에 없어 선을 못 그어도 따로 선다 — 묶어서 선을 숨기는 쪽보다 안전하다)
    Mo.linked = new Set([...Mo.cfo.map((p) => p.cf), ...Mo.dxtgw.flatMap((p) => [p.dxgw, p.tgw]), ...Mo.dxl.flatMap((p) => [p.dxgw, p.hub]), ...Mo.vpnl.flatMap((l) => [l.a, l.b]), ...Mo.lbt.map((p) => p.id).filter(Boolean),
      ...Mo.vlk.map((p) => p.api), ...Mo.ltg.map((p) => p.svc), ...wafE.map((e) => e.source), ...Mo.wfg.map((p) => p.id), ...[...Mo.ports.values()].map((P) => P.tgw), ...Mo.tgwPeers.flatMap((p) => [p.a, p.b]), ...Mo.stubLines.flatMap((p) => [p.a, p.b])])
    return Mo
  }

  // ======================================================================== 정보(누르면 보이는 것)
  // CloudFront 오리진 유형(§17.31 · 엔진 `via`) — 주소 꼴(#43)은 null(줄 없음)
  // 탄력적 IP 의 못 이은 까닭(2.10.0 · §17.34 · 엔진 `association_unresolved` = 「<id> <까닭>」) — LB 대상과 같은 까닭 코드는 같은 말(`lbtWhy`)
  const EIP_WHY = { no_eni: '연결된 ENI 를 찾지 못했습니다(삭제되었거나 다른 계정의 ENI)', eni_unread: '권한이 없어 연결된 ENI 를 읽지 못했습니다', not_collected: '연결된 리소스가 이 수집본에 없습니다(수집하지 않음)' }
  const eipWhyWord = (line) => { const [id, code] = String(line).split(' '); return `${id} · ${EIP_WHY[code] || lbtWhy(code)}` }
  // 배치 D 까닭 코드(VPC 링크 · 프록시 대상 · 가속기 엔드포인트 · §17.35~37) → 고객 말
  const BD_WHY = { stage_variable: '단계 변수로 지정한 VPC 링크입니다(배포 단계마다 다를 수 있어 선으로 잇지 않습니다)', link_not_found: '이 VPC 링크를 찾지 못했습니다',
    cloud_map: 'Cloud Map 서비스로 연결합니다(이 도구는 Cloud Map 을 수집하지 않습니다)', unknown_target: '연결 대상을 해석하지 못했습니다',
    not_collected: '이 수집본에 없는 리소스입니다(삭제되었거나 수집하지 않음)', not_in_scope: '수집 범위 밖 계정·리전입니다',
    eip_no_owner: '탄력적 IP 가 어느 리소스에도 연결되어 있지 않습니다', unknown_type: '이 도구가 모르는 엔드포인트 유형입니다' }
  const bdWhy = (u) => (u.code === 'not_in_scope' && u.region ? `수집 범위 밖 리전(${REGION_KO[u.region] ? `${REGION_KO[u.region]} · ${u.region}` : u.region})입니다 — 이 리전을 수집 범위에 넣으면 선으로 이어집니다` : BD_WHY[u.code] || `${u.code} — 연결하지 못했습니다`)
  // WAF 웹 ACL(2.12.0 · §17.41 · ADR 0014) — 보호 대상이 없을 때의 말. 🔴 「어디에도 붙지 않았다」고 쓰지 않는다 — 엔진은 이 툴이 그리는 셋
  //   (CloudFront · ALB · API Gateway)만 묻는다(AppSync · Cognito · App Runner · Verified Access 는 묻지 않는다)
  const WAF_NONE = '이 툴이 그리는 CloudFront·ALB·API Gateway 중 보호 대상이 없습니다'
  const WAF_RES = '연결된 AWS 리소스'   // 칸 이름 = WAF 콘솔 탭 「Associated AWS resources」(§17.11 「콘솔 표현 원칙」 · 옛 「보호 대상」 · WAF 콘솔은 한국어 화면에서도 영어라 그 탭 이름을 옮긴다)
  // WAF 보호 선의 정보창(세 길 모두 같은 말 · §17.41)
  function wafLineDet (w, name, what, stages) {
    return { title: `WAF 웹 ACL → ${what}`, sub: '이 웹 ACL 의 규칙이 이 리소스로 들어오는 요청을 먼저 검사합니다',
      rows: [['웹 ACL', w ? w.name : '?'], [what, name], ...(stages && stages.length ? [['스테이지', stages.join(', ')]] : []),
        ['기본 동작', ((w && w.n.attrs) || {})['기본 동작'] || '?']], secs: [] }
  }
  // 이 자원을 보호하는 WAF 웹 ACL(엔진 `waf_protection` 의 출발) — 정보창 「웹 ACL」 줄을 누르면 그 WAF 정보창으로(§17.41)
  const wafOf = (id) => { const e = X.T.edges.find((x) => x.kind === 'wafp' && x.target === id); return e ? e.source : null }
  // Global Accelerator 의 그리지 못한 대상(§17.37 · 사용자 결정 2026-10-21) — 왼쪽 아래 「!」 목록 줄 재료 · (a) 이번 수집에 없는 리전 (b) 수집한 리전에서
  //   못 찾음 = 리전마다 한 줄 · (c) 대상이 없는 그룹은 줄이 없다(고객이 일부러 비워 둘 수 있고 요금이 없다 — 정보창에만). 정보창과 같은 재료·같은 셈
  function gaNotes () {
    const out = []
    for (const g of [...M.globs.values()].filter((x) => x.kind === 'ga' && !x.ghost).sort((a, b) => (a.id < b.id ? -1 : 1))) {
      const tg = g.n.gaTargets || []; const un = g.n.gaUnres || []
      for (const r of [...new Set([...tg.map((t) => t.region), ...un.map((u) => u.region).filter(Boolean), ...(g.n.gaEmpty || [])])].sort()) {   // 빈 그룹 리전도 돈다 — 줄을 내지 않는 것은 아래 `n` 이 정한다
        const n = un.filter((u) => (u.code === 'not_in_scope' ? u.region === r : u.region ? u.region === r : tg.some((t) => t.ref === u.ref && t.region === r))).length
        if (n) out.push({ id: `${g.id}|${r}`, ga: g.id, name: g.name, region: r, text: `Global Accelerator ${g.name} — ${gaRegion(r)}의 대상 ${n}개를 그리지 못했습니다` })
      }
    }
    // #129(2.11.0 · §17.39~40) — 그린 자원의 그리지 못한 연결·대상(정보창의 주의색 줄과 같은 셈) · 자원마다 한 줄
    const code = (h) => { const r = X.mById.get(h.region); return r ? r.name : '—' }
    for (const g of [...M.globs.values()].filter((x) => x.kind === 'cwan').sort((a, b) => (a.id < b.id ? -1 : 1))) {
      const n = (g.n.cwanUnres || []).length + (g.n.cwanOther || []).length
      if (n) out.push({ id: `${g.id}|cwan`, open: g.id, type: 'core_network', name: g.name, region: '—', text: `Cloud WAN ${g.name} — 연결 ${n}개를 그리지 못했습니다` })
    }
    for (const h of [...M.hubs.values()].filter((x) => x.kind === 'lsn' || x.kind === 'lsvc').sort((a, b) => (a.id < b.id ? -1 : 1))) {
      const n = h.kind === 'lsn' ? (h.n.lsnUnres || []).length + (h.n.lsnSvcUnres || []).length : (h.n.lsvcUnres || []).length
      if (n) out.push({ id: `${h.id}|${h.kind}`, open: h.id, type: h.kind === 'lsn' ? 'lattice_service_network' : 'lattice_service', name: h.name, region: code(h),
        text: h.kind === 'lsn' ? `VPC Lattice 서비스 네트워크 ${h.name} — 연결 ${n}개를 그리지 못했습니다` : `VPC Lattice 서비스 ${h.name} — 대상 ${n}개를 그리지 못했습니다` })
    }
    return out
  }
  // Global Accelerator 정보창(§17.37) — 리전 = 「시드니(ap-southeast-2)」 꼴(지도의 `REGION_KO` 그대로 · 표에 없으면 코드만) · 종류 이름 · 을/를
  const gaRegion = (r) => (REGION_KO[r] ? `${REGION_KO[r]}(${r})` : r)
  const GA_KIND = { alb: '로드 밸런서', nlb: '로드 밸런서', gwlb: '로드 밸런서', lb: '로드 밸런서', ec2: 'EC2 인스턴스', eip: '탄력적 IP' }
  const eulReul = (w) => { const c = String(w).charCodeAt(String(w).length - 1); return c >= 0xac00 && c <= 0xd7a3 ? w + ((c - 0xac00) % 28 ? '을' : '를') : w + '을(를)' }
  const cfVia = (via, alias) => (via === 'vpc_origin' ? 'VPC 오리진' : via === 'dns_alias' ? `Route 53 별칭 (${alias || '?'})` : via === 's3' ? 'S3' : null)
  // 계정 = `계정 이름 (앞4…뒤4)` · 이름 없으면 `계정 앞4…뒤4`(§17.5 「계정은 이름으로 부른다」 · 꼴은 flow.js `acctLabel` 한 곳) — 정보창 「계정」·「소유자 ID」 줄 = 전체 이름
  const acctName = (i) => `계정 ${X.acctLabel(i)}`
  const regName = (id) => { const r = X.mById.get(id); const c = r ? r.name : ''; return REGION_KO[c] ? `${REGION_KO[c]} · ${c}` : c }
  const subnetName = (sid) => { const s = M.subnets.get(sid); return s ? `${s.name} · ${s.az}` : sid }
  // 전역 줄(Cloud WAN 코어 네트워크 · 2.11.0)도 포트 끝이 된다 — 이름은 그 칸에서
  const hubName = (id) => { const h = M.hubs.get(id) || M.globs.get(id); return h ? h.name : id }
  const nodeName = (id) => { const n = X.mById.get(id); return n ? disp(n) : id }
  // 같은 계정의 native id → 캔버스 노드 id(정보창용 · 모델의 `byNat` 과 같은 규칙)
  const byNatOf = (n, v) => { if (!v) return null; const a = X.acctOf(n); const hit = X.T.nodes.find((m) => X.acctOf(m) === a && natId(m) === v); return hit ? hit.id : null }
  const svcOf = (it) => (it.kind === 'lb' ? it.lbType || 'LB' : it.kind === 'rdsc' && /^aurora/i.test(String(((it.n || {}).attrs || {})['엔진'] || '')) ? 'Aurora' : SVC[it.kind])
  const hasNm = (it) => !it.ghost && (it.kind === 'lb' || !!it.named)
  const typeKo = (it) => it.ghost ? (it.outside ? 'VPC · 수집 범위 밖' : it.xscope ? 'Transit Gateway · 다른 계정(보기 범위 밖)' : it.stub ? 'Transit Gateway · 다른 계정(수집 범위 밖)' : 'DX 게이트웨이 · 읽지 못함')
    : it.kind === 'lb' ? `${it.lbType} · ${it.internet ? '인터넷용' : '내부용'}`
      : it.kind === 'vpce' ? `엔드포인트 · ${it.epType ? epTypeKo(it.epType) : '?'}`
        : it.kind === 'igw' && it.detached ? '인터넷 게이트웨이 · VPC 에 연결되지 않음' : it.kind === 'sm' ? SM_KO[it.n.type] || TYPE_KO.sm
          : it.kind === 'apigw' ? `API Gateway · ${({ REST: 'REST API', HTTP: 'HTTP API', WebSocket: 'WebSocket API' })[((it.n.attrs || {})['프로토콜'])] || 'API'}` : TYPE_KO[it.kind]
  const fmt = (v) => (Array.isArray(v) ? v.map((x) => (x && typeof x === 'object' ? JSON.stringify(x) : String(x))).join('\n') : v && typeof v === 'object' ? JSON.stringify(v) : String(v))
  const SKIP_ATTR = new Set(['id', 'state', 'routes', 'route_table'])
  // 정보창 줄 이름표(docs/qa/copy-a.md ⑩ 「영문 키가 화면 라벨로 그대로 나간다」) — 어댑터·손 픽스처가 영문 키로 적은 속성을 AWS 한국어 콘솔 용어로.
  //   🔴 키는 바꾸지 않는다 — 그 키를 읽는 곳(LB 종류·체계·리스너 수 · 대상 그룹 칸 · 검사 129 · 실계정 R 축)과 자유 검색(값)이 그대로다.
  //   이미 구운 화면 파일의 옛 키도 같은 이름표를 거친다. `null` = 줄로 적지 않는다(같은 말이 부제·칸에 이미 있다).
  //   종류마다 뜻이 다른 키(`type`)는 `ATTR_LABEL_OF` 가 먼저 정한다. 표에 없는 영문 키는 검사 129 가 잡는다
  const ATTR_LABEL = { 'target group': '대상 그룹', 'route table': '라우팅 테이블', 'private DNS': '프라이빗 DNS', service: '서비스', vpc: 'VPC',
    cidr: 'CIDR', region: '리전', asn: 'Amazon 측 ASN', attachments: '연결', 'available IP': '사용 가능한 IP', 'public IP': '퍼블릭 IP', via: '경유',
    'virtual interface': '가상 인터페이스', 'stateful 규칙 그룹': '상태 저장 규칙 그룹', 'stateless 규칙 그룹': '상태 비저장 규칙 그룹',
    scheme: null, listeners: null }   // LB 체계 = 부제 「인터넷용·내부용」 · 리스너 수 = 「리스너」 칸 개수(copy-a ⑩ 「뺀다」)
  const ATTR_LABEL_OF = { load_balancer: { type: null },   // ALB·NLB·GWLB = 부제
    ec2_instance: { type: '인스턴스 유형' }, sagemaker_notebook_instance: { type: '인스턴스 유형' }, vpc_endpoint: { type: '엔드포인트 유형' },
    internet_gateway: { vpc: null }, nat_gateway: { '연결': '연결 유형' },   // IGW 의 VPC = 「VPC」 줄 · 떼어낸 IGW = 부제
    // 어댑터의 한글 키가 정보창이 손으로 적는 줄(「종류」 = 리소스 종류 · 「리전」 = 그린 자리)과 같은 이름이라 한 창에 두 번 나오던 것 — 콘솔 이름으로 가른다
    vpn_gateway: { '종류': '유형' }, customer_gateway: { '종류': '유형' }, msk_cluster: { '종류': '클러스터 유형' }, s3_bucket: { '리전': '버킷 리전' } }
  // 값도 샌다(copy-a ⑩) — AWS 영문 열거값을 고객 말로 · 표에 없는 값은 원문 그대로(이름·ID·CIDR·포트·`t3.small` 같은 유형 값은 옮기지 않는다)
  const EP_TYPE_KO = { Interface: '인터페이스', Gateway: '게이트웨이', GatewayLoadBalancer: 'Gateway Load Balancer', 'Interface (PrivateLink)': '인터페이스(PrivateLink)' }
  const epTypeKo = (v) => EP_TYPE_KO[v] || String(v)
  const ATTR_VALUE = { '엔드포인트 유형': EP_TYPE_KO, '연결 유형': { public: '퍼블릭', private: '프라이빗' },
    '엔진 모드': { provisioned: '프로비저닝', serverless: '서버리스' } }   // Aurora `engine_mode` — MSK 「종류」(어댑터)와 같은 말
  const attrLabel = (n, k) => { const o = ATTR_LABEL_OF[n && n.type]; return o && k in o ? o[k] : k in ATTR_LABEL ? ATTR_LABEL[k] : k }
  // VPC·서브넷 카드의 공통 줄 — `attrRows` 중 그 카드가 손으로 적지 않는 것만(중복 방지).
  //   계수 속성(`route table`·`network ACL`·`security group`)은 칸이 이미 개수로 보여준다 — 줄로 또 적지 않는다
  //   (검사 129 가 그 값을 오라클로 쓰므로 `attrs` 에서는 지우지 않고 표시만 뺀다).
  const VS_SKIP = new Set(['cidr', 'AZ', 'AZ ID', 'default VPC', 'public IP 자동할당', 'public IP',
    'route table', 'network ACL', 'security group'])
  const vsRows = (n) => attrRows(n, VS_SKIP)   // 🔴 원래 키로 거른다 — 이름표를 거친 뒤(cidr → CIDR)에 거르면 같은 줄이 두 번 나온다
  // 「상태」 줄 — 모든 종류가 이 한 표를 쓴다(VPC·서브넷 #120 → 아이콘·피어링·어태치먼트·VIF·묶음 멤버). AWS 영문 값을 AWS 한국어 콘솔 말로
  //   (docs/qa/copy-a.md ⑩ 「값도 샌다」) · 표에 없는 값은 원문 그대로. VPC 엔드포인트는 첫 글자가 대문자다(`Available`) — 소문자로 한 번 더 찾는다
  const STATE_KO = { available: '사용 가능', pending: '보류 중', unavailable: '사용 불가', failed: '실패', 'failed-insufficient-capacity': '실패(용량 부족)',
    running: '실행 중', stopped: '중지됨', stopping: '중지 중', 'shutting-down': '종료 중', terminated: '종료됨',
    active: '활성', provisioning: '프로비저닝 중', active_impaired: '활성(손상됨)', attached: '연결됨', attaching: '연결 중', detaching: '분리 중', detached: '분리됨',
    associated: '연결됨', modifying: '수정 중', deleting: '삭제 중', deleted: '삭제됨', rejected: '거부됨', expired: '만료됨',
    pendingacceptance: '수락 대기 중', 'pending-acceptance': '수락 대기 중', 'initiating-request': '요청 시작 중',
    'create-in-progress': '생성 중', 'create-complete': '생성 완료', 'create-failed': '생성 실패', 'delete-in-progress': '삭제 중', 'delete-complete': '삭제 완료', 'delete-failed': '삭제 실패' }
  const stateKo = (v) => STATE_KO[v] || STATE_KO[String(v).toLowerCase()] || String(v)
  // 콘솔 표현(§17.11 「콘솔 표현 원칙」 · 사용자 결정 2026-10-24) — 콘솔식 표의 상태 말 = **그 화면의 한국어 콘솔 낱말 그대로**.
  //   한국어 콘솔도 화면마다 다르다(실측 2026-10-24): VPC 라우팅 = 「활성」 · TGW 계열·NAT = 영어 그대로(Available·Associated·Enabled) ·
  //   VPN 터널 = Up/Down(한국어 콘솔 「아래로」는 기계 번역 오류 → 영어 원문). 표에 없는 화면·값은 위 `stateKo`(옛 상태 줄과 같은 말)
  const CON_STATE = {
    tgw: { available: 'Available', associated: 'Associated', associating: 'Associating', disassociating: 'Disassociating', enabled: 'Enabled', enabling: 'Enabling',
      disabled: 'Disabled', disabling: 'Disabling', active: 'Active', blackhole: 'Blackhole', pending: 'Pending', deleting: 'Deleting', deleted: 'Deleted',
      modifying: 'Modifying', rejected: 'Rejected', failed: 'Failed', static: 'Static', propagated: 'Propagated', yes: 'Yes', no: 'No', true: 'Yes', false: 'No' },
    nat: { available: 'Available', pending: 'Pending', failed: 'Failed', deleting: 'Deleting', deleted: 'Deleted', public: 'Public', private: 'Private' },
    rtb: { active: '활성', blackhole: '블랙홀', yes: '예', no: '아니요', true: '예', false: '아니요' },
    tunnel: { up: 'Up', down: 'Down' }
  }
  const conState = (screen, v) => { if (v === null || v === undefined || v === '') return '–'; const t = CON_STATE[screen]; const k = String(v).toLowerCase(); return (t && t[k]) || stateKo(v) }
  // 상태 칸의 색(§17 상태색 — 정상 = 초록 ✓ · 이상 = 빨강) · 낱말은 위 표가 정하고 색은 뜻으로 고른다(같은 「Available」·「사용 가능」·「활성」 = 정상)
  const ST_OK = new Set(['available', 'associated', 'enabled', 'active', 'up', 'healthy', 'attached', 'in-use', '활성', '사용 가능', '연결됨'])
  const ST_BAD = new Set(['blackhole', 'down', 'failed', 'unhealthy', 'rejected', '블랙홀', '실패'])
  const stCls = (v) => { const k = String(v || '').toLowerCase(); return ST_OK.has(k) ? 'ok' : ST_BAD.has(k) ? 'bad' : '' }
  // 태그 값도 정보창 규칙(§17.11 「정보창에 넣지 않는 값」)을 따른다 — 키가 `SECRET` 이거나 값이 ARN 이면 값을 가린다.
  //   🔴 `aws:cloudformation:stack-id` 는 키에 「arn」이 없는데 값이 전체 ARN(계정ID 포함)이다 — 키만 보면 샌다.
  const TAG_HIDE = '(표시하지 않는 값입니다 — ARN·민감 정보)'
  const tagMask = (r) => (SECRET.test(r.a) || /\barn:aws[\w-]*:/i.test(r.b) ? { ...r, b: TAG_HIDE } : r)
  // 태그 칸 — 묶음 머리를 그룹으로 나눠 보여준다(필터의 묶음 순서와 같다 · §17.11)
  const tagSec = (n) => {
    const rs = (X.tagRows ? X.tagRows(n) : []).map(tagMask)
    if (!rs.length) return []
    const gs = []
    for (const r of rs) { const g = gs[gs.length - 1]; if (g && g.h === r.h) g.rows.push(r); else gs.push({ h: r.h, rows: [r] }) }
    return [{ label: '태그', n: rs.length, nc: false, groups: gs, empty: '(없음)' }]
  }
  function attrRows (n, skip) {
    const out = []
    for (const [k, v] of Object.entries((n && n.attrs) || {})) {
      if (SKIP_ATTR.has(k) || (skip && skip.has(k)) || SECRET.test(k) || v === undefined || v === null || v === '') continue
      const l = attrLabel(n, k); if (l === null) continue
      const vk = ATTR_VALUE[l]
      out.push([l, vk && typeof v === 'string' && vk[v] ? vk[v] : fmt(v)])
    }
    return out
  }
  // 🔴 옛 화면은 여기서 **해석 + 조치**를 줬다(「사선만으로는 조치를 말할 수 없으므로 패널에서 조치까지 적는다」).
  //    W5-3 뒤 한 줄로 줄어 조치가 사라졌고, 배너는 여전히 「정보창 「수집」 줄이 이 사실을 말한다」고 가리킨다
  //    — 가리킨 곳에 조치가 없었다(QA 기능개발 7 · A-01 과 같은 뿌리).
  //    조치 문구 = §17.26 C-3 표의 `access_denied` 줄(고객 말 · 「스택 B」 같은 내부 이름 대신 설치 안내의 단계).
  const DENY_WHY = '비어 보이는 것은 「없다」가 아니라 「모른다」입니다 · 읽기 전용 역할(설치 안내 4단계)의 권한을 확인하면 다음 수집부터 표시됩니다'
  // VPC 「일부 못 읽음」 줄 — 🔴 조치(역할 확인)는 **거부일 때만** 붙인다. 스로틀·오류·잘림에 붙이면 거짓 조치다(`flow.js` 해치 주석 · H1).
  //   거부가 아닌 이유는 「!」 상자와 같은 이유 문장(`popReason`)만 적는다. 「권한이 없어」는 쓰지 않는다 — 검사 92 가 그 말을 계정·리전 거부 줄로 센다.
  const DENIED = new Set(['access_denied', 'denied'])
  const vpcPartRow = (v) => {
    const cs = [...new Set((X.T.partial || []).filter((p) => p && p.target === v.id).map((p) => p.code))].sort()
    if (!M.partial.has(v.id)) return []
    const rest = X.popReason ? [...new Set(cs.filter((c) => !DENIED.has(c)).map(X.popReason))].filter((w) => w !== X.popReason('')) : []
    const den = cs.some((c) => DENIED.has(c))
    const head = den ? `이 VPC 의 일부를 권한 부족으로 읽지 못했습니다 — ${DENY_WHY}` : '이 VPC 의 일부를 읽지 못했습니다'
    return [['수집', rest.length ? `${head}${den ? ' · ' : ' — '}${rest.join(' ')}` : head, 'wn']]
  }
  const denyRow = (n) => (X.denyOf && n && X.denyOf(n) ? [['수집', `이 계정·리전의 일부를 권한이 없어 읽지 못했습니다 — ${DENY_WHY}`, 'wn']] : [])
  // 접는 칸 하나(「▸ 라우트 표 N개」). rows: {a,b,tk,cls} | {t}. nc = 엔진이 아직 안 모은다
  const sec = (label, rows, o = {}) => ({ label, n: o.nc ? null : (o.n ?? rows.length), nc: !!o.nc, groups: rows.length ? [{ h: o.h || '', rows }] : [], empty: o.nc ? NC : (o.empty || '(없음)') })
  const ncSec = (label, why) => ({ label, n: null, nc: true, groups: [], empty: why ? `${NC} — ${why}` : NC })
  // 콘솔식 표 하나(§17.11 「콘솔 표현 원칙」) — 칸(`sec`)의 묶음(`groups[]`)이 `tbl`(표 하나) 또는 `tabs`(칸 안의 작은 탭 = 콘솔 탭 순서의 표들)를 갖는다.
  //   cols = [{ h: 열 이름(한국어 콘솔 그대로), w: 폭(px), id: ID 칸(한 줄 · 끊지 않는다), st: 상태 칸(정상 초록 ✓ · 이상 빨강) }]
  //   rows = [{ c: [칸 글자 — cols 순서], tk: 누르면 강조할 열쇠, cls }] · wrap = 첫 줄 열 수 — 560 에 안 들어가는 표는 **두 줄 행**(둘째 줄 = 나머지 열 · 같은 순서)
  //   세 어휘(§0-5): rows 0 = 「(0)」 · nc = 수집 안 함 · ur = 읽지 못함 — 섞지 않는다
  //   🔁 2026-10-26(사용자 결정 「콘솔 방식은 그대로 따른다 · 다 보여 줄 필요는 없다」) — 한 줄(560 정보창 안 `TBL_BUDGET`)에 안 들어가는 표는 **콘솔 열 순서 그대로
  //   핵심 열만** 남긴다: 열 정의의 `x: 1` = 이 정보창에는 싣지 않는 콘솔 열(값은 줄을 눌러 여는 한 단계 아래 정보창 · 같은 정보창의 다른 칸에 있다).
  //   남긴 열의 폭 합(+ 칸 사이 8)이 `TBL_BUDGET` 안이면 두 줄 행을 쓰지 않는다(`o.wrap` 무시) — 넘칠 때만 두 줄 행(옛 길)
  const TBL_BUDGET = 500
  const tbl = (label, cols0, rows0, o = {}) => {
    const keep = cols0.map((c, i) => (c.x ? -1 : i)).filter((i) => i >= 0)
    const cols = keep.map((i) => cols0[i]); const rows = keep.length === cols0.length ? rows0 : rows0.map((r) => ({ ...r, c: keep.map((i) => (r.c || [])[i]) }))
    const fits = cols.reduce((a, c) => a + c.w, 0) + 8 * (cols.length - 1) <= TBL_BUDGET
    return { label, cols, rows, n: o.nc || o.ur ? null : (o.n ?? rows.length), wrap: fits ? cols.length : (o.wrap || cols.length), nc: !!o.nc, ur: !!o.ur,
      empty: o.empty || (o.nc ? NC : o.ur ? UNREAD : '(0)') }
  }
  // 콘솔이 「비어 있음」을 그리는 글자 — 빈 칸은 이 글자(대시)다(「없음」 낱말을 칸마다 쓰지 않는다)
  const NIL = '–'
  // 엔진 표 한 줄(탭 구분 · 2.13.0) → 칸 배열 · 어댑터가 옮긴 콘솔 재료(`n.con` — 키가 없으면 못 읽었거나 모으기 전 판)
  const TSV = (s) => String(s ?? '').split('\t')
  const conOf = (it) => (it && it.n && it.n.con) || null
  // 칸 머리의 「콘솔 ↗」(라우팅 테이블 · 보안 그룹 · 네트워크 ACL = 다른 정보창 안의 칸 · 2026-10-24 실콘솔 확인 · `flow.js` CONSOLE_PATH) — 리전을 모르면 만들지 않는다
  const conLink = (type, id, region) => (X.consoleUrl && id && region ? X.consoleUrl({ type, region, attrs: { id } }) : null)
  const withCu = (g, type, r) => Object.assign(g, { cu: conLink(type, r.id, r.region) })
  const dash = (v) => (v === undefined || v === null || v === '' ? NIL : String(v))
  // 칸 하나 = 표 하나(콘솔 탭 하나가 표 하나뿐인 칸) — 머리 숫자·세 어휘는 그 표의 것
  const tsec = (t, o = {}) => ({ label: t.label, n: t.n, nc: t.nc, ur: t.ur, groups: t.nc || t.ur || !t.rows.length ? [] : [{ h: o.h || '', dl: o.dl, tbl: t }], empty: t.empty })
  // ---- 갈래 ②(2026-10-24 · 검사 167) 콘솔 열 — 이름은 한국어 콘솔 그대로(`~/…/console-cmp/console/*-ko-*` · 비교정리 B·C) ----
  const LB_LSN_COLS = [{ h: '프로토콜:포트', w: 100, id: true }, { h: '기본 작업', w: 270 }, { h: '규칙', w: 50 }, { h: '보안 정책', w: 200, x: 1 }, { h: '기본 SSL/TLS 인증서', w: 250, id: true, x: 1 }]
  const LB_TG_COLS = [{ h: '이름', w: 170, id: true }, { h: '대상 유형', w: 90 }, { h: '프로토콜 : 포트', w: 100, id: true }, { h: '상태 검사', w: 260, x: 1 }, { h: '등록된 대상', w: 110 }]
  const TG_TYPE = { instance: '인스턴스', ip: 'IP 주소', lambda: 'Lambda 함수', alb: 'Application Load Balancer' }
  // 기본 작업(엔진 「forward:<대상 그룹>」·「redirect:…」·「fixed-response:…」) → 콘솔 말(「대상 그룹으로 전달 …」)
  const LB_ACT = { forward: '대상 그룹으로 전달', redirect: 'URL로 리디렉션', 'fixed-response': '고정 응답 반환', 'authenticate-cognito': 'Cognito 인증', 'authenticate-oidc': 'OIDC 인증' }
  const lbAct = (s) => { const t = String(s || ''); if (!t) return NIL; const i = t.indexOf(':'); const k = i < 0 ? t : t.slice(0, i); const v = i < 0 ? '' : t.slice(i + 1); return LB_ACT[k] ? `${LB_ACT[k]}${v ? ' ' + v : ''}` : t }
  // 상태 검사(엔진 대상 그룹 줄 칸 4~12: 프로토콜 · 포트 · 경로 · 정상 임계 · 비정상 임계 · 간격 · 제한 시간 · 성공 코드 · 켜짐)
  const NAT_SEC_COLS = [{ h: '프라이빗 IPv4 주소', w: 140, id: true }, { h: '네트워크 인터페이스 ID', w: 190, id: true }, { h: '퍼블릭 IPv4 주소', w: 140, id: true }]
  const VPCE_SN_COLS = [{ h: '서브넷 ID', w: 240, id: true }, { h: '가용 영역', w: 110 }, { h: 'IPv4 주소', w: 120, id: true }, { h: '네트워크 인터페이스 ID', w: 220, id: true, x: 1 }]
  // 엔진 줄의 서브넷 native id → 이 화면의 서브넷 칸(그 자원이 선 서브넷 먼저 · 없으면 같은 VPC)
  const subnetIdOf = (it, nat) => (it.subnets || []).find((x) => String(x).split('/').pop() === nat) ||
    [...M.subnets.values()].find((x) => x.vpc === it.vpc && String(x.id).split('/').pop() === nat)?.id || nat
  // VIF(콘솔 「가상 인터페이스」 표) — 한 줄 = VIF ID(이름) · VLAN · 상태 · BGP ASN · **누르면 그 VIF 정보창**(`go` · 캔버스 아이콘 없이 · 사용자 결정 2026-10-24)
  //   상태 = 모니터링 켜짐이면 그 VIF 자신의 관측 판정(`vifState` · BGP 수) · 꺼짐이면 구조 수명주기(BGP 안내는 표 머리 한 번 — `VIF_BGP_NOTE`)
  const VIF_COLS = [{ h: '가상 인터페이스 ID', w: 260, id: true }, { h: 'VLAN', w: 60 }, { h: '상태', w: 150, st: true }, { h: 'BGP ASN', w: 90, x: 1 }]
  const VIF_BGP_NOTE = () => (X.obs && X.obs.on() ? '' : 'BGP 상태는 모니터링을 켜면 표시됩니다(구성 정보 수집 시에는 BGP 세션을 확인하지 않습니다)')
  function vifRow (n) {
    const c = n.con || {}; const on = X.obs && X.obs.on(); const v = on ? vifState(n) : { b: (n.attrs || {}).state ? stateKo(n.attrs.state) : NIL }
    const ck = M.hidden.get(n.id)
    return { c: [`${natId(n)}${c.virtual_interface_name ? ` (${c.virtual_interface_name})` : n.name && n.name !== natId(n) ? ` (${n.name})` : ''}`, dash(c.vlan), v.b, dash(c.asn)],
      tk: ck ? [ck] : [], go: n.id, ...(v.cls ? { cls: v.cls } : {}) }
  }
  const DXGA_COLS = [{ h: '게이트웨이 ID', w: 200, id: true }, { h: '리전', w: 90 }, { h: 'AWS 계정', w: 130, x: 1 }, { h: '상태', w: 75, st: true }, { h: '허용 접두사', w: 110, id: true }, { h: '연결 유형', w: 150, x: 1 }]
  const DXGA_TYPE = { transitGateway: 'Transit Gateway', virtualPrivateGateway: '가상 프라이빗 게이트웨이' }
  // 오리진 이름 = 사용자가 지은 이름(CDK 가 만든 `FinOpsDevFrontendDistributionOrigin15140D64C` 처럼 끊을 자리 없는 긴 낱말이 흔하다) → `any` = 칸 폭 안에서 아무 데서나 줄을 바꾼다 · 오리진 도메인(S3 버킷 도메인 = 버킷 이름 + 리전 — 60자 넘게 흔하다)도 같다
  //   (ID 칸처럼 한 줄로 묶으면 칸이 넓어져 옆 칸을 밀어내 가로로 넘쳤다 · 실측 2026-10-26 CloudFront ×7 「오리진」·「동작」)
  const CF_ORG_COLS = [{ h: '오리진 이름', w: 150, any: 1 }, { h: '오리진 도메인', w: 240, any: 1 }, { h: '오리진 경로', w: 100, x: 1 }, { h: '오리진 유형', w: 90 }, { h: '오리진 액세스', w: 260, id: true, x: 1 }]
  const CF_OTYPE = { s3: 'S3', vpc: 'VPC', custom: '사용자 지정 오리진' }
  const CF_BHV_COLS = [{ h: '우선순위', w: 55 }, { h: '경로 패턴', w: 110, id: true }, { h: '오리진', w: 160, any: 1 }, { h: '뷰어 프로토콜 정책', w: 150 }, { h: '캐시 정책', w: 260, x: 1 }]
  const CF_VPP = { 'allow-all': 'HTTP and HTTPS', 'https-only': 'HTTPS only', 'redirect-to-https': 'Redirect HTTP to HTTPS' }
  const WAF_RULE_COLS = [{ h: '이름', w: 260, id: true }, { h: '작업', w: 90 }, { h: '우선순위', w: 70 }]
  const WAF_ACT = { allow: 'Allow', block: 'Block', count: 'Count', captcha: 'CAPTCHA', challenge: 'Challenge', none: 'Use rule actions', override: 'Override' }
  const lbHc = (x) => (x[12] === 'false' ? '꺼짐' : x[4] ? [`${x[4]}${x[6] ? ' ' + x[6] : ''}`, x[9] ? `간격 ${x[9]}초` : '', x[7] && x[8] ? `정상 ${x[7]} · 비정상 ${x[8]}` : '', x[11] ? `성공 코드 ${x[11]}` : ''].filter(Boolean).join(' · ') : NIL)
  // 목적지 정렬(§17.11 「순서」) — 문자열이 아니라 주소 값 순(10.1.4.0/24 < 10.1.10.0/24) · 같으면 접두사가 짧은 쪽 먼저 · IPv6 뒤 · 접두사 목록(pl-) 마지막
  const dstKey = (d) => {
    const m = /^(\d+)\.(\d+)\.(\d+)\.(\d+)(?:\/(\d+))?$/.exec(String(d || ''))
    if (m) return `0${[1, 2, 3, 4].map((i) => String(+m[i]).padStart(3, '0')).join('.')}/${String(m[5] ?? 32).padStart(3, '0')}`
    return /^pl-/.test(String(d || '')) ? `2${d}` : `1${d}`
  }
  const byDst = (x, y) => (dstKey(x) < dstKey(y) ? -1 : dstKey(x) > dstKey(y) ? 1 : 0)
  // 라우트 목적지 접두사 목록(2.10.0 · 배치 D · §17.33) — `pl-…` 를 사람이 읽는 이름으로(AWS 관리형 = 서비스 이름 · 고객 관리형 = 이름 + CIDR 항목).
  //   재료 = 어댑터가 그 서브넷의 라우트 테이블에서 옮긴 `pls`(엔진 속성 그대로). 🔴 못 읽은 것은 그렇게 말한다 — id 만 남기면 「이름 없는 목록」으로 읽힌다
  const PL_SVC = { s3: 'S3', dynamodb: 'DynamoDB', 'ec2-instance-connect': 'EC2 Instance Connect', 'vpc-lattice': 'VPC Lattice', 'route53-healthchecks': 'Route 53 상태 확인',
    'cloudfront.origin-facing': 'CloudFront 오리진 대상' }
  const plSvc = (name) => { const m = /^com\.amazonaws\.(?:global|[a-z]{2}(?:-[a-z]+)+-\d+)\.(ipv6\.)?(.+)$/.exec(String(name || '')); if (!m) return String(name || '') || '?'; return (PL_SVC[m[2]] || m[2]) + (m[1] ? ' (IPv6)' : '') }
  const PL_MAX = 4
  const dstWord = (s, d) => {
    if (!/^pl-/.test(String(d || ''))) return d
    const P = s && s.n && s.n.pls; if (!P) return d
    const x = P.lists[d]
    if (!x) return P.unread ? `${d} (접두사 목록 · 권한이 없어 이름을 읽지 못했습니다)` : `${d} (접두사 목록 · 이 계정에서 볼 수 없는 목록입니다)`
    if (x.aws) return `${plSvc(x.name)} (AWS 관리형 접두사 목록 · ${d})`
    const cs = x.cidrs === null ? '권한이 없어 항목을 읽지 못했습니다' : !x.cidrs.length ? '항목 없음'
      : x.cidrs.length > PL_MAX ? `${x.cidrs.slice(0, PL_MAX).join(', ')} 외 ${x.cidrs.length - PL_MAX}개` : x.cidrs.join(', ')
    return `${x.name || d} (고객 관리형 접두사 목록 · ${d} · ${cs})`
  }
  function routesTo (id) { // 이 게이트웨이로 보내는 라우트(역조회)
    const rows = []
    for (const s of M.subnets.values()) for (const r of s.routes) if (r.t === id) rows.push({ a: dstWord(s, r.d) || '(목적지 미상)', b: subnetName(s.id), tk: [s.id] })
    return rows
  }
  // DX 연결·DX 게이트웨이의 VIF ↔ 게이트웨이 관계는 그 정보창의 「DX 게이트웨이 연결」·「가상 인터페이스」 표가 말한다(검사 167) — 「그림에 없는 연결」에 두 번 적지 않는다
  const DX_REL_SKIP = new Set(['attachment'])
  // 정보창 제목 꼴(콘솔 상세 화면의 머리 · §17.11 「콘솔 표현 원칙」) — `ID / 이름` 종류와 이름만 쓰는 종류(TGW·VGW·IGW 는 위 콘솔 세부 정보 갈래)
  const CON_ID_TITLE = new Set(['vpn', 'cgw', 'nat', 'vpce', 'dxcon', 'dxgw'])
  const CON_NM_TITLE = new Set(['lb', 'waf', 'r53'])
  // 관계 종류(엔진 엣지 종류) → 고객 말 — 🔴 옛 「«attachment» →」처럼 내부 낱말을 그대로 내보이지 않는다(비교정리 B R-12)
  const REL_KO = { attachment: '연결', association: '연결', route: '라우트', origin: '오리진', dnsalias: '별칭', flow: '경로', peering: '피어링', eip_association: '탄력적 IP' }
  function relSec (key, skip) { // 선 없이 이어진 것(정보창에만 있는 관계)
    const rs = (M.rel.get(key) || []).filter((r) => !(skip && skip.has(r.ek))); if (!rs.length) return []
    return [sec('그림에 없는 연결', rs.map((r) => ({ a: r.ek ? `${REL_KO[r.ek] || '관계'} ${r.dir}`.trim() : r.kind, b: r.name, tk: [r.key], ek: r.ek })), { h: '선을 그리지 않고 여기에만 적습니다' })]
  }
  // TGW 어태치먼트 한 줄의 이름·누를 자리(VPC 어태치먼트 = 그 VPC 의 포트) — 「어태치먼트」 칸과 라우트 테이블 칸이 같은 말을 쓴다
  function attLabel (it, n) {
    const p = X.mById.get(n.parent_id); const isV = p && p.type === 'vpc'; const at = n.attrs || {}
    // TGW Connect(§17.38) = 그것이 올라탄 어태치먼트를 말한다(누르면 그 오버레이 선이 있는 포트 · 없으면 이 TGW)
    if (n.connect) { const c = M.cnx.find((x) => x.id === n.id) || {}; return { kind: `Connect(${n.connect.proto ? String(n.connect.proto).toUpperCase() : '?'})`, id: `${natId(n)} → ${n.connect.transport || '?'} 위`, tk: [c.port || it.id] } }
    return { kind: isV ? `VPC · ${disp(p)}` : String(at['종류'] || '어태치먼트'), id: natId(n) + (at['대상'] ? ` → ${at['대상']}` : ''),
      tk: [isV ? (M.portOf.get(p.id + '|' + it.id) || p.id) : it.id] }
  }
  // 이 자원 계정·리전의 그 종류 수집 칸(원문 coverage) — 「없다」와 「못 읽었다」와 「모으기 전 판」을 가른다
  function covOf (n, rtype) {
    const ac = X.mById.get(X.acctOf(n)); const acct = ac && ac.attrs ? ac.attrs.account : null
    return (X.T.coverage || []).find((c) => c.resource_type === rtype && c.account_id === acct && c.region === n.region) || null
  }
  const UNREAD = '읽지 못했습니다 — 권한 부족(「없음」이 아닙니다)'
  // 키가 없는 칸 — 그 자원이 「일부 못 읽음」이면 읽지 못한 것(`ur`), 아니면 이 스냅샷이 그 칸을 모으기 전 판이다(`nc`).
  //   🔴 세 어휘(개수 · 수집 안 함 · 읽지 못함)를 섞지 마라 — 「?개」는 어느 쪽인지 말하지 않는다
  const urSec = (label) => ({ label, n: null, nc: false, ur: true, groups: [], empty: UNREAD })
  const missSec = (label, it) => (M.partial.has(it.id) ? urSec(label)
    // 🔴 `ncSec` 을 쓰면 「이 도구는 수집하지 않습니다 — 이 수집본에는 …」로 **두 이유가 모순**된다(사용자 신고 2026-10-20).
    //    머리는 세 어휘(개수·수집 안 함·읽지 못함) 그대로 두고, 몸통은 이유 하나만 말한다.
    : { label, n: null, nc: true, groups: [], empty: '이 수집본에는 그 기록이 없습니다(수집 전 버전입니다)' })
  // TGW 라우트 테이블 칸(§17.11 · 콘솔 표현 원칙 · 사용자 결정 2026-10-24) — 라우팅 테이블마다 한 묶음 = 콘솔 상세 화면 그대로:
  //   머리 `ID / 이름` · 세부 정보 한 줄(상태 · 기본 연결 라우팅 테이블 · 기본 전파 라우팅 테이블) · 작은 탭 「연결 · 전파 · 접두사 목록 참조 · 경로」(콘솔 탭 순서)
  //   열 이름 = 한국어 콘솔(연결 탭 실측 2026-10-24 — 연결 ID · 연결 이름 · 리소스 유형 · 리소스 ID · 상태). 경로·전파 탭 열 = 영문 콘솔 열을 같은 낱말로(한국어 미확인)
  //   🔴 못 읽은 부분은 그 탭에서 「읽지 못했습니다」라고 말한다 — 탭을 빼면 「연결 없음」으로 읽힌다(어댑터가 못 읽음을 `null` 로 넘긴다)
  //   엔진이 아직 싣지 않는 열(연결·전파의 상태 · 경로의 상태(블랙홀 아닌 것)) = 대시(–) · 접두사 목록 참조 = 수집 안 함
  const TGW_RES = { vpc: 'VPC', vpn: 'VPN', peering: 'Peering', 'tgw-peering': 'Peering', connect: 'Connect', 'direct-connect-gateway': 'Direct Connect Gateway',
    'dx-gateway': 'Direct Connect Gateway', 'network-function': 'Network Function', 'vpn-concentrator': 'VPN Concentrator' }
  const TGW_ASSOC_COLS = [{ h: '연결 ID', w: 214, id: true, x: 1 }, { h: '연결 이름', w: 150 }, { h: '리소스 유형', w: 75 }, { h: '리소스 ID', w: 160, id: true }, { h: '상태', w: 85, st: true }]
  const TGW_PROP_COLS = [{ h: '연결 ID', w: 214, id: true, x: 1 }, { h: '리소스 유형', w: 90 }, { h: '리소스 ID', w: 180, id: true }, { h: '상태', w: 90, st: true }]
  const TGW_ROUTE_COLS = [{ h: 'CIDR', w: 105, id: true }, { h: '연결 ID', w: 214, id: true, x: 1 }, { h: '리소스 ID', w: 150, id: true },
    { h: '리소스 유형', w: 65 }, { h: '경로 유형', w: 75 }, { h: '경로 상태', w: 70, st: true }, { h: '접두사 목록 ID', w: 130, id: true, x: 1 }]
  const TGW_RT_LABEL = 'Transit Gateway 라우팅 테이블'   // 한국어 콘솔 메뉴 이름 그대로(옛 「라우트 테이블」)
  function tgwRtSec (it) {
    const a = it.n.attrs || {}
    const rts = (M.tgwRt.get(it.id) || []).slice().sort((x, y) => (x.id < y.id ? -1 : x.id > y.id ? 1 : 0))
    if (!rts.length) {
      if (Array.isArray(a.routes)) return sec(TGW_RT_LABEL, a.routes.map((r) => ({ a: r.d, b: String(r.t) })), { h: a.route_table ? String(a.route_table) : '' })
      const c = covOf(it.n, 'transit_gateway_route_table')
      return !c ? ncSec(TGW_RT_LABEL, '이 수집본에는 TGW 라우팅 테이블 기록이 없습니다(수집 전 버전입니다)')
        : c.status !== 'ok' ? urSec(TGW_RT_LABEL) : sec(TGW_RT_LABEL, [], { empty: '없음 — 이 TGW 에 라우팅 테이블이 없습니다' })
    }
    const byNatAtt = new Map((M.attOf.get(it.id) || []).map((n) => [natId(n), n]))
    // 어태치먼트 하나 = 콘솔 행의 재료(연결 ID · 연결 이름(Name 태그 — 없으면 대시) · 리소스 유형 · 리소스 ID) + 누를 자리
    const att = (nat) => {
      const n = byNatAtt.get(nat)
      if (!n) return { id: nat, name: NIL, type: NIL, res: NIL, tk: [], miss: true }
      const p = X.mById.get(n.parent_id); const isV = p && p.type === 'vpc'; const at = n.attrs || {}; const t = attLabel(it, n)
      const kind = n.connect ? 'connect' : isV ? 'vpc' : String(at['종류'] || '').toLowerCase()
      return { id: nat, name: n.name && n.name !== nat ? n.name : NIL, type: TGW_RES[kind] || (at['종류'] ? String(at['종류']) : NIL),
        res: isV ? natId(p) : at['대상'] ? String(at['대상']) : NIL, tk: t.tk }
    }
    const ord = (x, y) => (x.type < y.type ? -1 : x.type > y.type ? 1 : 0) || (x.name < y.name ? -1 : x.name > y.name ? 1 : 0) || (x.id < y.id ? -1 : x.id > y.id ? 1 : 0)
    // 2.13.0(검사 166) — 엔진이 행마다 리소스 유형·리소스 ID·상태를 준다(`assocRows`·`propRows`·`routeRows` · 어댑터가 칸으로 쪼갠 것) →
    //   그 값으로 채운다(옛 판 = 위 `att` 가 어태치먼트 노드에서 되짚은 값 · 상태 칸 대시). 리소스 유형 낱말 = 콘솔 표기(VPC · VPN · Peering)
    const rowOf = (rows, nat) => (Array.isArray(rows) ? rows.find((x) => x[0] === nat) : null)
    const resType = (v) => TGW_RES[String(v || '').toLowerCase()] || (v ? String(v) : NIL)
    const PLR_COLS = [{ h: '접두사 목록 ID', w: 150, id: true }, { h: '소유자 ID', w: 120, x: 1 }, { h: '연결 ID', w: 214, id: true, x: 1 },
      { h: '리소스 ID', w: 170, id: true }, { h: '상태', w: 90, st: true }, { h: '블랙홀', w: 60 }]
    const groups = rts.map((rt) => {
      const r = rt.tgwRt || {}; const ra = rt.attrs || {}
      const assoc = r.assoc === null ? tbl('연결', TGW_ASSOC_COLS, [], { ur: true, wrap: 3 })
        : tbl('연결', TGW_ASSOC_COLS, (r.assoc || []).map(att).sort(ord).map((x) => { const e = rowOf(r.assocRows, x.id)
          return { c: [x.id, x.name, e ? resType(e[1]) : x.type, e ? e[2] || NIL : x.res, e ? conState('tgw', e[3]) : NIL], tk: x.tk, ...(x.miss ? { cls: 'wn' } : {}) } }), { wrap: 3 })
      const prop = r.prop === null ? tbl('전파', TGW_PROP_COLS, [], { ur: true, wrap: 3 })
        : tbl('전파', TGW_PROP_COLS, (r.prop || []).map(att).sort(ord).map((x) => { const e = rowOf(r.propRows, x.id)
          return { c: [x.id, e ? resType(e[1]) : x.type, e ? e[2] || NIL : x.res, e ? conState('tgw', e[3]) : NIL], tk: x.tk, ...(x.miss ? { cls: 'wn' } : {}) } }), { wrap: 3 })
      // 접두사 목록 참조 — 엔진 7칸 「<pl-> <소유자> <어태치먼트> <리소스 유형> <리소스 ID> <상태> <블랙홀 yes|no>」 · 못 읽었으면 「읽지 못함」 · 옛 판 = 수집 안 함(세 어휘 §0-5)
      const plr = r.plr === null ? tbl('접두사 목록 참조', PLR_COLS, [], { ur: true, wrap: 3 }) : r.plr === undefined ? tbl('접두사 목록 참조', PLR_COLS, [], { nc: true })
        : tbl('접두사 목록 참조', PLR_COLS, r.plr.map((e) => ({ c: [e[0], e[1] ? X.acctMask(e[1]) : NIL, e[2] || NIL, e[4] || NIL, conState('tgw', e[5]), e[6] === 'yes' ? 'Yes' : e[6] === 'no' ? 'No' : NIL],
          tk: e[2] ? att(e[2]).tk : [] })).sort((x, y) => (x.c[0] < y.c[0] ? -1 : x.c[0] > y.c[0] ? 1 : 0)), { wrap: 3 })
      let routes
      if (r.routes === null) routes = tbl('경로', TGW_ROUTE_COLS, [], { ur: true, wrap: 3 })
      else {
        const rows = []
        for (const x of r.routes || []) {
          const t = att(x.att); const pl = /^pl-/.test(String(x.d))
          const e = Array.isArray(r.routeRows) ? r.routeRows.find((y) => y[0] === x.d && y[1] === x.att) : null
          rows.push({ d: x.d, c: [x.d, t.id, e ? e[3] || NIL : t.res, e ? resType(e[2]) : t.type, x.stat ? 'Static' : 'Propagated', e ? conState('tgw', e[5]) : NIL, pl ? x.d : NIL], tk: t.tk })
        }
        // 블랙홀 = 대상 어태치먼트가 없는 경로(엔진 `blackhole_destinations`) — 경로 상태 칸이 말한다(빨강 · 「이 목적지로 가는 트래픽을 폐기합니다」)
        for (const d of r.bh || []) { const pl = /^pl-/.test(String(d)); rows.push({ d, c: [d, NIL, NIL, NIL, NIL, 'Blackhole', pl ? d : NIL], tk: [], cls: 'wn', why: '이 목적지로 가는 트래픽을 폐기합니다' }) }
        rows.sort((x, y) => byDst(x.d, y.d) || (x.c[1] < y.c[1] ? -1 : x.c[1] > y.c[1] ? 1 : 0))
        routes = tbl('경로', TGW_ROUTE_COLS, rows, { wrap: 3 })
      }
      const dl = [['상태', conState('tgw', r.state)], ['기본 연결 라우팅 테이블', ra['기본 연결 표'] === '예' ? 'Yes' : ra['기본 연결 표'] === '아니요' ? 'No' : NIL],
        ['기본 전파 라우팅 테이블', ra['기본 전파 표'] === '예' ? 'Yes' : ra['기본 전파 표'] === '아니요' ? 'No' : NIL]]
      const nm = rt.name && rt.name !== natId(rt) ? ` / ${rt.name}` : ''
      return { h: `${natId(rt)}${nm}`, dl, rows: [], tabs: [assoc, prop, plr, routes] }
    })
    return { label: TGW_RT_LABEL, n: rts.length, nc: false, groups, empty: '(0)' }
  }
  // 보안 그룹 칸 — 어댑터 `sgIds`(엔진 속성 그대로). 이름은 같은 VPC 의 보안 그룹 표에서 찾는다. 키가 없으면 못 읽은 것이다
  //   콘솔 「인바운드 규칙 / 아웃바운드 규칙」 탭(§17.11 「콘솔 표현 원칙」 · 사용자 결정 2026-10-24 · 검사 167) — 보안 그룹마다 한 묶음:
  //   머리 = `sg-… / 이름` · 세부 정보 = 설명 · 탭 = 인바운드 (N) · 아웃바운드 (N)(N = 콘솔 셈법 = 엔진 줄 수 — 소스 하나가 한 줄).
  //   🔴 규칙을 못 읽었으면 「(0)」이 아니라 「읽지 못함」 · 모으기 전 판(옛 스냅샷)이면 「수집 안 함」(세 어휘 · §0-5)
  function sgSec (it, idsIn) {
    const ids = idsIn || it.n.sgIds
    if (!Array.isArray(ids)) return missSec('보안 그룹', it)
    const L = M.list.get(it.vpc) || []
    const groups = ids.map((g) => { const r = L.find((x) => x.type === 'security_group' && x.id === g); return r ? withCu(ruleGroup(r, it), 'security_group', r) : withCu({ h: `${g} (이 VPC 목록에 없음)`, rows: [] }, 'security_group', { id: g, region: it.region }) })
    return { label: '보안 그룹', n: ids.length, nc: false, groups, empty: '(0)' }
  }
  // 규칙 한 줄 — SG(엔진 「IP 버전 \t 프로토콜 \t 포트 범위 \t 소스 \t 설명」) · NACL(「규칙 번호 \t 프로토콜 \t 포트 \t CIDR \t allow|deny」) → 콘솔 낱말
  //   프로토콜 `-1` = 콘솔 「모두」(포트도 「모두」) · 포트 `22-22` = 「22」 · NACL 규칙 번호 32767(IPv4)·32768(IPv6) = 콘솔 「*」(맨 아래 기본 거부)
  const PROTO_KO = { '-1': '모두', tcp: 'TCP', udp: 'UDP', icmp: 'ICMP', icmpv6: 'ICMPv6', 6: 'TCP', 17: 'UDP', 1: 'ICMP', 58: 'ICMPv6' }
  const protoW = (p) => PROTO_KO[String(p).toLowerCase()] || String(p || NIL)
  const portW = (p, proto) => { if (String(proto) === '-1') return '모두'; const s = String(p || ''); if (!s) return '모두'; const m = /^(-?\d+)-(-?\d+)$/.exec(s); return m && m[1] === m[2] ? m[1] : s }
  const SG_COLS = (w) => [{ h: 'IP 버전', w: 56 }, { h: '프로토콜', w: 62 }, { h: '포트 범위', w: 80, id: true }, { h: w, w: 160, id: true }, { h: '설명', w: 110 }]
  const ACL_COLS = (w) => [{ h: '규칙 번호', w: 60 }, { h: '프로토콜', w: 62 }, { h: '포트 범위', w: 80, id: true }, { h: w, w: 170, id: true }, { h: '허용/거부', w: 72, st: true }]
  const aclNo = (n) => (+n >= 32767 ? '*' : String(n))
  const ACL_ACT = { allow: '허용', deny: '거부' }
  function ruleTbl (label, lines, isAcl, dir) {
    if (lines === undefined) return tbl(label, isAcl ? ACL_COLS(dir) : SG_COLS(dir), [], { nc: true })
    if (lines === null) return tbl(label, isAcl ? ACL_COLS(dir) : SG_COLS(dir), [], { ur: true })
    const rows = lines.map(TSV).map((x) => (isAcl ? { o: +x[0], c: [aclNo(x[0]), protoW(x[1]), portW(x[2], x[1]), x[3] || NIL, ACL_ACT[String(x[4]).toLowerCase()] || x[4]], cls: String(x[4]).toLowerCase() === 'deny' && +x[0] < 32767 ? 'wn' : '' }
      : { o: 0, c: [x[0] || NIL, protoW(x[1]), portW(x[2], x[1]), x[3] || NIL, x[4] || NIL] }))
    // 순서(§17.11 「순서」) — NACL = 규칙 번호 순(「*」 맨 아래 · 콘솔과 같다) · SG = 엔진 순(엔진이 정렬해 싣는다 — 결정론)
    if (isAcl) rows.sort((a, b) => a.o - b.o)
    return tbl(label, isAcl ? ACL_COLS(dir) : SG_COLS(dir), rows.map((r) => ({ c: r.c, ...(r.cls ? { cls: r.cls } : {}) })), { wrap: 4 })
  }
  function ruleGroup (r, it) {
    const isAcl = r.type === 'network_acl'
    const tabs = [ruleTbl('인바운드 규칙', r.rin, isAcl, '소스'), ruleTbl('아웃바운드 규칙', r.rout, isAcl, '대상')]
    // 규칙 키가 아예 없다 = 이 자원을 「일부 못 읽음」으로 받았으면 읽지 못한 것 · 아니면 모으기 전 판
    if (r.rin === undefined && r.rout === undefined && it && M.partial.has(it.id)) for (const t of tabs) { t.nc = false; t.ur = true; t.empty = UNREAD }
    const dl = [...(r.desc ? [['설명', r.desc]] : []), ...(isAcl && r.dflt ? [['기본', '예']] : [])]
    return { h: `${r.id} / ${r.name}`, dl, rows: [], tabs }
  }
  // 서브넷 「네트워크 ACL」 칸(콘솔 서브넷 상세의 「네트워크 ACL」 탭) — 이 서브넷에 연결된 ACL 하나(+ 인바운드·아웃바운드 규칙 탭).
  //   🔴 옛 「규칙은 수집하지 않습니다 — 개수만 수집합니다」(한 문장 안 모순 · 비교정리 B) 대신 규칙을 보인다 · 연결을 모르는 옛 스냅샷 = 수집 안 함
  function naclSec (s) {
    const L = (M.list.get(s.vpc) || []).filter((x) => x.type === 'network_acl')
    if (!L.some((x) => Array.isArray(x.subs))) return ncSec('네트워크 ACL', '이 수집본에는 서브넷 연결 기록이 없습니다(수집 전 버전입니다) — «표»의 네트워크 ACL 에서 확인하세요')
    const mine = L.filter((x) => (x.subs || []).includes(s.id))
    return { label: '네트워크 ACL', n: mine.length, nc: false, groups: mine.map((r) => withCu(ruleGroup(r, null), 'network_acl', r)), empty: '(0)' }
  }
  // LB 대상 그룹 칸(ECS) — 이름만(어댑터가 ARN 을 버린다). 같은 계정·리전 LB 의 대상 그룹 이름과 맞으면 그 LB 를 누를 수 있다
  function tgSec (it, aggKey) {
    const tgs = it.n.targetGroups
    if (!Array.isArray(tgs)) return missSec('LB 대상 그룹', it)
    const key = aggKey || B.cellOf.get(it.id) || it.id
    return sec('LB 대상 그룹', tgs.map((g) => {
      // 2.7.0(§17.27) — 그 대상 그룹으로 이 서비스에 오는 선이 있으면 그 LB(이름이 같은 다른 LB 와 헷갈리지 않는다)
      const ln = B.lines.find((l) => l.kind === 'lbtarget' && l.b === key && l.lbt.tgs.has(g))
      const lb = (ln && [...M.items.values()].find((x) => (B.cellOf.get(x.id) || x.id) === ln.a)) ||
        [...M.items.values()].find((x) => x.kind === 'lb' && x.acct === it.acct && x.region === it.region && String((x.n.attrs || {})['target group'] ?? '').split(', ').includes(g))
      // 2.8.0 — 대상 정체를 싣는 판인데 이리로 오는 선이 없다 = 이 자원으로 온 등록이 없다(원하는 수 0 인 ASG · §17.27 「ASG」).
      //   🔴 「이 LB 의 대상」이라고만 적으면 선이 빠진 것인지 등록이 없는 것인지 갈리지 않는다
      return { a: g, b: lb ? (!ln && M.lbtKnown ? `${lb.name} 의 대상 그룹 — 등록된 대상 0개(선 없음)` : `${lb.name} 의 대상`) : '(연결된 LB 가 이 화면에 없습니다)', tk: lb ? [lb.id] : [] }
    }))
  }
  // LB 「대상」 칸(§17.27) — 선마다 한 줄(누르면 그 대상) + 선이 없는 짝(다른 리전·계정 · 이 화면에 없다) + 못 이은 대상의 까닭(주의색)
  function lbTargetsSec (it) {
    const a = it.n.attrs || {}
    if (a['target group'] === undefined) return missSec('대상', it)
    const key = B.cellOf.get(it.id) || it.id
    const ls = B.lines.filter((l) => l.kind === 'lbtarget' && l.a === key)
    const drawn = new Set(ls.flatMap((l) => l.lbe || []))
    const loose = X.T.edges.filter((e) => e.kind === 'lbtarget' && e.source === it.id && !drawn.has(e.id))
    const unres = it.n.lbUnres || []
    if (!M.lbtKnown && !ls.length && !loose.length && !unres.length) return ncSec('대상', '이 수집본에는 대상 정보가 없습니다(이전 버전에서 수집)')
    const rows = ls.map((l) => ({ a: l.lbt.name, b: `${[...l.lbt.tgs].sort().join(', ')}${l.lbt.ports ? ' · :' + l.lbt.ports : ''} · 등록 ${l.lbt.cnt}`, tk: [l.b] }))
    for (const e of loose) rows.push({ a: nodeName(e.target), b: '그림에 없는 연결 — 다른 리전·계정이거나 이 화면에 없습니다', tk: [] })
    for (const u of unres) rows.push({ a: u.id, b: lbtWhy(u.code), cls: 'wn' })
    return sec('대상', rows)
  }
  // 대상 쪽 칸(§17.27) — 이 자원(묶음이면 묶음)으로 오는 LB → 대상 선마다 그 대상 그룹. 2.7.0 전 스냅샷 = 수집 안 함.
  //   🔴 이름 = 「이 리소스를 대상으로 쓰는 로드밸런서」 · **0개면 칸이 없다**(사용자 결정 2026-10-24 · 검사 167) — 옛 「LB 대상 그룹 0개」는
  //   ALB 정보창의 「대상 그룹 (1)」 바로 아래에서 「대상 그룹이 빠졌다」로 읽혔고, VPC 엔드포인트에는 콘솔에 없는 칸이었다(비교정리 B·C).
  //   배열을 돌려준다(펼쳐 넣는다 · 없으면 칸 0개)
  const LB_IN = '이 리소스를 대상으로 쓰는 로드밸런서'
  function lbInSec (it, key) {
    const k = key || B.cellOf.get(it.id) || it.id
    const ls = B.lines.filter((l) => l.kind === 'lbtarget' && l.b === k)
    // ASG 선으로 접힌 인스턴스(2.8.0) — 제 선은 없지만 LB 의 대상이다. 「접혔다」를 글로 말한다(누르면 그 ASG)
    const fl = it.id ? B.lines.filter((l) => l.kind === 'lbtarget' && l.lbt.fold && l.lbt.fold.has(it.id)) : []
    if (!ls.length && !fl.length) return M.lbtKnown ? [] : [ncSec(LB_IN)]
    return [sec(LB_IN, [...ls.flatMap((l) => [...l.lbt.tgs].sort().map((t) => ({ a: t, b: `${l.lbt.lbName} 의 대상${l.lbt.ports ? ' · :' + l.lbt.ports : ''}`, tk: [l.a] }))),
      ...fl.flatMap((l) => [...l.lbt.tgs].sort().map((t) => ({ a: t, b: `${l.lbt.lbName} 의 대상 — Auto Scaling 그룹 ${l.lbt.name} 의 선에 포함`, tk: [l.b] })))])]
  }
  // GWLB 엔드포인트 「제공자」 칸(§17.28 · 2.8.0) — 이 엔드포인트가 넘기는 곳(선마다 한 줄 · 누르면 제공자) · 못 찾았으면 서비스 이름 + 까닭(주의색)
  //   묶음(`VPC Endpoint ×N`)이면 `agg` = 그 칸 열쇠 · 든 엔드포인트 전부
  function epSec (it, agg, members) {
    const key = agg || B.cellOf.get(it.id) || it.id
    const mine = agg ? new Set(B.cellIds.get(agg) || []) : new Set([it.id])
    const ls = B.lines.filter((l) => l.kind === 'epprov' && l.a === key && [...l.epp.eps].some((id) => mine.has(id)))
    const loose = X.T.edges.filter((e) => e.kind === 'epprov' && mine.has(e.source) && !ls.some((l) => (l.lbe || []).includes(e.id)))
    const rows = [...ls.map((l) => ({ a: l.epp.name, b: l.epp.svcs.map(shortSvc).join(', '), tk: [l.b] })),
      ...loose.map((e) => ({ a: nodeName(e.target), b: '그림에 없는 연결 — 다른 리전·계정이거나 이 화면에 없습니다', tk: [] })),
      // 묶음이면 든 엔드포인트마다(그 엔드포인트의 주인 계정과 함께)
      ...(members || [it]).flatMap((m) => (m.n.epUnres || []).map((s) => ({ a: shortSvc(s), b: '제공자를 이 수집본에서 찾지 못했습니다 — ' + (m.n.epOwner && /^\d{12}$/.test(m.n.epOwner)
        // 주인 계정을 안다(소비자 쪽 · P2-1) — 「어느 계정의 서비스인가」는 말한다(마스킹) · 그 계정의 LB 는 수집하지 않아 모른다
        ? `제공자 계정 ${X.acctText(m.n.epOwner)} — 이 계정을 수집하지 않아 서비스 뒤의 NLB·GWLB 를 알 수 없습니다`
        : '서비스를 제공하는 계정을 수집하지 않았거나 엔드포인트 서비스를 읽지 못했습니다'), cls: 'wn' }))),
      // 묶음이면 든 엔드포인트의 주인 계정(제공자를 찾은 것 포함) — 다른 계정의 서비스로 넘기는 엔드포인트를 글로 본다
      ...(agg ? [] : it.n.epOwner && /^\d{12}$/.test(it.n.epOwner) && ls.length ? [{ a: '제공자 계정', b: X.acctText(it.n.epOwner) }] : [])]
    return sec('제공자', rows)
  }
  // LB 「엔드포인트」 칸(§17.28) — 이 LB 를 뒤에 둔 엔드포인트 서비스로 들어오는 엔드포인트(선마다 한 줄)
  function epInSec (it) {
    const key = B.cellOf.get(it.id) || it.id
    const ls = B.lines.filter((l) => l.kind === 'epprov' && l.b === key)
    const names = (it.n.attrs || {})['엔드포인트 서비스']
    return sec('엔드포인트', ls.map((l) => ({ a: l.epp.from, b: `이 서비스로 전달 — ${l.epp.svcs.map(shortSvc).join(', ')}`, tk: [l.a] })), { h: names ? `엔드포인트 서비스 ${names}` : '' })
  }
  function fwEpSec (it) {
    const key = B.cellOf.get(it.id) || it.id
    const ls = B.lines.filter((l) => l.kind === 'epprov' && l.b === key)
    return sec('엔드포인트', it.n.fwEps.map((eid) => {
      const v = byNatOf(it.n, eid); const l = v && ls.find((x) => x.epp.eps.has(v))
      return l ? { a: eid, b: `이 방화벽으로 전달 — ${l.epp.from}`, tk: [l.a] } : { a: eid, b: v ? '엔드포인트가 이 화면에 있지만 선을 그리지 못했습니다' : '이 엔드포인트가 이 수집본에 없습니다(엔드포인트를 수집하지 않았거나 다른 계정)', cls: 'wn' }
    }))
  }
  // 서브넷 그룹 칸(RDS · ElastiCache) — 어댑터 `groupSubnets`(엔진 후보 목록). 키가 없으면 못 읽었거나 모으기 전 판이다
  function groupSec (it) {
    const g = it.n.groupSubnets; const a = it.n.attrs || {}
    return Array.isArray(g) ? sec('서브넷 그룹', g.map((id) => (M.subnets.has(id) ? { a: M.subnets.get(id).az, b: subnetName(id), tk: [id] } : { a: '', b: `${id} (이 화면에 없음)` })), { h: a['서브넷 그룹'] || '' })
      : missSec('서브넷 그룹', it)
  }
  // 복제 그룹 칸(ElastiCache · §17.32) — 같은 복제 그룹의 다른 캐시 클러스터(누르면 그 카드). 들지 않으면 「없음」 · 멤버를 못 읽었으면 「읽지 못함」
  function cacheRgSec (it) {
    const rg = it.n.cacheRg
    if (!rg) return sec('복제 그룹', [], { empty: '(없음) — 복제 그룹에 속하지 않습니다' })
    if (!Array.isArray(rg.members)) return M.partial.has(it.id) ? urSec('복제 그룹') : ncSec('복제 그룹', '이 수집본에는 복제 그룹 멤버 기록이 없습니다')
    const self = natId(it.n)
    const rows = rg.members.filter((m) => m !== self).map((m) => {
      const o = [...M.items.values()].find((x) => x.kind === 'ec' && x.acct === it.acct && x.region === it.region && natId(x.n) === m)
      return o ? { a: '캐시 클러스터', b: o.name, tk: [o.id] } : { a: '캐시 클러스터', b: `${m} (이 화면에 없음)` }
    })
    return sec('복제 그룹', rows, { h: rg.id, empty: '(없음) — 이 복제 그룹에 다른 클러스터가 없습니다' })
  }
  // 멤버 인스턴스 칸(Aurora · DB 클러스터 · §17.32) — 멤버 RDS 카드를 누르게 한다(묶음 칸이면 묶음). 이 화면에 없으면 id 그대로
  function memberSec (it) {
    const ms = it.n.dbMembers
    if (!Array.isArray(ms)) return missSec('멤버 인스턴스', it)
    return sec('멤버 인스턴스', ms.map((m) => {
      const o = [...M.items.values()].find((x) => x.kind === 'rds' && x.acct === it.acct && x.region === it.region && natId(x.n) === m)
      return o ? { a: 'DB 인스턴스', b: o.name, tk: [B.cellOf.get(o.id) || o.id] } : { a: 'DB 인스턴스', b: `${m} (이 화면에 없음)` }
    }), { empty: '(없음) — 이 클러스터에 인스턴스가 없습니다(엔드포인트로 들어온 요청을 받을 곳이 없습니다)' })
  }
  // ---- #129(2.11.0 · §17.38~40) 정보창 칸 · 「!」 목록 재료(정보창과 같은 셈) ----
  const CWAN_KIND = { connect: 'Connect', site_to_site_vpn: 'Site-to-Site VPN', transit_gateway_route_table: 'Transit Gateway 라우팅 테이블', direct_connect_gateway: 'Direct Connect 게이트웨이' }
  const OV_WHY = { ip_target: 'IP 대상입니다 — 이 도구는 아직 IP 대상의 주인을 찾지 않습니다(선으로 그리지 않습니다)',
    not_collected: '이 수집본에 없는 리소스입니다(삭제되었거나 수집하지 않은 계정입니다)', unknown_type: '이 도구가 모르는 대상 유형입니다' }
  function cwanRows (it) {
    const ls = B.lines.filter((l) => l.kind === 'cwanatt' && l.a === it.id)
    const rows = ls.map((l) => ({ t: `VPC ${l.cwan.name} — 세그먼트 ${l.cwan.segs.join(', ') || '?'} · 엣지 위치 ${l.cwan.locs.map(gaRegion).join(', ') || '?'}(선으로 표시).`, tk: [l.b] }))
    // 엔진은 이었는데 이 화면에 선이 없는 VPC(보기 범위 · 그 VPC 상자가 없다) — 조용히 빼지 않는다
    for (const e of X.T.edges.filter((x) => x.kind === 'cwanatt' && x.source === it.id && !ls.some((l) => (M.ports.get(l.b) || {}).vpc === x.target))) rows.push({ t: `VPC ${nodeName(e.target)} — 지금 화면에 표시되지 않습니다(보기 범위 밖이거나 이 화면에 없는 리소스입니다).`, cls: 'wn' })
    const lost = []
    for (const u of it.n.cwanUnres || []) {
      lost.push(u)
      if (u.code === 'not_in_scope') rows.push({ t: `VPC ${u.ref} — ${gaRegion(u.region)}에 있는 VPC 입니다. 이번 수집에 이 리전이 들어 있지 않아 화면에 그리지 못했습니다. 수집할 리전에 ${eulReul(REGION_KO[u.region] || u.region)} 추가하면 다음 수집부터 이 VPC 까지 선으로 이어집니다.`, cls: 'wn' })
      else rows.push({ t: `VPC ${u.ref} — ${OV_WHY[u.code] || '연결하지 못했습니다'}`, cls: 'wn' })
    }
    for (const o of it.n.cwanOther || []) { lost.push(o); rows.push({ t: `${o.id} — ${CWAN_KIND[o.kind] || o.kind} 연결입니다${o.loc ? `(${gaRegion(o.loc)})` : ''}. 이 도구는 아직 이 연결 종류를 선으로 그리지 않습니다.`, cls: 'wn' }) }
    return { rows, lost: lost.length }
  }
  function cwanSec (it) {
    if (!it.n.cwanRead) return missSec('연결', it)
    return sec('연결', cwanRows(it).rows, { n: (it.n.attrs || {})['연결 수'] ?? undefined, empty: '(없음) — 이 코어 네트워크에 연결이 없습니다' })
  }
  function lsnSecs (it) {
    const ls = B.lines.filter((l) => l.kind === 'lsnatt' && l.b === it.id)
    const vrows = [...ls.map((l) => { const P = M.ports.get(l.a) || {}; const v = M.vpcs.get(P.vpc); return { a: 'VPC', b: v ? v.name : '?', tk: [l.a] } }),
      ...X.T.edges.filter((e) => e.kind === 'lsnvpc' && e.source === it.id && !ls.some((l) => (M.ports.get(l.a) || {}).vpc === e.target)).map((e) => ({ a: 'VPC', b: `${nodeName(e.target)} — 지금 화면에 표시되지 않습니다(보기 범위 밖이거나 이 화면에 없는 리소스입니다)`, cls: 'wn' })),
      ...(it.n.lsnUnres || []).map((u) => ({ a: u.ref, b: OV_WHY[u.code] || '연결하지 못했습니다', cls: 'wn' }))]
    const srows = [...(it.n.lsnSvcs || []).map((id) => (M.hubs.has(id) ? { a: '서비스', b: M.hubs.get(id).name, tk: [id] } : { a: '서비스', b: `${nodeName(id)} (이 화면에 없음)` })),
      ...(it.n.lsnSvcUnres || []).map((u) => ({ a: u.ref, b: '이 수집본에 없는 서비스입니다(다른 계정이 공유한 서비스일 수 있습니다)', cls: 'wn' }))]
    return [it.n.lsnRead ? sec('연결된 VPC', vrows, { empty: '(없음) — 이 서비스 네트워크에 연결된 VPC 가 없습니다' }) : missSec('연결된 VPC', it),
      it.n.lsnSvcRead ? sec('서비스', srows, { empty: '(없음) — 이 서비스 네트워크에 연결된 서비스가 없습니다' }) : missSec('서비스', it)]
  }
  function lsvcSecs (it) {
    const ls = B.lines.filter((l) => l.kind === 'lsvctgt' && l.a === it.id)
    const TW = { instance: 'EC2 인스턴스', alb: '로드 밸런서', lambda: 'Lambda 함수' }
    const rows = [...ls.map((l) => ({ a: l.ltg.tgs.join(', ') || '대상 그룹', b: `${TW[l.ltg.type] || '대상'} ${l.ltg.name}${l.ltg.ports ? ' · :' + l.ltg.ports : ''} 로 전달`, tk: [l.b] })),
      ...X.T.edges.filter((e) => e.kind === 'lsvctgt' && e.source === it.id && !ls.some((l) => l.b === (B.cellOf.get(e.target) || e.target))).map((e) => ({ a: ((e.attrs || {}).target_groups || []).join(', ') || '대상 그룹', b: `${nodeName(e.target)} — 지금 화면에 표시되지 않습니다(보기 범위 밖이거나 이 화면에 없는 리소스입니다)`, cls: 'wn' })),
      ...(it.n.lsvcUnres || []).map((u) => ({ a: u.ref, b: OV_WHY[u.code] || '연결하지 못했습니다', cls: 'wn' })),
      ...(it.n.lsvcEmpty || []).map((g) => ({ a: g, b: '이 대상 그룹에 등록된 대상이 없습니다(이 그룹으로 온 요청은 처리되지 않습니다)', cls: 'wn' }))]
    const nets = (it.n.lsvcNets || []).map((id) => (M.hubs.has(id) ? { a: '서비스 네트워크', b: M.hubs.get(id).name, tk: [id] } : { a: '서비스 네트워크', b: `${nodeName(id)} (이 화면에 없음)` }))
    return [it.n.lsvcRead ? sec('대상', rows, { empty: '(없음) — 이 서비스에 대상 그룹이 없습니다' }) : missSec('대상', it), sec('서비스 네트워크', nets, { empty: '(없음) — 어느 서비스 네트워크에도 연결되지 않았습니다' })]
  }
  // ======================================================================== 콘솔식 라우팅 쪽 정보창
  //   §17.11 「콘솔 표현 원칙」 · 사용자 결정 2026-10-24(고객은 AWS 콘솔에 익숙하다 — 콘솔의 정보 표현을 따른다) · 검사 166.
  //   VPC · 서브넷 · 라우팅 테이블 · TGW · TGW 어태치먼트 · VPC 피어링 · VGW · IGW — 칸 = 콘솔 탭 · 열 = 한국어 콘솔 열 이름·순서 · 값 = `ID (이름)`(예외 E1)
  const idNm = (id, nm) => (id ? (nm && nm !== id ? `${id} (${nm})` : String(id)) : NIL)
  const nmIx = new Map()   // 계정|native id → 캔버스 노드 id(그림 이름표와 같은 이름을 붙이는 끈 · 예외 E1)
  const natNode = (ctx, v) => {
    if (!v) return null
    const k = X.acctOf(ctx) + '|' + v
    if (!nmIx.has(k)) nmIx.set(k, byNatOf(ctx, v))
    return nmIx.get(k)
  }
  const natNm = (ctx, v) => { const id = natNode(ctx, v); const n = id && X.mById.get(id); return n && named(n) ? n.name : null }
  const yesNo = (v) => (v === true ? '예' : v === false ? '아니요' : NIL)
  // 엔진 옵션 값(enable/disable) — TGW·어태치먼트 콘솔은 영어 원문 그대로 첫 글자만 대문자(영문 콘솔 실측 2026-10-24 `05-TGW상세` ·
  //   `04-TGW어태치먼트상세`: 「Enable」·「Disable」 · 암호화 지원만 「Disabled」 = API 값이 `disabled`) · 한국어 콘솔 미확인(§0-6 「한국어 미확인은 영문 콘솔 값」)
  const enDis = (v) => (v === undefined || v === null || v === '' ? NIL : String(v).charAt(0).toUpperCase() + String(v).slice(1))
  // VPC 라우팅 테이블 「라우팅」 표(한국어 콘솔 실측 2026-10-24 `01-라우팅테이블-ko-1-Routes` — 대상 · 대상 · 상태 · 전파됨 · 라우팅 오리진).
  //   같은 낱말 「대상」 둘은 괄호로 가른다(예외 E2). 5열이 한 줄에 안 들어간다(대상 장비 = `ID (이름)`) → 두 줄 행(첫 줄 = 알아보는 열)
  const RTB_COLS = [{ h: '대상(Destination)', w: 130, id: true }, { h: '대상(Target)', w: 215, id: true }, { h: '상태', w: 65, st: true }, { h: '전파됨', w: 55 }, { h: '라우팅 오리진', w: 130, x: 1 }]
  const RT_ORIG = { CreateRouteTable: '라우팅 테이블 생성', CreateRoute: '라우팅 생성' }   // 한국어 콘솔 낱말 · 전파(EnableVgwRoutePropagation)는 한국어 미확인 → 원문
  // 접두사 목록 풀이(§17.33 · 검사 152) — 표의 대상(Destination) 칸은 콘솔처럼 `pl-…` id 만(통합정리 0-3) · 이름(AWS 관리형 = 서비스 · 고객 관리형 = 이름 + CIDR)은
  //   표 아래 풀이 줄로(id → 이름 · 못 읽었으면 그 말). 재료 = 어댑터가 그 서브넷의 라우팅 테이블에서 옮긴 `pls`(`dstWord`)
  const plRows = (s, ds) => [...new Set(ds.filter((d) => /^pl-/.test(String(d || ''))))].sort().map((d) => ({ a: d, b: dstWord(s, d), cls: 'pln' }))
  function rtbRoutes (r, ctx) {
    const rt = r.rt; const rows = []
    for (const x of rt.routes) {
      const pl = /^pl-/.test(x.d); const tid = x.t === 'local' ? null : natNode(ctx, x.t)
      // 못 이은 대상 ENI(2.8.0 · P2-2 · 엔진 `unresolved_route_targets`) = 그 ENI + 까닭(주의색) — 「이 화면에 없는 대상」 한 말로 뭉개지 않는다
      const u = (rt.unres || []).find((y) => y.t === x.t && (!y.d || y.d === x.d))
      rows.push({ d: x.d, loc: x.t === 'local', c: [x.d || NIL, x.t === 'local' ? 'local' : u ? `${x.t} — ${routeWhy(u.why)}` : idNm(x.t, natNm(ctx, x.t)), x.st ? conState('rtb', x.st) : NIL,
        rt.old ? NIL : x.orig === 'EnableVgwRoutePropagation' ? '예' : '아니요', x.orig ? RT_ORIG[x.orig] || x.orig : NIL],
      tk: tid ? [B.cellOf.get(tid) || tid] : [], ...(x.st === 'blackhole' ? { cls: 'bh', why: '이 목적지로 가는 트래픽을 폐기합니다' } : u ? { cls: 'wn' } : {}), ...(pl ? { pl: true } : {}) })
    }
    // 옛 판(엔진 `routes` 없음)은 못 이은 ENI 를 엣지로 모르므로 여기서 줄을 더한다(옛 서브넷 표와 같은 줄)
    if (rt.old) for (const u of rt.unres || []) if (!rows.some((y) => y.d === u.d && String(y.c[1]).startsWith(u.t + ' '))) rows.push({ d: u.d, loc: false, c: [u.d || NIL, `${u.t} — ${routeWhy(u.why)}`, NIL, NIL, NIL], tk: [], cls: 'wn' })
    // 순서(§17.11 · 통합정리 0-4) = local 먼저 → 목적지 주소 오름차순 → 접두사 목록(pl-) 마지막
    rows.sort((x, y) => (x.loc === y.loc ? 0 : x.loc ? -1 : 1) || byDst(x.d, y.d) || (x.c[1] < y.c[1] ? -1 : x.c[1] > y.c[1] ? 1 : 0))
    return tbl('라우팅', RTB_COLS, rows, { wrap: 2 })
  }
  // 라우팅 테이블 한 장 = 콘솔 상세 화면(머리 `ID / 이름` · 세부 정보 · 탭 「라우팅 · 서브넷 연결 · 엣지 연결 · 라우팅 전파」 — 태그·관련 리소스는 뺀다)
  function rtbGroup (r, v) {
    const rt = r.rt; const ctx = v ? v.n : null
    const L = v ? (M.list.get(v.id) || []) : []
    const explicit = new Set(L.filter((x) => x.rt).flatMap((x) => x.rt.assoc))
    const sn = (sid) => { const s = M.subnets.get(sid); return s ? idNm(natId(s.n), s.named ? s.name : null) : String(sid).split('/').pop() }
    const cidrOf = (sid) => { const s = M.subnets.get(sid); return (s && (s.n.attrs || {}).cidr) || NIL }
    const azOf = (sid) => (M.subnets.get(sid) || {}).az || NIL
    const subRows = rt.assoc.map((sid) => ({ az: azOf(sid), c: [sn(sid), cidrOf(sid), '명시적'], tk: M.subnets.has(sid) ? [sid] : [] }))
    if (rt.main && v) for (const s of v.subnets) if (!explicit.has(s.id)) subRows.push({ az: s.az, c: [sn(s.id), cidrOf(s.id), '명시적 연결 없음(기본 테이블)'], tk: [s.id] })
    subRows.sort((x, y) => (x.az < y.az ? -1 : x.az > y.az ? 1 : 0) || (x.c[0] < y.c[0] ? -1 : x.c[0] > y.c[0] ? 1 : 0))
    const SUB_COLS = [{ h: '서브넷 ID', w: 220, id: true }, { h: 'IPv4 CIDR', w: 120, id: true }, { h: '연결', w: 140 }]
    const EDGE_COLS = [{ h: '게이트웨이 ID', w: 300, id: true }, { h: '유형', w: 160 }]
    const PROP_COLS = [{ h: '가상 프라이빗 게이트웨이', w: 340, id: true }, { h: '전파', w: 80 }]
    const gwType = (g) => (/^igw-/.test(g) ? '인터넷 게이트웨이' : /^vgw-/.test(g) ? '가상 프라이빗 게이트웨이' : NIL)
    const edge = rt.edge === null ? tbl('엣지 연결', EDGE_COLS, [], { nc: true })
      : tbl('엣지 연결', EDGE_COLS, rt.edge.slice().sort().map((g) => { const id = ctx && natNode(ctx, g); return { c: [idNm(g, ctx && natNm(ctx, g)), gwType(g)], tk: id ? [id] : [] } }))
    // 라우팅 전파 = 이 VPC 에 붙은 VGW 마다 전파 예/아니요(콘솔 탭과 같다) — 엔진은 전파하는 VGW id 만 준다(`propagating_vgw_ids`)
    const vgws = v ? [...M.hubs.values()].filter((h) => h.kind === 'vgw' && (h.n.vpcIds || []).includes(v.id)) : []
    const prop = rt.vgw === null ? tbl('라우팅 전파', PROP_COLS, [], { nc: true })
      : tbl('라우팅 전파', PROP_COLS, [...new Set([...vgws.map((h) => natId(h.n)), ...rt.vgw])].sort().map((g) => { const h = vgws.find((x) => natId(x.n) === g)
        return { c: [idNm(g, h && h.named ? h.name : null), rt.vgw.includes(g) ? '예' : '아니요'], tk: h ? [h.id] : [] } }))
    const dl = [['기본', rt.main ? '예' : '아니요'], ['명시적 서브넷 연결', `${rt.assoc.length} 서브넷`], ['엣지 연결', rt.edge && rt.edge.length ? rt.edge.join(', ') : NIL],
      ...(rt.old ? [['수집본', '옛 판 — VPC 내부(local) 경로·상태·라우팅 오리진은 다음 수집부터 보입니다']] : [])]
    const ps = v ? v.subnets.find((x) => x.n.rtId === r.id) : null
    return { h: `${r.id}${r.name && r.name !== r.id ? ` / ${r.name}` : ''}`, dl, rows: plRows(ps, rt.routes.map((x) => x.d)), tabs: [rtbRoutes(r, ctx), tbl('서브넷 연결', SUB_COLS, subRows, { wrap: 2 }), edge, prop] }
  }
  // VPC 의 라우팅 테이블 칸(콘솔 VPC › 라우팅 테이블 · 통합정리 2-1·2-3) — 테이블마다 한 묶음(기본 테이블 먼저 → 이름 → ID)
  function rtbSec (v, cnt) {
    const rs = (M.list.get(v.id) || []).filter((x) => x.type === 'route_table' && x.rt)
      .sort((x, y) => (x.rt.main === y.rt.main ? 0 : x.rt.main ? -1 : 1) || String(x.name).localeCompare(String(y.name)) || (x.id < y.id ? -1 : 1))
    if (!rs.length) return sec('라우팅 테이블', [], cnt === undefined ? {} : { n: cnt, empty: '개수만 확인했습니다 — 목록은 수집하지 않습니다' })
    return { label: '라우팅 테이블', n: rs.length, nc: false, groups: rs.map((r) => withCu(rtbGroup(r, v), 'route_table', r)), empty: '(0)' }
  }
  // 서브넷의 「라우팅 테이블」 칸(콘솔 서브넷 › 라우팅 테이블 탭 · 통합정리 2-2) — 머리 「라우팅 테이블: ID / 이름」 + 그 테이블의 「라우팅 (N)」.
  //   🔴 옛 제목 「라우팅 테이블 2개」는 경로 수였다(R-01) — 칸 수 = 테이블 1 · 경로 수는 표 탭 「라우팅 (N)」이 말한다
  function subRtSec (s) {
    const v = M.vpcs.get(s.vpc); const r = s.n.rtId ? (M.list.get(s.vpc) || []).find((x) => x.type === 'route_table' && x.id === s.n.rtId && x.rt) : null
    if (!r) return null
    const dl = [['기본', r.rt.main ? '예' : '아니요'], ...(s.n.rtImplicit ? [['연결', '명시적 연결 없음 — VPC 의 기본 라우팅 테이블을 따릅니다']] : []),
      ...(r.rt.old ? [['수집본', '옛 판 — VPC 내부(local) 경로·상태·라우팅 오리진은 다음 수집부터 보입니다']] : [])]
    return { label: '라우팅 테이블', n: 1, nc: false, groups: [withCu({ h: `라우팅 테이블: ${r.id}${r.name && r.name !== r.id ? ` / ${r.name}` : ''}`, dl, rows: plRows(s, r.rt.routes.map((x) => x.d)), tabs: [rtbRoutes(r, v ? v.n : s.n)] }, 'route_table', r)], empty: '(0)' }
  }
  // 「이 게이트웨이(피어링)로 라우트를 보내는 서브넷」(예외 E4 — 콘솔에 없는 거꾸로 계산한 칸 · 콘솔과 같은 칸 아래) = 표 「목적지 · 서브넷 ID · 가용 영역」
  const GWR_COLS = [{ h: '목적지', w: 120, id: true }, { h: '서브넷 ID', w: 270, id: true }, { h: '가용 영역', w: 80 }]
  function gwRouteSec (label, id) {
    const rows = []
    const pr = []
    for (const s of M.subnets.values()) for (const r of s.routes) if (r.t === id) { rows.push({ d: r.d, c: [r.d || NIL, idNm(natId(s.n), s.named ? s.name : null), s.az], tk: [s.id] }); pr.push(...plRows(s, [r.d])) }
    rows.sort((x, y) => byDst(x.d, y.d) || (x.c[1] < y.c[1] ? -1 : x.c[1] > y.c[1] ? 1 : 0))
    const pu = [...new Map(pr.map((x) => [x.a, x])).values()].sort((x, y) => (x.a < y.a ? -1 : 1))
    return { label, n: rows.length, nc: false, groups: rows.length ? [{ h: '', rows: pu, tbl: tbl(label, GWR_COLS, rows) }] : [], empty: '(0)' }
  }
  // TGW 「어태치먼트 (N)」 칸(콘솔 Transit Gateway Attachment 목록 · 한국어 콘솔 열 실측 2026-10-24 `03-TGW어태치먼트목록-ko`) — 🔴 옛 「연결 4개」(R-02 · 예외 E3).
  //   Transit gateway ID 열은 뺀다(이 정보창이 그 TGW) · NAT 상태 열도 뺀다(Disabled 만 나온다) · 두 줄 행(첫 줄 = Name · ID · 상태)
  //   🔁 2026-10-26(사용자 결정) — 이 정보창에는 Name · 상태 · 리소스 유형 · 리소스 ID 네 열(한 줄) · 어태치먼트 ID 는 줄을 눌러 여는 어태치먼트 정보창 제목에 ·
  //   연결 라우팅 테이블·연결 상태는 아래 「Transit Gateway 라우팅 테이블」 칸의 「연결」 탭에 있다
  const ATT_COLS = [{ h: 'Name', w: 155 }, { h: 'Transit Gateway Attachment ID', w: 220, id: true, x: 1 }, { h: '상태', w: 85, st: true },
    { h: '리소스 유형', w: 75 }, { h: '리소스 ID', w: 155, id: true }, { h: '연결 라우팅 테이블 ID', w: 170, id: true, x: 1 }, { h: '연결 상태', w: 90, st: true, x: 1 }]
  function tgwAttSec (it) {
    const atts = M.attOf.get(it.id) || []
    const rows = atts.map((n) => {
      const p = X.mById.get(n.parent_id); const isV = p && p.type === 'vpc'; const at = n.attrs || {}; const c = n.con || {}; const t = attLabel(it, n)
      const kind = n.connect ? 'connect' : isV ? 'vpc' : String(c.resType || at['종류'] || '').toLowerCase()
      return { type: TGW_RES[kind] || (kind ? kind : NIL), name: named(n) ? n.name : NIL, id: natId(n),
        c: [named(n) ? n.name : NIL, natId(n), conState('tgw', at.state), TGW_RES[kind] || kind || NIL, c.resId || (isV ? natId(p) : at['대상'] || NIL),
          c.assocRt || NIL, c.assocState ? conState('tgw', c.assocState) : NIL], tk: t.tk, go: (t.tk || [])[0] }
    })
    rows.sort((x, y) => (x.type < y.type ? -1 : x.type > y.type ? 1 : 0) || (x.name < y.name ? -1 : x.name > y.name ? 1 : 0) || (x.id < y.id ? -1 : x.id > y.id ? 1 : 0))
    return { label: '어태치먼트', n: rows.length, nc: false, groups: rows.length ? [{ h: '', rows: [], tbl: tbl('어태치먼트', ATT_COLS, rows, { wrap: 3 }) }] : [], empty: '(0)' }
  }
  // TGW 「세부 정보」(콘솔 순서 · 통합정리 2-4) — 엔진 옵션 키가 없으면(옛 판·못 읽음) 그 줄이 없다
  function tgwRows (it) {
    const c = it.n.con || {}; const a = it.n.attrs || {}
    // 「상태」 줄은 모든 정보창이 같은 고객 말(`stateKo` · copy-a ⑩ · 검사 129) — 콘솔식 표 안의 상태 칸만 콘솔 낱말(`conState`)
    const rows = [['Transit Gateway ID', natId(it.n)], ['상태', it.state ? stateKo(it.state) : NIL], ['Amazon ASN', a['Amazon 측 ASN'] ?? NIL]]
    const opt = [['DNS 지원', c.dns], ['기본 연결 라우팅 테이블', c.defAssoc], ['연결 라우팅 테이블 ID', c.assocRt, true], ['공유 어태치먼트 자동 수락', c.autoAccept],
      ['기본 전파 라우팅 테이블', c.defProp], ['전파 라우팅 테이블 ID', c.propRt, true], ['VPN ECMP 지원', c.vpnEcmp], ['보안 그룹 참조 지원', c.sgRef], ['멀티캐스트 지원', c.multicast],
      ['암호화 지원', c.encryption]]
    for (const [k, v, raw] of opt) if (v !== undefined) rows.push([k, raw ? String(v) : enDis(v)])
    if (Array.isArray(c.cidrs)) rows.push(['Transit Gateway CIDR 블록', c.cidrs.length ? c.cidrs.join(', ') : NIL])
    if (c.desc) rows.push(['설명', String(c.desc)])
    rows.push(['소유자 ID', acctName(it.acct)])
    return rows
  }
  function itemSecs (it) {
    const k = it.kind; const a = it.n.attrs || {}
    if (k === 'tgw') {
      const secs = []
      if (!it.ghost) {
        // 콘솔 탭 순서와 같은 무게 — 어태치먼트 먼저(이 TGW 에 무엇이 붙었나) → 라우팅 테이블(어떻게 보내나)
        secs.push(tgwAttSec(it))
        secs.push(tgwRtSec(it))
      }
      return secs
    }
    if (k === 'lb') {
      const ports = a['리스너 포트']; const c = conOf(it) || {}
      // 콘솔 「리스너 및 규칙」 표(2.13.0 · 검사 167) — 프로토콜:포트 · 기본 작업 · 규칙 · 보안 정책 · 기본 SSL/TLS 인증서 · 포트 순.
      //   옛 스냅샷(재료 없음) = 옛 줄(포트만) 그대로
      const lsn = Array.isArray(c.listener_details)
        ? tsec(tbl('리스너 및 규칙', LB_LSN_COLS, c.listener_details.map(TSV).sort((x, y) => (+x[1] - +y[1]) || (x[0] < y[0] ? -1 : x[0] > y[0] ? 1 : 0))
          .map((x) => ({ c: [x[0] || x[1] ? `${x[0] || '?'}:${x[1] || '?'}` : NIL, lbAct(x[2]), dash(x[3]), dash(x[4]), dash(String(x[5] || '').split(',').filter(Boolean)[0])] })), { wrap: 3 }))
        : ports ? sec('리스너 및 규칙', String(ports).split(/[,\s]+/).filter(Boolean).map((p) => ({ a: p, b: '' })), { n: a.listeners })
          : a.listeners !== undefined ? sec('리스너 및 규칙', [], { n: a.listeners, empty: `포트·규칙은 ${NC}` }) : missSec('리스너 및 규칙', it)
      // 콘솔 「대상 그룹」 — 이름 · 대상 유형 · 프로토콜:포트 · 상태 검사 · 등록된 대상(구조 수) · 이름 순. 대상 상태(정상·비정상)는 관측 몫이다(모니터링 켜짐 = 「대상」 줄)
      const tgNote = X.obs && X.obs.on() ? '' : '대상 상태(정상·비정상)는 모니터링을 켜면 표시됩니다'
      const tgs = Array.isArray(c.target_group_details)
        ? tsec(tbl('대상 그룹', LB_TG_COLS, c.target_group_details.map(TSV).sort((x, y) => (x[0] < y[0] ? -1 : x[0] > y[0] ? 1 : 0))
          .map((x) => ({ c: [x[0], TG_TYPE[x[3]] || dash(x[3]), x[1] ? `${x[1]}${x[2] ? ':' + x[2] : ''}` : NIL, lbHc(x), tgRegWord(it, x[0]) || NIL] })), { wrap: 3 }), { h: tgNote })
        : a['target group'] !== undefined ? sec('대상 그룹', a['target group'] ? String(a['target group']).split(', ').map((g) => ({ a: g, b: tgRegWord(it, g) })) : [], { h: tgNote })
          : missSec('대상 그룹', it)
      return [lsn, tgs, lbTargetsSec(it), sgSec(it), ...lbInSec(it),
      // GWLB·NLB 를 뒤에 둔 엔드포인트 서비스(2.8.0 · §17.28) — 이리로 넘기는 엔드포인트. 엔진이 묻지 않는 종류(ALB)·옛 스냅샷은 칸이 없다
      ...((it.n.attrs || {})['엔드포인트 서비스'] !== undefined ? [epInSec(it)] : [])]
    }
    // NAT(콘솔 「보조 IPv4 주소」 탭 · 2.13.0) — 기본(primary)이 아닌 주소만(기본 주소는 세부 정보 줄) · 재료가 없는 옛 스냅샷 = 칸 없음
    if (k === 'nat') {
      const ad = (conOf(it) || {}).addresses
      const sub = Array.isArray(ad) ? [tsec(tbl('보조 IPv4 주소', NAT_SEC_COLS, ad.map(TSV).filter((x) => x[4] !== 'true').map((x) => ({ c: [dash(x[1]), dash(x[2]), dash(x[0])] }))))] : []
      return [...sub, gwRouteSec('이 게이트웨이로 보내는 라우트', it.id)]   // 예외 E4 — IGW 와 같은 표(마무리 통합)
    }
    if (k === 'igw') return [gwRouteSec('이 게이트웨이로 보내는 라우트', it.id)]   // 예외 E4(콘솔에 없는 칸 · 표) — 검사 166
    const snRows = () => it.subnets.map((s) => ({ a: M.subnets.get(s).az, b: subnetName(s), tk: [s] }))
    // 🔴 EC2 의 LB 대상 그룹 소속은 엔진이 모으지 않는다(대상 쪽 API 가 따로다) — ECS 는 서비스가 자기 대상 그룹을 말한다
    // 🔁 2.7.0(§17.27) — 이 인스턴스로 오는 LB → 대상 선이 말한다(그 전 스냅샷 = 수집 안 함)
    if (k === 'ec2') return [sgSec(it), ...lbInSec(it, it.aggKey), ...tagSec(it.n)]
    // 리전 줄에 선 것(§17.12 ①)은 「서브넷 (없음)」 대신 왜 서브넷이 없는지를 말한다
    const snSec = () => sec('서브넷', snRows(), it.regional ? { empty: NOSUB_WHY[it.n.type] } : {})
    if (k === 'ecs') return [sgSec(it), tgSec(it, it.aggKey), snSec()]
    if (k === 'rds') return [sgSec(it), groupSec(it), ...lbInSec(it, it.aggKey)]
    if (k === 'sm') return it.n.type === 'sagemaker_notebook_instance' ? [sgSec(it), ...lbInSec(it)] : [sgSec(it), snSec(), ...lbInSec(it)]
    if (k === 'ec') return [sgSec(it), groupSec(it), cacheRgSec(it), ...lbInSec(it)]
    if (k === 'rdsc') return [sgSec(it), groupSec(it), memberSec(it), ...lbInSec(it)]
    if (k === 'os' || k === 'msk') return [sgSec(it), snSec(), ...lbInSec(it)]
    if (k === 'eic') return [sgSec(it), ...lbInSec(it)]
    if (k === 'eks') return [sgSec(it), sec('클러스터 서브넷', snRows()), ...lbInSec(it)]
    // VPC 엔드포인트(콘솔 「서브넷」·「보안 그룹」 탭 · 2.13.0) — 인터페이스·GWLB 엔드포인트만(게이트웨이 엔드포인트는 서브넷·SG 가 없다 = 엔진이 키를 안 만든다).
    //   ENI·IP 를 못 읽었으면 엔진이 키를 지우고 자원을 partial 로 둔다 → 「읽지 못함」(「(0)」이 아니다)
    if (k === 'vpce') {
      const c = conOf(it) || {}; const iface = it.n.attrs && it.n.attrs.type !== 'Gateway'
      const sn = Array.isArray(c.subnet_addresses) ? [tsec(tbl('서브넷', VPCE_SN_COLS, c.subnet_addresses.map(TSV).sort((x, y) => (x[0] < y[0] ? -1 : x[0] > y[0] ? 1 : 0))
        .map((x) => ({ c: [M.subnets.has(subnetIdOf(it, x[0])) && M.subnets.get(subnetIdOf(it, x[0])).named ? `${x[0]} (${M.subnets.get(subnetIdOf(it, x[0])).name})` : x[0], dash(x[1]), dash(x[2]), dash(x[3])], tk: M.subnets.has(subnetIdOf(it, x[0])) ? [subnetIdOf(it, x[0])] : [] })), { wrap: 2 }))]
        : iface && conOf(it) && M.partial.has(it.id) ? [urSec('서브넷')] : []
      const sg = Array.isArray(c.security_groups) ? [sgSec(it, c.security_groups.map((g) => TSV(g)[0]))] : []
      return [...(it.n && Array.isArray(it.n.epUnres) ? [epSec(it)] : []), ...sn, ...sg, ...lbInSec(it, it.aggKey)]
    }
    if (k === 'brk') return [...lbInSec(it)]
    // VPC Lambda(2.8.0 · P2-6) = 「보안 그룹」·「서브넷」 칸도 · 리전 줄 Lambda 는 2.7.0 그대로(LB 대상 그룹만)
    if (k === 'lambda') return it.vpc ? [sgSec(it), sec('서브넷', snRows()), ...lbInSec(it)] : [...lbInSec(it)]
    // ASG(2.8.0) — 「LB 대상 그룹」 칸 = 이 그룹이 든 대상 그룹(이리로 접힌 선이 있으면 그 LB · 등록이 없으면 그렇게 말한다)
    if (k === 'asg') return [sec('서브넷', snRows()), tgSec(it, it.aggKey)]
    // 방화벽(2.8.0 · §17.28) — 「엔드포인트」 칸 = 제 엔드포인트마다 그 칸 · 이리로 오는 넘김 선(못 그렸으면 그 까닭)
    if (k === 'nfw') return [sec('서브넷', snRows()), ...(Array.isArray(it.n.fwEps) ? [fwEpSec(it)] : [])]
    // DX 연결(콘솔 「가상 인터페이스」 탭 · 검사 167) — VIF 마다 한 줄(누르면 그 VIF 정보창 · 캔버스 아이콘은 없다) + 이 연결의 VIF 가 붙은 DX 게이트웨이.
    //   🔴 옛 「그림에 없는 연결 «attachment» → DX Gateway ×5」(내부 낱말 · 묶음 아이콘을 가리킴 · 비교정리 B) 대신 이 표가 그 관계를 말한다(누르면 그 게이트웨이)
    if (k === 'dxcon') {
      const vifs = (M.vifOf.get(it.id) || []).slice().sort((x, y) => (natId(x) < natId(y) ? -1 : 1))
      const vt = vifs.length || !a['virtual interface'] ? tsec(tbl('가상 인터페이스', VIF_COLS, vifs.map(vifRow)), { h: VIF_BGP_NOTE() })
        : sec('가상 인터페이스', [{ a: '', b: String(a['virtual interface']) }])
      const gw = new Map()
      for (const n of vifs) for (const e of X.T.edges.filter((x) => x.kind === 'attachment' && x.source === n.id)) { const g = X.mById.get(e.target); if (g && g.type === 'direct_connect_gateway') push(gw, g.id, natId(n)) }
      const gt = tsec(tbl('DX 게이트웨이 연결', [{ h: 'DX 게이트웨이 ID', w: 290, id: true }, { h: '거쳐 가는 가상 인터페이스', w: 200, id: true }],
        [...gw.entries()].sort((x, y) => (x[0] < y[0] ? -1 : 1)).map(([g, vs]) => { const gn = X.mById.get(g); const ck = B.cellOf.get(g) || g
          return { c: [`${natId(gn)}${gn.name && gn.name !== natId(gn) ? ` (${gn.name})` : ''}`, vs.join(', ')], tk: [ck], go: B.det.has(g) || B.mem.has(g) ? g : undefined } })))
      return [vt, gt]
    }
    if (k === 'dxgw') {
      const cs = M.dxtgw.filter((p) => p.dxgw === it.id)
      const vg = it.n.vgwIds
      // VIF ↔ DX 게이트웨이 = 엔진 `attachment` 선(VIF → DXGW). VIF 는 DX 연결 아이콘 안에 들어가 있다 → 누르면 그 연결이 밝아진다
      const vifs = X.T.edges.filter((e) => e.kind === 'attachment' && e.target === it.id).map((e) => X.mById.get(e.source)).filter((n) => n && n.type === 'direct_connect_virtual_interface')
      // VGW 는 2.6.0 부터 노드다 — 이 화면에 있으면 이름으로 쓰고 누르면 밝아진다(선은 없다 · 엔진에 그 엣지가 없다 · §17.1)
      const vgwOf = (g) => [...M.hubs.values()].find((h) => h.kind === 'vgw' && natId(h.n) === g)
      // 콘솔 「게이트웨이 연결」 탭(2.13.0 · 검사 167) — 게이트웨이 ID(이름) · 리전 · 계정 / 상태 · 허용 접두사 · 연결 유형(두 줄 행) · 종류 → 이름 → ID 순.
      //   재료가 없는 옛 스냅샷 = 옛 「연결된 게이트웨이」 줄 그대로
      const ga = (conOf(it) || {}).gateway_associations
      const gwName = (gid) => { const t = [...M.hubs.values()].find((h) => natId(h.n) === gid); return t ? { w: `${gid} (${t.name})`, tk: [t.id] } : { w: gid, tk: [] } }
      const gaSec = Array.isArray(ga)
        ? tsec(tbl('게이트웨이 연결', DXGA_COLS, ga.map(TSV).map((x) => ({ x, g: gwName(x[1]) })).sort((p, q) => (p.x[2] < q.x[2] ? -1 : p.x[2] > q.x[2] ? 1 : 0) || (p.g.w < q.g.w ? -1 : 1))
          .map(({ x, g }) => ({ c: [g.w, dash(x[3]), x[4] && /^\d{12}$/.test(x[4]) ? X.acctMask(x[4]) : dash(x[4]), conState('tgw', x[5]), dash(String(x[6] || '').split(',').filter(Boolean).join(', ')), DXGA_TYPE[x[2]] || dash(x[2])], tk: g.tk })), { wrap: 3 }))
        : sec('게이트웨이 연결', [...cs.map((p) => ({ a: 'TGW', b: hubName(p.tgw), tk: [p.tgw] })), ...(vg || []).map((g) => { const h = vgwOf(g); return h ? { a: 'VGW', b: h.name, tk: [h.id] } : { a: 'VGW', b: `${g} (이 화면에 없음)` } })],
          { empty: Array.isArray(vg) ? '(없음)' : '(없음) — VGW 연결은 이 수집본에 기록이 없습니다' })
      return [gaSec, tsec(tbl('가상 인터페이스', VIF_COLS, vifs.slice().sort((x, y) => (natId(x) < natId(y) ? -1 : 1)).map(vifRow)), { h: VIF_BGP_NOTE() })]
    }
    if (k === 'vgw') {
      // 콘솔 VGW 상세(통합정리 2-11) — 붙은 VPC 는 「세부 정보」의 VPC 줄(옛 「연결된 VPC」 칸) · 칸 = 예외 E6(VPN 연결 · DX 게이트웨이) + E4(보내는 라우트) · 모두 표
      const one = (cols, label, rows) => ({ label, n: rows.length, nc: false, groups: rows.length ? [{ h: '', rows: [], tbl: tbl(label, cols, rows) }] : [], empty: '(0)' })
      const vpn = M.vpnl.filter((l) => l.b === it.id).map((l) => { const h = M.hubs.get(l.a); return { c: [h ? idNm(natId(h.n), h.named ? h.name : null) : String(l.a).split('/').pop(), h ? stateKo(h.state) : NIL], tk: [l.a] } })
        .sort((x, y) => (x.c[0] < y.c[0] ? -1 : 1))
      const dx = [...M.globs.values()].filter((g) => g.kind === 'dxgw' && !g.ghost && (g.n.vgwIds || []).includes(natId(it.n)))
        .map((g) => ({ c: [idNm(natId(g.n), g.named ? g.name : null), g.state ? stateKo(g.state) : NIL], tk: [g.id] })).sort((x, y) => (x.c[0] < y.c[0] ? -1 : 1))
      return [one([{ h: 'VPN ID', w: 380, id: true }, { h: '상태', w: 90, st: true }], 'VPN 연결', vpn),
        one([{ h: 'DX 게이트웨이 ID', w: 390, id: true }, { h: '상태', w: 90, st: true }], 'DX 게이트웨이', dx), gwRouteSec('이 게이트웨이로 보내는 라우트', it.id)]
    }
    if (k === 'cgw') return [sec('VPN 연결', vpnOf(it.id))]
    // CloudFront(§17.31) — 「원본」 칸 = 원본 엣지마다 한 줄(오리진 유형 → 자원 · 누르면 그 자원 · S3 버킷도) · 원본 엣지가 없으면 칸 없음
    if (k === 'cf') {
      const og = X.T.edges.filter((e) => e.kind === 'origin' && e.source === it.id)
      const c = conOf(it) || {}
      // 콘솔 「오리진」·「동작」 탭(2.13.0 · 검사 167 · 콘솔 상세는 `cloudfront:GetDistribution` 이 없어 직접 대조하지 못했다 — 열 이름은 AWS 문서 기준)
      //   오리진 줄을 누르면 그 원본 자원(원본 엣지의 끝 — 도메인이 그 LB 의 DNS 이름·그 버킷의 이름으로 시작하면)
      if (Array.isArray(c.origins)) {
        //   원본 엣지 ↔ 오리진 줄 = 도메인으로 잇는다(LB DNS 이름 · 버킷 이름 · 별칭 · 그 자원 속성 값 중 같은 글자) · 오리진 유형이 비면 그 엣지의 `via`(엔진 판정)로
        const edgeOf = (dom) => { const d = String(dom || '').toLowerCase().replace(/\.+$/, '')
          return og.find((x) => { const t = X.mById.get(x.target) || {}; const dn = String((t.con || {}).dns_name || '').toLowerCase(); const bk = String((t.attrs || {})['버킷'] || '').toLowerCase()
            const al = String((x.attrs || {}).alias || '').toLowerCase()
            return (dn && (d === dn || d === 'dualstack.' + dn)) || (bk && d.startsWith(bk + '.')) || (al && d === al) || Object.values(t.attrs || {}).some((v) => typeof v === 'string' && v.toLowerCase() === d) }) || null }
        const VIA_T = { s3: 's3', vpc_origin: 'vpc', dns_alias: 'custom' }
        const ors = c.origins.map(TSV).sort((x, y) => (x[0] < y[0] ? -1 : x[0] > y[0] ? 1 : 0)).map((x) => ({ x, e: edgeOf(x[1]) }))
        // VPC 원본(EC2 등 — 도메인이 그 자원 속성에 없다)은 남은 `vpc_origin` 엣지와 짝 수가 같을 때만 차례로 잇는다(못 맞추면 누를 수 없는 줄로 둔다 — 지어내지 않는다)
        const usedE = new Set(ors.filter((o) => o.e).map((o) => o.e.id))
        const restE = og.filter((x) => !usedE.has(x.id) && (x.attrs || {}).via === 'vpc_origin').sort((p, q) => (p.target < q.target ? -1 : 1))
        const restO = ors.filter((o) => !o.e && o.x[2] === 'vpc')
        if (restE.length && restE.length === restO.length) restO.forEach((o, i) => { o.e = restE[i] })
        const ot = tbl('오리진', CF_ORG_COLS, ors.map(({ x, e }) => { const ty = x[2] || (e ? VIA_T[(e.attrs || {}).via] : '')
          return { c: [dash(x[0]), dash(x[1]), dash(x[3]), CF_OTYPE[ty] || dash(ty), x[4] ? `${x[4]}${x[5] ? ` (${x[5]})` : ''}` : NIL], tk: e ? [B.cellOf.get(e.target) || e.target] : [] } }), { wrap: 2 })
        const bt = Array.isArray(c.behaviors) ? tbl('동작', CF_BHV_COLS, c.behaviors.map(TSV).sort((x, y) => +x[0] - +y[0])
          .map((x) => ({ c: [dash(x[0]), x[1] ? x[1] : '기본값 (*)', dash(x[2]), CF_VPP[x[3]] || dash(x[3]), x[5] ? x[5] : dash(x[4])] })), { wrap: 3 }) : null
        return [tsec(ot), ...(bt ? [tsec(bt)] : M.partial.has(it.id) ? [urSec('동작')] : [])]
      }
      return og.length ? [sec('오리진', og.map((e) => { const w = cfVia((e.attrs || {}).via, (e.attrs || {}).alias); const t = X.mById.get(e.target)
        return { a: w || ((t && t.type) === 'ec2_instance' ? 'EC2' : '로드밸런서'), b: nodeName(e.target), tk: [B.cellOf.get(e.target) || e.target] } }))] : []
    }
    // S3 버킷(§17.31) — 이 버킷을 원본으로 쓰는 배포(같은 전역 줄 · 선이 아니다)
    if (k === 's3') return [sec('이 버킷을 사용하는 배포', X.T.edges.filter((e) => e.kind === 'origin' && e.target === it.id).map((e) => ({ a: 'CloudFront', b: nodeName(e.source), tk: [e.source] })))]
    if (k === 'r53') {
      // 별칭(§17.30) — 별칭 엣지마다 한 줄(레코드 이름 → 대상 · 누르면 대상) · 못 이은 별칭 = 주의색(수집하지 않은 LB·CloudFront)
      const al = X.T.edges.filter((e) => e.kind === 'dnsalias' && e.source === it.id)
      const unres = Array.isArray(it.n.zoneUnres) ? [...it.n.zoneUnres].sort() : []
      const rows = [...al.map((e) => ({ a: ((e.attrs || {}).record_names || []).join(', ') || '?', b: nodeName(e.target), tk: [B.cellOf.get(e.target) || e.target] })),
        ...unres.map((d) => ({ a: d, b: '이 수집본에 없는 로드밸런서·CloudFront — 수집 범위 밖이거나 수집하지 않았습니다', cls: 'wn' }))]
      const vs = Array.isArray(it.n.zoneVpcs) ? it.n.zoneVpcs.map((id) => { const v = [...M.vpcs.values()].find((x) => natId(x.n) === id); return v ? { a: 'VPC', b: v.name, tk: [v.id] } : { a: 'VPC', b: `${id} (이 화면에 없음)` } }) : null
      return [sec('별칭', rows), ...(a['유형'] === '프라이빗' ? [vs ? sec('연결된 VPC', vs) : missSec('연결된 VPC', it)] : [])]
    }
    // 배치 D(2.10.0 · §17.35~37) — 선마다 한 줄(누르면 그 끝) + 못 이은 것(까닭 · 주의색)
    if (k === 'apigw') {
      const ls = B.lines.filter((l) => l.kind === 'vpclink' && l.a === it.id)
      const loose = X.T.edges.filter((e) => e.kind === 'vpclink' && e.source === it.id && !ls.some((l) => l.b === (B.cellOf.get(e.target) || e.target)))
      const rows = [...ls.map((l) => ({ a: (l.vlk.names.length ? l.vlk.names : l.vlk.ids).join(', '), b: `${l.vlk.lbName} 로 전달`, tk: [l.b] })),
        ...loose.map((e) => ({ a: ((e.attrs || {}).vpc_link_names || []).join(', ') || ((e.attrs || {}).vpc_link_ids || []).join(', '), b: `${nodeName(e.target)} — 그림에 없는 연결(다른 리전이거나 이 화면에 없습니다)`, tk: [] })),
        ...(it.n.apiUnres || []).map((u) => ({ a: u.ref, b: bdWhy(u), cls: 'wn' }))]
      return [it.n.apiRead ? sec('VPC 링크', rows) : missSec('VPC 링크', it)]
    }
    // WAF 웹 ACL(2.12.0 · §17.41) — 「보호 대상」 칸 = 선마다 한 줄(누르면 그 대상) + 이 화면에 없는 대상 + 이 수집본에 없는 대상(까닭 · 주의색)
    if (k === 'waf') {
      const ls = B.lines.filter((l) => l.kind === 'wafp' && l.a === it.id)
      const what = (id) => ({ cloudfront: 'CloudFront 배포', load_balancer: '로드밸런서', api_gateway: 'API Gateway' })[(X.mById.get(id) || {}).type] || '리소스'
      const loose = X.T.edges.filter((e) => e.kind === 'wafp' && e.source === it.id && !ls.some((l) => l.b === (B.cellOf.get(e.target) || e.target)))
      const stOf = (id) => { const e = X.T.edges.find((x) => x.kind === 'wafp' && x.source === it.id && x.target === id); const s = e && (e.attrs || {}).stages; return Array.isArray(s) && s.length ? ` · 스테이지 ${s.join(', ')}` : '' }
      const rows = [...ls.map((l) => ({ a: what(l.b), b: `${nodeName(l.b)}${stOf(l.b)}`, tk: [l.b] })),
        ...loose.map((e) => ({ a: what(e.target), b: `${nodeName(e.target)}${stOf(e.target)} — 그림에 없는 연결(다른 리전이거나 이 화면에 없습니다)`, tk: [] })),
        ...(it.n.wafUnres || []).map((u) => ({ a: u.ref, b: bdWhy(u), cls: 'wn' }))]
      // 콘솔 「규칙」 탭(2.13.0 · 검사 167) — 이름 · 작업 · 우선순위 · 우선순위 순(콘솔 Rules 표 · 「사용자 지정 응답」은 엔진이 싣지 않는다 → 열을 두지 않는다)
      const rl = (conOf(it) || {}).rules
      const rt = Array.isArray(rl) ? [tsec(tbl('규칙', WAF_RULE_COLS, rl.map(TSV).sort((x, y) => +x[0] - +y[0]).map((x) => ({ c: [dash(x[1]), WAF_ACT[String(x[2]).toLowerCase()] || dash(x[2]), dash(x[0])] }))))]
        : it.n.wafRead === undefined && M.partial.has(it.id) ? [urSec('규칙')] : []
      return [...rt, rows.length ? sec(WAF_RES, rows) : M.partial.has(it.id) ? urSec(WAF_RES) : sec(WAF_RES, [], { empty: WAF_NONE })]
    }
    if (k === 'rdsp') {
      const ls = B.lines.filter((l) => l.kind === 'dbproxy' && l.a === (B.cellOf.get(it.id) || it.id))
      const rows = [...ls.map((l) => ({ a: l.prx.kind === 'cluster' ? 'DB 클러스터' : 'DB 인스턴스', b: `${l.prx.name}${l.prx.ports ? ' · :' + l.prx.ports : ''}`, tk: [l.b] })),
        ...(it.n.proxyMembers || []).map((m) => ({ a: '클러스터 멤버', b: m })),
        ...(it.n.proxyUnres || []).map((u) => ({ a: u.ref, b: bdWhy(u), cls: 'wn' }))]
      return [sgSec(it), snSec(), it.n.proxyRead ? sec('대상', rows) : missSec('대상', it)]
    }
    // Global Accelerator(§17.37 · 🔁 2026-10-21 사용자 요청 「쉬운 말로」) — 엔드포인트 그룹(리전)마다 한 줄 · 맨 위 한 줄 요약.
    //   줄은 리전 이름으로 시작한다(지도의 리전 이름 표 `REGION_KO` 그대로) · 그린 곳 = 보통 줄 · 못 그린 곳(이번 수집에 없는 리전 · 대상 못 찾음 · 대상 없음) = 주의색
    if (k === 'ga') {
      if (!it.n.gaRead) return [missSec('엔드포인트', it)]
      const ls = B.lines.filter((l) => l.kind === 'gaep' && l.a === it.id)
      const tg = it.n.gaTargets || []; const un = it.n.gaUnres || []; const em = it.n.gaEmpty || []
      const regs = [...new Set([...tg.map((t) => t.region), ...em])].sort()
      const rows = []; let drawnR = 0; let emptyR = 0
      for (const r of regs) {
        const R = gaRegion(r)
        const drawn = ls.filter((l) => l.gae.region === r).map((l) => ({ t: `${l.gae.what} ${l.gae.name}${l.gae.eip ? `(탄력적 IP ${l.gae.eip})` : ''}`, k: l.b })).sort((x, y) => (x.t < y.t ? -1 : x.t > y.t ? 1 : 0))
        const out = un.filter((u) => u.code === 'not_in_scope' && u.region === r)
        const miss = un.filter((u) => u.code !== 'not_in_scope' && (u.region ? u.region === r : tg.some((t) => t.ref === u.ref && t.region === r)))
        if (drawn.length) {
          drawnR++
          rows.push({ t: drawn.length === 1 ? `${R} — ${drawn[0].t} 로 트래픽을 보냅니다(선으로 표시).` : `${R} — 대상 ${drawn.length}개(${drawn.map((x) => x.t).join(' · ')})로 트래픽을 보냅니다(선으로 표시).`, tk: drawn.map((x) => x.k) })
        }
        if (out.length) {
          const kinds = {}; for (const u of out) { const t = tg.find((x) => x.ref === u.ref && x.region === r); const w = t && GA_KIND[t.kind]; if (w) kinds[w] = (kinds[w] || 0) + 1 }
          const kw = Object.entries(kinds).sort((x, y) => (x[0] < y[0] ? -1 : 1)).map(([w, n]) => `${w} ${n}개`).join(' · ')
          rows.push({ t: `${R} — 이 리전에 대상 ${out.length}개${kw ? `(${kw})` : ''}가 있지만, 이번 수집에 이 리전이 들어 있지 않아 화면에 그리지 못했습니다. 수집할 리전에 ${eulReul(REGION_KO[r] || r)} 추가하면 다음 수집부터 선으로 이어집니다.`, cls: 'wn' })
        }
        if (miss.length) rows.push({ t: `${R} — 대상 ${miss.length}개를 찾지 못했습니다(삭제되었거나 아직 그리지 않는 종류입니다).`, cls: 'wn' })
        // 엔진은 이었지만 이 화면에 선이 없는 대상(보기 범위로 숨겼다 · 대상 칸이 이 화면에 없다) — 조용히 빼지 않는다
        const loose = X.T.edges.filter((e) => e.kind === 'gaep' && e.source === it.id && (e.attrs || {}).endpoint_region === r && !ls.some((l) => l.b === (B.cellOf.get(e.target) || e.target)))
        if (loose.length) rows.push({ t: `${R} — 대상 ${loose.length}개는 지금 화면에 표시되지 않습니다(보기 범위 밖이거나 이 화면에 없는 리소스입니다).`, cls: 'wn' })
        const isEmpty = em.includes(r) && !drawn.length && !out.length && !miss.length
        if (isEmpty) emptyR++
        if (isEmpty) rows.push({ t: `${R} — 이 리전으로 보낼 대상이 등록되어 있지 않습니다. 이 리전으로 들어온 트래픽은 처리되지 않습니다.`, cls: 'wn' })
      }
      // 요약(🔁 사용자 결정 2026-10-21 QA C) — 대상 없는 그룹은 「그리지 못한 곳」이 아니다(고객이 일부러 비워 둔다) · 0 인 항목은 빼되 「화면에 그린 곳」은 늘
      const lostR = regs.length - drawnR - emptyR
      rows.unshift({ t: `이 Global Accelerator는 ${regs.length}개 리전으로 트래픽을 나눕니다 — ` + [`화면에 그린 곳 ${drawnR}`, lostR ? `그리지 못한 곳 ${lostR}` : '', emptyR ? `대상 없음 ${emptyR}` : ''].filter(Boolean).join(' · ') })
      return [sec('엔드포인트', rows, { n: tg.length })]   // 머리 숫자 = 등록된 엔드포인트 수(요약·리전 줄 수가 아니다)
    }
    // #129(2.11.0 · §17.39) — Cloud WAN 「연결」 = 선마다 한 줄(누르면 그 VPC 포트) + 그리지 못한 것(주의색 · 까닭을 고객 말로)
    if (k === 'cwan') return [cwanSec(it)]
    // §17.40 — 서비스 네트워크 「연결된 VPC」·「서비스」 · 서비스 「대상」·「서비스 네트워크」
    if (k === 'lsn') return lsnSecs(it)
    if (k === 'lsvc') return lsvcSecs(it)
    if (k === 'vpn') {
      const tun = tunnelSec(it)
      const c = it.n.vpn || {}; const ls = M.vpnl.filter((l) => l.a === it.id).map((l) => M.hubs.get(l.b)).filter(Boolean)
      const raw = (kd, id, label) => (id && !ls.some((h) => h.kind === kd) ? [{ a: label, b: `${id} (이 화면에 없음)` }] : [])
      return [tun, sec('고객 게이트웨이', [...ls.filter((h) => h.kind === 'cgw').map((h) => ({ a: 'CGW', b: h.name, tk: [h.id] })), ...raw('cgw', c.cgw, 'CGW')]),
        sec('대상 게이트웨이', [...ls.filter((h) => h.kind !== 'cgw').map((h) => ({ a: h.kind === 'tgw' ? 'TGW' : 'VGW', b: h.name, tk: [h.id] })), ...raw('vgw', c.vgw, 'VGW'), ...raw('tgw', c.tgw, 'TGW')]),
        // 정적 라우트 = 라우팅이 정적일 때만(콘솔 — 동적(BGP)이면 탭이 비어 있다 · 비교정리 B M-12)
        ...(c.routes && !c.routes.length && (conOf(it) || {}).static_routes_only === false ? [] : [Array.isArray(c.routes) ? sec('정적 라우트', c.routes.slice().sort(byDst).map((d) => ({ a: d, b: '' }))) : missSec('정적 라우트', it)])]
    }
    return []
  }
  // VIF 줄의 오른쪽(§17.11 DX 행) — 모니터링 켜짐이면 **그 VIF 자신의** 관측 판정 + 사유, 꺼짐이면 구조의 수명주기 상태.
  //   🔴 DX 연결 고리는 가장 나쁜 VIF 를 따른다(flow.js `obsOf`) — 줄마다 말하지 않으면 「어느 VIF 가 이상인가」가 화면 어디에도 없다.
  //   판정·문구는 flow.js(`obs.of`·`obs.word`)가 정한다 — 여기는 자리와 경고색만
  //   BGP(2026-10-15): 켜짐이면 관측 행의 BGP 피어 수를 이름으로 말한다(「BGP up 2/2」 · 「BGP 일부 down 1/2」 — 글은 flow.js `obs.bgp`) ·
  //   꺼짐이면 구조 상태 + 「BGP 는 켜면 보인다」 — 구조에는 BGP 세션이 없다(`direct_connect_virtual_interface.py` 머리말).
  const vifState = (n) => {
    const st = [(n.attrs || {}).state].filter(Boolean).map(stateKo).join(' · ')
    const o = X.obs && X.obs.of ? X.obs.of(n.id) : null
    if (o === null) return { b: [st, X.obs && X.obs.on() ? '표시할 상태 정보가 없습니다' : 'BGP 상태는 모니터링을 켜면 표시됩니다(구성 정보 수집 시에는 BGP 세션을 확인하지 않습니다)'].filter(Boolean).join(' · ') }
    if (!o) return { b: [st, '관측 행 없음'].filter(Boolean).join(' · ') }
    const g = X.obs.bgp ? X.obs.bgp(o) : null
    if (g) return { b: g.t, cls: o.status === 'down' ? 'bh' : g.cls || (o.partial ? 'wn' : '') }
    return { b: X.obs.word(o.status, o) + (o.why ? ' — ' + o.why : ''), cls: o.status === 'down' ? 'bh' : o.deleting === true ? '' : o.status === 'none' || o.partial ? 'wn' : '' }
  }
  // VPN 「터널 세부 정보」(콘솔 터널 상태 표 · 사용자 결정 2026-10-24 · 검사 167) — 두 줄 행: 터널 번호 · 외부 IP · 내부 IPv4 CIDR / 상태 · 마지막 상태 변경 · 세부 정보.
  //   상태 = 🔴 **모니터링을 꺼도 보인다**: 꺼짐 = 배포 빌드가 구조 수집 직후 1층 observe 로 구운 「수집 시각 기준」(`window.NT_SNAPOBS` · ADR 0006 O13) ·
  //   켜짐 = 실시간 관측 행의 `tunnels`(5분 > 매시 — 덮는다). 구운 것이 없으면(로컬 파일·옛 빌드) 상태 칸은 대시 + 안내 · 굽다 실패(ok:false) = 「확인하지 못했습니다」(「터널 없음」이 아니다 · 불변식 ④)
  //   번호 = 바깥 IP 순(엔진이 그 순서로 싣는다 · 콘솔 「터널 1·2」와 같은 순서인지는 실측 필요 — 비교정리 B)
  const TUN_COLS = [{ h: '터널 번호', w: 60 }, { h: '외부 IP 주소', w: 125, id: true }, { h: '내부 IPv4 CIDR', w: 160, id: true, x: 1 }, { h: '상태', w: 75, st: true }, { h: '마지막 상태 변경', w: 190 }, { h: '세부 정보', w: 260, x: 1 }]
  const kstHm = (iso, full) => { const t = Date.parse(iso || ''); if (!Number.isFinite(t)) return null; const s = new Date(t + 9 * 3600e3).toISOString(); return full ? s.slice(0, 16).replace('T', ' ') + ' KST' : s.slice(11, 16) }
  function tunnelSec (it) {
    const c = conOf(it) || {}; const ins = Array.isArray(c.tunnel_inside_cidrs) ? c.tunnel_inside_cidrs.map(TSV) : null
    const outs = ins ? ins.map((x) => x[0]) : Array.isArray(c.tunnel_outside_ips) ? [...c.tunnel_outside_ips] : null
    if (!outs) return M.partial.has(it.id) ? urSec('터널 세부 정보') : ncSec('터널 세부 정보', '이 수집본에는 터널 기록이 없습니다(수집 전 버전입니다)')
    const on = X.obs && X.obs.on(); const o = on && X.obs.of ? X.obs.of(it.id) : null
    const so = typeof window !== 'undefined' ? window.NT_SNAPOBS : undefined
    let st = null; let note = ''; let cls = ''
    if (on) { if (o && Array.isArray(o.tunnels)) { st = o.tunnels; const at = X.obs.at ? X.obs.at() : null; note = at ? `${at} 관측` : '' } else { note = '이 VPN 의 터널 상태 관측이 없습니다'; cls = 'wn' } }
    else if (so && so.ok === true) { st = (so.tunnels || {})[it.id] || []; const hm = kstHm(so.at); note = `수집 시각 기준${hm ? ' ' + hm : ''} — 실시간 상태는 모니터링을 켜면 5분마다 갱신됩니다` }
    else if (so && so.ok === false) { note = `터널 상태를 확인하지 못했습니다${so.why ? ' — ' + so.why : ''}`; cls = 'wn' }
    else note = '터널 상태는 모니터링을 켜면 표시됩니다'
    const byIp = new Map((st || []).map((t) => [t.outside_ip, t]))
    const rows = outs.map((ip, i) => { const t = byIp.get(ip); const sv = t ? conState('tunnel', t.status) : NIL
      return { c: [String(i + 1), dash(ip), ins ? dash(ins[i][1]) : NIL, sv, t ? (kstHm(t.last_status_change, true) || NIL) : NIL, t ? dash(t.status_message) : NIL], ...(t && /down/i.test(t.status) ? { cls: 'bh' } : {}) } })
    const s = tsec(tbl('터널 세부 정보', TUN_COLS, rows, { wrap: 3 }), { h: note })
    if (cls && s.groups[0]) s.groups[0].hcls = cls
    return s
  }
  // VIF 정보창(콘솔 Direct Connect 「가상 인터페이스」 상세 · 비교정리 B 2-14) — 세부 정보(콘솔 General 순서) + 「피어링」(BGP 피어 · 두 줄 행) + 「광고 접두사」.
  //   피어 IP = 엔진이 2.13.0 부터 싣는다(사용자 결정 2026-10-24 「콘솔이 보여 주는 값이면 보여 준다」) · 🔴 인증 키(`authKey`)·라우터 설정은 엔진이 여전히 싣지 않는다
  const VIF_TYPE = { private: '프라이빗', public: '퍼블릭', transit: '전송' }
  const VIF_PEER_COLS = [{ h: 'BGP ASN', w: 70 }, { h: '주소 체계', w: 80, x: 1 }, { h: '내 라우터 피어 IP', w: 140, id: true }, { h: 'Amazon 라우터 피어 IP', w: 140, id: true }, { h: '피어 ID', w: 160, id: true, x: 1 }, { h: 'BGP 상태', w: 110, st: true }]
  function detVif (n) {
    const c = n.con || {}; const con = X.mById.get(M.hidden.get(n.id)) || null
    const gw = c.direct_connect_gateway_id ? [...M.globs.values(), ...M.hubs.values()].find((h) => h.n && natId(h.n) === c.direct_connect_gateway_id) : null
    const on = X.obs && X.obs.on(); const v = on ? vifState(n) : null
    const rows = [['종류', '가상 인터페이스'], ['가상 인터페이스 ID', natId(n)], ...(c.virtual_interface_name ? [['이름', c.virtual_interface_name]] : []),
      ...((n.attrs || {}).state ? [['상태', stateKo(n.attrs.state)]] : []), ...(c.vlan !== undefined ? [['VLAN', String(c.vlan)]] : []),
      ...(c.virtual_interface_type ? [['유형', VIF_TYPE[c.virtual_interface_type] || c.virtual_interface_type]] : []), ...(c.region ? [['리전', REGION_KO[c.region] ? `${REGION_KO[c.region]} · ${c.region}` : c.region]] : []),
      ...(c.amazon_side_asn !== undefined ? [['Amazon 측 ASN', String(c.amazon_side_asn)]] : []),
      ...(c.connection_id ? [['연결 ID', con ? `${c.connection_id} (${disp(con)})` : c.connection_id, null, con && B.det.has(con.id) ? con.id : undefined]] : []),
      ...(c.direct_connect_gateway_id ? [['Direct Connect 게이트웨이', gw ? `${c.direct_connect_gateway_id} (${gw.name})` : c.direct_connect_gateway_id, null, gw && B.det.has(gw.id) ? gw.id : undefined]] : []),
      ...(c.virtual_gateway_id ? [['가상 프라이빗 게이트웨이', c.virtual_gateway_id]] : []), ...(c.mtu !== undefined ? [['MTU', String(c.mtu)]] : []),
      ...(c.jumbo_frame_capable !== undefined ? [['점보 프레임', c.jumbo_frame_capable ? '예' : '아니요']] : []), ...denyRow(n)]
    const peers = Array.isArray(c.bgp_peer_details)
      ? tsec(tbl('피어링', VIF_PEER_COLS, c.bgp_peer_details.map(TSV).map((x) => ({ c: [dash(x[1]), dash(x[0]), dash(x[3]), dash(x[2]), dash(x[4]), on ? (v ? v.b : NIL) : NIL], ...(on && v && v.cls ? { cls: v.cls } : {}) })), { wrap: 4 }), { h: VIF_BGP_NOTE() })
      : M.partial.has(n.id) ? urSec('피어링') : ncSec('피어링', '이 수집본에는 BGP 피어 기록이 없습니다(수집 전 버전입니다)')
    const pf = Array.isArray(c.route_filter_prefixes) ? tsec(tbl('광고 접두사', [{ h: '접두사', w: 200, id: true }], c.route_filter_prefixes.slice().sort(byDst).map((d) => ({ c: [d] })))) : null
    const vn = c.virtual_interface_name || (n.name && n.name !== natId(n) ? n.name : '')
    if (vn && !rows.some((r) => r[0] === '이름')) rows.splice(2, 0, ['이름', vn])
    return { title: `${natId(n)}${vn ? ` / ${vn}` : ''}`, sub: '가상 인터페이스(VIF) — 캔버스에서는 Direct Connect 연결 안에 있습니다', rows, secs: [peers, ...(pf ? [pf] : [])] }
  }
  // LB 「대상」 줄(§17.11 LB 행 · QA B B-14) — 칸을 펼치지 않아도 대상 등록·헬스가 보이게 위 줄들 사이에 둔다.
  //   켜짐 = 그 LB 관측 행의 `targets`(+ 문서 시각).
  //   🔴 `targets` 가 없는 행을 「0개」로 적지 마라 — 못 읽은 것이 「대상 없는 LB」가 된다. 수·문구는 모두 여기서 정한다
  //   꺼짐 = 구조의 **등록 대상 수**(`n.reg` · 사용자 결정 2026-10-16 — 꺼도 수를 본다 · ALB·NLB·GWLB). 헬스는 구조에 없다
  //   🔴 켜짐에서는 관측 수가 이긴다(5분 > 매시) — 구조 수를 같이 적지 않는다(§17.11)
  // 그룹 하나의 등록 수(「대상 그룹(+대상 상태)」 칸의 오른쪽 글) — 기록 없는 옛 스냅샷 = 빈칸
  const tgRegWord = (it, g) => { const r = it.n.reg; const v = r && r.tg ? r.tg[g] : undefined; return v === undefined ? '' : v === 'unread' ? '대상 수 못 읽음' : `대상 ${v}개` }
  // 관측 `targets` 의 다섯 갈래(1.15.0 · #137 · 엔진 `services/base.py` `TARGET_HEALTH_DETAIL`) → 화면 말(docs/qa/copy-a.md ⑭) · 이 순서로 적는다
  const LB_PARTS = [['health_check_off', '헬스 체크 꺼짐'], ['initial', '등록 중'], ['draining', '빼는 중'], ['unused', '사용되지 않음'], ['other_unavailable', '상태 확인 불가']]
  // 「대상」 줄의 수 절 — LB 정보창 「대상」 줄과 **LB → 대상 선 정보창 「대상」 줄**(flow.js `obs.lineRows` · §17.27)이 이 한 함수로 짓는다.
  //   🔴 선 줄에 따로 글을 짓지 마라(사용자 결정 2026-10-22 — 헬스 체크를 끈 Lambda 선이 판정 「정상」 옆에 「1개 중 0개 정상」을 적어 고객이 헷갈렸다).
  //   정상도 비정상도 아닌 대상(AWS `initial`·`draining`·`unused`·`unavailable` — 헬스 체크를 끈 Lambda 대상이 `unavailable` 이다).
  //   관측 1.15.0(#137) = 다섯 갈래를 따로 센다(`LB_PARTS`) → 0 이 아닌 것만 그 이름으로 · 표에 없는 상태(`unhealthy.draining` 등) = 「그 밖 N개」
  //   🔴 다섯이 없는 옛 문서(≤1.13.0)는 어느 상태인지 화면이 가를 수 없다 — 셋 다 말한다(§17.11 · QA C #130). 「빼는 중」으로 단정하지 마라
  //   돌려주는 값 = [글, 그 밖 수] · 시각·색은 부르는 쪽이 정한다
  function targetsWord (t) {
    const parts = LB_PARTS.every(([k]) => Number.isInteger(t[k]))
    const rest = t.total - t.healthy - t.unhealthy - (parts ? LB_PARTS.reduce((s, [k]) => s + t[k], 0) : 0)
    const base = `대상 ${t.total}개 · 정상 ${t.healthy}개 · 비정상 ${t.unhealthy}개`
    if (parts) return [base + LB_PARTS.filter(([k]) => t[k] > 0).map(([k, w]) => ` · ${w} ${t[k]}개`).join('') + (rest > 0 ? ` · 그 밖 ${rest}개` : ''), rest]
    return [base + (rest > 0 ? ` · 그 밖 ${rest}개(헬스 체크 꺼짐·등록 중·빼는 중 중 하나)` : ''), rest]
  }
  function lbTargetRow (it) {
    const a = it.n.attrs || {}; const tg = a['target group']
    const nTg = tg === undefined ? null : tg ? String(tg).split(', ').length : 0
    const NOTG = '대상 그룹 없음 — 이 로드 밸런서로 들어온 요청을 받을 대상이 없습니다'
    const o = X.obs && X.obs.of ? X.obs.of(it.id) : null
    if (o === null && X.obs && X.obs.on()) return ['대상', '표시할 상태 정보가 없습니다 — 상단 배너를 확인하세요', 'wn']
    if (o === null) {
      if (nTg === null) return ['대상', M.partial.has(it.id) ? '대상 그룹을 읽지 못했습니다' : '이 수집본에는 대상 그룹 기록이 없습니다', 'wn']
      if (!nTg) return ['대상', NOTG, 'wn']
      const reg = it.n.reg; const sAt = X.structAt ? X.structAt() : null
      if (!reg) return ['대상', `대상 그룹 ${nTg}개 — 이 수집본에는 대상 수 기록이 없습니다 · 모니터링을 켜면 표시됩니다`]
      if (reg.n === 'unread') return ['대상', `대상 그룹 ${nTg}개 — 대상 수를 읽지 못해 표시하지 않습니다`, 'wn']
      if (!reg.n) return ['대상', `대상 0개 — 대상 그룹 ${nTg}개에 등록된 대상이 없습니다` + (sAt ? ` · ${sAt} 구조 수집` : ''), 'wn']
      return ['대상', `대상 ${reg.n}개 (대상 그룹 ${nTg}개)` + (sAt ? ` · ${sAt} 구조 수집` : '') + ' — 정상·비정상은 모니터링을 켜면 표시됩니다']
    }
    if (!o) return ['대상', '이 로드 밸런서의 상태 정보가 없습니다', 'wn']
    const t = o.targets
    if (!t) return ['대상', '대상 상태를 읽지 못해 대상 수를 표시하지 않습니다', 'wn']
    const at = X.obs.at ? X.obs.at() : null; const when = at ? ` · ${at} 관측` : ''
    if (!t.total) return nTg === 0 ? ['대상', NOTG, 'wn'] : ['대상', `대상 0개 — ${nTg ? `대상 그룹 ${nTg}개에` : '대상 그룹에'} 등록된 대상이 없습니다${when}`, 'wn']
    // 수 절 = `targetsWord`(선 정보창과 한 벌) · 🔴 색은 정상·비정상만 본다 — 헬스 체크 꺼짐을 주의색으로 칠하면 Lambda 대상 LB 가 늘 경고가 된다
    const [word, rest] = targetsWord(t)   // `rest` = 그 밖 수 — 색에 넣지 않는다(검사 129 「그 밖을 비정상색」 브레이크가 이 자리를 깬다)
    const tone = t.unhealthy && !t.healthy ? 'bh' : t.unhealthy ? 'wn' : ''
    return ['대상', word + when, tone]
  }
  // VPN 연결 줄(VGW·CGW 정보창) — 그 게이트웨이에 닿는 VPN 연결 선
  const vpnOf = (id) => M.vpnl.filter((l) => l.b === id).map((l) => ({ a: 'VPN', b: hubName(l.a), tk: [l.a] }))
  // 세부 정보 줄을 콘솔 「세부 정보」에 맞춘다(§17.11 「콘솔 표현 원칙」 · 2.13.0 · 검사 167) — 어댑터 줄을 바꾸거나 빼고 콘솔에만 있던 값을 더한다.
  //   재료(`n.con`)가 없는 옛 스냅샷은 그대로 둔다
  function conRows (it, rows) {
    const c = conOf(it); if (!c) return
    const put = (label, v, after) => { if (v === undefined || v === null || v === '') return; const i = rows.findIndex((r) => r[0] === after); const r = [label, String(v)]; if (i < 0) rows.push(r); else rows.splice(i + 1, 0, r) }
    const drop = (label) => { const i = rows.findIndex((r) => r[0] === label); if (i >= 0) rows.splice(i, 1) }
    const yn = (v) => (v === true || v === 'true' ? '예' : v === false || v === 'false' ? '아니요' : v)
    if (it.kind === 'lb') {
      drop('구성된 AZ')
      // 표가 말하는 줄은 뺀다(「리스너 및 규칙」·「대상 그룹」 칸 — 같은 사실 두 번 · 비교정리 B)
      if (Array.isArray(c.listener_details)) drop('리스너 포트')
      if (Array.isArray(c.target_group_details)) drop('대상 그룹')
      put('DNS 이름', c.dns_name, '상태')
      const sn = it.subnets.map((s) => subnetName(s))
      put('가용 영역', sn.length ? sn.join('\n') : Array.isArray(c.availability_zones) ? c.availability_zones.join(', ') : null, 'DNS 이름')
    }
    if (it.kind === 'nat' && Array.isArray(c.addresses)) {
      drop('EIP')
      const p = c.addresses.map(TSV).find((x) => x[4] === 'true') || c.addresses.map(TSV)[0]
      if (p) { put('기본 퍼블릭 IPv4 주소', p[0] ? `${p[0]}${p[3] ? ` (할당 ID ${p[3]})` : ''}` : null, '상태'); put('기본 프라이빗 IPv4 주소', p[1], '기본 퍼블릭 IPv4 주소'); put('기본 네트워크 인터페이스 ID', p[2], '기본 프라이빗 IPv4 주소') }
      // 연결 유형 = 한국어 콘솔도 영어 그대로(Public · Private — C-ko-nat) · 어댑터 줄은 이미 「연결 유형」 이름표다
      const ci = rows.findIndex((r) => r[0] === '연결 유형' || r[0] === '연결'); if (ci >= 0 && (it.n.attrs || {})['연결']) rows[ci] = ['연결 유형', conState('nat', it.n.attrs['연결'])]
    }
    if (it.kind === 'vpn') {
      put('로컬 IPv4 네트워크 CIDR', c.local_ipv4_network_cidr); put('원격 IPv4 네트워크 CIDR', c.remote_ipv4_network_cidr)
      // 값 낱말 = 한국어 콘솔 VPN 「세부 정보」 그대로(§17.11 「콘솔 표현 원칙」 · B-09-VPN-ko — 「비활성화됨」 · 「퍼블릭 IPv4」 · 「표준」) · 콘솔에 없는 원문은 그대로
      const VPN_WORD = { PublicIpv4: '퍼블릭 IPv4', PrivateIpv4: '프라이빗 IPv4', Ipv6: 'IPv6', standard: '표준', large: '대형' }
      put('가속 활성화됨', c.enable_acceleration === true ? '활성화됨' : c.enable_acceleration === false ? '비활성화됨' : null); put('외부 IP 주소 유형', c.outside_ip_address_type ? VPN_WORD[c.outside_ip_address_type] || c.outside_ip_address_type : null)
      put('게이트웨이 연결 상태', c.gateway_association_state ? conState('tgw', c.gateway_association_state) : null); put('터널 대역폭', c.tunnel_bandwidth ? VPN_WORD[c.tunnel_bandwidth] || c.tunnel_bandwidth : null)
      if (Array.isArray(c.tunnel_inside_cidrs)) drop('외부 터널 IP')   // 「터널 세부 정보」 표가 말한다
    }
    if (it.kind === 'dxcon') {
      put('AWS 논리적 디바이스', c.aws_logical_device_id || c.aws_device); put('점보 프레임', yn(c.jumbo_frame_capable)); put('MACsec', yn(c.macsec_capable))
    }
    if (it.kind === 'cf') {
      if (c.comment) put('설명', c.comment, 'ID')
      if (Array.isArray(c.origins)) drop('원본')   // 「오리진」 칸 하나로(비교정리 C M-11)
      put('HTTP 버전', c.http_version ? String(c.http_version).toUpperCase().replace('HTTP', 'HTTP/').replace('//', '/') : null); put('IPv6', yn(c.is_ipv6_enabled))
    }
    if (it.kind === 'waf' && Array.isArray(c.rules)) drop('규칙')   // 「규칙」 표가 말한다(이름 · 작업 · 우선순위)
    if (it.kind === 'vpce' && c.service_name) { const i = rows.findIndex((r) => r[0] === '서비스'); if (i >= 0) rows[i] = ['서비스 이름', c.service_name]; else put('서비스 이름', c.service_name, 'ID') }
  }
  function detItem (it) {
    if (it.outside) { // §17.1 — 피어링 연결의 엔진 속성이 말하는 상대 쪽. 계정은 가린다(전체 ID 는 화면에 싣지 않는다)
      const sd = it.side
      return { title: 'VPC', sub: typeKo(it), rows: [['종류', typeKo(it)], ['VPC ID', sd.vpc], ['계정', sd.acct ? X.acctText(sd.acct) : '모름'], ['리전', sd.region ? (REGION_KO[sd.region] ? `${REGION_KO[sd.region]} · ${sd.region}` : sd.region) : '모름'],
        ['비고', '수집 범위 밖 — 피어링 연결로만 확인했습니다']], secs: [] }
    }
    if (it.xscope) { // §17.22 — 자리는 범위 안 쪽 리전 상자지만 칸은 그 TGW 의 실제 계정·리전을 말한다
      const r = (() => { let c = it.n; let g = 0; while (c && g++ < 40) { if (c.type === 'region') return c; c = c.parent_id ? X.mById.get(c.parent_id) : null } return null })()
      const rows = [['종류', typeKo(it)], ['계정', acctName(it.xacct)], ['리전', r ? regName(r.id) : '모름'],
        ['수집 여부', '예 — 수집했지만 보기 범위에서 제외했습니다'], ['되돌리기', '상단바에서 «계정: 전체»를 누르세요']]
      return { title: svcOf(it), sub: typeKo(it), rows, secs: relSec(it.id) }
    }
    // 콘솔 「세부 정보」(§17.11 「콘솔 표현 원칙」 · 검사 166) — TGW · VGW · IGW 는 콘솔 칸 이름·순서 그대로 · 제목 `ID / 이름`(통합정리 0-3)
    const conT = !it.ghost && (it.kind === 'tgw' || it.kind === 'vgw' || it.kind === 'igw' || it.kind === 'cgw' || it.kind === 'r53')
    if (conT) {
      const reg = ['리전', it.region ? regName(it.region) : '전역(리전 없음)']
      const vpcCell = (ids) => ids.map((id) => { const v = M.vpcs.get(id); return v ? idNm(natId(v.n), v.named ? v.name : null) : String(id).split('/').pop() }).join(', ') || NIL
      const at = it.n.attrs || {}; const cc = conOf(it) || {}
      // CGW = 콘솔 고객 게이트웨이 「세부 정보」 순서 · Route 53 = 콘솔 「호스팅 영역 세부 정보」(제목 = 영역 이름 — 콘솔 머리)
      const rows = it.kind === 'cgw' ? [['고객 게이트웨이 ID', natId(it.n)], ['상태', it.state ? stateKo(it.state) : NIL], ['유형', cc.type || at['종류'] || NIL],
          ['IP 주소', cc.ip_address || at['IP 주소'] || NIL], ['BGP ASN', cc.bgp_asn ?? at['BGP ASN'] ?? NIL], ['인증서 ARN', cc.uses_certificate === true ? '사용' : NIL],
          ['디바이스', cc.device_name || at['디바이스 이름'] || NIL], reg, ['소유자 ID', acctName(it.acct)]]
        : it.kind === 'r53' ? [['호스팅 영역 이름', at['영역'] || it.name || NIL], ['호스팅 영역 ID', natId(it.n)], ['유형', at['유형'] || NIL], ['레코드 수', at['레코드 수'] ?? NIL],
            ...(Array.isArray(it.n.zoneVpcs) && it.n.zoneVpcs.length ? [['연결된 VPC', vpcCell(it.n.zoneVpcs)]] : []), ['소유자 ID', acctName(it.acct)]]
        : it.kind === 'tgw' ? [...tgwRows(it), reg]
        : it.kind === 'vgw' ? [['가상 프라이빗 게이트웨이 ID', natId(it.n)], ['상태', it.state ? stateKo(it.state) : NIL], ['유형', (it.n.attrs || {})['종류'] || NIL],
            ['VPC', vpcCell(it.n.vpcIds || [])], ['Amazon ASN', (it.n.attrs || {})['Amazon 측 ASN'] ?? NIL], reg, ['소유자 ID', acctName(it.acct)]]
          : [['인터넷 게이트웨이 ID', natId(it.n)], ['상태', it.state ? stateKo(it.state) : NIL], ['VPC ID', it.vpc ? vpcCell([it.vpc]) : (it.n.con && it.n.con.vpcNat) || '미부착'], reg, ['소유자 ID', acctName(it.acct)]]
      rows.push(...denyRow(it.n))
      const nm = named(it.n) ? it.name : null
      return { title: it.kind === 'r53' ? (it.name || natId(it.n)) : nm ? `${natId(it.n)} / ${nm}` : natId(it.n), sub: typeKo(it), rows, secs: [...itemSecs(it), ...relSec(it.id)] }
    }
    const rows = [['종류', typeKo(it)], ['계정', acctName(it.acct)], ['리전', it.region ? regName(it.region) : '전역(리전 없음)']]
    const v = M.vpcs.get(it.vpc); if (v) rows.push(['VPC', v.name])
    if (!it.ghost) rows.push(['ID', natId(it.n)])
    if (it.state) rows.push(['상태', stateKo(it.state)])
    if (it.ghost) rows.push(['비고', it.stub ? '수집 대상 계정·리전이 아닙니다 — 피어링 연결로만 확인했습니다' : '이 계정의 DX 게이트웨이를 읽지 못했습니다 — TGW 어태치먼트로만 확인했습니다'])
    if (!it.ghost) rows.push(...attrRows(it.n))
    // 「웹 ACL」 줄(§17.41) — 엔진이 WAF 노드를 준 스냅샷(2.12.0)이면 이름을 누르면 그 WAF 정보창으로(넷째 칸 = 열 열쇠) · 옛 스냅샷은 글자 그대로.
    //   API Gateway 는 속성 줄이 없다 → 보호 선이 있으면 그 이름 · WAF 를 읽었는데 없으면 「없음」 · 못 읽었으면 「읽지 못했습니다」(「없음」과 가른다 · 불변식 ④)
    if (!it.ghost && (it.kind === 'cf' || it.kind === 'lb' || it.kind === 'apigw')) {
      const w = wafOf(it.id); const wn = w && (M.globs.get(w) || M.hubs.get(w))
      const r = rows.find((x) => x[0] === '웹 ACL')
      if (r && wn) r[3] = w
      else if (!r && it.kind === 'apigw') {
        const c = covOf(it.n, 'waf_web_acl')
        if (wn) rows.push(['웹 ACL', wn.name, null, w])
        else if (c && c.status === 'ok') rows.push(['웹 ACL', '없음'])
        else if (c && c.status === 'denied') rows.push(['웹 ACL', UNREAD, 'wn'])
      }
    }
    if (!it.ghost) conRows(it, rows)
    if (it.kind === 'lb' && !it.ghost) rows.push(lbTargetRow(it))
    // 탄력적 IP(2.10.0 · §17.34) — 이 자원의 퍼블릭 IP 가 탄력적 IP 면 그 할당을 말한다(선이 아니라 이 줄 · EIP 는 아이콘이 아니다)
    //   NAT 는 콘솔 「기본 퍼블릭 IPv4 주소」 한 줄이 그 할당까지 말한다(`conRows`) — 🔴 옛 「EIP」·「탄력적 IP」 두 줄 중복(비교정리 C M-10)
    if (Array.isArray(it.n.eips) && it.n.eips.length && !(it.kind === 'nat' && rows.some((r) => r[0] === '기본 퍼블릭 IPv4 주소'))) rows.push(['탄력적 IP', it.n.eips.map((e) => `${e.ip} — 탄력적 IP입니다(할당 ID ${e.alloc}${e.svc ? ` · AWS 가 ${String(e.svc).toUpperCase()} 에 관리하는 주소` : ''}${e.name ? ` · ${e.name}` : ''})`).join('\n')])
    rows.push(...denyRow(it.n))
    // 리전 줄에 선 것(§17.12 ①) — 왜 VPC·서브넷 안이 아닌지를 접힌 칸이 아니라 첫 줄들에서 말한다
    if (it.regional) rows.push(['표시 위치', `리전 줄 — ${NOSUB_WHY[it.n.type]}`])
    if (it.kind === 'lambda' && !it.vpc) rows.push(['표시 위치', it.n.fnRead ? '리전 줄 — VPC 에 연결되지 않은 함수입니다' : '리전 줄 — VPC 연결 여부를 알 수 없습니다(로드밸런서 대상에서만 확인한 함수입니다)'])
    // LB = 콘솔 「가용 영역」 한 줄이 서브넷·AZ 를 같이 말한다(`conRows` — 🔴 옛 「구성된 AZ」·「서브넷 N개」·「서브넷의 AZ」 세 줄 · 비교정리 B R-05)
    const lbAz = it.kind === 'lb' && rows.some((r) => r[0] === '가용 영역')
    if (it.subnets.length && !lbAz) rows.push([it.subnets.length > 1 ? `서브넷 ${it.subnets.length}개` : '서브넷', it.subnets.map(subnetName).join('\n')])
    if (it.subnets.length > 1 && !lbAz) rows.push(['서브넷의 AZ', [...new Set(it.subnets.map((s) => M.subnets.get(s).az))].sort().join(', ')])
    // CloudFront = 콘솔이 배포를 구분하는 「설명」으로 부른다(🔴 비교정리 C R-13 — 도메인만으로는 어느 배포인지 모른다)
    const cfc = it.kind === 'cf' ? (conOf(it) || {}).comment : null
    // 제목 = 그 콘솔 상세 화면의 머리(§17.11 「콘솔 표현 원칙」 · 검사 122) — EC2 계열은 `ID / 이름` · LB·WAF·Route 53 은 이름 · CloudFront 는 `ID / 설명`
    const ttl = it.ghost ? null : CON_ID_TITLE.has(it.kind) ? (hasNm(it) ? `${natId(it.n)} / ${it.name}` : natId(it.n))
      : CON_NM_TITLE.has(it.kind) ? (hasNm(it) ? it.name : null) : it.kind === 'cf' ? `${natId(it.n)} / ${cfc || it.name}` : null
    return { title: ttl || (cfc ? `${svcOf(it)} (${cfc})` : hasNm(it) ? `${svcOf(it)} (${it.name})` : svcOf(it)), sub: typeKo(it), rows, secs: [...itemSecs(it), ...relSec(it.id, it.kind === 'dxcon' || it.kind === 'dxgw' ? DX_REL_SKIP : null)] }
  }
  function detVpc (v) {
    const pub = v.subnets.filter((s) => s.tier === 'public').length
    const L = M.list.get(v.id) || []; const a = v.n.attrs || {}; const c = v.n.con || {}
    const ls = (t, label, cnt) => { const rs = L.filter((r) => r.type === t).map((r) => ({ a: String(r.name || r.id), b: String(r.detail || '') })); return rs.length || cnt === undefined ? sec(label, rs) : sec(label, [], { n: cnt, empty: '개수만 확인했습니다 — 목록은 수집하지 않습니다' }) }
    // 콘솔 「세부 정보」(통합정리 2-1 · 검사 166) — DNS·테넌시·DHCP·IPv6 는 키가 있을 때만(못 읽음 = 「읽지 못함」 · 옛 판 = 줄 없음) · 기본 라우팅 테이블 = 목록의 main
    const dnsRow = (lab, val) => (val !== undefined ? [[lab, val === true ? '활성화됨' : val === false ? '비활성화됨' : String(val)]]
      : M.partial.has(v.id) && (X.T.partial || []).some((p) => p && p.target === v.id && p.api === 'ec2:DescribeVpcAttribute') ? [[lab, UNREAD, 'wn']] : [])
    const mainRt = L.find((r) => r.type === 'route_table' && r.rt && r.rt.main)
    const conRows = [...dnsRow('DNS 호스트 이름', c.dnsHostnames), ...dnsRow('DNS 확인', c.dnsSupport),
      ...(c.tenancy !== undefined ? [['테넌시', String(c.tenancy)]] : []),
      ...(c.dhcpId !== undefined ? [['DHCP 옵션 세트', c.dhcpId + (Array.isArray(c.dhcp) && c.dhcp.length ? `\n${c.dhcp.join('\n')}` : '')]] : []),
      ...(mainRt ? [['기본 라우팅 테이블', idNm(mainRt.id, mainRt.name)]] : []),
      ...(Array.isArray(c.ipv6) ? [['IPv6 CIDR', c.ipv6.length ? c.ipv6.join(', ') : NIL]] : [])]
    return { title: v.named ? `${natId(v.n)} / ${v.name}` : natId(v.n), sub: `VPC · ${acctName(v.acct)} · ${v.code}`,
      // 🔴 아이콘 자원은 카드가 「상태」를 직접 적는데 VPC·서브넷만 빠져 있었다(QA 기능개발 7 · §17.11 개정).
      //    `소유 계정`(RAM 공유 VPC 의 범위 밖 소유자)·`미해결 대상`(불변식 ④ 축)은 `attrRows` 가 준다.
      rows: [['VPC ID', natId(v.n)], ...(a.state ? [['상태', stateKo(a.state)]] : []), ...conRows, ['기본 VPC', a['default VPC'] || '?'], ['IPv4 CIDR', v.cidr || '?'],
        ['소유자 ID', acctName(v.acct)], ...vsRows(v.n),
        ['AZ', [...new Set(v.subnets.map((s) => s.az))].sort().join(', ') || '없음'],
        ['서브넷', `${v.subnets.length}개 (퍼블릭 ${pub} · 프라이빗 ${v.subnets.length - pub})`],
        ['TGW 어태치먼트', v.ports.filter((t) => { const P = M.ports.get(M.portOf.get(v.id + '|' + t)) || {}; return !P.vgw && !P.cwan && !P.lsn }).map(hubName).join(', ') || '없음'],
        // #129(2.11.0 · §17.39~40) — 그 포트가 있을 때만 줄이 있다
        ...(v.ports.some((t) => (M.ports.get(M.portOf.get(v.id + '|' + t)) || {}).cwan) ? [['Cloud WAN', v.ports.filter((t) => (M.ports.get(M.portOf.get(v.id + '|' + t)) || {}).cwan).map(hubName).join(', ')]] : []),
        ...(v.ports.some((t) => (M.ports.get(M.portOf.get(v.id + '|' + t)) || {}).lsn) ? [['VPC Lattice 서비스 네트워크', v.ports.filter((t) => (M.ports.get(M.portOf.get(v.id + '|' + t)) || {}).lsn).map(hubName).join(', ')]] : []),
        ...(v.ports.some((t) => (M.ports.get(M.portOf.get(v.id + '|' + t)) || {}).vgw) ? [['VGW', v.ports.filter((t) => (M.ports.get(M.portOf.get(v.id + '|' + t)) || {}).vgw).map(hubName).join(', ')]] : []), ...denyRow(v.n),
        // 프라이빗 호스팅 영역(2.8.0 · §17.30) — 이 VPC 에서 이름이 풀리는 영역(영역 쪽 `zoneVpcs`) · 없으면 줄이 없다
        ...(() => { const zs = [...M.globs.values()].filter((g) => g.kind === 'r53' && (g.n.zoneVpcs || []).includes(natId(v.n))).map((g) => g.name); return zs.length ? [['프라이빗 호스팅 영역', zs.join(', ')]] : [] })(),
        ...vpcPartRow(v)],
      secs: [vpcNetSec(v), vpcSubSec(v), rtbSec(v, a['route table']), ls('network_acl', '네트워크 ACL', a['network ACL']), ls('security_group', '보안 그룹', a['security group']), ...tagSec(v.n)] }
  }
  // VPC 「서브넷 (N)」(통합정리 2-1 — 콘솔 Resource map 의 서브넷 목록) = 서브넷 ID (이름) · 가용 영역 · IPv4 CIDR · 순서 = AZ → CIDR
  function vpcSubSec (v) {
    const rows = v.subnets.map((s) => ({ az: s.az, cd: (s.n.attrs || {}).cidr || '', c: [idNm(natId(s.n), s.named ? s.name : null), s.az, (s.n.attrs || {}).cidr || NIL], tk: [s.id] }))
      .sort((x, y) => (x.az < y.az ? -1 : x.az > y.az ? 1 : 0) || byDst(x.cd, y.cd))
    const t = tbl('서브넷', [{ h: '서브넷 ID', w: 260, id: true }, { h: '가용 영역', w: 90 }, { h: 'IPv4 CIDR', w: 120, id: true }], rows)
    return { label: '서브넷', n: rows.length, nc: false, groups: rows.length ? [{ h: '', rows: [], tbl: t }] : [], empty: '(0)' }
  }
  // VPC 「네트워크 연결 (N)」(통합정리 2-1 — 콘솔 Resource map 「Network Connections」) = 이 VPC 의 바깥 길(IGW · NAT · 게이트웨이 엔드포인트 · 포트 · 피어링)
  function vpcNetSec (v) {
    const rows = []
    const add = (kind, id, nm, st, tk) => rows.push({ c: [kind, idNm(id, nm), st ? stateKo(st) : NIL], tk })
    for (const it of v.items) {
      if (it.kind === 'igw') add('인터넷 게이트웨이', natId(it.n), it.named ? it.name : null, it.state, [it.id])
      else if (it.kind === 'nat') add('NAT 게이트웨이', natId(it.n), it.named ? it.name : null, it.state, [it.id])
      else if (it.kind === 'vpce' && it.epType === 'Gateway') add('게이트웨이 엔드포인트', natId(it.n), it.svc, it.state, [B.cellOf.get(it.id) || it.id])
    }
    for (const t of v.ports) {
      const pk = M.portOf.get(v.id + '|' + t); const P = M.ports.get(pk) || {}; const h = M.hubs.get(t) || M.globs.get(t)
      const kind = P.vgw ? '가상 프라이빗 게이트웨이' : P.cwan ? 'Cloud WAN 연결' : P.lsn ? 'VPC Lattice 서비스 네트워크' : 'Transit Gateway 어태치먼트'
      const id = P.vgw || P.cwan || P.lsn ? (h ? natId(h.n) : String(t).split('/').pop()) : natId(P.n)
      const nm = P.vgw || P.cwan || P.lsn ? (h && h.named ? h.name : null) : named(P.n || {}) ? P.n.name : null
      add(kind, id, nm, P.vgw ? 'attached' : (P.n && P.n.attrs || {}).state, [pk || t])
    }
    for (const P of M.pp.values()) if (P.vpc === v.id) add('VPC 피어링', natId(P.n), named(P.n) ? P.n.name : null, (P.n.attrs || {}).state, [P.key])
    rows.sort((x, y) => (x.c[0] < y.c[0] ? -1 : x.c[0] > y.c[0] ? 1 : 0) || (x.c[1] < y.c[1] ? -1 : x.c[1] > y.c[1] ? 1 : 0))
    const t = tbl('네트워크 연결', [{ h: '유형', w: 150 }, { h: 'ID', w: 260, id: true }, { h: '상태', w: 70, st: true }], rows, { wrap: 2 })
    return { label: '네트워크 연결', n: rows.length, nc: false, groups: rows.length ? [{ h: '', rows: [], tbl: t }] : [], empty: '(0)' }
  }
  function detSub (s) {
    const a = s.n.attrs || {}
    const rows = [['CIDR', a.cidr || '?'], ['AZ', s.az], ...(a['AZ ID'] ? [['AZ ID', a['AZ ID']]] : []), ['ID', natId(s.n)],
      ...(a.state ? [['상태', stateKo(a.state)]] : []), ...vsRows(s.n),
      ['나가는 경로', [...s.exits].map(nodeName).join(', ') || '없음'],
      ['퍼블릭 IPv4 자동 할당', a['public IP 자동할당'] ?? a['public IP'] ?? '?'],
      ['이 서브넷의 리소스', s.items.map((x) => `${typeKo(x)} ${x.name}`).join('\n') || '없음']]
    // 못 이은 ENI 라우트(2.8.0 · P2-2 · 엔진 `unresolved_route_targets`) = 그 ENI + 까닭(주의색) — 「이 화면에 없는 대상」 한 말로 뭉개지 않는다
    const rr = s.routes.map((r) => ({ a: dstWord(s, r.d) || '(목적지 미상)', b: r.t ? nodeName(r.t) : r.why ? `${r.raw} — ${routeWhy(r.why)}` : `${r.raw} (이 화면에 없는 대상)`, tk: r.t ? [r.t] : [], cls: r.bh ? 'bh' : r.why ? 'wn' : '' }))
    // 「라우팅 테이블」 칸 = 콘솔 서브넷 › 라우팅 테이블 탭(검사 166) · 그 테이블 기록이 없는 수집본(목록 없음)만 옛 줄
    const rtS = subRtSec(s)
    return { title: s.named ? `${natId(s.n)} / ${s.name}` : natId(s.n), sub: `서브넷 · ${s.tier === 'public' ? '퍼블릭' : '프라이빗'}`, rows,
      secs: [rtS || sec('라우팅 테이블', rr, { h: a.route_table ? String(a.route_table) : '' }), naclSec(s), ...tagSec(s.n)] }
  }
  function detPp (P) {
    const v = M.vpcs.get(P.vpc); const a = P.n.attrs || {}; const c = P.n.con
    // 콘솔 피어링 상세(통합정리 2-7 · 검사 166) — 「세부 정보」 = 요청자/수락자 두 묶음(소유자 ID · VPC · CIDR · 리전) · 정보창을 연 쪽 VPC 에 「(이 VPC)」
    //   🔴 옛 「이 VPC · 상대」는 누가 요청했는지를 지웠다 — 요청자/수락자를 뒤바꿔 적으면 수락 대기 피어링을 어느 계정에서 수락해야 하는지 오판한다
    const mine = v ? natId(v.n) : null
    const vpcOf = (id) => { const x = [...M.vpcs.values()].find((y) => natId(y.n) === id); return x && x.named ? x.name : null }
    const side = (lab, s) => (!s ? [] : [[`${lab} 소유자 ID`, s.acct ? X.acctText(s.acct) : NIL], [`${lab} VPC`, idNm(s.vpc, vpcOf(s.vpc)) + (s.vpc && s.vpc === mine ? ' (이 VPC)' : '')],
      [`${lab} CIDR`, [...(s.cidrs || []), ...(s.ipv6 || [])].join(', ') || NIL], [`${lab} 리전`, s.region ? (REGION_KO[s.region] ? `${REGION_KO[s.region]} · ${s.region}` : s.region) : NIL]])
    const rows = c ? [['피어링 연결 ID', natId(P.n)], ['상태', a.state ? stateKo(a.state) : NIL], ...side('요청자', c.req), ...side('수락자', c.acc), ...(c.exp ? [['만료 시간', String(c.exp)]] : [])]
      : [['ID', natId(P.n)], ['이 VPC', v ? v.name : '?'], ['상대', ppPeer(P)], ...(a.state ? [['상태', stateKo(a.state)]] : [])]
    const pid = natId(P.n)
    // 라우팅 테이블 칸(콘솔 「Route tables」 탭) = 이 피어링으로 경로를 둔 테이블(양쪽 VPC 중 이 화면에 있는 것 전부) — 라우팅 테이블 ID (이름) · VPC · 기본 · 연결 대상
    const rtRows = []
    for (const [vid, L] of M.list) for (const r of L) {
      if (r.type !== 'route_table' || !r.rt || !r.rt.routes.some((x) => x.t === pid)) continue
      const vv = M.vpcs.get(vid); const n = r.rt.assoc.length
      rtRows.push({ c: [idNm(r.id, r.name), vv ? idNm(natId(vv.n), vv.named ? vv.name : null) : NIL, r.rt.main ? '예' : '아니요', n === 1 ? natId((M.subnets.get(r.rt.assoc[0]) || {}).n || { id: r.rt.assoc[0] }) : `${n} 서브넷`], tk: vv ? [vv.id] : [] })
    }
    rtRows.sort((x, y) => (x.c[1] < y.c[1] ? -1 : x.c[1] > y.c[1] ? 1 : 0) || (x.c[0] < y.c[0] ? -1 : 1))
    const one = (label, cols, rs, o = {}) => ({ label, n: o.nc ? null : rs.length, nc: !!o.nc, groups: rs.length ? [{ h: '', rows: [], tbl: tbl(label, cols, rs, o) }] : [], empty: o.nc ? NC : '(0)' })
    const secs = [one('라우팅 테이블', [{ h: '라우팅 테이블 ID', w: 250, id: true }, { h: 'VPC ID', w: 230, id: true }, { h: '기본', w: 60 }, { h: '연결 대상', w: 220, id: true }], rtRows, { wrap: 2 })]
    // DNS 칸(콘솔 「DNS」 탭) — 원격 VPC 에서 DNS 확인 허용 · 키가 없으면(못 읽음·옛 판) 칸 자체가 수집 안 함
    if (c) {
      const dn = [['요청자 VPC', c.req], ['수락자 VPC', c.acc]].filter(([, s]) => s && typeof s.dns === 'boolean').map(([l, s]) => ({ c: [`${l} — ${idNm(s.vpc, vpcOf(s.vpc))}`, yesNo(s.dns)] }))
      secs.push(dn.length ? one('DNS', [{ h: 'VPC', w: 380, id: true }, { h: '원격 VPC 에서 DNS 확인 허용', w: 170 }], dn, { wrap: 1 }) : one('DNS', [], [], { nc: true }))
    }
    // 예외 E4 — 이 피어링으로 실제로 나가는 서브넷(콘솔에 없는 거꾸로 계산 · 라우팅 테이블 칸 아래)
    secs.push(gwRouteSec('이 피어링으로 라우트를 보내는 서브넷', P.pcx))
    const nm = named(P.n) ? P.n.name : null
    return { title: c ? (nm ? `${pid} / ${nm}` : pid) : 'VPC 피어링', sub: 'VPC 아래쪽 변의 작은 원 — 이 VPC 의 서브넷이 이 피어링으로 나가는 지점입니다. 점선이 여기서 시작합니다.', rows, secs }
  }
  function detPort (p, how) {
    const v = M.vpcs.get(p.vpc); const a = p.n.attrs || {}
    // VGW 포트 = 합성 칸(엔진 노드 없음 — VGW 의 붙은 VPC 속성) · 붙은 서브넷이라는 개념이 없다(VGW 는 VPC 에 붙는다)
    if (p.vgw) return { title: 'VGW 연결', sub: how.replace('TGW', 'VGW'), rows: [['VGW', hubName(p.tgw)], ['VPC', v ? v.name : '?'], ['ID', natId(M.hubs.get(p.tgw).n)], ['상태', stateKo('attached')]], secs: [] }
    // Cloud WAN(§17.39) · VPC Lattice(§17.40) 포트 = 합성 칸(엔진 노드 없음 — 연결 선의 속성) · Cloud WAN 은 그 코어 네트워크로 가는 라우트를 든다
    if (p.cwan) {
      const ea = p.ea || {}; const subs = v ? v.subnets.filter((s) => s.exits.has(p.tgw)).map((s) => s.id).sort((x, y) => subnetName(x).localeCompare(subnetName(y)) || (x < y ? -1 : 1)) : []
      return { title: 'Cloud WAN 연결', sub: how, rows: [['코어 네트워크', hubName(p.tgw)], ['VPC', v ? v.name : '?'], ['세그먼트', (ea.segments || []).join(', ') || '?'],
        ['엣지 위치', (ea.edge_locations || []).map(gaRegion).join(', ') || '?'], ['연결 ID', (ea.attachment_ids || []).join(', ') || '?'], ...(ea.state ? [['상태', stateKo(ea.state)]] : [])],
      secs: [sec('이 코어 네트워크로 라우트를 보내는 서브넷', subs.map((sid) => ({ a: M.subnets.get(sid).routes.filter((r) => r.t === p.tgw).map((r) => r.d || '(목적지 미상)').join(' · ') || '라우트', b: subnetName(sid), tk: [sid] })))] }
    }
    if (p.lsn) return { title: 'VPC Lattice 연결', sub: how, rows: [['서비스 네트워크', hubName(p.tgw)], ['VPC', v ? v.name : '?'], ...((p.ea || {}).state ? [['상태', stateKo(p.ea.state)]] : [])], secs: [] }
    // TGW Connect 는 같은 짝 어태치먼트가 아니다 — 이 어태치먼트 **위**로 지나가는 GRE 오버레이다(아래 줄 · 점선)
    const dup = [...M.hidden.entries()].filter(([id, r]) => r === p.id && !M.cnx.some((c) => c.id === id)).map(([id]) => X.mById.get(id)).filter(Boolean)
    const rides = M.cnx.filter((c) => c.port === p.id)
    // 엔진은 어태치먼트의 서브넷을 모으지 않는다(`DescribeTransitGatewayAttachments` 에 없다) — VPC id 를 이 줄에 적던 어댑터를 고쳤다
    const subs = [a.subnets, ...dup.map((n) => (n.attrs || {}).subnets)].filter((x) => x != null && x !== '').map((x) => (typeof x === 'number' ? `${x}개` : x)).join('\n')
    const extra = [...(dup.length ? [['같은 짝의 어태치먼트', dup.map(natId).join(', ')]] : []),
      ...(rides.length ? [['TGW Connect', rides.map((c) => `${disp(c.n)} — 이 어태치먼트 위로 GRE 터널이 지나갑니다(점선)`).join('\n')]] : [])]
    const c = p.n.con
    // 콘솔 Transit Gateway Attachment 상세(통합정리 2-6 · 검사 166) — 「세부 정보」 콘솔 순서 + 「서브넷 (N)」 칸.
    //   🔴 옛 「연결된 서브넷 — 수집 안 함」(R-03) — 엔진이 이제 VPC 어태치먼트의 서브넷을 모은다(`DescribeTransitGatewayVpcAttachments`).
    //   못 읽었으면 「읽지 못함」(거부) · 옛 판(키 없음)만 아래 옛 줄로 남긴다
    if (c && (Array.isArray(c.subnets) || c.subnetsDenied || c.assocRt || c.assocState)) {
      const tg = M.hubs.get(p.tgw)
      const rows = [['Transit Gateway Attachment ID', natId(p.n)], ['상태', a.state ? stateKo(a.state) : NIL], ['리소스 유형', TGW_RES[String(c.resType || 'vpc').toLowerCase()] || 'VPC'],
        ['연결 상태', c.assocState ? conState('tgw', c.assocState) : NIL], ['Transit Gateway ID', tg ? idNm(natId(tg.n), tg.named ? tg.name : null) : (c.tgwId || NIL)],
        ['리소스 소유자 ID', c.owner ? X.acctText(c.owner) : NIL], ['리소스 ID', idNm(c.resId || (v ? natId(v.n) : NIL), v && v.named ? v.name : null)],
        ['연결 라우팅 테이블 ID', c.assocRt || NIL], ...[['DNS 지원', c.dns], ['보안 그룹 참조 지원', c.sgRef], ['IPv6 지원', c.ipv6], ['어플라이언스 모드 지원', c.appliance]]
          .filter(([, x]) => x !== undefined).map(([k, x]) => [k, enDis(x)]), ...extra]
      const SN_COLS = [{ h: '서브넷 ID', w: 320, id: true }, { h: '가용 영역', w: 120 }]
      const sn = Array.isArray(c.subnets) ? c.subnets.map((s, i) => { const sid = (c.subnetNodes || [])[i]; const S = sid && M.subnets.get(sid)
        return { az: S ? S.az : '', c: [idNm(s, S && S.named ? S.name : null), S ? S.az : NIL], tk: S ? [sid] : [] } }).sort((x, y) => (x.az < y.az ? -1 : x.az > y.az ? 1 : 0) || (x.c[0] < y.c[0] ? -1 : 1)) : []
      const snSec = c.subnetsDenied ? { label: '서브넷', n: null, nc: false, ur: true, groups: [], empty: UNREAD }
        : Array.isArray(c.subnets) ? { label: '서브넷', n: sn.length, nc: false, groups: sn.length ? [{ h: '', rows: [], tbl: tbl('서브넷', SN_COLS, sn) }] : [], empty: '(0)' }
          : missSec('서브넷', { id: p.n.id })
      const nm = named(p.n) ? p.n.name : null
      return { title: nm ? `${natId(p.n)} / ${nm}` : natId(p.n), sub: how, rows, secs: [snSec] }
    }
    return { title: 'TGW 어태치먼트', sub: how, rows: [['TGW', hubName(p.tgw)], ['VPC', v ? v.name : '?'], ['ID', natId(p.n)], ...(a.state ? [['상태', stateKo(a.state)]] : []),
      ['연결된 서브넷', subs || '수집 안 함'], ...extra], secs: [] }
  }

  // ======================================================================== 배치
  const cellMake = (it, mode = 'below') => {
    const W = mode === 'right' ? 104 : it.kind === 'igw' ? 96 : CW - 6
    const l1 = svcOf(it); const l2 = it.ghost ? (it.outside ? it.other : it.xscope ? '범위 밖' : it.stub ? '다른 계정' : '읽지 못함') : hasNm(it) ? paren(it.name, W) : ''
    B.det.set(it.id, () => detItem(it))
    // nm·nmW = 형제 줄임(`fitIcons`)의 입력 — l2 는 형제를 다 모은 뒤 그 자리에서 다시 쓴다(위 `paren` 은 형제 없이 쓰일 때의 값)
    const nm = !it.ghost && hasNm(it) ? it.name : null
    return { key: it.id, kind: it.kind, acct: it.acct, l1, l2, nm, nmW: W - tw('()', 10.5), off: it.kind === 'ec2' && !!it.state && it.state !== 'running', ghost: !!it.ghost, lb: it.lbType,
      subs: it.subnets.slice(), ids: [it.id], it }
  }
  // 묶음 멤버(§17.11 「포함된 리소스」 · 사용자 결정 2026-10-23 · 검사 162) — 멤버 id → { g: 묶음 키, det: 그 낱개의 정보창 · n: 모델 노드 }
  //   정보창 목록의 줄 · 주소(`#sel=<멤버 id>`) · 「!」 목록 줄이 이 표로 낱개 정보창을 연다. 🔴 묶음을 새로 만드는 곳은 멤버마다 여기 올린다 —
  //   안 올리면 목록 줄이 글자로만 남는다(누를 수 없다 = 그 멤버의 정보를 볼 길이 없다)
  const memReg = (g, x, f = () => detItem(x)) => { if (!B.mem.has(x.id)) B.mem.set(x.id, { g, det: f, n: x.n }) }
  function aggCell (g, parent) {
    const k0 = g[0].kind; const key = `aws:g:${k0}:${parent}`
    for (const x of g) memReg(key, x)
    const run = g.filter((x) => x.state === 'running').length
    const l1 = `${SVC[k0]} ×${g.length}`
    B.det.set(key, () => ({ title: l1, sub: `${TYPE_KO[k0]} 묶음 — 같은 서브넷·같은 종류가 ${AGG_T}개를 넘어 하나로 묶였습니다`,
      rows: [['서브넷', subnetName(g[0].subnets[0])], ...(k0 === 'ec2' && g.some((x) => x.state) ? [['실행 / 그 외', `${run} / ${g.length - run}`]] : [])],
      members: g.map((x) => ({ name: x.name, sub: [x.itype, x.state && stateKo(x.state), natId(x.n)].filter(Boolean).join(' · '), key: x.id })), secs: itemSecs({ kind: k0, n: { attrs: {} }, subnets: [], aggKey: key }) })) // 묶음도 낱개와 같은 칸(§17.11)
    return { key, kind: k0, acct: g[0].acct, l1, l2: '', agg: g.length, subs: [g[0].subnets[0]], ids: g.map((x) => x.id) }
  }
  // 줄 묶음(§17.1 「전역 줄·리전 자원 줄 묶음」 · 사용자 결정 2026-10-23) — 선이 닿지 않는(`M.linked` 밖) 같은 종류가 LINE_T 개 이상이면 한 칸.
  //   묶을 수 있는 종류 = 제 자리가 따로 없는 것(전역 줄 전부 · 리전 줄의 DX 연결·VPN·CGW·떨어진 IGW·서브넷 없는 리전 자원·Lambda·API·Lattice 서비스).
  //   TGW·VGW·Lattice 서비스 네트워크는 제 포트 위가 자리라 묶지 않는다 · 흐린 칸(범위 밖·읽지 못함)은 묶지 않는다(무엇인지가 칸마다 다르다)
  const LINE_T = 3
  const LINE_HUB = new Set(['dxcon', 'vpn', 'cgw', 'igw', 'lambda', 'apigw', 'lsvc', 'waf'])
  const LINE_GLOB = new Set(['cf', 's3', 'r53', 'dxgw', 'ga', 'cwan', 'waf'])   // WAF(2.12.0 · §17.41) — 보호 대상이 없는 것만 묶인다
  function lineGroups (list, can) {
    const free = (h) => !h.ghost && can(h) && !M.linked.has(h.id)
    const by = new Map(); for (const h of list) if (free(h)) push(by, h.kind, h)
    const of = new Map()
    for (const [kind, ms] of by) if (ms.length >= LINE_T) { const g = { kind, members: ms }; for (const h of ms) of.set(h.id, g) }
    return { of, free }
  }
  // 묶음 칸 — 서브넷 묶음(`aggCell`)과 같은 모양(겹친 아이콘 · `<종류> ×N` · 정보창 「포함된 리소스」 목록 · 멤버마다 제 열쇠).
  //   멤버 낱개 정보창도 등록한다(목록에서 하나를 고르면 그 정보창이 열린다 — 낱개로 섰을 때와 같은 칸)
  // 묶음 정보창의 칸 = 멤버 낱개의 칸을 이름(§17.11 — 낱개와 같은 칸 목록)마다 합친 것 · 멤버마다 머리(그 이름)를 단 묶음 · 머리 숫자 = 멤버 줄 수의 합
  const mergeSecs = (ms) => {
    const by = new Map()
    for (const x of ms) {
      for (const sc of itemSecs(x)) {
        if (!by.has(sc.label)) by.set(sc.label, { ...sc, n: 0, groups: [] })
        const o = by.get(sc.label); o.nc = o.nc || sc.nc; o.ur = o.ur || sc.ur; o.n = o.nc ? null : o.n + (sc.n || 0)
        for (const gr of sc.groups) o.groups.push({ ...gr, h: gr.h ? `${x.name} · ${gr.h}` : x.name })
      }
    }
    return [...by.values()]
  }
  function lineAggCell (g, where, ai, place) {
    const k = g.kind; const key = `aws:g:${k}:${where}`; const ids = g.members.map((x) => x.id)
    const l1 = `${SVC[k]} ×${g.members.length}`
    for (const x of g.members) memReg(key, x) // 목록 줄 → 그 낱개 정보창(§17.11 · 검사 162) — 낱개 칸이 없으니 `B.det` 이 아니라 멤버 표에
    B.det.set(key, () => ({ title: l1, sub: `선이 이어지지 않은 ${SVC[k]} ${g.members.length}개를 하나로 묶었습니다 — 선이 이어진 것은 따로 서 있습니다`,
      rows: [['위치', place], ['계정', acctName(ai)]],
      // CloudFront 줄 = 콘솔이 배포를 구분하는 「설명」 먼저(🔴 비교정리 C R-13 — 「CloudFront ×7」 목록이 도메인뿐이라 어느 배포인지 몰랐다) · 도메인은 아래 작은 글씨
      members: g.members.map((x) => { const cm = k === 'cf' && x.n.con && x.n.con.comment
        return { name: cm || x.name, sub: [cm ? x.name : '', x.state && stateKo(x.state), natId(x.n)].filter(Boolean).join(' · '), key: x.id } }), secs: [...mergeSecs(g.members), ...relSec(key)] }))
    B.cellKind.set(key, k); B.cellL.set(key, l1); B.cellIds.set(key, ids); for (const id of ids) B.cellOf.set(id, key)
    B.agg.set(key, { acct: ai, kind: k, ids })
    return { key, kind: k, acct: ai, l1, l2: '', agg: g.members.length, subs: [], ids }
  }
  function vpceCell (v, list) {
    if (list.length === 1) return cellMake(list[0])
    const key = `aws:g:vpce:${v.id}`
    for (const x of list) memReg(key, x)
    const nI = list.filter((x) => x.epType === 'Interface').length; const nG = list.filter((x) => x.epType === 'Gateway').length
    B.det.set(key, () => ({ title: `${SVC.vpce} ×${list.length}`, sub: `인터페이스 ${nI} · 게이트웨이 ${nG}`, rows: [['VPC', v.name], ['계정', acctName(v.acct)]],
      members: list.map((x) => ({ name: x.svc, sub: `${x.epType === 'Gateway' ? '게이트웨이' : '인터페이스'} · ${natId(x.n)}${x.subnets.length ? ' · ' + x.subnets.map(subnetName).join(', ') : x.epType === 'Gateway' ? '' : ' · 서브넷 없음'}`, key: x.id })),   // 개수만 적으면 엔드포인트가 선 서브넷을 이 화면 어디서도 못 본다
      secs: [...(list.some((x) => Array.isArray(x.n.epUnres)) ? [epSec({ n: {} }, key, list)] : []), ...lbInSec({}, key), ...relSec(key)] }))
    return { key, kind: 'vpce', acct: v.acct, l1: SVC.vpce, l2: `×${list.length}`, agg: list.length, subs: [], ids: list.map((x) => x.id) }
  }
  const subSort = (a, b) => (a.tier === b.tier ? 0 : a.tier === 'public' ? -1 : 1) || a.name.localeCompare(b.name)
  function subnetCells (s) {
    const its = s.items.filter((x) => x.subnets.length === 1 && x.kind !== 'vpce' && x.kind !== 'brk' && x.kind !== 'igw')
    const rest = its.filter((x) => x.kind !== 'nat').sort((a, b) => ORDER[a.kind] - ORDER[b.kind] || a.name.localeCompare(b.name))
    const out = []; const byK = new Map(); for (const x of rest) push(byK, x.kind, x)
    for (const [k, g] of byK) { if (g.length > AGG_T && ['ec2', 'ecs', 'rds'].includes(k)) out.push(aggCell(g, s.id)); else for (const x of g) out.push(cellMake(x)) }
    for (const x of its.filter((x) => x.kind === 'nat')) out.push(cellMake(x)) // NAT 은 맨 끝 — 옆 서브넷에서 오는 선이 다른 아이콘을 안 지나게
    return out
  }
  const iconL = (c) => c.x + CW / 2 - IC / 2 - 1; const iconR = (c) => c.x + CW / 2 + IC / 2 + 1; const iconY = (c) => c.y + 2 + IC / 2
  const kindOfTarget = (tk) => (M.ports.has(tk) ? 'port' : M.pp.has(tk) ? 'pcx' : B.cellKind.get(tk) || '')
  // ENI 주인으로 가는 라우트(2.8.0 · P2-2 — NVA·NAT 인스턴스·어플라이언스) = 그 자원 종류 + 「(ENI)」
  const whyOf = (tk) => (M.ports.has(tk) && M.ports.get(tk).vgw ? '가상 프라이빗 게이트웨이(VGW)' : M.ports.has(tk) && M.ports.get(tk).cwan ? 'Cloud WAN 코어 네트워크' : 0) || ({ port: 'Transit Gateway', pcx: 'VPC 피어링', igw: 'Internet Gateway', nat: 'NAT Gateway', vpce: 'VPC Endpoint(게이트웨이)' }[kindOfTarget(tk)] ||
    (TYPE_KO[kindOfTarget(tk)] ? `${TYPE_KO[kindOfTarget(tk)]}(ENI)` : '?'))
  const ppPeer = (P) => (M.vpcs.has(P.other) ? 'VPC ' + M.vpcs.get(P.other).name : 'VPC · 수집 범위 밖')
  const tgtName = (tk) => (M.ports.has(tk) ? hubName(M.ports.get(tk).tgw) : M.pp.has(tk) ? `VPC 피어링 → ${ppPeer(M.pp.get(tk))}` : B.cellL.get(tk) || tk)
  const rdet = (sid, tk, from) => () => ({ title: '라우트로 나가는 길', sub: whyOf(tk), rows: [...(from ? [['출발', from]] : []), ['서브넷', subnetName(sid)], ['대상', tgtName(tk)]],
    secs: [sec('이 길의 라우트', M.subnets.get(sid).routes.filter((r) => r.t && (r.t === tk || (M.ports.has(tk) && r.t === M.ports.get(tk).tgw) || (M.pp.has(tk) && r.t === M.pp.get(tk).pcx) || (B.cellIds.get(tk) || []).includes(r.t))).map((r) => ({ a: dstWord(M.subnets.get(sid), r.d) || '(목적지 미상)', b: nodeName(r.t), cls: r.bh ? 'bh' : '' })))] })
  const tgtOf = (v, t, epCell) => {
    const ty = (X.mById.get(t) || {}).type
    if (ty === 'transit_gateway' || ty === 'vpn_gateway' || ty === 'core_network') return M.portOf.get(v.id + '|' + t) || null
    if (ty === 'vpc_endpoint') return epCell && epCell.ids.includes(t) ? epCell.key : null
    if (ty === 'vpc_peering_connection') return M.ppOf.get(v.id + '|' + t) || null
    return null
  }

  function layoutVpc (v) {
    B.det.set(v.id, () => detVpc(v)); for (const s of v.subnets) B.det.set(s.id, () => detSub(s))
    const Lo = { v, azb: [], subs: [], cells: [], right: [], ports: [], pports: [], lines: [], igw: null }
    const igwIt = v.items.find((x) => x.kind === 'igw')
    const byAz = new Map(); for (const s of v.subnets) push(byAz, s.az, s)
    const azs = [...byAz.keys()].sort(); for (const a of azs) byAz.get(a).sort(subSort)
    const vpces = v.items.filter((x) => x.kind === 'vpce'); const epCell = vpces.length ? vpceCell(v, vpces) : null
    const portKeys = v.ports.map((t) => M.portOf.get(v.id + '|' + t))
    const rt = portKeys.slice()
    if (epCell && v.subnets.some((s) => [...s.exits].some((t) => epCell.ids.includes(t)))) rt.push(epCell.key)
    // 피어링 포트 차선 — TGW 포트 차선 오른쪽. 아래쪽 변의 포트로 내려간다(§17.1)
    const ppKeys = [...new Set(v.subnets.flatMap((s) => [...s.exits].map((t) => (X.mById.get(t) || {}).type === 'vpc_peering_connection' && M.ppOf.get(v.id + '|' + t)).filter(Boolean)))].sort()
    rt.push(...ppKeys)
    const lbsMulti = v.items.filter((x) => x.kind === 'lb' && x.subnets.length !== 1).sort((a, b) => b.internet - a.internet || a.name.localeCompare(b.name))
    const multiW = v.items.filter((x) => WL.includes(x.kind) && x.subnets.length !== 1)
    // Bedrock 엔드포인트(§17.32) — 일반 엔드포인트와 같은 오른쪽 열 · 묶음 바로 위(엔드포인트끼리 붙어 선다)
    const brks = v.items.filter((x) => x.kind === 'brk').sort((a, b) => a.name.localeCompare(b.name) || (a.id < b.id ? -1 : 1))
    const rightCells = [...lbsMulti.map((x) => cellMake(x)), ...multiW.map((x) => cellMake(x)), ...brks.map((x) => cellMake(x)), ...(epCell ? [epCell] : [])]
    const feeds = igwIt ? lbsMulti.filter((x) => x.internet && x.subnets.some((s) => M.subnets.get(s).exits.has(igwIt.id))) : []
    const GL = igwIt ? 72 : 16; const GX = GL - 14
    const C = { Lo, igwIt, igwKey: igwIt ? igwIt.id : null, azs, byAz, rt, epCell, rightCells, feeds, GL, GX, nLanes: rt.length + feeds.length, portKeys, ppKeys }
    const L = O.sn ? layoutSub(v, C) : layoutFlat(v, C)
    inetCell(v, L)
    return L
  }
  // 인터넷 칸(§17.24 · 검사 137) — 이 VPC 의 서브넷이 기본 라우트를 IGW 로 보낼 때만, IGW 왼쪽 바깥에 짧은 가로선으로.
  //   수집한 자원이 아니라 이 도구가 그린 칸이다(어댑터는 노드를 만들지 않는다 · 정보창이 그렇게 말한다)
  function inetCell (v, Lo) {
    Lo.padL = 0
    const g = Lo.igw; const dm = g && M.dflt.get(g.key); if (!dm) return
    const key = 'aws:inet:' + g.key; const igwN = B.cellL.get(g.key) || g.key
    Lo.inet = { key, kind: 'inet', acct: v.acct, l1: SVC.inet, l2: '', subs: [], ids: [], synth: true, cx: -INET_DX, cy: g.cy }
    B.cellKind.set(key, 'inet'); B.cellL.set(key, SVC.inet); B.cellIds.set(key, []); B.inet.set(key, { acct: v.acct, igw: g.key })
    const subs = [...dm.keys()].sort((a, b) => subnetName(a).localeCompare(subnetName(b)) || (a < b ? -1 : 1))
    const dst = (sid) => [...dm.get(sid)].sort().join(' · ') || '기본 라우트'
    B.det.set(key, () => ({ title: SVC.inet, sub: '이 도구가 그린 칸입니다 — 수집한 리소스가 아닙니다', rows: [['IGW', igwN], ['VPC', v.name], ['계정', acctName(v.acct)]],
      secs: [sec('기본 라우트를 보내는 서브넷', subs.map((sid) => ({ a: dst(sid), b: subnetName(sid), tk: [sid] })))] }))
    Lo.lines.push({ a: g.key, b: key, kind: 'inet', arrow: false, pts: [[-IC / 2, g.cy], [-INET_DX + IC / 2, g.cy]],
      det: () => ({ title: 'Internet Gateway ↔ 인터넷', sub: '이 VPC 의 기본 라우팅이 이 IGW 로 향합니다', rows: [['IGW', igwN], ['서브넷', subs.map(subnetName).join('\n')]], secs: [] }) })
    Lo.padL = INET_PAD
  }
  function finishVpc (v, C, FR, bottom, empty) {
    const { Lo, rightCells, nLanes } = C
    // 위 변 포트끼리는 20px(포트 원 r=9 — 10px 이면 TGW 둘·TGW+VGW 가 겹친다 · 검사 123). 포트 하나인 VPC 는 좌표 그대로
    const nP = C.portKeys.length
    const laneX = (k) => FR + 16 + 20 * Math.min(k, Math.max(nP - 1, 0)) + 10 * Math.max(0, k - Math.max(nP - 1, 0))
    const rightX = nLanes ? laneX(nLanes - 1) + 14 : FR + 12
    let ry = VPC_HDR + 14
    for (const c of rightCells) { c.x = rightX; c.y = ry; ry += CH + 8; Lo.right.push(c); Lo.cells.push(c) }
    let w = rightCells.length ? rightX + CW + 8 : nLanes ? laneX(nLanes - 1) + 16 : FR + 14
    let h = Math.max(bottom, ry - 8) + 14
    if (empty && !rightCells.length) { w = Math.max(w, 230); h = VPC_HDR + (C.igwIt ? 72 : 46); Lo.empty = empty }
    Lo.w = Math.max(w, 230); Lo.h = h; Lo.nameW = (v.ports.length ? laneX(0) - 14 : Lo.w - 12) - 38 - 18
    C.portKeys.forEach((pk, i) => Lo.ports.push({ key: pk, tgw: v.ports[i], x: laneX(i), y: 0 }))
    for (const pk of C.ppKeys) Lo.pports.push({ key: pk, x: laneX(C.rt.indexOf(pk)), y: Lo.h })
    return laneX
  }
  function feedLines (C, laneX) {
    const { Lo, feeds, rt } = C
    feeds.forEach((lb, j) => {
      const c = Lo.right.find((x) => x.key === lb.id); const Xl = laneX(rt.length + j); const top = VPC_HDR + 6
      Lo.lines.push({ a: Lo.igw.key, b: c.key, kind: 'route', arrow: true, pts: [[8, Lo.igw.cy - 10], [8, top], [Xl, top], [Xl, iconY(c)], [iconL(c), iconY(c)]],
        det: () => ({ title: '인터넷 → 로드밸런서', sub: '인터넷용 LB 의 서브넷이 이 IGW 로 나갑니다', rows: [['서브넷', lb.subnets.map(subnetName).join('\n')]], secs: [] }) })
    })
  }
  // IGW 도 칸이다 — 빼면 범례에 IGW 줄이 없고 서브넷 → IGW 선 정보창이 대상 이름 대신 원 id 를 쓴다(#45 리뷰)
  const regCells = (Lo) => { for (const c of Lo.igw ? [...Lo.cells, Lo.igw] : Lo.cells) { B.cellKind.set(c.key, c.kind); B.cellL.set(c.key, `${c.l1} ${c.l2}`.trim()); B.cellIds.set(c.key, c.ids); for (const id of c.ids) B.cellOf.set(id, c.key) } }

  function layoutSub (v, C) {
    const { Lo, igwIt, azs, byAz, rt, epCell, GL, GX } = C
    const azHdr = O.az ? AZ_HDR : 0
    const meas = new Map()
    for (const s of v.subnets) { const cells = subnetCells(s); const nc = Math.min(cells.length, 3); const nr = Math.ceil(cells.length / 3); meas.set(s.id, { cells, w: Math.max(168, 12 + nc * CW), h: cells.length ? SUB_HDR + nr * CH + 2 : 44 }) }
    const ncol = Math.max(0, ...azs.map((a) => byAz.get(a).length))
    const colW = [...Array(ncol)].map((_, c) => Math.max(...azs.map((a) => (byAz.get(a)[c] ? meas.get(byAz.get(a)[c].id).w : 0))))
    const colX = (c) => colW.slice(0, c).reduce((p, q) => p + q, 0) + c * SGAP
    const azW = SP * 2 + colX(ncol) - (ncol ? SGAP : 0)
    const padB = 10 + 5 * (rt.length + 1)
    let y = VPC_HDR + 14
    const subPos = new Map(); const rows = []
    for (const [ri, a] of azs.entries()) {
      const list = byAz.get(a); const rh = Math.max(...list.map((s) => meas.get(s.id).h))
      const box = { x: GL, y, w: azW, h: azHdr + rh + padB, az: a }; rows.push(box)
      list.forEach((s, c) => {
        const m = meas.get(s.id); const sb = { s, x: GL + SP + colX(c), y: y + azHdr, w: colW[c], h: rh, col: c, row: ri, last: c === list.length - 1, bottom: y + azHdr + rh }
        m.cells.forEach((cell, i) => { cell.x = sb.x + 6 + (i % 3) * CW; cell.y = sb.y + SUB_HDR + Math.floor(i / 3) * CH; Lo.cells.push(cell) })
        sb.cells = m.cells; Lo.subs.push(sb); subPos.set(s.id, sb)
      })
      y += box.h + 10
    }
    if (O.az) Lo.azb = rows
    const laneX = finishVpc(v, C, GL + azW, azs.length ? y - 10 : VPC_HDR + 14, !azs.length && '서브넷 없음')
    if (igwIt) {
      const fp = Lo.subs.filter((s) => s.s.tier === 'public').sort((a, b) => a.row - b.row || a.col - b.col)[0]
      const iy = fp ? fp.y + 14 : rows[0] ? rows[0].y + azHdr + 14 : VPC_HDR + 26
      Lo.igw = { ...cellMake(igwIt), cx: 0, cy: iy }
    }
    fitIcons([...Lo.cells, Lo.igw])
    regCells(Lo)
    const add = (sid, tid, a, b, pts, det) => Lo.lines.push({ a, b, kind: 'route', arrow: true, pts, det, sub: sid, tid })
    for (const sb of Lo.subs) {
      const s = sb.s
      for (const t of [...s.exits].sort()) {
        if (Lo.igw && t === Lo.igw.key) {
          const iy = Lo.igw.cy; const yy = sb.y + 14
          const pts = sb.col === 0 ? (yy === iy ? [[sb.x, yy], [IC / 2 - 4, iy]] : [[sb.x, yy], [GX, yy], [GX, iy], [IC / 2 - 4, iy]])
            : [[sb.x + 10, sb.bottom], [sb.x + 10, sb.bottom + 6], [GX, sb.bottom + 6], [GX, iy], [IC / 2 - 4, iy]]
          add(s.id, t, s.id, Lo.igw.key, pts, rdet(s.id, Lo.igw.key))
        } else if (M.items.has(t) && M.items.get(t).kind === 'nat' && M.items.get(t).vpc === v.id) {
          const ck = B.cellOf.get(t); const c = ck && Lo.cells.find((x) => x.key === ck); if (!c) continue
          const ns = subPos.get(M.items.get(t).subnets[0]); const ny = iconY(c)
          let pts
          if (ns && ns.s.id === s.id) continue // 자기 서브넷의 NAT 으로 가는 라우트는 없다(있으면 정보창)
          if (ns && ns.row === sb.row && ns.col === sb.col - 1) pts = [[sb.x, ny], [iconR(c), ny]]
          else if (ns && ns.col < sb.col) pts = [[sb.x, sb.y + 14], [sb.x - SGAP / 2, sb.y + 14], [sb.x - SGAP / 2, ny], [iconR(c), ny]]
          else pts = [[sb.x + sb.w, sb.y + 14], [sb.x + sb.w + SGAP / 2, sb.y + 14], [sb.x + sb.w + SGAP / 2, ny], [iconL(c), ny]]
          add(s.id, t, s.id, c.key, pts, rdet(s.id, c.key))
        } else {
          const tk = tgtOf(v, t, epCell); if (!tk) continue
          const k = rt.indexOf(tk); if (k < 0) continue
          if (Lo.lines.some((l) => l.a === s.id && l.b === tk)) { Lo.lines.find((l) => l.a === s.id && l.b === tk).extra = (Lo.lines.find((l) => l.a === s.id && l.b === tk).extra || []).concat(t); continue }
          const Xl = laneX(k)
          const head = sb.last ? [[sb.x + sb.w, sb.y + 10 + 6 * k], [Xl, sb.y + 10 + 6 * k]]
            : [[sb.x + sb.w - 10 - 6 * k, sb.bottom], [sb.x + sb.w - 10 - 6 * k, sb.bottom + 6 + 5 * (k + 1)], [Xl, sb.bottom + 6 + 5 * (k + 1)]]
          const tail = M.ports.has(tk) ? [[Xl, 10]] : M.pp.has(tk) ? [[Xl, Lo.h - 10]] : [[Xl, iconY(epCell)], [iconL(epCell), iconY(epCell)]]
          add(s.id, t, s.id, tk, [...head, ...tail], rdet(s.id, tk))
        }
      }
    }
    feedLines(C, laneX)
    return Lo
  }

  function layoutFlat (v, C) {
    const { Lo, igwIt, igwKey, azs, byAz, rt, epCell, GL, GX } = C
    const azHdr = O.az ? AZ_HDR : 0
    const rowsIn = azs.map((a) => {
      const nats = []; const rest = []; for (const s of byAz.get(a)) for (const c of subnetCells(s)) (c.kind === 'nat' ? nats : rest).push(c)
      const sub = []; let cur = null
      for (const c of [...nats, ...rest]) { if (!cur || cur.length >= ROWN || (c.kind === 'nat' && cur.length)) sub.push((cur = [])); cur.push(c) }
      return { az: a, sub }
    })
    const natCells = rowsIn.flatMap((r) => r.sub.flat()).filter((c) => c.kind === 'nat')
    const NX = (j) => GL + 2 + 8 * j
    const FL = GL + 8 * natCells.length + 6
    const maxN = Math.max(0, ...rowsIn.flatMap((r) => r.sub.map((s) => s.length)))
    const azW = Math.max(168, SP * 2 + maxN * CW)
    const tOrder = [...(igwKey ? [igwKey] : []), ...natCells.map((c) => c.key), ...rt]
    const cellTargets = (c) => {
      if (c.subs.length !== 1) return []
      const s = M.subnets.get(c.subs[0]); const out = new Map()
      for (const t of [...s.exits].sort()) {
        let k = null
        if (igwIt && t === igwIt.id) k = igwKey
        else if (natCells.some((n) => n.key === t)) k = t
        else { const tk = tgtOf(v, t, epCell); k = tk && rt.includes(tk) ? tk : null }
        if (k && k !== c.key && !out.has(k)) out.set(k, t)
      }
      return [...out.keys()].sort((a, b) => tOrder.indexOf(a) - tOrder.indexOf(b)).map((k) => ({ k, t: out.get(k) }))
    }
    let y = VPC_HDR + 14
    const rows = []; const subRows = []
    for (const r of rowsIn) {
      if (!r.sub.length && !O.az) continue
      const top = y; let yy = y + azHdr + 4
      for (const cells of r.sub) {
        for (const c of cells) c.tg = cellTargets(c)
        const used = [...new Set(cells.flatMap((c) => c.tg.map((x) => x.k)))].sort((a, b) => tOrder.indexOf(a) - tOrder.indexOf(b))
        cells.forEach((c, i) => { c.x = FL + SP + i * CW; c.y = yy; Lo.cells.push(c) })
        const bot = yy + CH; subRows.push({ cells, used, bot })
        yy = bot + (used.length ? 5 * used.length + 9 : 4)
      }
      const h = r.sub.length ? yy - top + 2 : AZ_HDR + 14
      rows.push({ x: FL, y: top, w: azW, h, az: r.az }); y = top + h + 10
    }
    if (O.az) Lo.azb = rows
    const laneX = finishVpc(v, C, FL + azW, rows.length ? y - 10 : VPC_HDR + 14, !rows.length && '표시할 리소스 없음')
    const isPub = (c) => c.subs.length === 1 && M.subnets.get(c.subs[0]).tier === 'public'
    let iy = VPC_HDR + 26
    if (igwIt) {
      const fr = subRows.find((sr) => sr.cells.some(isPub))
      iy = fr ? iconY(fr.cells[0]) : rows[0] ? rows[0].y + azHdr + 14 : VPC_HDR + 26
      Lo.igw = { ...cellMake(igwIt), cx: 0, cy: iy }
    }
    fitIcons([...Lo.cells, Lo.igw])
    regCells(Lo)
    for (const sr of subRows) {
      for (const c of sr.cells) {
        const m = c.tg.length; const cx = c.x + CW / 2; const y0 = iconY(c) + IC / 2 + (c.l2 ? 28 : 18)
        c.tg.forEach(({ k: tk, t }, i) => {
          const sx = cx + (i - (m - 1) / 2) * 6; const ly = sr.bot + 6 + 5 * sr.used.indexOf(tk)
          const j = natCells.findIndex((n) => n.key === tk)
          let tail
          if (tk === igwKey) tail = [[GX, ly], [GX, iy], [IC / 2 - 4, iy]]
          else if (j >= 0) { const n = natCells[j]; const ny = iconY(n) + 8; tail = [[NX(j), ly], [NX(j), ny], [iconL(n), ny]] }
          else { const Xl = laneX(rt.indexOf(tk)); tail = M.ports.has(tk) ? [[Xl, ly], [Xl, 10]] : M.pp.has(tk) ? [[Xl, ly], [Xl, Lo.h - 10]] : [[Xl, ly], [Xl, iconY(epCell)], [iconL(epCell), iconY(epCell)]] }
          Lo.lines.push({ a: c.key, b: tk, kind: 'route', arrow: true, pts: [[sx, y0], [sx, ly], ...tail], det: rdet(c.subs[0], tk, `${c.l1} ${c.l2}`.trim()), sub: c.subs[0], tid: t })
        })
      }
    }
    feedLines(C, laneX)
    return Lo
  }

  // 전역 줄 → VPC 안 선(CloudFront 원본 · Route 53 별칭 · Global Accelerator · Cloud WAN)이 닿는 리전 — Cloud WAN 은 아이콘이 아니라 VPC 위 변 포트로 간다(2.11.0 · §17.39)
  const cfoRegion = (p) => (p.cwan ? (M.vpcs.get((M.ports.get(p.lb) || {}).vpc) || {}).region : (M.items.get(p.lb) || {}).region)
  // cap = 리전 자원 줄의 폭 상한(§17.1 「줄 묶음」 · 아래 `layoutRegs`) · pre = 같은 리전의 1차 배치(VPC 배치를 다시 하지 않는다 — 결과는 같다)
  function layoutRegion (ai, rn, cap = MAXW + 36, pre = null) {
    const peered = new Set([...M.peers.flatMap((p) => [p.a, p.b]), ...M.xpeers.map((p) => p.a)])
    const vs = [...M.vpcs.values()].filter((v) => v.region === rn.id)
    const lays = pre ? pre.lays : vs.map(layoutVpc).sort((a, b) => (b.v.ports.length > 0) - (a.v.ports.length > 0) || peered.has(a.v.id) - peered.has(b.v.id) || b.h - a.h || a.v.name.localeCompare(b.v.name))
    const hubs = [...M.hubs.values()].filter((h) => h.region === rn.id)
    const R = { acct: ai, rn, region: rn.id, lays, hub: [], rows: [] }
    // VPN 선(§17.1) = 아이콘 위 → 리전 줄 위 제 차선(5px 간격) → 상대 아이콘 위. 차선이 있는 리전만 리전 줄을 내린다
    //   (VPN 없는 화면은 바이트 그대로) · 차선은 DX 게이트웨이 차선(REG_HDR+9+3i) 밑에서 시작한다
    // DX 길(🔁 2026-10-26 · DX 연결 → DXGW · DXGW → VGW)도 리전 머리 밑 DX 차선(TGW ↔ DXGW 다음 번호)을 쓴다 — 이 리전에서 출발하는 것만 센다(없으면 바이트 그대로)
    R.dxlN = M.dxl.filter((p) => { const h = M.hubs.get(p.hub); const g = M.globs.get(p.dxgw); return h && g && h.acct === g.acct && h.region === rn.id }).length
    R.vpnBase = REG_HDR + Math.max(14, 12 + 3 * (M.dxtgw.length + R.dxlN))
    R.vpnN = M.vpnl.filter((l) => (M.hubs.get(l.a) || {}).region === rn.id).length
    const hubTop = R.vpnN ? R.vpnBase + 5 * (R.vpnN - 1) + 9 : R.dxlN ? REG_HDR + 14 + 3 * (M.dxtgw.length + R.dxlN) : REG_HDR + 14
    R.hubTop = hubTop
    // 어태치먼트 차선(§17.1 · #75) — 선마다 제 가로 차선. 들어오는 선(다른 계정·리전 VPC → 이 리전 TGW) · 이 리전 선 · 나가는 선
    //   (이 리전 VPC → 다른 리전 상자의 TGW) 을 한 묶음으로 센다. 셋 이하면 맨 아래 차선 = 옛 채널(hubTop + HH + 16) 그대로 · 맨 위 = TGW 줄 밑 8px(그 위 4px 는 CloudFront 원본 선 자리) · 넷부터 VPC 를 4px 씩 내린다
    const hubOf = (id) => M.hubs.get(id)
    const vin0 = new Set(lays.map((L) => L.v.id))
    const aIn = [...M.ports.values()].filter((P) => !vin0.has(P.vpc) && (hubOf(P.tgw) || {}).region === rn.id)
    const aLoc = lays.flatMap((L) => L.ports.filter((p) => (hubOf(p.tgw) || {}).region === rn.id).map((p) => ({ L, p })))
    const aOut = lays.flatMap((L) => L.ports.filter((p) => hubOf(p.tgw) && hubOf(p.tgw).region !== rn.id).map((p) => ({ L, p })))
    const nA = aIn.length + aLoc.length + aOut.length
    const aBase0 = hubs.length ? Math.max(hubTop + HH + 8, hubTop + HH + 16 - 4 * (nA - 1)) : REG_HDR + 8
    const y00 = hubs.length ? Math.max(hubTop + HH + 34, aBase0 + 4 * (nA - 1) + 18) : nA ? Math.max(REG_HDR + 18, aBase0 + 4 * (nA - 1) + 18) : REG_HDR + 18
    // CloudFront 원본·Route 53 별칭 선 차선(§17.31 · 2.8.0) — 맨 윗줄 VPC 위 틈(어태치먼트 차선 위 · 리전 줄 아이콘 밑)에 이 리전으로 오는 선마다 4px.
    //   모자라면 그만큼 VPC 를 내린다(어태치먼트 「넷부터 4px」와 같은 처분 · 모자라지 않으면 바이트 그대로) — 🔴 lbt 픽스처 2.8.0 실측: 다섯째 선이
    //   차선이 없어 리전 머리 차선으로 올라갔고 VPC 오른쪽으로 내려오다 VGW 아이콘을 지났다(검사 130)
    //   바닥 = 리전 줄 아이콘 밑 · LB → 리전 줄 자원(Lambda) 선이 있으면 그 차선 묶음 밑(`lbtLines` 「hub」 = 아이콘 밑 6px · 선마다 3px) + 4px
    const nC = hubs.length ? M.cfo.filter((p) => cfoRegion(p) === rn.id).length : 0
    const nH = M.lbt.filter((p) => p.id && (M.hubs.get(p.id) || {}).region === rn.id).length + M.vlk.filter((p) => (M.hubs.get(p.api) || {}).region === rn.id).length +
      M.ltg.filter((p) => (M.hubs.get(p.svc) || {}).region === rn.id).length + M.wfl.filter((p) => (M.hubs.get(p.waf) || {}).region === rn.id).length
    const cFloor = nH ? hubTop + IC + 6 + 3 * (nH - 1) + 4 : hubTop + HH + 2
    const cX = nC ? Math.max(0, cFloor - ((nA > 1 ? aBase0 - 4 : y00 - 22) - 4 * (nC - 1))) : 0
    const aBase = aBase0 + cX; const y0 = y00 + cX
    const GYV = 40; const placed = []
    for (const L of lays) {
      let best = null
      // 인터넷 칸이 있는 VPC 는 왼쪽 INET_PAD 까지 차지한다(§17.24) — 리전 틀 안 · 옆 VPC 와 GXV 띄움
      const x0 = Math.max(X0, 16 + L.padL)
      for (const x of [x0, ...placed.map((r) => r.X + r.w + GXV + L.padL)]) {
        if (x > x0 && x + L.w > MAXW) continue
        const y = Math.max(y0, ...placed.filter((r) => r.X - r.padL < x + L.w + GXV && x - L.padL < r.X + r.w + GXV).map((r) => r.Y + r.h + GYV))
        if (!best || y < best.y || (y === best.y && x < best.x)) best = { x, y }
      }
      L.X = best.x; L.Y = best.y; placed.push(L)
    }
    const bottom = Math.max(y0, ...lays.map((L) => L.Y + L.h))
    R.contentBottom = bottom
    // 피어링 점선 = 점선마다 제 가로 차선(§17.1 · 한 차선을 같이 타면 두 피어링이 한 줄로 포개진다) → 차선 수만큼 리전 상자를 늘린다
    const vin = new Set(lays.map((L) => L.v.id)); R.peerN = M.peers.filter((p) => vin.has(p.a) || vin.has(p.b)).length; const PL = PEER_LANE * Math.max(0, R.peerN - 1)
    R.peerPL = PL
    R.w = Math.max(Math.max(0, ...lays.map((L) => L.X + L.w)) + 36, 420); R.h = bottom + 40 + PL
    // 범위 밖 피어 VPC(§17.1) — 리전 상자 밑줄. 범위 안 VPC 가운데 밑에서 시작해 겹치지 않게 오른쪽으로 민다
    R.xp = []; let xe = -Infinity
    for (const p of M.xpeers.map((p) => ({ p, L: lays.find((L) => L.v.id === p.a) })).filter((x) => x.L).sort((a, b) => a.L.X + a.L.w / 2 - (b.L.X + b.L.w / 2) || (a.p.pcx < b.p.pcx ? -1 : 1))) {
      const hx = Math.max(Math.round(p.L.X + p.L.w / 2 - IC / 2), xe + 16); xe = hx + HW
      R.xp.push({ ...cellMake(p.p.it, 'right'), hx, hy: bottom + XP_GAP + PL, xp: p.p })
    }
    if (R.xp.length) { R.h = bottom + XP_GAP + PL + IC + 2 + 26; R.w = Math.max(R.w, xe + 20) }
    R.empty = !lays.length && !hubs.length
    if (R.empty) R.h = REG_HDR + 44
    R.base = R.w   // 자원 줄을 빼고 잰 폭(VPC · 범위 밖 칸) — 계정의 리전들이 이것의 최댓값으로 자원 줄 상한을 맞춘다(`layoutRegs`)
    let hx = 250
    // 줄 묶음(§17.1 · 사용자 결정 2026-10-23): 선이 닿지 않는 같은 종류가 LINE_T 개 이상이면 그 종류의 첫 멤버 자리에 `<종류> ×N` 한 칸.
    //   선이 닿지 않는 칸(묶음 · 낱개)이 상한 `cap` 을 넘기면 줄에 세우지 않고 리전 상자 맨 아래 격자(`over`)로 보낸다 —
    //   선이 닿는 칸은 넘겨도 줄에 선다(선의 길이 그 자리를 전제한다 · 리전이 그만큼 넓어진다 = 지금까지와 같다)
    const lg = lineGroups(hubs, (h) => LINE_HUB.has(h.kind) || !!h.regional)
    const over = []
    const seq = (h) => {
      const g = lg.of.get(h.id); if (g && g.members[0] !== h) return
      const c = g ? lineAggCell(g, R.region, ai, `리전 자원 줄 · ${regName(R.region)}`) : cellMake(h, 'right')
      if ((g || lg.free(h)) && hx + HW + 36 > cap) { over.push(c); return }
      R.hub.push({ ...c, hx, hy: hubTop }); hx += HW + 16 }
    for (const h of hubs.filter((h) => h.kind === 'dxcon')) seq(h)
    // VPN 연결·고객 게이트웨이 = 줄 왼쪽(DX 연결 다음 · TGW 앞). 줄 오른쪽 끝은 VPC 오른쪽 변으로 내려가는 CloudFront 원본 선 자리라
    //   거기 두면 이름표가 그 선을 막는다(30계정 fixture 의 홀로 선 CGW · 검사 130 실측 2026-10-06). TGW 는 제 포트 가운데라 밀리지 않는다
    for (const h of hubs.filter((h) => h.kind === 'vpn')) seq(h)
    for (const h of hubs.filter((h) => h.kind === 'cgw')) seq(h)
    // TGW 다음 VGW — 둘 다 제 포트(VPC 위 변) 가운데 위를 노린다 · 앞 허브에 밀리면 오른쪽(어태치먼트 선이 꺾인다 — 여러 VPC 의 TGW 와 같다)
    // VPC Lattice 서비스 네트워크(2.11.0 · §17.40)도 같다 — 붙은 VPC 포트 가운데 위를 노린다
    for (const t of [...hubs.filter((h) => h.kind === 'tgw' && !h.ghost), ...hubs.filter((h) => h.kind === 'vgw'), ...hubs.filter((h) => h.kind === 'lsn')]) {
      const px = lays.flatMap((L) => L.ports.filter((p) => p.tgw === t.id).map((p) => L.X + p.x))
      // 포트 평균은 1/3 px 같은 소수가 된다 → 정수로 올림(소수 좌표는 아이콘 테두리를 흐리게 그린다 · 검사 4 ·
      //   반올림보다 올림이 겹쳐달림을 덜 만든다 — 30계정 수평 15150 vs 15120 실측 2026-10-03)
      let cx = px.length ? Math.ceil(px.reduce((a, b) => a + b, 0) / px.length) : hx + IC / 2
      cx = Math.max(cx, hx + IC / 2); R.hub.push({ ...cellMake(t, 'right'), hx: cx - IC / 2, hy: hubTop }); hx = cx - IC / 2 + HW + 16
    }
    for (const h of hubs.filter((h) => h.kind === 'igw' || h.ghost)) seq(h)
    for (const h of hubs.filter((h) => h.regional)) seq(h)
    // Lambda(§17.27) = 줄의 맨 끝 — LB → 함수 선은 줄 **밑**(아이콘 밑 변)으로 들어온다(이름표는 오른쪽)
    for (const h of hubs.filter((h) => h.kind === 'lambda')) seq(h)
    // WAF 웹 ACL(2.12.0 · §17.41) = API Gateway 바로 앞 — WAF → API 선은 줄 **위** 차선(VPN 선과 같은 길)이라 짧게 · WAF → ALB 선은 API 의 VPC 링크와 같은 길(줄 **밑** → VPC 위 변 → LB)
    for (const h of hubs.filter((h) => h.kind === 'waf')) seq(h)
    // API Gateway(2.10.0 · §17.35) = Lambda 다음(줄 맨 끝) — VPC 링크 선은 Lambda 와 같은 길(줄 **밑** → VPC 위 변 → LB)
    for (const h of hubs.filter((h) => h.kind === 'apigw')) seq(h)
    // VPC Lattice 서비스(2.11.0 · §17.40) = 줄 맨 끝(API Gateway 다음) — 대상 선은 API Gateway VPC 링크와 같은 길(줄 **밑** → VPC 위 변 → 대상)
    for (const h of hubs.filter((h) => h.kind === 'lsvc')) seq(h)
    // 넘친 칸 = 리전 상자 맨 아래(범위 밖 피어 VPC 줄 밑) 격자 — 자원 줄과 같은 x=250 부터 · 줄 간격 = 전역 줄과 같은 60 · 폭 상한 안에서 줄을 바꾼다.
    //   선이 하나도 닿지 않는 칸만 오므로 그 밑으로 지나가는 선이 없다(VPC 밑 피어링 차선 · 범위 밖 칸은 그 위다)
    R.overN = over.length
    if (over.length) {
      const per = Math.max(1, Math.floor((cap - 250 - 20 + 16) / (HW + 16))); const oy = R.h - 6
      over.forEach((c, i) => R.hub.push({ ...c, hx: 250 + (i % per) * (HW + 16), hy: oy + Math.floor(i / per) * GLOB_ROW, over: true }))
      R.h = oy + (Math.ceil(over.length / per) - 1) * GLOB_ROW + HH + 16
    }
    fitIcons([...R.hub, ...R.xp])
    R.w = Math.max(R.w, hx + 20, ...(over.length ? [cap] : []))
    // 차선 · TGW 밑 끝 자리(리전 상대 좌표). 계정이 다르면 나가는 쪽 = 계정 사이 길의 방향(`across` — 앞 계정의 오른쪽 틈)
    const side = (va, ha) => (va === ha || M.accts.indexOf(va) > M.accts.indexOf(ha) ? 'L' : 'R')
    const tcx = new Map(R.hub.map((h) => [h.key, h.hx + IC / 2]))
    const ord = (x, y) => (x < y ? -1 : x > y ? 1 : 0)
    R.attE = new Map()
    for (const [t, cx] of tcx) {
      const es = [...aIn.filter((P) => P.tgw === t).map((P) => ({ k: P.id, o: side(P.acct, hubOf(t).acct) === 'R' ? -Infinity : Infinity })),
        ...aLoc.filter(({ p }) => p.tgw === t).map(({ L, p }) => ({ k: p.key, o: L.X + p.x }))].sort((x, y) => x.o - y.o || ord(x.k, y.k))
      const m = es.length; const st = m <= 9 ? 4 : m <= 17 ? 2 : 1
      es.forEach((e, i) => R.attE.set(e.k, cx + Math.round(st * (i - (m - 1) / 2))))
    }
    const inD = (P) => { const e = R.attE.get(P.id); return side(P.acct, hubOf(P.tgw).acct) === 'R' ? e : R.w - e }
    const outD = ({ L, p }) => (side(L.v.acct, hubOf(p.tgw).acct) === 'R' ? R.w - L.X - p.x : L.X + p.x)
    const locD = ({ L, p }) => Math.abs(L.X + p.x - (R.attE.get(p.key) ?? L.X + p.x))
    const lanes = [...aIn.sort((x, y) => inD(x) - inD(y) || ord(x.id, y.id)).map((P) => P.id),
      ...aLoc.sort((x, y) => locD(y) - locD(x) || ord(x.p.key, y.p.key)).map(({ p }) => p.key),
      ...aOut.sort((x, y) => outD(y) - outD(x) || ord(x.p.key, y.p.key)).map(({ p }) => p.key)]
    R.attY = new Map(lanes.map((k, i) => [k, aBase + 4 * i]))
    R.y0 = y0; R.attTop = aBase; R.attN = nA
    return R
  }

  // ======================================================================== 그림 조각
  const tgS = (fg) => `fill="none" stroke="${fg}" stroke-width="1.3" stroke-linejoin="round"`
  const offG = (k, fill, size = 36) => {
    const o = ICON[k]; if (!o) return `<circle r="${size / 3}" fill="none" stroke="${fill}" stroke-width="1.6"/>`
    const [x, y, w, h] = o.vb === 48 ? [0, 0, 48, 48] : o.bb; const s = size / Math.max(w, h)
    return `<use href="#aw-o-${k}" data-off="${k}" transform="scale(${+s.toFixed(4)}) translate(${P2(-(x + w / 2))},${P2(-(y + h / 2))})" fill="${fill}"/>`
  }
  const offTile = (k, x, y, S, bg, fg) => `<rect x="${x}" y="${y}" width="${S}" height="${S}" fill="${bg}"/><use href="#aw-o-${k}" data-off="${k}" transform="translate(${x},${y}) scale(${+(S / 40).toFixed(4)})" fill="${fg}"/>`
  const TILE = {
    region: (x, y, bg = '#30363d', fg = '#c9d1d9') => offTile('region', x, y, 20, bg, fg),
    vpc: (x, y, bg = COL.vpc, fg = '#fff') => offTile('vpc', x, y, 22, bg, fg),
    lock: (x, y, bg = COL.priv, fg = '#c9d1d9') => offTile('priv', x, y, 17, bg, fg),
    globe: (x, y, bg = COL.pub, fg = '#c9d1d9') => `<rect x="${x}" y="${y}" width="17" height="17" fill="${bg}"/><circle cx="${x + 8.5}" cy="${y + 8.5}" r="5" ${tgS(fg)}/><path d="M${x + 3.5},${y + 8.5}h10M${x + 8.5},${y + 3.5}c-3,3 -3,7 0,10c3,-3 3,-7 0,-10" ${tgS(fg)}/>`,
  }
  const LBK = { ALB: 'alb', NLB: 'nlb', GWLB: 'gwlb', CLB: 'clb' }
  function glyph (kind, cx, cy, col, dim, sub) {
    const st = `stroke="${col}" fill="none" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"`
    const T = (s) => `<g transform="translate(${P2(cx)},${P2(cy)})"${dim === true ? ' opacity=".35"' : ''}>${s}</g>`
    const sq = (k) => T(`<rect x="-18" y="-18" width="36" height="36" fill="${col}"/><use href="#aw-o-${k}" data-off="${k}" transform="scale(.45) translate(-40,-40)" fill="#fff"/>`)
    switch (kind) {
      case 'ec2': case 'ecs': case 'rds': case 'nat': case 'vpce': return T(offG(kind, col))
      // Bedrock 엔드포인트(§17.32) — VPC 엔드포인트 공식 모양 + 오른쪽 위 네 갈래 별(손그림 표시 · Bedrock 공식 경로가 덱 사본에 없다)
      case 'brk': return T(`${offG('vpce', col)}<path d="M12,-18l1.6,4.4 4.4,1.6 -4.4,1.6 -1.6,4.4 -1.6,-4.4 -4.4,-1.6 4.4,-1.6z" fill="${col}" stroke="${BG}" stroke-width="1"/>`)
      case 'eks': case 'sm': return T(offG(kind, col, 30))
      case 'lb': return T(offG(LBK[sub] || 'alb', col))
      case 'igw': return T(`<circle r="19" fill="${BG}"/>${offG('igw', col)}`)
      case 'inet': return T(`<circle r="19" fill="${BG}"/>${offG('inet', col)}`)
      case 'eic': return T(`<g ${st}><rect x="-13" y="-10" width="26" height="20" rx="2"/><path d="M-7,-3l4,3 -4,3M0,4h6"/></g>`)
      // S3 버킷(§17.31 · §17.10 예외 여섯째) — 덱 사본에 경로가 없다 → 손그림 버킷(위 타원 + 아래로 좁아지는 몸통 + 띠 하나)
      case 's3': return T(`<g ${st}><ellipse cx="0" cy="-10" rx="13" ry="4"/><path d="M-13,-10L-10,12C-7,15 7,15 10,12L13,-10"/><path d="M-11.6,1C-6,3.5 6,3.5 11.6,1" stroke-width="1.3"/></g>`)
      // Route 53(§17.30 · §17.10 예외 다섯째) — 덱 사본에 경로가 없다 → 손그림(방패 모양 테 + 「53」 대신 점 셋 = 이름 → 주소)
      case 'r53': return T(`<g ${st}><path d="M0,-15l12,5v8c0,8 -6,13 -12,16c-6,-3 -12,-8 -12,-16v-8z"/><path d="M-6,-1h12M-6,4h8M-6,-6h10" stroke-width="1.8"/></g>`)
      // Lambda(§17.27 · §17.10 예외 넷째) — 덱 사본에 경로가 없다 → 손그림 λ(둥근 네모 + 글자 모양 선)
      case 'lambda': return T(`<g ${st}><rect x="-14" y="-14" width="28" height="28" rx="4"/><path d="M-6,-9h3l9,18h3M-0.5,-1.5l-5.5,10.5" stroke-width="2.2"/></g>`)
      // ElastiCache(§17.32 · §17.10 예외 다섯째) — 덱 사본에 경로가 없다 → 손그림(납작한 원통 셋을 포갠 캐시 + 번개 한 획)
      // Aurora · DB 클러스터 — RDS 공식 모양(같은 서비스) + 점선 테(클러스터 = 인스턴스 묶음의 논리 단위 · §17.32)
      // RDS 프록시(§17.36) — RDS 공식 모양(같은 서비스) + 왼쪽 위 양방향 화살 표시(손그림 · 「연결을 모아 넘긴다」)
      case 'rdsp': return T(`${offG('rds', col, 28)}<path d="M-17,-13h10M-10,-16l3,3 -3,3M-11,-7h-6M-14,-10l-3,3 3,3" fill="none" stroke="${col}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>`)
      // API Gateway(§17.35 · §17.10 예외 여덟째) — 덱 사본에 경로가 없다 → 손그림(문 모양 테 + 안으로 드는 꺾쇠 둘)
      case 'apigw': return T(`<g ${st}><path d="M-14,14v-20a14,14 0 0 1 28,0v20z"/><path d="M-6,0l-5,5 5,5M6,0l5,5 -5,5" stroke-width="1.8"/></g>`)
      // Global Accelerator(§17.37 · §17.10 예외 아홉째) — 덱 사본에 경로가 없다 → 손그림(지구 테 + 빠른 화살 셋)
      case 'ga': return T(`<g ${st}><circle r="13"/><path d="M-13,0h26M0,-13c-6,6 -6,20 0,26M0,-13c6,6 6,20 0,26" stroke-width="1.2"/><path d="M6,-17h12M10,-21l4,4 -4,4" stroke-width="1.8"/></g>`)
      // Cloud WAN(§17.39) · WAF 웹 ACL(§17.41) — 덱 2024 배포본 공식 아키텍처 아이콘 경로(`aws-icons.js` · 사용자 2026-10-27 「아이콘이 이상하다」 → 손그림 철회)
      case 'cwan': case 'waf': return T(offG(kind, col))
      // VPC Lattice 서비스 네트워크(§17.40 · 예외 열한째) — 손그림(육각 테 + 가운데에서 뻗는 셋)
      case 'lsn': return T(`<g ${st}><path d="M0,-15l13,7.5v15l-13,7.5l-13,-7.5v-15z"/><circle r="3"/><path d="M0,-3v-7M2.6,1.5l6,3.5M-2.6,1.5l-6,3.5" stroke-width="1.6"/></g>`)
      // VPC Lattice 서비스(§17.40 · 예외 열두째) — 손그림(둥근 네모 + 안으로 드는 화살)
      case 'lsvc': return T(`<g ${st}><rect x="-13" y="-13" width="26" height="26" rx="7"/><path d="M-7,0h12M1,-4l4,4 -4,4" stroke-width="1.8"/></g>`)
      case 'rdsc': return T(`<rect x="-17" y="-17" width="34" height="34" rx="6" fill="none" stroke="${col}" stroke-width="1.2" stroke-dasharray="3 3"/>${offG('rds', col, 26)}`)
      // OpenSearch(§17.32 · §17.10 예외 여섯째) — 덱 사본에 경로가 없다 → 손그림(돋보기 + 막대 셋)
      case 'os': return T(`<g ${st}><circle cx="-3" cy="-3" r="10"/><path d="M4.5,4.5l9,9M-8,1v-4M-3,1v-8M2,1v-6" stroke-width="1.8"/></g>`)
      // MSK(§17.32 · §17.10 예외 일곱째) — 덱 사본에 경로가 없다 → 손그림(브로커 다섯을 잇는 Kafka 모양)
      case 'msk': return T(`<g ${st}><circle cx="-6" cy="0" r="4.5"/><circle cx="-6" cy="-11" r="3.5"/><circle cx="-6" cy="11" r="3.5"/><circle cx="8" cy="-6" r="3.5"/><circle cx="8" cy="6" r="3.5"/><path d="M-6,-4.5v-3M-6,4.5v3M-2,-2l6.8,-2.6M-2,2l6.8,2.6"/></g>`)
      case 'ec': return T(`<g ${st}><ellipse cx="0" cy="-10" rx="12" ry="4"/><path d="M-12,-10v6c0,2.2 5.4,4 12,4s12,-1.8 12,-4v-6M-12,-2v6c0,2.2 5.4,4 12,4s12,-1.8 12,-4v-6M-12,6v6c0,2.2 5.4,4 12,4s12,-1.8 12,-4v-6"/><path d="M2,-6l-4,6h4l-3,6" stroke-width="1.4"/></g>`)
      case 'tgw': return dim === 'ghost' ? T(`<rect x="-18" y="-18" width="36" height="36" fill="none" stroke="${col}" stroke-dasharray="4 3"/><use href="#aw-o-tgw" data-off="tgw" transform="scale(.4) translate(-40,-40)" fill="${col}"/>`) : sq('tgw')
      case 'dxcon': return sq('dxcon')
      // VPN 세 종류 = 한 색 · 덱의 모양으로 가른다(§17.7 · §17.10)
      case 'vgw': case 'vpn': case 'cgw': return T(offG(kind, col))
      case 'cf': return T(offG('cf', col))
      case 'asg': case 'nfw': return T(offG(kind, col))
      case 'xvpc': return T(`<rect x="-18" y="-18" width="36" height="36" fill="none" stroke="${col}" stroke-dasharray="4 3"/>${offG('vpc', col, 30)}`)
      case 'dxgw': return dim === 'ghost' ? T(`<rect x="-18" y="-18" width="36" height="36" fill="none" stroke="${col}" stroke-dasharray="4 3"/>${offG('dxgw', col, 30)}`) : T(offG('dxgw', col))
      default: return T(`<circle r="12" ${st}/>`)
    }
  }
  const colOf = (c) => (c.ghost ? COL.ghost : COL[c.kind])
  // 피어링 포트 = VPC 색 원 + 두 점을 잇는 짧은 선(두 VPC 가 맞닿은 자리 · §17.1)
  const ppSvg = (cx, cy) => `<circle cx="${cx}" cy="${cy}" r="9" fill="${BG}" stroke="${COL.vpc}" stroke-width="1.6"/><path d="M${cx - 3.5},${cy}H${cx + 3.5}" stroke="${COL.vpc}" stroke-width="1.4"/><circle cx="${cx - 3.5}" cy="${cy}" r="2" fill="${COL.vpc}"/><circle cx="${cx + 3.5}" cy="${cy}" r="2" fill="${COL.vpc}"/>`
  const portSvg = (cx, cy, c = COL.tgw) => `<circle cx="${cx}" cy="${cy}" r="9" fill="${BG}" stroke="${c}" stroke-width="1.6"/><circle cx="${cx}" cy="${cy}" r="2.6" fill="${c}"/>${[0, 90, 180, 270].map((d) => { const r = (d * Math.PI) / 180; return `<path d="M${P2(cx + 3.5 * Math.cos(r))},${P2(cy + 3.5 * Math.sin(r))}L${P2(cx + 7 * Math.cos(r))},${P2(cy + 7 * Math.sin(r))}" stroke="${c}" stroke-width="1.4"/>` }).join('')}`
  const orth = (pts) => 'M' + pts.map(([x, y]) => P2(x) + ',' + P2(y)).join('L')
  const LBL = `font-size="10.5" paint-order="stroke" stroke="${BG}" stroke-width="3" stroke-linejoin="round"`
  function iconSvg (c, cx, cy, mode = 'below') {
    const col = colOf(c)
    let g = ''
    if (c.agg) g += glyph(c.kind, cx + 5, cy - 5, col, true, c.lb)
    g += glyph(c.kind, cx, cy, col, c.ghost ? 'ghost' : false, c.lb)
    if (c.off) g = `<g opacity=".4">${g}</g>`
    const f1 = c.off ? '#6e7681' : '#c9d1d9'
    let hit, lab
    if (mode === 'right') {
      hit = { x: cx - IC / 2 - 2, y: cy - IC / 2 - 2, w: HW, h: IC + 4 }
      lab = `<text x="${P2(cx + IC / 2 + 7)}" y="${P2(cy + (c.l2 ? -2 : 4))}" class="aw-il" fill="${f1}" ${LBL}>${esc(c.l1)}</text>` +
        (c.l2 ? `<text x="${P2(cx + IC / 2 + 7)}" y="${P2(cy + 11)}" class="aw-il" fill="#8b949e" ${LBL}>${esc(c.l2)}</text>` : '')
    } else {
      const hw = c.kind === 'igw' ? 52 : c.kind === 'inet' ? INET_HW : CW / 2 - 2
      hit = { x: cx - hw, y: cy - IC / 2 - 2, w: hw * 2, h: CH - 4 }
      lab = `<text x="${P2(cx)}" y="${P2(cy + IC / 2 + 12)}" class="aw-il" text-anchor="middle" fill="${f1}" ${LBL}>${esc(c.l1)}</text>` +
        (c.l2 ? `<text x="${P2(cx)}" y="${P2(cy + IC / 2 + 24)}" class="aw-il" text-anchor="middle" fill="#8b949e" ${LBL}>${esc(c.l2)}</text>` : '')
    }
    // 눈에 보이는 자리(그림 + 모니터링 테·테 배지 + 이름표) — 누르는 칸(`hit`)보다 좁다. 선 위 등급 배지가 피하는 것(§17.20)
    const r0 = IC / 2 + 10; const lw = (t) => tw(t, 10.5)
    const vis = [{ x: cx - r0, y: cy - r0 - (c.agg ? 5 : 0), w: r0 * 2 + (c.agg ? 5 : 0), h: r0 * 2 + (c.agg ? 5 : 0) }]
    if (mode === 'right') for (const [t, y] of [[c.l1, cy + (c.l2 ? -2 : 4)], [c.l2, cy + 11]]) { if (t) vis.push({ x: cx + IC / 2 + 5, y: y - 10, w: lw(t) + 4, h: 13 }) }
    else for (const [t, y] of [[c.l1, cy + IC / 2 + 12], [c.l2, cy + IC / 2 + 24]]) { if (t) vis.push({ x: cx - lw(t) / 2 - 2, y: y - 10, w: lw(t) + 4, h: 13 }) }
    B.vis.set(c.key, vis)
    B.icon.set(c.key, { cx, cy }); B.rect.set(c.key, hit); B.col.set(c.key, col)
    B.labels.push({ key: c.key, text: c.l1 + (c.l2 ? ' ' + c.l2 : ''), l1: c.l1, l2: c.l2 })
    return `<g class="aw-ic" data-k="${esc(c.key)}" data-kind="${c.kind}" data-subs="${esc(c.subs.join(' '))}" data-members="${esc((c.ids || []).join(' '))}"><rect class="aw-hit" x="${P2(hit.x)}" y="${P2(hit.y)}" width="${P2(hit.w)}" height="${P2(hit.h)}" fill="transparent"/>${g}${lab}</g>`
  }
  function pushLine (a, b, kind, pts, det, arrow, extra) {
    const k = `aws:l:${B.lines.length}`
    B.lines.push({ k, a, b, kind, pts, arrow: !!arrow, ...(extra || {}) }); if (det) B.det.set(k, det)
    return k
  }
  const addBox = (id, kind, x, y, w, h, node, parentBox, extra) => B.boxes.set(id, { id, kind, x, y, w, h, node, parentBox: parentBox || null, ...(extra || {}) })
  const synth = (id, type, parent, region) => ({ id, type, parent_id: parent || null, region: region || null, name: id, attrs: {} })

  // ======================================================================== 배치 한 벌
  // snl·azl = 서브넷·AZ 이름표 자리(LB → 대상 선이 피한다 · §17.27) · chips = 선 칩(그 선의 `l.chip` 과 같은 것 — 배지가 피한다)
  const newB = () => ({ inet: new Map(), det: new Map(), icon: new Map(), rect: new Map(), vis: new Map(), col: new Map(), lines: [], labels: [], boxes: new Map(), cellOf: new Map(), cellKind: new Map(), cellL: new Map(), cellIds: new Map(), frames: [],
    snl: new Map(), azl: new Map(), vnl: [], chips: [], agg: new Map(), mem: new Map() })
  // 🔴 전역 줄 아이콘은 x=250 부터 — 윗줄 계정에서 내려오는 선이 이 계정 왼쪽 여백(틈 차선이 넘친 것)으로 내려온다.
  //    왼쪽부터 채우면 30계정 화면에서 TGW 피어링 선 4개가 CloudFront 를 지난다(검사 130 · 2026-09-30 실측)
  const GLOB_H = 26 + HH + 16; const GLOB_X = 250; const GLOB_ROW = HH + 16
  const globsOf = (a) => [...M.globs.values()].filter((g) => g.acct === a)
  // 전역 줄은 계정 폭(= 리전 폭)을 넘으면 다음 줄로 내린다 — 폭을 늘리면 옆 계정이 밀린다(§17.1)
  const globPer = (w) => Math.max(1, Math.floor((w - GLOB_X - 16 + 16) / (HW + 16)))
  // 전역 줄의 칸 — 줄 묶음(§17.1)을 거친 것(묶음 = `{ kind, members }` · 그 종류의 첫 멤버 자리 · 나머지 순서는 그대로)
  const globRow = (a) => { const gs = globsOf(a); const lg = lineGroups(gs, (g) => LINE_GLOB.has(g.kind)); return gs.filter((g) => { const x = lg.of.get(g.id); return !x || x.members[0] === g }).map((g) => lg.of.get(g.id) || g) }
  const globH = (a, w) => { const n = globRow(a).length; return n ? GLOB_H + (Math.ceil(n / globPer(w)) - 1) * GLOB_ROW : 0 }
  // 묶음 칸의 상자 노드 — 서브넷 묶음과 같은 모양(`aws-agg` · 멤버 = 묶인 자원 · 모니터링은 멤버 중 나쁜 쪽을 그린다)
  const aggNode = (c, parent) => ({ id: c.key, type: 'aws-agg', agg: true, parent_id: parent, region: ((M.hubs.get(c.ids[0]) || M.globs.get(c.ids[0]) || {}).n || {}).region || null, members: c.ids.map((id) => ({ id })), attrs: {} })
  const acctSize = (a, rs) => {
    const rw = Math.max(420, ...rs.map((r) => r.w)); const w = AG + rw + 24
    const h = TOP + (globsOf(a).length ? globH(a, rw) + 22 : 0) + rs.reduce((t, R) => t + R.h + 24, 0) + (rs.length ? 0 : 60)
    return { w, h }
  }
  // 계정의 리전들 — 리전 자원 줄 상한 = 리전 폭 상한(VPC 스카이라인 2100 + 36)과 그 계정 리전들의 바탕 폭 중 큰 것(§17.1 「줄 묶음」).
  //   1차는 기본 상한으로 놓고, 넘친 리전이 있는데 다른 리전이 더 넓으면(계정 상자는 가장 넓은 리전 폭이다) 그 폭으로 다시 놓는다 — 쓸 수 있는 폭을 남기지 않는다
  const layoutRegs = (a) => {
    const rs = (M.regs.get(a) || []).map((rn) => layoutRegion(a, rn))
    const cap = Math.max(MAXW + 36, ...rs.map((R) => R.base))
    return rs.map((R) => (R.overN && cap > MAXW + 36 ? layoutRegion(a, R.rn, cap, R) : R))
  }
  // 줄 나누기는 두 체크박스를 다 켠 치수로 한 번 정한다 — 체크박스를 눌러도 계정이 다른 줄로 옮겨 가지 않는다
  function pickRows (accts) {
    const B0 = B; const O0 = O; B = newB(); O = { az: true, sn: true }
    let sz; try { sz = accts.map((a) => acctSize(a, layoutRegs(a))) } finally { B = B0; O = O0 }
    const chunk = (k) => { const r = []; for (let i = 0; i < accts.length; i += k) r.push(accts.slice(i, i + k)); return r }
    const cost = (k) => { const rs = chunk(k).map((r) => r.map((a) => sz[accts.indexOf(a)]))
      const w = Math.max(...rs.map((r) => r.reduce((t, z) => t + z.w + GAPX, -GAPX))); const h = rs.reduce((t, r) => t + Math.max(...r.map((z) => z.h)) + GAPX, -GAPX)
      return Math.min(1, REF_W / w, REF_H / h) }   // 화면 맞춤 배율
    let best = accts.length || 1
    for (let k = accts.length; k >= 1; k--) if (cost(k) > cost(best) + 1e-9) best = k
    return cost(best) >= WRAP_GAIN * cost(accts.length) ? chunk(best) : [accts]
  }
  function build () {
    B = newB()
    const out = { box: [], ln: [], ic: [] }
    const accts = M.accts
    const rows = accts.length ? pickRows(accts) : [[]]
    const regs = new Map(accts.map((a) => [a, layoutRegs(a)]))
    const regW = new Map(accts.map((a) => [a, Math.max(420, ...regs.get(a).map((r) => r.w))]))
    const rowOf = new Map(); const posOf = new Map(); rows.forEach((r, i) => r.forEach((a, j) => { rowOf.set(a, i); posOf.set(a, j) }))
    // 다른 줄의 계정을 잇는 선은 줄 사이 틈(가로)과 왼쪽 여백(세로)으로 돈다 — 틈 폭은 지나갈 선 수로 정한다
    const hubAcct = (id) => (M.hubs.get(id) || {}).acct; const vpcAcct = (id) => (M.vpcs.get(id) || {}).acct
    const hN = rows.map(() => 0); let mN = 0
    for (const [a, b] of [...M.tgwPeers.map((p) => [hubAcct(p.a), hubAcct(p.b)]), ...M.stubLines.map((p) => [hubAcct(p.a), hubAcct(p.b)]), ...M.peers.map((p) => [vpcAcct(p.a), vpcAcct(p.b)]),
      // 다른 계정 TGW 어태치먼트도 줄 사이 틈을 지난다(§17.1 · #75)
      ...[...M.ports.values()].filter((P) => M.hubs.has(P.tgw) && hubAcct(P.tgw) !== P.acct).map((P) => [P.acct, hubAcct(P.tgw)])]) {
      if (!rowOf.has(a) || !rowOf.has(b)) continue
      const ra = Math.min(rowOf.get(a), rowOf.get(b)); const rb = Math.max(rowOf.get(a), rowOf.get(b))
      if (ra === rb) { if (Math.abs(posOf.get(a) - posOf.get(b)) > 1) hN[ra]++; continue }
      hN[ra]++; if (rb > ra + 1) { hN[rb - 1]++; mN++ }
    }
    const MARG = mN ? 16 + 6 * mN : 0
    const acctX = new Map(); const acctY = new Map(); const acctW = new Map(); const acctH = new Map()
    const rowY = []; const rowH = []; let yy = 0; let W = 400
    rows.forEach((r, i) => {
      let ax0 = MARG
      for (const a of r) {
        acctX.set(a, ax0); acctY.set(a, yy); acctW.set(a, AG + regW.get(a) + 24)
        let y = TOP + (globsOf(a).length ? globH(a, regW.get(a)) + 22 : 0)
        for (const R of regs.get(a)) { R.X = ax0 + AG; R.Y = yy + y; R.w = regW.get(a); y += R.h + 24 }
        if (!regs.get(a).length) y += 60
        acctH.set(a, y); ax0 += acctW.get(a) + GAPX
      }
      W = Math.max(W, ax0 - GAPX + 40)
      rowY.push(yy); rowH.push(Math.max(0, ...r.map((a) => acctH.get(a)))); yy += rowH[i] + Math.max(GAPX, 28 + 5 * hN[i])
    })
    const H = Math.max(200, rowY[rowY.length - 1] + rowH[rowH.length - 1]) + 40
    // 못 읽은 곳(불변식 ④) — 틀이 없는 곳(안 그린 리전 · 아이콘)은 그것을 담은 가장 가까운 틀에 표시한다.
    // 안 그러면 권한이 막힌 계정이 「표시할 자원 없음」으로 보인다 — 있는데 없다고 그리는 것이 최악이다
    const fIds = new Set(M.vpcs.keys()); for (const a of accts) { fIds.add(X.accounts[a].id); for (const R of regs.get(a)) fIds.add(R.region) }
    const PM = new Set()
    for (const t of M.partial) {
      let c = X.mById.get(t); let g = 0
      while (c && !fIds.has(c.id) && g++ < 40) c = c.parent_id ? X.mById.get(c.parent_id) : null
      const f = c && fIds.has(c.id) ? c.id : fIds.has(t) ? t : (() => { const a = accts.find((i) => t.includes(String(X.accounts[i].id).replace(/^acct-/, ''))); return a === undefined ? null : X.accounts[a].id })()
      if (f) PM.add(f)
    }
    // 전멸(§17.26 A-01 · 대장 131 · QA A-01) — 틀이 품은 범위를 **하나도** 못 읽었다. 판정 = flow.js `wipedOf`(그 (계정, 리전)의 구조 행에 `ok` 가 없다).
    //   🔴 「못 읽음」 표시가 있는 틀(`PM`)에서만 말을 고른다 — 표시를 새로 만들지 않는다(그림·표시 자리는 그대로 · §3h H3).
    //   리전 틀은 수집 결과가 아니라 **보기 범위**로 그려지므로 「그려진 리전 0」만으로는 전멸을 알 수 없다(옛 결함 — 리전 3틀이 다 「일부 못 읽음」이었다).
    const wipedR = (R) => PM.has(R.region) && !!(X.wipedOf && X.wipedOf(R.rn))
    const wipedA = (a) => { const rs = regs.get(a); return rs.length ? !globsOf(a).length && rs.every(wipedR) : PM.has(X.accounts[a].id) }
    for (const a of accts) if (regs.get(a).length && wipedA(a)) PM.add(X.accounts[a].id)
    B.pm = PM
    const warn = (x, y, all) => `<text x="${x}" y="${y}" text-anchor="end" fill="#d29922" font-size="11.5">${all ? '읽지 못함' : '일부 못 읽음'}</text>`

    // 계정 틀 이름표(§17.5 「계정은 이름으로 부른다」) — `계정 ` 과 ` (앞4…뒤4)` 는 그대로 두고 **이름만** 폭 예산 안으로 줄인다(형제 = 화면의 계정 틀 전부 · ①②③).
    //   예산 = 틀 폭 − 22(띠 여백) − 오른쪽 「일부 못 읽음」/「읽지 못함」 자리. 전체 이름은 그 계정 자원의 정보창 「계정」 줄 · 「계정:」 팝오버 줄
    const aTail = (a) => ` (${X.acctMask(X.acctNumOf(X.accounts[a]))})`
    const aBud = (a) => acctW.get(a) - 22 - (PM.has(X.accounts[a].id) ? tw(wipedA(a) ? '읽지 못함' : '일부 못 읽음', 11.5) + 22 : 10)
    const aNm = fitNames(accts.filter((a) => X.acctNameOf(X.accounts[a])).map((a) => ({ key: a, name: X.acctNameOf(X.accounts[a]), W: Math.max(24, aBud(a) - tw('계정 ' + aTail(a), 12)) })), 12)
    const acctLab = (a) => (aNm.has(a) ? '계정 ' + aNm.get(a) + aTail(a) : acctName(a))
    for (const a of accts) {
      const an = X.accounts[a]; const ax = OX + acctX.get(a); const ay = OY + acctY.get(a)
      const lab = acctLab(a)
      out.box.push(`<g class="aw-acct" data-f="${esc(an.id)}"><rect x="${ax}" y="${ay}" width="${acctW.get(a)}" height="${acctH.get(a)}" rx="2" fill="none" stroke="#8b949e" stroke-width="1.4"/><rect x="${ax}" y="${ay}" width="${P2(tw(lab, 12) + 22)}" height="24" fill="#30363d"/><text x="${ax + 11}" y="${ay + 16.5}" fill="#e6edf3" font-weight="700" font-size="12">${esc(lab)}</text>` +
        (PM.has(an.id) ? warn(ax + acctW.get(a) - 10, ay + 16.5, wipedA(a)) : '') +
        (regs.get(a).length ? '' : PM.has(an.id) ? `<text x="${ax + AG}" y="${ay + TOP + 20}" fill="#d29922" font-size="12">이 계정을 읽지 못했습니다 — 비어 있는 것이 아닙니다</text>` : `<text x="${ax + AG}" y="${ay + TOP + 20}" fill="#6e7681" font-size="12">표시할 리소스 없음</text>`) + '</g>')
      B.labels.push({ key: an.id, text: lab, frame: true })
      addBox(an.id, 'aws-acct', ax, ay, acctW.get(a), acctH.get(a), an, null)
      B.frames.push({ id: an.id, kind: 'acct', x: ax, y: ay, w: acctW.get(a), h: acctH.get(a), parent: null })
      const gws = globRow(a)
      if (gws.length) {
        const gx = ax + AG; const gy = ay + TOP; const gid = 'aws:glob:' + an.id; const gh = globH(a, regW.get(a)); const per = globPer(regW.get(a))
        out.box.push(`<g class="aw-glob" data-f="${esc(gid)}"><rect x="${gx}" y="${gy}" width="${regW.get(a)}" height="${gh}" rx="2" fill="none" stroke="#6e7681" stroke-width="1.2" stroke-dasharray="6 4"/>${TILE.globe(gx + 8, gy + 6, '#30363d', '#c9d1d9')}<text x="${gx + 32}" y="${gy + 19}" fill="#8b949e" font-size="12.5">전역 · 리전에 속하지 않음</text></g>`)
        addBox(gid, 'aws-glob', gx, gy, regW.get(a), gh, synth(gid, 'aws-glob', an.id), an.id)
        B.frames.push({ id: gid, kind: 'glob', x: gx, y: gy, w: regW.get(a), h: gh, parent: an.id })
        const gcs = gws.map((g) => (g.members ? lineAggCell(g, an.id, a, '전역 줄 · 리전에 속하지 않음') : cellMake(g, 'right'))); fitIcons(gcs)
        gws.forEach((g, i) => {
          const c = gcs[i]; const cx = gx + GLOB_X + (i % per) * (HW + 16) + IC / 2; const cy = gy + 26 + Math.floor(i / per) * GLOB_ROW + IC / 2 + 2
          out.ic.push(iconSvg(c, cx, cy, 'right'))
          addBox(c.key, 'aws-hub', cx - IC / 2, cy - IC / 2, IC, IC, g.members ? aggNode(c, gid) : g.n, gid, g.ghost ? { hiddenIds: [g.att] } : null)
        })
      }
      for (const R of regs.get(a)) {
        const rx = OX + R.X; const ry = OY + R.Y
        const pm = PM.has(R.region) ? warn(rx + R.w - 10, ry + 20, wipedR(R)) : ''
        // 전멸한 리전의 빈 자리 = 「비어 있다」가 아니라 「못 읽었다」(불변식 ④ — 있는데 없다고 그리지 않는다) · 계정째 전멸이면 계정을 말한다
        const emptyT = !R.empty ? '' : !wipedR(R) ? `<text x="${rx + 34}" y="${ry + REG_HDR + 22}" fill="#6e7681" font-size="12">표시할 리소스 없음</text>`
          : `<text x="${rx + 34}" y="${ry + REG_HDR + 22}" fill="#d29922" font-size="12">${wipedA(a) ? '이 계정을 읽지 못했습니다 — 비어 있는 것이 아닙니다' : '이 리전을 읽지 못했습니다 — 비어 있는 것이 아닙니다'}</text>`
        out.box.push(`<g class="aw-reg" data-f="${esc(R.region)}"><rect x="${rx}" y="${ry}" width="${R.w}" height="${R.h}" rx="2" fill="none" stroke="#6e7681" stroke-width="1.2" stroke-dasharray="6 4"/>${TILE.region(rx + 8, ry + 6)}<text x="${rx + 34}" y="${ry + 20}" fill="#8b949e" font-size="12.5">${esc(regName(R.region))}</text>${pm}` +
          emptyT + '</g>')
        B.labels.push({ key: R.region, text: regName(R.region), frame: true })
        addBox(R.region, 'aws-region', rx, ry, R.w, R.h, R.rn, an.id)
        B.frames.push({ id: R.region, kind: 'region', x: rx, y: ry, w: R.w, h: R.h, parent: an.id })
        for (const hc of R.hub) {
          const cx = rx + hc.hx + IC / 2; const cy = ry + hc.hy + IC / 2 + 2
          out.ic.push(iconSvg(hc, cx, cy, 'right'))
          const h = M.hubs.get(hc.key)
          const hid = [...M.hidden.entries()].filter(([, r]) => r === hc.key || (hc.agg && hc.ids.includes(r))).map(([id]) => id)
          addBox(hc.key, 'aws-hub', cx - IC / 2, cy - IC / 2, IC, IC, h ? h.n : aggNode(hc, R.region), R.region, hid.length ? { hiddenIds: hid } : null)
        }
        for (const xc of R.xp) {
          const cx = rx + xc.hx + IC / 2; const cy = ry + xc.hy + IC / 2 + 2
          out.ic.push(iconSvg(xc, cx, cy, 'right')); B.cellKind.set(xc.key, 'xvpc')
          addBox(xc.key, 'aws-hub', cx - IC / 2, cy - IC / 2, IC, IC, xc.it.n, R.region)
        }
        const vNm = fitNames(R.lays.filter((L) => L.v.named).map((L) => ({ key: L.v.id, name: L.v.name, W: Math.max(60, L.nameW) - tw('VPC ', 12.5) - tw('()', 12.5) })), 12.5)
        for (const L of R.lays) {
          const Xv = rx + L.X; const Yv = ry + L.Y; const v = L.v
          L.ax = Xv; L.ay = Yv
          const nm = v.named ? `VPC (${vNm.get(v.id)})` : 'VPC'
          const nmW = tw(nm, 12.5)
          const pv = PM.has(v.id) ? warn(Xv + L.w - 10, Yv + 22) : ''
          L.hdr = { x: Xv + 6, y: Yv + 4, w: nmW + 58, h: 26 }   // VPC 이름 자리 — CloudFront 원본 선이 피한다(검사 130 이 이름표로 잰다)
          B.vnl.push(L.hdr)   // LB → 대상 선의 칩이 피한다(§17.27)
          let s = `<g class="aw-vpc" data-v="${esc(v.id)}"><rect class="aw-vb" x="${Xv}" y="${Yv}" width="${L.w}" height="${L.h}" rx="2" fill="#10151c" stroke="${COL.vpc}" stroke-width="1.4"/>${TILE.vpc(Xv + 8, Yv + 6)}`
          s += `<g class="aw-vt" data-v="${esc(v.id)}"><rect class="aw-hit" x="${Xv + 6}" y="${Yv + 4}" width="${P2(nmW + 58)}" height="26" fill="transparent"/><text x="${Xv + 38}" y="${Yv + 22}" class="aw-vn" fill="#e6edf3" font-weight="600" font-size="12.5">${esc(nm)}</text><text x="${P2(Xv + 44 + nmW)}" y="${Yv + 22}" class="aw-tri" fill="#8b949e" font-size="11">▸</text></g>${pv}`
          B.labels.push({ key: v.id, text: nm, frame: true })
          if (L.empty) s += `<text x="${Xv + 38}" y="${Yv + 58}" fill="#6e7681" font-size="12">${esc(L.empty)}</text>`
          // 서브넷을 끄면 서브넷은 VPC 가 대표한다 — 안 그러면 잔차 표가 「아직 그리지 못하는 종류」고 거짓 고발한다
          addBox(v.id, 'aws-vpc', Xv, Yv, L.w, L.h, v.n, R.region, O.sn ? null : { hiddenIds: v.subnets.map((x) => x.id) })
          B.frames.push({ id: v.id, kind: 'vpc', x: Xv, y: Yv, w: L.w, h: L.h, parent: R.region })
          B.rect.set(v.id, { x: Xv, y: Yv, w: L.w, h: L.h })
          for (const b of L.azb) {
            const aid = `aws:az:${v.id}:${b.az}`
            s += `<g class="aw-az" data-f="${esc(aid)}"><rect x="${Xv + b.x}" y="${Yv + b.y}" width="${b.w}" height="${b.h}" rx="2" fill="none" stroke="#484f58" stroke-width="1.1" stroke-dasharray="5 4"/><text x="${P2(Xv + b.x + b.w / 2)}" y="${Yv + b.y + 15}" text-anchor="middle" fill="#8b949e" font-size="11">${esc(b.az)}</text></g>`
            B.labels.push({ key: aid, text: b.az, frame: true })
            push(B.azl, v.id, { x: P2(Xv + b.x + b.w / 2 - tw(b.az, 11) / 2 - 2), y: Yv + b.y + 4, w: P2(tw(b.az, 11) + 4), h: 14 })
            addBox(aid, 'aws-az', Xv + b.x, Yv + b.y, b.w, b.h, synth(aid, 'aws-az', v.id, v.n.region), v.id)
            B.frames.push({ id: aid, kind: 'az', x: Xv + b.x, y: Yv + b.y, w: b.w, h: b.h, parent: v.id, az: b.az })
          }
          const sNm = fitNames(L.subs.filter((sb) => sb.s.named).map((sb) => ({ key: sb.s.id, name: sb.s.name, W: sb.w - 34 - tw('Subnet ', 11) - tw('()', 11) })), 11)
          for (const sb of L.subs) {
            const pub = sb.s.tier === 'public'
            const nmS = sb.s.named ? `Subnet (${sNm.get(sb.s.id)})` : 'Subnet'
            const azBox = L.azb.find((b) => b.az === sb.s.az)
            const par = azBox ? `aws:az:${v.id}:${sb.s.az}` : v.id
            s += `<g class="aw-sn${pub ? ' aw-pub' : ''}" data-k="${esc(sb.s.id)}"><rect x="${Xv + sb.x}" y="${Yv + sb.y}" width="${sb.w}" height="${sb.h}" rx="1" fill="${pub ? SNF.pub : SNF.priv}" stroke="${pub ? COL.pub : COL.priv}"/>${pub ? TILE.globe(Xv + sb.x + 5, Yv + sb.y + 5) : TILE.lock(Xv + sb.x + 5, Yv + sb.y + 5)}<text x="${Xv + sb.x + 28}" y="${Yv + sb.y + 17.5}" fill="#c9d1d9" font-size="11">${esc(nmS)}</text></g>`
            B.labels.push({ key: sb.s.id, text: nmS, frame: true })
            B.snl.set(sb.s.id, { x: Xv + sb.x + 4, y: Yv + sb.y + 4, w: P2(24 + tw(nmS, 11) + 4), h: 18 })
            B.rect.set(sb.s.id, { x: Xv + sb.x, y: Yv + sb.y, w: sb.w, h: sb.h })
            addBox(sb.s.id, 'aws-subnet', Xv + sb.x, Yv + sb.y, sb.w, sb.h, sb.s.n, par, { nm: nmS })
            B.frames.push({ id: sb.s.id, kind: 'subnet', x: Xv + sb.x, y: Yv + sb.y, w: sb.w, h: sb.h, parent: par, pub })
          }
          s += '</g>'
          out.box.push(s)
          const cellPar = (c) => (O.sn && c.subs.length === 1 && L.subs.some((sb) => sb.s.id === c.subs[0]) && !L.right.includes(c) ? c.subs[0]
            : O.az && c.subs.length === 1 && L.azb.some((b) => b.az === M.subnets.get(c.subs[0]).az) && !L.right.includes(c) ? `aws:az:${v.id}:${M.subnets.get(c.subs[0]).az}` : v.id)
          for (const c of L.cells) {
            const cx = Xv + c.x + CW / 2; const cy = Yv + c.y + 2 + IC / 2
            out.ic.push(iconSvg(c, cx, cy))
            const node = c.it ? c.it.n : { id: c.key, type: 'aws-agg', agg: true, parent_id: v.id, region: v.n.region, members: c.ids.map((id) => ({ id })), attrs: {} }
            addBox(c.key, 'aws-item', cx - IC / 2, cy - IC / 2, IC, IC, node, cellPar(c))
          }
          if (L.igw) {
            out.ic.push(iconSvg(L.igw, Xv + L.igw.cx, Yv + L.igw.cy))
            addBox(L.igw.key, 'aws-item', Xv + L.igw.cx - IC / 2, Yv + L.igw.cy - IC / 2, IC, IC, L.igw.it.n, v.id, { border: true })
          }
          if (L.inet) {
            // 그린 칸(§17.24) — VPC 밖이라 부모 = 리전. 픽스처의 인터넷 노드가 여기로 숨었으면 이 칸이 대표한다
            const c = L.inet; const cx = Xv + c.cx; const cy = Yv + c.cy
            out.ic.push(iconSvg(c, cx, cy))
            const hid = [...M.hidden.entries()].filter(([, r]) => r === c.key).map(([id]) => id)
            addBox(c.key, 'aws-hub', cx - IC / 2, cy - IC / 2, IC, IC, synth(c.key, 'aws-inet', null, R.region), R.region, hid.length ? { hiddenIds: hid } : null)
          }
          for (const p of L.ports) {
            const px = Xv + p.x; const py = Yv + p.y
            const P = M.ports.get(p.key); const pc = P.vgw ? COL.vgw : P.cwan ? COL.cwan : P.lsn ? COL.lsn : COL.tgw
            out.ic.push(`<g class="aw-pt" data-k="${esc(p.key)}" data-kind="${P.vgw ? 'vgwport' : P.cwan ? 'cwanport' : P.lsn ? 'lsnport' : 'port'}" data-subs=""><rect class="aw-hit" x="${px - 11}" y="${py - 11}" width="22" height="22" fill="transparent"/>${portSvg(px, py, pc)}</g>`)
            B.icon.set(p.key, { cx: px, cy: py }); B.rect.set(p.key, { x: px - 9, y: py - 9, w: 18, h: 18 }); B.col.set(p.key, pc)
            B.det.set(p.key, () => detPort(P, P.vgw ? 'VPC 테두리의 작은 원 — 이 VPC 가 VGW 에 연결된 지점입니다.' : P.cwan ? 'VPC 테두리의 작은 원 — 이 VPC 가 Cloud WAN 코어 네트워크에 연결된 지점입니다.'
              : P.lsn ? 'VPC 테두리의 작은 원 — 이 VPC 가 VPC Lattice 서비스 네트워크에 연결된 지점입니다. 이 VPC 안의 클라이언트가 그 네트워크의 서비스를 이름으로 부릅니다.'
                : 'VPC 테두리의 작은 원 — 이 VPC 가 TGW 에 연결된 지점입니다. 연결된 서브넷은 아래 칸에 있습니다.'))
            const hid = [...M.hidden.entries()].filter(([, r]) => r === p.key).map(([id]) => id)
            addBox(p.key, 'aws-port', px - 9, py - 9, 18, 18, P.n, v.id, { border: true, ...(hid.length ? { hiddenIds: hid } : {}) })
          }
          for (const p of L.pports) {
            const px = Xv + p.x; const py = Yv + p.y; const P = M.pp.get(p.key)
            out.ic.push(`<g class="aw-pt" data-k="${esc(p.key)}" data-kind="pcxport" data-subs=""><rect class="aw-hit" x="${px - 11}" y="${py - 11}" width="22" height="22" fill="transparent"/>${ppSvg(px, py)}</g>`)
            B.icon.set(p.key, { cx: px, cy: py }); B.rect.set(p.key, { x: px - 9, y: py - 9, w: 18, h: 18 }); B.col.set(p.key, COL.vpc)
            B.det.set(p.key, () => detPp(P))
            addBox(p.key, 'aws-port', px - 9, py - 9, 18, 18, synth(p.key, 'aws-pcxport', v.id, R.region), v.id, { border: true })
          }
          for (const l of L.lines) pushLine(l.a, l.b, l.kind, l.pts.map(([x, y]) => [Xv + x, Yv + y]), l.det, l.arrow, { sub: l.sub, tid: l.tid, extra: l.extra })
        }
      }
    }
    const regOf = (id) => { for (const rs of regs.values()) for (const R of rs) if (R.region === id) return R; return null }
    // 어태치먼트: 포트 → 위 제 차선 → TGW 밑 제 자리(§17.1 · #75). TGW 가 다른 리전 상자(= 다른 계정)면 계정 사이 길로 돈다 —
    //   그 길(`across`)은 아래에서 정의되고 틈 차선 번호를 센다 → 자리만 잡아 두고 다른 선을 다 그은 뒤 채운다(옛 선의 틈 차선이 그대로다)
    const xAtt = []; const detN = new Map()
    for (const a of accts) {
      for (const R of regs.get(a)) {
        const ry = OY + R.Y
        for (const L of R.lays) {
          for (const p of L.ports) {
            if ((M.ports.get(p.key) || {}).cwan) continue   // Cloud WAN(§17.39) = 전역 줄 → 포트(CloudFront 원본 선과 같은 길 · 아래 `M.cfo`)
            const P = B.icon.get(p.key); const Tt = B.icon.get(p.tgw); if (!P || !Tt) continue
            const Pp = M.ports.get(p.key); const RH = regOf(M.hubs.get(p.tgw).region)
            // VPC Lattice 연결(§17.40) = 어태치먼트와 같은 길 · Lattice 색(선 종류 `lsnatt`)
            const ak = Pp.lsn ? 'lsnatt' : 'att'; const how = Pp.lsn ? '이 선이 VPC Lattice 서비스 네트워크 연결입니다' : '이 선이 TGW 연결입니다'
            const ex = OX + RH.X + RH.attE.get(p.key); const tb = Tt.cy + IC / 2
            if (RH !== R) {
              pushLine(p.key, p.tgw, ak, null, () => detPort(Pp, `${how} — 다른 계정의 ${Pp.lsn ? '서비스 네트워크' : 'TGW'}`), false, { xacct: true })
              xAtt.push({ l: B.lines[B.lines.length - 1], va: L.v.acct, ha: M.hubs.get(p.tgw).acct, s: [[P.cx, P.cy - 9], [P.cx, ry + R.attY.get(p.key)]],
                yh: OY + RH.Y + RH.attY.get(p.key), e: [[ex, tb]] })
              continue
            }
            const ly = ry + R.attY.get(p.key)
            // 윗줄 VPC 가 포트 위를 막으면 두 줄 사이 틈 → 막은 VPC 들의 가까운 옆 틈으로 올라간다(§17.1 · #74 — 곧게 올라가면 그 VPC 를 관통한다)
            const blk = R.lays.filter((L2) => L2 !== L && L2.ax < P.cx && P.cx < L2.ax + L2.w && L2.ay + L2.h < P.cy && L2.ay + L2.h > ly)
            let pts
            if (blk.length) {
              const k = detN.get(R) || 0; detN.set(R, k + 1)
              const lo = Math.min(...blk.map((b) => b.ax)); const hi = Math.max(...blk.map((b) => b.ax + b.w))
              const gx = P.cx - lo <= hi - P.cx ? lo - GXV / 2 + 4 * k : hi + GXV / 2 - 4 * k; const yb = P.cy - 20 - 4 * k
              pts = [[P.cx, P.cy - 9], [P.cx, yb], [gx, yb], [gx, ly], [ex, ly], [ex, tb]]
            } else pts = Math.abs(P.cx - ex) < 0.5 ? [[P.cx, P.cy - 9], [ex, tb]] : [[P.cx, P.cy - 9], [P.cx, ly], [ex, ly], [ex, tb]]
            pushLine(p.key, p.tgw, ak, pts, () => detPort(Pp, how))
          }
        }
      }
    }
    const gapN = new Map(); const leftN = new Map()
    // 같은 계정 안 리전 사이 = 계정 틀과 리전 틀 사이 왼쪽 틈(폭 AG) — VPC 를 가로지르지 않는다(검사 130)
    const leftX = (A) => { const i = leftN.get(A) || 0; leftN.set(A, i + 1); return OX + acctX.get(A) + 10 + 6 * i }
    const gapX = (A) => { const i = gapN.get(A) || 0; gapN.set(A, i + 1); return OX + acctX.get(A) + acctW.get(A) + GAPX / 2 - 12 + 8 * i }
    // B 계정 왼쪽 틈 = 같은 줄 앞 계정의 오른쪽 틈(차선을 같이 센다) · 줄 첫 계정이면 왼쪽 여백
    const hN2 = new Map(); let mI = 0
    const mLane = () => OX + 8 + 6 * mI++
    const hGap = (r) => { const i = hN2.get(r) || 0; hN2.set(r, i + 1); return OY + rowY[r] + rowH[r] + 14 + 5 * i }
    const leftOf = (Bc) => (posOf.get(Bc) ? gapX(rows[rowOf.get(Bc)][posOf.get(Bc) - 1]) : null)
    // 다른 계정 사이 길(가운데 마디) — 바로 옆 계정이면 A 오른쪽 틈 하나로 내려가고, 다른 줄이면 줄 사이 틈으로 돈다.
    // 같은 줄이라도 사이에 계정이 있으면 그 줄 아래 틈으로 돈다 — 리전 머리 높이로 가면 사이 계정 틀을 관통한다(§17.15 · #45 리뷰)
    const across = (Ac, ya, Bc, yb) => {
      const ra = rowOf.get(Ac); const rb = rowOf.get(Bc); const xa = gapX(Ac)
      if (rb === undefined || ra === undefined || (ra === rb && posOf.get(Bc) - posOf.get(Ac) <= 1)) return [[xa, ya], [xa, yb]]
      const h1 = hGap(ra)
      if (ra === rb) { const xb = leftOf(Bc); return [[xa, ya], [xa, h1], [xb, h1], [xb, yb]] }
      if (rb === ra + 1) { const xb = leftOf(Bc) ?? mLane(); return [[xa, ya], [xa, h1], [xb, h1], [xb, yb]] }
      const xm = mLane(); const h2 = hGap(rb - 1); const xb = leftOf(Bc) ?? xm
      return [[xa, ya], [xa, h1], [xm, h1], [xm, h2], [xb, h2], [xb, yb]]
    }
    const accOrd = (i) => accts.indexOf(i)
    // TGW 피어링 · 스텁: TGW 위 → 리전 머리 아래 차선 → (계정이 다르면 계정 사이 틈) → 상대 TGW 위
    const hubLine = (ida, idb, kind, det, extra) => {
      let A = M.hubs.get(ida); let Bh = M.hubs.get(idb); if (!A || !Bh) return
      if (accOrd(A.acct) > accOrd(Bh.acct)) { [A, Bh] = [Bh, A] }
      const PA = B.icon.get(A.id); const PB = B.icon.get(Bh.id); if (!PA || !PB) return
      const RA = regOf(A.region); const RB = regOf(Bh.region)
      const ya = OY + RA.Y + REG_HDR + 6; const yb = OY + RB.Y + REG_HDR + 6
      const Xl = A.acct !== Bh.acct ? null : A.region === Bh.region ? Math.max(PA.cx, PB.cx) + IC : leftX(A.acct)
      const mid = Xl === null ? across(A.acct, ya, Bh.acct, yb) : [[Xl, ya], [Xl, yb]]
      pushLine(A.id, Bh.id, kind, [[PA.cx, PA.cy - IC / 2], [PA.cx, ya], ...mid, [PB.cx, yb], [PB.cx, PB.cy - IC / 2]], det, false, extra)
    }
    for (const p of M.tgwPeers) {
      hubLine(p.a, p.b, 'tgwpeer', () => {
        const A = M.hubs.get(p.a); const Bh = M.hubs.get(p.b)
        return { title: 'TGW 피어링', sub: `${A.name} ↔ ${Bh.name}`, rows: [['계정', `${acctName(A.acct)} · ${X.mById.get(A.region).name} ↔ ${acctName(Bh.acct)} · ${X.mById.get(Bh.region).name}`],
          ...(p.att ? [['어태치먼트', natId(X.mById.get(p.att))]] : [])], secs: [] }
      }, p.att ? { rep: [p.att], att: p.att } : null)
    }
    for (const p of M.stubLines) {
      hubLine(p.a, p.b, 'tgwpeer', () => {
        const st = M.hubs.get(p.b); const a = st.n.attrs || {}
        if (st.xscope) return { title: 'TGW 피어링 · 다른 계정', sub: '보기 범위 밖', rows: [['TGW', hubName(p.a)], ['상대 계정', acctName(st.xacct)]], secs: [] }
        return { title: 'TGW 피어링 · 다른 계정', sub: '수집 범위 밖', rows: [['TGW', hubName(p.a)], ...attrRows(st.n)], secs: [] }
      })
    }
    // VPC 피어링(점선): VPC 밑 → 그 줄 아래 차선 → (계정 사이 틈) → 상대 VPC 밑
    const allLays = [...regs.values()].flat().flatMap((R) => R.lays.map((L) => ({ L, R })))
    // 나가는 자리 = 그 VPC 의 피어링 포트 밑(서브넷 라우트 선이 거기서 끝난다 · §17.18-11) · 포트가 없으면 VPC 아래쪽 변 가운데
    const ppExit = (L, pcx) => { const P = B.icon.get(M.ppOf.get(L.v.id + '|' + pcx)); return P ? [P.cx, P.cy + 9] : [L.ax + L.w / 2, L.ay + L.h] }
    const laneN = new Map()   // 리전 상자 → 다음 피어링 차선 번호(피어링 id 순)
    const nextLane = (R) => { const i = laneN.get(R) || 0; laneN.set(R, i + 1); return i }
    for (const p of [...M.peers].sort((x, y) => (x.pcx < y.pcx ? -1 : x.pcx > y.pcx ? 1 : 0))) {
      let A = allLays.find((x) => x.L.v.id === p.a); let Bv = allLays.find((x) => x.L.v.id === p.b); if (!A || !Bv) continue
      if (accOrd(A.L.v.acct) > accOrd(Bv.L.v.acct)) [A, Bv] = [Bv, A]
      const li = new Map([[A.R, nextLane(A.R)], ...(Bv.R !== A.R ? [[Bv.R, nextLane(Bv.R)]] : [])])
      const lane = (x) => OY + x.R.Y + x.R.contentBottom + 20 + PEER_LANE * li.get(x.R)
      const [ax, ab] = ppExit(A.L, p.pcx); const [bx, bb] = ppExit(Bv.L, p.pcx)
      const pts = A.L.v.acct === Bv.L.v.acct ? [[ax, ab], [ax, lane(A)], [bx, lane(A)], [bx, bb]] : [[ax, ab], [ax, lane(A)], ...across(A.L.v.acct, lane(A), Bv.L.v.acct, lane(Bv)), [bx, lane(Bv)], [bx, bb]]
      pushLine(A.L.v.id, Bv.L.v.id, 'peer', pts, () => ({ title: 'VPC 피어링', sub: disp(p.n), rows: [['ID', natId(p.n)], ...attrRows(p.n), ...(p.n.attrs && p.n.attrs.state ? [['상태', stateKo(p.n.attrs.state)]] : [])], secs: [] }), false, { pcx: p.pcx, rep: [p.pcx] })
    }
    // 범위 밖 피어 VPC(점선): VPC 밑 → 리전 밑 차선 → 흐린 VPC 칸 위
    for (const R of [...regs.values()].flat()) {
      for (const xc of R.xp) {
        const A = allLays.find((x) => x.L.v.id === xc.xp.a); const PG = B.icon.get(xc.key); if (!A || !PG) continue
        const [ax, ab] = ppExit(A.L, xc.xp.pcx); const lane = OY + R.Y + R.contentBottom + 28 + R.peerPL
        const pts = Math.abs(ax - PG.cx) < 0.5 ? [[ax, ab], [ax, PG.cy - IC / 2]] : [[ax, ab], [ax, lane], [PG.cx, lane], [PG.cx, PG.cy - IC / 2]]
        const p = xc.xp
        pushLine(p.a, xc.key, 'peer', pts, () => ({ title: 'VPC 피어링 · 범위 밖', sub: disp(p.n), rows: [['ID', natId(p.n)], ['상대 VPC', p.it.side.vpc], ...attrRows(p.n), ...(p.n.attrs && p.n.attrs.state ? [['상태', stateKo(p.n.attrs.state)]] : [])], secs: [] }), false, { pcx: p.pcx, rep: [p.pcx] })
      }
    }
    // DX 게이트웨이 ↔ TGW: TGW 위 → 리전 차선 → 계정 안 왼쪽 틈 → 전역 줄 아이콘 밑 → DX 게이트웨이 밑
    M.dxtgw.forEach((p, i) => {
      const Tt = M.hubs.get(p.tgw); const PT = B.icon.get(p.tgw); const PG = B.icon.get(p.dxgw); if (!Tt || !PT || !PG) return
      const R = regOf(Tt.region)
      const y = OY + R.Y + REG_HDR + 9 + 3 * i; const Xl = leftX(Tt.acct); const gy = PG.cy + IC / 2 + 8 + 4 * i
      const sx = PT.cx - 8; const ex = PG.cx - 8
      pushLine(p.tgw, p.dxgw, 'dxtgw', [[sx, PT.cy - IC / 2], [sx, y], [Xl, y], [Xl, gy], [ex, gy], [ex, PG.cy + IC / 2]],
        () => ({ title: 'TGW ↔ DX 게이트웨이', sub: '연결(association) — 온프레미스 경로가 이 TGW 로 들어옵니다', rows: [['TGW', hubName(p.tgw)], ['DX 게이트웨이', M.globs.get(p.dxgw).ghost ? '읽지 못함' : M.globs.get(p.dxgw).name], ['어태치먼트', natId(X.mById.get(p.att))]], secs: [] }), false, { att: p.att })
    })
    // DX 연결 → DX 게이트웨이 · DX 게이트웨이 → VGW(사용자 결정 2026-10-26) — 같은 길 모양 · 차선은 TGW ↔ DXGW 다음 번호부터(서로 포개지지 않게)
    //   차선 번호 — 리전 머리 밑 = 그 리전 안 차례(리전 상자가 `dxlN` 만큼 자원 줄을 내렸다) · 전역 줄 밑 = 그 계정 안 차례
    const dxR = new Map(); const dxA = new Map()
    M.dxl.forEach((p, j) => {
      const H = M.hubs.get(p.hub); const PH = B.icon.get(p.hub); const PG = B.icon.get(p.dxgw); const G = M.globs.get(p.dxgw); if (!H || !PH || !PG || !G) return
      if (H.acct !== G.acct) return   // 다른 계정의 전역 줄로 가는 길은 그리지 않는다(정보창이 말한다)
      const R = (regs.get(H.acct) || []).find((x) => x.region === H.region); if (!R) return
      const ir = dxR.get(R) || 0; dxR.set(R, ir + 1); const ia = dxA.get(H.acct) || 0; dxA.set(H.acct, ia + 1)
      const y = OY + R.Y + REG_HDR + 9 + 3 * (M.dxtgw.length + ir); const Xl = leftX(H.acct); const gy = PG.cy + IC / 2 + 8 + 4 * (M.dxtgw.length + ia)
      const sx = PH.cx - 8; const ex = PG.cx - 8 + 3 * ((j % 3) - 1)
      const vif = p.kind === 'dxvif'
      pushLine(p.hub, p.dxgw, p.kind, [[sx, PH.cy - IC / 2], [sx, y], [Xl, y], [Xl, gy], [ex, gy], [ex, PG.cy + IC / 2]],
        () => (vif ? { title: 'DX 연결 → DX 게이트웨이', sub: '가상 인터페이스(VIF)로 연결 — 온프레미스 경로가 이 게이트웨이로 들어옵니다',
          rows: [['DX 연결', H.name], ['DX 게이트웨이', G.name], ['가상 인터페이스', p.vifs.map((v) => natId(X.mById.get(v))).join(', ')]], secs: [] }
          : { title: 'DX 게이트웨이 → VGW', sub: '게이트웨이 연결(association) — 이 게이트웨이의 경로가 VGW 가 붙은 VPC 로 갑니다',
            rows: [['DX 게이트웨이', G.name], ['VGW', H.name]], secs: [] }), false, {})
    })
    // Site-to-Site VPN(§17.1): VPN 연결 ↔ CGW · VGW · TGW — 아이콘 위 → 리전 줄 위 제 차선 → 상대 아이콘 위(화살표 없음).
    //   긴 선이 위 차선(짧은 선을 품는다 → 겹쳐 지나지 않는다) · 한 아이콘에 여러 선이면 끝을 옆으로 벌린다(TGW 는 오른쪽 — 왼쪽은 DX 차선)
    for (const R of [...regs.values()].flat()) {
      const ls = M.vpnl.filter((l) => (M.hubs.get(l.a) || {}).region === R.region && B.icon.has(l.a) && B.icon.has(l.b))
      if (!ls.length) continue
      const P = (id) => B.icon.get(id); const span = (l) => Math.abs(P(l.a).cx - P(l.b).cx)
      const lane = new Map([...ls].sort((x, y) => span(y) - span(x) || (x.a < y.a ? -1 : x.a > y.a ? 1 : x.b < y.b ? -1 : 1)).map((l, i) => [l, OY + R.Y + R.vpnBase + 5 * i]))
      const ends = new Map()   // 아이콘 → 그 아이콘에 닿는 선(상대 x 순)
      for (const l of ls) for (const [me, ot] of [[l.a, l.b], [l.b, l.a]]) { if (!ends.has(me)) ends.set(me, []); ends.get(me).push({ l, ox: P(ot).cx }) }
      const ex = (id, l) => {
        const es = ends.get(id).sort((x, y) => x.ox - y.ox); const j = es.findIndex((e) => e.l === l); const m = es.length
        return M.hubs.get(id).kind === 'tgw' ? P(id).cx + 8 + 5 * j : Math.round(P(id).cx + (j - (m - 1) / 2) * 8)
      }
      for (const l of ls) {
        const y = lane.get(l); const xa = ex(l.a, l); const xb = ex(l.b, l); const ta = P(l.a).cy - IC / 2; const tb = P(l.b).cy - IC / 2
        const ob = M.hubs.get(l.b)
        if (l.kind === 'wafp') {
          // WAF 웹 ACL → API Gateway(§17.41) = WAF 색 1.5 · 같은 색 화살촉(API 쪽) · 리전 줄 위 차선(VPN 선과 같은 길)
          const st = ((l.e && l.e.attrs) || {}).stages
          pushLine(l.a, l.b, 'wafp', [[xa, ta], [xa, y], [xb, y], [xb, tb]], () => wafLineDet(M.hubs.get(l.a), ob.name, 'API Gateway', Array.isArray(st) ? st : null), true)
          continue
        }
        if (l.kind === 'lsvctgt') {
          // VPC Lattice 서비스 → Lambda(§17.40) = Lattice 색 1.5 · 같은 색 화살촉(함수 쪽) · 리전 줄 위 차선(VPN 선과 같은 길)
          const ea = (l.e && l.e.attrs) || {}; const sv = M.hubs.get(l.a)
          pushLine(l.a, l.b, 'lsvctgt', [[xa, ta], [xa, y], [xb, y], [xb, tb]], () => ({ title: 'VPC Lattice 서비스 → Lambda 함수', sub: '이 서비스로 들어온 요청이 대상 그룹을 거쳐 이 함수로 전달됩니다',
            rows: [['서비스', sv.name], ['Lambda 함수', ob.name], ['대상 그룹', (ea.target_groups || []).join(', ') || '?']], secs: [] }), true,
          { ltg: { tgs: (ea.target_groups || []).slice(), name: ob.name, type: 'lambda', ports: '' } })
          continue
        }
        pushLine(l.a, l.b, 'vpn', [[xa, ta], [xa, y], [xb, y], [xb, tb]], () => ({ title: 'Site-to-Site VPN', sub: ob.kind === 'cgw' ? 'VPN 연결 ↔ 고객 게이트웨이' : `VPN 연결 ↔ ${ob.kind === 'tgw' ? 'TGW' : 'VGW'}`,
          rows: [['VPN 연결', hubName(l.a)], [ob.kind === 'cgw' ? '고객 게이트웨이' : ob.kind === 'tgw' ? 'TGW' : 'VGW', ob.name], ...(l.att ? [['어태치먼트', natId(X.mById.get(l.att))]] : [])], secs: [] }), false, l.att ? { att: l.att } : null)
      }
    }
    // CloudFront 범위 WAF → 배포(2.12.0 · §17.41): 같은 전역 줄 — WAF 아이콘 위 → 그 줄 아이콘 위 틈의 제 차선(아이콘 위 5px · 선마다 3px 위로 · 여섯째부터 같은 차선) → 배포 위(화살촉).
    //   🔴 아이콘 **밑** 틈은 CloudFront 원본 선·DX 선 차선이다 — 위 틈만 쓴다. 전역 줄이 줄바꿈해 두 끝의 줄이 다르면 전역 틀 안 첫 칸 왼쪽(14px)의 세로 길로 옮겨 탄다
    //   (그 자리는 아이콘·이름표가 없다 — 전역 줄 아이콘은 x=250 부터 · 머리 글자는 그보다 왼쪽 위)
    if (M.wfg.length) {
      const wfN = new Map(); const lane = (t) => { const i = wfN.get(t) || 0; wfN.set(t, i + 1); return t - 5 - 3 * Math.min(i, 5) }
      for (const p of M.wfg) {
        const PA = B.icon.get(p.waf); const PB = B.icon.get(p.id); if (!PA || !PB) continue
        const F = B.frames.find((f) => f.kind === 'glob' && PA.cx >= f.x && PA.cx <= f.x + f.w && PA.cy >= f.y && PA.cy <= f.y + f.h); if (!F) continue
        const ta = Math.round(PA.cy - IC / 2 - 1); const tb = Math.round(PB.cy - IC / 2 - 1)
        const ya = lane(ta); const yb = ta === tb ? ya : lane(tb); const xc = F.x + GLOB_X - 14
        const pts = ta === tb ? [[PA.cx, ta], [PA.cx, ya], [PB.cx, ya], [PB.cx, tb]] : [[PA.cx, ta], [PA.cx, ya], [xc, ya], [xc, yb], [PB.cx, yb], [PB.cx, tb]]
        pushLine(p.waf, p.id, 'wafp', pts, () => wafLineDet(M.globs.get(p.waf), M.globs.get(p.id).name, 'CloudFront 배포', null), true)
      }
    }
    // CloudFront → 원본 LB(#43 · §17.1): 전역 줄 아이콘 밑 → 계정 안 왼쪽 틈(다른 계정이면 계정 사이 길) → LB 의 리전 차선 → LB.
    //   마지막 마디는 후보 넷(VPC 바로 위 틈 · 리전 머리 밑 차선 × LB 위로 · VPC 오른쪽으로) 중 남의 아이콘·이름표·끝이 없는 틀을 가장 적게 지나는 것
    if (M.cfo.length) {
      const hitR = (x1, y1, x2, y2, r) => {   // Liang–Barsky — 선분이 사각형 안을 지나는가(검사 130 과 같은 식)
        let t0 = 0; let t1 = 1; const dx = x2 - x1; const dy = y2 - y1
        for (const [p, q] of [[-dx, x1 - r.x], [dx, r.x + r.w - x1], [-dy, y1 - r.y], [dy, r.y + r.h - y1]]) {
          if (p === 0) { if (q < 0) return false; continue }
          const t = q / p
          if (p < 0) { if (t > t1) return false; if (t > t0) t0 = t } else { if (t < t0) return false; if (t < t1) t1 = t }
        }
        return t1 - t0 > 1e-6
      }
      const sh = (r, d) => ({ x: r.x + d, y: r.y + d, w: r.w - 2 * d, h: r.h - 2 * d })
      const icons = [...B.boxes.values()].filter((b) => /aws-(item|hub|port)/.test(b.kind)).map((b) => ({ k: b.id, r: sh(b, 2) }))
      const labs = [...B.vis.entries()].flatMap(([k, rs]) => rs.slice(1).map((r) => ({ k, r: sh(r, 1) })))
      const hdrs = allLays.filter((x) => x.L.hdr).map((x) => ({ k: x.L.v.id, r: sh(x.L.hdr, 1) }))
      const tiers = B.frames.filter((f) => f.kind === 'acct' || f.kind === 'region')
      const inF = (f, [x, y]) => x >= f.x && x <= f.x + f.w && y >= f.y && y <= f.y + f.h
      const score = (pts, a, b) => {
        const ends = [pts[0], pts[pts.length - 1]]
        const obs = [...icons.filter((o) => o.k !== a && o.k !== b), ...labs.filter((o) => o.k !== a && o.k !== b), ...hdrs,
          ...tiers.filter((f) => !ends.some((q) => inF(f, q))).map((f) => ({ r: sh(f, 2) }))]
        let n = 0
        for (let i = 0; i + 1 < pts.length; i++) for (const o of obs) if (hitR(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1], o.r)) n++
        return n
      }
      // 차선 번호는 높이마다 센다 — 다른 계정 사이 선 둘이 같은 높이 VPC 로 서로 반대로 들어가면 계정 사이 틈에서 포개진다(2026-10-04 fixture 실측)
      // 아이콘 밑 차선도 계정마다 센다 — 전체 순번이면 30계정에서 아래 DX 연결 아이콘까지 내려간다(2026-10-04 CI 실측)
      const gK = new Map(); const hK = new Map(); const vpcK = new Map(); const cK = new Map(); const nx = (m, k) => { const i = m.get(k) || 0; m.set(k, i + 1); return i }
      M.cfo.forEach((p) => {
        const G = M.globs.get(p.cf); const lk = B.cellOf.get(p.lb) || p.lb
        // Cloud WAN(2.11.0 · §17.39) — 끝 = VPC 위 변 포트(아이콘이 아니다) · 그 VPC 가 선의 리전·계정을 정한다
        const cwP = p.cwan ? M.ports.get(p.lb) : null; const cwV = cwP ? M.vpcs.get(cwP.vpc) : null
        const it = cwP ? (cwV ? { kind: 'port', region: cwV.region, vpc: cwV.id, acct: cwV.acct, name: cwV.name } : null) : M.items.get(p.lb)
        const PC = B.icon.get(p.cf); const PL = B.icon.get(lk); const R = it && regOf(it.region); const lay = R && R.lays.find((L) => L.v.id === it.vpc)
        if (!PC || !PL || !lay) return
        const outs = M.cfo.filter((q) => q.cf === p.cf); const inn = M.cfo.filter((q) => q.lb === p.lb)
        const off = (arr) => 6 * (arr.indexOf(p) - (arr.length - 1) / 2)
        const sx = PC.cx + off(outs); const tx = PL.cx + off(inn); const sy = PL.cy + off(inn)
        const gy = PC.cy + IC / 2 + 8 + 4 * (M.dxtgw.length + M.dxl.filter((q) => (M.globs.get(q.dxgw) || {}).acct === G.acct && (M.hubs.get(q.hub) || {}).acct === G.acct).length + nx(cK, G.acct))
        const ry = OY + R.Y; const kv = nx(vpcK, it.vpc)
        const yG = lay.ay - 22 - 4 * nx(gK, Math.round(lay.ay)); const yH = ry + REG_HDR - 4 + 3 * nx(hK, Math.round(ry))
        // 맨 윗줄 VPC 의 「바로 위 틈」은 어태치먼트 차선 묶음 위로 올린다(§17.1 · #75 — 차선이 둘 이상이면 그 높이를 어태치먼트가 쓴다)
        const yGa = R.attN > 1 && Math.abs(lay.ay - ry - R.y0) < 0.5 ? yG - (R.y0 - 22 - (R.attTop - 4)) : yG
        const dxR = lay.ax + lay.w + 10 + 4 * kv
        const xa = G.acct === it.acct
        // 계정 사이 길은 차선 번호를 한 번만 쓴다 — 끝 높이(yE)는 후보마다 갈아 끼운다
        const Xl = xa ? leftX(it.acct) : null
        const mid = xa ? [[Xl, gy], [Xl, 0]] : accOrd(G.acct) < accOrd(it.acct) ? across(G.acct, gy, it.acct, 0) : across(it.acct, 0, G.acct, gy).reverse()
        const cands = []
        for (const yE of [yGa, yH]) {
          const m = mid.map((q) => q.slice()); m[m.length - 1][1] = yE
          // 포트(Cloud WAN)는 위에서만 들어온다 — 옆으로 들어오면 VPC 위 변을 따라 달린다
          if (cwP) { cands.push([...m, [tx, yE], [tx, PL.cy - 10]]); continue }
          cands.push([...m, [tx, yE], [tx, PL.cy - IC / 2 - 1]])
          cands.push([...m, [dxR, yE], [dxR, sy], [PL.cx + IC / 2 + 1, sy]])
        }
        let best = null; let bs = Infinity
        // 굽힘 8 을 넘는 후보는 버린다(검사 73 — 피어링과 같은 상한) · 위로 들어가는 후보는 늘 8 안이다(계정 사이 길 ≤ 6점)
        for (const c of cands) { const pts = [[sx, PC.cy + IC / 2], [sx, gy], ...c]; if (pts.length - 2 > 8) continue; const s = score(pts, p.cf, lk); if (s < bs) { bs = s; best = pts } }
        const a = G.n.attrs || {}
        if (p.cwan) {
          // Cloud WAN(§17.39) = Cloud WAN 색 1.5 · 화살촉 없음(연결 — TGW 어태치먼트와 같다) · 정보창 = 코어 네트워크 · VPC · 세그먼트 · 엣지 위치 · 연결 ID
          const ea = p.ea || {}
          pushLine(p.cf, lk, 'cwanatt', best, () => ({ title: 'Cloud WAN 연결', sub: '이 VPC 가 Cloud WAN 코어 네트워크에 연결되어 있습니다(VPC 연결)',
            rows: [['코어 네트워크', G.name], ['VPC', it.name], ['세그먼트', (ea.segments || []).join(', ') || '?'], ['엣지 위치', (ea.edge_locations || []).map(gaRegion).join(', ') || '?'],
              ['연결 ID', (ea.attachment_ids || []).join(', ') || '?'], ...(xa ? [] : [['계정', `${acctName(G.acct)} → ${acctName(it.acct)}`]])], secs: [] }), false,
          { cwan: { vpc: it.vpc, name: it.name, segs: (ea.segments || []).slice(), locs: (ea.edge_locations || []).slice() } })
          return
        }
        if (p.ga) {
          // Global Accelerator(§17.37) = 가속기 색 1.5 · 같은 색 화살촉(엔드포인트 쪽) · 정보창 = 가속기 · 엔드포인트 · 리전 · 리스너 · 거친 EIP
          const ea = p.ea || {}; const tw = it.kind === 'ec2' ? 'EC2 인스턴스' : '로드밸런서'
          pushLine(p.cf, lk, 'gaep', best, () => ({ title: `Global Accelerator → ${tw}`, sub: 'Global Accelerator의 고정 IP 로 들어온 요청이 이 엔드포인트로 전달됩니다',
            rows: [['Global Accelerator', G.name], [tw, it.name], ['엔드포인트 리전', ea.endpoint_region || '?'], ['리스너', (ea.listener_ports || []).join(', ') || '?'],
              ...(ea.via_eip ? [['탄력적 IP', `${ea.via_eip} — 엔드포인트로 등록한 탄력적 IP 가 이 인스턴스에 연결되어 있습니다`]] : []),
              ...(xa ? [] : [['계정', `${acctName(G.acct)} → ${acctName(it.acct)}`]])], secs: [] }), true,
          { gae: { what: it.kind === 'ec2' ? 'EC2 인스턴스' : '로드 밸런서', name: it.name, region: ea.endpoint_region || '?', eip: ea.via_eip || null } })
          return
        }
        if (p.dns) {
          // Route 53 별칭(§17.30) = 영역 색 1.5 · 같은 색 화살촉(이름이 가리키는 쪽) · 정보창 = 영역 · 레코드 이름 · 로드밸런서
          pushLine(p.cf, lk, 'dnsalias', best, () => ({ title: 'Route 53 별칭 → 로드밸런서', sub: '이 이름으로 들어온 요청이 이 로드밸런서로 전달됩니다(별칭 레코드)',
            rows: [['호스팅 영역', a['영역'] || G.name], ['레코드', p.rec.join('\n') || '?'], ['로드밸런서', it.name], ...(xa ? [] : [['계정', `${acctName(G.acct)} → ${acctName(it.acct)}`]])], secs: [] }), true)
          return
        }
        // 오리진 유형(§17.31) — VPC 원본 · 사용자 도메인 → Route 53 별칭 · 없음 = 주소 꼴(#43 · 줄 없음)
        const vw = cfVia(p.via, p.alias); const tw = it.kind === 'ec2' ? 'EC2 인스턴스' : '로드밸런서'
        pushLine(p.cf, lk, 'cforigin', best, () => ({ title: `CloudFront → ${tw}`, sub: `오리진 — 이 배포로 들어온 요청을 이 ${tw}로 전달합니다`,
          rows: [['CloudFront', a['도메인'] || G.name], [tw, it.name], ...(vw ? [['오리진 유형', vw]] : []), ...(xa ? [] : [['계정', `${acctName(G.acct)} → ${acctName(it.acct)}`]])], secs: [] }), true)
      })
    }
    // 다른 계정 TGW 어태치먼트(§17.1 · §17.15 · #75): 포트 위 → 스포크 리전 제 차선 → 계정 사이 길 → 허브 리전 제 차선 → TGW 밑
    for (const x of xAtt) {
      const ys = x.s[1][1]; const [ex, tb] = x.e[0]
      const mid = x.va === x.ha ? [[leftX(x.va), ys], [leftX(x.va), x.yh]] : accOrd(x.va) < accOrd(x.ha) ? across(x.va, ys, x.ha, x.yh) : across(x.ha, x.yh, x.va, ys).reverse()
      x.l.pts = [...x.s, ...mid, [ex, x.yh], [ex, tb]]
    }
    // LB → 대상 선(§17.27) — 다른 선을 다 그은 뒤에 긋는다(먼저 그은 선과 같은 줄로 겹쳐 달리지 않으려고 그 선들을 본다)
    if (M.cnx.length) cnxLines()
    if (M.lbt.length || M.epp.length || M.vlk.length || M.prx.length || M.ltg.length || M.wfl.length) lbtLines(allLays, regOf)
    // 대표로만 있는 것(피어링 연결·인터넷·AWS 서비스…) — 그리지 않는 대리 상자 하나가 모두 대표한다
    addBox('aws:rep', 'aws-rep', 0, 0, 0, 0, synth('aws:rep', 'aws-rep'), null, { hiddenIds: [...M.hidden.keys()] })
    for (const l of B.lines) {
      const d = orth(l.pts)
      const conn = l.kind !== 'route'
      // LB → 대상(§17.27) = LB 색 1.5 · LB 색 화살촉 · 칩(등록 N · 외부 IP N)은 선 묶음 안 — 검색 흐림이 선과 같이 흐린다
      const lbt = l.kind === 'lbtarget'
      // 엔드포인트 → 제공자(§17.28) = 엔드포인트 색 1.5 · 같은 색 화살촉(넘기는 주인이 엔드포인트 하나다 — §17.27 과 같은 예외)
      const epp = l.kind === 'epprov'
      // Route 53 별칭(§17.30) = 영역 색 1.5 · 같은 색 화살촉
      const dnsa = l.kind === 'dnsalias'
      // 배치 D(2.10.0) — VPC 링크 = API 색 · 프록시 = RDS 색 · 가속기 = 가속기 색(셋 다 1.5 · 같은 색 화살촉 · §17.35~37)
      // #129(2.11.0) — Cloud WAN 연결 = Cloud WAN 색 · Lattice 연결·대상 = Lattice 색(셋 다 1.5) · Connect 오버레이 = TGW 색 점선 1.2(§17.38~40)
      const bdk = { vpclink: 'apigw', dbproxy: 'rdsp', gaep: 'ga', cwanatt: 'cwan', lsnatt: 'lsn', lsvctgt: 'lsvc', cnx: 'tgw', wafp: 'waf' }[l.kind] || null   // WAF 보호(2.12.0 · §17.41) = WAF 색 1.5 · 같은 색 화살촉
      const chip = lbt && l.chip ? `<g class="aw-chip"><rect x="${l.chip.x}" y="${l.chip.y}" width="${l.chip.w}" height="${l.chip.h}" rx="3" fill="${BG}" stroke="#8b949e" stroke-width="1"/><text x="${P2(l.chip.x + l.chip.w / 2)}" y="${P2(l.chip.y + 9.5)}" text-anchor="middle" fill="#c9d1d9" font-size="9.5">${esc(l.chip.text)}</text></g>` : ''
      out.ln.push(`<g class="aw-ln aw-${l.kind}" data-k="${l.k}" data-a="${esc(l.a)}" data-b="${esc(l.b)}"><path class="aw-hitl" d="${d}" fill="none" stroke="transparent" stroke-width="9"/><path class="aw-vis" d="${d}" fill="none" stroke="${lbt ? COL.lb : epp ? COL.vpce : dnsa ? COL.r53 : bdk ? COL[bdk] : conn ? '#9aa4b0' : '#7d8590'}" stroke-width="${l.kind === 'cnx' ? 1.2 : lbt || epp || dnsa || bdk ? 1.5 : conn ? 1.6 : 1.3}"${l.kind === 'peer' ? ' stroke-dasharray="7 5"' : l.kind === 'cnx' ? ' stroke-dasharray="4 3"' : ''}${l.arrow ? ` marker-end="url(#${lbt ? 'aw-arlb' : epp ? 'aw-arep' : dnsa ? 'aw-ardns' : bdk ? 'aw-ar' + bdk : 'aw-ar'})"` : ''}/>${chip}</g>`)
    }
    // 줄 끝 계정의 오른쪽 틈 차선이 그림 밖으로 나가지 않게
    const Wl = Math.max(W, ...B.lines.flatMap((l) => l.pts.map((q) => q[0] - OX + 20)))
    // VIF 정보창(갈래 ② · 사용자 결정 2026-10-24) — 캔버스 아이콘 없이 정보창만(DX 연결·DX 게이트웨이 정보창의 「가상 인터페이스」 줄이 연다 · 주소 `#sel=` 로도 열린다)
    for (const vs of M.vifOf.values()) for (const n of vs) if (!B.det.has(n.id)) B.det.set(n.id, () => detVif(n))
    return { out, W: Wl + OX, H: H + OY }
  }

  // ======================================================================== LB → 대상 선(§17.27)
  // (LB 칸, 대상 칸) 마다 선 하나 · 길 = VPC 안 **틈의 좌표**(AZ 줄 사이 · 서브넷 칸 사이 · 아이콘 칸 경계 · 서브넷 이름 줄 · 오른쪽 열
  // 가장자리)로 만든 격자에서 굽힘을 아끼는 가장 짧은 길. 남의 아이콘 그림·이름표·포트·VPC·서브넷·AZ 이름은 지나지 않는다(§17.14 · 검사 130).
  // 🔴 결정론 — 좌표는 정수 · 정렬 · 동점이면 좌표가 작은 쪽(힙이 (값, 칸 번호, 축)으로 줄 선다). 같은 그림 → 같은 길.
  // 포개짐 값 = 1px 에 20(2.8.0 · 굽힘 30) — 8 이던 동안 LB 왼쪽 변에서 나간 첫 마디가 서브넷 → TGW 라우트 머리 줄(10px)과 겹치는 길이
  //   돌아가는 길보다 싸서 골라졌다(lbt 픽스처 2.8.0 실측 · 검사 148 「포개짐 0」). 포개짐은 굽힘 하나보다 비싸야 한다
  //   🔁 2.9.0(#114) 30 — 새 카드 넷이 오른쪽 열을 채운 #114 lbt 픽스처에서 20·25 이면 `edge-nlb` 의 두 선이 y=422 로 400px 포개진다(검사 148 FAIL ·
  //   실측 2026-10-20). 40 이면 포개짐은 없지만 굽힘 상한이 이 픽스처에서 한 번도 걸리지 않아 브레이크 `148-굽힘상한없음` 이 죽었다 → 30 = 둘 다 산다
  //   (2.8.0 브레이크 `148-포개짐값작음` 을 이 값에 다시 걸었다)
  const LBT_BEND = 30; const LBT_LAP = 30; const LBT_BMAX = 6
  const LBT_KIND = { instance: '인스턴스', ip: 'IP', lambda: 'Lambda', alb: 'ALB' }
  // 엔진 `unresolved_targets` 의 까닭 코드 → 사람 말(ADR 0002 §8). 모르는 코드 = 「<코드> — 수집하지 않는 서비스」(코드를 지우지 않는다)
  const LBT_WHY = { not_collected: '이 수집본에 없는 리소스 — 수집 범위 밖이거나 수집하지 않았습니다', not_in_scope: '수집 범위 밖 계정·리전',
    no_eni: 'VPC 안 IP · 소유 리소스 없음(ENI 없음)', eni_unread: 'ENI 를 읽지 못함 — 권한 부족(「없음」이 아닙니다)', ambiguous: '같은 IP 의 ENI 가 둘 이상 — 하나로 정하지 않았습니다',
    // 2.9.0(#114 · §17.32) — 수집하는 서비스가 됐다. 이 코드 = 그 ENI 의 주인 카드를 못 찾았다(권한 거부 · 범위 밖 · 지워졌다)
    elasticache: 'ElastiCache — 연결된 캐시 클러스터를 찾지 못함(권한이 없거나 수집 범위 밖일 수 있습니다)', opensearch: 'OpenSearch — 연결된 도메인을 찾지 못함(권한이 없거나 수집 범위 밖일 수 있습니다)', msk: 'MSK(Kafka) — 연결된 클러스터를 찾지 못함(브로커 목록을 읽지 못했거나 서버리스 클러스터일 수 있습니다)',
    efs: 'EFS 마운트 대상 — 수집하지 않는 서비스', lambda: 'Lambda(VPC ENI) — 함수를 특정하지 못함', nat_gateway: 'NAT 게이트웨이의 ENI',
    ecs_task: 'ECS 태스크 — 이 대상 그룹을 사용하는 서비스를 특정하지 못함', rds: 'RDS — DB 를 특정하지 못함(같은 VPC·AZ 에 DB 가 여러 개이고 한 클러스터의 멤버도 아님)',
    vpc_endpoint: 'VPC 엔드포인트 — 이 수집본에 없음', load_balancer: '로드밸런서의 ENI — 이 수집본에 없음', eks: 'EKS — 클러스터가 이 수집본에 없음',
    redshift: 'Redshift — 수집하지 않는 서비스', dms: 'DMS — 수집하지 않는 서비스', directory_service: 'Directory Service — 수집하지 않는 서비스',
    sagemaker: 'SageMaker — 리소스를 특정하지 못함', eni: '소유자를 알 수 없는 ENI(연결된 리소스 없음)' }
  const lbtWhy = (code) => LBT_WHY[code] || `${code} — 수집하지 않는 서비스`
  // 라우트 ENI 의 까닭(2.8.0 · P2-2) — ENI 를 물은 뒤의 말(LB 대상의 「VPC 안 IP」 말과 다르다) · 나머지는 LB 대상과 같은 서비스 이름
  const ROUTE_WHY = { no_eni: '이 ENI 를 찾지 못했습니다(삭제되었거나 다른 계정의 ENI)', eni_unread: 'ENI 를 읽지 못함(권한)', not_collected: '소유 리소스가 이 수집본에 없습니다(수집하지 않음)', ambiguous: '소유 리소스를 하나로 정하지 못함' }
  const routeWhy = (code) => ROUTE_WHY[code] || lbtWhy(code)
  // 라우트 선을 이미 따로 긋는 대상(서브넷 칸 배치 · 레인 차선) — 나머지 같은 VPC 아이콘 대상(ENI 주인 · P2-2)은 넘김 선 길 찾기로 긋는다
  const ROUTE_DRAWN = new Set(['igw', 'nat', 'vpce'])
  // 엔드포인트 → 제공자를 무엇으로 이었나(엔진 `resolved_by` · §17.28)
  const EPP_HOW = { endpoint_service: '제공자 계정의 엔드포인트 서비스 구성이 이 서비스 뒤의 로드밸런서를 가리킵니다',
    firewall_endpoint: '방화벽 동기화 상태에 이 엔드포인트가 방화벽 엔드포인트로 나와 있습니다 — 이 서브넷의 트래픽은 방화벽을 거칩니다' }
  // TGW Connect(2.11.0 · §17.38) — Connect 어태치먼트 = 전송 어태치먼트 선(VPC 포트 → TGW · DX 게이트웨이 ↔ TGW) **위에 겹친** 점선(TGW 색).
  //   새 길을 찾지 않는다 — 그 선의 곧은 마디마다 4px 옆으로 민 평행선이다(끝점은 같은 아이콘 변 위) · Connect 가 여럿이면 4px 씩 더 민다
  const offsetOrth = (pts, d) => {
    const n = pts.length; if (n < 2) return pts
    const segV = (i) => Math.abs(pts[i][0] - pts[i + 1][0]) < 0.01   // i → i+1 이 세로인가
    const out = []
    for (let i = 0; i < n; i++) {
      const [x, y] = pts[i]
      if (i === 0) { out.push(segV(0) ? [x + d, y] : [x, y + d]); continue }
      if (i === n - 1) { out.push(segV(n - 2) ? [x + d, y] : [x, y + d]); continue }
      out.push([segV(i - 1) || segV(i) ? x + d : x, !segV(i - 1) || !segV(i) ? y + d : y])
    }
    return out
  }
  function cnxLines () {
    const per = new Map()
    for (const c of M.cnx) {
      const base = c.port ? B.lines.find((l) => l.kind === 'att' && l.a === c.port && l.pts) : c.dx ? B.lines.find((l) => l.kind === 'dxtgw' && l.att === c.dx.att && l.pts) : null
      if (!base) continue
      const j = per.get(base.k) || 0; per.set(base.k, j + 1)
      const cn = c.n.connect || {}; const peers = cn.peers
      const tr = X.mById.get(cn.on); const onDx = !!c.dx
      // 두 끝 = 전송 선의 두 끝(포트 · TGW / TGW · DX 게이트웨이) — 검색 흐림·길 강조가 같은 칸으로 판단한다
      pushLine(base.a, base.b, 'cnx', offsetOrth(base.pts, 4 * (j + 1)), () => ({ title: 'TGW Connect(SD-WAN 오버레이)', sub: `GRE 터널이 이 ${onDx ? 'Direct Connect 게이트웨이' : 'VPC'} 어태치먼트 위로 지나갑니다 — 피어(SD-WAN 장비)와 TGW 가 BGP 로 경로를 주고받습니다`,
        rows: [['Connect 어태치먼트', `${disp(c.n)}${named(c.n) ? ` · ${natId(c.n)}` : ''}`], ['전송 어태치먼트', `${onDx ? 'DX 게이트웨이' : 'VPC'} · ${tr ? natId(tr) : cn.transport || '?'}`],
          ['TGW', hubName(c.tgw)], ['프로토콜', cn.proto ? String(cn.proto).toUpperCase() : '?'], ...(c.n.attrs && c.n.attrs.state ? [['상태', stateKo(c.n.attrs.state)]] : [])],
        secs: [peerSec(c)] }), false, { att: c.id, rep: [c.id], base: base.k, cnx: { id: c.id, on: cn.on, dx: onDx, peers: Array.isArray(peers) ? peers.length : null } })
    }
  }
  // Connect 피어 칸 — 피어마다 한 줄(GRE 주소 · BGP 내부 CIDR · ASN) · 못 읽었으면 「읽지 못함」(「피어 없음」과 가른다 · 불변식 ④)
  function peerSec (c) {
    const peers = (c.n.connect || {}).peers
    if (!Array.isArray(peers)) return M.partial.has(c.id) ? urSec('Connect 피어') : sec('Connect 피어', [], { nc: true })
    return sec('Connect 피어', peers.map((p) => ({ a: p.name || p.id, b: `GRE ${p.tgw} ↔ ${p.peer} · BGP 내부 CIDR ${p.cidrs.join(', ') || '?'} · ASN ${p.tasn} ↔ ${p.pasn}` })),
      { empty: '(없음) — 이 Connect 에 피어가 없습니다(GRE 터널이 없어 경로를 주고받지 않습니다)' })
  }
  function lbtLines (allLays, regOf) {
    const layOf = new Map(allLays.map((x) => [x.L.v.id, x]))
    const kOf = (id) => B.cellOf.get(id) || id
    const vpcOfKey = (k) => { const ids = B.cellIds.get(k) || [k]; const it = M.items.get(ids[0]); return it ? it.vpc : null }
    // ① 묶기 — 묶음 칸(`EC2 ×N`)에 든 대상들 · 같은 출구로 나가는 외부 IP 들은 선 하나
    // ASG 로 접기(2.8.0 · §17.27 「ASG」 · 사용자 결정 2026-10-20 P1-2) — 인스턴스가 지금 그 ASG 의 것이고(엔진 `auto_scaling_group_id`)
    //   그 선의 대상 그룹이 **전부** 그 ASG 가 든 그룹이면(ASG `target_group_arns`) 선 끝 = ASG 아이콘. 같은 LB 에서 그 ASG 로 접힌 선은 하나다.
    //   🔴 그룹 하나라도 ASG 가 안 든 것이면 접지 않는다 — 손으로 등록한 그룹까지 ASG 가 넘기는 것처럼 보이면 거짓이다
    const asgOf = (r, lbIt) => {
      const t = M.items.get(r.id); const g = t && t.kind === 'ec2' && t.n.asg ? M.items.get(t.n.asg) : null
      if (!g || g.kind !== 'asg' || g.acct !== lbIt.acct || g.region !== lbIt.region || !Array.isArray(g.n.targetGroups)) return null
      const tgs = (r.e.attrs || {}).target_groups || []
      return tgs.length && tgs.every((x) => g.n.targetGroups.includes(x)) ? g.id : null
    }
    const G = new Map()
    for (const r of M.lbt) {
      const lbIt = M.items.get(r.lb); const a = kOf(r.lb)
      const fold = r.ext ? null : asgOf(r, lbIt)
      const b = r.ext ? (r.exit ? r.exit.key : lbIt.vpc) : kOf(fold || r.id)
      const gk = a + '\u0000' + b
      let g = G.get(gk)
      if (!g) { g = { a, b, lb: lbIt, ext: !!r.ext, exit: r.ext ? r.exit : null, ids: new Set(), lbe: [], tgs: new Set(), ports: new Set(), types: new Set(), cnt: 0, ips: new Set(), raw: new Set(), fold: !!fold }; G.set(gk, g) }
      const at = r.e.attrs || {}
      g.lbe.push(r.e.id); if (r.id) g.ids.add(r.id)
      for (const t of at.target_groups || []) g.tgs.add(t)
      for (const p of at.ports || []) g.ports.add(p)
      for (const t of at.target_types || []) g.types.add(t)
      g.cnt += at.target_count || 0
      if (r.ext) { g.ips.add(r.ip); for (const x of r.raw || []) g.raw.add(x) }
    }
    // 엔드포인트 → 제공자(§17.28) — 같은 길 찾기·끝점 규칙을 쓴다(출발 = 엔드포인트 칸 · 묶음 칸이면 묶음 · 도착 = 제공자 아이콘)
    for (const r of M.epp) {
      const vIt = M.items.get(r.lb); const a = kOf(r.lb); const b = kOf(r.id)
      const gk = 'epprov\u0000' + a + '\u0000' + b
      let g = G.get(gk)
      if (!g) { g = { kind: 'epprov', a, b, lb: vIt, ext: false, exit: null, ids: new Set(), lbe: [], tgs: new Set(), ports: new Set(), types: new Set(), cnt: 0, ips: new Set(), raw: new Set(), svcs: new Set(), eps: new Set() }; G.set(gk, g) }
      g.lbe.push(r.e.id); g.ids.add(r.id); g.eps.add(r.lb)
      if ((r.e.attrs || {}).service_name) g.svcs.add(r.e.attrs.service_name)
      g.how = (g.how || new Set()).add((r.e.attrs || {}).resolved_by || '?')
    }
    // API Gateway → LB(VPC 링크 · 2.10.0 · §17.35) — LB → 리전 줄(Lambda) 선과 **같은 길**을 찾고 거꾸로 긋는다(화살촉 = LB 쪽 · 출발 = API 아이콘 밑 변)
    for (const r of M.vlk) {
      const lbIt = M.items.get(r.lb); const a = kOf(r.lb); const gk = 'vpclink\u0000' + a + '\u0000' + r.api
      let g = G.get(gk)
      if (!g) { g = { kind: 'vpclink', a, b: r.api, lb: lbIt, ext: false, exit: null, ids: new Set(), lbe: [], tgs: new Set(), ports: new Set(), types: new Set(), cnt: 0, ips: new Set(), raw: new Set(), vids: new Set(), vnames: new Set() }; G.set(gk, g) }
      g.lbe.push(r.e.id); g.ids.add(r.api)
      for (const x of (r.e.attrs || {}).vpc_link_ids || []) g.vids.add(x)
      for (const x of (r.e.attrs || {}).vpc_link_names || []) g.vnames.add(x)
    }
    // RDS 프록시 → DB(2.10.0 · §17.36) — 넘김 선과 같은 길 찾기(출발 = 프록시 칸 · 도착 = RDS·클러스터 아이콘)
    for (const r of M.prx) {
      const pIt = M.items.get(r.px); const a = kOf(r.px); const b = kOf(r.id); const gk = 'dbproxy\u0000' + a + '\u0000' + b
      let g = G.get(gk)
      if (!g) { g = { kind: 'dbproxy', a, b, lb: pIt, ext: false, exit: null, ids: new Set(), lbe: [], tgs: new Set(), ports: new Set(), types: new Set(), cnt: 0, ips: new Set(), raw: new Set() }; G.set(gk, g) }
      g.lbe.push(r.e.id); g.ids.add(r.id)
      for (const x of (r.e.attrs || {}).ports || []) g.ports.add(x)
      g.types.add((r.e.attrs || {}).target_type || 'instance')
    }
    // VPC Lattice 서비스 → EC2·ALB(2.11.0 · §17.40) — API Gateway VPC 링크와 **같은 길**(대상 → 리전 줄 밑 → 서비스 아이콘)을 찾고 거꾸로 긋는다(화살촉 = 대상 쪽)
    for (const r of M.ltg) {
      const tIt = M.items.get(r.id); const a = kOf(r.id); const gk = 'lsvctgt\u0000' + a + '\u0000' + r.svc
      let g = G.get(gk)
      if (!g) { g = { kind: 'lsvctgt', a, b: r.svc, lb: tIt, ext: false, exit: null, ids: new Set(), lbe: [], tgs: new Set(), ports: new Set(), types: new Set(), cnt: 0, ips: new Set(), raw: new Set() }; G.set(gk, g) }
      g.lbe.push(r.e.id); g.ids.add(r.id)
      for (const x of (r.e.attrs || {}).target_groups || []) g.tgs.add(x)
      for (const x of (r.e.attrs || {}).ports || []) g.ports.add(x)
      g.types.add((r.e.attrs || {}).target_type || 'instance')
    }
    // WAF 웹 ACL → ALB(2.12.0 · §17.41) — Lattice 서비스 → ALB 와 **같은 길**(LB → 리전 줄 밑 → WAF 아이콘)을 찾고 거꾸로 긋는다(화살촉 = LB 쪽)
    for (const r of M.wfl) {
      const tIt = M.items.get(r.id); const a = kOf(r.id); const gk = 'wafp\u0000' + a + '\u0000' + r.waf
      let g = G.get(gk)
      if (!g) { g = { kind: 'wafp', a, b: r.waf, lb: tIt, ext: false, exit: null, ids: new Set(), lbe: [], tgs: new Set(), ports: new Set(), types: new Set(), cnt: 0, ips: new Set(), raw: new Set() }; G.set(gk, g) }
      g.lbe.push(r.e.id); g.ids.add(r.id)
    }
    // 서브넷 → ENI 주인(§17.29 · 2.8.0 · P2-2) — 라우트 대상이 IGW·NAT·엔드포인트·포트가 아니라 같은 VPC 의 **아이콘**(NVA EC2 · NAT 인스턴스 · 방화벽
    //   어플라이언스 …)이면 이 길 찾기로 긋는다(서브넷 테두리 → 그 아이콘 변). 서브넷 칸을 끈 화면에는 서브넷이 없다 → 긋지 않는다(정보창 「선 없이 이어진 것」)
    if (O.sn) {
      for (const s of [...M.subnets.values()].sort((x, y) => (x.id < y.id ? -1 : 1))) {
        for (const t of [...s.exits].sort()) {
          const it = M.items.get(t); if (!it || it.vpc !== s.vpc || ROUTE_DRAWN.has(it.kind) || it.subnets.includes(s.id)) continue
          const b = kOf(t); const gk = 'reni\u0000' + s.id + '\u0000' + b
          let g = G.get(gk)
          if (!g) { g = { kind: 'reni', a: s.id, b, lb: { vpc: s.vpc, region: (M.vpcs.get(s.vpc) || {}).region, name: s.name }, sub: s.id, ext: false, exit: null, ids: new Set(), lbe: [], tgs: new Set(), ports: new Set(), types: new Set(), cnt: 0, ips: new Set(), raw: new Set() }; G.set(gk, g) }
          g.ids.add(t)
        }
      }
    }
    const groups = [...G.values()].sort((x, y) => (x.a < y.a ? -1 : x.a > y.a ? 1 : x.b < y.b ? -1 : x.b > y.b ? 1 : 0))
    // ② 끝점 — 아이콘 그림 상자(묶음 = 겹친 그림만큼 위·오른쪽으로 넓다 · 포트 = 원) 밖 1px. 위·왼쪽·오른쪽 변(아래는 이름표)
    const box = (k) => {
      const c = B.icon.get(k); if (!c) return null
      if (M.ports.has(k) || M.pp.has(k)) return { x: c.cx - 9, y: c.cy - 9, w: 18, h: 18 }
      const ag = (B.cellIds.get(k) || []).length > 1 ? 5 : 0
      return { x: c.cx - IC / 2 - 1, y: c.cy - IC / 2 - 1 - ag, w: IC + 2 + ag, h: IC + 2 + ag }
    }
    // `j` = 같은 아이콘에 닿는 몇 번째 선인가 → 변 위에서 6px 씩 벌린다(0 · −6 · +6 · −12 · +12 · 그 뒤는 되돌아온다) — 한 변을 여러 선이
    //   같이 쓰면 첫 마디가 한 줄로 포개진다(검사 148 「포개짐 0」)
    const FAN = [0, -6, 6, -12, 12, -3, 3, -9, 9, -15, 15]   // 2.9.0(#114) — 다섯을 넘으면 되돌아와 첫 마디가 포개졌다(NLB 대상 11 · 검사 148 실측) → 3px 틈을 더 쓴다
    const sides = (k, which, j = 0) => {
      const b = box(k); const c = B.icon.get(k); if (!b) return []
      const o = FAN[j % FAN.length]
      const all = { up: [Math.round(c.cx + o), Math.floor(b.y) - 1, 'U'], left: [Math.floor(b.x) - 1, Math.round(c.cy + o), 'L'], right: [Math.ceil(b.x + b.w) + 1, Math.round(c.cy + o), 'R'] }
      return which.map((w) => ({ x: all[w][0], y: all[w][1], dir: all[w][2] }))
    }
    const fanOut = new Map(); const fanIn = new Map(); const nxtF = (m, k) => { const i = m.get(k) || 0; m.set(k, i + 1); return i }
    // 나가는 방향의 반대 = 들어오는 방향(대상 위 변 = 아래로 들어온다)
    const IN = { U: 'D', L: 'R', R: 'L' }
    // ③ 장애물 — 그 VPC 안의 아이콘 그림 · 이름표 · 포트 · VPC·서브넷·AZ 이름(끝점 아이콘도 장애물이다 — 끝점은 그 상자 밖에 있다)
    const obsC = new Map()
    const obstacles = (L) => {
      if (obsC.has(L)) return obsC.get(L)
      const inV = (c) => c.cx >= L.ax - 12 && c.cx <= L.ax + L.w + 12 && c.cy >= L.ay - 12 && c.cy <= L.ay + L.h + 12
      const out = []
      for (const [k, c] of B.icon) {
        if (!inV(c)) continue
        const b = box(k); if (b) out.push(b)
        for (const v of (B.vis.get(k) || []).slice(1)) out.push({ x: v.x - 1, y: v.y - 1, w: v.w + 2, h: v.h + 2 })
      }
      if (L.hdr) out.push(L.hdr)
      for (const sb of L.subs) { const r = B.snl.get(sb.s.id); if (r) out.push(r) }
      for (const r of B.azl.get(L.v.id) || []) out.push(r)
      obsC.set(L, out); return out
    }
    const inside = (r, x, y) => x > r.x && x < r.x + r.w && y > r.y && y < r.y + r.h
    // ④ 먼저 그은 선의 곧은 구간(같은 줄로 겹쳐 달리면 값을 크게 — 검사 148 「8px 넘게 포개짐 0」)
    const SH = new Map(); const SV = new Map()
    const addSeg = (p, q) => {
      if (Math.abs(p[1] - q[1]) < 0.01) push(SH, Math.round(p[1]), [Math.min(p[0], q[0]), Math.max(p[0], q[0])])
      else if (Math.abs(p[0] - q[0]) < 0.01) push(SV, Math.round(p[0]), [Math.min(p[1], q[1]), Math.max(p[1], q[1])])
    }
    for (const l of B.lines) if (l.pts) for (let i = 1; i < l.pts.length; i++) addSeg(l.pts[i - 1], l.pts[i])
    const lap = (m, c, lo, hi) => { let s = 0; for (let d = -2; d <= 2; d++) for (const [a, b] of m.get(c + d) || []) { const o = Math.min(hi, b) - Math.max(lo, a); if (o > 0) s += o } return s }
    // ⑤ 격자 — VPC 마다 한 벌(그 VPC 에 닿는 선들의 끝점 좌표를 다 넣는다)
    const plan = []; const extra = new Map()
    const ext = (vid, pts) => { for (const p of pts) push(extra, vid, p) }
    for (const g of groups) {
      if (g.kind === 'reni') {
        // 출발 = 서브넷 테두리(오른쪽 · 왼쪽 = 머리 줄 높이 · 아래 = 오른쪽 끝에서 안쪽 4px) — 바깥 3px 의 틈에서 시작해 테두리까지 잇는다
        const L1 = layOf.get(g.lb.vpc); const T1 = L1 && L1.L; const sb = T1 && T1.subs.find((x) => x.s.id === g.a)
        if (!sb || !B.icon.get(g.b) || vpcOfKey(g.b) !== g.lb.vpc) continue
        const ax = T1.ax; const ay = T1.ay; const yy = Math.round(ay + sb.y + 13)
        const st = [{ x: Math.round(ax + sb.x + sb.w + 3), y: yy, dir: 'R', head: [Math.round(ax + sb.x + sb.w), yy] },
          { x: Math.round(ax + sb.x - 3), y: yy, dir: 'L', head: [Math.round(ax + sb.x), yy] },
          { x: Math.round(ax + sb.x + sb.w - 4), y: Math.round(ay + sb.bottom + 3), dir: 'D', head: [Math.round(ax + sb.x + sb.w - 4), Math.round(ay + sb.bottom)] }]
        const gl = sides(g.b, ['up', 'left', 'right'], nxtF(fanIn, g.b)).map((s) => ({ ...s, dir: IN[s.dir] }))
        plan.push({ g, mode: 'in', L: T1, st, goals: gl })
        ext(T1.v.id, [...st.map((s) => [s.x, s.y]), ...gl.map((s) => [s.x, s.y])])
        continue
      }
      const L0 = layOf.get(g.lb.vpc); const PS = B.icon.get(g.a); if (!L0 || !PS) continue
      const L = L0.L; const st = sides(g.a, ['up', 'left', 'right'], nxtF(fanOut, g.a))
      let p = null
      if (g.ext) {
        const pc = g.exit ? B.icon.get(g.exit.key) : null
        if (pc) {
          const top = Math.abs(pc.cy - L.ay) < 1
          const gx = Math.round(pc.cx + 6); const gy = Math.round(top ? pc.cy + 11 : pc.cy - 11)
          p = { g, mode: 'in', L, st, goals: [{ x: gx, y: gy, dir: top ? 'U' : 'D' }], tail: [[gx, Math.round(top ? pc.cy + 7 : pc.cy - 7)]] }
        } else {
          const gx = Math.round(L.ax + L.w - 4)
          p = { g, mode: 'in', L, st, border: gx, goals: [], tail: null }
        }
      } else if (M.hubs.has(g.b)) {
        const h = M.hubs.get(g.b); const PH = B.icon.get(g.b); const R = regOf(h.region)
        if (PH && R && h.region === g.lb.region) p = { g, mode: 'hub', L, st, PH, R }
      } else {
        const tv = vpcOfKey(g.b); const T0 = tv && layOf.get(tv)
        if (T0 && B.icon.get(g.b)) {
          const gl = sides(g.b, ['up', 'left', 'right'], nxtF(fanIn, g.b)).map((s) => ({ ...s, dir: IN[s.dir] }))
          if (tv === g.lb.vpc) p = { g, mode: 'in', L, st, goals: gl }
          else if (T0.R === L0.R) p = { g, mode: 'cross', L, st, T: T0.L, goals: gl }
        }
      }
      if (!p) continue
      plan.push(p)
      ext(p.L.v.id, [...p.st.map((s) => [s.x, s.y]), ...(p.goals || []).map((s) => [s.x, s.y])])
      if (p.mode === 'cross') ext(p.T.v.id, p.goals.map((s) => [s.x, s.y]))
      if (p.border) ext(p.L.v.id, [[p.border, Math.round(PS.cy)]])
    }
    const gridC = new Map()
    const gridOf = (L) => {
      if (gridC.has(L)) return gridC.get(L)
      const ax = L.ax; const ay = L.ay; const X = new Set(); const Y = new Set()
      const ix = (v) => X.add(Math.round(v)); const iy = (v) => Y.add(Math.round(v))
      // 틈마다 차선 둘 이상(3~4px 간격) — 나란히 가는 선이 한 줄로 포개지지 않게(같은 줄을 타면 값이 크다 · `LBT_LAP`)
      ix(ax + 6); ix(ax + 10); ix(ax + L.w - 4); ix(ax + L.w - 8); ix(ax + L.w - 12)
      iy(ay + 14); iy(ay + 18); iy(ay + 22); iy(ay + VPC_HDR + 8); iy(ay + VPC_HDR + 11); iy(ay + L.h - 4); iy(ay + L.h - 8)
      for (const b of L.azb) { ix(ax + b.x - 3); ix(ax + b.x - 6); ix(ax + b.x + b.w + 3); ix(ax + b.x + b.w + 6); iy(ay + b.y - 3); iy(ay + b.y - 7); iy(ay + b.y + b.h + 3); iy(ay + b.y + b.h + 7) }
      for (const sb of L.subs) { ix(ax + sb.x - 3); ix(ax + sb.x - 11); ix(ax + sb.x + sb.w + 3); ix(ax + sb.x + sb.w + 11); ix(ax + sb.x + 4); ix(ax + sb.x + sb.w - 4); iy(ay + sb.y + 9); iy(ay + sb.y + 13); iy(ay + sb.bottom + 3) }
      for (const c of L.cells) { ix(ax + c.x); ix(ax + c.x + CW); iy(ay + c.y - 1); iy(ay + c.y + CH + 1) }
      for (const [x, y] of extra.get(L.v.id) || []) { ix(x); iy(y) }
      const xs = [...X].filter((x) => x > ax && x < ax + L.w).sort((a, b) => a - b)
      const ys = [...Y].filter((y) => y > ay && y < ay + L.h).sort((a, b) => a - b)
      const nx = xs.length; const ny = ys.length; const obs = obstacles(L)
      const id = (i, j) => i * ny + j
      const ok = new Uint8Array(nx * ny); const bh = new Uint8Array(nx * ny); const bv = new Uint8Array(nx * ny)
      const rowO = ys.map((y) => obs.filter((r) => y > r.y && y < r.y + r.h)); const colO = xs.map((x) => obs.filter((r) => x > r.x && x < r.x + r.w))
      for (let i = 0; i < nx; i++) for (let j = 0; j < ny; j++) ok[id(i, j)] = rowO[j].some((r) => inside(r, xs[i], ys[j])) ? 0 : 1
      // 가로 칸(i → i+1, j) · 세로 칸(i, j → j+1)이 장애물을 지나면 막힌다
      for (let j = 0; j < ny; j++) for (let i = 0; i + 1 < nx; i++) bh[id(i, j)] = rowO[j].some((r) => Math.max(r.x, xs[i]) < Math.min(r.x + r.w, xs[i + 1])) ? 1 : 0
      for (let i = 0; i < nx; i++) for (let j = 0; j + 1 < ny; j++) bv[id(i, j)] = colO[i].some((r) => Math.max(r.y, ys[j]) < Math.min(r.y + r.h, ys[j + 1])) ? 1 : 0
      const G2 = { L, xs, ys, nx, ny, id, ok, bh, bv, xi: new Map(xs.map((x, i) => [x, i])), yi: new Map(ys.map((y, j) => [y, j])), obs }
      gridC.set(L, G2); return G2
    }
    // ⑥ 길 찾기 — 상태 = (칸, 마지막 축) · 힙 = (값, 칸, 축) 순(결정론). 출발 = 끝점 변에서 바깥 방향으로만 · 도착 = 정해진 방향으로만
    const DIRS = { R: [1, 0, 0], L: [-1, 0, 0], D: [0, 1, 1], U: [0, -1, 1] }
    // 🔵 굽힘 수도 상태에 넣는다(2.8.0) — 값(굽힘 30 · 포개짐 20/px)만으로는 막힌 격자에서 굽힘 7 인 길이 「가장 싸다」가 됐다(검사 148 ·
    //   lbt 픽스처에 전역 줄·VPC Lambda 가 들어간 뒤 `web-alb → lbt-eks` 가 세 조합에서 7). 굽힘 6 을 넘는 걸음은 아예 없다
    const K = LBT_BMAX + 1
    const SI = (n, ax, b) => (n * 2 + ax) * K + b
    const route = (G2, starts, goals) => {
      const { xs, ys, nx, ny, id, ok, bh, bv } = G2
      const best = new Float64Array(nx * ny * 2 * K).fill(Infinity); const prev = new Int32Array(nx * ny * 2 * K).fill(-1)
      const heap = []
      const less = (a, b) => (a[0] !== b[0] ? a[0] < b[0] : a[1] !== b[1] ? a[1] < b[1] : a[2] !== b[2] ? a[2] < b[2] : a[3] < b[3])
      const hpush = (v) => { heap.push(v); let i = heap.length - 1; while (i > 0) { const p = (i - 1) >> 1; if (!less(heap[i], heap[p])) break; [heap[i], heap[p]] = [heap[p], heap[i]]; i = p } }
      const hpop = () => { const top = heap[0]; const last = heap.pop(); if (heap.length) { heap[0] = last; let i = 0; for (;;) { const l = 2 * i + 1; const r = l + 1; let m = i; if (l < heap.length && less(heap[l], heap[m])) m = l; if (r < heap.length && less(heap[r], heap[m])) m = r; if (m === i) break; [heap[i], heap[m]] = [heap[m], heap[i]]; i = m } } return top }
      const gmap = new Map(); for (const g of goals) { const i = G2.xi.get(g.x); const j = G2.yi.get(g.y); if (i !== undefined && j !== undefined) gmap.set(id(i, j), g) }
      const sdir = new Map()
      for (const s of starts) {
        const i = G2.xi.get(s.x); const j = G2.yi.get(s.y); if (i === undefined || j === undefined || !ok[id(i, j)]) continue
        const n = id(i, j); sdir.set(n, s.dir); const ax = DIRS[s.dir][2]; const c0 = s.cost || 0
        if (c0 < best[SI(n, ax, 0)]) { best[SI(n, ax, 0)] = c0; hpush([c0, n, ax, 0]) }
      }
      let done = null; let doneCost = Infinity
      while (heap.length) {
        const [c, n, ax, b] = hpop()
        const cur = SI(n, ax, b)
        if (c > best[cur]) continue
        if (c >= doneCost) break
        const i = Math.floor(n / ny); const j = n % ny
        for (const [dk, [dx, dy, nax]] of Object.entries(DIRS)) {
          if (prev[cur] < 0 && sdir.has(n) && dk !== sdir.get(n)) continue   // 출발점 = 바깥 방향 하나(첫 걸음의 축 = 출발 축 → 굽힘 0)
          const nb = b + (nax !== ax ? 1 : 0); if (nb > LBT_BMAX) continue
          const i2 = i + dx; const j2 = j + dy; if (i2 < 0 || j2 < 0 || i2 >= nx || j2 >= ny) continue
          const m = id(i2, j2); if (!ok[m]) continue
          if (dx && bh[id(Math.min(i, i2), j)]) continue
          if (dy && bv[id(i, Math.min(j, j2))]) continue
          const len = Math.abs(xs[i2] - xs[i]) + Math.abs(ys[j2] - ys[j])
          const pen = dx ? lap(SH, ys[j], Math.min(xs[i], xs[i2]), Math.max(xs[i], xs[i2])) : lap(SV, xs[i], Math.min(ys[j], ys[j2]), Math.max(ys[j], ys[j2]))
          const nc = c + len + LBT_LAP * pen + (nax !== ax ? LBT_BEND : 0)
          const g = gmap.get(m)
          if (g) {
            if (g.dir && g.dir !== dk) continue
            const tot = nc + (g.bias || 0)
            if (tot < doneCost) { doneCost = tot; done = { from: cur, m, g, nax } }
            continue
          }
          const si = SI(m, nax, nb)
          if (nc < best[si]) { best[si] = nc; prev[si] = cur; hpush([nc, m, nax, nb]) }
        }
      }
      if (!done) return null
      const nodes = [done.m]; let s = done.from
      while (s >= 0) { nodes.push(Math.floor(s / (2 * K))); s = prev[s] }
      nodes.reverse()
      const pts = []
      for (const n of nodes) { const p = [xs[Math.floor(n / ny)], ys[n % ny]]; if (!pts.length || pts[pts.length - 1][0] !== p[0] || pts[pts.length - 1][1] !== p[1]) pts.push(p) }
      return { pts, goal: done.g }
    }
    const simplify = (pts) => {
      const out = []
      for (const p of pts) {
        if (out.length && out[out.length - 1][0] === p[0] && out[out.length - 1][1] === p[1]) continue
        if (out.length >= 2) { const a = out[out.length - 2]; const b = out[out.length - 1]; if ((a[0] === b[0] && b[0] === p[0]) || (a[1] === b[1] && b[1] === p[1])) { out[out.length - 1] = p; continue } }
        out.push(p)
      }
      return out
    }
    // VPC 위 변으로 나가는 자리 — 포트(±12) · VPC 이름 자리가 아닌 x · 위 변 바로 아래 줄(VPC 위 + 18)
    const topExits = (G2, bias) => {
      const L = G2.L; const pxs = L.ports.map((p) => L.ax + p.x)
      return G2.xs.filter((x) => !pxs.some((px) => Math.abs(px - x) < 12) && !(L.hdr && x >= L.hdr.x - 2 && x <= L.hdr.x + L.hdr.w + 2))
        .map((x) => ({ x, y: Math.round(L.ay + 18), dir: null, bias: bias(x) }))
    }
    // 리전 줄 밑 차선(Lambda 등 리전 자원 줄 아이콘으로) · VPC 줄 바로 위 틈(다른 VPC 로) — 차선 번호는 리전마다 센다
    const hubN = new Map(); const crossN = new Map(); const nxt = (m, k) => { const i = m.get(k) || 0; m.set(k, i + 1); return i }
    const inHub = new Map()   // 허브 아이콘 → 들어오는 선 수(밑 변에서 6px 씩 벌린다)
    for (const p of plan) if (p.mode === 'hub') inHub.set(p.g.b, (inHub.get(p.g.b) || 0) + 1)
    const seenHub = new Map()
    const chipTodo = []
    for (const p of plan) {
      const g = p.g; const G2 = gridOf(p.L)
      // 출발 변 값에 작은 차이(위 변 < 왼쪽 < 오른쪽) — 같은 값이면 결정론이 힙 순서에만 기댄다
      const st = p.st.map((s, i) => ({ ...s, cost: i }))
      let pts = null
      if (p.mode === 'in') {
        const goals = p.border ? G2.ys.map((y) => ({ x: p.border, y, dir: 'R' })) : p.goals
        const r = route(G2, st, goals); if (!r) continue
        // 서브넷에서 나가는 라우트(P2-2)는 테두리부터 — 출발점이 그 서브넷 테두리 바깥 3px 의 틈이다
        const s0 = p.g.kind === 'reni' ? st.find((x) => x.x === r.pts[0][0] && x.y === r.pts[0][1]) : null
        pts = simplify([...(s0 && s0.head ? [s0.head] : []), ...r.pts, ...(p.tail || []), ...(p.border ? [[Math.round(p.L.ax + p.L.w), r.goal.y]] : [])])
      } else if (p.mode === 'hub') {
        const PH = p.PH; const R = p.R; const n = inHub.get(g.b) || 1; const j = nxt(seenHub, g.b)
        const tx = Math.round(PH.cx + 6 * (j - (n - 1) / 2)); const hb = Math.round(PH.cy + IC / 2 + 2)
        const lane = Math.round(OY + R.Y + R.hubTop + IC + 6 + 3 * nxt(hubN, R.region))
        const r = route(G2, st, topExits(G2, (x) => Math.abs(x - tx))); if (!r) continue
        pts = simplify([...r.pts, [r.goal.x, lane], [tx, lane], [tx, hb]])
      } else if (p.mode === 'cross') {
        const T2 = gridOf(p.T); const lane = Math.round(Math.min(p.L.ay, p.T.ay) - 8 - 3 * nxt(crossN, p.L.v.id < p.T.v.id ? p.L.v.id : p.T.v.id))
        const ra = route(G2, st, topExits(G2, (x) => Math.abs(x - (p.T.ax + p.T.w / 2)))); if (!ra) continue
        const rb = route(T2, topExits(T2, (x) => Math.abs(x - ra.goal.x)).map((e) => ({ ...e, dir: 'D', cost: e.bias })), p.goals); if (!rb) continue
        pts = simplify([...ra.pts, [ra.goal.x, lane], [rb.pts[0][0], lane], ...rb.pts])
      }
      if (!pts || pts.length < 2) continue
      for (let i = 1; i < pts.length; i++) addSeg(pts[i - 1], pts[i])
      if (g.kind === 'reni') {
        // 서브넷 → ENI 주인(§17.29) = **라우트 선**이다(같은 회색 · 같은 화살촉 · 같은 정보창 「라우트로 나가는 길」 · 회계·관측도 라우트 선으로)
        const ids = [...g.ids].sort()
        pushLine(g.a, g.b, 'route', pts, rdet(g.a, g.b), true, { sub: g.a, tid: ids[0], ...(ids.length > 1 ? { extra: ids.slice(1) } : {}), reni: true })
        continue
      }
      if (g.kind === 'vpclink') {
        // API Gateway → LB(§17.35) = API 색 1.5 · 같은 색 화살촉(LB 쪽) — 길은 LB → 리전 줄로 찾았으니 거꾸로 긋는다
        const ids = [...g.vids].sort(); const names = [...g.vnames].sort(); const api = M.hubs.get(g.b)
        pushLine(g.b, g.a, 'vpclink', pts.slice().reverse(), () => ({ title: 'API Gateway → 로드밸런서', sub: '이 API 로 들어온 요청이 VPC 링크를 거쳐 이 로드밸런서로 전달됩니다',
          rows: [['API', `${api.name} · ${typeKo(api)}`], ['VPC 링크', [...names, ...(names.length ? [] : ids)].join('\n') || '?'], ['VPC 링크 ID', ids.join(', ') || '?'], ['로드밸런서', g.lb.name]], secs: [] }), true,
        { lbe: g.lbe.slice(), vlk: { ids, names, lbName: g.lb.name } })
        continue
      }
      if (g.kind === 'lsvctgt') {
        // VPC Lattice 서비스 → 대상(§17.40) = Lattice 색 1.5 · 같은 색 화살촉(대상 쪽) — 길은 대상 → 리전 줄로 찾았으니 거꾸로 긋는다
        const sv = M.hubs.get(g.b); const tNm = g.ids.size === 1 ? nodeName([...g.ids][0]) : B.cellL.get(g.a) || g.a
        const ports = [...g.ports].sort((x, y) => x - y).join(', '); const tw = g.lb.kind === 'lb' ? '로드 밸런서' : 'EC2 인스턴스'
        pushLine(g.b, g.a, 'lsvctgt', pts.slice().reverse(), () => ({ title: `VPC Lattice 서비스 → ${tw}`, sub: '이 서비스로 들어온 요청이 대상 그룹을 거쳐 이 대상으로 전달됩니다',
          rows: [['서비스', sv.name], [tw, tNm], ['대상 그룹', [...g.tgs].sort().join(', ') || '?'], ['포트', ports || '?']], secs: [] }), true,
        { lbe: g.lbe.slice(), ltg: { tgs: [...g.tgs].sort(), name: tNm, type: g.lb.kind === 'lb' ? 'alb' : 'instance', ports } })
        continue
      }
      if (g.kind === 'wafp') {
        // WAF 웹 ACL → ALB(§17.41) = WAF 색 1.5 · 같은 색 화살촉(LB 쪽) — 길은 LB → 리전 줄로 찾았으니 거꾸로 긋는다
        const w = M.hubs.get(g.b)
        pushLine(g.b, g.a, 'wafp', pts.slice().reverse(), () => wafLineDet(w, g.lb.name, '로드밸런서', null), true, { lbe: g.lbe.slice() })
        continue
      }
      if (g.kind === 'dbproxy') {
        // RDS 프록시 → DB(§17.36) = RDS 색 1.5 · 같은 색 화살촉(DB 쪽) · 칩 없음
        // 대상 이름 = 그 DB 의 이름(묶음 칸이면 칸 이름) — 칸 이름표 첫 줄(「RDS」)만 적으면 어느 DB 인지 모른다(188 실화면)
        const tNm = g.ids.size === 1 ? nodeName([...g.ids][0]) : B.cellL.get(g.b) || g.b; const ports = [...g.ports].sort((x, y) => x - y).join(', '); const kind = g.types.has('cluster') ? 'cluster' : 'instance'
        pushLine(g.a, g.b, 'dbproxy', pts, () => ({ title: `RDS 프록시 → ${kind === 'cluster' ? 'DB 클러스터' : 'DB 인스턴스'}`, sub: '앱이 프록시로 연결하면 프록시가 연결을 모아 이 DB 로 전달합니다',
          rows: [['프록시', g.lb.name], [kind === 'cluster' ? 'DB 클러스터' : 'DB 인스턴스', tNm], ['포트', ports || '?']], secs: [] }), true,
        { lbe: g.lbe.slice(), prx: { name: tNm, kind, ports } })
        continue
      }
      if (g.kind === 'epprov') {
        // 엔드포인트 → 제공자(§17.28) — 칩 없음(한 엔드포인트 = 서비스 하나) · 정보창 = 엔드포인트 · 제공자 · 서비스 · 연결 근거
        const tNm = B.cellL.get(g.b) || g.b; const fromNm = B.cellL.get(g.a) || g.a
        const svcs = [...g.svcs].sort(); const eps = [...g.eps].sort()
        pushLine(g.a, g.b, 'epprov', pts, () => ({ title: 'VPC 엔드포인트 → 제공자', sub: g.how.has('firewall_endpoint') ? '이 엔드포인트로 보낸 트래픽을 방화벽이 검사합니다' : '이 엔드포인트로 보낸 트래픽을 서비스의 로드밸런서가 받습니다',
          rows: [['엔드포인트', eps.map((id) => `${nodeName(id)} · ${natId(X.mById.get(id) || { id })}`).join('\n') || fromNm], ['제공자', tNm], ['서비스', svcs.map(shortSvc).join('\n') || '?'],
            ['연결 근거', [...g.how].sort().map((h) => EPP_HOW[h] || h).join('\n')]], secs: [] }), true,
        { lbe: g.lbe.slice(), epp: { name: tNm, from: fromNm, svcs, eps: new Set(eps) } })
        continue
      }
      const lbName = g.lb.name
      const tName = g.ext ? `외부 IP ${[...g.ips].length}개` : B.cellL.get(g.b) || (M.hubs.get(g.b) ? `${svcOf(M.hubs.get(g.b))} (${M.hubs.get(g.b).name})` : g.b)
      const ports = [...g.ports].sort((x, y) => x - y).join(', ')
      const types = [...g.types].sort().map((t) => LBT_KIND[t] || t).join(' · ')
      const ips = [...g.ips].sort()
      const label = g.ext ? `외부 IP ${ips.length}` : M.hubs.get(g.b) ? `${svcOf(M.hubs.get(g.b))} (${M.hubs.get(g.b).name})` : B.cellL.get(g.b) || g.b
      const exitName = g.ext ? (g.exit ? `${whyOf(g.exit.key)} · ${M.hubs.has(g.exit.hub) ? M.hubs.get(g.exit.hub).name : g.exit.nat}` : g.raw.size ? `${[...g.raw].sort().join(', ')} — 이 VPC 에 해당 연결 지점이 없습니다(선은 VPC 오른쪽 변에서 끝납니다)` : 'VPC 밖으로 나가는 경로를 알 수 없습니다 — 라우팅 테이블에 이 IP 로 가는 라우트가 없거나 읽지 못했습니다') : null
      const lk = pushLine(g.a, g.b, 'lbtarget', pts, () => ({ title: '로드밸런서 → 대상', sub: g.ext ? '이 로드밸런서가 VPC 밖 IP 로 요청을 전달합니다(화살표는 그 IP 가 나가는 연결 지점입니다)' : '대상 그룹이 이 대상으로 요청을 전달합니다',
        rows: [['로드밸런서', lbName], ['대상', tName], ['대상 그룹', [...g.tgs].sort().join(', ') || '?'], ['포트', ports || '없음(Lambda)'], ['대상 유형', types || '?'], ['등록된 대상', `${g.cnt}개`],
          ...(g.fold ? [['포함된 인스턴스', [...g.ids].sort().map((id) => nodeName(id)).join('\n')]] : []),
          ...(g.ext ? [['외부 IP', ips.join('\n')], ['나가는 연결 지점', exitName]] : [])], secs: [] }), true,
      { lbe: g.lbe.slice(), lbt: { name: label, lbName, tgs: new Set(g.tgs), ports, cnt: g.cnt, ext: g.ext, ips, ...(g.fold ? { fold: new Set(g.ids) } : {}) } })
      const l = B.lines[B.lines.length - 1]
      // 칩 — 등록 2 이상 = `×N` · 등록 0(그룹을 든 ECS 서비스 · 2.8.0) = `×0` · 외부 IP = `외부 IP N`(1 이어도). 자리는 **선을 다 그은 뒤** 정한다(뒤에 그은 선이 칩을 지나지 않게)
      //   🔴 0 에 칩을 안 달면 「등록 0 인 서비스로 가는 선」과 「등록 1」이 같은 모양이다 — 요청을 받을 곳이 없는데 받는 것처럼 읽힌다
      const text = g.ext ? `외부 IP ${ips.length}` : g.cnt >= 2 || g.cnt === 0 ? `×${g.cnt}` : null
      if (text) chipTodo.push({ l, text })
      void lk
    }
    // 칩 자리 — 끝에서 6px 씩 거슬러 오르며 선 옆(3 · 8 · 14px) 자리 중 아이콘·이름표·틀 머리 줄·다른 칩·**모든 선**과 겹치지 않는 첫 자리(결정론)
    for (const { l, text } of chipTodo) { const c = lbtChip(l.pts, text); if (c) { l.chip = { ...c, text }; B.chips.push(l.chip) } }
  }
  function lbtChip (pts, text) {
    const w = Math.ceil(tw(text, 9.5)) + 8; const h = 13
    const segs = []
    for (const l of B.lines) if (l.pts) for (let i = 1; i < l.pts.length; i++) { const [x1, y1] = l.pts[i - 1]; const [x2, y2] = l.pts[i]; segs.push({ x: Math.min(x1, x2) - 1, y: Math.min(y1, y2) - 1, w: Math.abs(x2 - x1) + 2, h: Math.abs(y2 - y1) + 2 }) }
    // 피할 것 = 아이콘 그림·이름표·포트 · 틀 **이름 글자**(VPC·서브넷·AZ — 머리 줄 전체가 아니다: 출구 포트 선은 VPC 머리 줄 안에서 끝난다) ·
    //   계정·리전·전역 머리 줄 · 다른 칩 · 모든 선(칩은 글자다 — 선이 지나가면 「선이 이름표를 지난다」와 같다 · §17.14)
    const busy = [...badgeBusy().filter((r) => !r.hdr), ...B.frames.filter((f) => f.kind === 'acct' || f.kind === 'region' || f.kind === 'glob').map((f) => ({ x: f.x, y: f.y, w: f.w, h: FR_HDR[f.kind] || 24 })),
      ...B.vnl, ...B.snl.values(), ...[...B.azl.values()].flat(), ...B.chips, ...segs]
    // 끝(화살촉)에서 시작점 쪽으로 6px 씩 · 그 점에서 선 옆 3 → 8 → 14px · 첫 빈 자리(결정론). 화살촉 바로 옆 10px 은 비운다(머리를 가린다)
    const cands = []
    let walked = 0
    for (let i = pts.length - 1; i >= 1; i--) {
      const [x2, y2] = pts[i]; const [x1, y1] = pts[i - 1]; const len = Math.abs(x2 - x1) + Math.abs(y2 - y1); const hz = Math.abs(y1 - y2) < 0.01
      for (let d = 0; d <= len; d += 6) {
        if (walked + d < 10) continue
        const t = len ? d / len : 0; const mx = Math.round(x2 + (x1 - x2) * t); const my = Math.round(y2 + (y1 - y2) * t)
        for (const gap of [3, 8, 14, 20]) {
          if (hz) cands.push({ x: mx - Math.round(w / 2), y: my + gap, w, h }, { x: mx - Math.round(w / 2), y: my - gap - h, w, h })
          else cands.push({ x: mx + gap, y: my - Math.round(h / 2), w, h }, { x: mx - gap - w, y: my - Math.round(h / 2), w, h })
        }
      }
      walked += len
    }
    const free = cands.find((r) => !hitIn(busy, r))
    if (free) return free
    // 선 옆이 다 막혔다(포트 가까이 선이 촘촘한 곳) — 화살촉 둘레 80px 안의 6px 격자에서 가장 가까운 빈 자리(결정론 · 거리 → y → x)
    const [ex, ey] = pts[pts.length - 1]; const ring = []
    for (let dy = -84; dy <= 84; dy += 6) for (let dx = -84; dx <= 84; dx += 6) ring.push({ x: Math.round(ex + dx - w / 2), y: Math.round(ey + dy - h / 2), w, h, d: Math.abs(dx) + Math.abs(dy) })
    ring.sort((a, b) => a.d - b.d || a.y - b.y || a.x - b.x)
    const r = ring.find((q) => q.d >= 12 && !hitIn(busy, q))
    return r ? { x: r.x, y: r.y, w, h } : cands[0] || null
  }

  // ======================================================================== 엣지 회계
  // 엣지 하나 = 그린 선 · 배치로 말함(자원이 그 서브넷 안) · 대표(한 상자로 합쳐짐) · 정보창(선 없이 이어진 것) · 못 그림 중 하나
  function account () {
    const cnt = { drawn: 0, placement: 0, represented: 0, card: 0, unwired: 0, hand: 0 }
    const unwired = []; M.rel = new Map()
    const keyOf = (id) => {
      let cur = id; let g = 0
      while (cur && g++ < 6) {
        if (B.cellOf.has(cur)) return B.cellOf.get(cur)
        if (B.boxes.has(cur) && cur !== 'aws:rep') return cur
        if (B.icon.has(cur)) return cur
        if (M.subnets.has(cur)) return M.subnets.get(cur).vpc   // 서브넷을 끈 화면
        const h = M.hidden.get(cur); if (!h) return null
        if (h === 'aws:rep') return 'aws:rep'
        cur = h
      }
      return null
    }
    // TGW Connect 오버레이(§17.38 · `cnx`)는 짝 색인에 넣지 않는다 — Connect 어태치먼트 자신의 관계는 그 점선에만(전송 선에 섞지 않는다) ·
    //   VPC 포트 위 점선은 두 끝이 전송 선과 같으니(포트 · TGW) 그 전송 관계의 끈도 같이 싣는다(`onBase` · DX 위 점선은 두 끝이 TGW · DX 게이트웨이라 싣지 않는다)
    const LS = B.lines.filter((l) => l.kind !== 'cnx')
    const cnxLine = new Map(); for (const l of B.lines) if (l.kind === 'cnx') cnxLine.set(l.att, l.k)
    const onBase = new Map(); for (const l of B.lines) if (l.kind === 'cnx' && l.base && l.cnx && !l.cnx.dx) push(onBase, l.base, l)
    const pair = new Set(); for (const l of LS) { pair.add(l.a + '\u0000' + l.b); pair.add(l.b + '\u0000' + l.a) }
    const route = new Set(); for (const l of B.lines) if (l.sub) { for (const t of [l.tid, ...(l.extra || [])]) if (t) route.add(l.sub + '\u0000' + t) }
    const pcxDrawn = new Set(B.lines.filter((l) => l.pcx).map((l) => l.pcx))
    const lineOf = new Map(); for (const l of LS) { lineOf.set(l.a + '\u0000' + l.b, l.k); lineOf.set(l.b + '\u0000' + l.a, l.k) }
    const routeLine = new Map(); for (const l of B.lines) if (l.sub) { for (const t of [l.tid, ...(l.extra || [])]) if (t && !routeLine.has(l.sub + '\u0000' + t)) routeLine.set(l.sub + '\u0000' + t, l.k) }
    const pcxLine = new Map(); for (const l of B.lines) if (l.pcx && !pcxLine.has(l.pcx)) pcxLine.set(l.pcx, l.k)
    const byLk = new Map(B.lines.map((l) => [l.k, l]))
    // 선 자체가 어태치먼트인 선(TGW 피어링 · TGW ↔ DX 게이트웨이) — 그 어태치먼트에 닿는 관계는 두 끝이 같은 TGW 로 접혀도 이 선의 끈이다(§17.20)
    const attLine = new Map(); for (const l of B.lines) if (l.att && !attLine.has(l.att)) attLine.set(l.att, l.k)
    // LB → 대상 선(§17.27) — 그 선이 합친 캔버스 엣지 id 들(묶음 칸·외부 IP 출구로 접혀 두 끝이 엔진 노드가 아니다)
    const lbtLine = new Map(); for (const l of B.lines) for (const id of l.lbe || []) lbtLine.set(id, l.k)
    // 모니터링 끈(§17.20) — 이 선이 그린 관계의 엔진 엣지 id(`attrs.obs_edges`)와 **그 관계를 실어 온 출발 자원**(라우트 = 서브넷).
    //   판정은 flow.js 의 `wireObs` 한 곳이 한다(§6 규칙 10 포함) — 여기는 그 재료(`obsIds`·`obsFrom`)만 싣는다
    const tie = (lk, e) => {
      const l0 = lk && byLk.get(lk); const oe = e.attrs && e.attrs.obs_edges; if (!l0 || !oe) return
      for (const l of [l0, ...(onBase.get(lk) || [])]) {
        if (!l.obsIds) { l.obsIds = []; l.obsFrom = new Map() }
        for (const id of Array.isArray(oe) ? oe : [oe]) {
          if (!l.obsIds.includes(id)) l.obsIds.push(id)
          const f = l.obsFrom.get(id) || []; if (!f.includes(e.source)) f.push(e.source); l.obsFrom.set(id, f)
        }
      }
    }
    const rel = (k1, k2, kind, name, ek, dir) => { push(M.rel, k1, { key: k2, kind, name, ek, dir }) }
    const label = (k) => B.cellL.get(k) || (M.subnets.get(k) && subnetName(k)) || (M.vpcs.get(k) && M.vpcs.get(k).name) || (M.hubs.get(k) && M.hubs.get(k).name) || (M.globs.get(k) && M.globs.get(k).name) || k
    B.edgeOf = new Map()
    const gh = (id) => X.goneByHand(id) && !M.xg.has(id)
    const ro = (id) => !!X.outScope && X.outScope(id)   // 실제로 보기 범위 밖(흐린 TGW 도 여기) — 양 끝이 다 밖이면 이 화면의 관계가 아니다
    for (const e of X.T.edges) {
      const ga = gh(e.source); const gb = gh(e.target)
      if (ga || gb) {
        // 범위 안 ↔ 범위 밖인데 흐린 TGW 로 못 이은 관계 — 조용히 지우지 않는다(§17.22 · 「못 그린 관계 N」에 센다)
        if (ro(e.source) !== ro(e.target) && !gh(ro(e.source) ? e.target : e.source)) { cnt.unwired++; unwired.push({ e, dead: '범위 밖', why: '보기 범위 밖과 잇는 관계 — 새 화면은 아직 이 관계를 범위 밖 표시에 잇지 못한다(§17.22)' }); continue }
        cnt.hand++; continue
      }
      const ka = keyOf(e.source); const kb = keyOf(e.target)
      const bad = (w) => { cnt.unwired++; unwired.push({ e, dead: w.dead || null, why: w.why }) }
      if (e.fallback) { bad({ why: `${WHY_NEW} — 엣지 종류 «${e.kind}»` }); continue }
      // TGW Connect 어태치먼트의 관계(Connect → TGW) = 그 오버레이 점선(§17.38) — 전송 선(포트 → TGW)에 앉히면 전송의 판정이 Connect 의 것과 섞인다
      const cxk = cnxLine.get(e.source) || cnxLine.get(e.target)
      if (cxk) { cnt.drawn++; B.edgeOf.set(e.id, cxk); tie(cxk, e); continue }
      if ((e.kind === 'lbtarget' || e.kind === 'epprov') && lbtLine.has(e.id)) { cnt.drawn++; const lk = lbtLine.get(e.id); B.edgeOf.set(e.id, lk); tie(lk, e); continue }
      // 포트 선(§17.39 Cloud WAN · §17.40 Lattice 연결) — 엣지 두 끝(코어 네트워크·서비스 네트워크 ↔ VPC 상자)과 선 두 끝(그 칸 ↔ VPC 위 변 포트)이 다르다 →
      //   그 VPC 의 포트로 선을 찾는다. 못 찾으면(선을 못 그은 화면) 아래 갈래 그대로 · 🔴 2026-10-23 실계정: 이 갈래가 없어 「그림에 없는 연결」로 새고 관측 끈이 0 이었다
      const pk = (e.kind === 'cwanatt' || e.kind === 'lsnvpc') && M.portOf.get(e.target + '|' + e.source)
      const plk = pk && (lineOf.get(pk + '\u0000' + e.source) || (ka && lineOf.get(pk + '\u0000' + ka)))
      if (plk) { cnt.drawn++; B.edgeOf.set(e.id, plk); tie(plk, e); continue }
      // 서브넷 → 피어링 라우트는 그 서브넷의 라우트 선이 그렸다(§17.18-11) — 관측도 그 선에 싣는다(§17.20). 나머지 피어링 관계 = 점선
      const rk = (pcxDrawn.has(e.source) || pcxDrawn.has(e.target)) && (routeLine.get(e.source + '\u0000' + e.target) || routeLine.get(e.target + '\u0000' + e.source))
      if (rk) { cnt.drawn++; B.edgeOf.set(e.id, rk); tie(rk, e); continue }
      if (pcxDrawn.has(e.source) || pcxDrawn.has(e.target)) { cnt.drawn++; tie(pcxLine.get(e.source) || pcxLine.get(e.target), e); continue }
      if (!ka || !kb) {
        // 끝점이 모델에 아예 없는 것과 모델에는 있지만 그리지 않는 종류는 다른 말이다(대장 98 · 검사 89 e4)
        const nw = (id) => (X.mById.has(id) ? '그리지 않은 종류' : '모델에 없다')
        const dead = !ka && !kb ? (nw(e.source) === nw(e.target) ? `양쪽 다 ${nw(e.source)}` : `출발이 ${nw(e.source)} · 도착이 ${nw(e.target)}`) : `${!ka ? '출발' : '도착'}이 ${nw(!ka ? e.source : e.target)}`
        bad({ dead, why: `${WHY_NEW} — 끝점이 잔차다(${dead})` }); continue
      }
      if (ka === kb || ka === 'aws:rep' || kb === 'aws:rep') { cnt.represented++; tie(attLine.get(e.source) || attLine.get(e.target), e); continue }
      // 픽스처의 인터넷 노드 ↔ 칸이 있는 IGW = 그 IGW 의 인터넷 칸 선이 그렸다(§17.24 · 노드는 첫째 칸 하나로 숨는다)
      const inetK = (id, o) => ((X.mById.get(id) || {}).type === 'internet' && B.inet.has('aws:inet:' + o) ? 'aws:inet:' + o : null)
      const ik = inetK(e.source, kb) || inetK(e.target, ka)
      if (ik) { cnt.drawn++; B.edgeOf.set(e.id, lineOf.get(B.inet.get(ik).igw + '\u0000' + ik) || null); continue }   // 모니터링 끈은 잇지 않는다 — 그린 칸의 선이라 잴 구간이 없다
      if (pair.has(ka + '\u0000' + kb) || route.has(e.source + '\u0000' + e.target) || route.has(e.target + '\u0000' + e.source)) { cnt.drawn++; const lk = lineOf.get(ka + '\u0000' + kb) || routeLine.get(e.source + '\u0000' + e.target) || routeLine.get(e.target + '\u0000' + e.source) || null; B.edgeOf.set(e.id, lk); tie(lk, e); continue }
      const place = (sid, oid) => { const s = M.subnets.get(sid); if (!s) return false; const it = M.items.get(oid); return !!(it && it.subnets.includes(sid)) }
      const inVpc = (vid, oid) => { const it = M.items.get(oid); return !!(M.vpcs.has(vid) && it && it.vpc === vid) }
      if (place(e.source, e.target) || place(e.target, e.source) || inVpc(e.source, e.target) || inVpc(e.target, e.source)) { cnt.placement++; continue }
      cnt.card++
      rel(ka, kb, `«${e.kind}» ${e.source === e.target ? '' : ka === keyOf(e.source) ? '→' : '←'}`, label(kb), e.kind, e.source === e.target ? '' : ka === keyOf(e.source) ? '→' : '←'); rel(kb, ka, `«${e.kind}» ←`, label(ka), e.kind, '←')
    }
    return { cnt, unwired }
  }

  // ======================================================================== flow.js 가 부르는 것
  function layout (ctx) {
    const t0 = performance.now()
    X = ctx; wire()
    O = { az: ctx.S.awsAz !== false, sn: ctx.S.awsSn !== false }
    M = model()
    const { out, W, H } = build()
    const { cnt, unwired } = account()
    SC = { out, W, H, cnt, opt: { ...O } }
    const nXwire = B.lines.filter((l) => M.xg.has(l.a) || M.xg.has(l.b)).length   // 배너 「외부 연결 N」(§17.22)
    const nItems = [...B.boxes.values()].filter((b) => b.kind === 'aws-item' || b.kind === 'aws-hub' || b.kind === 'aws-port').length
    return { nXwire, boxes: B.boxes, unwired, awsWhy: M.why, awsNotes: gaNotes(), canvasW: W, canvasH: H,
      nEdges: B.lines.length, nRawEdges: ctx.T.edges.length, ms: performance.now() - t0, nItems, scope: ctx.SCOPE || null }
  }
  function defs () {
    return '<defs>' + Object.entries(ICON).map(([k, o]) => `<path id="aw-o-${k}" d="${o.d}" fill-rule="evenodd"/>`).join('') +
      '<marker id="aw-ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,1L10,5L0,9z" fill="#7d8590"/></marker>' +
      // LB → 대상 선의 화살촉 = LB 색(§17.27)
      `<marker id="aw-arlb" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,1L10,5L0,9z" fill="${COL.lb}"/></marker>` +
      // 엔드포인트 → 제공자 선의 화살촉 = 엔드포인트 색(§17.28)
      `<marker id="aw-arep" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,1L10,5L0,9z" fill="${COL.vpce}"/></marker>` +
      // Route 53 별칭 선의 화살촉 = 영역 색(§17.30)
      `<marker id="aw-ardns" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,1L10,5L0,9z" fill="${COL.r53}"/></marker>` +
      // 배치 D(§17.35~37) — VPC 링크 · RDS 프록시 · 가속기 선의 화살촉 = 출발 서비스 색
      ['apigw', 'rdsp', 'ga', 'lsvc', 'waf'].map((k) => `<marker id="aw-ar${k}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,1L10,5L0,9z" fill="${COL[k]}"/></marker>`).join('') + '</defs>'
  }
  function render (L, ctx) {
    const t0 = performance.now()
    X = ctx
    const S = ctx.S
    ctx.svgEl.classList.add('aws'); ctx.svgEl.classList.remove('aw-sel'); ctx.svgEl.classList.toggle('anim', !!S.anim)
    ctx.svgEl.innerHTML = defs() + `<g id="vp" transform="translate(${S.tx},${S.ty}) scale(${S.zoom})" font-family="-apple-system,BlinkMacSystemFont,'Apple SD Gothic Neo','Segoe UI',sans-serif">` +
      `<rect x="0" y="0" width="${SC.W}" height="${SC.H}" fill="${BG}"/>` +
      `<g id="aw-box">${SC.out.box.join('')}</g><g id="aw-ln">${SC.out.ln.join('')}</g><g id="aw-ic">${SC.out.ic.join('')}</g>${obsLayer()}</g>`
    //   층 맨 앞 투명 판 = 그림 크기 고정 — 세모가 움직여도 층의 그릴 범위가 그대로라 브라우저가 층 나누기를 다시 하지 않는다(§17.25 「성능」)
    { const ov = utlEl(); if (ov) ov.innerHTML = UTL ? `<g id="aw-utl-vp" transform="translate(${S.tx},${S.ty}) scale(${S.zoom})"><rect x="0" y="0" width="${SC.W}" height="${SC.H}" fill-opacity="0"/>${UTL}</g>` : '' }
    // VPC 이름 옆 ▸ 는 실제 글자 폭 뒤에(추정 폭은 한글·영문 섞이면 어긋난다)
    //   🔴 폭은 올림한 정수로 쓰고 **이름마다 처음 잰 값을 다시 쓴다** — 측정값이 다시 그릴 때마다 흔들려 같은 그림이
    //      바이트로 달라진다(모니터링을 껐다 켠 그림 = 켜기 전 그림 · 검사 92·94·96). 올림만으로는 모자랐다: 리눅스
    //      크로미움(CI)은 첫 그림과 다시 그린 그림의 폭이 1px 넘게 달랐다(실측 2026-10-03 · 155 ↔ 154). 맥에서는 안 난다
    for (const g of ctx.svgEl.querySelectorAll('.aw-vt')) {
      const t = g.querySelector('.aw-vn'); const tr = g.querySelector('.aw-tri'); const h = g.querySelector('.aw-hit')
      try {
        const nm = t.textContent; let w = VT_W.get(nm)
        if (w === undefined) { w = Math.ceil(t.getComputedTextLength()); if (w > 0) VT_W.set(nm, w) }
        if (w > 0) { tr.setAttribute('x', +t.getAttribute('x') + w + 7); h.setAttribute('width', w + 52) }
      } catch (e) { /* 측정 불가(비표시) */ }
    }
    const az = document.getElementById('aws-az'); const sn = document.getElementById('aws-sn')
    if (az) az.checked = O.az; if (sn) sn.checked = O.sn
    legend()
    HL = null; anchorPt = null; openV = null; cardKey = null
    const card = document.getElementById('awscard'); if (card) card.style.display = 'none'
    ctx.applyDim(L)   // 검색 흐림은 다시 그린 뒤에도 남는다(그린 다음에 건다)
    utAfterRender()   // 사용률 막대 · 고르기 다시 걸기 · 세모 움직임(§17.25)
    const renderMs = performance.now() - t0
    ctx.syncChrome(L)
    return { renderMs, nMatch: NMATCH }
  }
  // ======================================================================== 실시간 모니터링(§17.20)
  // 상태는 **따로 선 한 층**에만 그린다 — 틀·선·아이콘 세 층은 켜기 전과 바이트가 같고, 끄면 이 층이 통째로 없다.
  // 판정은 flow.js 가 한다(`obsOf`·`wireObs`). 여기는 자리와 모양만 정한다.
  const OBS_CARD = new Set(['aws-item', 'aws-hub', 'aws-port', 'aws-subnet'])
  const OBS_GLYPH = { down: '!', none: '?' }
  // 멈춘 선 · 이상 테의 점선(사용자 결정 2026-10-14 · QA B P8) — 흐름 테(`4 3`)·유령(`14 6`)·피어링(`7 5`)과 다른 무늬
  const STOP_DASH = '6 4'; const DOWN_DASH = '5 3'
  function obsLayer () {
    const o = X && X.obs; if (!o) return ''
    // 🔴 모니터링(선·테·배지)과 사용률(세모·칩)은 **따로 선 두 스위치**다(사용자 결정 2026-10-06 · 대장 127·140).
    //   한쪽만 켜면 다른 쪽은 한 줄도 그리지 않는다 — 사용률만 켠 화면에 초록 선·테가 나오면 고객은 모니터링이 켜진 줄 안다
    const mon = !!o.on(); const C = o.C; const anim = !!X.S.anim; const out = []
    // 사용률(§17.25)을 **먼저** 앉힌다 — 등급 배지가 그 자리를 피해 간다. 사용률 꺼짐 · 행 없음 = 장면 없음(§17.20 그대로)
    const ur = X.util ? X.util.rows() : null
    if (!mon && !(ur && ur.length)) { UTS = null; UTL = ''; return '' }
    const busy = badgeBusy(); const U = utScene(busy)
    // 선 — 그 구간 자신을 잰 `live` 만 초록(검사 95·111 과 같은 뜻). 노랑·빨강 선은 없다 — 막힘은 아이콘이 말한다
    //   둘 다 = 세모가 있는 선은 점만 뺀다(세모와 점이 한 선 위에서 겹치지 않게)
    //   모니터링 꺼짐 = 선·등급 배지 0 — 그 문은 판정 쪽(`wireObs`·`wireBadge` 의 `S.monitor`) 하나다. 여기 또 두면 한쪽 브레이크가 죽는다
    //   🔁 예외(사용자 결정 2026-10-14 · QA B P8): 흐르다가 **멈춘** 선 = 빨간 점선(이력의 `live → up` 뒤 `→ live` 없음 · 판정은 flow.js `stopped`)
    for (const l of B.lines) {
      const f = l.obsIds ? o.line(l) : null
      if (!f) {
        if (l.obsIds && mon && o.stopped && o.stopped(l)) out.push(`<g class="aw-fs" data-k="${l.k}"><path class="aw-fsv" d="${orth(l.pts)}" fill="none" stroke="${C.err}" stroke-width="2" stroke-dasharray="${STOP_DASH}" stroke-linejoin="round"/></g>`)
        continue
      }
      const d = orth(l.pts); const dots = anim && !(U && U.tri.has(l.k))
      out.push(`<g class="aw-fl" data-k="${l.k}" data-flow="${f.via}"><path class="aw-flv" d="${d}" fill="none" stroke="${C.ok}" stroke-width="2.4" stroke-linejoin="round"/>` +
        (dots ? `<path class="obsflow" d="${d}" fill="none" stroke="${C.ok}" stroke-width="5.8" stroke-linecap="round" stroke-dasharray="0.1 19.9"/>` : '') + '</g>')
    }
    out.push(gradeBadges(o, C, busy))
    // 아이콘·포트 = 테(관측 있음) + 배지 · 서브넷 = 배지만(테두리는 서브넷 톤 채널이 쓴다)
    for (const [k, b] of mon ? B.boxes : []) {
      if (!OBS_CARD.has(b.kind)) continue
      const r = o.node(b); if (!r) continue   // null = 노드 관측이 없는 문서 · '' = 이 자리는 미측정(테 없음)
      const st = r.status
      const g = OBS_GLYPH[st] || (st === 'live' && !anim ? '≈' : r.partial ? '~' : '')
      const col = st === 'down' ? C.err : C.dim
      let s = `<g class="aw-ob" data-k="${esc(k)}" data-obs="${st}"${r.partial ? ' data-partial="1"' : ''}${g ? ` data-g="${g}"` : ''}>`
      let bx, by
      if (b.kind === 'aws-subnet') { bx = b.x + b.w - 18; by = b.y + 4 }
      else {
        const c = B.icon.get(k) || { cx: b.x + b.w / 2, cy: b.y + b.h / 2 }; const rr = b.kind === 'aws-port' ? 13 : IC / 2 + 5
        s += `<circle class="aw-ring${st === 'live' && anim ? ' aw-live' : ''}" cx="${P2(c.cx)}" cy="${P2(c.cy)}" r="${rr}" fill="none" stroke="${col}" stroke-width="${st === 'down' ? 1.8 : 1.2}" stroke-opacity="${st === 'down' ? 1 : 0.7}"${st === 'down' ? ` stroke-dasharray="${DOWN_DASH}"` : st === 'live' && anim ? ' stroke-dasharray="4 3"' : ''}/>`
        bx = c.cx + rr - 10; by = c.cy + rr - 10
      }
      if (g) s += `<rect x="${P2(bx)}" y="${P2(by)}" width="14" height="14" rx="3" fill="${C.chip}" stroke="${col}" stroke-opacity="0.9"/><text class="obsg" x="${P2(bx + 7)}" y="${P2(by + 10.5)}" fill="${col}" font-size="10" text-anchor="middle" font-weight="700">${g}</text>`
      out.push(s + '</g>')
    }
    // 🔴 얹을 것이 없으면 층도 만들지 않는다 — 관측 0행·짝 어긋남에서 그림은 켜기 전과 **바이트 동일**이어야 한다(검사 94·96)
    // 사용률 층은 따로 한 층(`aw-ut`) — 모니터링만 보기 · 행 없음이면 없다(검사 140)
    //   세모는 그림 밖 겹친 층(`#awsutl`)에 — 그림 안이면 움직일 때마다 그림 전체를 다시 그린다(§17.25 「성능」 · 검사 145). 층이 없는 문서면 예전처럼 여기에
    const ov = utlEl(); UTL = U && U.tsvg && ov ? U.tsvg : ''
    const us = U ? U.svg + (ov ? '' : U.tsvg) : ''
    return (out.some(Boolean) ? `<g id="aw-obs" pointer-events="none">${out.join('')}</g>` : '') + (us ? `<g id="aw-ut" pointer-events="none">${us}</g>` : '')
  }
  // 선 위 등급 배지(§17.20) — 글자는 flow.js `wireBadge`, 자리는 여기서 정한다.
  //   🔴 초록이 아닌 선에도 붙는다 — 경로 성립·차단·흐름 없음은 초록이 아닌 선이 하는 말이다.
  //   자리: 가장 긴 곧은 구간부터 가운데 → 1/4 → 3/4, 가로면 아래 → 위 · 세로면 오른쪽 → 왼쪽.
  //   아이콘 그림(테 포함) · 이름표 · 틀 머리 줄 · 먼저 놓은 배지와 겹치지 않는 첫 자리.
  const FR_HDR = { acct: 24, glob: 26, region: REG_HDR, vpc: VPC_HDR, az: AZ_HDR, subnet: SUB_HDR }
  // 배지가 피할 자리 — 아이콘 그림(테 포함) · 이름표 · 포트 · 틀 머리 줄. 사용률 배지와 등급 배지가 **같은 목록**을 나눠 쓴다
  function badgeBusy () {
    const busy = []
    for (const k of B.icon.keys()) {
      const v = B.vis.get(k); if (v) { busy.push(...v); continue }
      const c = B.icon.get(k); busy.push({ x: c.cx - 18, y: c.cy - 18, w: 36, h: 36 })   // 포트(18px) + 테(r 13) + 테 배지(cx+3 ~ cx+17)
    }
    for (const f of B.frames) busy.push({ x: f.x, y: f.y, w: f.w, h: FR_HDR[f.kind] || 24, hdr: 1 })
    return busy
  }
  const hitIn = (busy, r) => busy.some((q) => r.x < q.x + q.w && q.x < r.x + r.w && r.y < q.y + q.h && q.y < r.y + r.h)
  // 선 위 배지 자리 후보(폭 w) — 가장 긴 곧은 구간부터 가운데 → 바깥으로 8px 씩, 가로면 아래 → 위 · 세로면 오른쪽 → 왼쪽
  function lineCands (l, w) {
    const segs = []
    for (let i = 1; i < l.pts.length; i++) {
      const [x1, y1] = l.pts[i - 1]; const [x2, y2] = l.pts[i]
      segs.push({ x1, y1, x2, y2, len: Math.abs(x2 - x1) + Math.abs(y2 - y1), i })
    }
    segs.sort((a, b) => b.len - a.len || a.i - b.i)
    const cand = []
    for (const g of segs) {
      // 가운데에서 바깥으로 8px 씩 — 짧은 선은 몇 자리 안 나온다(그래서 긴 구간부터)
      const ts = [0.5]; const st = g.len > 0 ? 8 / g.len : 1
      for (let j = 1; 0.5 + j * st <= 0.95; j++) ts.push(0.5 - j * st, 0.5 + j * st)
      for (const t of ts) {
        const mx = g.x1 + (g.x2 - g.x1) * t; const my = g.y1 + (g.y2 - g.y1) * t
        if (g.y1 === g.y2) {
          cand.push({ x: mx - w / 2, y: my + 2, w, h: 11, tx: mx, ty: my + 11, a: 'middle' })
          cand.push({ x: mx - w / 2, y: my - 13, w, h: 11, tx: mx, ty: my - 4, a: 'middle' })
        } else {
          cand.push({ x: mx + 4, y: my - 6, w, h: 11, tx: mx + 4, ty: my + 3, a: 'start' })
          cand.push({ x: mx - 4 - w, y: my - 6, w, h: 11, tx: mx - 4, ty: my + 3, a: 'end' })
        }
      }
    }
    // 옆자리가 다 막힌 짧은 선 — 선 위에 바로 얹는다(글자 뒤 바탕색 테두리가 선을 가린다)
    for (const g of segs) cand.push({ x: (g.x1 + g.x2) / 2 - w / 2, y: (g.y1 + g.y2) / 2 - 6, w, h: 11, tx: (g.x1 + g.x2) / 2, ty: (g.y1 + g.y2) / 2 + 3, a: 'middle' })
    return cand
  }
  function gradeBadges (o, C, busy) {
    if (!o.badge) return ''
    const out = []
    for (const l of B.lines) {
      if (!l.obsIds || !l.obsIds.length || !l.pts || l.pts.length < 2) continue
      const bd = o.badge(obsLine(l)); if (!bd) continue
      // 그래도 자리가 없으면 등급 글자만(§17.20) — 온전한 말은 정보창 「판정 등급」 줄. 다 막히면 첫 후보(결정론)
      let text = bd.label; let short = false; const all = lineCands(l, tw(text, 9))
      let c = all.find((r) => !hitIn(busy, r))
      if (!c) { const t2 = bd.short || (bd.g === 'A' || bd.g === 'E' ? '드롭' : bd.g); const c2 = lineCands(l, tw(t2, 9)).find((r) => !hitIn(busy, r)); if (c2) { c = c2; text = t2; short = true } }
      c = c || all[0]; if (!c) continue
      busy.push(c)
      out.push(`<text class="aw-wg" data-k="${l.k}" data-g="${bd.g}"${short ? ' data-short="1"' : ''} x="${P2(c.tx)}" y="${P2(c.ty)}" fill="${C.dim}" font-size="9" text-anchor="${c.a}" paint-order="stroke" stroke="${BG}" stroke-width="3" stroke-linejoin="round" pointer-events="none">${esc(text)}</text>`)
    }
    return out.join('')
  }
  // 배너 회계(flow.js `obsStats`)가 읽는 것 — 선과 끈. 컨테이너·아이콘 구분은 상자 종류가 말한다
  //   `kind: 'flow'` = 라우트 선 — 「양 끝만 흐른다」(`endsOnly`)를 셀 수 있는 유일한 종류
  //   🔴 끈 없는 선도 준다 — 「양 끝만 흐르고 칠하지 않은 선」은 끈이 없는 선에서 나온다(선 전부를 센다)
  const obsLine = (l) => ({ k: l.k, a: l.a, b: l.b, kind: l.kind === 'route' ? 'flow' : l.kind, feed: l.kind === 'route' && !l.sub, obsIds: l.obsIds || [], obsFrom: l.obsFrom || new Map() })
  const obsLines = () => (B ? B.lines.map(obsLine) : [])

  // ======================================================================== 사용률(§17.25 · ADR 0011)
  // 숫자는 엔진이 낸 원래 단위(bit/s · pps · % · 개수)다 — 비율은 여기서 `limit` 으로 나눈다. 판정(초록 선)은 바꾸지 않는다(ADR 0008).
  // 🔴 없는 값을 0 으로 그리지 마라: 못 읽음 = `사용률 ?` · 한도 모름 = `한도 ?` · 그 기간이 없는 행 = `?`(앞 기간 값을 끌어 쓰지 않는다)
  const UT_WIN = [
    { m: 15, t: '15분', g: 0 }, { m: 60, t: '1시간', g: 0 }, { m: 1440, t: '24시간', g: 0 },
    { m: 10080, t: '1주', g: 1 }, { m: 43200, t: '1달', g: 1 }, { m: 129600, t: '3달', g: 1 }]
  const UT_UNIT = { 15: '1분', 60: '1분', 1440: '1분', 10080: '1분', 43200: '5분', 129600: '1시간' }   // CloudWatch 보관 해상도(ADR 0011 D2)
  const UT_SPD = [0, 4.4, 10, 25.7, 65]          // px/s · 단 = <1 Mbps · 1–100 Mbps · 100 Mbps–1 Gbps · ≥1 Gbps(2026-10-26 · 옛 = 비율 <10 · 10–50 · 50–80 · ≥80)
  const UT_GAP = 40
  const UT_TRI = [[4.4, 0], [-3.6, -3.7], [-3.6, 3.7]]
  const UT_COL = '#FF9F1C'
  // 🔁 2026-10-26 사용률 → 사용량(§17.25 개정 · 관측 1.18.0 · 사용자 결정 「사용률은 말 그대로 사용량」) — 통신량은 한도 대비가 아니다.
  //   배지·목록의 첫 숫자 = **기간 합**(`windows[].total`). 'peak' 로 바꾸면 가장 바빴던 단위의 속도가 된다 — 사용자가 「일반적인가」 확인 중이라
  //   이 한 줄에서만 정한다(하네스 `verify-aws.mjs` 의 `UT168_BADGE` 와 짝)
  const UT_BADGE = 'total'
  const UT_RATE = new Set(['bandwidth', 'packet_rate', 'capacity', 'invocations', 'requests', 'connections'])
  // 합의 단위 → 목록 정렬 묶음(바이트 → 횟수 → 패킷 → 그 밖) — 서로 다른 단위의 합을 한 줄로 견주지 않는다
  const UT_TCLS = { byte: 0, count: 1, packet: 2 }
  const UT_M = {
    bandwidth: '통신량', packet_rate: '패킷', capacity: '용량', rejected_connections: '연결 거부', port_allocation_errors: '포트 할당 오류',
    invocations: '호출', requests: '요청', connections: '연결',
    packets_dropped: '드롭 패킷', ip_addresses: 'IP 주소', security_group_rules: '규칙', network_acl_entries: '항목',
    routes_ipv4: 'IPv4 라우트', routes_ipv6: 'IPv6 라우트', routes_propagated: '전파 라우트', vpc_ipv4_cidrs: 'IPv4 CIDR', vpc_ipv6_cidrs: 'IPv6 CIDR',
    vpc_subnets: '서브넷', vpc_network_acls: '네트워크 ACL', vpc_route_tables: '라우팅 테이블', vpc_interface_endpoints: '인터페이스 엔드포인트',
    vpc_active_peerings: '활성 피어링', tgw_attachments: '어태치먼트', tgw_route_tables: '라우팅 테이블', tgw_peering_attachments: '피어링 어태치먼트',
    tgw_routes: '라우트(테이블 합)', dxgw_virtual_interfaces: '가상 인터페이스', dxgw_virtual_private_gateways: '가상 프라이빗 게이트웨이', dxgw_transit_gateways: 'Transit Gateway' }
  const UT_PART = { ingress_ipv4: '인바운드 IPv4', ingress_ipv6: '인바운드 IPv6', egress_ipv4: '아웃바운드 IPv4', egress_ipv6: '아웃바운드 IPv6', ingress: '인바운드', egress: '아웃바운드' }
  const UT_SRC = { account_quota: '계정 한도', default_quota: '기본 한도', fixed: '고정 한도', resource_attribute: '회선 대역폭', aws_computed: 'AWS 계산' }
  const utSrc = (m) => m.limit_source === 'arithmetic' && m.limit ? `계산(/${Math.round(32 - Math.log2(m.limit + 5))} − 5)` : UT_SRC[m.limit_source] || '—'
  // 한도·사용률을 못 읽은 이유(§3k K3 · 화면 말투 = docs/qa/copy-a.md 합니다체) — 첫 줄 = 고객 말(`X.popReason` = §17.26 C-3 표 ·
  //   표 밖 코드 = 「읽지 못했습니다.」) · 둘째 줄 = 원문(코드 · 실패한 API · AWS 메시지). 🔴 코드·API 이름을 사람 문장 자리에 내지 않는다
  //   (옛 결함 2개: 객체를 그대로 찍어 `[object Object]` · 메시지가 없으면 `access_denied` 가 그대로 보였다)
  const utReason = (r, lead) => {
    if (!r) return ''
    const raw = [r.code, r.api, r.message].filter(Boolean).join(' · ')
    return (lead ? lead + ' ' : '') + X.popReason(r.code) + (raw ? '\n원문: ' + raw : '')
  }
  const UT_LIMIT_LEAD = '계정 한도를 읽지 못해 기본 한도로 계산합니다.'
  const utNum = (x) => { const r = x >= 10 ? Math.round(x) : Math.round(x * 10) / 10; return String(r) }
  function utFmt (v, unit) {
    if (v == null) return '?'
    if (unit === 'percent') return `${utNum(v)}%`
    if (unit === 'count') return Math.round(v).toLocaleString('en-US')
    // 1분에 1회 = 0.017/s — 반올림하면 「0/s」(쓰지 않은 것)처럼 읽힌다(188 실화면 2026-10-26 · Lambda 1,440회/24시간) → 「<0.1/s」
    if (unit === 'count_per_second') return v > 0 && v < 0.05 ? '<0.1/s' : `${utNum(v)}/s`
    const u = unit === 'packet_per_second' ? ['pps', 'kpps', 'Mpps', 'Gpps'] : ['bps', 'kbps', 'Mbps', 'Gbps', 'Tbps']
    let i = 0; let x = v
    while (x >= 1000 && i < u.length - 1) { x /= 1000; i++ }
    return `${i ? utNum(x) : Math.round(x)} ${u[i]}`
  }
  const utPct = (p) => (p == null ? '?' : p > 0 && p < 1 ? '<1%' : `${Math.round(p)}%`)
  // 세모 빠르기 4단 = 가장 바빴던 단위의 속도(bit/s) <1 Mbps · 1–100 Mbps · 100 Mbps–1 Gbps · ≥1 Gbps(§17.25 개정 2026-10-26 · 결정 ②)
  const utLv = (bps) => (bps < 1e6 ? 1 : bps < 1e8 ? 2 : bps < 1e9 ? 3 : 4)
  // 기간 합 글자 — 바이트 = 1000 단위(B · KB · MB · GB · TB) · 횟수·패킷 = 쉼표
  function utBytes (b) {
    const u = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']; let i = 0; let x = b
    while (x >= 1000 && i < u.length - 1) { x /= 1000; i++ }
    return `${i ? utNum(x) : Math.round(x)} ${u[i]}`
  }
  const utTot = (v) => (v.tot == null ? '?' : v.tu === 'byte' ? utBytes(v.tot) : v.tu === 'packet' ? `${Math.round(v.tot).toLocaleString('en-US')} 패킷` : Math.round(v.tot).toLocaleString('en-US'))
  // 통신량 한 값의 글자(배지 · 줄인 글자 · 목록 첫 칸) — 요청·호출 = 「요청 N건」·「호출 N회」 · 연결 = 「연결 N」 · 옛 문서(합 없음) = 가장 바빴던 단위
  function utMain (v) {
    const m = v.m
    if (UT_BADGE === 'total' && v.tot != null) return m.measure === 'requests' ? `요청 ${utTot(v)}건` : m.measure === 'invocations' ? `호출 ${utTot(v)}회` : utTot(v)
    if (m.measure === 'connections') return `연결 ${utFmt(v.peak, 'count')}`
    return m.unit === 'percent' ? `${UT_M[m.measure] || m.measure} ${utFmt(v.peak, 'percent')}` : utFmt(v.peak, m.unit)
  }
  // 「N일치만」 — 기록이 기간보다 짧은 선(엔진이 덮은 시간을 줄여 실은 것만)
  function utShort (w) {
    const c = w && w.covered_seconds; if (c == null || c >= w.minutes * 60) return ''
    if (c >= 86400) return `${Math.floor(c / 86400)}일치만`
    if (c >= 3600) return `${Math.floor(c / 3600)}시간치만`
    return `${Math.max(1, Math.floor(c / 60))}분치만`
  }
  // 멈춘 EC2(§17.25 「멈춘 EC2」) — 스냅샷 상태가 stopped. 멈춘 인스턴스는 지표를 내지 않는다
  const utStopped = (id) => { const n = id && X.mById.get(id); return !!n && n.type === 'ec2_instance' && (n.attrs || {}).state === 'stopped' }
  // 값 없음(#128 · 불변식 ④ · 관측 1.13.0 `no_points`) — 읽었는데 「값이 있을 때만 보내는」 지표가 이 기간에 점을 하나도 안 보냈다.
  //   트래픽이 없었는지 · 새 자원인지 · 지표가 발행되지 않는지 엔진도 모른다 → 0 도 「?」(못 읽음)도 아니다. 정렬·세모 = 모름
  const utNoPts = (w) => w.no_points === true
  const UT_NONE_WHY = 'AWS 가 이 기간에 이 지표 값을 하나도 보내지 않았습니다. 트래픽이 없었거나, 새로 만든 자원이거나, 이 지표가 발행되지 않는 경우입니다.'
  // 한 측정의 이 기간 값 — st: 'pct'(비율 있음) · 'val'(값만 · 한도 ?) · 'unread'(사용률 ?) · 'none'(값 없음 = 읽었고 · 점이 없다 · 0 이 아니다)
  //   · 'stop'(멈춤 = 멈췄고 · 읽었고 · 점이 없다)
  function utVal (m, W, id) {
    const v = { m, st: 'unread', pct: null, peak: null, avg: null, avgPct: null, sum: null, short: '', dir: null, az: null, tot: null, tu: null, cls: 0 }
    if (m.unread) return v
    if (m.kind === 'count') {
      const u = m.now ? m.now.used : null; if (u == null) return v
      v.peak = u; v.st = m.limit ? 'pct' : 'val'; if (m.limit) v.pct = u / m.limit * 100
      return v
    }
    const w = (m.windows || []).find((x) => x.minutes === W); if (!w) return v
    if (utNoPts(w)) { v.st = 'none'; return v }
    if (m.kind === 'events') { if (w.sum == null) return v; v.sum = w.sum; v.st = 'val'; return v }
    if (w.peak == null) { if (utStopped(id)) v.st = 'stop'; return v }
    v.peak = w.peak; v.avg = w.average == null ? null : w.average; v.short = utShort(w); v.dir = w.peak_direction || null; v.az = w.peak_az || null
    // 🔁 사용량(2026-10-26) — 통신량은 한도로 나누지 않는다. 옛 문서(1.17.0 이전)의 limit·% 도 비율로 쓰지 않는다(§17.25 개정 「옛 문서」)
    v.tot = w.total == null ? null : w.total; v.tu = m.total_unit || null; v.cls = v.tu in UT_TCLS ? UT_TCLS[v.tu] : 3
    v.st = 'val'
    return v
  }
  const UT_RANK = { pct: 0, val: 1, unread: 2, none: 3, stop: 4 }   // 값 없음 = 모름 — 0% 자리(pct)에 서지 않는다(#128)
  // 순서 — 상태 → 합의 단위 묶음(바이트 → 횟수 → 패킷 → 그 밖) → 합 내림차순 → 비율(개수 한도) → 이름 → id(결정론)
  const utRank = (a, b) => (UT_RANK[a.st] - UT_RANK[b.st]) || ((a.cls || 0) - (b.cls || 0)) || ((b.tot ?? -1) - (a.tot ?? -1)) || ((b.pct ?? -1) - (a.pct ?? -1))
  const utCmp = (a, b) => utRank(a, b) || a.name.localeCompare(b.name) || a.key.localeCompare(b.key)
  // 목록 자원(SG·NACL·라우트 표 — 노드가 아니다 · §13-55)의 표 행. 엔진 id `<종류>/<계정>/<리전>/<id>` → 그 VPC 표의 행
  //   고치기 전에는 사용률 목록이 이 행을 못 찾아 엔진 id 를 그대로 이름 칸에 썼다(QA B · `sg-…`·`acl-…` 가 보였다)
  const listRow = (id) => {
    const [t, acct, rg, nat] = String(id).split('/'); if (!nat || !M) return null
    for (const [vid, rs] of M.list) {
      if (String(vid).split('/')[1] !== acct) continue
      const r = rs.find((x) => x.type === t && x.id === nat && x.region === rg); if (r) return r
    }
    return null
  }
  // 목록 행 — 통신량(rate) · 개수 한도(count). 거부·드롭 수(events)는 배지에만 붙는다
  //   이름 칸 = 다른 행과 같이 이름(목록 자원 = SG 그룹 이름 · NACL Name 태그) · AWS id 는 그 칸의 title
  function utRows (rows, W) {
    const rate = []; const count = []
    for (const r of rows || []) {
      if (!r || !Array.isArray(r.measures)) continue
      const n = X.mById.get(r.target_id); const lr = n ? null : listRow(r.target_id)
      // VPC 엔드포인트 = 정보창과 같은 전체 서비스 이름(`com.amazonaws.<리전>.ssm` — 줄인 「ssm」 은 리전·계정마다 같은 이름이 여럿이라 못 가린다 · 2026-10-26)
      const vsvc = n && n.type === 'vpc_endpoint' && n.con && n.con.service_name ? String(n.con.service_name) : ''
      const name = vsvc || (n ? nodeName(r.target_id) : lr ? lr.name || lr.id : r.target_id)
      const type = n ? n.type : lr ? lr.type : ''
      const aid = n ? natId(n) : lr ? lr.id : String(r.target_id).split('/').pop()
      for (const m of r.measures) {
        if (m.kind === 'events') continue
        const v = utVal(m, W, r.target_id); const row = { ...v, key: `${r.target_id}|${m.measure}`, id: r.target_id, type, name, aid }
        ;(m.kind === 'count' ? count : rate).push(row)
      }
    }
    return { rate: rate.sort(utCmp), count: count.sort(utCmp) }
  }
  // 사용률이 앉는 자리 — TGW 어태치먼트 = 그 선 · 그 밖 = 그 자원의 상자(목록 자원 = 그 VPC)
  function utHome (id) {
    const ls = B.lines.filter((l) => (l.kind === 'att' && l.a === id) || l.att === id)
    if (ls.length) return { lines: ls }
    const a = markAt(id) || listHome(id); if (!a) return null
    const b = B.boxes.get(a.key)
    if (b && b.kind === 'aws-port') { const pl = B.lines.filter((l) => l.kind === 'att' && l.a === a.key); if (pl.length) return { lines: pl, at: a } }
    return { at: a, box: b }
  }
  // 배지 글자 — 한 자원의 대표(비율이 가장 높은 rate 측정) + 거부·드롭 사실
  function utBadgeText (r, W) {
    const ms = r.measures || []
    let best = null
    if (ms.some((m) => m.measure === 'ip_addresses')) {
      const v = utVal(ms.find((m) => m.measure === 'ip_addresses'), W)
      return { v, text: v.st === 'pct' ? `IP ${utPct(v.pct)} · ${utFmt(v.peak, 'count')} / ${utFmt(v.m.limit, 'count')}` : '사용률 ?' }
    }
    // 대표 = 상태 → 합의 단위 묶음(바이트가 먼저) → 합이 큰 것 · 같으면 먼저 적힌 측정. 연결 수(RDS 보조)는 대표가 아니라 꼬리
    for (const m of ms) {
      if (m.kind !== 'rate' || !UT_RATE.has(m.measure) || m.measure === 'connections') continue
      const v = utVal(m, W, r.target_id)
      if (!best || utRank(v, best) < 0) best = v
    }
    const conn = ms.find((m) => m.kind === 'rate' && m.measure === 'connections')
    if (!best && conn) best = utVal(conn, W, r.target_id)
    if (!best) return null
    let text = best.st === 'unread' ? '사용률 ?' : best.st === 'stop' ? '멈춤' : best.st === 'none' ? '값 없음' : utMain(best)
    if (best.st === 'val' && best.m.includes === 'tunnel_health_checks') text += ' · 점검 포함'
    if (best.st !== 'unread' && best.short) text += ` · ${best.short}`
    if (conn && conn !== best.m) { const cv = utVal(conn, W, r.target_id); if (cv.st === 'val') text += ` · 연결 ${utFmt(cv.peak, 'count')}` }
    for (const e of ms) {
      if (e.kind !== 'events') continue
      const v = utVal(e, W); const nm = UT_M[e.measure] || e.measure
      if (v.st === 'unread') text += ` · ${nm} ?`
      else if (v.sum > 0) text += ` · ${nm} ${Math.round(v.sum).toLocaleString('en-US')}`
    }
    return { v: best, text }
  }
  // 줄인 글자 — 자리가 없을 때만(§17.25). 비율 · IP 비율 · 「한도 ?」 · 「?」
  function utTight (t) {
    if (t.st === 'unread') return '?'
    if (t.st === 'stop') return '멈춤'
    if (t.st === 'none') return '값 없음'
    if (t.v.m.measure === 'ip_addresses') return `IP ${utPct(t.pct)}`
    return t.st === 'pct' ? utPct(t.pct) : utMain(t.v)
  }
  // 장면 — 배지(자리 찾기는 `busy` 를 등급 배지와 나눠 쓴다) · 세모. null = 그리지 않음(모니터링만 · 행 없음)
  let UTS = null
  let UTL = ''   // 겹친 층에 넣을 세모(§17.25 「성능」)
  const utlEl = () => (X && X.svgEl.ownerDocument.getElementById('awsutl')) || null
  function utScene (busy) {
    UTS = null
    const S = X.S; const rows = X.util ? X.util.rows() : null
    if (!rows || !rows.length) return null
    const W = S.utWin
    // 자리마다 묶는다 — 같은 선 · 같은 상자에 앉는 자원이 여럿이면 가장 바쁜 것 + `외 N−1`
    const seats = new Map()
    for (const r of rows) {
      if (!r || !r.target_id) continue
      const t = utBadgeText(r, W); if (!t) continue
      const h = utHome(r.target_id); if (!h) continue
      const ent = { r, ...t, name: nodeName(r.target_id), key: r.target_id, st: t.v.st, pct: t.v.pct, cls: t.v.cls, tot: t.v.tot }
      const ks = h.lines ? h.lines.map((l) => l.k) : [h.at.key]
      for (const k of ks) { if (!seats.has(k)) seats.set(k, { k, line: h.lines ? h.lines.find((l) => l.k === k) : null, at: h.at, box: h.box, list: [] }); seats.get(k).list.push(ent) }
    }
    const out = []; const tout = []; const tri = new Map(); const badges = []
    const order = [...seats.values()].sort((a, b) => (a.line ? 0 : 1) - (b.line ? 0 : 1) || a.k.localeCompare(b.k, 'en', { numeric: true }))
    // 자리 = 겹치지 않는 첫 후보(§17.25). 없으면 줄인 글자로 한 번 더 · 그래도 없으면 그림에 놓지 않는다(목록에는 있다)
    //   고치기 전에는 막힌 자리에서 첫 후보에 겹쳐 놓아 배지와 이름이 서로를 가렸다(검사 141 첫 실행)
    const drop = []
    const seat = (s, w) => {
      if (s.line) return lineCands(s.line, w).find((q) => !hitIn(busy, q)) || null
      if (s.box.kind === 'aws-subnet') {
        const b = s.box; const nmw = tw(b.nm || 'Subnet', 11)
        const cs = [{ x: b.x + b.w - 22 - w, y: b.y + 8, w, h: 12, tx: b.x + b.w - 22, ty: b.y + 17.5, a: 'end' },
          { x: b.x + b.w - 6 - w, y: b.y + b.h - 15, w, h: 12, tx: b.x + b.w - 6, ty: b.y + b.h - 6, a: 'end' }]
        // 이름 줄 자리는 이름과 닿으면 비어 있어도 쓰지 않는다 · 그 서브넷 자기 머리 줄은 막힘으로 치지 않는다(이름 비켜 두기가 대신한다)
        //   고치기 전에는 자기 머리 줄에 늘 막혀 이름 줄 자리를 한 번도 쓰지 못했다(검사 141 브레이크 `27-이름가림` 이 살아나지 않아 찾았다)
        const own = busy.filter((q) => !(q.hdr && q.x === b.x && q.y === b.y))
        return cs.find((q, i) => (i || q.x > b.x + 28 + nmw + 4) && !hitIn(i ? busy : own, q)) || null
      }
      const ic = B.icon.get(s.at.key)
      const vis = B.vis.get(s.at.key) || [{ x: ic.cx - 18, y: ic.cy - 18, w: 36, h: 36 }]
      const bot = Math.max(...vis.map((q) => q.y + q.h)); const top0 = Math.min(...vis.map((q) => q.y))
      const right = Math.max(...vis.map((q) => q.x + q.w))
      const cs = [0, 12, 24].map((dy) => ({ x: ic.cx - w / 2, y: bot + 1 + dy, w, h: 11, tx: ic.cx, ty: bot + 10 + dy, a: 'middle' }))
      cs.push({ x: ic.cx - w / 2, y: top0 - 13, w, h: 11, tx: ic.cx, ty: top0 - 4, a: 'middle' })
      cs.push({ x: right + 3, y: ic.cy - 6, w, h: 11, tx: right + 3, ty: ic.cy + 3, a: 'start' })
      return cs.find((q) => !hitIn(busy, q)) || null
    }
    for (const s of order) {
      s.list.sort(utCmp)
      const top = s.list[0]
      if (s.line) {
        if (!s.line.pts || s.line.pts.length < 2) continue
        // 세모 = 바이트가 0 보다 큰 선(§17.25 개정 2026-10-26 · 결정 ②) — `pct` 칸에 속도(bit/s)를 실어 빠르기·「더 바쁜 선」을 가른다
        const tv = top.v
        if (top.st === 'val' && tv.m.measure === 'bandwidth' && tv.peak != null && (tv.tot != null ? tv.tot > 0 : tv.peak > 0)) tri.set(s.line.k, { l: s.line, pct: tv.peak, dir: tv.dir, id: top.key })
      } else if (s.box && s.box.kind === 'aws-subnet') {
        if (s.at.inside) continue   // 서브넷을 끈 화면 — 목록·고르기(VPC 강조)만
      } else if (s.box && (s.box.kind === 'aws-item' || s.box.kind === 'aws-hub' || s.box.kind === 'aws-port')) {
        if (!B.icon.get(s.at.key)) continue
      } else continue   // 틀(VPC · 접힌 묶음)에 앉는 rate — 목록에만
      let text = top.text + (s.list.length > 1 ? ` 외 ${s.list.length - 1}` : ''); let short = false
      let c = seat(s, tw(text, 9.5))
      if (!c) { text = utTight(top); short = true; c = seat(s, tw(text, 9.5)) }
      if (!c) { drop.push({ k: s.k, id: top.key }); continue }
      busy.push(c)
      badges.push({ k: s.k, text, st: top.st, id: top.key, n: s.list.length, short })
      out.push(`<text class="aw-ub" data-k="${esc(s.k)}" data-st="${top.st}"${s.list.length > 1 ? ` data-n="${s.list.length}"` : ''}${short ? ' data-short="1"' : ''} x="${P2(c.tx)}" y="${P2(c.ty)}" fill="#8b949e" font-size="9.5" text-anchor="${c.a}" paint-order="stroke" stroke="${BG}" stroke-width="3" stroke-linejoin="round">${esc(text)}</text>`)
    }
    // 세모 — 포개진 곧은 구간(8px 넘게 같이 달림)은 가장 바쁜 선의 것만(같으면 먼저 그린 선)
    const T = [...tri.values()].map((t) => utPath(t))
    const busier = (a, b) => (b.pct - a.pct) || a.idx - b.idx
    for (let i = 0; i < T.length; i++) for (let j = i + 1; j < T.length; j++) {
      const [p, q] = busier(T[i], T[j]) <= 0 ? [T[i], T[j]] : [T[j], T[i]]
      for (const sq of q.segs) for (const sp of p.segs) {
        const ov = utOverlap(sp, sq); if (ov) q.hide.push(ov)
      }
    }
    const tris = []
    for (const t of T) {
      const n = Math.floor(t.len / UT_GAP); if (!n) continue
      const lv = utLv(t.pct); const sp = t.len / n
      const items = []; for (let i = 0; i < n; i++) items.push((i + 0.5) * sp)
      const q = { ...t, n, sp, lv, items }; tris.push(q)
      tout.push(`<g class="aw-utt" data-k="${t.k}" data-lv="${lv}" data-dir="${t.rev ? 'out' : 'in'}" data-n="${n}"><path d="${utLineD(q, 0)}" fill="${UT_COL}" stroke="${BG}" stroke-width="0.9" stroke-linejoin="round"/></g>`)
    }
    UTS = { W, tris, badges, tri, seats: [...seats.keys()], drop }
    return { svg: out.join(''), tsvg: tout.join(''), tri }
  }
  // 선 길이 공간 — 세모는 l.a → l.b 가 정방향(TGW 들어옴 `in` = 포트 → TGW). `out` 이면 거꾸로 달린다
  function utPath (t) {
    const pts = t.dir === 'out' ? t.l.pts.slice().reverse() : t.l.pts
    const segs = []; let acc = 0
    for (let i = 1; i < pts.length; i++) {
      const [x1, y1] = pts[i - 1]; const [x2, y2] = pts[i]; const len = Math.hypot(x2 - x1, y2 - y1)
      if (len > 0) segs.push({ x1, y1, x2, y2, len, s: acc, a: Math.atan2(y2 - y1, x2 - x1) * 180 / Math.PI })
      acc += len
    }
    return { k: t.l.k, idx: B.lines.indexOf(t.l), pct: t.pct, rev: t.dir === 'out', segs, len: acc, hide: [] }
  }
  function utPose (t, s) {
    for (const g of t.segs) if (s <= g.s + g.len) { const f = (s - g.s) / g.len; return { x: g.x1 + (g.x2 - g.x1) * f, y: g.y1 + (g.y2 - g.y1) * f, a: g.a } }
    const g = t.segs[t.segs.length - 1]; return { x: g.x2, y: g.y2, a: g.a }
  }
  const utHidden = (t, s) => t.hide.some(([a, b]) => s >= a && s <= b)
  // 한 선의 세모 전부 = 모양 하나(§17.25 「성능」 — 세모마다 따로 옮기면 움직일 때마다 브라우저가 층을 통째로 다시 나눈다).
  //   세모 하나 = 꼭짓점 셋(앞 끝이 진행 방향) · 숨은 세모 = 그리지 않는 점 하나(`M x,y`)로 남겨 순서를 지킨다(검사 141·142 가 하나하나 읽는다)
  const P3 = (n) => Math.round(n * 100) / 100
  function utTriD (ps, hid) {
    const x = P2(ps.x); const y = P2(ps.y)
    if (hid) return `M${x},${y}`
    const r = Math.round(ps.a) * Math.PI / 180; const c = Math.cos(r); const sn = Math.sin(r)
    return 'M' + UT_TRI.map(([u, v]) => `${P3(x + u * c - v * sn)},${P3(y + u * sn + v * c)}`).join('L') + 'Z'
  }
  function utLineD (t, d) {
    let o = ''
    for (const s0 of t.items) { const s = ((s0 + d) % t.len + t.len) % t.len; o += utTriD(utPose(t, s), utHidden(t, s)) }
    return o
  }
  // 두 곧은 구간이 같은 줄에서 8px 넘게 겹치면 q 쪽 길이 공간의 [시작, 끝]
  function utOverlap (p, q) {
    const hz = (g) => Math.abs(g.y1 - g.y2) < 0.5; const vt = (g) => Math.abs(g.x1 - g.x2) < 0.5
    let lo, hi, ax
    if (hz(p) && hz(q) && Math.abs(p.y1 - q.y1) < 0.5) { ax = 'x' } else if (vt(p) && vt(q) && Math.abs(p.x1 - q.x1) < 0.5) { ax = 'y' } else return null
    const r = (g) => ax === 'x' ? [Math.min(g.x1, g.x2), Math.max(g.x1, g.x2)] : [Math.min(g.y1, g.y2), Math.max(g.y1, g.y2)]
    const [p0, p1] = r(p); const [q0, q1] = r(q); lo = Math.max(p0, q0); hi = Math.min(p1, q1)
    if (hi - lo <= 8) return null
    const at = (v) => q.s + Math.abs(v - (ax === 'x' ? q.x1 : q.y1))
    const a = at(lo); const b = at(hi)
    return [Math.min(a, b), Math.max(a, b)]
  }
  // 움직임 — rAF 한 루프가 세모 전부를 옮긴다(§6.10 예외 · 대장 124). 줄임 동작 · 움직임 꺼짐 = 멈춘 자리 그대로
  let utRaf = 0; let utT0 = 0; let utEls = null
  const utReduce = () => !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches)
  function utAnimStart () {
    if (utRaf) cancelAnimationFrame(utRaf); utRaf = 0; utEls = null
    if (!UTS || !UTS.tris.length || !X.S.anim || utReduce()) return
    const root = utlEl() || X.svgEl
    utEls = UTS.tris.map((t) => ({ t, el: root.querySelector(`.aw-utt[data-k="${t.k}"] > path`) })).filter((e) => e.el)
    utT0 = performance.now()
    const step = (now) => {
      if (!utEls) return
      const dt = (now - utT0) / 1000
      for (const { t, el } of utEls) el.setAttribute('d', utLineD(t, UT_SPD[t.lv] * dt))
      utRaf = requestAnimationFrame(step)
    }
    utRaf = requestAnimationFrame(step)
  }
  // ---- 막대 · 기간 메뉴 · 사용률 목록 · 고르기(§17.25). 전부 `#svg` 밖 HTML — 내보내기에 섞이지 않는다
  let utOpen = ''; let utTab = 'rate'
  const utBar = () => document.getElementById('awsut')
  // 상단바 「사용률」 버튼(대장 127) — 막대와 **같은 행**을 보고 같은 때 다시 그린다(둘이 따로 판단하면 버튼은 켜졌는데 막대가 없는 화면이 나온다)
  const utBtn = () => document.getElementById('btn-util')
  //   🔴 모니터링과 **무관**하다(사용자 결정 2026-10-06 「사용률만 누르면 바로 사용률이 보이면 되」) — 흐려지는 때 = 켤 수 없을 때뿐
  //   (사용률 값이 구워져 있지 않고 실시간 주소도 없거나 만료 · 비교 칸). 켜고 끄기는 flow.js `utilClick` 한 곳이 한다
  function utBtnSync () {
    const b = utBtn(); if (!b || !X) return
    const u = X.util || {}; const on = !!(u.on && u.on()); const can = !!(u.can && u.can())
    const dis = !can && !on
    b.classList.toggle('dis', dis); b.classList.toggle('on', on)
    b.setAttribute('aria-disabled', dis ? 'true' : 'false'); b.setAttribute('aria-pressed', on ? 'true' : 'false')
    b.title = dis ? '사용률 — 이 파일에는 사용률 값과 실시간 주소가 없어 켤 수 없습니다' : on ? '사용률 — 눌러서 사용률을 끕니다' : '사용률 — 눌러서 사용률을 켭니다(모니터링과 별개)'
  }
  function utBtnClick (e) {
    if (!X) return
    e.stopPropagation()
    if (!X.util || !X.util.click) return
    utOpen = ''
    X.util.click()
  }
  function utBarRender () {
    const el = utBar(); if (!el || !X) return
    const S = X.S; const rows = X.util ? X.util.rows() : null
    utBtnSync()
    if (!rows || !rows.length) { el.style.display = 'none'; el.innerHTML = ''; utOpen = ''; return }
    el.style.display = 'flex'
    // 「보기: 모니터링만/사용률만/둘 다」 칸은 걷어냈다(2026-10-06) — 상단 두 버튼이 그 셋을 이미 고른다
    const W = S.utWin; const R = utRows(rows, W)
    const win = UT_WIN.find((x) => x.m === W) || UT_WIN[0]
    let h = `<button type="button" class="ut-b${utOpen === 'win' ? ' on' : ''}" data-ut="win">최근 ${win.t} ▾</button>` +
      `<button type="button" class="ut-b${utOpen === 'list' ? ' on' : ''}" data-ut="list">사용률 목록 ▾ <b>${R.rate.length + R.count.length}</b></button>`
    const pk = S.utPick ? [...R.rate, ...R.count].find((r) => r.key === S.utPick) : null
    if (pk) h += `<span class="ut-chip">선택: ${esc(pk.name)} <button type="button" data-ut="unpick" title="고르기 풀기">✕</button></span>`
    if (utOpen === 'win') {
      h += `<div id="aws-utm" class="ut-pop"><div class="ut-h">${UT_BADGE === 'total' ? '기간 동안의 사용량 합 기준' : '기간 중 가장 바빴던 때 기준'}</div>`
      for (const g of [0, 1]) {
        h += `<div class="ut-g">${g ? '추세 · 1시간마다 갱신' : '지금 상태'}</div>`
        for (const x of UT_WIN.filter((q) => q.g === g)) h += `<button type="button" class="ut-w${x.m === W ? ' on' : ''}" data-ut="w" data-m="${x.m}"><span class="ut-ck">${x.m === W ? '✓' : ''}</span>최근 ${x.t}</button>`
      }
      h += '</div>'
    }
    if (utOpen === 'list') {
      const tab = (t, nm, n) => `<button type="button" class="ut-tab${utTab === t ? ' on' : ''}" data-ut="tab" data-t="${t}">${nm} ${n}</button>`
      h += `<div id="aws-utp" class="ut-pop"><div class="ut-tabs">${tab('rate', '통신량', R.rate.length)}${tab('count', '개수 한도', R.count.length)}</div>`
      const bar = (p) => p == null ? '' : `<i class="ut-bar" style="width:${Math.min(100, p) * 0.5}px"></i>`
      const cell = (r, p, s) => r.st === 'unread' ? '?' : r.st === 'stop' ? '멈춤' : r.st === 'none' ? '값 없음' : r.st === 'val' ? (s === 'pct' ? '한도 ?' : '') : `${bar(p)}${utPct(p)}`
      if (utTab === 'rate') {
        // 🔁 사용량(§17.25 개정 2026-10-26) — 칸 = 대상 · 종류 · 사용량(기간 합) · 가장 바빴던 <단위> · 평균. 「값 / 한도」·「한도 출처」 칸은 개수 한도 칸에만
        h += `<div class="ut-h">사용량 많은 순 · 최근 ${win.t} · 줄을 누르면 그 선이 굵어집니다</div><table><thead><tr><th>대상</th><th>종류</th><th>사용량</th><th>가장 바빴던 ${UT_UNIT[W]}</th><th>평균</th></tr></thead><tbody>`
        for (const r of R.rate) {
          const m = r.m; const sh = r.short ? ` <span class="ut-sh">${r.short}</span>` : ''
          const gone = r.st === 'stop' ? '멈춤' : r.st === 'none' ? '값 없음' : '?'
          const use = r.st === 'val' ? esc(utMain(r)) + sh : gone
          const pk = r.st === 'val' ? utFmt(r.peak, m.unit) : r.st === 'unread' ? '?' : '—'
          const avg = r.st === 'val' ? (r.avg == null ? '—' : utFmt(r.avg, m.unit)) : r.st === 'unread' ? '?' : '—'
          const nm = r.name + (r.az ? ` · ${r.az}` : '') + (m.includes === 'tunnel_health_checks' ? ' (점검 바이트 포함)' : '')
          h += `<tr class="ut-r${S.utPick === r.key ? ' on' : ''}" data-ut="row" data-r="${esc(r.key)}" data-st="${r.st}"${m.unread ? ` title="${esc(utReason(m.unread))}"` : r.st === 'none' ? ` title="${esc(UT_NONE_WHY)}"` : ''}><td title="${esc(r.aid)}">${esc(nm)}</td><td>${esc(((TYPE_LBL(r.type)) + ' ' + (UT_M[m.measure] || m.measure)).trim())}</td>` +
            `<td>${use}</td><td>${pk}</td><td>${avg}</td></tr>`
        }
      } else {
        h += '<div class="ut-h">한도에 가까운 순 · 지금 개수(기간과 무관)</div><table><thead><tr><th>대상</th><th>무엇을 세나</th><th>사용률</th><th>지금 / 한도</th><th>한도 출처</th></tr></thead><tbody>'
        for (const r of R.count) {
          const m = r.m; const what = (UT_M[m.measure] || m.measure) + (m.part ? ` · ${UT_PART[m.part] || m.part}` : '')
          const val = r.st === 'unread' ? '?' : r.st === 'pct' ? `${utFmt(r.peak, 'count')} / ${utFmt(m.limit, 'count')}` : utFmt(r.peak, 'count')
          h += `<tr class="ut-r${S.utPick === r.key ? ' on' : ''}" data-ut="row" data-r="${esc(r.key)}" data-st="${r.st}"${m.unread ? ` title="${esc(utReason(m.unread))}"` : ''}><td title="${esc(r.aid)}">${esc(r.name)}</td><td>${esc(what)}</td>` +
            `<td>${cell(r, r.pct, 'pct')}</td><td>${val}</td><td${m.limit_reason ? ` title="${esc(utReason(m.limit_reason, UT_LIMIT_LEAD))}"` : ''}>${esc(utSrc(m))}</td></tr>`
        }
      }
      h += '</tbody></table></div>'
    }
    el.innerHTML = h
  }
  const TYPE_LBL = (t) => (X && X.TYPE_LABEL && X.TYPE_LABEL[t]) || ''
  // 고르기 — 다시 그리기가 강조를 지우므로 그린 뒤마다 다시 건다. 고른 줄이 이 문서에 없으면 푼다
  function utApplyPick () {
    if (!X) return
    const svg = X.svgEl; const S = X.S; const roots = [svg, utlEl()].filter(Boolean)
    for (const r of roots) { for (const e of r.querySelectorAll('.aw-uhl')) e.classList.remove('aw-uhl'); r.classList.remove('aw-upick') }
    const rows = X.util ? X.util.rows() : null
    if (!S.utPick) return
    if (!rows || !rows.length) { S.utPick = null; return }
    const id = S.utPick.slice(0, S.utPick.lastIndexOf('|'))
    const ms = S.utPick.slice(S.utPick.lastIndexOf('|') + 1)
    const r = rows.find((x) => x && x.target_id === id && (x.measures || []).some((m) => m.measure === ms))
    if (!r) { S.utPick = null; return }
    const h = utHome(id); if (!h) { for (const r of roots) r.classList.add('aw-upick'); return }
    const add = (sel) => { for (const r of roots) for (const e of r.querySelectorAll(sel)) e.classList.add('aw-uhl') }
    if (h.lines) for (const l of h.lines) add(`.aw-ln[data-k="${l.k}"],.aw-fl[data-k="${l.k}"],.aw-utt[data-k="${l.k}"],.aw-ub[data-k="${l.k}"]`)
    else { add(h.at.sel); add(`.aw-ub[data-k="${CSS.escape(h.at.key)}"]`) }
    for (const r of roots) r.classList.add('aw-upick')
  }
  function utPickSet (key) {
    const S = X.S; S.utPick = key && S.utPick !== key ? key : null
    closeAll(); utOpen = ''; utBarRender(); utApplyPick()
  }
  // 막대 클릭 — 메뉴 열기/닫기 · 보기 · 기간 · 칸 · 줄 · ✕
  function utClick (e) {
    const b = e.target.closest('[data-ut]'); if (!b || !X) return
    e.stopPropagation()
    const S = X.S; const a = b.dataset.ut
    if (a === 'win' || a === 'list') { utOpen = utOpen === a ? '' : a; utBarRender(); return }
    if (a === 'w') { S.utWin = +b.dataset.m; utOpen = ''; X.rerender(); return }
    if (a === 'tab') { utTab = b.dataset.t; utBarRender(); return }
    if (a === 'row') { utPickSet(b.dataset.r); return }
    if (a === 'unpick') utPickSet(null)
  }
  // Esc — 열린 메뉴부터 닫고, 메뉴가 없으면 고르기를 푼다. 먹은 게 없으면 false(다른 오버레이가 쓴다)
  function utEsc () {
    if (utOpen) { utOpen = ''; utBarRender(); return true }
    if (X && X.S.utPick) { utPickSet(null); return true }
    return false
  }
  function utAfterRender () { utBarRender(); utApplyPick(); utAnimStart() }
  const utDump = () => (UTS ? { W: UTS.W, badges: UTS.badges.map((b) => ({ ...b })), drop: UTS.drop.map((d) => ({ ...d })), tris: UTS.tris.map((t) => ({ k: t.k, n: t.n, lv: t.lv, pct: t.pct, rev: t.rev, len: P2(t.len), hide: t.hide.map(([a, b]) => [P2(a), P2(b)]) })) } : null)
  const utList = () => (X && X.util && X.util.rows() ? utRows(X.util.rows(), X.S.utWin) : null)

  // 이력 비교 칸(§17.23) — 표시가 앉을 자리. 대리 상자(`aws:rep`)에만 대표되거나 어디에도 없으면 null(= 못 그린 변화)
  //   inside = 묶음 안(`×N` 아이콘 · 포트에 합쳐진 어태치먼트 · 서브넷을 끈 화면의 서브넷 → VPC) — 테·흐림 없이 알약만 붙는다
  const MARK_SEL = { 'aws-item': 'aw-ic', 'aws-hub': 'aw-ic', 'aws-port': 'aw-pt', 'aws-subnet': 'aw-sn' }
  function markAt (id) {
    if (!B) return null
    const at = (k, inside) => {
      const b = B.boxes.get(k); if (!b) return null
      const q = CSS.escape(k)
      const sel = MARK_SEL[b.kind] ? `.${MARK_SEL[b.kind]}[data-k="${q}"]` : b.kind === 'aws-vpc' ? `.aw-vpc[data-v="${q}"]` : `[data-f="${q}"]`
      return { key: k, sel, x: b.x, y: b.y, w: b.w, h: b.h, inside }
    }
    let cur = id; let inside = false
    for (let g = 0; cur && g < 6; g++) {
      if (B.cellOf.has(cur)) { const k = B.cellOf.get(cur); return at(k, inside || (B.cellIds.get(k) || []).length > 1) }
      if (cur !== 'aws:rep' && B.boxes.has(cur)) return at(cur, inside)
      if (M.subnets.has(cur)) { cur = M.subnets.get(cur).vpc; inside = true; continue }
      const h = M.hidden.get(cur); if (!h || h === 'aws:rep') return null
      cur = h; inside = true
    }
    return null
  }
  // 그 관계를 실은 선 — **관계 id(`obsIds`)로만** 찾는다(끝점으로 찾으면 같은 두 끝 사이의 다른 관계에 앉는다)
  const markLines = (rid) => (B ? B.lines.filter((l) => (l.obsIds || []).includes(rid)).map((l) => ({ k: l.k, pts: l.pts, ids: l.obsIds.slice(),
    ends: [l.a, l.b].map((k) => B.boxes.get(k)).filter(Boolean).map((b) => ({ x: b.x, y: b.y, w: b.w, h: b.h })) })) : [])
  // 두 자리(markAt · markLines)에 없을 때의 자리(§17.23 W4 개정) — 없으면 null(= 못 그린 변화)
  //   선을 긋지 않는 관계 = 출발 → 도착 순으로 자리가 있는 끝(목록 자원 끝은 그 VPC 가 마지막) · 알약만이라 늘 inside
  //   목록 자원(§13-55 — 엔진 id `<종류>/<계정>/<리전>/<id>`) = 그 VPC 상자 · VPC 피어링 = 그 피어링 선(선 = 그 피어링 하나)
  const listHome = (id) => {
    const [t, acct, rg, nat] = String(id).split('/')
    if (!nat) return null
    for (const [vid, rs] of M.list) {
      if (String(vid).split('/')[1] !== acct) continue
      if (rs.some((r) => r.type === t && r.id === nat && r.region === rg)) { const a = markAt(vid); return a && { ...a, inside: true } }
    }
    return null
  }
  function markSeat (id) {
    if (!B) return null
    const s = String(id)
    if (s.includes('|')) {
      const ends = s.split('|').slice(1, 3)
      for (const f of [markAt, listHome]) for (const e of ends) { const a = f(e); if (a) return { at: { ...a, inside: true }, end: e } }
      return null
    }
    const pl = B.lines.filter((l) => l.pcx === s)
    if (pl.length) return { lines: pl.map((l) => ({ k: l.k, pts: l.pts, ids: [s], ends: [l.a, l.b].map((k) => B.boxes.get(k)).filter(Boolean).map((b) => ({ x: b.x, y: b.y, w: b.w, h: b.h })) })) }
    const a = listHome(s); return a ? { at: a } : null
  }

  // 검색 흐림(§17.21) — flow.js 가 판정한 집합(`sk` = `filterSet`, 필터가 남길 것)을 그대로 쓴다. `hits` = 직접 맞은 자원(맞은 수)
  //   아이콘·포트·서브넷 = 묶인 자원 중 하나라도 남으면 제 밝기 · 선 = 두 끝 중 하나라도 흐리면 흐림 · 틀 = 제 밝기인 것을 품지 않으면 흐림
  //   🔴 남긴 것에 `aw-skeep` — 클릭 흐림(CSS `aw-sel`)이 그것을 건너뛴다 = 검색이 이긴다(§17.18-3). 다시 그리지 않는다
  let NMATCH = 0; let SHITS = null   // SHITS = 지금 검색어에 직접 맞은 자원(묶음 정보창이 맞은 멤버만 남길 때 쓴다 · §17.11)
  function applyDim (L, sk, hits) {
    if (!X || !B) return 0
    const on = !!sk; SHITS = on && hits ? hits : null; const dim = new Map(); const keep = []; let n = 0
    const mem = (k) => B.cellIds.get(k) || [k]
    for (const el of X.svgEl.querySelectorAll('.aw-ic,.aw-pt,.aw-sn')) {
      const k = el.dataset.k; const ms = mem(k)
      const kept = on && ms.some((m) => sk.has(m))
      el.classList.toggle('aw-sdim', on && !kept); el.classList.toggle('aw-skeep', kept)
      dim.set(k, on && !kept); if (kept) keep.push(k)
      if (on && hits && ms.some((m) => hits.has(m))) n++
    }
    const fr = on ? new Set(ctxFrames(keep)) : null
    for (const f of B.frames) {
      if (f.kind === 'subnet') continue   // 서브넷은 위에서 자기 구성원으로 판정했다
      const kept = on && (fr.has(f.id) || sk.has(f.id))
      for (const e of byF(f.id)) { e.classList.toggle(f.kind === 'vpc' ? 'aw-sdimv' : 'aw-sdim', on && !kept); e.classList.toggle('aw-skeep', kept) }
      if (!dim.has(f.id)) dim.set(f.id, on && !kept)   // 피어링 선은 VPC 끼리 잇는다 → 선보다 틀을 먼저 정한다
      if (f.kind === 'vpc' && on && hits && hits.has(f.id)) n++   // VPC 이름표(`.aw-vt`)는 VPC 틀 안이라 같이 흐린다
    }
    for (const el of X.svgEl.querySelectorAll('.aw-ln')) {
      const d = on && (!!dim.get(el.dataset.a) || !!dim.get(el.dataset.b))
      el.classList.toggle('aw-sdim', d); el.classList.toggle('aw-skeep', on && !d)
    }
    NMATCH = n
    return n
  }
  // 비교 칸 「생김」 시각의 흐림(§3j J4 개정 · 사용자 결정 2026-10-14 QA B P8) — 남길 자리(상자 키)·선(선 키)만 제 밝기,
  //   나머지 아이콘·포트·선·상태 층은 흐린다. 틀(계정·리전·VPC·AZ·서브넷)은 남긴 자리를 품거나 그 자신이 남길 자리면 제 밝기(검색 흐림과 같은 규칙).
  //   🔴 클래스는 검색 흐림(`aw-sdim`)과 따로다 — 다시 그린 뒤 검색 흐림이 이것을 지우지 않게. 돌려준 값 = 흐린 수(하네스용)
  function keepOnly (keys, lines) {
    if (!X || !B) return null
    const ks = new Set(keys); const ls = new Set(lines); const n = { ic: 0, ln: 0, fr: 0, ob: 0 }
    for (const el of X.svgEl.querySelectorAll('#vp .aw-ic,#vp .aw-pt')) { const d = !ks.has(el.dataset.k); el.classList.toggle('aw-cdim', d); if (d) n.ic++ }
    const fr = new Set(ctxFrames(ks))
    for (const f of B.frames) {
      const d = !(fr.has(f.id) || ks.has(f.id))
      for (const e of byF(f.id)) e.classList.toggle(f.kind === 'vpc' ? 'aw-cdimv' : 'aw-cdim', d)
      if (d) n.fr++
    }
    for (const el of X.svgEl.querySelectorAll('#vp .aw-ln')) { const d = !ls.has(el.dataset.k); el.classList.toggle('aw-cdim', d); if (d) n.ln++ }
    for (const el of X.svgEl.querySelectorAll('#aw-obs [data-k]')) { const d = !(ks.has(el.dataset.k) || ls.has(el.dataset.k)); el.classList.toggle('aw-cdim', d); if (d) n.ob++ }
    return n
  }
  const FIT_TOP = 48
  function fit (L, ctx) {
    const r = ctx.svgEl.getBoundingClientRect(); const S = ctx.S
    // 위 여백 48px = 사용률 막대(§17.25 · 그리기 영역 왼쪽 위) 자리 — 막대가 첫 계정 이름을 덮지 않게 늘 비운다(§17 「첫 화면」 · 검사 24·140)
    const all = Math.min((r.width - 32) / L.canvasW, (r.height - FIT_TOP - 16) / L.canvasH, 1.4)
    if (all >= 0.35) { S.zoom = all; S.tx = (r.width - L.canvasW * S.zoom) / 2; S.ty = FIT_TOP + (r.height - FIT_TOP - 16 - L.canvasH * S.zoom) / 2 } else { S.zoom = 0.35; S.tx = 16; S.ty = FIT_TOP }
    ctx.applyTransform()
  }
  // 칸·서브넷을 켜고 끌 때: 화면 가운데에 가장 가까운 VPC 를 같은 화면 자리에 둔다(배율 그대로, 검사 123)
  function anchor () {
    if (!X || !SC) return null
    const r = X.svgEl.getBoundingClientRect(); const S = X.S
    const cx = (r.width / 2 - S.tx) / S.zoom; const cy = (r.height / 2 - S.ty) / S.zoom
    let best = null
    for (const [id, b] of B.boxes) {
      if (b.kind !== 'aws-vpc') continue
      const dx = Math.max(b.x - cx, 0, cx - b.x - b.w); const dy = Math.max(b.y - cy, 0, cy - b.y - b.h)
      const d = Math.hypot(dx, dy) * 1e6 + Math.hypot(b.x + b.w / 2 - cx, b.y + b.h / 2 - cy)
      if (!best || d < best.d) best = { d, v: id, x: b.x, y: b.y }
    }
    return best
  }
  function restore (a) {
    if (!a || !X || !B) return false
    const b = B.boxes.get(a.v); if (!b) return false
    X.S.tx += (a.x - b.x) * X.S.zoom; X.S.ty += (a.y - b.y) * X.S.zoom
    X.applyTransform(); return true
  }
  function setOpt (az, sn) {
    if (!X) return
    const a = anchor(); closeAll()
    X.S.awsAz = !!az; X.S.awsSn = !!sn
    X.relayout(); restore(a)
    if (X.onOpt) X.onOpt()   // 열린 이력 비교 칸이 같은 체크박스로 다시 열린다(§17.23)
  }

  // ======================================================================== 길 강조
  const SYM = { peer: 1, tgwpeer: 1 }
  function path (starts) {
    const out = new Map(); const inn = new Map()
    for (const l of B.lines) { push(out, l.a, [l.b, l.k]); push(inn, l.b, [l.a, l.k]); if (SYM[l.kind]) { push(out, l.b, [l.a, l.k]); push(inn, l.a, [l.b, l.k]) } }
    // 피어링 포트 ↔ 점선 ↔ 상대 쪽(§17.18-11). 상대가 VPC 틀이면 거기서 **멈춘다**(셋째 칸) — VPC 피어링은 이어 타지 않는다(A↔B·B↔C 가 있어도
    //   A 의 서브넷 길이 C 로 번지면 틀린 말이다). 상대 쪽 포트는 들어오는 라우트 선만 있어 멈출 필요가 없다
    for (const l of B.lines) {
      if (l.kind !== 'peer' || !l.pcx) continue
      const end = (id) => { const k = M.ppOf.get(id + '|' + l.pcx); return k && B.icon.has(k) ? k : null }
      const ea = end(l.a); const eb = end(l.b); if (!ea && !eb) continue
      const A = ea || l.a; const Bb = eb || l.b
      push(out, A, [Bb, l.k, !eb]); push(inn, Bb, [A, l.k, !ea]); push(out, Bb, [A, l.k, !ea]); push(inn, A, [Bb, l.k, !eb])
    }
    // NAT 은 자기 서브넷의 라우트로 나간다 → 밖으로 가는 길이 NAT 에 닿으면 그 서브넷에서 이어 간다
    const natSub = new Map(); for (const [k, kd] of B.cellKind) if (kd === 'nat') { const it = M.items.get(k); if (it) natSub.set(k, it.subnets) }
    const nodes = new Set(starts); const lines = new Set()
    for (const m of [out, inn]) {
      const q = [...starts]; const seen = new Set(starts)
      while (q.length) {
        const x = q.shift(); const nx = [...(m.get(x) || [])]
        if (m === out && natSub.has(x)) for (const s of natSub.get(x)) nx.push([s, null])
        // 서브넷을 끄면 NAT 이 있는 서브넷의 선은 그 서브넷 자원에서 나간다 — 같은 서브넷 자원도 이어 준다
        if (m === out && !O.sn && M.subnets.has(x)) for (const [k] of B.cellKind) { const ids = B.cellIds.get(k) || []; if (ids.some((id) => (M.items.get(id) || {}).subnets && M.items.get(id).subnets.length === 1 && M.items.get(id).subnets[0] === x)) nx.push([k, null]) }
        for (const [y, l, stop] of nx) { if (l) lines.add(l); nodes.add(y); if (!seen.has(y)) { seen.add(y); if (!stop) q.push(y) } }
      }
    }
    return { nodes, lines }
  }
  const $$ = (s) => [...X.svgEl.querySelectorAll(s)]
  // 🔴 키 → 요소 색인은 그린 장면마다 한 번 만든다. 강조 노드마다 전체 DOM 을 훑으면 30계정에서 클릭 한 번이
  // 3.7초 멈춘다(노드 1,080 + 선 1,109 + 틀 1,590 → 약 3,800번 훑기 · #45 리뷰). 장면이 바뀌었는지는 `#vp` 로 본다.
  let IX = null
  const index = () => {
    const vp = X.svgEl.querySelector('#vp')
    if (IX && IX.vp === vp) return IX
    IX = { vp, k: new Map(), f: new Map() }
    const put = (m, key, e) => { if (key == null) return; const a = m.get(key); if (a) a.push(e); else m.set(key, [e]) }
    for (const e of X.svgEl.querySelectorAll('[data-k],.aw-vpc[data-v],[data-f]')) {
      if (e.hasAttribute('data-k')) put(IX.k, e.dataset.k, e)
      if (e.classList.contains('aw-vpc') && e.hasAttribute('data-v')) put(IX.k, e.dataset.v, e)
      // 틀 = `data-f` · 서브넷 · VPC(ctxFrames 가 돌려주는 id 의 세 모양)
      if (e.hasAttribute('data-f')) put(IX.f, e.dataset.f, e)
      if (e.classList.contains('aw-sn') && e.hasAttribute('data-k')) put(IX.f, e.dataset.k, e)
      if (e.classList.contains('aw-vpc') && e.hasAttribute('data-v')) put(IX.f, e.dataset.v, e)
    }
    return IX
  }
  const byK = (k) => index().k.get(k) || []
  const byF = (k) => index().f.get(k) || []
  function unhl () { if (!X) return; X.svgEl.classList.remove('aw-sel'); for (const e of $$('.aw-hl,.aw-ctx')) e.classList.remove('aw-hl', 'aw-ctx'); HL = null }
  // 고른 길 밖은 틀까지 흐린다 — 길 위의 것을 품은 틀만 제 밝기(§17.8)
  function ctxFrames (keys) {
    const C = (r) => [r.x + r.w / 2, r.y + r.h / 2]
    const pts = [...keys].map((k) => B.rect.get(k)).filter(Boolean).map(C)
    const hit = (b) => pts.some(([x, y]) => x >= b.x - 2 && x <= b.x + b.w + 2 && y >= b.y - 2 && y <= b.y + b.h + 2)
    const out = []
    for (const f of B.frames) if (hit(f)) out.push(f.id)
    return out
  }
  function mark (nodes, lines) {
    X.svgEl.classList.add('aw-sel')
    for (const n of nodes) for (const e of byK(n)) e.classList.add('aw-hl')
    for (const l of lines) for (const e of byK(l)) e.classList.add('aw-hl')
    const fr = ctxFrames(nodes)
    for (const id of fr) for (const e of byF(id)) e.classList.add('aw-ctx')
    HL = { nodes: [...nodes], lines: [...lines], frames: fr }
  }

  // ======================================================================== 정보창
  const card = () => document.getElementById('awscard')
  function closeCard () {
    const c = card(); if (c) c.style.display = 'none'
    anchorPt = null; cardKey = null
    if (openV && X) { for (const t of $$(`.aw-vt[data-v="${CSS.escape(openV)}"] .aw-tri`)) t.textContent = '▸'; for (const g of $$(`.aw-vpc[data-v="${CSS.escape(openV)}"]`)) g.classList.remove('aw-open'); openV = null }
  }
  function closeAll () { const was = !!(cardKey || HL); unhl(); closeCard(); return was }
  function place () {
    const c = card(); if (!c || !anchorPt || c.style.display !== 'flex' || !X) return
    const r = X.svgEl.getBoundingClientRect(); const st = X.stage.getBoundingClientRect(); const S = X.S
    const sx = r.left - st.left + S.tx + anchorPt.x * S.zoom; const sy = r.top - st.top + S.ty + anchorPt.y * S.zoom
    const cw = c.offsetWidth || 360; const ch = c.offsetHeight || 200
    c.style.left = Math.max(6, Math.min(sx - 18, st.width - cw - 6)) + 'px'
    c.style.top = Math.max(6, Math.min(sy + 6, st.height - ch - 6)) + 'px'
  }
  function setOp () {
    const c = card(); if (!c) return
    const OPV = X.S.awsOp || 0; const a = 1 - OPV / 100
    const body = c.querySelector('.aw-body'); if (body) body.style.opacity = a
    c.style.background = `rgba(22,27,34,${a})`; c.style.borderColor = `rgba(72,79,88,${a})`; c.style.boxShadow = a > 0 ? `0 10px 30px rgba(0,0,0,${0.5 * a})` : 'none'
    // 90% 이상이면 카드 위를 눌러도 뒤 그림이 눌린다(투명도 줄은 그대로 잡힌다)
    c.style.pointerEvents = OPV >= 90 ? 'none' : 'auto'
    const op = c.querySelector('.aw-op'); if (op) { op.style.pointerEvents = 'auto'; op.querySelector('b').textContent = OPV + '%' }
  }
  function expand (tks) {
    const out = []
    for (const k of tks) {
      if (byK(k).length) { out.push(k); continue }
      const ck = B.cellOf.get(k) || (M.hidden.get(k) && (B.cellOf.get(M.hidden.get(k)) || M.hidden.get(k)))
      if (ck && byK(ck).length) out.push(ck)
    }
    return out
  }
  // 선이 대표하는 자원(VPC 피어링 · TGW 피어링 어태치먼트)은 그 선과 두 끝을 강조한다
  function expandL (tks) {
    const nodes = expand(tks); const lines = []
    for (const l of B.lines) if (l.rep && l.rep.some((r) => tks.includes(r))) { lines.push(l.k); for (const e of [l.a, l.b]) if (!nodes.includes(e)) nodes.push(e) }
    return { nodes, lines }
  }
  // 글자 속 ID·CIDR 은 중간에서 끊지 않는다(§17.11 「콘솔 표현 원칙」 — `vpn-07922…` 가 하이픈에서 꺾여 두 줄로 갈라지던 결함) —
  //   그 낱말만 한 줄 묶음(`.aw-nb`)으로 감싸고 나머지는 글자 그대로(줄바꿈·textContent 는 바뀌지 않는다 — 검사들이 읽는 글자 = 화면 글자)
  const NB_RE = /(?:[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*-[0-9a-f]{8,}\b|\d{1,3}(?:\.\d{1,3}){3}\/\d{1,2}|[0-9a-f:]+::\/\d{1,3}|[a-z0-9]+\.cloudfront\.net)/g
  // 36자 UUID(DX 게이트웨이 ID)만 하이픈 뒤에서 꺾일 자리(`<wbr>` — 글자는 그대로)를 둔다(🔁 2026-10-26): 좁은 표 칸(어태치먼트 「리소스 ID」)에서
  //   한 줄로 두면 칸을 넘어 옆 칸 위로 겹치거나 표가 가로로 넘친다(사용자 스크린샷) — 겹침보다 두 줄이 낫다 · 짧은 ID(`vpc-…`·`tgw-attach-…`)는 그대로 끊지 않는다
  const UUID_RE = /\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/g
  const uuidText = (el, u) => u.split('-').forEach((p, k, all) => { el.appendChild(document.createTextNode(p + (k < all.length - 1 ? '-' : ''))); if (k < all.length - 1) el.appendChild(document.createElement('wbr')) })
  const nbText = (el, t) => {
    let i = 0
    for (const m of t.matchAll(NB_RE)) {
      if (m.index > i) el.appendChild(document.createTextNode(t.slice(i, m.index)))
      const sp = document.createElement('span'); sp.className = 'aw-nb'; sp.textContent = m[0]; el.appendChild(sp); i = m.index + m[0].length
    }
    if (i < t.length) el.appendChild(document.createTextNode(t.slice(i)))
  }
  function idText (el, text) {
    const t = String(text ?? ''); let j = 0
    if (!t) { el.appendChild(document.createTextNode('')); return }
    // UUID 먼저(NB_RE 가 UUID 가운데의 `d0e0f00-…` 를 한 줄 묶음으로 잡지 않게)
    for (const m of t.matchAll(UUID_RE)) { if (m.index > j) nbText(el, t.slice(j, m.index)); uuidText(el, m[0]); j = m.index + m[0].length }
    if (j < t.length) nbText(el, t.slice(j))
  }
  // 줄 하나를 누르면 그 자원 강조(옛 줄 · 콘솔식 표 행이 같은 길) — 도식에 없는 대상(못 그린 것)은 누를 수 있는 척하지 않는다
  function bindTk (e, tk) {
    if (tk.length && expandL(tk).nodes.length) { e.dataset.tk = tk.join(' ')
      e.onclick = (ev) => { ev.stopPropagation(); unhl(); const { nodes, lines } = expandL(tk); if (nodes.length) mark(nodes, lines) } }
  }
  // 콘솔식 표(§17.11 「콘솔 표현 원칙」) — 머리줄 + 행 · 열 폭 고정(행끼리 줄이 맞는다) · wrap < 열 수 = 두 줄 행(머리줄도 같은 모양)
  const tabLab = (t) => t.label + (t.nc ? ' — 수집 안 함' : t.ur ? ' — 읽지 못함' : ` (${t.n ?? '?'})`)
  function tblEl (t) {
    const w = document.createElement('div'); w.className = 'aw-tbl'; w.dataset.tbl = t.label || ''
    if (t.nc || t.ur || !t.rows.length) { const e = document.createElement('div'); e.className = 'aw-tmt' + (t.ur ? ' aw-wn' : ''); e.textContent = t.empty; w.appendChild(e); return w }
    const lines = t.wrap < t.cols.length ? [[0, t.wrap], [t.wrap, t.cols.length]] : [[0, t.cols.length]]
    const fillLine = (el, i0, i1, text, isHead, r) => {
      for (let i = i0; i < i1; i++) {
        const c = t.cols[i]; const sp = document.createElement('span'); sp.style.width = c.w + 'px'
        const v = isHead ? c.h : (r.c[i] === undefined || r.c[i] === null || r.c[i] === '' ? NIL : String(r.c[i]))
        // ID 칸 = ID 낱말만 한 줄 묶음(`idText`) · 뒤의 「(이름)」은 칸이 모자라면 다음 줄로 — 옛 칸 전체 `nowrap` 은 긴 ID + 이름이 옆 칸 위로 넘쳐 겹쳤다
        //   (사용자 스크린샷 2026-10-26 · DX 연결 「DX 게이트웨이 연결」 `a4fc51ee-… (NWRA_DXGW)` 가 「거쳐 가는 가상 인터페이스」를 덮음)
        if (c.id && !isHead) idText(sp, v); else sp.textContent = v
        const cl = [c.id ? 'aw-c-id' : '', c.any ? 'aw-c-any' : '', i === i1 - 1 ? 'aw-c-last' : '', !isHead && c.st && stCls(v) ? 'aw-st-' + stCls(v) : ''].filter(Boolean)
        if (cl.length) sp.className = cl.join(' ')
        if (!isHead && c.st && r.why) sp.title = r.why
        el.appendChild(sp)
      }
    }
    const build = (isHead, r) => {
      const e = document.createElement('div')
      lines.forEach(([i0, i1], li) => { const l = document.createElement('div'); l.className = li ? 'aw-l2' : 'aw-l1'; fillLine(l, i0, i1, null, isHead, r); e.appendChild(l) })
      return e
    }
    const hd = build(true); hd.className = 'aw-gh'; w.appendChild(hd)
    for (const r of t.rows) {
      const e = build(false, r); const tk = r.tk || []
      e.className = 'aw-r aw-g' + (r.cls ? ' aw-' + r.cls : '') + (tk.length ? '' : ' aw-nk'); bindTk(e, tk)
      // 누르면 다른 정보창(`go` — 예: DX 연결의 VIF 줄 → 그 VIF 정보창 · 맨 위 「← 돌아가기」) · 길 강조는 `tk` 그대로
      if (r.go && (B.det.has(r.go) || B.mem.has(r.go))) {
        e.classList.add('aw-go'); e.classList.remove('aw-nk'); e.tabIndex = 0; e.setAttribute('role', 'button'); e.dataset.go = r.go
        const run = (ev) => { ev.stopPropagation(); if (tk.length) { unhl(); const { nodes, lines } = expandL(tk); if (nodes.length) mark(nodes, lines) } const from = cardKey; showCard(r.go, anchorPt, from); if (X.onSel) X.onSel(r.go) }
        e.onclick = run; e.onkeydown = (ev) => { if (ev.key === 'Enter') run(ev) }
      }
      w.appendChild(e)
    }
    return w
  }
  // 칸 안의 작은 탭(콘솔 탭 순서) — 첫 탭이 열린 채로 시작한다(콘솔 상세 화면이 첫 탭을 열고 시작하는 것과 같다)
  function tabsEl (tabs) {
    const w = document.createElement('div')
    const bar = document.createElement('div'); bar.className = 'aw-tabs'; bar.setAttribute('role', 'tablist'); w.appendChild(bar)
    const panes = tabs.map((t, i) => {
      const b = document.createElement('button'); b.type = 'button'; b.className = 'aw-tab'; b.setAttribute('role', 'tab'); b.dataset.tab = t.label
      b.textContent = tabLab(t); b.setAttribute('aria-selected', i === 0 ? 'true' : 'false'); bar.appendChild(b)
      const pn = document.createElement('div'); pn.className = 'aw-tpane'; pn.setAttribute('role', 'tabpanel'); pn.dataset.tab = t.label; pn.hidden = i !== 0
      pn.appendChild(tblEl(t)); w.appendChild(pn)
      b.onclick = (ev) => { ev.stopPropagation(); for (const x of bar.children) x.setAttribute('aria-selected', x === b ? 'true' : 'false'); for (const x of panes) x.hidden = x !== pn }
      return pn
    })
    return w
  }
  // 칸 하나(접는 단추 + 몸통). 단추 글자 = 콘솔 탭 꼴 「<이름> (N)」(§17.11 「콘솔 표현 원칙」 — 옛 「N개」)
  function secEl (s) {
    const b = document.createElement('div'); b.className = 'aw-rtb'
    const box = document.createElement('div'); box.className = 'aw-rtl'; box.style.display = 'none'
    const lab = () => (box.style.display === 'none' ? '▸ ' : '▾ ') + s.label + (s.nc ? ' — 수집 안 함' : s.ur ? ' — 읽지 못함' : ` (${s.n ?? '?'})`)
    b.textContent = lab(); b.dataset.sec = s.label
    b.onclick = (e) => { e.stopPropagation(); box.style.display = box.style.display === 'none' ? 'block' : 'none'; b.textContent = lab() }
    if (!s.groups.length) { const e = document.createElement('div'); e.className = 'aw-mt'; e.textContent = s.empty || '(없음)'; box.appendChild(e) }
    for (const g of s.groups) {
      if (g.h) {
        const h4 = document.createElement('h4'); if (g.hcls) h4.className = 'aw-' + g.hcls; idText(h4, g.h)
        if (g.cu) { const a = document.createElement('a'); a.className = 'aw-clink aw-clink-s'; a.href = g.cu; a.target = '_blank'; a.rel = 'noopener noreferrer'; a.textContent = '콘솔 ↗'; a.onclick = (ev) => ev.stopPropagation(); h4.append(' ', a) }
        box.appendChild(h4)
      }
      // 세부 정보 한 줄(콘솔 「세부 정보」 상자) — 「이름 값 · 이름 값」
      if (g.dl && g.dl.length) { const e = document.createElement('div'); e.className = 'aw-gd'; e.textContent = g.dl.map(([k, v]) => `${k} ${v}`).join(' · '); box.appendChild(e) }
      if (g.tabs) box.appendChild(tabsEl(g.tabs))
      else if (g.tbl) box.appendChild(tblEl(g.tbl))
      for (const r of g.rows || []) {
        const e = document.createElement('div'); const tk = r.tk || []
        e.className = 'aw-r' + (r.t !== undefined ? ' aw-t' : '') + (r.cls ? ' aw-' + r.cls : '') + (tk.length ? '' : ' aw-nk')
        if (r.t !== undefined) { const sp = document.createElement('span'); sp.textContent = r.t; e.append(sp) } else {
          const a = document.createElement('span'); idText(a, r.a ?? ''); const c = document.createElement('span'); idText(c, r.b ?? ''); e.append(a, c)
        }
        bindTk(e, tk)
        box.appendChild(e)
      }
    }
    return [b, box]
  }
  function det (k) { const f = B && (B.det.get(k) || (B.mem.get(k) || {}).det); return f ? f() : null }
  // 묶음 정보창의 「포함된 리소스 N개」(§17.11 · 사용자 결정 2026-10-23 · 검사 162) — 줄을 누르면 그 낱개의 정보창(맨 위 「← 목록으로」).
  //   🔴 도식은 그대로다 — 묶음을 펼치지 않고 강조도 묶음 아이콘에 남는다(정보창 안에서만 오간다). 마우스를 올려 보여 주는 말풍선은 두지 않는다(사용자 결정)
  //   검색어가 있으면(§17.21) 맞은 멤버만 남긴다(「전체 보기」로 푼다) · 10개를 넘으면 이름 찾기 칸
  const MEM_FIND = 10
  let memAll = null; let memQ = ''   // 「전체 보기」를 누른 묶음 · 찾기 칸 글자 — 낱개에서 돌아왔을 때 그대로
  function memList (k, d) {
    const m = document.createElement('div'); m.className = 'aw-mem'; m.dataset.g = k
    const hd = document.createElement('b'); hd.textContent = '포함된 리소스 ' + d.members.length + '개'; m.appendChild(hd)
    const hit = SHITS && memAll !== k ? d.members.filter((x) => SHITS.has(x.key)) : []
    const list = hit.length && hit.length < d.members.length ? hit : d.members
    if (list !== d.members) {
      const n = document.createElement('div'); n.className = 'aw-memn'; n.textContent = `검색어와 일치하는 ${list.length}개만 보여 줍니다 `
      const all = document.createElement('button'); all.type = 'button'; all.className = 'aw-mema'; all.textContent = `전체 ${d.members.length}개 보기`
      all.onclick = (ev) => { ev.stopPropagation(); memAll = k; fill(k, cardBack); place() }
      n.appendChild(all); m.appendChild(n)
    }
    const rows = []
    for (const x of list) {
      const can = !!(B.mem.get(x.key) && B.mem.get(x.key).g === k)
      const e = document.createElement(can ? 'button' : 'div'); e.textContent = x.name
      if (can) { e.type = 'button'; e.className = 'aw-mr'; e.dataset.mk = x.key; e.onclick = (ev) => { ev.stopPropagation(); memOpen(k, x.key) } }
      const s = document.createElement('small'); s.textContent = x.sub; e.appendChild(s); rows.push([e, `${x.name} ${x.sub}`.toLowerCase()])
    }
    if (list.length > MEM_FIND) {
      const q = document.createElement('input'); q.type = 'search'; q.className = 'aw-memq'; q.placeholder = '이름으로 찾기'; q.setAttribute('aria-label', '포함된 리소스를 이름으로 찾기'); q.value = memQ
      const z = document.createElement('div'); z.className = 'aw-memz'; z.textContent = '일치하는 리소스가 없습니다'
      const run = () => { const t = q.value.trim().toLowerCase(); let any = false; for (const [e, txt] of rows) { const on = !t || txt.includes(t); e.hidden = !on; any = any || on } z.hidden = any }
      q.oninput = () => { memQ = q.value; run() }
      m.appendChild(q); for (const [e] of rows) m.appendChild(e); m.appendChild(z); run()
    } else for (const [e] of rows) m.appendChild(e)
    return m
  }
  function memOpen (g, mk) {
    showCard(mk, anchorPt, g); if (X.onSel) X.onSel(mk)
    const b = card().querySelector('.aw-back'); if (b) b.focus({ preventScroll: true })
  }
  function memBack () {
    const g = cardBack; const from = cardKey; if (!g) return
    showCard(g, anchorPt); if (X.onSel) X.onSel(g)
    const r = card().querySelector(`.aw-mr[data-mk="${CSS.escape(from)}"]`); if (r && !r.hidden) r.focus({ preventScroll: true })
  }
  function fill (k, back) {
    const c = card(); const d = det(k)
    c.innerHTML = ''
    const body = document.createElement('div'); body.className = 'aw-body'; c.appendChild(body)
    if (back) {
      const bt = document.createElement('button'); bt.type = 'button'; bt.className = 'aw-back'; bt.dataset.back = back
      const bd = det(back) || {}
      // 묶음 목록에서 왔으면 「목록으로」 · 다른 정보창의 표 줄(`go`)에서 왔으면 「돌아가기」(예: VIF → 그 DX 연결)
      bt.textContent = bd.members ? `← ${bd.title || '묶음'} 목록으로` : `← ${bd.title || '이전'} 정보창으로 돌아가기`; bt.onclick = (ev) => { ev.stopPropagation(); memBack() }
      body.appendChild(bt)
    }
    const h = document.createElement('h3'); h.innerHTML = '<span class="aw-tri">▾</span><span></span>'; h.lastChild.textContent = (d && d.title) || '(정보 없음)'; body.appendChild(h)
    if (d) {
      const sb = document.createElement('div'); sb.className = 'aw-sub'; sb.textContent = d.sub || ''; body.appendChild(sb)
      // 대장 58 — 이 자원 하나를 가리키는 카드에만 붙인다(묶음 아이콘·그린 칸·유령은 가리킬 대상이 하나가 아니다).
      //   새 탭으로 연다(`_blank`+`noopener`) — 이 화면을 떠나지 않는다. 불변식 ⑥ 의 「요청 0」은 **누르기 전**의 약속이다.
      const cn = X.mById ? X.mById.get(k) : null
      const cu = cn && X.consoleUrl ? X.consoleUrl(cn) : null
      if (cu) {
        const a = document.createElement('a'); a.className = 'aw-clink'; a.href = cu
        a.target = '_blank'; a.rel = 'noopener noreferrer'; a.textContent = 'AWS 콘솔에서 열기 ↗'
        a.onclick = (ev) => ev.stopPropagation()
        body.appendChild(a)
        // 다른 계정 소유(예: 575 의 피어링 · Lattice 서비스) — 콘솔은 그 계정으로 로그인해야 열린다(2026-10-24 실콘솔: 188 콘솔에서 「No matching resource」)
        const own = String((X.acctOf && X.acctOf(cn)) || '').replace(/^acct-/, '')
        if (own && M.accts.length > 1) { const sp = document.createElement('span'); sp.className = 'aw-cacct'; sp.textContent = ` 계정 ${X.acctText(own)} 콘솔에서 열립니다`; body.appendChild(sp) }
      }
      const t = document.createElement('table')
      for (const [a, b, cl, go] of d.rows || []) {
        const tr = t.insertRow(); tr.insertCell().textContent = a; const td = tr.insertCell(); idText(td, b ?? ''); if (cl) td.className = 'aw-' + cl
        // 넷째 칸 = 누르면 열 정보창(§17.41 「웹 ACL」 줄 → 그 WAF) — 도식에 그 칸이 있을 때만 누를 수 있다(없으면 글자 그대로)
        if (go && (B.det.has(go) || B.mem.has(go))) {
          td.classList.add('aw-go'); td.tabIndex = 0; td.setAttribute('role', 'button'); td.dataset.go = go
          const run = (ev) => { ev.stopPropagation(); open(go); if (X.onSel) X.onSel(go) }
          td.onclick = run; td.onkeydown = (ev) => { if (ev.key === 'Enter') run(ev) }
        }
      }
      body.appendChild(t)
      if (d.members) body.appendChild(memList(k, d))
      if (d.secs && d.secs.length) { const w = document.createElement('div'); w.className = 'aw-secs'; for (const s of d.secs) w.append(...secEl(s)); body.appendChild(w) }
    }
    // 실시간 모니터링 켜짐이면 맨 아래 「상태」 칸(§17.20) — 줄은 flow.js `obsRows` 가 준다. 꺼짐이면 칸 자체가 없다(§17.11 칸 목록 = 꺼짐 기준)
    const ob = X.obs && X.obs.on() ? obsRowsOf(k) : null
    if (ob && ob.length) {
      const hd = document.createElement('div'); hd.className = 'aw-sech aw-obsh'; hd.textContent = '상태(모니터링)'; body.appendChild(hd)
      const t = document.createElement('table'); t.className = 'aw-obst'
      for (const [a, v, ti, dr] of ob) { const tr = t.insertRow(); if (dr) tr.dataset.row = dr; tr.insertCell().textContent = a; const td = tr.insertCell(); td.textContent = v ?? ''; if (ti) td.title = ti }
      body.appendChild(t)
    }
    const op = document.createElement('div'); op.className = 'aw-op'
    op.innerHTML = '<span>투명도</span><input type="range" min="0" max="100" step="5" aria-label="정보창 투명도"><b></b>'; c.appendChild(op)
    const rg = op.querySelector('input'); rg.value = X.S.awsOp || 0
    rg.oninput = () => { X.S.awsOp = +rg.value; setOp() }
    setOp()
  }
  function obsRowsOf (k) {
    const b = B.boxes.get(k)
    if (b && OBS_CARD.has(b.kind)) return X.obs.rows(b)
    // 묶음 멤버 = 그 낱개 하나의 판정(묶음 칸은 「나쁜 쪽이 이긴다」로 멤버를 다 합친다 — 낱개 정보창에서는 그 멤버의 행만)
    const mm = B.mem.get(k); if (mm && mm.n) return X.obs.rows({ id: k, kind: 'aws-item', node: mm.n })
    const l = B.lines.find((x) => x.k === k)
    // 옛 선과 같은 모양으로 넘긴다 — 「양 끝만 흐른다」(`endsOnly`)는 `kind: 'flow'` 만 본다
    return l && X.obs.lineRows ? X.obs.lineRows(obsLine(l)) : null
  }
  function showCard (k, pt, back) { cardBack = back || null; fill(k, cardBack); anchorPt = pt; cardKey = k; card().style.display = 'flex'; place() }
  const svgPt = (e) => { const r = X.svgEl.getBoundingClientRect(); const S = X.S; return { x: (e.clientX - r.left - S.tx) / S.zoom, y: (e.clientY - r.top - S.ty) / S.zoom } }
  const below = (k) => { const r = B.rect.get(k); return r ? { x: r.x, y: r.y + r.h } : { x: 10, y: 10 } }
  function openVpc (k, pt) {
    showCard(k, pt); openV = k
    for (const t of $$(`.aw-vt[data-v="${CSS.escape(k)}"] .aw-tri`)) t.textContent = '▾'
    for (const g of $$(`.aw-vpc[data-v="${CSS.escape(k)}"]`)) g.classList.add('aw-open')
  }
  function select (k, pt) { // 아이콘·서브넷·포트를 고른다 = 길 강조 + 정보창
    const el = byK(k)[0]
    const starts = [k, ...((el && el.dataset.subs) || '').split(' ').filter(Boolean)]
    const { nodes, lines } = path(starts); mark(nodes, lines)
    memAll = null; memQ = ''
    // 검색어에 맞은 멤버가 하나뿐인 묶음을 누르면 그 낱개 정보창을 바로 연다(§17.11 · 돌아가기 있음) — 돌려준 키 = 연 정보창(주소에 싣는다)
    const ms = SHITS ? (B.cellIds.get(k) || []).filter((m) => SHITS.has(m) && B.mem.has(m) && B.mem.get(m).g === k) : []
    if (ms.length === 1) { showCard(ms[0], pt || below(k), k); return ms[0] }
    showCard(k, pt || below(k)); return k
  }
  function onClick (ev) {
    if (!X) return
    // 🔴 전파를 끊지 않는다 — 끊으면 `document` 버블의 닫기 리스너(≡ 메뉴·범위·모니터링 팝오버 · 이 화면의 범례·설명)가
    // 안 돌아 「밖 클릭으로 닫힌다」(계약 §B2)가 깨진다(#45 리뷰).
    if (downAt && Math.abs(ev.clientX - downAt.x) + Math.abs(ev.clientY - downAt.y) > 4) return
    const vt = ev.target.closest('.aw-vt')
    // 사람이 고른 것만 주소(`#sel=`)에 싣는다(§13-58 · 검사 58) — `open` 은 해시를 읽어 여는 쪽이라 쓰지 않는다
    const sel = (k) => { if (X.onSel) X.onSel(k) }
    if (vt) { const k = vt.dataset.v; const was = openV === k; closeAll(); if (!was) { const r = B.rect.get(k); openVpc(k, { x: r.x + 6, y: r.y + 30 }) } sel(was ? null : k); return }
    const t = ev.target.closest('[data-k]'); closeAll(); if (!t) { sel(null); return }
    const k = t.dataset.k
    if (t.classList.contains('aw-ln')) { mark([t.dataset.a, t.dataset.b], [k]); showCard(k, svgPt(ev)); sel(k); return }
    sel(select(k))
  }
  function open (k) {
    if (!X || !B) return false
    closeAll()
    if (M.vpcs.has(k)) { const r = B.rect.get(k); openVpc(k, { x: r.x + 6, y: r.y + 30 }); return true }
    const l = B.lines.find((x) => x.k === k)
    if (l) { mark([l.a, l.b], [k]); showCard(k, { x: l.pts[0][0], y: l.pts[0][1] }); return true }
    // 묶음 멤버(주소 `#sel=` · 「!」 목록 줄) = 묶음 강조 + 그 낱개 정보창(§17.11 · 검사 162)
    const mm = B.mem.get(k)
    if (mm && !B.det.has(k) && byK(mm.g).length) { select(mm.g); memAll = null; showCard(k, below(mm.g), mm.g); return true }
    if (!B.det.has(k)) return false
    select(k); return true
  }

  // ======================================================================== 범례 · 막대
  // 범례 줄 한 벌 — 화면(#awsleg HTML)과 내보낸 그림(SVG 안 범례, §17.13 · §17.18-2)이 **같은 줄**을 쓴다
  function legendRows () {
    const present = new Set([...B.cellKind.values()]); for (const h of M.hubs.values()) present.add(h.ghost ? 'ghost-tgw' : h.kind); for (const g of M.globs.values()) present.add(g.ghost ? 'ghost' : g.kind)
    if (M.xpeers.length) present.add('xvpc')
    const LEGK = [['inet', '인터넷'], ['igw', 'Internet Gateway'], ['nat', 'NAT Gateway'], ['lb', 'Load Balancer'], ['vpce', 'VPC Endpoint'], ['nfw', 'Network Firewall'], ['ec2', 'EC2'], ['asg', 'Auto Scaling 그룹'], ['ecs', 'ECS'], ['eks', 'EKS'],
      ['sm', 'SageMaker'], ['eic', 'EIC Endpoint (EC2 Instance Connect Endpoint)'], ['rds', 'RDS'], ['tgw', 'Transit Gateway'], ['ghost-tgw', 'Transit Gateway · 다른 계정'],
      ['dxcon', 'Direct Connect'], ['dxgw', 'DX Gateway (Direct Connect Gateway)'], ['ghost', 'DX Gateway · 읽지 못함'], ['vgw', 'Virtual Private Gateway (VGW)'], ['vpn', 'Site-to-Site VPN 연결'], ['cgw', 'Customer Gateway'], ['cf', 'CloudFront'], ['xvpc', 'VPC · 수집 범위 밖'],
      // #114(§17.32) — 범례 = 서비스 이름만(PR #118 · 손그림 메모는 개발 문서 몫)
      ['lambda', 'Lambda'], ['r53', 'Route 53 호스팅 영역'], ['s3', 'S3 버킷'],
      ['ec', 'ElastiCache'], ['rdsc', 'Aurora · DB 클러스터'], ['os', 'OpenSearch'], ['msk', 'MSK(Kafka)'], ['brk', 'Bedrock Endpoint'],
      // 배치 D(2.10.0 · §17.35~37)
      ['apigw', 'API Gateway'], ['rdsp', 'RDS Proxy'], ['ga', 'Global Accelerator'],
      // #129(2.11.0 · §17.39~40)
      ['cwan', 'Cloud WAN'], ['lsn', 'VPC Lattice 서비스 네트워크'], ['lsvc', 'VPC Lattice 서비스'],
      // 2.12.0(§17.41)
      ['waf', 'WAF 웹 ACL']].filter(([k]) => present.has(k))
    const mini = (k, sub) => `<svg width="24" height="24" viewBox="-20 -20 40 40">${k === 'ghost' ? glyph('dxgw', 0, 0, COL.ghost, 'ghost') : k === 'ghost-tgw' ? glyph('tgw', 0, 0, COL.ghost, 'ghost') : glyph(k, 0, 0, COL[k], false, sub)}</svg>`
    const lbTypes = [...new Set([...M.items.values()].filter((x) => x.kind === 'lb').map((x) => x.lbType))].sort()
    const box = (st, tile = '') => `<svg width="34" height="22" viewBox="0 0 34 22"><rect x="1" y="1" width="32" height="20" rx="2" ${st}/>${tile}</svg>`
    return [{ h: '영역' },
      { ic: [box('fill="none" stroke="#8b949e" stroke-width="1.4"')], t: '계정' },
      { ic: [box('fill="none" stroke="#6e7681" stroke-width="1.2" stroke-dasharray="6 4"')], t: '리전 · 전역' },
      { ic: [box(`fill="#10151c" stroke="${COL.vpc}" stroke-width="1.4"`, TILE.vpc(4, 3, COL.vpc).replace(/width="22" height="22"/, 'width="16" height="16"'))], t: 'VPC' },
      { ic: [box('fill="none" stroke="#484f58" stroke-width="1.1" stroke-dasharray="5 4"')], t: '가용 영역(AZ)' },
      { ic: [box(`fill="${SNF.pub}" stroke="${COL.pub}"`, TILE.globe(3, 2.5))], t: '퍼블릭 서브넷 (지구본)' },
      { ic: [box(`fill="${SNF.priv}" stroke="${COL.priv}"`, TILE.lock(3, 2.5))], t: '프라이빗 서브넷 (자물쇠)' },
      { h: '서비스 (같은 서비스는 같은 색 · 계정과 무관)' },
      ...LEGK.map(([k, n]) => (k === 'lb' ? { kind: 'lb', ic: lbTypes.map((t) => mini('lb', t)), t: `${lbTypes.join(' · ')} (로드 밸런서)` } : { kind: k, ic: [mini(k)], t: n })),
      ...([...M.ports.values()].some((p) => !p.vgw && !p.cwan && !p.lsn) ? [{ ic: [`<svg width="24" height="24" viewBox="-12 -12 24 24">${portSvg(0, 0)}</svg>`], t: 'VPC 테두리의 작은 원 = TGW 연결 지점' }] : []),
      ...([...M.ports.values()].some((p) => p.cwan) ? [{ kind: 'cwanport', ic: [`<svg width="24" height="24" viewBox="-12 -12 24 24">${portSvg(0, 0, COL.cwan)}</svg>`], t: 'VPC 테두리의 민트색 원 = Cloud WAN 연결 지점' }] : []),
      ...([...M.ports.values()].some((p) => p.lsn) ? [{ kind: 'lsnport', ic: [`<svg width="24" height="24" viewBox="-12 -12 24 24">${portSvg(0, 0, COL.lsn)}</svg>`], t: 'VPC 테두리의 장미색 원 = VPC Lattice 서비스 네트워크 연결 지점' }] : []),
      ...([...M.ports.values()].some((p) => p.vgw) ? [{ kind: 'vgwport', ic: [`<svg width="24" height="24" viewBox="-12 -12 24 24">${portSvg(0, 0, COL.vgw)}</svg>`], t: 'VPC 테두리의 하늘색 원 = VGW 연결 지점' }] : []),
      ...(B.lines.some((l) => l.sub && M.pp.has(l.b)) ? [{ ic: [`<svg width="24" height="24" viewBox="-12 -12 24 24">${ppSvg(0, 0)}</svg>`], t: 'VPC 아래쪽 변의 작은 원 = VPC 피어링 연결 지점' }] : []),
      { h: '선' },
      { ic: ['<svg width="34" height="12"><path d="M1,6H26" stroke="#7d8590" stroke-width="1.3"/><path d="M24,2.5L31,6L24,9.5z" fill="#7d8590"/></svg>'], t: '라우트로 나가는 길(화살표 = 진행 방향)' },
      { ic: ['<svg width="34" height="12"><path d="M1,6H33" stroke="#9aa4b0" stroke-width="1.6"/></svg>'], t: `연결(어태치먼트 · TGW 피어링 · DX 게이트웨이${B.lines.some((l) => l.kind === 'vpn') ? ' · VPN' : ''})` },
      ...(B.lines.some((l) => l.kind === 'cforigin') ? [{ ic: ['<svg width="34" height="12"><path d="M1,6H26" stroke="#9aa4b0" stroke-width="1.6"/><path d="M24,2.5L31,6L24,9.5z" fill="#7d8590"/></svg>'], t: 'CloudFront 오리진(화살표 = 요청이 가는 로드밸런서·EC2)' }] : []),
      ...(B.lines.some((l) => l.kind === 'dnsalias') ? [{ kind: 'dnsalias', ic: [`<svg width="34" height="12"><path d="M1,6H26" stroke="${COL.r53}" stroke-width="1.5"/><path d="M24,2.5L31,6L24,9.5z" fill="${COL.r53}"/></svg>`], t: 'Route 53 별칭(화살표 = 그 이름이 가리키는 로드밸런서)' }] : []),
      ...(B.lines.some((l) => l.kind === 'vpclink') ? [{ kind: 'vpclink', ic: [`<svg width="34" height="12"><path d="M1,6H26" stroke="${COL.apigw}" stroke-width="1.5"/><path d="M24,2.5L31,6L24,9.5z" fill="${COL.apigw}"/></svg>`], t: 'API Gateway VPC 링크(화살표 = 요청이 전달되는 로드밸런서)' }] : []),
      ...(B.lines.some((l) => l.kind === 'dbproxy') ? [{ kind: 'dbproxy', ic: [`<svg width="34" height="12"><path d="M1,6H26" stroke="${COL.rdsp}" stroke-width="1.5"/><path d="M24,2.5L31,6L24,9.5z" fill="${COL.rdsp}"/></svg>`], t: 'RDS 프록시 → DB(화살표 = 프록시가 연결을 전달하는 DB)' }] : []),
      ...(B.lines.some((l) => l.kind === 'gaep') ? [{ kind: 'gaep', ic: [`<svg width="34" height="12"><path d="M1,6H26" stroke="${COL.ga}" stroke-width="1.5"/><path d="M24,2.5L31,6L24,9.5z" fill="${COL.ga}"/></svg>`], t: 'Global Accelerator → 엔드포인트(화살표 = 요청이 전달되는 로드밸런서·인스턴스)' }] : []),
      ...(B.lines.some((l) => l.kind === 'cnx') ? [{ kind: 'cnx', ic: [`<svg width="34" height="12"><path d="M1,6H33" stroke="${COL.tgw}" stroke-width="1.2" stroke-dasharray="4 3"/></svg>`], t: 'TGW Connect(SD-WAN 오버레이 · 점선 = 그 어태치먼트 위로 지나가는 GRE 터널)' }] : []),
      ...(B.lines.some((l) => l.kind === 'cwanatt') ? [{ kind: 'cwanatt', ic: [`<svg width="34" height="12"><path d="M1,6H33" stroke="${COL.cwan}" stroke-width="1.5"/></svg>`], t: 'Cloud WAN 연결(코어 네트워크 ↔ VPC)' }] : []),
      ...(B.lines.some((l) => l.kind === 'lsnatt') ? [{ kind: 'lsnatt', ic: [`<svg width="34" height="12"><path d="M1,6H33" stroke="${COL.lsn}" stroke-width="1.5"/></svg>`], t: 'VPC Lattice 서비스 네트워크 연결(서비스 네트워크 ↔ VPC)' }] : []),
      ...(B.lines.some((l) => l.kind === 'lsvctgt') ? [{ kind: 'lsvctgt', ic: [`<svg width="34" height="12"><path d="M1,6H26" stroke="${COL.lsvc}" stroke-width="1.5"/><path d="M24,2.5L31,6L24,9.5z" fill="${COL.lsvc}"/></svg>`], t: 'VPC Lattice 서비스 → 대상(화살표 = 요청이 전달되는 EC2·로드 밸런서·Lambda)' }] : []),
      ...(B.lines.some((l) => l.kind === 'wafp') ? [{ kind: 'wafp', ic: [`<svg width="34" height="12"><path d="M1,6H26" stroke="${COL.waf}" stroke-width="1.5"/><path d="M24,2.5L31,6L24,9.5z" fill="${COL.waf}"/></svg>`], t: 'WAF 웹 ACL → 보호 대상(화살표 = 이 웹 ACL 이 요청을 먼저 검사하는 CloudFront·로드 밸런서·API Gateway)' }] : []),
      ...(B.lines.some((l) => l.kind === 'epprov') ? [{ kind: 'epprov', ic: [`<svg width="34" height="12"><path d="M1,6H26" stroke="${COL.vpce}" stroke-width="1.5"/><path d="M24,2.5L31,6L24,9.5z" fill="${COL.vpce}"/></svg>`], t: 'VPC 엔드포인트 → 제공자(화살표 = 트래픽을 전달받는 GWLB·방화벽)' }] : []),
      ...(B.lines.some((l) => l.kind === 'lbtarget') ? [{ kind: 'lbtarget', ic: [`<svg width="34" height="12"><path d="M1,6H26" stroke="${COL.lb}" stroke-width="1.5"/><path d="M24,2.5L31,6L24,9.5z" fill="${COL.lb}"/></svg>`], t: 'LB → 대상(화살표 = 요청이 전달되는 대상 · ×N = 등록 수 · 외부 IP N = VPC 밖 IP 수)' }] : []),
      { ic: ['<svg width="34" height="12"><path d="M1,6H33" stroke="#9aa4b0" stroke-width="1.6" stroke-dasharray="7 5"/></svg>'], t: 'VPC 피어링' }]
  }
  function legend () {
    const el = document.getElementById('awsleg'); if (!el) return
    el.innerHTML = legendRows().map((r) => (r.h ? `<h4>${esc(r.h)}</h4>` : `<div class="aw-lg"${r.kind ? ` data-kind="${r.kind}"` : ''}>${r.ic.join('')}<span>${esc(r.t)}</span></div>`)).join('')
  }
  // 내보낸 그림 안의 범례(§17.18-2) — 화면의 범례는 SVG 밖 HTML 이라 그대로는 파일에 안 실린다. 같은 줄을 SVG 로 다시 그린다
  function legendSvg () {
    if (!B) return null
    const rows = legendRows(); const RH = 28; const HH4 = 30; const PAD = 16; const W = 420
    let y = PAD; const out = []
    for (const r of rows) {
      if (r.h) { out.push(`<text x="${PAD}" y="${y + 18}" fill="#c9d1d9" font-size="13" font-weight="600">${esc(r.h)}</text>`); y += HH4; continue }
      let x = PAD
      for (const ic of r.ic) {
        const w = +(/width="([\d.]+)"/.exec(ic) || [0, 24])[1]; const h = +(/height="([\d.]+)"/.exec(ic) || [0, 24])[1]
        out.push(ic.replace(/^<svg /, `<svg x="${x}" y="${P2(y + (RH - h) / 2)}" `)); x += w + 4
      }
      out.push(`<text x="${Math.max(PAD + 42, x + 6)}" y="${y + RH / 2 + 4}" fill="#c9d1d9" font-size="12"${r.kind ? ` data-kind="${r.kind}"` : ''}>${esc(r.t)}</text>`)
      y += RH
    }
    const h = y + PAD
    return { w: W, h, svg: `<g id="aw-exleg" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"><rect x="0.5" y="0.5" width="${W - 1}" height="${h - 1}" rx="4" fill="#0d1117" stroke="#30363d"/>${out.join('')}</g>` }
  }
  function wire () {
    if (wired || !X) return
    wired = true
    const svg = X.svgEl
    svg.addEventListener('mousedown', (e) => { downAt = { x: e.clientX, y: e.clientY } }, true)
    svg.addEventListener('click', onClick, true)
    const c = document.getElementById('awscard'); if (c) c.addEventListener('click', (e) => e.stopPropagation())
    const ck = () => setOpt(document.getElementById('aws-az').checked, document.getElementById('aws-sn').checked)
    const az = document.getElementById('aws-az'); const sn = document.getElementById('aws-sn')
    if (az) az.addEventListener('change', ck); if (sn) sn.addEventListener('change', ck)
    const pop = (id, btn) => (e) => {
      e.stopPropagation(); const n = document.getElementById(id); const on = n.style.display !== 'block'
      for (const x of ['awsleg', 'awsnote']) document.getElementById(x).style.display = 'none'
      for (const x of ['aws-legb', 'aws-info']) document.getElementById(x).classList.remove('on')
      if (on) { n.style.display = 'block'; document.getElementById(btn).classList.add('on') }
    }
    const lb = document.getElementById('aws-legb'); if (lb) lb.addEventListener('click', pop('awsleg', 'aws-legb'))
    const inf = document.getElementById('aws-info'); if (inf) inf.addEventListener('click', pop('awsnote', 'aws-info'))
    const ub = utBar(); if (ub) ub.addEventListener('click', utClick)
    const ubt = utBtn(); if (ubt) ubt.addEventListener('click', utBtnClick)
    document.addEventListener('click', (e) => {
      // 사용률 메뉴 밖을 누르면 열린 메뉴만 닫힌다 — 고른 것은 남는다(§17.25)
      if (utOpen && !(e.target.closest && e.target.closest('#awsut'))) { utOpen = ''; utBarRender() }
      if (e.target.closest && e.target.closest('#awsleg,#awsnote,#aws-legb,#aws-info')) return
      for (const x of ['awsleg', 'awsnote']) { const n = document.getElementById(x); if (n) n.style.display = 'none' }
      for (const x of ['aws-legb', 'aws-info']) { const n = document.getElementById(x); if (n) n.classList.remove('on') }
    })
  }
  // ======================================================================== 하네스용 읽기
  function dump () {
    if (!SC || !B) return null
    return {
      opt: { ...O }, W: SC.W, H: SC.H, edges: { ...SC.cnt, total: X.T.edges.length },
      frames: B.frames.map((f) => ({ ...f })),
      cells: [...B.boxes.values()].filter((b) => /aws-(item|hub|port)/.test(b.kind)).map((b) => ({ key: b.id, kind: b.kind, type: b.node.type, x: b.x, y: b.y, w: b.w, h: b.h,
        parent: b.parentBox, cellKind: B.cellKind.get(b.id) || (M.hubs.get(b.id) || M.globs.get(b.id) || {}).kind || (b.kind === 'aws-port' ? 'port' : ''),
        members: B.cellIds.get(b.id) || [b.id], col: B.col.get(b.id) || null, border: !!b.border, rect: B.rect.get(b.id) || null, acct: (M.items.get(b.id) || M.hubs.get(b.id) || M.globs.get(b.id) || B.inet.get(b.id) || B.agg.get(b.id) || {}).acct,
        ...(B.inet.has(b.id) ? { synth: true } : {}), ...(M.pp.has(b.id) ? { pcx: M.pp.get(b.id).pcx } : {}) })),
      lines: B.lines.map((l) => ({ k: l.k, a: l.a, b: l.b, kind: l.kind, pts: l.pts.map((p) => p.slice()), sub: l.sub || null, to: l.sub ? [l.tid, ...(l.extra || [])].filter(Boolean) : [], pcx: l.pcx || null, att: l.att || null,
        ...(l.lbe ? { lbe: l.lbe.slice(), chip: l.chip ? { ...l.chip } : null } : {}) })),
      labels: B.labels.map((x) => ({ ...x })),
      hidden: [...M.hidden.entries()], why: [...M.why.entries()],
      rel: [...M.rel.entries()].map(([k, v]) => [k, v.length]),
      hl: HL ? { nodes: HL.nodes.slice(), lines: HL.lines.slice(), frames: HL.frames.slice() } : null,
      card: cardKey, back: cardBack, openV, op: X.S.awsOp || 0,
      cellOf: [...B.cellOf.entries()],
      pm: [...(B.pm || [])].sort(),
      model: { vpcs: M.vpcs.size, subnets: M.subnets.size, items: M.items.size, hubs: M.hubs.size, globs: M.globs.size, ports: M.ports.size,
        peers: M.peers.length, tgwPeers: M.tgwPeers.length, dxtgw: M.dxtgw.length, stubLines: M.stubLines.length },
      // 사용률(§17.25 · 검사 140~145) — 장면(배지 · 세모 · 숨긴 구간)과 막대 상태. 하네스는 글자를 관측 문서에서 **따로** 만든다
      ut: { view: X.util && X.util.on() ? (X.obs && X.obs.on() ? 'both' : 'ut') : 'mon', win: X.S.utWin, pick: X.S.utPick || null, open: utOpen, tab: utTab, scene: utDump(), anim: !!utRaf },
    }
  }
  // 정보창 칸(§17.11) — 검사 129 가 이름·개수를 독립으로 맞춰 본다
  const row = (r) => ({ a: r.a, b: r.b, t: r.t, c: r.c, cls: r.cls, tk: r.tk || [], ...(r.ek ? { ek: r.ek } : {}), ...(r.go ? { go: r.go } : {}) })
  const tblOf = (t) => ({ label: t.label, n: t.n, nc: t.nc, ur: t.ur, empty: t.empty, cols: t.cols.map((c) => c.h), wrap: t.wrap, rows: t.rows.map(row) })
  const gRows = (g) => [...(g.rows || []), ...(g.tbl ? g.tbl.rows : []), ...(g.tabs || []).flatMap((t) => t.rows)]
  const secsOf = (k) => { const d = det(k); return d ? { title: d.title, sub: d.sub || '', rows: d.rows, secs: (d.secs || []).map((s) => ({ label: s.label, n: s.n, nc: s.nc, ur: !!s.ur, empty: s.empty, rows: s.groups.flatMap(gRows).map(row), groups: s.groups.map((g) => ({ h: g.h, dl: g.dl || null, rows: (g.rows || []).map(row), tbl: g.tbl ? tblOf(g.tbl) : null, tabs: g.tabs ? g.tabs.map(tblOf) : null })) })), members: d.members || null } : null }

  const keys = () => (B ? [...B.det.keys()] : [])   // 하네스용: 정보창이 열리는 열쇠 전부
  // `stateKo` = 정보창 「상태」 줄의 표(STATE_KO) — 「!」 상자 「모니터링 이상」 절의 상태 칸이 같은 말을 쓴다(§17.26 C-8)
  globalThis.NT_AWS = { targetsWord, stateKo, layout, render, applyDim, keepOnly, fit, place, closeAll, open, keys, det: secsOf, dump, setOpt, legendSvg, obsLines, markAt, markLines, markSeat, COL, SVC,
    utEsc, utList, utPick: (k) => utPickSet(k), UT: { WIN: UT_WIN, SPD: UT_SPD, GAP: UT_GAP } }
})()
