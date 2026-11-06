// nettopo 화면의 껍데기 — 위 막대·정보창·목록·배너·실시간 모니터링·이력 비교·내보내기·팬/줌·필터·범위·검색.
// 그림(배치·틀·선·아이콘·정보창 내용)은 aws-canvas.js(`window.NT_AWS`)가 그린다. 정본: docs/canvas-principles.md
//   (§17 새 도식 · §3 화면 축 · §13 승인 대장) — 규칙을 바꾸려면 그 문서를 먼저 고치고 verify-flow.mjs 검사를 같이 고쳐라.
;(function () {
  'use strict'
  // ---- §3j J5 이력 비교 화면의 오른쪽 칸 ----
  // 오른쪽 칸은 **이 페이지를 한 번 더 연 것**이다(`srcdoc` 틀). 부모가 그때의 구조·상태·표시를 설정 태그
  // `#nettopo-cmp` 에 실어 넣고, 이 모듈은 그 태그가 있으면 `window.TOPO`·`window.OBS` 대신 그것을 그린다.
  // 🔴 같은 배치 함수·같은 그리기를 지나야 「그때」가 「지금」과 같은 규칙으로 그려진다 — 그리기 경로를
  //    하나 더 만들면 두 칸이 서로 다른 규칙을 말한다(§3j J5 「왜 `render(L, P)` 로 넓히지 않았나」).
  // 🔴 모양이 틀리면 **죽는다** — 반쯤 맞는 설정으로 그리면 오른쪽 칸이 「지금」을 「그때」라고 보여 준다.
  const CMP = (() => {
    const el = document.getElementById('nettopo-cmp')
    if (!el) return null
    const j = JSON.parse(el.textContent)
    if (!j || j.v !== 1 || !j.topo || !Array.isArray(j.topo.nodes) || !Array.isArray(j.marks) ||
      (j.focus != null && (!Array.isArray(j.focus.marks) || !Array.isArray(j.focus.seats)))) {
      throw new Error('nettopo-cmp: 설정 모양이 틀렸다')
    }
    return j
  })()
  // 부모만: 이 문서의 원문 = 비교 칸을 여는 재료. 🔴 **DOM 을 바꾸기 전에** 뜬다 — 그린 뒤에 뜨면 오른쪽 칸이
  //    왼쪽 그림을 한 벌 더 들고 태어난다. 이 시점의 문서에는 이 스크립트까지만 있다(검증 오버레이는 제품에 없다 — 하네스가 나중에 주입한다).
  const DOC_SRC = CMP ? null : '<!DOCTYPE html>\n' + document.documentElement.outerHTML
  // 부모만: 열린 비교 칸(`cmpOpen`). 한 번에 하나다. 🔴 평소 그림은 이 값을 **흐림에만** 읽는다(J1 — 비교 중에는
  //    검색 흐림을 끈다). 배치·그리기는 읽지 않는다 — 과거를 골라도 왼쪽은 바이트 동일이다.
  let CMPV = null
  const T = CMP ? CMP.topo : window.TOPO
  const TYPE_LABEL = window.TYPE_LABEL

  // ---- 색 토큰 (§8). 이 객체 밖에서 색 리터럴을 쓰지 마라.
  // flow.css 의 :root 변수와 값을 같게 둔다(규약이다 — 두 곳을 비교하는 검사는 없다).
  // v4: 아쿠아 → AWS 주황 기점(사용자 요구 2026-09-13 "aws 색깔인 주황색 비슷한 것").
  // 면 계층은 AWS Squid Ink(#232F3E) 계열로 올려 대비를 키웠다 — line 51→86, wire 132→143.
  const C = {
    bg: '#0a0d12', lane: '#131a24', box: '#1b2432', chip: '#24303f',
    line: '#46586e', wire: '#7f91a5', text: '#f2f6fa', dim: '#9fb0c0',
    // §8.2 v9-B: 상태색은 **선명도**로 갈린다(사용자 결정 2026-09-14) — 배경 대비 ≥ 5.0.
    accent: '#ffffff', ok: '#2fe08a', warn: '#ffcf1a', err: '#ff3b30'
  }
  // 점선 무늬. 틀 종류의 점선은 aws-canvas.js 가 정한다(§17) — 여기 남은 것은 `ghost` 하나다.
  // `ghost` = 없어진 것(§3j J4). 🔴 **비교 칸 안에서만** 쓴다 — 평소 그림의 연결선 대시는 흐름 하나다.
  //    무늬가 흐름·리전·AZ 점선과 달라야 「없어졌다」가 다른 뜻으로 읽히지 않는다(글자도 항상 붙는다).
  const DASH = { ghost: '14 6' }

  // ---- 웨이브 2 (§13-53~64) ----
  // §13-56: 빈·에러·지연 문구는 **미리 정한 설계물**이다. 그때그때 만들면 화면마다 다른 말을
  // 한다 → 이 상수 셋 밖의 안내 문구를 쓰지 마라(검사 56).
  const COPY = {
    empty: '조건에 맞는 리소스가 없습니다. 필터를 지우거나 조건을 넓혀 보세요.',
    error: '조건을 해석할 수 없습니다 — 예: type=ec2_instance AND NOT environment=prod',
    // §3k: 소요 시간은 사양 문장이다 → 개발 설명(`slowDev`)으로 옮겼다. 화면은 출처만 말한다.
    slow: '이 화면은 마지막 수집 결과입니다',
    slowDev: '수집은 계정·리전 수에 따라 약 20분이 걸린다',
    // 검색 `상태:` 원자(§17.26 C-8) — 모니터링이 꺼져 있으면 아무것도 맞지 않고 **그 사실을 말한다**(조용한 0건 금지 · §13-56)
    monOff: '상태 조건은 모니터링을 켰을 때만 맞는 리소스가 있습니다 — 지금은 모니터링이 꺼져 있거나 상태 정보가 없습니다'
  }
  // §3k K3 — 못 읽은 이유는 **사람 말**로 쓴다. 원문 코드·AWS 메시지는 `title`(마우스를 올렸을 때)과 배너
  // 개발 설명에만 간다(검사 115 ③). 🔴 표에 없는 코드를 화면 글자로 내지 않는다 — 「읽지 못했다」로 말한다.
  const REASON_WORD = {
    access_denied: '권한이 없어 읽지 못했습니다',
    throttled: 'AWS 가 요청량 초과로 거절했습니다 — 다음 수집 때 다시 읽습니다',
    unsupported_region: '이 리전에서는 해당 서비스를 사용할 수 없습니다',
    api_error: 'AWS 가 오류를 반환했습니다',
    timeout: 'AWS 응답 시간이 초과됐습니다',
    pagination_truncated: '목록이 길어 끝까지 읽지 못했습니다',
    credentials_unavailable: '해당 계정에 접근하지 못했습니다'
  }
  // `reason` 없이 커버리지 `status` 가 코드 자리에 온 경우도 같은 표로 옮긴다(§3k K3).
  const STATUS_REASON = { denied: 'access_denied', unsupported: 'unsupported_region', error: 'api_error' }
  // 🆕 왼쪽 아래 「!」 상자의 이유 문장(§17.26 C-3 표가 정본 · 사용자 결정 2026-10-20) — **고객 말**(합니다체 · 할 일까지).
  //    위 `REASON_WORD`(했다체)는 정보창·독·캔버스 `title` 자리에 그대로다. 🔴 「스택 B」 같은 내부 이름 대신 설치 안내의 단계를 댄다.
  const POP_REASON = {
    access_denied: '권한이 없어 읽지 못했습니다. 읽기 전용 역할(설치 안내 4단계)의 권한을 확인하면 다음 수집부터 표시됩니다.',
    throttled: '요청량 초과로 AWS 가 거절했습니다. 다음 수집 때 다시 읽습니다.',
    unsupported_region: '이 리전에서는 해당 서비스를 사용할 수 없습니다. 별도 조치는 필요 없습니다.',
    api_error: 'AWS 가 오류를 반환했습니다. 다음 수집 때 다시 읽습니다.',
    timeout: 'AWS 응답 시간이 초과됐습니다. 다음 수집 때 다시 읽습니다.',
    pagination_truncated: '목록이 너무 길어 끝까지 읽지 못했습니다. 캔버스에는 읽은 부분까지만 표시됩니다.',
    credentials_unavailable: '해당 계정에 접근하지 못했습니다. 해당 계정에 읽기 전용 역할(설치 안내 4단계)이 있는지 확인하세요.',
    skipped: '수집하지 않도록 설정된 영역입니다.'
  }
  const popReason = (c) => POP_REASON[STATUS_REASON[c] || c] || POP_REASON_ELSE
  const POP_REASON_ELSE = '읽지 못했습니다.'

  const reasonWord = (c) => (c === 'skipped' ? '건너뛰었습니다' : REASON_WORD[STATUS_REASON[c] || c] || '읽지 못했습니다')
  // §13-58: 타입별 콘솔 경로. 리전이 빠지면 엉뚱한 리전을 연다(전역 규칙: 리전 항상 명시).
  // 경로가 타입마다 달라야 딥링크가 의미를 갖는다(검사 58이 경로 중복 0을 잰다).
  const CONSOLE_PATH = {
    vpc: (n, id) => `vpc/home?region=${n.region}#VpcDetails:VpcId=${id}`,
    subnet: (n, id) => `vpc/home?region=${n.region}#SubnetDetails:subnetId=${id}`,
    internet_gateway: (n, id) => `vpc/home?region=${n.region}#InternetGateway:internetGatewayId=${id}`,
    nat_gateway: (n, id) => `vpc/home?region=${n.region}#NatGatewayDetails:natGatewayId=${id}`,
    transit_gateway: (n, id) => `vpc/home?region=${n.region}#TransitGatewayDetails:transitGatewayId=${id}`,
    // 🔁 2026-10-24 실콘솔 확인(콘솔링크-검증 · 옛 꼴 `#TransitGatewayAttachment:` 은 VPC 홈으로 튕겼다)
    transit_gateway_attachment: (n, id) => `vpc/home?region=${n.region}#TransitGatewayAttachmentDetails:transitGatewayAttachmentId=${id}`,
    vpc_peering_connection: (n, id) => `vpc/home?region=${n.region}#PeeringConnections:vpcPeeringConnectionId=${id}`,
    vpc_endpoint: (n, id) => `vpc/home?region=${n.region}#EndpointDetails:vpcEndpointId=${id}`,   // 🔁 상세 꼴(옛 목록+필터 · 2026-10-24 실콘솔 확인)
    // 🔴 이름이 낡아 조용히 안 걸렸다 — 엔진 타입은 `vpn_gateway`·`direct_connect_connection` 이다(QA 기능개발 7).
    //    옛 이름(`virtual_private_gateway`·`dx_connection`)은 아래에서 별칭으로 남긴다 — 렌더러 픽스처가 아직 그 이름을 쓴다(`aws-canvas.js` `KIND` 와 같은 방식).
    vpn_gateway: (n, id) => `vpc/home?region=${n.region}#VpnGatewayDetails:VpnGatewayId=${id}`,   // 🔁 상세 꼴(2026-10-24 실콘솔 확인)
    customer_gateway: (n, id) => `vpc/home?region=${n.region}#CustomerGatewayDetails:customerGatewayId=${id}`,   // 🔁 상세 꼴(2026-10-24 실콘솔 확인)
    ec2_instance: (n, id) => `ec2/home?region=${n.region}#InstanceDetails:instanceId=${id}`,
    auto_scaling_group: (n, id) => `ec2/home?region=${n.region}#AutoScalingGroupDetails:id=${id}`,
    eks_cluster: (n, id) => `eks/home?region=${n.region}#/clusters/${id}`,
    load_balancer: (n, id) => `ec2/home?region=${n.region}#LoadBalancers:search=${id}`,
    network_firewall: (n, id) => `vpc/home?region=${n.region}#NetworkFirewalls:name=${id}`,
    firewall_endpoint: (n, id) => `vpc/home?region=${n.region}#FirewallEndpoints:id=${id}`,
    route53_resolver_endpoint: (n, id) => `route53resolver/home?region=${n.region}#/outbound-endpoints/${id}`,
    direct_connect_connection: (n, id) => `directconnect/v2/home?region=${n.region}#/connections/${id}`,
    cloudfront: (n, id, r) => `cloudfront/v4/home?region=${r}#/distributions/${id}`,
    endpoint_service: (n, id) => `vpc/home?region=${n.region}#EndpointServices:serviceId=${id}`,
    aws_service: (n) => `console/home?region=${n.region}`,
    // 🆕 2026-10-24 실콘솔 확인(상세 화면) — EIP = 자기 정보창 · SG·NACL·라우팅 테이블 = 다른 정보창 칸 머리(`aws-canvas.js` `conLink`)
    elastic_ip: (n, id, r) => `ec2/home?region=${r}#ElasticIpDetails:AllocationId=${id}`,
    security_group: (n, id, r) => `vpc/home?region=${r}#SecurityGroup:groupId=${id}`,
    network_acl: (n, id, r) => `vpc/home?region=${r}#NetworkAclDetails:networkAclId=${id}`,
    // 🆕 정보창 개편(§17.11 「콘솔 표현 원칙」 · 사용자 결정 2026-10-24) — 「AWS 콘솔에서 열기」는 **모든 정보창**에 있다(CloudFront·WAF·Lambda … 빠져 있었다).
    //   `r` = 콘솔 리전(전역 자원은 아래 `GLOBAL_HOME`) · 이름으로 여는 콘솔(Lambda·S3·SageMaker 등)은 이름을 쓴다(인코딩)
    vpn_connection: (n, id, r) => `vpc/home?region=${r}#VpnConnectionDetails:VpnConnectionId=${id}`,
    route_table: (n, id, r) => `vpc/home?region=${r}#RouteTableDetails:RouteTableId=${id}`,
    transit_gateway_route_table: (n, id, r) => `vpc/home?region=${r}#TransitGatewayRouteTableDetails:transitGatewayRouteTableId=${id}`,
    direct_connect_gateway: (n, id, r) => `directconnect/v2/home?region=${r}#/dxgateways/${id}`,
    direct_connect_virtual_interface: (n, id, r) => `directconnect/v2/home?region=${r}#/virtual-interfaces/${id}`,
    ec2_instance_connect_endpoint: (n, id, r) => `vpc/home?region=${r}#InstanceConnectEndpointDetails:instanceConnectEndpointId=${id}`,
    lattice_service_network: (n, id, r) => `vpc/home?region=${r}#ServiceNetwork:serviceNetworkId=${id}`,
    lattice_service: (n, id, r) => `vpc/home?region=${r}#Service:serviceId=${id}`,
    lambda_function: (n, id, r) => `lambda/home?region=${r}#/functions/${encodeURIComponent(n.name || id)}`,
    api_gateway: (n, id, r) => (/^rest$/i.test(String((n.attrs || {})['프로토콜'] || '')) ? `apigateway/main/apis/${id}/resources?api=${id}&region=${r}` : `apigateway/main/api-detail?api=${id}&region=${r}`),
    rds_db_instance: (n, id, r) => `rds/home?region=${r}#database:id=${id};is-cluster=false`,
    rds_db_cluster: (n, id, r) => `rds/home?region=${r}#database:id=${id};is-cluster=true`,
    rds_db_proxy: (n, id, r) => `rds/home?region=${r}#proxy:id=${encodeURIComponent(n.name || id)}`,
    elasticache_cluster: (n, id, r) => `elasticache/home?region=${r}#/redis/${encodeURIComponent(id)}`,
    opensearch_domain: (n, id, r) => `aos/home?region=${r}#opensearch/domains/${encodeURIComponent(n.name || id)}`,
    msk_cluster: (n, id, r) => `msk/home?region=${r}#/clusters?search=${encodeURIComponent(n.name || id)}`,
    ecs_service: (n, id, r) => `ecs/v2/clusters/${encodeURIComponent((n.attrs || {})['클러스터'] || '')}/services/${encodeURIComponent(n.name || id)}?region=${r}`,
    sagemaker_notebook_instance: (n, id, r) => `sagemaker/home?region=${r}#/notebook-instances/${encodeURIComponent(n.name || id)}`,
    sagemaker_domain: (n, id, r) => `sagemaker/home?region=${r}#/studio/${id}`,
    sagemaker_endpoint: (n, id, r) => `sagemaker/home?region=${r}#/endpoints/${encodeURIComponent(n.name || id)}`,
    s3_bucket: (n, id, r) => `s3/buckets/${encodeURIComponent(n.name || id)}?region=${r}`,
    waf_web_acl: (n, id, r) => `wafv2/homev2/web-acl/${encodeURIComponent(n.name || id)}/${id}/overview?region=${n.region ? r : 'global'}`,
    route53_hosted_zone: (n, id) => `route53/v2/hostedzones#ListRecordSets/${id}`,
    global_accelerator: (n, id, r) => `globalaccelerator/home?region=${r}#AcceleratorDetails:AcceleratorArn=${encodeURIComponent(String((n.attrs || {}).arn || id))}`,
    core_network: (n, id, r) => `networkmanager/home?region=${r}#/networks/${encodeURIComponent(String((n.attrs || {})['글로벌 네트워크'] || ''))}/core-network`
  }
  // 전역 자원(리전이 없다)의 콘솔 리전 — CloudFront·Route 53·WAF(CloudFront 범위)·DX 게이트웨이 = us-east-1 · Global Accelerator·Network Manager = us-west-2(AWS 가 정한 홈 리전)
  //   S3 = 리전이 없는 노드로 온다 → us-east-1 로 열면 S3 콘솔이 버킷 리전으로 옮겨 간다(2026-10-24 · 링크가 아예 없었다 — 실콘솔 미확인)
  const GLOBAL_HOME = { cloudfront: 'us-east-1', route53_hosted_zone: 'us-east-1', waf_web_acl: 'us-east-1', direct_connect_gateway: 'us-east-1', global_accelerator: 'us-west-2', core_network: 'us-west-2', s3_bucket: 'us-east-1' }
  CONSOLE_PATH.virtual_private_gateway = CONSOLE_PATH.vpn_gateway
  CONSOLE_PATH.dx_connection = CONSOLE_PATH.direct_connect_connection
  // §13-62 태그 버킷. 규칙은 키 접두뿐이다 — 값을 보고 버킷을 정하면 같은 키가 노드마다
  // 다른 버킷에 들어간다. `Untagged` 는 **항상 맨 아래**(검사 62).
  const TAG_BUCKETS = ['Custom', 'AWS', 'Terraform', 'K8s', 'Untagged']
  const bucketOf = (k) => k.startsWith('aws:') ? 'AWS'
    : k.startsWith('terraform') ? 'Terraform'
      : (k.startsWith('kubernetes.io/') || k.startsWith('k8s.io/')) ? 'K8s' : 'Custom'
  // §13-60 내보내기: 캔버스만 나간다. 폭 3840 은 Cloudcraft PNG 기본값과 같다.
  const EXPORT_W = 3840

  // 리전 위→아래 고정 순서 (§5). 목록에 없으면 코드 사전순으로 뒤에 붙는다.
  const REGION_ORDER = ['us-east-1', 'us-east-2', 'us-west-1', 'us-west-2', 'ca-central-1',
    'sa-east-1', 'eu-west-1', 'eu-west-2', 'eu-central-1', 'eu-north-1', 'me-central-1',
    'af-south-1', 'ap-south-1', 'ap-southeast-1', 'ap-southeast-2', 'ap-northeast-1',
    'ap-northeast-2', 'ap-northeast-3']

  // ---------------- 모델 ----------------
  const byId = new Map()
  const kids = new Map()
  for (const n of T.nodes) byId.set(n.id, n)
  for (const n of T.nodes) {
    if (!n.parent_id) continue
    if (!kids.has(n.parent_id)) kids.set(n.parent_id, [])
    kids.get(n.parent_id).push(n)
  }
  // §13-49: **검색과 필터는 같은 것이 아니다.** 검색은 비매칭을 회색화할 뿐이고(맥락 유지),
  // 필터는 **제거**다(파괴적 → 확인 모달).
  let FILTER = null           // Set<id> | null
  // §13-63: 사용자 편집(노드 숨기기)도 **같은 문 하나**를 지난다. 편집을 다른 데서 걸러내면
  // 라이브 복귀 때 «어디까지 되돌려야 하는지»가 코드 여러 곳에 흩어진다(검사 63의 바이트 동일).
  let HIDDEN = new Set()      // 편집으로 숨긴 노드 id
  // 대장 94·101: 관측 변경 이력. 없을 수도 있다 — 실계정 스냅샷을 그대로 구운 HTML 에는
  // `window.OBS` 가 없다(`adapt-engine-snapshot.mjs`). 없으면 독의 이력 탭이 **그 사실을 말한다**.
  // 🔴 `let` 이다 — 실시간 채널(S3)이 새 문서로 갈아 끼운다. 바꾸는 곳은 `obsPairUp` 하나뿐이고,
  //    구운 문서는 `OBS_BAKED` 로 남겨 실시간을 끄면 그 문서로 돌아간다.
  // 오른쪽 비교 칸(§3j)은 부모가 되짚어 만든 「그때」 문서를 쓴다 — 이 페이지에 구운 문서가 아니다.
  const OBS_BAKED = CMP ? (CMP.obs || null) : (window.OBS || null)
  let OBS = OBS_BAKED
  // ---- 실시간 주소 칸(ADR 0003 D2 · 결정 2026-09-27) ----
  // 배포된 `index.html` 에서는 서명 전용 함수(`infra/state-lambda/signer.py`)가 이 칸을 5분마다 새로
  // 굽는다. 로컬·픽스처에서는 값이 null 이다 → **주소가 없는 화면**이고 «모니터링» 은 구운 문서만 얹는다.
  // 🔴 모양이 하나라도 틀리면 주소가 없는 것으로 본다 — 반쯤 맞는 칸으로 요청을 만들면 실패 사유가
  //    「권한」인지 「칸이 깨졌다」인지 화면이 구별하지 못한다. 만료 시각이 없는 칸도 버린다:
  //    자동 꺼짐의 상한을 모르는 채로 켜면 탭을 닫은 뒤 과금이 언제 멈추는지 아무도 모른다.
  const LIVE_SLOT = (() => {
    // 🔴 오른쪽 비교 칸은 **요청 0** 이다(§3j J5) — 원문에 구운 칸이 있어도 읽지 않는다.
    if (CMP) return null
    const el = document.getElementById('nettopo-live')
    let j = null
    try { j = el ? JSON.parse(el.textContent) : null } catch (e) { return null }
    if (!j || j.v !== 1 || typeof j.state_url !== 'string' || typeof j.control_url !== 'string') return null
    const urlUntil = Date.parse(j.expires_at)
    if (!Number.isFinite(urlUntil)) return null
    // 🆕 최신 화면 열기(QA B P3 · 사용자 결정 2026-10-14) — 서명 함수가 굽는 **이동 주소**. 사람이 «최신 화면 열기» 를
    //    눌렀을 때만 이 탭이 넘어간다(fetch 0 · 검사 106). 🔴 **같은 버킷의 `/index.html` 만** 받는다 — 칸이 바뀐 파일이
    //    남의 주소를 실으면 이 버튼이 아무 데로나 사람을 보낸다. 상태 주소와 오리진이 다르거나 https 가 아니면 버튼이 없다
    //    (= 칸 없는 옛 서명 함수와 같은 화면 — 「최신 index.html 을 다시 받는다」를 말한다).
    let indexUrl = null
    try {
      const iu = typeof j.index_url === 'string' ? new URL(j.index_url) : null
      if (iu && iu.protocol === 'https:' && iu.origin === new URL(j.state_url).origin && iu.pathname === '/index.html') indexUrl = j.index_url
    } catch (e) { indexUrl = null }
    // 🆕 따라 보기(사용자 결정 2026-10-28 · ADR 0005 D4 보강 4) — 서명 함수가 굽는 **제어 키 읽기 주소**. 다른 창이 켜거나 바꾸거나
    //    끈 것을 1분마다 읽어 따라간다. 🔴 **같은 버킷의 `/control/desired.json` 만** 받는다(위 이동 주소와 같은 이유 — CSP `connect-src`
    //    는 그 오리진 하나다). 칸에 없으면(옛 서명 함수) 옛 동작이다: 다른 창과 부딪히면 덮지 않고 이 창만 끈다.
    let ctlGetUrl = null
    try {
      const cu = typeof j.control_get_url === 'string' ? new URL(j.control_get_url) : null
      if (cu && cu.protocol === 'https:' && cu.origin === new URL(j.state_url).origin && cu.pathname === '/control/desired.json') ctlGetUrl = j.control_get_url
    } catch (e) { ctlGetUrl = null }
    return { stateUrl: j.state_url, controlUrl: j.control_url, ctlGetUrl, indexUrl, urlUntil, signedAt: j.signed_at || null }
  })()
  // 🆕 서명 전 화면 파일(QA B 2차 2026-10-17) — 구조 빌드는 **서명 함수가 있는 스택에서만** CSP `connect-src` 에 버킷 오리진을
  //    굽고(없으면 `'none'` · `infra/stack-a-collector.yaml` `LIVE_CHANNEL`), 그 판은 서명 함수가 칸을 채워 `index.html` 로 게시한다.
  //    그러니 **버킷 오리진 CSP + 빈 칸** = 칸을 채우기 전의 원본이 사람 손에 들어왔다(서명 전 원본 키를 직접 받았거나, 게시 키를
  //    나누기 전 스택의 빌드 직후 몇 초). 이 파일로는 켤 수 없다는 사실과 할 일(잠시 뒤 다시 받기)을 말한다 — 말하지 않으면 사람은
  //    «모니터링» 을 눌러 보고 「실시간 주소가 없다」만 본다. 로컬 빌드(`'none'`)·채운 칸·모양이 깨진 칸은 여기 해당하지 않는다.
  const UNSIGNED_PAGE = (() => {
    if (CMP || LIVE_SLOT) return false
    const el = document.getElementById('nettopo-live')
    let j = null
    try { j = el ? JSON.parse(el.textContent) : null } catch (e) { return false }
    if (!j || j.v !== 1 || j.state_url !== null || j.control_url !== null) return false
    const meta = document.querySelector('meta[http-equiv="Content-Security-Policy"]')
    const cs = ((meta && meta.getAttribute('content')) || '').split(';').map((s) => s.trim()).find((s) => s.startsWith('connect-src ')) || ''
    return /^connect-src https:\/\/[a-z0-9][a-z0-9-]*\.s3\.[a-z0-9-]+\.amazonaws\.com$/.test(cs)
  })()
  // 🆕 상태를 얻을 길(사용자 결정 2026-10-20 · 대장 128 · §17.26 C-1) — 「모니터링」·「사용률」·「이력」 버튼은 이것이 참일 때만 **보인다**.
  //    실시간 주소 칸(`LIVE_SLOT`) · 서명 전 원본(`UNSIGNED_PAGE` — 상태 함수가 있는 스택) · 구운 상태 문서(`window.OBS` 의 `run`) 중 하나.
  //    셋 다 없으면 = 상태 함수 없이 올린 스택 A(빌드 `LIVE_CHANNEL=DISABLED` → 칸 null · CSP 의 connect-src 가 none · 구조만 `STRUCT=1`)
  //    🔴 이 주석에 CSP 원문 토큰(따옴표 친 none)을 그대로 쓰지 마라 — 인라인되면 산출물에 그 글자가 두 번 생겨 하네스의 칸 치환(검사 101~103·116)이 멈춘다
  //    또는 로컬에서 구조만 구운 파일 — 눌러도 얹을 것이 없다 → **숨긴다**(흐리게가 아니다 — 흐린 버튼은 「고장」으로 읽힌다 · QA A-06).
  //    🔴 열 때 **한 번** 정한다 — 받은 파일의 성질이고, 하네스 주입(`setObservations`)으로 바뀌지 않는다. 비교 칸(§3j)은 상단바가 없다.
  const STATE_WAY = !CMP && (!!LIVE_SLOT || UNSIGNED_PAGE || !!(OBS_BAKED && OBS_BAKED.run))
  // 이보다 짧게 남은 주소로는 켜지 않는다 — 켜자마자 서명이 죽어 「읽지 못함」만 보인다.
  // 🔴 켜기 전 줄·알약도 같은 선으로 «죽은 주소»를 말한다(#31·#32 리뷰 후속 · ADR 0005 D4 보강 2) — 배너가 첫
  //    렌더에서 읽으므로 여기 둔다(아래 토글 블록에 두면 TDZ 로 모듈이 죽는다).
  const LIVE_MIN_URL_MS = 60 * 1000
  const liveUrlDead = (now) => !!LIVE_SLOT && LIVE_SLOT.urlUntil - now < LIVE_MIN_URL_MS
  // «최신 화면 열기» 를 낼 수 있나 — 이동 주소가 있고 아직 살아 있다(죽은 서명으로 넘어가면 403 화면만 남는다).
  const canGoLatest = () => !!(LIVE_SLOT && LIVE_SLOT.indexUrl) && !liveUrlDead(Date.now())
  // 밀리초 → `…Z` 초 단위 ISO(핸들러가 `fromisoformat` 으로 읽는다) · 화면용 `HH:MMZ`(아래 `hhmm` 과 같은 모양).
  const msIso = (ms) => new Date(ms).toISOString().replace(/\.\d{3}Z$/, 'Z')
  // 🔴 **화면 글자의 시각은 전부 KST**(UTC+9 고정 · 서머타임 없음 · 사용자 결정 2026-09-27). 계약·키·정렬·
  //    되짚기·제어 문서는 UTC ISO 그대로다 — 바꾸는 것은 사람이 읽는 글자뿐이다. 브라우저 시간대를 쓰지
  //    않는다: 같은 파일이 여는 기계마다 다른 시각을 말하면 사람도 하네스도 대조할 수 없다(검사 112).
  const KST_MS = 9 * 3600 * 1000
  const kstIso = (ms) => new Date(ms + KST_MS).toISOString()   // KST 벽시계를 `…Z` 모양으로 — 글자만 쓴다
  const msHm = (ms) => kstIso(ms).slice(11, 16) + ' KST'
  const msHms = (ms) => kstIso(ms).slice(11, 19) + ' KST'
  // 경과 `m:ss`(진행 표시) · 남은 시간 「약 N분」/「약 H시간 M분」(따라 보는 창) — 음수는 0 으로
  const mss = (ms) => { const t = Math.max(0, Math.floor(ms / 1000)); return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}` }
  const aboutMin = (ms) => {
    const m = Math.max(1, Math.round(ms / 60e3))
    return m < 60 ? `약 ${m}분` : `약 ${Math.floor(m / 60)}시간${m % 60 ? ` ${m % 60}분` : ''}`
  }
  // ISO → `YYYY-MM-DD HH:MM:SS KST`. 못 읽는 값은 **원문 그대로** 둔다 — 지우면 무엇을 못 읽었는지 사라진다.
  const kstAt = (iso) => {
    const t = Date.parse(iso)
    return Number.isFinite(t) ? kstIso(t).slice(0, 19).replace('T', ' ') + ' KST' : String(iso)
  }
  // 🔴 이 객체의 필드를 바꾸는 곳은 아래 `liveOn`·`livePoll`·`liveRenew`·`liveOff` 넷뿐이다(토글 블록 뒤).
  //    `until` = 이 창이 스스로 끄는 시각(상한) · `lease` = 서버에 마지막으로 쓴 만료 · `mode` = 'lease'(5분마다
  //    조건부 연장) | 'fixed'(옛 저장소 — 상한을 한 번에 썼다) · `ctlEtag` = 내가 마지막으로 쓴 제어 문서의 ETag.
  //    여기 선언하는 이유: 배너(`bannerRows`)가 첫 렌더에서 읽는다 — 뒤에 두면 TDZ 로 모듈이 죽는다.
  const LIVE = { on: false, busy: false, layer2: false, util: false, setWhy: '', since: 0, until: 0, untilBy: null, etag: null,
    doc: null, docAt: 0, phase: null, why: '', note: '', notePill: '', noteWarn: false, reads: 0, writes: 0,
    lastCheck: 0, seq: 0, pollT: null, offT: null, asked: 0, pairWait: false,
    mode: null, lease: 0, ctlEtag: null, renewWhy: '', renewT: null,
    // 🆕 따라 보기(ADR 0005 D4 보강 4): `mode` 에 'follow'(다른 창이 켠 판을 읽기만 한다 — 쓰지도 연장하지도 않는다)가 더해졌다.
    //    `ctlAt` = 제어 키를 마지막으로 읽은(또는 쓴) 시각 · `ctlSeen` = 마지막으로 본 제어 문서의 ETag(조건부 읽기) ·
    //    `ctlWhy` = 제어 키를 못 읽은 이유(화면은 그대로 · 경고 꼬리 1개) · `mine` = 이 창이 **보낸** 제어 문서들의 지문
    //    (응답이 끊겨 확인 못 한 것 포함 — 412 뒤 읽은 판이 이 중 하나면 내 쓰기다 · 보강 1) · `wseq`·`wIn` = 쓰기 순번·진행 중 수
    //    (쓰기 전에 떠난 읽기 응답이 쓰기 뒤에 도착하면 버린다) · `end` = 따라 보는 판의 끝 시각(그 창이 고른 시간 · 없으면 0).
    ctlAt: 0, ctlSeen: null, ctlWhy: '', ctlReads: 0, mine: [], wseq: 0, wIn: 0, end: 0,
    // 🆕 켜는 시간(사용자 결정 2026-10-28) — 팝오버에서 고른다(기본 4시간 · 최대 8시간 = 서버 천장). 「사용률」 버튼도 이 값으로 켠다.
    //    `cap` = 지금 켜진 판이 쓰는 시간(켤 때 `dur` 를 옮겨 둔다 — 켠 뒤 팝오버를 바꿔도 지금 판의 자동 꺼짐 문장은 그대로다).
    dur: 4 * 3600 * 1000, cap: 0,
    // 🆕 첫 관측 진행 표시(사용자 결정 2026-10-28) — `doneReads` = 기다리던 것이 도착한 읽기 번호(그 읽기까지만 「③ 도착 ✓」를 말한다).
    doneWhat: '', doneReads: -1 }
  // §3g 범위 축 — «누구 것»을 좁힌다. 무엇을 그리는지는 새 도식이 `outScope` 로 정한다(§17.22).
  // 🔴 **「보기 범위」는 「수집 범위」가 아니다**(G3). 수집 범위는 `control/desired.json` 의 `scope`
  //    (ADR 0005)이고 이쪽은 화면 필터일 뿐이다 — 재수집 0 · 추가 비용 0. 합치면 「화면만 좁혔는데
  //    이력이 비었다」가 되고 아무도 이유를 설명할 수 없다.
  // 🔁 2026-10-20(대장 134 · G1): 여러 계정이다. **계정 노드 id 의 정렬 배열**(1개 이상) | null(=전체). 빈 배열은 두지 않는다 —
  //    마지막 계정을 끄면 `null` 이다(빈 배열이면 `inScope` 가 전부 false 라 빈 캔버스가 된다).
  let SCOPE = null
  let SCOPE_SET = null        // SCOPE 의 판정용 사본(`inScope` 가 계정마다 배열을 훑지 않게) — `scopeSet` 만 쓴다

  // 계정 이름(§17.5 「계정은 이름으로 부른다」 · 사용자 결정 2026-10-28) = 계정 노드 `name`(엔진이 실은 AWS 계정 이름) · 없으면 null.
  //   「-」·「–」·공백뿐 · 계정 번호 그 자체 = 이름 없음(번호를 이름으로 찍으면 12자리가 화면에 나온다 — 검사 122)
  const acctNumOf = (n) => String((n && n.attrs && (n.attrs.account || n.attrs.id)) || '')
  const acctNameOf = (n) => (n && typeof n.name === 'string' && !/^[\s\u002d\u2013]*$/.test(n.name) && n.name !== acctNumOf(n) ? n.name : null)
  // 정렬 = 이름 → id(§17.15 · 검사 131). 이름 있는 계정이 먼저(이름순), 없는 계정은 그 뒤(id 순) — 옛 상수 「계정」이 라틴 이름 뒤로 가던 순서
  const accounts = T.nodes.filter((n) => n.type === 'account')
    .sort((a, b) => { const x = acctNameOf(a); const y = acctNameOf(b)
      return (x === null) - (y === null) || (x !== null && y !== null ? x.localeCompare(y) : 0) || a.id.localeCompare(b.id) })

  const regionRank = (code) => {
    const i = REGION_ORDER.indexOf(code)
    return i < 0 ? REGION_ORDER.length : i
  }
  // 계정 ID 는 **마스킹해 보여준다**(앞 4 · 뒤 4). 크럼·범례가 같은 함수를 쓴다 — 두 곳이 다르게
  // 마스킹하면 사용자가 «다른 계정»으로 읽는다.
  const acctMask = (s) => String(s).slice(0, 4) + '…' + String(s).slice(-4)
  // 계정을 부르는 **한 꼴**(§17.5) — 이름 있음 = `이름 (앞4…뒤4)` · 없음 = 마스킹만. 자리마다 따로 지으면 한 계정이 자리마다 다르게 읽힌다.
  //   번호만 아는 자리(거부 줄 · 제외 목록 · 정보창 소유자)는 `acctText(번호)` — 수집한 계정이면 이름이 붙고, 수집 밖 계정은 마스킹만이다
  const acctShow = (num, name) => (name ? `${name} (${acctMask(num)})` : acctMask(num))
  const acctByNum = new Map(accounts.map((a) => [acctNumOf(a), a]))
  const acctText = (num) => acctShow(num, acctNameOf(acctByNum.get(String(num))))
  const acctLabel = (i) => (accounts[i] ? acctShow(acctNumOf(accounts[i]), acctNameOf(accounts[i])) : `계정 ${i + 1}`)

  // ---------------- §3g 범위 축 — 판정 (대장 93 · 사용자 요구 2026-09-20) ----------
  // 범위는 사실이므로 **모델 조상만** 본다(`mById`).
  const mById = new Map(T.nodes.map((n) => [n.id, n]))
  const acctIdCache = new Map()
  const acctOf = (n) => {
    if (!n) return null
    if (acctIdCache.has(n.id)) return acctIdCache.get(n.id)
    let cur = n; let out = null; let guard = 0
    while (cur && guard++ < 40) {
      if (cur.type === 'account') { out = cur.id; break }
      cur = cur.parent_id ? mById.get(cur.parent_id) : null
    }
    acctIdCache.set(n.id, out)
    return out
  }
  // 범위 밖 **모델 노드**만 false 가 된다.
  const inScope = (id) => !SCOPE || SCOPE_SET.has(acctOf(mById.get(id)))
  // 🔴 `SCOPE`(모듈 지역)와 `S.scope`(직렬화되는 상태)는 **한 사실의 두 사본**이다 — `lvSet` 과
  //    같은 이유로 여기 한 곳에서만 같이 쓴다. 따로 대입하면 `dump().scope` 와 화면이 갈린다.
  // 받는 것 = id 배열 | id 하나(옛 단일 경로) | null. 이 스냅샷에 없는 id 는 **그 하나만** 버린다(G4).
  // 🔴 결정론(불변식 ②): id 문자열 순으로 정렬해 한 벌만 든다 — 고른 순서가 배치·주소에 새지 않는다.
  //    `localeCompare` 를 쓰지 마라(실행 환경 로캘에 따라 순서가 갈린다).
  // `S.scope` = 정렬한 id 를 쉼표로 이은 글자 | null — 계정 하나면 옛 값(그 id)과 같다.
  const scopeSet = (ids) => {
    const want = new Set(ids == null ? [] : Array.isArray(ids) ? ids : [ids])
    const next = accounts.map((a) => a.id).filter((id) => want.has(id)).sort((a, b) => (a < b ? -1 : a > b ? 1 : 0))
    const key = next.length ? next.join(',') : null
    if (key === S.scope) return S.scope
    SCOPE = next.length ? next : null; SCOPE_SET = SCOPE ? new Set(SCOPE) : null; S.scope = key
    return S.scope
  }

  // ---------------- §3h 권한 축 — 「권한이 없어 모른다」 (대장 93 · H1) ----------
  // W5: 새 도식은 사선 해치를 칠하지 않는다 — 아래 (계정, 리전) 판정은 정보창 「수집」 줄과 배너 줄이 쓴다.
  //     아래 「해치」는 그 판정(`denySet`)의 옛 이름이다.
  // 🔴 판정의 단위는 **(계정, 리전)** 이다(사용자 결정 2026-09-21). 계약의 coverage 항목은
  //    (계정, 리전, **리소스종류**)이지만 종류 단위로 말하면 **그 종류의 아이콘이 0개일 때 말할 곳이
  //    없다** — 못 읽어서 아이콘이 없는 것인데 화면은 「없다」고 말한다(불변식 ④). 계정·리전으로 접으면
  //    「이 영역은 모른다」가 정보창·배너 줄에 남는다.
  // 🔴 **`denied` 만** 해치다. `throttled`·`error`·`unsupported` 는 조치가 다르다 — 해치가 말하는
  //    조치는 「스택 B 를 배포한다」이고 스로틀·오류에 그 문구를 붙이면 **거짓 조치**다. 그쪽은
  //    `T.partial` 마커(§13-54)가 「확인하지 못했다」로 이미 말한다(H1 은 다섯 어휘를 합치지 않는다).
  // 🔴 `?`(AWS 가 관측하지 못한다)는 **여기 없다.** 계약에 그 어휘가 없다 —
  //    `schema/topology.schema.json` `$defs.attribute_value` 는 `null` 만 허용하고 「값이 없다」와
  //    「원리적으로 못 본다」를 구분하지 못한다. 렌더러가 `null` 을 임의로 `?` 로 읽으면 H1 의 3·4행을
  //    화면이 합치고 불변식 ③(표시용 지식을 렌더러에 몰래 두지 않는다)을 깬다 → **스키마 선행**이다.
  // 자원이 하나도 안 읽힌 계정은 표시할 자리가 없으므로 **배너 줄이 유일한 근거**가 된다(아래 `bannerRows`).
  let COV = Array.isArray(T.coverage) ? T.coverage.slice() : []
  let DENY = null
  const denyCache = new Map()
  const denySet = () => {
    if (DENY) return DENY
    DENY = new Set()
    for (const c of COV) if (c && c.status === 'denied') DENY.add(c.account_id + '|' + c.region)
    return DENY
  }
  // 걷기는 `mById`(모델 전용)다. 계정 번호는 노드 id 가 아니라 `attrs.account` 다(범례·마스킹이 읽는 곳과 같은 필드).
  const denyOf = (n) => {
    if (!n || !COV.length) return false
    if (denyCache.has(n.id)) return denyCache.get(n.id)
    let cur = n; let acct = null; let code = null; let guard = 0
    while (cur && guard++ < 40) {
      if (!code && cur.type === 'region') code = cur.name
      if (cur.type === 'account') { acct = cur; break }
      cur = cur.parent_id ? mById.get(cur.parent_id) : null
    }
    const num = acct && acct.attrs ? (acct.attrs.account || acct.attrs.id) : null
    const out = !!(num && code && denySet().has(num + '|' + code))
    denyCache.set(n.id, out)
    return out
  }
  // 전멸(§17.26 A-01 · 대장 131) — 그 (계정, 리전)의 구조 수집 행이 1개 이상이고 그중 `ok` 가 **하나도 없다**. 새 도식이 「못 읽음」 표시가 있는
  //   틀에서만 이것으로 「읽지 못함」/「일부 못 읽음」을 고른다(aws-canvas.js). 🔴 coverage 가 없는 입력은 판정하지 않는다(false — 모르는 것을 「전부」라 하지 않는다)
  const wipedOf = (rn) => {
    if (!rn || rn.type !== 'region' || !COV.length) return false
    let cur = rn; let acct = null; let g = 0
    while (cur && g++ < 40) { if (cur.type === 'account') { acct = cur; break } cur = cur.parent_id ? mById.get(cur.parent_id) : null }
    const num = acct && acct.attrs ? String(acct.attrs.account || acct.attrs.id || '') : ''
    const rows = COV.filter((c) => c && String(c.account_id) === num && c.region === rn.name)
    return rows.length > 0 && !rows.some((c) => c.status === 'ok')
  }
  // 하네스 주입구(§10 검사 92). 🔴 픽스처로는 §3h 를 **원리적으로 못 잰다** — `fixture-flow.js` 에
  // `coverage` 가 0회이고 어댑터는 그것을 리전 단위 `partial` 로 접는다. `setAccountSplit`(검사 12)과
  // 같은 성격의 대조군 주입구이고, 제품 경로(`T.coverage`)와 **같은 코드**를 지난다.
  const covSet = (rows) => {
    COV = Array.isArray(rows) ? rows.slice() : []
    DENY = null; denyCache.clear()
    return COV.length
  }
  // 🔴 분모(「발견한 계정」)는 계약에 없다 — `discovery.account_ids` 가 스키마에 없고 엔진 자동
  //    발견도 미구현이다. 그래서 **숫자를 하나만 낸다**(사용자 결정 2026-09-21): `B = A` 를 내면
  //    화면이 「전부 읽었다」는 거짓 안심을 준다. `T.discovery.account_ids` 가 들어오는 날 두
  //    숫자가 된다(§3h H2).
  const covAccounts = () => {
    const read = new Set(); const denied = new Set()
    for (const c of COV) {
      if (!c) continue
      if (c.status === 'ok') read.add(c.account_id)
      else if (c.status === 'denied') denied.add(c.account_id)
    }
    const d = T.discovery && Array.isArray(T.discovery.account_ids) ? T.discovery.account_ids.length : null
    return { read: read.size, denied: denied.size, found: d, rows: COV.length }
  }
  // §3h H2 **둘째 줄**(ADR 0005 D5): 지표 층의 대상 = 관측 `scope` ∩ **멤버 역할 진입 성공**.
  // 🔴 첫 줄(`covAccounts` = 구조 문서)과 합치지 마라 — 셈의 출처가 다르다(구조는 다 읽었는데 지표만
  //    막힌 계정이 한 줄에서는 「안 읽은 계정」으로 뭉개진다 · canvas-principles §3h H2).
  // 🔴 「제외」는 **진입 거부**(`sts:*` + denied)뿐이다. 진입한 뒤의 API 거부는 대상이다(역할은
  //    있다 — 그 권한 부재는 해치·지표 줄이 말한다). 진입 throttle·세션 팩토리 실패는 어느 쪽도
  //    아니므로 「확인 못함」으로 따로 센다 → 셋의 합 = scope 계정 수(조용히 빠지는 계정 0 · 불변식 ④).
  // 🔴 2층(`layer: metric`) 행은 보지 않는다 — 진입 여부는 1층이 말한다. 2층 거부를 진입으로 세면
  //    역할이 없는 계정이 「대상」이 된다.
  // `scope`·`coverage` 는 관측 스키마의 필수 필드라 실제 문서에는 늘 있다. 없으면(렌더러 옛 픽스처)
  // 숫자를 지어내지 않고 null 이다.
  const monTargets = () => {
    if (!OBS || !OBS.scope || !Array.isArray(OBS.scope.account_ids) || !Array.isArray(OBS.coverage)) return null
    const entered = new Set(); const refused = new Set()
    for (const c of OBS.coverage) {
      if (!c || c.layer !== 'describe') continue
      const api = c.reason && typeof c.reason.api === 'string' ? c.reason.api : ''
      if (c.status === 'ok') entered.add(c.account_id)
      else if (api.startsWith('sts:')) { if (c.status === 'denied') refused.add(c.account_id) }
      else if (api && !api.startsWith('nettopo:')) entered.add(c.account_id)
    }
    const out = { target: [], excluded: [], unknown: [] }
    for (const a of [...new Set(OBS.scope.account_ids)].sort()) {
      if (entered.has(a)) out.target.push(a)
      else if (refused.has(a)) out.excluded.push(a)
      else out.unknown.push(a)
    }
    return out
  }

  // ---------------- 관측(상태) 축 — ADR 0006 · 채널 배분표 §8.3 ----------------
  // 🔴 **구조 위에 얹는 레이어다.** 꺼져 있으면(기본) 그림은 이 축이 없던 때와 바이트 동일이어야
  //    한다 → 모든 분기가 `S.monitor` 아래에 있다. 기본을 켜면 검사 17·24·30·63·87 오라클이 전부
  //    흔들리고, 무엇보다 **돈이 드는 층을 사람이 켠 적 없이 보고 있다**고 화면이 주장하게 된다.
  // 🔴 **어휘는 4개뿐이다**(`live`·`up`·`down`·`none`). 다섯 번째를 만들지 마라 — 「이번엔 못
  //    읽었다」는 관측이 아니라 `coverage` 의 일이고 그것은 이미 권한 축(§3h)이 말한다(ADR 0006 §O7
  //    규칙 2). 두 어휘를 합치면 고객이 고칠 수 있는 것(권한)을 고치지 않는다.
  // 🔴 **`observations` 에 항목이 없는 것과 `none` 은 다르다**(§O7 마지막 🔴). 항목 없음 = 「그릴
  //    상태가 없다」 → 상태 층에 **아무것도 그리지 않는다.** `none` = 「AWS 가 관측 수단을
  //    주지 않는다」 → `?` 를 붙인다(aws-canvas.js `OBS_GLYPH`).
  // 🔴 **짝이 어긋난 두 파일을 겹쳐 그리지 않는다.** 계약이 이 거부를 화면에 요구한다 —
  //    `schema/observation.schema.json` `$defs.run.topology_collected_at` 의 `$comment`:
  //    「화면은 이 값이 어긋나면 겹쳐 그리기를 거부해야 한다」. 어긋난 채 겹치면 **없어진 리소스에
  //    상태가 붙고**, 더 나쁘게는 남은 카드가 「정상」으로 보인다(불변식 ④의 최악: 있는데 없다고,
  //    없는데 있다고 그린다). 값이 아예 없는 문서(옛 픽스처)는 어긋났다고 단정할 수 없으므로
  //    통과시킨다 — 「모른다」를 「틀렸다」로 바꿔 읽지 않는다.
  // 🔴 **짝은 내용 지문이 먼저다**(결정 2026-09-27). 수집 시각은 내용이 같아도 매시 바뀐다 — 시각으로만
  //    맞추면 실시간 화면이 매시 구조 빌드 직후 겹쳐 그리기를 멈춘다(실측 188: 연속 4회 수집이 `run`
  //    밖은 바이트 동일). 지문이 **양쪽에 다 있을 때만** 지문으로 판정하고, 한쪽이라도 없으면 시각으로
  //    되돌아간다 — 한쪽만 있는 지문을 「다르다」로 읽으면 배포 직전에 받은 화면 파일(지문 없음)이
  //    새 상태 문서(지문 있음)를 전부 거부한다. `by` = 무엇으로 판정했나 → 배너가 **조치를 다르게**
  //    말한다(지문 = 네트워크가 바뀌었다 → 최신 화면 파일 / 시각 = 수집기가 짝 맞는 두 파일을 굽는다).
  // 🆕 `newer` = 지문이 다를 때 **어느 쪽이 새 구조인가**(QA B P3 · 사용자 결정 2026-10-14) — 두 구조의 수집 시각을 비교한다.
  //    'obs' = 상태가 더 새 구조를 보고 있다(열어 둔 화면이 옛 구조 → «최신 화면 열기») · 'page' = 이 그림이 더 새 구조다
  //    (상태 함수가 아직 새 구조로 재지 않았다 → 기다리면 저절로 얹힌다) · null = 판정할 수 없다(옛 문장 그대로).
  //    🔴 두 경우의 조치가 정반대다 — 이 그림이 더 새로운데 「최신 화면 파일을 다시 받아라」고 하면 같은 파일을 다시 받는다(B-09).
  //    시각 판정(`by: 'time'`)에는 붙이지 않는다 — 지문이 없는 옛 파일 짝이라 고칠 주체가 수집기다(`pairFix`).
  const pairOf = (obs) => {
    const r = obs && obs.run ? obs.run : null
    const od = r ? r.topology_content_digest || null : null
    const td = T.run ? T.run.content_digest || null : null
    if (od && td) {
      const ot = Date.parse(r.topology_collected_at); const tt = Date.parse(T.run.collected_at)
      const newer = od === td || !Number.isFinite(ot) || !Number.isFinite(tt) || ot === tt ? null : ot > tt ? 'obs' : 'page'
      return { stale: od !== td, by: 'digest', newer }
    }
    const ot = r ? r.topology_collected_at || null : null
    const tt = T.run ? T.run.collected_at || null : null
    return { stale: !!(ot && tt && ot !== tt), by: ot && tt ? 'time' : null }
  }
  // 짝이 어긋났을 때 사람이 할 일. 🔴 둘을 섞지 마라 — 지문이 다른 경우에 「수집기가 다시 굽는다」고
  //    말하면 사람은 기다리고, 수집기는 이미 새 판을 구워 두었으므로 영영 안 풀린다.
  const pairFix = (p) => p.by !== 'digest'
    ? '수집기가 짝이 맞는 두 파일을 다시 만들면 해결됩니다'
    : p.newer === 'page'
      ? '이 구성도가 더 최신입니다 — 새 구성으로 상태를 다시 측정하고 있습니다(보통 몇 분) · 기다리면 자동으로 표시됩니다'
      : p.newer === 'obs' && canGoLatest()
        ? '상단 «최신 화면 열기»를 누르면 새 구성으로 그린 화면이 이 탭에 열리고 모니터링이 이어집니다'
        : '최신 화면 파일(index.html)을 다시 받아 주세요 — 최신 수집 결과로 그려지고 상태가 다시 표시됩니다'
  let OBS_PAIR = null
  let OBS_PAIRING = { stale: false, by: null }
  let OBS_STALE = false
  let PAIR_FIX = ''
  let OBSROWS = []
  // 사용률(§17.25 · ADR 0011) — 관측 문서의 `utilization[]`. 짝이 어긋난 문서 = 없음(상태 행과 같은 규율)
  let UTROWS = null
  // 문서 하나를 화면의 「지금 문서」로 앉힌다. 🔴 구운 문서(로드 때 1회)와 실시간 채널(폴링)이 **이
  //    함수 하나**를 지난다 — 채널이 짝 판정을 따로 하면 구운 문서에서 잰 거부(검사 94)가 폴링한
  //    문서에서는 미측정이 된다. 짝·행을 **한 번에** 바꾼다: 하나라도 옛 문서에 남으면 배너와 카드가
  //    서로 다른 문서를 말한다. 색인 캐시는 아래 `obsAdopt` 가 비운다(여기선 아직 선언 전이다).
  const obsPairUp = (doc) => {
    OBS = doc || null
    OBS_PAIR = OBS && OBS.run ? OBS.run.topology_collected_at || null : null
    OBS_PAIRING = pairOf(OBS)
    OBS_STALE = OBS_PAIRING.stale
    PAIR_FIX = pairFix(OBS_PAIRING)
    OBSROWS = OBS && !OBS_STALE && Array.isArray(OBS.observations) ? OBS.observations : []
    UTROWS = OBS && !OBS_STALE && Array.isArray(OBS.utilization) ? OBS.utilization : null
  }
  obsPairUp(OBS)
  // ---- 옵션형 근거 «출처»(ADR 0009 D6). 「고객이 흐름 로그·Container Insights 를 켰나」다 ----
  // 🔴 **`coverage` 어휘와 다른 축이다**(ADR 0009 D6 의 🔴 · §3h). 배너의 `deny` 줄(②)은 「우리가
  //    못 읽었다 → 스택 B 를 배포하라」이고 이쪽(③)은 「고객이 안 켰다 → 켜면 더 정확해진다」다.
  //    합치면 사람은 둘 다 틀린 조치를 한다(권한을 줘도 알약이 안 내려가고, 흐름 로그를 켜도
  //    해치가 안 벗겨진다).
  // 🔴 **빈 배열은 「전부 꺼짐」이 아니다** — 탐지를 아예 하지 않은 문서다(`sources.py`
  //    `SourceScan.not_scanned`, 스키마 `$comment`). 그때 화면은 **아무 말도 하지 않는다**:
  //    꺼졌다고 말할 근거가 없는데 「지표 기반만」이라고 적으면 그것이 곧 ④(모름을 꺼짐으로)다.
  // 🔴 **매번 다시 읽는다**(모듈 초기화 때 굳히지 않는다) — 굳히면 문서를 갈아 끼우는 경로가
  //    화면에 닿지 못해 하네스가 네 상태 중 한 가지만 잰다(= 나머지는 미측정 — 검증 규칙).
  const srcRows = () => (OBS && Array.isArray(OBS.sources) ? OBS.sources.filter((s) => s && s.kind) : [])
  // 🔴 `unit` 은 **리소스 종류 축이 아니다**(ADR 0009 D2-3) — `TYPE_LABEL` 을 재사용하면 축이
  //    섞이고, 종류에 없는 단위(`ecs_cluster`·`resolver_endpoint`)가 raw 로 새어 나온다.
  const SRC_LABEL = {
    vpc_flow_log: 'VPC 흐름 로그', tgw_flow_log: 'TGW 흐름 로그',
    log_delivery: '로그 전달 배선', container_insights: 'Container Insights',
    eks_network_policy_log: 'EKS 네트워크 정책 로그', ec2_detailed_monitoring: 'EC2 상세 모니터링',
    vpn_tunnel_bgp_log: 'VPN 터널·BGP 로그', resolver_target_ns_metrics: 'Resolver 대상 NS 지표'
  }
  const SRC_UNIT = {
    vpc: 'VPC', transit_gateway: 'TGW', ecs_cluster: 'ECS 클러스터',
    eks_cluster: 'EKS 클러스터', ec2_instance: 'EC2 인스턴스',
    vpn_connection: 'VPN 연결', resolver_endpoint: 'Resolver 엔드포인트'
  }
  const srcLabel = (s) => (SRC_LABEL[s.kind] || s.kind) +
    // 「3/7 VPC」는 켜진 범위다. 🔴 없으면 **적지 않는다** — 분모를 못 믿거나 못 읽은 자리다
    //  (「0/7」은 「7개 다 꺼져 있다」로 읽힌다 → 엔진이 그때 `scope` 를 지운다).
    (s.scope ? ` ${s.scope.enabled_ids}/${s.scope.total_ids} ${SRC_UNIT[s.scope.unit] || s.scope.unit}` : '')
  // ---- 흐름 로그 한 개 단위(ADR 0009 D5 추가 표 · §3k K3 추가 표 · 대장 107·108) ----
  const flowLogsOf = (s) => (s && Array.isArray(s.resources) ? s.resources.filter((r) => r && r.id) : [])
  const unreadLog = (r) => r.used === false && r.why_code !== 'duplicate_covered'
  // 일부만 읽음 — 계약이 `used: true` 에 허용하는 코드는 `capped`·`throttled`·`subnet_unattributable` 셋뿐이다.
  const partialLog = (r) => r.used === true && !!r.why_code
  // §3k K3 추가 표 그대로. 🔴 「도구가 아직 안 읽는다」(앞 두 줄)와 「고객 설정·권한」은 **다른 문장**이다 — 앞의 것은
  //    고객이 할 일이 없다. 🔴 `access_denied` 는 파라미터 이름을 적지 않는다(U9 전) — 엔진 문장이 목적지별 이름을
  //    담고 있고 그것은 줄의 `title` 로 간다. 코드는 화면 글자로 내지 않는다(표에 없으면 「읽지 못했다」).
  const FLOW_WHY = {
    format_parquet: 'Parquet 형식은 아직 읽지 않습니다',
    reader_pending_tgw: '이 도구는 아직 TGW 흐름 로그를 읽지 않습니다',
    format_v2: '이 로그에 경로 필드가 없습니다 — 사용자 지정 형식이 필요합니다',
    fields_missing: '이 로그에 경로 필드가 없습니다 — 사용자 지정 형식이 필요합니다',
    access_denied: '읽기 권한이 없습니다 — 스택 파라미터로 이 로그 읽기를 허용하세요',
    kms: '로그를 푸는 키 권한이 없습니다 — 스택 파라미터로 그 키의 복호화를 허용하세요(S3 버킷)',
    log_class_infrequent: '드문 접근 등급 로그 그룹이라 읽을 수 없습니다',
    destination_firehose: 'Firehose 로 보내는 로그는 읽을 수 없습니다',
    traffic_reject_only: '거부 기록만 남기는 로그입니다',
    not_found: '로그 목적지가 없습니다',
    throttled: 'AWS 가 요청량 초과로 거절했습니다',
    capped: '읽기 한도에 닿아 일부만 읽었습니다',
    subnet_unattributable: '서브넷을 구분할 필드가 없습니다',
    shared_participant: '공유 VPC 의 다른 계정 로그입니다',
    format_mismatch: '로그 형식이 설정과 다릅니다',
    read_error: '읽는 중 예상하지 못한 오류가 발생했습니다'
  }
  const flowWhy = (c) => FLOW_WHY[c] || '읽지 못했습니다'
  const flowLogTitle = (r) => [r.why_code, r.why, r.detail].filter(Boolean).join('\n')
  // 캔버스가 아는 이름 → 없으면 id 의 마지막 조각(계정·리전 접두는 화면에 내지 않는다).
  const nameOfId = (id) => { const n = id ? byId.get(id) : null; return (n && n.name) || String(id || '').split('/').pop() }
  const srcStats = () => {
    const SRC = srcRows()
    const by = (f) => SRC.filter(f).sort((a, b) => a.kind.localeCompare(b.kind))
    // 🔴 `used` 는 `state` 와 **독립**이다(ADR 0009 D2): 켜져 있어도 우리가 못 읽을 수 있고
    //    (버킷 정책·SSE-KMS CMK·`logGroupClass`), 그때 할 말은 「꺼져 있다」가 아니다.
    const used = by((s) => s.state === 'on' && s.used)
    // 🔴 「못 읽음」은 **종류 안의 로그 하나**까지 본다(ADR 0009 D6 「세는 법」 · 대장 108). 188 처럼 동부 VPC 로그는
    //    읽고 서울 VPC 로그(Parquet)는 못 읽은 계정에서 종류만 보면 VPC 흐름 로그는 「사용」뿐이라 못 읽은 로그가
    //    알약에서 사라진다 → 한 종류가 **두 수에 같이** 들어간다. 일부만 읽은 로그(`used: true` + 코드)는 읽은 기록을
    //    판정에 썼으므로 세지 않고, `duplicate_covered` 는 경고가 아니다(더 나은 로그를 읽었다).
    const unread = by((s) => s.state === 'on' && (!s.used || flowLogsOf(s).some(unreadLog)))
    const unknown = by((s) => s.state === 'unknown')
    // 🔴 **분모 0 은 「꺼짐」이 아니라 「해당 자원이 없다」다**(실계정 실측 2026-09-25: 개발
    //    계정에 VPN 연결·Resolver 엔드포인트가 0개인데 그 근거가 「꺼져 있다」로 적혔다).
    //    합쳐 두면 화면이 **없는 자원을 켜라고** 권고한다 — 고객이 따를 수 없는 조치다.
    //    엔진은 `scope.total_ids` 로 이 둘을 이미 구분해 준다(ADR 0009 D2 `scope`).
    const na = by((s) => s.state === 'off' && s.scope && !s.scope.total_ids)
    const off = by((s) => s.state === 'off' && !(s.scope && !s.scope.total_ids))
    return {
      rows: SRC.length, used, unread, unknown, off, na,
      // 알약은 **1개**다(ADR 0009 D6) → 우선순위가 필요하다. 「켜 뒀는데 우리가 못 읽었다」가
      // 가장 먼저다 — 그것을 안 말하면 고객은 자기가 켠 근거가 이미 반영됐다고 믿는다.
      // 🔴 단 **「사용」을 가리지 않는다**(2026-09-28 추가) — 둘 다 있으면 한 알약에 둘 다 적는다(`mixed`).
      //    못 읽음만 보이면 사람은 「흐름 로그가 하나도 안 쓰였다」로 읽고 초록 선의 근거를 의심한다.
      pill: unread.length ? (used.length ? 'mixed' : 'unread') : used.length ? 'used' : unknown.length ? 'unknown' : 'off'
    }
  }
  let obsById = null
  let obsEdgeById = null
  const obsCache = new Map()
  // 하나의 `<g>` 가 **N개 모델 노드를 대표**할 수 있다(`members` · `hiddenIds`). 그때 나쁜 쪽이
  // 이긴다(불변식 ④: 과보고가 침묵보다 낫다). 순위를 바꾸면 접힌 장애가 조용해진다.
  const OBS_RANK = { down: 4, live: 3, up: 2, none: 1 }
  const obsIndex = () => {
    if (obsById) return obsById
    obsById = new Map()
    // 🔴 `target_kind` 를 본다. 엣지 관측을 노드 id 공간에 섞으면 같은 native id 를 쓰는 노드가
    //    남의 상태를 입는다. (실측 2026-09-24: 이 스냅샷의 엣지 관측 157건은 전부 캔버스가
    //    **선을 그리지 않는** 관계 3종이라 앉을 자리가 없다 — 그 사실은 배너가 말한다.)
    for (const o of OBSROWS) if (o && o.target_kind === 'node') obsById.set(o.target_id, o)
    return obsById
  }
  const obsRepIds = (n, b) => {
    const ids = [n.id]
    if (n.members) for (const m of n.members) ids.push(m.id)
    if (b && b.hiddenIds) for (const id of b.hiddenIds) ids.push(id)
    return ids
  }
  // 반환값은 **관측 문서의 행 그대로**다(사본 아님 — 읽기만 한다). `null` = 「이 대상에는 관측
  // 항목이 없다」이고 그것은 어휘가 아니라 **부재**다. 부르는 쪽이 둘을 갈라야 한다.
  const obsOf = (n, b) => {
    if (!n || !obsIndex().size) return null
    const key = n.id + '|' + (b && b.hiddenIds && b.hiddenIds.length ? b.hiddenIds.length : 0)
    if (obsCache.has(key)) return obsCache.get(key)
    let best = null
    for (const id of obsRepIds(n, b)) {
      // 과거의 판정은 여기서 갈아 끼우지 않는다 — 오른쪽 비교 칸이 **그때 판정으로 만든 문서**를 받는다(§3j J2).
      const o = obsIndex().get(id)
      if (o && (!best || (OBS_RANK[o.status] || 0) > (OBS_RANK[best.status] || 0))) best = o
    }
    obsCache.set(key, best)
    return best
  }
  // ---- 모니터링 이상 목록의 갈래(§17.26 C-8 · 검사 172) — 「!」 상자의 절과 검색 `상태:` 가 **이 한 함수**를 쓴다 ----
  //   🔴 세 갈래를 합치지 않는다(§3h H1 · 판정 순서 「모름」→「고장」): `down` = 이상 · `down` 아님 + `partial` = 일부 못 읽음 ·
  //      `none`(지우는 중 제외 — 「삭제 중」은 AWS 가 정확히 말한 것이다) = 관측 불가. 행이 없으면 `null`(부재 — 갈래가 아니다).
  //   🔴 사용률(「멈춤」 배지)은 보지 않는다 — 사용률 축은 상태를 바꾸지 않는다(C-8). 판정 = 관측 행 그대로.
  //   묶음(`×N`)·대표 상자를 거치지 않는다(`obsOf` 아님) — 목록은 멤버마다 한 줄이다.
  const monOn = () => !CMP && !!S.monitor && !OBS_STALE && obsIndex().size > 0
  const monKindOf = (o) => (!o ? null : o.status === 'down' ? 'down' : o.status === 'none' ? (o.deleting === true ? null : 'none') : o.partial ? 'partial' : null)
  const monKind = (id) => (monOn() ? monKindOf(obsIndex().get(id)) : null)
  // 하네스 주입구 — `covSet`(검사 92)과 같은 성격이다. 픽스처에는 `window.OBS` 가 **없으므로**
  // 이것 없이는 `down`·`none` 경로를 원리적으로 못 잰다(실계정 스냅샷에도 `down` 이 0건이다).
  // 제품 경로(`window.OBS.observations`)와 **같은 코드**를 지난다.
  const obsSet = (rows) => {
    OBSROWS = Array.isArray(rows) ? rows.slice() : []
    obsById = null; obsEdgeById = null; obsCache.clear()
    return OBSROWS.length
  }
  // 실시간 채널이 받은 문서(또는 `null` = 얹을 문서 없음)를 앉힌다. 짝 판정은 `obsPairUp` 이 한다.
  const obsAdopt = (doc) => {
    obsPairUp(doc)
    obsById = null; obsEdgeById = null; obsCache.clear()
    return OBSROWS.length
  }
  // 엣지 관측은 **노드와 다른 id 공간**(엔진: `"<edge_type>|<source>|<target>"`)이라 별도 색인이다.
  // 섞으면 같은 native id 를 쓰는 노드가 남의 상태를 입는다(`obsIndex` 의 🔴 와 같은 이유).
  const obsEdgeIndex = () => {
    if (obsEdgeById) return obsEdgeById
    obsEdgeById = new Map()
    for (const o of OBSROWS) {
      if (!o || o.target_kind !== 'edge') continue
      const p = obsEdgeById.get(o.target_id)
      if (!p || (OBS_RANK[o.status] || 0) > (OBS_RANK[p.status] || 0)) obsEdgeById.set(o.target_id, o)
    }
    return obsEdgeById
  }
  // 이 선을 말하는 **엣지 관측 행 한 개**(여러 개면 상태가 가장 센 것). 🔴 흐름 판정(`wireObs`)과
  // **같은 행**을 골라야 한다 — 선은 초록이 아닌데 패널이 다른 행의 근거를 읽어 주면 화면 안에서
  // 두 말이 서로 어긋난다. 그래서 고르는 규칙을 한 곳에 두고 둘이 같이 쓴다.
  const wireRow = (w) => {
    if (!w || !w.obsIds || !w.obsIds.length || !obsEdgeIndex().size) return null
    let best = null
    for (const id of w.obsIds) {
      const o = subnetRow(obsEdgeIndex().get(id), w.obsFrom && w.obsFrom.get(id))
      if (o && (!best || (OBS_RANK[o.status] || 0) > (OBS_RANK[best.status] || 0))) best = o
    }
    return best
  }
  // 🔴 **흐름 로그 근거는 서브넷별이다**(§6 규칙 10 · 대장 107 · 검사 119). 라우트 행은 라우트 테이블 단위라
  //    `by_subnet[]` 을 달고 오면 **이 선을 실어 온 서브넷**(`from`)의 `live` 항목만 본다 — 같은 테이블을 쓰는
  //    옆 서브넷의 왕복 기록으로 이 선이 초록이 되면 그것이 곧 거짓 초록이다. 게이트 셋(규칙 10 ⓐ·ⓒ):
  //    대상이 IGW·피어링인 라우트만(엔진이 그 둘에만 쓴다 — NAT·TGW 선에 얹힌 항목은 계약 밖이다) ·
  //    1층이 막힘이면 보지 않는다(1층이 이긴다) · 항목이 없으면 행 그대로. 돌려주는 것은 **사본**이다 —
  //    관측 문서의 행은 읽기만 한다(`obsOf` 와 같은 규율). `subnet_id` 는 패널이 「어느 서브넷」을 적는 데만 쓴다.
  const ROUTE_FL = /^route\|[^|]+\|(internet_gateway|vpc_peering_connection)\//
  const subnetRow = (o, from) => {
    if (!o || !Array.isArray(o.by_subnet) || o.status === 'down' || !ROUTE_FL.test(o.target_id)) return o
    const it = o.by_subnet.find((x) => x && x.status === 'live' && !!from && from.includes(x.subnet_id))
    if (it) return { ...o, status: 'live', evidence: it.evidence, by_subnet: undefined, subnet_id: it.subnet_id }
    // 🆕 W3③ 「재 봤더니 흐름 없음」(§6 규칙 10 ⓔ · ADR 0008 D4c ④) — `up` + `flow_log` + 점 0 항목. 🔴 이 선을 실어 온
    //    서브넷 **모두**에 그 항목이 있을 때만이다 — 접힌 선에서 한 서브넷의 침묵을 선 전체의 침묵으로 말하면 거짓이다.
    //    상태는 행 그대로(초록도 빨강도 아니다) · 근거만 그 항목으로 바꾼다. 첫 서브넷(정렬)이 「본 곳」이다.
    const quiet = (sid) => o.by_subnet.find((x) => x && x.subnet_id === sid && x.status === 'up' && x.evidence &&
      x.evidence.metric === 'flow_log' && x.evidence.datapoints === 0)
    const subs = (from || []).filter((sid) => byId.has(sid) && byId.get(sid).type === 'subnet').sort()
    if (subs.length && subs.every(quiet)) {
      return { ...o, evidence: quiet(subs[0]).evidence, by_subnet: undefined, subnet_id: subs[0], quiet: subs }
    }
    return o
  }
  // 근거 없는 라우트 선(IGW·피어링)의 패널 문장 — 세 문장 중 하나(§6 규칙 10 ⓓ). 🔴 흐름 로그는 선을 빨갛게도,
  //    「흐름 없음」으로도 만들지 않는다 — 「기록이 없다」는 「흐름이 없다」가 아니다(짧은 창·집계 간격·샘플 없음).
  //    재료 = 그 서브넷 또는 그 VPC 에 **붙은** VPC 흐름 로그. ENI 단위 로그는 어느 서브넷을 덮는지 모른다 → 쓰지 않는다.
  //    우선순위: 하나라도 다 읽었으면 「읽었다」 > 일부만 읽은 것 > 못 읽은 것. 붙은 로그가 없으면 `null`(말할 재료가 없다).
  const FLOW_READ = '읽었습니다 — 이 창에 이 서브넷의 왕복 기록 없음(흐름이 없다는 뜻은 아닙니다)'
  const flowLogNote = (w) => {
    if (!S.monitor || !w || !w.obsIds || !w.obsFrom) return null
    const row = wireRow(w)
    if (!row || row.status === 'live' || row.status === 'down' || row.quiet) return null
    const at = new Set()
    for (const id of w.obsIds) {
      const o = obsEdgeIndex().get(id)
      if (!o || o.status === 'down' || !ROUTE_FL.test(o.target_id)) continue
      for (const sid of w.obsFrom.get(id) || []) {
        const n = byId.get(sid)
        if (!n || n.type !== 'subnet') continue
        at.add(sid); if (n.parent_id) at.add(n.parent_id)
      }
    }
    if (!at.size) return null
    const logs = flowLogsOf(srcRows().find((s) => s.kind === 'vpc_flow_log'))
      .filter((r) => r.attached_to && at.has(r.attached_to) && r.why_code !== 'duplicate_covered')
      .sort((a, b) => a.id.localeCompare(b.id))
    if (!logs.length) return null
    const done = logs.find((r) => r.used === true && !r.why_code)
    if (done) return { t: FLOW_READ, title: done.id }
    const r = logs.find(partialLog) || logs[0]
    return { t: `${partialLog(r) ? '일부만 읽음' : '켜져 있지만 못 읽음'} — ${flowWhy(r.why_code)}`,
      title: `${r.id}\n${flowLogTitle(r)}` }
  }
  // 🔴 「이 구간을 **쟀나**」의 유일한 판별식. 계약상 `live` 는 `evidence.metric` + `datapoints ≥ 1`
  //    을 달고 오므로(ADR 0006), 지표를 보고서도 흐름이 아니라고 말한 행과 **구조 조회만 한** 행이
  //    이 한 줄로 갈린다. 두 곳(`wireObs`·`endsOnly`)이 각자 쓰면 한쪽만 고쳐져 화면이 자기와
  //    모순된 말을 한다 — 「측정됐다」와 「아무도 재지 않았다」를 같은 선에 동시에 붙인다.
  const metricSeen = (o) => !!(o && o.evidence && o.evidence.metric && (o.evidence.datapoints || 0) >= 1)
  // 🔴 **선이 초록으로 흐를지**를 정하는 유일한 곳(§6.10 v4). 근거를 두 단으로 쓰고 **어느 단을
  //    썼는지 돌려준다** — ② 는 «선 자신의 관측이 아니라 종단 자원의 관측»이라 추론이고, 추론을
  //    사실처럼 그리면 불변식 ③(렌더러가 사실을 발명하지 않는다)이 깨진다. 배너가 그 수를 말한다.
  //    ① 선 자신의 관측: 어댑터가 실어 보낸 엔진 엣지 id(`w.obsIds`) ↔ 엣지 관측 행.
  //    ② 없으면 종단 상자의 `live`. `up` 은 승격하지 않는다 — 「describe 가 됐다」는 트래픽이
  //       흐른다는 뜻이 아니다. `down` 도 승격하지 않는다(적색은 카드 전용 어휘다, §8.3).
  const wireObs = (w, L) => {
    if (!S.monitor || !w) return null
    // 🔵 **종류 게이트는 ① 이 아니라 ② 에만 걸린다**(결함 수정 2026-09-25 · ADR 0008 D8-1).
    //    이전 판은 여기서 `w.kind !== 'flow'` 를 잘라 **두 근거를 같이** 막았고, 그 결과가
    //    「측정으로 초록이 된 선 0개」였다: 엔진이 지표 근거를 붙이는 관계는 부착·엔드포인트 연결·
    //    LB 배치 3종인데(`engine/src/nettopo/metrics.py:181-217`) 그 3종의 캔버스 종류는
    //    `attachment`·`service`·`logical` 이라 전부 여기서 걸렸고, 남은 `flow` 선은 라우트
    //    재투영뿐이며 `route` 에는 지표 probe 가 없다 → **초록이 될 수 있는 선은 지표를 못 받고,
    //    지표를 받는 선은 초록이 될 수 없었다.**
    // 🔴 왜 ① 은 종류를 가려도 되는 근거가 없나: 부착 구간이 지표로 `live` 라는 것은
    //    **그 부착을 지난 트래픽을 직접 센 것**이다(그 지표의 차원이 구간을 이름 댄다 — D8-1 · 엔진이
    //    「들어옴」과 「통과」를 가른 뒤다 — ADR 0008 정정 1-2). 이것을
    //    「구조를 말하는 선이니 흐를 수 없다」로 막으면 측정을 버리는 것이고, 불변식 ③ 이 금지하는
    //    「렌더러가 사실을 발명한다」의 **반대 방향 오답**(있는 사실을 지운다)이다.
    // 🔴 반면 ② 는 종류를 가려야 한다: 종단이 `live` 라는 이유로 부착·피어링선을 흐르게 하면
    //    「이 부착으로 트래픽이 지나간다」는 **없는 사실**이 된다(ADR 0008 D4 — 인스턴스 지표는
    //    «어디로» 갔는지 말하지 않는다). 그래서 게이트를 ② 바로 앞으로 내렸다.
    // 🔴 흐름을 말할 수 있는 근거는 **지표**뿐이다(계약: `live` ⇒ `evidence.metric` +
    //    `datapoints ≥ 1`) → `live` 만 초록이고 `up`·`down`·`none` 은 전부 초록이 아니다.
    // 🔴 여기서 `down`·「지표를 봤다」를 따로 걸러 냈던 줄을 **지웠다**(2026-09-26): 그것은 ② 로
    //    내려보낼지 말지를 정하는 문이었고, ② 가 폐기된 뒤에는 걸러도 안 걸러도 `null` 이라
    //    **어떤 브레이크도 그 줄을 깨서 화면을 바꿀 수 없다**(= 실패할 수 없는 어서션, §4).
    //    그 구별은 사라진 게 아니라 `endsOnly` 로 **옮겨 갔다** — 「재지 못했다」와 「재서 아니었다」는
    //    사람에게 하는 말이 다르고, 지금은 그 차이가 거절 계수와 선 패널 문장으로 보인다.
    const best = wireRow(w)
    if (best && best.status === 'live') return { status: 'live', via: 'edge', row: best }
    // ② **종단 추론은 폐기됐다**(ADR 0008 · S2 · 2026-09-26). 양 끝 자원이 흐른다는 사실은
    //    「그 트래픽이 **이 구간을** 지났다」를 뜻하지 않는다 — 인스턴스 지표는 어디로 갔는지
    //    말하지 않는다(ADR 0008 D4). 사람이 겪던 일: 아무도 재지 않은 구간이 초록으로 흐르고,
    //    고객은 그것을 「이 경로로 트래픽이 흐르는 것을 확인했다」로 읽는다.
    //    🔴 그 규칙은 승인 대장에 없었다 — 정본 문구·검사·구현이 같은 커밋(`1ce26b9`)에서 같이
    //    생겼고, 검사가 지켜 준 것은 「매번 같은 그림」이지 「사람이 좋다고 한 규칙」이 아니다.
    //    대신 **거절한 수를 센다**(`endsOnly`) — 배너가 「칠하지 않았다」를 직접 말한다.
    return null
  }
  // 옛 ② 가 초록으로 칠했을 선 = 「양 끝만 흐르는 흐름선」. 🔴 **0 으로 지우지 않고 센다.**
  // 지우면 배너가 「초록 N개」만 말하고, 왜 나머지가 초록이 아닌지는 화면에 없다(불변식 ④).
  // 이 수가 0 이면 「종단 추론을 안 한다」는 어서션은 **실패할 수 없다** = 미측정이다.
  const endsOnly = (w, L) => {
    if (!S.monitor || !w || w.kind !== 'flow') return false
    if (!L || !L.boxes) return false
    if (wireObs(w, L)) return false
    // 🔴 **「재지 못했다」와 「재서 아니었다」를 가른다.** 옛 근거 ② 는 지표를 본 행(`down`·0점)이
    //    있으면 **거기까지 내려오지도 못했다** — 그 선은 측정된 선이다. 여기서 같이 세면 화면이
    //    「이 구간을 아무도 재지 않았다」를 **측정된 선에** 붙여 거짓을 말하고, 하네스 쪽 오라클과도
    //    수가 어긋난다(오라클은 그 두 경우를 `metBlock`·`rawFall` 로 따로 센다).
    const row = wireRow(w)
    if (row && (row.status === 'down' || metricSeen(row))) return false
    // 🔴 「재서 흐름 없음」(A0)도 잰 선이다 — 「이 구간 자신을 잰 근거가 없다」를 붙이면 같은 창의 「판정 등급」 줄
    //    (「이 구간 자신을 창 내내 쟀고」)과 정면으로 어긋난다(W5 이식 때 발견 · 두 화면 공통)
    if (gradeOf(row) === 'A0') return false
    for (const bid of [w.a, w.b]) {
      const b = L.boxes.get(bid)
      const o = b ? obsOf(b.node, b) : null
      if (o && o.status === 'live') return true
    }
    return false
  }
  // 🔴 **판정 등급 5개 + A0**(ADR 0008 D1 · A0 = 「D1 개정」 2026-10-02). 상태 어휘를 늘리지 않고 그 «위에» 얹는다 — `status` 는
  //    여전히 `live`·`up`·`down`·`none` 4개이고, 등급은 「무엇을 근거로 그렇게 말하나」다.
  //    등급을 `status` 로 분기하지 않고 **행이 들고 온 키로** 정한다(`observed_on`·`path_ok`):
  //    추론을 데이터에 적게 한 뒤 그것만 읽는 것이 불변식 ③ 을 지키는 유일한 방법이다.
  const GRADE = {
    A: { badge: '직접 측정', why: '이 구간 자체의 지표로 흐름을 측정했습니다' },
    B: { badge: '연역', why: '인접 리소스의 지표가 이 구간을 지나야만 나올 수 있는 값을 보였습니다' },
    C: { badge: '경로 성립', why: '흐름은 측정하지 못했지만 흐를 수 있는 조건을 모두 확인했고 모두 충족했습니다 — 흐른다는 뜻은 아닙니다' },
    D: { badge: '차단', why: '흐를 수 없는 조건을 확인했거나 차단 지표에 값이 있습니다' },
    E: { badge: '판단 불가', why: '판정에 필요한 정보 중 하나 이상을 읽지 못했습니다' },
    // 🆕 W3④(ADR 0008 「D1 개정」 2026-10-02): 쟀고 0 — 옛 판은 이 행을 E 로 떨궈 근거 줄(「흐름 없음」)과 반대말을 했다.
    A0: { badge: '측정: 흐름 없음', why: '이 구간 자체를 창 내내 측정했고 지나간 트래픽이 없었습니다 — 끊겼다는 뜻은 아닙니다' }
  }
  // 🔴 인자는 **행 하나**다. 「어느 대상으로 보나」를 밖에서 받으면 같은 행이 호출자마다 다른 등급을
  //    받을 수 있고, 그건 계약이 아니라 호출 습관이 등급을 정하는 것이다. 엔진은 `observed_on` 을
  //    **`target_id` 와 다를 때만** 적는다(`engine/src/nettopo/observe.py:319`) → 키의 **존재**가
  //    곧 B 다. 그래도 값 비교를 남긴 이유: 계약이 바뀌어 항상 적히게 되면 존재만 보는 판은 A 를
  //    전멸시키고 그 사실은 화면에서 안 보인다(조용한 오답보다 중복 조건이 싸다).
  const gradeOf = (row) => {
    if (!row) return 'E'
    const on = row.evidence && row.evidence.observed_on
    if (row.status === 'live') return (on && on !== row.target_id) ? 'B' : 'A'
    if (row.status === 'down') return 'D'
    // 🔴 `path_ok` 는 `true` 만 쓴다 — **부재가 E** 다(「못 읽었거나 계산하지 않았다」 · D8-3).
    //    `!== false` 로 쓰면 키가 없는 행 전부가 C 로 승격돼 회색 실선이 근거를 얻는다.
    if (row.status === 'up' && row.path_ok === true) return 'C'
    // 🔴 A0 = **이 구간 자신을** 쟀고 0(`datapoints === 0` — `null` 은 안 쟀음이라 E 그대로). 인접 자원의 0 은 E 다:
    //    「인접 > 0 ⇒ 지났다」(B)의 역은 성립하지 않는다. 흐름 로그 「흐름 없음」은 `observed_on` 이 출발 서브넷 자신이라 예외.
    const ev = row.evidence
    if (row.status === 'up' && ev && ev.metric && ev.datapoints === 0 &&
      (ev.metric === 'flow_log' || !on || on === row.target_id)) return 'A0'
    return 'E'
  }
  // 「어느 지표를 얼마나 오래 재서 몇 점을 봤나」 한 줄. 🔴 **못 쟀음(`null`)과 재서 0 은 다르다** —
  // 합치면 「값이 없다」가 「흐름이 없다」로 읽힌다(ADR 0006 결정 3).
  // 흐름 로그 근거 한 줄(§6 규칙 10 · ADR 0008 「D4b 개정」). 🔴 점 수가 아니라 **왕복 기록 수**다 — 나간 기록과
  //   같은 ENI 로 돌아온 답을 짝지은 수라서 「데이터포인트」로 적으면 지표처럼 읽힌다. 창은 분으로 적는다.
  //   어느 서브넷에서 봤나: 라우트 선은 그 선의 서브넷(`subnetRow` 가 붙인 것), 카드·피어링 행은 `observed_on`.
  //   🆕 W3③: 「흐름 없음」 항목(`subnetRow` 가 `quiet` 를 붙인 것)은 그 문장 · TGW 어태치먼트 행은 「TGW 흐름 로그」.
  const FLOW_QUIET = '이 창에 흐름 없음 — 이 서브넷의 모든 ENI 가 기록 없음(NODATA)'
  const flowLogLine = (ev, subnetId, row) => {
    const ws = ev.window_seconds
    const n = ev.datapoints === null || ev.datapoints === undefined ? '?' : ev.datapoints
    const tgw = !!(row && /^attachment\|transit_gateway_attachment\//.test(row.target_id || ''))
    const bits = [row && row.quiet ? FLOW_QUIET : `${tgw ? 'TGW ' : ''}흐름 로그 왕복 기록 ${n}건`]
    if (ws) bits.push(ws % 60 ? `창 ${ws}초` : `창 ${ws / 60}분`)
    const on = row && row.quiet ? row.quiet : [subnetId || ev.observed_on].filter(Boolean)
    if (on.length) bits.push(`본 곳 ${on.map(nameOfId).join(', ')}`)
    return bits.join(' · ')
  }
  const evidenceLine = (row) => {
    const ev = (row && row.evidence) || null
    if (!ev) return '근거 항목 없음'
    if (ev.metric === 'flow_log') return flowLogLine(ev, row.subnet_id, row)
    const bits = [ev.api]
    if (ev.metric) bits.push(`지표 ${ev.metric}`)
    if (ev.window_seconds) bits.push(`창 ${ev.window_seconds}초`)
    // 🔴 점 수는 **지표 근거에만** 말한다(#31·#32 리뷰 후속). 구성·상태 API 로 판정한 행(`metric: null`
    //    — 경로 성립·헬스·상태 조회)에 「못 셈(안 쟀다)」을 붙이면 **읽은 것을 안 쟀다고** 말한다.
    if (!ev.metric) bits.push('지표 아님 — API 응답으로 판정')
    else if (ev.datapoints === null || ev.datapoints === undefined) bits.push('0 아닌 점 못 셈(측정하지 않음)')
    else bits.push(`0 아닌 점 ${ev.datapoints}개`)
    if (ev.observed_on) bits.push(`잰 대상 ${ev.observed_on}`)
    return bits.join(' · ')
  }
  // 🔴 **캔버스에 글자로 찍는 등급은 B·C·D 뿐이다**(S2-3 · 사용자 배지 문구 승인 2026-09-25).
  //    A 와 E 를 뺀 것은 게을러서가 아니라 **둘은 캔버스에 이미 채널이 있기 때문**이다:
  //    ⓐ A(직접 측정) = 초록 굵은 선 자신이다. 초록 옆에 「직접 측정」을 또 적으면 같은 사실이
  //      두 채널을 쓰고, 정작 초록인데 **연역**(B)인 선과 눈으로 구별되지 않는다 — 배지를 B 에만
  //      붙이면 「초록 + 배지 없음 = 이 구간을 직접 쟀다」가 화면에서 읽힌다.
  //    ⓑ E(판단 불가) = **아무 표시도 없음**이다. 실계정에서 E 는 선 153개 중 138개다(실측
  //      2026-09-26) → 붙이면 캔버스가 「판단 불가」 글자로 덮여 정작 B·C·D 를 못 찾는다.
  //      「왜 이 선은 아무 말이 없나」의 답은 선 패널(§S2-4)과 배너가 글로 댄다.
  // 🔴 **색은 쓰지 않는다**(§8.3 규칙 1). D(차단)를 적색으로 칠하고 싶어지지만 적색은 카드 전용
  //    어휘이고, 선의 색 채널은 「흐름을 쟀나」 하나에 이미 묶여 있다. 배지는 `C.dim` 한 색이다.
  // 🔴 드롭은 **등급과 따로** 붙는다 — 초록(A)인데 버리는 선이 있을 수 있고, 그 사실이 등급
  //    필터에 걸려 사라지면 화면은 「흐른다」만 말하고 「일부는 버려진다」를 안 말한다(불변식 ④).
  // LB → 대상 선(§17.27 · 관측 1.8.0)의 등록 헬스 — 그 선에 실린 관측 행들의 `targets` 합(묶음 칸으로 합친 선 = 행 여럿).
  //   `null` = 헬스를 말하는 행이 없다(다른 종류의 선 · `targets` 도 사유도 없는 행 — 그때는 등급 배지 규칙 그대로) ·
  //   `{ unread: true }` = `targets` 없이 **일부 못 읽음**인 행이 있다(엔진 `_observe_lb_target` — 헬스를 못 읽었다 · 수를 짓지 않는다).
  const TGT3 = { total: 1, healthy: 1, unhealthy: 1 }
  const lineTargets = (w) => {
    if (!w || !w.obsIds || !w.obsIds.length || !obsEdgeIndex().size) return null
    let rows = 0; const t = { total: 0, healthy: 0, unhealthy: 0 }; let unread = false; let all = null
    for (const id of w.obsIds) {
      if (!/^load_balancer_target\|/.test(String(id))) continue
      const o = obsEdgeIndex().get(id); if (!o) continue
      if (o.targets) {
        rows++; t.total += o.targets.total; t.healthy += o.targets.healthy; t.unhealthy += o.targets.unhealthy
        // 그 밖의 정수 칸(1.15.0 · #137 다섯 갈래)도 더한다 — 이름은 여기서 모른다(글은 `NT_AWS.targetsWord` 한 벌)
        const ks = Object.keys(o.targets).filter((k) => !(k in TGT3) && Number.isInteger(o.targets[k]))
        for (const k of ks) t[k] = (t[k] || 0) + o.targets[k]
        all = all ? all.filter((k) => ks.includes(k)) : ks
      } else if (o.partial) unread = true
    }
    if (unread) return { unread: true }
    if (!rows) return null
    // 🔴 갈래는 **모든 행에 있을 때만** 남긴다 — 옛 문서(≤1.13.0) 행이 하나라도 섞인 합친 선에서 일부 행의 갈래만 적으면
    //    나머지 행의 「정상도 비정상도 아닌」 대상이 표에 없는 상태(「그 밖 N개」)로 읽힌다 → 지우면 옛 대체 문구(셋 다 말한다)가 된다
    for (const k of Object.keys(t)) if (!(k in TGT3) && !all.includes(k)) delete t[k]
    return t
  }
  const wireBadge = (w) => {
    // 오른쪽 비교 칸(§3j J2)에는 붙이지 않는다 — 등급은 **지금** 근거로 매긴 값이라 그때 그림에 얹으면 거짓이다.
    if (CMP || !S.monitor || OBS_STALE) return null
    const row = wireRow(w)
    const g = gradeOf(row)
    // LB → 대상 선(§17.27) = 등급 글자 대신 등록 헬스(이 선에는 흐름 지표가 없다 — 등급은 언제나 E 나 D 다)
    const lt = lineTargets(w)
    // 자리가 없을 때의 짧은 글(2.8.0) — 등급 글자(E·D)로 줄이면 LB 선에서 뜻이 없다(이 선의 등급은 흐름이 아니라 헬스다) → `h/M` · `꺼짐` · `?`
    //   `short` = 자리가 없을 때의 줄인 글자(§17.27 · 2026-10-20) — 등급 글자(D·E)는 이 선에서 아무것도 말하지 않는다 → 정상 수/분모만
    if (lt && lt.unread) return { g, label: '대상 상태 ?', short: '?' }
    if (lt) {
      // 분모 M = 헬스 체크가 판정하는 대상 = 전체 − 헬스 체크 꺼짐(§17.27 「배지 분모」 · 사용자 결정 2026-10-22 — 꺼짐 Lambda 선의 「정상 0/1」이 고장으로 읽혔다).
      //   `health_check_off` 는 다섯 갈래가 **모든 행에 있을 때만** 남는다(`lineTargets`) → 옛 문서·섞인 합친 선 = 0 = 옛 `정상 h/t`.
      //   🔴 옛 문서의 「그 밖」(전체 − 정상 − 비정상)을 꺼짐으로 단정하지 마라(§17.11). 등록 중·빼는 중·사용되지 않음·상태 확인 불가는 M 에 남는다(정상이 아니다)
      const off = Number.isInteger(lt.health_check_off) ? lt.health_check_off : 0
      const m = lt.total - off
      // 전부 꺼짐 = 정보창 「대상」 줄과 같은 낱말(「정상 0/0」은 등록 0 선과 같은 글자다)
      if (lt.total && !m) return { g, label: '헬스 체크 꺼짐', short: '꺼짐' }
      return { g, label: `정상 ${lt.healthy}/${m}` + (lt.unhealthy ? ` · 비정상 ${lt.unhealthy}` : ''), short: `${lt.healthy}/${m}` }
    }
    const bits = []
    if (g === 'B' || g === 'C' || g === 'D' || g === 'A0') bits.push(GRADE[g].badge)
    if (row && (row.drops || 0) > 0) bits.push(`드롭 ${row.drops}`)
    return bits.length ? { g, label: bits.join(' · ') } : null
  }
  // 화면이 말하는 숫자와 하네스가 재는 숫자를 **한 출처**로 묶는다. 🔴 「관측 N건」은 아무것도
  // 말하지 않는다 — 질문은 **그려진 카드 중 몇 개가 상태를 갖나**다. 그래서 `L.boxes` 를 돈다.
  // 새 도식의 컨테이너(§17.20) — 계정·전역 줄·리전·VPC·AZ. 서브넷은 배지를 받으므로 카드로 센다
  const AWS_CONT = new Set(['aws-acct', 'aws-glob', 'aws-region', 'aws-vpc', 'aws-az'])
  const obsStats = (L) => {
    const s = { rows: OBSROWS.length, cards: 0, live: 0, up: 0, down: 0, none: 0, unobserved: 0,
      containers: 0, partial: 0, cardPartial: 0, nodeRows: 0, nodeUnplaced: 0,
      edgeRows: 0, edgeUndrawn: 0, edgeOnWire: 0, flows: 0, flowByEdge: 0, flowByEnds: 0,
      flowEndsOnly: 0 }
    // 🔴 앉을 자리가 없는 관측을 **세서 말한다.** 조용히 버리면 화면은 「나머지는 다 정상」으로
    //    읽힌다(불변식 ④: 있는데 없다고 그리는 것이 최악이다).
    //    ⓐ 엣지 — 어댑터가 `attrs.obs_edges` 로 끈을 달아 준 관계는 **그려진 선에 앉는다**(실측
    //      2026-09-24: 라우트 24건). 끈이 없는 종류(`association`·`security_group_reference`)는
    //      캔버스가 선을 그리지 않으므로 앉을 자리가 없다 — 그 수를 따로 센다. 🔴 예전에는 157건
    //      **전부**를 「선을 그리지 않는 관계」로 말했는데 라우트는 실제로 그려진다(거짓 설명이었다).
    // 선과 끈은 도식이 준다(`obsIds`·`obsFrom`·`a`·`b`·`kind`, §17.20)
    const wireList = L ? NT_AWS.obsLines() : []
    const wireObsIds = new Set()
    for (const w of wireList) for (const id of w.obsIds || []) wireObsIds.add(id)
    for (const o of OBSROWS) {
      if (!o) continue
      if (o.partial) s.partial++
      if (o.target_kind !== 'edge') { s.nodeRows++; continue }
      s.edgeRows++
      if (wireObsIds.has(o.target_id)) s.edgeOnWire++
      else s.edgeUndrawn++
    }
    if (!L || !L.boxes) return s
    // ⓒ **카드에도 컨테이너에도 앉지 못한 노드** 관측. 실측 2026-09-24: 보안그룹·NACL 133건이
    //   여기 해당한다(캔버스가 그 종류를 그리지 않는다) — 세지 않으면 배너가 「관측 불가 0」이라
    //   말하면서 133건을 숨긴다. 「그려진 카드 중」이라는 한정을 화면이 직접 말해야 한다.
    const placed = new Set()
    for (const [, b] of L.boxes) {
      // 새 도식의 대리 상자(`aws:rep` — 그리지 않는 것 전부)는 카드가 아니다 → 그 관측은 아래 「앉지 못한 노드」로 센다
      if (b.kind === 'aws-rep') continue
      const o = obsOf(b.node, b)
      if (b.node) for (const id of obsRepIds(b.node, b)) placed.add(id)
      // ⓑ 컨테이너(계정·리전·VPC·AZ) — 테두리는 **컨테이너 유형** 채널이 이미 쓰고 있어 상태에
      //   내주지 않는다(§8.3 「한 채널에 한 의미」). VPC 자신의 관측은 「describe 가 됐다」라서
      //   **안쪽 카드가 이미 말하는 것**이다. 그래도 수는 배너에 적는다 — 「23건이 어디로 갔나」를
      //   사람이 물을 수 있어야 한다.
      if (AWS_CONT.has(b.kind)) { if (o) s.containers++; continue }
      s.cards++
      if (!o) s.unobserved++
      else {
        if (o.status in s) s[o.status]++
        if (o.partial) s.cardPartial++
      }
    }
    for (const o of OBSROWS) if (o && o.target_kind === 'node' && !placed.has(o.target_id)) s.nodeUnplaced++
    // 흐름 선. 🔴 `flowByEnds` 는 **영구히 0 이어야 한다**(종단 추론 폐기 · ADR 0008 · S2).
    // 그 자리를 지우지 않고 남겨 둔 이유: 0 이 아닌 값이 나오면 그것이 곧 회귀의 신호다.
    // 대신 「양 끝만 흐르지만 칠하지 않은 선」을 `flowEndsOnly` 로 세서 배너가 말한다.
    for (const w of wireList) {
      const f = wireObs(w, L)
      if (!f) { if (endsOnly(w, L)) s.flowEndsOnly++; continue }
      s.flows++
      if (f.via === 'edge') s.flowByEdge++; else s.flowByEnds++
    }
    return s
  }

  // ---------------- 상태 ----------------
  const S = {
    awsAz: true, awsSn: true, awsOp: 0,   // §17 AZ/서브넷 켜기 · 정보창 투명도(%)
    anim: !window.matchMedia || !window.matchMedia('(prefers-reduced-motion: reduce)').matches,
    sel: null, search: '',
    filter: '',                 // 49: 파괴적 필터(확인 후에만 적용된다)
    // §3g 범위 축. 🔴 기본값 `null` = 「전체」다.
    scope: null,                // 고른 계정 노드 id 를 정렬해 쉼표로 이은 글자 | null. SCOPE 의 직렬화 사본 — 쓰는 곳은 `scopeSet` 뿐이다
    // 모니터링 표시(ADR 0006) — **스위치**다.
    // 🔴 기본값 `false` = 「지금 그대로」다. 켜면 **돈이 드는 층(2층 지표)을 사람이 켠 적 없이** 화면이
    //    「보고 있다」고 주장하게 된다(ADR 0005 — 기본 꺼짐은 제품 결정이다).
    // 🔴 `S.mode`('live'|'snapshot', 편집 게이트)와 **다른 축**이다. 합치지 마라.
    // 🔴 오른쪽 비교 칸(§3j J2)만 켠 채 태어난다 — 그때 상태는 이미 받은 문서라 요청·요금이 0 이다.
    monitor: !!CMP,
    // 사용률(§17.25) — **모니터링과 따로 선 스위치**다(사용자 결정 2026-10-06 「사용률만 보고 싶은 고객은 사용률만 누르면
    //   바로 사용률이 보이면 되」). 기본 꺼짐(모니터링과 같은 이유 — 지표 호출은 돈이 든다) · 기간(분, 기본 15) · 고른 줄 `<target_id>|<measure>`
    util: false, utWin: 15, utPick: null,
    // ---- 웨이브 2 상태 (§13-53~64) ----
    mode: 'live',               // 63: 'live' | 'snapshot'. 편집하면 자동으로 snapshot 이 된다
    history: [],                // 63: 버전 히스토리(편집·모드 전환 이력)
    // ---- 대장 101 독(오른쪽 패널) · 대장 94 변경 이력 ----
    // 🔴 `dock` 은 **배타**다: '' | 'hist' | 'notes'. 기본값 '' = 닫힘(첫 화면은 「지금」만 보여준다).
    dock: '',
    // 94: 고른 과거 시각(ISO). null = 「지금」. 🔴 평소 그림은 이 값을 읽지 않는다(§3j J1) —
    //    읽는 곳은 독의 줄 표시와 오른쪽 비교 칸(`CMPV`)뿐이다.
    histAt: null,
    listOn: false, listSort: { col: 'kind', dir: 1 },  // 55: 비시각화 리소스 표
    unplaced: [],               // §3i: 배치가 세우지 못한 잔차(대장 97). 표·배너가 같은 값을 쓴다
    unwired: [],                // §3i 엣지 쪽(대장 98): 긋지 못한 관계. 개수를 위와 합치지 않는다
    qErr: '',                   // 62: 쿼리 파싱 실패 문구(COPY.error)
    zoom: 1, tx: 0, ty: 0
  }

  // ---------------- 전체 배치 ----------------
  function layout () {
    return NT_AWS.layout(awsCtx())
  }

  // ---------------- 그리기 ----------------
  const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
  // §3k K2: 배너 문장의 `**x**` 는 굵게다 — **이스케이프 뒤에** 바꾼다(원문이 태그를 만들 수 없다).
  const fmtBan = (t) => esc(t).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')

  const svgEl = document.getElementById('svg')
  const sideEl = document.getElementById('side')

  // 질의 하나 · 판정 하나. 검색(회색화)과 필터(제거)가 **같은 판정식**을 써야
  // «검색은 되는데 필터하면 사라진다»가 안 생긴다(§13-49).
  // 자유 문자열(부분 일치) — 쿼리 언어의 «연산자가 없는 항» 이 이것이다.
  const matchFree = (n, q) => (n.name || '').toLowerCase().includes(q) ||
    (n.id || '').toLowerCase().includes(q) ||
    Object.values(n.attrs || {}).some((v) => String(v).toLowerCase().includes(q))

  // ---- §13-62 필터 쿼리 언어 ----
  // 문법: `expr := and ( 'or' and )*` · `and := factor ( 'and'? factor )*` ·
  //       `factor := ('not'|'-')? atom` · `atom := tag:k=v | tag:k | 상태:<이상|일부못읽음|관측불가> | k=v | *glob* | 부분문자열`
  // 값은 **부분 일치**이고 `*` 를 쓰면 글롭이다(`type=ec2` 가 `ec2_instance` 를 잡는다).
  // 알 수 없는 키의 `k=v` 는 **태그 키**로 본다 — 갭 26의 예시(`NOT environment=prod`)가 그것이다.
  // 잘못된 쿼리는 조용히 «0건»이 되면 안 된다(그러면 사용자가 «없다»고 오판한다) →
  // 파싱 실패는 COPY.error 로 끝나고 화면은 **하나도 바뀌지 않는다**(검사 62).
  const FIELD = { type: (n) => n.type, region: (n) => n.region || '', az: (n) => n.az || '',
    tier: (n) => n.tier || '', name: (n) => n.name || '', id: (n) => n.id || '' }
  const escRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const globRe = (s) => new RegExp('^' + s.split('*').map(escRe).join('.*') + '$')
  const matchVal = (actual, v) => {
    const a = String(actual == null ? '' : actual).toLowerCase()
    return v.includes('*') ? globRe(v).test(a) : a.includes(v)
  }
  const tagsOf = (n) => n.tags || {}
  // `상태:<갈래>`(§17.26 C-8) — 「!」 상자 「모니터링 이상」 절과 **같은 판정**(`monKind`). 모니터링이 꺼져 있으면 아무것도 맞지 않는다
  //   (그 사실은 필터 줄이 말한다 — `COPY.monOff` · `syncFbar`). 모르는 값 = 해석 실패(화면 불변 · 검사 62 와 같은 규칙)
  const MON_Q = { 이상: 'down', 일부못읽음: 'partial', 관측불가: 'none' }
  const MON_Q_RE = /(?:^|\s|-)상태:/
  function atomFn (tok) {
    if (tok.startsWith('상태:')) {
      const want = MON_Q[tok.slice(3)]
      if (!want) throw new Error('state')
      return (n) => monKind(n.id) === want
    }
    if (tok.startsWith('tag:')) {
      const body = tok.slice(4)
      if (!body) throw new Error('tag')
      const eq = body.indexOf('=')
      if (eq === 0) throw new Error('tag')
      if (eq < 0) return (n) => Object.keys(tagsOf(n)).some((k) => k.toLowerCase().includes(body))
      const k = body.slice(0, eq); const v = body.slice(eq + 1)
      if (!k || !v) throw new Error('tag')
      return (n) => Object.entries(tagsOf(n))
        .some(([tk, tv]) => tk.toLowerCase().includes(k) && matchVal(tv, v))
    }
    const eq = tok.indexOf('=')
    if (eq >= 0) {
      const k = tok.slice(0, eq); const v = tok.slice(eq + 1)
      if (!k || !v) throw new Error('kv')
      if (FIELD[k]) return (n) => matchVal(FIELD[k](n), v)
      return (n) => Object.entries(tagsOf(n))
        .some(([tk, tv]) => tk.toLowerCase() === k && matchVal(tv, v))
    }
    if (tok.includes('*')) {
      const re = globRe(tok)
      return (n) => re.test((n.name || '').toLowerCase()) || re.test((n.id || '').toLowerCase())
    }
    return (n) => matchFree(n, tok)
  }
  const qCache = new Map()
  function parseQuery (q) {
    const key = String(q)
    if (qCache.has(key)) return qCache.get(key)
    const out = (() => {
      const toks = key.trim().split(/\s+/).filter(Boolean)
      if (!toks.length) return { ok: true, fn: () => true, empty: true }
      const ors = []; let cur = []; let neg = false; let want = false
      const bad = { ok: false, err: COPY.error }
      for (const t of toks) {
        if (t === 'or' || t === 'and') {
          if (!cur.length || neg) return bad
          if (t === 'or') { ors.push(cur); cur = [] }
          want = true; continue
        }
        if (t === 'not') { if (neg) return bad; neg = true; continue }
        let s = t; let nn = neg
        while (s.startsWith('-') && s.length > 1) { nn = !nn; s = s.slice(1) }
        let f
        try { f = atomFn(s) } catch (e) { return bad }
        cur.push(nn ? (n) => !f(n) : f)
        neg = false; want = false
      }
      if (neg || want || !cur.length) return bad
      ors.push(cur)
      return { ok: true, fn: (n) => ors.some((and) => and.every((f) => f(n))) }
    })()
    qCache.set(key, out)
    return out
  }
  const matchQ = (n, q) => {
    const p = parseQuery(q)
    return p.ok ? p.fn(n) : false
  }
  // 태그 버킷 (§13-62). 접두 규칙으로만 나누고 `Untagged` 는 항상 맨 아래다.
  function tagBuckets () {
    const m = new Map(TAG_BUCKETS.map((b) => [b, new Map()]))
    let untagged = 0
    for (const n of T.nodes) {
      const ks = Object.keys(tagsOf(n))
      if (!ks.length) { untagged++; continue }
      for (const k of ks) {
        const b = m.get(bucketOf(k))
        b.set(k, (b.get(k) || 0) + 1)
      }
    }
    return TAG_BUCKETS.map((b) => b === 'Untagged'
      ? { bucket: b, keys: [{ key: '(태그 없음)', n: untagged }], n: untagged }
      : { bucket: b, n: [...m.get(b).values()].reduce((s, v) => s + v, 0),
        keys: [...m.get(b).entries()].sort((p, q) => p[0].localeCompare(q[0]))
          .map(([key, n]) => ({ key, n })) })
  }

  function matches (n) { return !!S.search && matchQ(n, S.search.toLowerCase()) }

  function render (L) {
    const r = NT_AWS.render(L, awsCtx())
    if (CMP) cmpDecorate(L)
    return r
  }

  // 호버: 닿지 않는 것만 흐리게. 색을 쓰지 않고 투명도로만 처리한다(§1.4)
  function applyDim (L) {
    // §13-49: 검색은 **필터가 제거할 것과 정확히 같은 집합**을 회색화한다(제거하지 않고).
    // 두 기능이 같은 판정식을 공유하므로 «검색엔 보이는데 필터하면 사라진다»가 원리적으로 없다.
    // 🔴 비교 칸이 열려 있으면 검색 흐림을 끈다(§3j J1) — 회색화가 한 화면에서 두 뜻을 말하지 않게 한다.
    const sk = S.search && !CMPV ? filterSet(S.search) : null
    syncFbar()   // `상태:` 검색 + 모니터링 꺼짐 = 「아무것도 맞지 않는 이유」 한 줄(§17.26 C-8) — 모니터링을 켜고 끌 때도 이 길로 다시 정한다
    return NT_AWS.applyDim(L, sk, sk ? new Set(T.nodes.filter(matches).map((n) => n.id)) : null)   // §17.21 · 돌려준 값 = 맞은 수
  }

  // ---------------- 패널 ----------------
  // 🔴 결함(2026-09-24 발견): 관측 행의 `partial`·`error_count`·`why`·`evidence` 를 읽는 코드가
  //    **0곳**이어서 「정상」과 「정상이지만 지표를 못 읽었다」가 화면에서 완전히 같았다. 계약이 이
  //    네 필드를 required 로 둔 이유가 바로 그것이다(`schema/observation.schema.json:131-152`:
  //    「`up` 만 적으면 2층 실패가 사라진다」·「사유 없는 `?` 는 장식이다」). 카드 한 장의 사정은
  //    배너에 쓸 수 없으니(107장이다) **패널이 말한다** — 캔버스는 글리프 1자까지만 쓴다(§8.3).
  const OBS_WORD = { live: '지금 트래픽이 흐르고 있습니다', up: '정상입니다(구조 조회 기준)',
    down: '이상입니다', none: 'AWS 가 관측 수단을 제공하지 않습니다' }
  // 지우는 중(관측 1.12.0 `deleting` · 사용자 결정 2026-10-21 QA C) — `none` 이지만 「관측 수단이 없다」가 아니다. 판정 말만 바꾼다
  const OBS_DELETING = '삭제 중 — AWS가 이 리소스를 삭제하고 있습니다(이상으로 판정하지 않습니다)'
  // 새 도식에서 판정할 행이 없는 선(§17.20) — 이유는 선 모양(`w.kind`·`w.feed`)과 실은 관계의 종류(엔진 엣지 id 의 첫 칸)로 가른다
  const LINE_UNMEASURED = '측정할 수 없음'
  const METRIC_ONLY = new Set(['attachment', 'endpoint_connection', 'load_balancer_placement'])
  const lineNoRowWhy = (w) => {
    if (w.kind === 'inet') return '인터넷 쪽 구간을 측정하는 관측이 없습니다 — IGW 는 통과량 지표를 제공하지 않습니다(흐름은 서브넷 → IGW 선에 표시됩니다)'
    if (w.feed) return 'LB 서브넷의 라우트를 LB 앞에 다시 그린 선입니다 — 흐름은 그 서브넷의 IGW 선에 표시됩니다'
    const ty = (w.obsIds || []).map((id) => String(id).split('|')[0])
    if (!ty.length) return '이 선에 연결된 관계가 없습니다 — 측정할 대상이 없습니다'
    if (ty.includes('peering')) return '흐름 로그에서 이 피어링을 지난 왕복 기록을 찾지 못했습니다(흐름이 없다는 뜻은 아닙니다)'
    if (ty.every((t) => METRIC_ONLY.has(t))) return '이 구간의 지표 응답이 없습니다'
    if (ty.every((t) => t === 'origin')) return 'CloudFront 는 오리진마다 상태·지표를 제공하지 않습니다 — 이 선은 구성만 나타냅니다'
    return '이 관계의 상태는 측정하지 않았습니다'
  }
  // 선의 「사유」 줄(§17.20 「선의 사유」 표 · #150) — 엔진 엣지 행의 `why` 를 **고객 말**(「~합니다」)로 바꾼다. 원문은 그 칸의 `title`.
  //   🔴 원문을 화면 글자로 내지 않는다 — 지표 이름 · `(창 900초)` · `(개발)` · `blackhole` 같은 개발 말이 섞인다(K3 「원문은 title 에만」과 같은 규율).
  //   갈래 = 판정 × 관계 종류(엔진 엣지 id 첫 칸) × 원문 꼴. 표에 없는 원문은 **판정으로** 떨어진다 — 원인을 지어내지 않는다.
  const LINE_WHY_DEV = '이 연결의 상태를 판정하지 못했습니다 — 이 도구의 처리 문제이며 AWS 구성의 문제가 아닙니다'
  const LINE_WHY_METRIC = [
    [/^통과량 0 · /, '이 구간에서 트래픽이 막혀 있습니다 — 측정 구간 동안 통과량이 없고 차단으로 버려진 패킷이 기록되었습니다'],
    [/ = 0 \(창 \d+초\)$/, '이 구간의 상태 지표가 0입니다 — 이 구간으로 트래픽을 전달하지 못합니다'],
    [/들어왔지만 나간 통과량이 없고/, '들어온 트래픽은 있지만 나간 트래픽이 없습니다 — 차단 여부를 측정하지 못해 전달되었는지 알 수 없습니다'],
    [/값이 0 이면 발행되지 않는 지표입니다/, '측정 구간 동안 지표 데이터가 없습니다 — 트래픽이 없었는지 측정하지 못했는지 구별할 수 없습니다'],
    [/값 0 도 발행하는 지표인데/, '측정 구간 동안 지표 데이터가 없습니다 — 트래픽이 없어도 값을 보내는 지표라 수집 설정을 확인해야 합니다']
  ]
  const LINE_WHY_BY_STATUS = {
    down: 'AWS 가 이 연결을 이상으로 보고했습니다',
    none: 'AWS 가 이 연결의 상태를 제공하지 않아 판정하지 않았습니다'
  }
  const lineWhy = (o) => {
    if (!o || !o.why) return null
    const raw = String(o.why); const ty = String(o.target_id).split('|')[0]
    if (/\(개발\)$/.test(raw)) return { t: LINE_WHY_DEV, title: raw }
    if (ty === 'route' && o.status === 'down') {
      return { t: '라우트의 대상을 사용할 수 없어 이 경로로 가는 트래픽이 버려집니다(블랙홀) — 대상이 삭제되었거나 분리되었는지 확인하세요', title: raw }
    }
    if (ty === 'load_balancer_target' && o.status === 'down') {
      return { t: '헬스 체크를 통과한 대상이 없습니다 — 이 선으로 들어온 요청을 처리할 대상이 없습니다', title: raw }
    }
    for (const [re, t] of LINE_WHY_METRIC) if (re.test(raw)) return { t, title: raw }
    return { t: LINE_WHY_BY_STATUS[o.status] || '이 연결의 상태에 참고할 내용이 있습니다 — 원문은 이 줄에 마우스를 올리면 표시됩니다', title: raw }
  }
  // DX VIF 의 BGP 피어 수(관측 `bgp` · 스키마 1.6.0 · §17.11 DX 행) → VIF 줄의 글. 키가 없으면 `null`(옛 문서 · 수명주기로 판정한 VIF —
  //   부르는 쪽이 판정 말로 떨어진다). 🔴 판정은 엔진이 접은 값 그대로다 — 여기서는 「몇 개 중 몇 개」를 이름으로 말할 뿐이다
  const obsBgp = (o) => {
    const g = o && o.bgp
    if (!g) return null
    if (!g.total) return { t: 'BGP 피어 없음', cls: 'wn' }
    if (g.up === g.total) return { t: `BGP up ${g.up}/${g.total}`, cls: '' }
    if (!g.up) return { t: `BGP 전부 down 0/${g.total} — 이 VIF 로는 온프레미스 경로가 없습니다`, cls: 'bh' }
    return { t: `BGP 일부 down ${g.up}/${g.total} — 나머지 피어로 연결됩니다`, cls: 'wn' }
  }
  // 「상태」 칸의 줄 — 새 도식 정보창(§17.20, aws-canvas.js 가 DOM textContent 로 넣는다)이 쓴다
  function obsRows (n, b) {
    if (!S.monitor || OBS_STALE) return null
    const o = obsOf(n, b)
    if (!o) return null
    const r = []
    r.push(['판정', o.deleting === true ? OBS_DELETING : OBS_WORD[o.status] || o.status])
    // 🔴 `partial` 은 판정을 바꾸지 않으므로(계약: `up` + `partial`) **글로 말하지 않으면 사라진다.**
    if (o.partial) {
      // §3k K3: 이유는 사람 말로 — 원문 코드·AWS 메시지는 `title` 에만 둔다(검사 115 ③).
      const rs = (o.partial_reasons || []).filter((x) => x && (x.code || x.message))
      const word = [...new Set(rs.map((x) => reasonWord(x.code)))].slice(0, 3).join(' · ')
      const raw = rs.map((x) => [x.code, x.message].filter(Boolean).join(' — ')).join(' / ')
      r.push(['부분 실패', '근거의 일부를 읽지 못한 상태로 판정했습니다 — 「정상」과 같게 보지 마세요' + (word ? ' · ' + word : ''), raw])
    }
    // 🔴 `0`(에러 없음)과 `null`(세지 못했다)은 다른 사실이다(계약 `error_count`) → 갈라 적는다.
    if (o.error_count) r.push(['오류 수', String(o.error_count)])
    else if (o.error_count === null) r.push(['오류 수', '세지 못했습니다(0 이 아닙니다)'])
    if (o.why) r.push(['사유', o.why])
    // LB 대상 수(계약 `targets` · ADR 0006 W3②). 🔴 키가 없으면 줄도 없다 — 못 읽은 것을 「0개」로 적으면
    // 「대상이 없는 LB」가 된다(그 사유는 위 「부분 실패」 줄이 말한다). 등록 중·빼는 중은 어느 쪽에도 안 센다.
    const t = o.targets
    if (t) {
      r.push(['대상', t.total ? `${t.total}개 중 ${t.healthy}개 정상` + (t.unhealthy ? ` · 비정상 ${t.unhealthy}개` : '') : '대상 없음(확인함)'])
    }
    const e = o.evidence
    if (e && e.metric === 'flow_log') {
      // 흐름 로그로 증명한 카드(IGW — 서브넷 선 하나가 증명되면 B). 지표 줄의 「데이터포인트」로 적지 않는다.
      r.push(['근거', flowLogLine(e, null)])
    } else if (e) {
      r.push(['근거 API', e.api])
      if (e.metric) {
        r.push(['지표', e.metric +
          (e.datapoints !== null && e.datapoints !== undefined ? ` · 0 아닌 데이터포인트 ${e.datapoints}개` : '') +
          (e.window_seconds ? ` · 구간 ${e.window_seconds}초` : '')])
      }
    }
    return r
  }

  // ---- §13-58 딥링크: 카드 → AWS 콘솔 ----
  // 리전 없는 URL 은 만들지 않는다(전역 규칙: 리전 항상 명시 — 리전이 빠지면 콘솔이 셸 기본
  // 리전을 열어 «다른 리전의 같은 이름»을 보여준다).
  const resIdOf = (n) => String((n.attrs && (n.attrs.id || n.attrs.arn)) || n.id)
  function consoleUrl (n) {
    if (!n) return null
    const r = n.region || GLOBAL_HOME[n.type]   // 리전 없는 URL 은 만들지 않는다 — 전역 자원만 그 서비스의 홈 리전
    const f = CONSOLE_PATH[n.type]
    if (!r || !f) return null
    return `https://${r}.console.aws.amazon.com/${f(n, resIdOf(n), r)}`
  }

  // ---- §13-57 관계 패널: 선을 누르면 «관계의 성질»이 나온다 ----
  // §3e 로 선 하나가 여러 원본을 대표하므로, 선을 열지 못하면 합쳐진 원본을 볼 방법이 없다.
  // 선의 「판정 등급」 줄들 — 정보창 「상태」 칸(§17.20 · `obs.lineRows`)이 쓴다. 줄 = `[이름, 값, data-row, title]`.
  const wireGradeRows = (w, L) => {
    if (!S.monitor || OBS_STALE) return []
    const row = wireRow(w)
    const g = gradeOf(row)
    const out = [['판정 등급', `${g} · ${GRADE[g].badge} — ${GRADE[g].why}`, 'flowgrade']]
    // 🔴 「양 끝만 흐른다」를 **여기서 말한다.** 이 줄이 없으면 회색 선을 본 사람은 고장을
    //    의심하고, 실제 사실(그 구간을 아무도 재지 않았다)은 화면 어디에도 없다.
    if (endsOnly(w, L)) {
      out.push(['초록이 아닌 이유', '양 끝 리소스는 흐르지만 이 구간 자체를 측정한 근거가 없습니다 — ' +
        '양 끝의 트래픽이 이 경로로 갔다는 보장이 없어 초록으로 표시하지 않습니다', 'flowends'])
    }
    out.push(['판정 근거', row ? evidenceLine(row) : '이 경로의 관측 항목이 없습니다', 'flowobs'])
    // 근거 없는 IGW·피어링 라우트 선 — 흐름 로그를 읽었는지·못 읽었는지(§6 규칙 10 ⓓ). 로그 id·코드는 `title` 에만.
    const fln = flowLogNote(w)
    if (fln) out.push(['흐름 로그', fln.t, 'flowlog', fln.title])
    if (row && (row.drops || 0) > 0) out.push(['드롭', `${row.drops}`, 'flowdrops'])
    return out
  }

  // ================ 웨이브 2 (§13-53~64) ================
  // 캔버스 **밖** DOM 이다. 크롬을 SVG 안에 그리면 내보내기(60)에 섞여 들어간다(§13-59).
  const bansEl = document.getElementById('bans')
  const emptyEl = document.getElementById('empty')
  const chromeEl = document.getElementById('chrome')
  // 왼쪽 아래 「!」 원과 그 상자(대장 129 · §17.26 C-3·C-7) — 옛 아래쪽 표(`#list`)·배너 알약 3개의 자리다
  const listEl = document.getElementById('probpop')
  const probsEl = document.getElementById('probs')

  const cardCount = (LL) => LL.nItems

  // ---- §3i 잔차: 「그릴 규칙이 없는 것」은 조용히 버리지 않는다 (대장 97 · 불변식 ④) ----
  // 배치가 끝난 뒤 «모델 − (그려짐 ∪ 대표됨)» 을 센다.
  // 🔴 「모르는 타입 목록」을 두지 않는 이유: 목록을 관리하면 다음에 생기는 버림 경로를 또 놓친다.
  //    잔차는 결과(그려졌나·대표됐나)로 세므로, 새 경로가 생겨도 제품이 스스로 신고한다.
  // 사유 문구는 도식이 준다(aws-canvas.js `awsWhy`). `WHY_NONE` 은 도식이 사유를 주지 않은 노드의
  //    방어용 문구다 — 타입과 티어를 **둘 다** 넣는다(하나만 쓰면 개발자가 엉뚱한 곳을 고친다).
  // 🔴 필터(§13-49)·편집 숨김(§13-63)은 잔차가 **아니다**. 사람이 의도한 제거이고 자기 어휘
  //    (확인 모달·스냅샷 배지)를 이미 갖는다 — 섞으면 필터 한 번에 수백 줄이 쏟아진다.
  const WHY_RULE = '그리지 않는 종류'
  const WHY_NONE = '아직 그리지 못하는 종류'
  const ancTypeOf = (n, t) => {
    let c = byId.get(n.parent_id)
    while (c) { if (c.type === t) return c; c = byId.get(c.parent_id) }
    return null
  }
  // 「그려짐 ∪ 대표됨」. 🔴 **잔차 판정과 선 판정이 이 한 식을 같이 쓴다** — 선을 그릴 때 「대표
  //    안 된 노드」를 조상으로 환산하면 없는 관계가 그려지므로(실측 2026-09-20: 리전 → VPC 로 없는 선),
  //    거기서도 같은 술어가 필요하다. 두 곳에 따로 적으면 한쪽만 고쳐져 어긋난다.
  function repSetOf (boxes) {
    const rep = new Set()
    for (const b of boxes.values()) {
      const ms = b.node && b.node.members ? b.node.members.map((m) => m.id) : [b.id]
      for (const m of ms) rep.add(m)
      for (const h of (b.hiddenIds || [])) rep.add(h)
    }
    return rep
  }
  // 사람이 의도한 제거(§13-49 필터 · §13-63 편집 숨김 · §3g 범위)는 잔차가 아니다 — 엣지 쪽도 같다.
  // 여기서 세지 않으면 §3i 잔차 신고(대장 97·98)가 「아직 그리지 못하는 종류」고 **거짓 고발**한다.
  const goneByHand = (id) => {
    if ((FILTER && !FILTER.has(id)) || HIDDEN.has(id)) return true
    // (옛 네 번째 채널 = 과거 시각은 폐기됐다 — 과거는 평소 그림을 건드리지 않는다, §3j J1.)
    // 🔴 **다섯 번째 채널 = 범위 축**(§3g). 범위 밖인데 **밖으로 나가는 선이 없는** 노드는 스텁의
    //    대표도 아니므로 「대표됨」에 안 들고, 여기서 안 세면 계정 1개를 고르는 순간 §3i 가 남의
    //    계정 자원 수백 개를 「아직 그리지 못하는 종류」고 거짓 고발한다. 「내가 안 보기로 했다」는
    //    「규칙이 없다」가 아니다.
    return !!(SCOPE && !inScope(id))
  }
  function unplacedRows (L) {
    const rep = repSetOf(L.boxes)
    const out = []
    for (const n of byId.values()) {
      if (n.type === 'account' || rep.has(n.id)) continue
      if (goneByHand(n.id)) continue
      const v = ancTypeOf(n, 'vpc'); const r = ancTypeOf(n, 'region')
      out.push({ id: n.id, type: n.type, name: n.name || '', vpc: v ? v.name : '—',
        region: r ? r.name : (n.region || '—'),
        detail: '부모 «' + (n.parent_id || '없다') +
          (n.parent_id && !byId.has(n.parent_id) ? ' — 모델에 없다»' : '»'),
        why: (L.awsWhy && L.awsWhy.get(n.id)) || `${WHY_NONE} — 타입 «${n.type}» · 티어 «${n.tier || '없다'}»` })
    }
    return out.sort((a, b) => a.id.localeCompare(b.id))   // 불변식 ②: 순서는 id 로 고정한다
  }
  // §3i 의 엣지 쪽(대장 98). 그리지 못한 «관계» 도 같은 표·같은 사유 열로 신고한다.
  // 🔴 노드 잔차와 **개수를 합치지 않는다** — 배너가 「리소스 N개」라고 말하는데 관계가 섞이면
  //    그 문장이 거짓이 된다. 두 pill·두 상세 행으로 따로 센다.
  function unwiredRows (L) {
    const nm = (id) => { const n = byId.get(id); return n ? (n.name || id) : id }
    return (L.unwired || []).map(({ e, dead, why }) => {
      const src = byId.get(e.source)
      const r = src ? ancTypeOf(src, 'region') : null
      // 🔴 `a`·`b` 는 표 열이 아니다(LIST_COLS 밖) — 하네스가 **끝점으로** 맞춰 보기 위한 값이다.
      //    엣지 id 는 어댑터가 순번으로 붙이므로 하네스가 예측할 수 없다.
      return { id: e.id, a: e.source, b: e.target,
        type: `관계 «${e.kind}»`, name: `${nm(e.source)} → ${nm(e.target)}`,
        vpc: (src && ancTypeOf(src, 'vpc') ? ancTypeOf(src, 'vpc').name : '—'),
        region: r ? r.name : ((src || {}).region || '—'),
        detail: `출발 «${e.source}» → 도착 «${e.target}»`,
        why: why || `${WHY_NONE} — ` + [e.fallback ? `엣지 종류 «${e.kind}»` : null,
          dead ? `끝점이 ${dead}` : null].filter(Boolean).join(' · ') }
    }).sort((a, b) => a.id.localeCompare(b.id))
  }

  // ---- 「캔버스에 없는 리소스」 표(옛 §13-55 리스트 뷰 · 🔁 2026-10-20 왼쪽 아래 「!」 상자 · §17.26 C-7) ----
  // 🔴 표에는 **그림에 넣지 못한 것만** 있다 — 규칙상 그리지 않는 SG·RT·NACL(사유 없음 = `WHY_RULE`)은 VPC 정보창이 말하므로
  //    표에도 원에도 없다(사용자 결정 2026-10-20 갱신). 「아직 그리지 못하는 종류」와 「그리지 않는 종류」를 섞지 않는 §3i I4 는 그대로다 —
  //    섞이던 자리(한 표)가 없어졌다. 사유 열 = 고객 말(`popWhy`) · 원래 사유(`why`)·내용(`detail`)은 그 칸의 `title`.
  // 종류 칸 = 사람 말(이력 줄과 같은 이름 · 모르는 종류는 원래 이름 그대로 — 이 화면이 모르는 것이라 지어 부르지 않는다) · 관계 = 「연결」
  const LIST_COLS = [['kind', '종류'], ['id', 'ID'], ['name', '이름'],
    ['vpc', 'VPC'], ['region', '리전'], ['say', '사유']]
  // 고객 말(§17.26 표가 정본). 🔴 표에 없는 사유는 「놓을 자리를 찾지 못했다」로 말한다 — 원문(개발 말)을 화면 글자로 내지 않는다.
  const POP_WHY = [
    [(r) => /^관계 «/.test(r.type) && String(r.why).startsWith(WHY_NONE) && !/끝점/.test(r.why), '아직 그리지 못하는 연결 종류'],
    [(r) => /^관계 «/.test(r.type) && /보기 범위 밖/.test(r.why), '보기 범위 밖과의 연결(「계정: 전체」에서 표시)'],
    [(r) => /^관계 «/.test(r.type), '연결된 리소스가 캔버스에 없음'],
    [(r) => String(r.why).startsWith(WHY_NONE), '아직 그리지 못하는 종류'],
    [(r) => /구성 일부를 읽지 못해/.test(r.why), '구성 일부를 읽지 못해 위치를 알 수 없음'],
    [(r) => /들어갈 (계정|리전|VPC) 상자가 없다|서브넷이 이 화면에 없다|VPC 가 이 화면에 없다|구성에 서브넷이 없다/.test(r.why), '속한 계정·리전·VPC·서브넷이 캔버스에 없음'],
    // 배치 D(2.10.0 · §17.34) — 탄력적 IP 는 카드가 아니라 주인의 정보창 줄이다. 연결이 있는데 주인을 못 찾으면 여기 선다(사유 = 고객 말 · 원래 사유는 title)
    //   🔁 연결 없는 EIP 는 여기 오지 않는다(사용자 결정 2026-10-21 오전 · 도식이 대리 상자로 대표시킨다)
    [(r) => /^연결 대상을 찾지 못한 탄력적 IP/.test(r.why), '연결된 리소스를 찾지 못한 탄력적 IP'],
    // #129(2.11.0 · §17.38) — Connect 는 아이콘이 아니라 올라탄 어태치먼트 선 위의 점선이다. 그 어태치먼트가 이 화면에 없으면 여기 선다
    [(r) => /^TGW Connect 어태치먼트 — 올라탄 어태치먼트/.test(r.why), '올라탄 어태치먼트가 캔버스에 없는 TGW Connect']
  ]
  const POP_WHY_ELSE = '표시할 위치 없음'
  const popWhy = (r) => { const m = POP_WHY.find(([f]) => f(r)); return m ? m[1] : POP_WHY_ELSE }
  function listRows () {
    const c = S.listSort.col; const d = S.listSort.dir
    const rows = (T.listOnly || []).filter((r) => r.why && r.why !== WHY_RULE)
      .concat(S.unplaced || []).concat(S.unwired || []).concat(S.notes || []).map((r) => ({ ...r, say: r.fixedSay || popWhy(r),
        kind: /^관계 «/.test(r.type) ? '연결' : (HIST_TYPE[r.type] || TYPE_LABEL[r.type] || r.type) }))
    return rows.sort((a, b) =>
      d * (String(a[c]).localeCompare(String(b[c])) || a.id.localeCompare(b.id)))
  }
  function listCsv () {
    const q = (v) => '"' + String(v).replace(/"/g, '""') + '"'
    return [LIST_COLS.map((c) => q(c[1])).join(',')]
      .concat(listRows().map((r) => LIST_COLS.map((c) => q(r[c[0]])).join(','))).join('\n')
  }
  // ---- 「모니터링 이상」 절(§17.26 C-8 · 사용자 결정 2026-10-28 · 검사 172) — 켰을 때만 · 갈래 = `monKind` ----
  //   줄 = 계정 · 리전 · VPC · 리소스 · 상태 · 사유. 상태 글은 **이미 화면에 있는 다섯 곳**에서만 가져온다(C-8 표 — 새로 짓지 않는다).
  const MON_SHORT = { live: '흐름 있음', up: '정상', down: '이상', none: '관측 불가' }
  const MON_RAW = /^AWS 가 보고한 [\w.-]+ = '([^']*)'/
  const monState = (o) => {
    const g = o.bgp ? obsBgp(o) : null
    if (g) return g.t
    if (Array.isArray(o.tunnels) && o.tunnels.length) {
      return `터널 ${o.tunnels.length}개 중 ${o.tunnels.filter((t) => /down/i.test(String(t && t.status))).length}개 Down`
    }
    if (o.targets && o.targets.total && window.NT_AWS && NT_AWS.targetsWord) return NT_AWS.targetsWord(o.targets)[0]
    const m = MON_RAW.exec(String(o.why || ''))
    if (m && window.NT_AWS && NT_AWS.stateKo) return NT_AWS.stateKo(m[1])
    return MON_SHORT[o.status] || o.status
  }
  const MON_DEV = '이 리소스의 상태를 판정하지 못했습니다 — 이 도구의 처리 문제이며 AWS 구성의 문제가 아닙니다'
  const monWhy = (o, k) => {
    if (k === 'partial') {
      const rs = (o.partial_reasons || []).filter((x) => x && (x.code || x.message))
      const ws = [...new Set(rs.map((x) => reasonWord(x.code)))]   // 정보창 「부분 실패」 줄과 같은 말(§3k K3 — 원문은 title)
      const word = ws.slice(0, 3).join(' · ')
      return { t: '근거의 일부를 읽지 못한 상태로 판정했습니다' + (word ? ' · ' + word : ''),
        title: rs.map((x) => [x.code, x.message].filter(Boolean).join(' — ')).join(' / ') }
    }
    if (o.why && /\(개발\)$/.test(String(o.why))) return { t: MON_DEV, title: String(o.why) }
    if (o.why) return { t: String(o.why), title: String(o.why) }
    return { t: k === 'none' ? OBS_WORD.none : OBS_WORD[o.status] || o.status, title: '' }
  }
  // 누르면 갈 자리 — 그 리소스(묶음 멤버 · 대표 상자 안이면 그 상자) → 없으면 들어 있는 VPC → 없으면 `null`(누를 데가 없다)
  const monGo = (n) => {
    const N = window.NT_AWS
    if (!N || !N.markAt) return null
    if (N.markAt(n.id)) return n.id
    const v = ancTypeOf(n, 'vpc')
    return v && N.markAt(v.id) ? v.id : null
  }
  function monRows () {
    if (!monOn()) return null
    const out = { down: [], partial: [], none: [] }
    for (const n of T.nodes) {
      const k = monKindOf(obsIndex().get(n.id))
      if (!k || goneByHand(n.id)) continue
      const o = obsIndex().get(n.id)
      const a = acctOf(n); const ai = a ? accounts.findIndex((x) => x.id === a) : -1
      const v = ancTypeOf(n, 'vpc'); const r = ancTypeOf(n, 'region')
      const kind = HIST_TYPE[n.type] || TYPE_LABEL[n.type] || n.type
      const w = monWhy(o, k)
      out[k].push({ id: n.id, kind: k, acct: ai >= 0 ? acctLabel(ai) : '—', region: n.region || (r ? r.name : '') || '—',
        vpc: v ? (v.name || '—') : '—', name: n.name && n.name !== kind ? `${kind} (${n.name})` : kind,
        state: monState(o), why: w.t, title: w.title, go: monGo(n) })
    }
    const by = (x, y) => String(x.acct).localeCompare(String(y.acct)) || String(x.region).localeCompare(String(y.region)) ||
      String(x.vpc).localeCompare(String(y.vpc)) || x.id.localeCompare(y.id)   // 불변식 ②
    for (const k in out) out[k].sort(by)
    return out
  }
  const MON_COLS = [['acct', '계정'], ['region', '리전'], ['vpc', 'VPC'], ['name', '리소스'], ['state', '상태'], ['why', '사유']]
  // 🔴 `<table>` 을 쓰지 않는다 — 「캔버스에 없는 리소스」 표(`#probpop tbody tr`)를 세는 검사 88·89·146 이 이 줄까지 세면 두 목록이 섞인다
  const monTable = (k, rows) => `<div class="pmt" data-mon="${k}"><div class="pmh">` + MON_COLS.map(([, h]) => `<span>${h}</span>`).join('') + '</div>' +
    rows.map((r) => `<div class="pmr${r.go ? ' lopen' : ''}" data-id="${esc(r.id)}"${r.go ? ` data-go="${esc(r.go)}"` : ''} title="${esc(r.go ? '눌러서 이 리소스로 이동' : '캔버스에 자리가 없는 리소스입니다')}">` +
      MON_COLS.map(([c]) => `<span${c === 'why' && r.title ? ` title="${esc(r.title)}"` : ''}>${esc(r[c])}</span>`).join('') + '</div>').join('') + '</div>'
  function monSection (M, opened) {
    const out = ['<div class="psec" data-sec="mon">', `<div class="pst" data-monh="down">모니터링 이상 ${M.down.length}개</div>`]
    out.push(M.down.length ? monTable('down', M.down) : '<div class="pnote">이상으로 판정된 리소스가 없습니다.</div>')
    // 「모름」 두 갈래는 접어 둔다(머리를 누르면 펼침) — 수가 많아도 이상 목록을 밀어내지 않는다.
    //   🔴 머리에 `data-kind` 를 달지 않는다 — 검사 115·146 이 `.pst[data-kind]` 를 배너 줄(`dump().bans`)과 맞춰 본다(이 절은 배너 줄이 아니다)
    for (const [k, h] of [['partial', '일부 못 읽음'], ['none', '관측 불가']]) {
      if (M[k].length) out.push(`<details class="pmd" data-mon="${k}"${opened && opened.has(k) ? ' open' : ''}><summary class="pst" data-monh="${k}">${h} ${M[k].length}개</summary>${monTable(k, M[k])}</details>`)
    }
    out.push('</div>')
    return out.join('')
  }
  // 줄을 누름 = 그 리소스를 가운데로 + 고른다(길 강조 + 정보창 · `#sel=`) — 정보창을 여는 길은 공유 링크·GA 줄과 같은 `NT_AWS.open`
  function monFocus (k) {
    const N = window.NT_AWS
    const a = N && N.markAt ? N.markAt(k) : null
    const r = svgEl.getBoundingClientRect()
    if (!a || !(r.width > 0) || !(r.height > 0)) return null
    let z = Math.min(Math.max(S.zoom, 1), FOCUS_MAX)
    if (a.w > 0) z = Math.min(z, (0.8 * r.width) / a.w)
    if (a.h > 0) z = Math.min(z, (0.8 * r.height) / a.h)
    z = Math.max(z, Math.min(S.zoom, FOCUS_MAX))   // 누르기 전보다 멀어지지 않는다
    cmpAim(z, a.x + a.w / 2, a.y + a.h / 2)
    const ok = N.open(k)
    if (ok) { S.sel = k; setHash(k) }
    return { key: k, box: a.key, zoom: S.zoom, tx: S.tx, ty: S.ty, open: !!ok }
  }
  // 원이 서는가 = 진짜 문제가 하나라도 있나(§17.26 C-3). 🔴 수집 실패·거부가 하나라도 있으면 **반드시** 선다(불변식 ④).
  //   모니터링을 켰으면 「이상」·「일부 못 읽음」도 세운다(C-8) — 「관측 불가」만으로는 세우지 않는다(할 일이 없는 영원한 사실)
  const POP_KINDS = new Set(['deny', 'partial', 'undrawn'])
  const monRaise = () => { const M = monRows(); return !!M && (M.down.length > 0 || M.partial.length > 0) }
  const probsOn = () => !CMP && ((T.partial || []).length > 0 || covAccounts().denied > 0 || listRows().length > 0) || monRaise()   // `monRaise` 는 `!CMP` 를 스스로 본다(`monOn`)
  function renderList () {
    if (!listEl) return
    const has = probsOn()
    if (!has) S.listOn = false
    if (probsEl) {
      probsEl.hidden = !has
      probsEl.classList.toggle('on', S.listOn)
      probsEl.title = '확인이 필요한 항목이 있습니다 — 눌러서 확인하세요'
      probsEl.setAttribute('aria-expanded', S.listOn ? 'true' : 'false')
    }
    listEl.classList.toggle('on', S.listOn)
    if (!S.listOn) { listEl.innerHTML = ''; return }
    // 글은 배너 줄과 **같은 출처**다(`bannerRows` — `dump().bans` 의 `deny`·`partial`·`undrawn`) — 자리만 상단 배너에서 이 상자로 옮겼다
    const B = bannerRows().filter((r) => POP_KINDS.has(r.kind))
    const line = (r) => `<div class="pl" data-kind="${r.kind}"${r.title ? ` title="${esc(r.title)}"` : ''}>${fmtBan(r.text)}</div>`
    const read = B.filter((r) => r.kind === 'deny').concat(B.filter((r) => r.kind === 'partial'))
    const und = B.find((r) => r.kind === 'undrawn')
    const rows = listRows()
    const out = ['<div class="phd"><b>확인이 필요한 항목</b><span class="px" data-lclose title="닫기">✕</span></div>']
    if (read.length) out.push('<div class="psec" data-sec="read"><div class="pst">확인하지 못한 영역</div>' + read.map(line).join('') + '</div>')
    // 모니터링을 켰을 때만(§17.26 C-8) — 꺼져 있으면 절이 **없다**(상자는 옛 내용 그대로)
    const M = monRows()
    if (M) out.push(monSection(M, new Set([...listEl.querySelectorAll('details.pmd[open]')].map((d) => d.dataset.mon))))
    if (rows.length) {
      out.push('<div class="psec" data-sec="list">' +
        `<div class="pst" data-kind="undrawn">${esc(und ? und.text : '')}</div>` +
        '<div class="pnote">아래 항목은 아키텍처에 그리지 못했습니다.</div>' +
        '<div class="pacts"><span class="lbtn" data-csv>CSV 내려받기</span></div>' +
        '<div class="lwrap"><table><thead><tr>' +
        LIST_COLS.map(([k, lab]) => `<th data-col="${k}">${lab}${S.listSort.col === k ? (S.listSort.dir > 0 ? ' ▲' : ' ▼') : ''}</th>`).join('') +
        '</tr></thead><tbody>' + rows.map((r) =>
          (r.open ? `<tr data-open="${esc(r.open)}" class="lopen" title="눌러서 정보창 열기">` : '<tr>') + LIST_COLS.map(([k]) => k === 'say'
            ? `<td title="${esc([r.why, r.detail].filter(Boolean).join(' · '))}">${esc(r[k])}</td>`
            : `<td>${esc(r[k])}</td>`).join('') + '</tr>').join('') +
        '</tbody></table></div></div>')
    }
    listEl.innerHTML = out.join('')
  }
  function toggleList (on) {
    S.listOn = (on === undefined ? !S.listOn : !!on) && probsOn()
    renderList(); return S.listOn
  }
  function sortList (col) {
    if (!LIST_COLS.some(([k]) => k === col)) return null
    S.listSort = S.listSort.col === col ? { col, dir: -S.listSort.dir } : { col, dir: 1 }
    renderList(); return { ...S.listSort }
  }

  // ---- §13-59 크롬 · §13-56 상태 문구 ----
  // §3g 검사 91 ①: 배너의 「외부 연결 N」과 `dump().nXwire` 는 **같은 출처**여야 한다. 두 곳에서
  // 따로 세면 어긋나는 날이 오고, 그때 하네스는 제품이 스스로 센 값끼리 비교하게 된다.
  const xwireCount = (L) => (L ? L.nXwire || 0 : 0)
  // ---- 실시간 채널 줄(ADR 0006 O6) — 🔴 모니터링 줄들보다 **먼저** 말한다 ----
  // 읽지 못했을 때 이 줄이 없으면 카드의 상태가 왜 사라졌는지 화면 어디에서도 알 수 없다. 읽지 못한
  // 경우는 **정확히 1줄**이다(검사 101) — 사유가 여러 줄로 흩어지면 요약 알약과 대응이 깨진다.
  // §3k K1: 줄마다 **사람 문장**(`text` — 화면) + **개발 설명**(`dev` — `dump().bans`·V 오버레이). 주기·지연·층 과금
  // 같은 사양 문장은 `dev` 로 간다(명세서 후보 = `docs/product-spec-notes.md` §2). 못 읽은 이유는 사람 문장에 남는다.
  function liveRows () {
    if (LIVE.busy) return [{ kind: 'live', text: '실시간 켜는 중', dev: '저장소에 켜짐을 쓰고 있다(제어 키 PUT)' }]
    if (LIVE.on) {
      // 🆕 따라 보는 창(보강 4 B·H) — 「다른 창에서 바꾼 설정으로 보는 중 · 약 N분 뒤 자동 멈춤」. 끝 = 그 창이 고른 시각(`until`) ·
      //    모르면(옛 화면 파일이 켠 판) 그 판의 임대(`expiresAt`). 기준 시각 = 마지막 제어 읽기(읽기 사이 화면·dump 가 같은 글자다).
      const fol = LIVE.mode === 'follow'
      const folLeft = fol ? aboutMin((LIVE.end || LIVE.lease) - (LIVE.ctlAt || Date.now())) : ''
      const until = fol ? `다른 창에서 바꾼 설정으로 보는 중 · ${folLeft} 뒤 자동 멈춤` : `${msHm(LIVE.until)} 까지`
      const layer = [S.monitor ? (LIVE.layer2 ? '모니터링 지표까지(호출당 과금)' : '모니터링 구조 조회만(지표 요금 없음)') : '',
        LIVE.util ? '사용률(지표 호출당 과금)' : ''].filter(Boolean).join(' + ')
      const chk = LIVE.lastCheck ? ` · 마지막 확인 ${msHm(LIVE.lastCheck)}` : ''
      // 켠 쪽만 말한다(끈 층이 문서에 남은 것은 그리지 않으므로 사람이 기다릴 것이 없다)
      const pend = LIVE.phase !== 'ok' ? [] : livePendWords()
      const fast = liveWaiting(Date.now())
      // 🆕 진행 표시(사용자 결정 2026-10-28) — 화면이 이미 아는 사실만 쓴다(요청·권한 0): ① 켜기 쓰기가 성공했다 · ② 경과(켠·바꾼 시각 →
      //    마지막 확인) · 보통 약 3분(꺼져 있다 켠 첫 실행 실측 173.8초) · 최대 약 8분(빠른 읽기 창) · ③ 도착. 8분이 지나면 이 표시를 접고
      //    옛 문장(기다림 → 15분 「새 상태가 오지 않습니다」)으로 돌아간다 — 약속한 「최대」를 넘긴 단계 표시는 거짓이 된다.
      const steps = (what) => ` · ① 켜기 요청 저장됨 ✓ · ② ${what} · 경과 ${mss((LIVE.lastCheck || LIVE.asked) - LIVE.asked)} · 보통 약 3분, 최대 약 8분 · ` +
        `10초마다 확인${LIVE.lastCheck ? `(마지막 확인 ${msHms(LIVE.lastCheck)})` : ''}`
      const done = LIVE.doneWhat && LIVE.doneReads === LIVE.reads ? ` · ③ ${LIVE.doneWhat} 도착 ✓` : ''
      // 🔴 옛 저장소(조건부 연장 불가)면 창을 닫아도 그때까지 켜져 있다 · 연장이 실패 중이면 곧 꺼질 수 있다 —
      //    둘 다 요금이 걸린 사실이라 **사람 문장**이다(모든 단계의 줄 끝에 붙는다).
      const tail = (LIVE.mode === 'fixed' ? ` · 이 저장소는 창을 닫아도 ${until} 켜져 있습니다` : '') +
        (LIVE.renewWhy ? ` · ⚠ 연장하지 못했습니다(${LIVE.renewWhy}) — ${msHm(LIVE.lease)} 까지 다시 시도합니다` : '') +
        (LIVE.setWhy ? ` · ⚠ 켜기·끄기 설정을 저장소에 쓰지 못했습니다(${LIVE.setWhy}) — 화면은 그대로입니다` : '') +
        // 🆕 제어 키를 못 읽었다(보강 4 · 불변식 ④) — 화면은 마지막 그대로 · 경고 꼬리 하나
        (LIVE.ctlWhy ? ` · ⚠ 다른 창의 변경을 확인하지 못했습니다(${LIVE.ctlWhy}) — 화면은 그대로입니다` : '') +
        // 층을 막 켰다 — 지금 문서는 그 층 없이 만들어졌다(켜는 쓰기가 깨운 관측이 그 층을 싣고 온다)
        (pend.length ? (fast ? steps(`${pend.join('·')} 수집 중`) : ` · ${pend.join('·')} — 새 관측을 만드는 중입니다(도착하면 바로 표시됩니다)`) : '')
      const keep = ` · 실시간은 ${fol ? `${folLeft} 뒤 자동으로 멈춥니다` : `${until} 켜져 있습니다`} — 끄려면 ${liveBtns()}`
      const dev = (x) => [x, `층: ${layer}`,
        fol ? `따라 보기: 다른 창이 쓴 제어 키를 1분마다 읽기만 한다(쓰지도 연장하지도 않는다) · 그 판의 임대 ${msHm(LIVE.lease)}` +
          (LIVE.end ? ` · 그 창이 고른 끝 ${msHm(LIVE.end)}` : ' · 그 창이 고른 끝을 모른다(옛 화면 파일의 판)') + ' — 켠 창을 닫으면 임대 끝에 서버가 멈춘다'
          : `상한: ${LIVE.untilBy === 'url' ? '이 화면 파일의 주소가 그때 만료된다' : `켠 시각 + ${durWord(LIVE.cap || LIVE.dur)}`}`,
        LIVE.mode === 'lease' ? `임대: 서버 만료 ${msHm(LIVE.lease)} — 5분마다 15분씩 조건부 연장 · 창을 닫으면 약 20분 안에 멈춘다`
          : fol ? '' : '임대 없음: 이 저장소는 조건부 연장을 못 받는다(CORS 에 if-match 가 없거나 ETag 를 못 읽었다) — 상한을 한 번에 썼다',
        LIVE_SLOT.ctlGetUrl ? '제어 키 읽기: 1분마다(다른 창이 켜고 바꾸고 끈 것을 1분 안에 안다)' : '제어 키 읽기 주소 없음(옛 서명 함수) — 다른 창과 부딪히면 이 창만 끈다',
        fast ? `켠 층을 기다리는 동안 10초마다 확인한다(${msHm(LIVE.asked + LIVE_WAIT_FOR_MS)} 까지 · 그 뒤 1분마다)${chk}`
          : `1분마다 확인한다${chk}`].filter(Boolean).join(' · ')
      if (LIVE.phase === 'fail') {
        return [{ kind: 'live', text: `상태를 읽지 못했습니다 — 아이콘의 상태 표시와 흐름을 지웠습니다 · 사유: ${LIVE.why}${keep}${tail}`,
          dev: dev(`아래 그림은 수집 시각의 구조다(${kstAt(T.run.collected_at)}) · 1분마다 다시 읽는다`) }]
      }
      if (LIVE.phase === 'stale') {
        return [{ kind: 'live', text: `새 상태가 오지 않습니다 — 아이콘의 상태 표시와 흐름을 지웠습니다 · ${LIVE.why}${keep}${tail}`,
          dev: dev(`아래 그림은 수집 시각의 구조다(${kstAt(T.run.collected_at)}) · 상태 함수는 5분마다 돈다 — ` +
            '세 주기 넘게 새 관측이 없으면 그쪽이 멈춘 것이다') }]
      }
      // 🔴 `wait` 의 `LIVE.why` 는 사양 문장이다(「바로 깨운다 · 옛 스택이면 다음 5분 주기」) → 개발 설명으로.
      // 🆕 이 그림이 상태 문서보다 새 구조다(B-09) — 「짝 안 맞음」이 아니라 **기다림**이다. 사람이 할 일이 없다.
      if (LIVE.phase === 'wait' && LIVE.pairWait) {
        return [{ kind: 'live', text: `실시간 갱신 — ${until} · 이 구성도가 더 최신입니다 — 새 구성으로 측정한 첫 관측을 기다리는 중입니다(보통 몇 분)${tail}`,
          dev: dev(`${LIVE.why} · 구조 단이 새 구조를 올리면 상태 함수가 바로 다시 잰다(옛 스택이면 다음 5분 주기)`) }]
      }
      // 🆕 상태가 더 새 구조를 보고 있다(B-13) — 이 그림에는 얹지 않는다. 할 일 = 상단 «최신 화면 열기»(주소가 없으면 다시 받기).
      if (LIVE.phase === 'moved') {
        return [{ kind: 'live', text: `실시간 갱신 — ${until} · 마지막 관측 ${msHm(LIVE.docAt)} 은 새 구성의 것이라 이 구성도에 표시하지 않았습니다 · ` +
          (canGoLatest() ? '상단 «최신 화면 열기»를 누르면 새 구성의 화면으로 넘어가 모니터링이 이어집니다' : '최신 index.html 을 다시 받아 주세요') + tail,
          dev: dev(`${LIVE.why} · 상태 함수는 늘 최신 구조로 잰다 — 이 화면 파일의 구조는 그대로라 기다려도 풀리지 않는다`) }]
      }
      if (LIVE.phase === 'wait') {
        return [{ kind: 'live', text: `실시간 갱신 — ${until}${fast ? steps('관측 중') : ' · 첫 관측을 기다리는 중'}${tail}`,
          dev: dev(`${LIVE.why} · 얹히기까지 보통 몇 분(꺼져 있다 켠 첫 실행 ≈3분 · 옛 스택이면 최대 약 8분)`) }]
      }
      return [{ kind: 'live', text: `실시간 갱신 — ${until} · 마지막 관측 ${msHm(LIVE.docAt)}${done}${tail}`, dev: dev('') }]
    }
    if (LIVE.note) return [{ kind: 'live', text: LIVE.note, dev: '' }]
    // 🆕 서명 전 화면 파일(위 `UNSIGNED_PAGE`) — 켜기 전에도, 구운 문서로 켠 뒤에도 같은 한 줄이다(사람이 할 일이 같다).
    if (UNSIGNED_PAGE) {
      return [{ kind: 'live', text: '서명 전 파일입니다 — 잠시 뒤 다시 받아 주세요. 이 파일로는 실시간 모니터링을 켤 수 없습니다',
        dev: '주소 칸이 비었는데 CSP 가 버킷 오리진을 연다 = 서명 함수가 칸을 채우기 전의 원본(unsigned/index.html 또는 게시 키를 나누기 전 스택의 빌드 직후)' }]
    }
    // 🔴 켤 수 있다는 사실을 화면이 말한다 — 배포본에는 구운 상태가 없어서, 이 줄이 없으면
    //    「이 화면에 실시간이 있다」를 아무도 모른다(위 「상태 데이터가 있다」 줄과 같은 이유).
    // 🔴 이미 죽은 주소면 「켤 수 있다 … 까지(지난 시각)」가 아니라 **누른 뒤와 같은 문장**을 먼저 말한다 — 받은 뒤
    //    오래 둔 파일·켜지 않고 열어 둔 탭에서 사람은 눌러 본 뒤에야 만료를 알았다(#31·#32 리뷰 후속).
    if (LIVE_SLOT && !S.monitor && liveUrlDead(Date.now())) {
      return [{ kind: 'live', text: '이 파일의 주소가 만료됐습니다 — 최신 index.html 을 다시 받으면 켤 수 있습니다',
        dev: `서명 주소 만료 ${msHm(LIVE_SLOT.urlUntil)} · 켜기 하한 = 남은 수명 1분` }]
    }
    if (LIVE_SLOT && !S.monitor) {
      // 🔁 2026-10-28: 켜 둘 시간은 «모니터링» 팝오버에서 고른다(30분~8시간 · 기본 4시간) — 「사용률」 버튼은 고른 시간으로 켠다.
      return [{ kind: 'live', text: `실시간 갱신을 켤 수 있습니다 — 상단 «모니터링» 또는 «사용률»(각각 켭니다 · ${durWord(LIVE.dur)} · 이 화면 파일로는 ` +
        `${msHm(LIVE_SLOT.urlUntil)} 까지) · 지금 이 구성도는 구조만 표시합니다`,
      dev: '켜면 그 자리에서 첫 관측을 시작하고 그 뒤 5분마다 새 관측을 카드에 얹는다 · 시각 = 이 화면 파일에 구운 서명 주소의 만료' }]
    }
    // 주소가 없는 화면에서 켰는데 구운 문서도 없다 — 켜도 아무것도 안 바뀌는 이유를 말한다.
    if (!LIVE_SLOT && S.monitor && !(OBS && OBS.run)) {
      return [{ kind: 'live', text: '모니터링 — 표시할 상태 정보가 없습니다. 이 화면 파일에는 실시간 주소가 없습니다 · 구조만 표시됩니다',
        dev: '로컬에서 만든 파일이거나, 배포본이면 서명 함수가 아직 주소를 굽지 않았다' }]
    }
    return []
  }
  // §3k K5: 수집 실패의 «곳»은 부모 사슬로 푼 사람 말이다(계정 마스킹 · 리전 · VPC 이름). 대상 id·`scope` 원어를
  // 화면에 내지 않는다. 못 풀면(모델에 없는 대상) 대상 id 를 그대로 쓴다 — 조용히 지우지 않는다(불변식 ④).
  function partialPlace (p) {
    let acct = p.acct || null; let region = p.region || null; let vpc = null; let g = 0
    for (let n = mById.get(p.target) || byId.get(p.target); n && g++ < 40;
      n = n.parent_id ? mById.get(n.parent_id) || byId.get(n.parent_id) : null) {
      if (n.type === 'vpc' && !vpc) vpc = n.name || null
      if (n.type === 'region' && !region) region = n.name || n.region || null
      if (n.type === 'account' && !acct) acct = n.attrs ? n.attrs.account || n.attrs.id || null : null
    }
    const bits = [acct ? '계정 ' + acctText(acct) : null, region, vpc].filter(Boolean)
    return bits.length ? bits.join(' / ') : String(p.target)
  }
  // (곳, 이유) 하나에 한 묶음 — 순서는 `T.partial` 순서(결정론).
  function partialGroups () {
    const G = new Map()
    for (const p of (T.partial || [])) {
      const place = partialPlace(p); const word = reasonWord(p.code); const k = place + '|' + word
      if (!G.has(k)) G.set(k, { place, word, items: [] })
      G.get(k).items.push(p)
    }
    return [...G.values()]
  }
  function bannerRows () {
    // §3k K3: 사람 문장은 코드의 사람 말 · 원문(코드 + AWS 메시지)은 `title` 과 개발 설명.
    // §3k K5: 같은 곳·같은 이유는 한 줄이고, 못 읽은 **종류**를 앞 4개까지 이름으로 말한다(나머지는 `외 N종`).
    const rows = partialGroups().map((g) => {
      const ts = [...new Set(g.items.map((p) => p.rtype).filter(Boolean))].map(typeWord)
      const what = ts.length ? ` · ${ts.slice(0, 4).join(', ')}${ts.length > 4 ? ` 외 ${ts.length - 4}종` : ''}` : ''
      const raw = g.items.map((p) => `${p.code} — ${p.reason}`)
      // 🔁 2026-10-20(§17.26 C-3): 이 줄의 자리 = 왼쪽 아래 「!」 상자 · 글 = 고객 말(합니다체 · `POP_REASON`)
      return { kind: 'partial', text: `${g.place}${what} — ${popReason(g.items[0].code)}`,
        title: raw.join('\n'), dev: `원문: ${raw.join(' / ')} · 사람 말(배너 옛 글): ${g.word}` }
    })
    // §3h H2: **권한 부재는 숫자로 말한다.** 🔴 새 도식은 사선을 칠하지 않는다(W5) — 그린 것은 정보창 「수집」
    //    줄이 말하고, 자원이 하나도 안 읽힌 계정은 그릴 칸이 없어 캔버스에 흔적이 0이다. 그 계정이 존재한다는
    //    사실은 이 줄이 **유일한 근거**다.
    // 🔴 분모를 만들지 않는다(사용자 결정 2026-09-21): 「D개 중 A개」의 분모가 계약에 없어서
    //    `B = A` 를 쓰면 「전부 읽었다」는 거짓 안심이 된다. `T.discovery.account_ids` 가 오면 붙는다.
    // 🔁 2026-10-20(§17.26 C-3): **계정마다 한 줄** · 자리 = 왼쪽 아래 「!」 상자 맨 위 · 글 = 고객 말(내부 어휘 0 — 「스택 B」 대신 설치 안내의 단계).
    //    그 계정을 하나도 못 읽었으면(구조 행에 `ok` 가 없다) 「캔버스가 비어 있습니다」, 아니면 거부된 리전을 댄다.
    const cvA = covAccounts()
    if (cvA.denied) {
      const byAcct = new Map()
      for (const c of COV) {
        if (!c || !c.account_id) continue
        const e = byAcct.get(c.account_id) || { ok: false, deny: new Set() }
        if (c.status === 'ok') e.ok = true
        if (c.status === 'denied') e.deny.add(c.region || '전역')
        byAcct.set(c.account_id, e)
      }
      for (const [a, e] of [...byAcct.entries()].filter(([, x]) => x.deny.size).sort((x, y) => String(x[0]).localeCompare(String(y[0])))) {
        const regs = [...e.deny].sort()
        rows.push({ kind: 'deny', text: e.ok
          ? `계정 ${acctText(a)} — 이 계정의 일부 리전(${regs.slice(0, 4).join(', ')}${regs.length > 4 ? ` 외 ${regs.length - 4}` : ''})에 대한 읽기 권한이 없어 ` +
            '캔버스에 빠진 리소스가 있습니다. 해당 계정의 읽기 전용 역할(설치 안내 4단계)을 확인하면 다음 수집부터 표시됩니다.'
          : `계정 ${acctText(a)} — 이 계정에 대한 읽기 권한이 없어 캔버스가 비어 있습니다. ` +
            '해당 계정에 읽기 전용 역할(설치 안내 4단계)을 만들면 다음 수집부터 표시됩니다.',
        dev: '권한이 막힌 곳은 「없다」가 아니라 「모른다」다(§3h) · 그린 것은 정보창 「수집」 줄도 말한다 · ' +
          '자원이 하나도 안 읽힌 계정은 그릴 칸이 없어 **이 줄이 유일한 근거**다' +
          (cvA.found ? ` · 발견한 계정 ${cvA.found}개` : '') + ` · 거부된 리전: ${regs.join(', ')}` })
      }
    }
    // ---- 모니터링(ADR 0006). 🔴 이 줄은 **세 가지 다른 사실**을 갈라 말한다 ----
    //  ⓐ 판정 분포 — 무엇이 흐르고 무엇이 이상인가.
    //  ⓑ **앉을 자리가 없어 그리지 못한 관측의 수** — 조용히 버리면 「나머지는 다 정상」으로 읽힌다
    //     (불변식 ④). 엣지 관측은 캔버스가 선을 그리지 않는 관계라서, 컨테이너 관측은 테두리 채널을
    //     컨테이너 유형이 이미 쓰고 있어서 그렸을 자리가 없다.
    //  ⓒ **어휘를 섞지 않는다** — 「AWS 가 값을 주지 않는다」(`?`)와 「권한이 없어 못 읽었다」(권한 줄)
    //     는 고칠 주체가 다르다. 뒤엣것은 위 `deny` 줄이 말한다(ADR 0006 §O7 규칙 2).
    rows.push(...liveRows())
    const mon0 = rows.length
    if (OBS && OBS.run) {
      const os = obsStats(L)
      if (OBS_STALE) {
        // 🔴 토글과 **무관하게** 말한다 — 거부의 이유를 모르면 사람은 「모니터링이 고장났다」로 읽고
        //    버튼을 계속 누른다. 고칠 주체는 수집기(짝 맞는 두 파일을 같이 굽는다)다.
        // 사람 문장 = 무엇이 어긋났나(구조 수집 결과가 달라짐 / 짝이 안 맞음) + 할 일. 🔴 두 경우의 할 일이 다르다(`pairFix`).
        // 🔴 지문이 다르다 ≠ 네트워크가 바뀌었다(#32 리뷰 2, 2026-09-28): 지문은 카드·선 목록이라, 그 회차에 한 종류를
        //    못 읽어 카드가 빠져도 달라진다. 「구성이 바뀌었다」고 단정하면 사람은 누가 무엇을 바꿨는지 찾으러 간다.
        rows.push({ kind: 'mon', text: (OBS_PAIRING.by !== 'digest'
          ? '상태 데이터가 이 구성도와 맞지 않습니다 — 아이콘에 표시하지 않았습니다 · '
          : OBS_PAIRING.newer === 'page'
            ? '상태 데이터가 **이 구성도보다 이전 구조**를 기준으로 합니다(구조 수집 결과가 달라졌습니다) — 상태를 아이콘에 표시하지 않았습니다 · '
            : '이 구성도를 만든 뒤 **구조 수집 결과가 달라졌습니다**(구성이 바뀌었거나, 그 회차에 일부를 읽지 못해 아이콘이 빠졌습니다) — 상태를 아이콘에 표시하지 않았습니다 · ') + PAIR_FIX,
          dev: (OBS_PAIRING.by === 'digest'
            ? `상태는 구조 스냅샷 ${kstAt(OBS_PAIR)} 의 아이콘·선 목록을, ` +
              `지금 그림은 ${kstAt(T.run.collected_at)} 의 목록을 보고 있다(내용 지문이 다르다). 겹쳐 그리면 새로 생긴 것의 상태는 ` +
              '앉을 자리가 없고 없어진 것은 「미측정」처럼 보이므로 **얹지 않는다**. '
            : `관측은 구조 스냅샷 ${kstAt(OBS_PAIR)} 를 보고 만들어졌는데 지금 그림은 ${kstAt(T.run.collected_at)} 이다. ` +
              '겹쳐 그리면 없어진 것에 상태가 붙고 남은 것이 「정상」으로 보이므로 **얹지 않는다**. ') +
            '이력 되짚기도 같은 이유로 멈춘다.' })
      } else if (!S.monitor) {
        // 🔴 켤 수 있다는 사실을 화면이 말해야 한다. 안 말하면 「데이터는 오는데 아무도 안 본다」가
        //    되고, 그 층은 **돈을 쓰면서** 아무 값도 못 만든다(ADR 0005 의 기본 꺼짐과 짝이다).
        rows.push({ kind: 'mon', text: `상태 데이터가 있습니다(기준 ${kstAt(OBS.run.observed_at)}) — ` +
          `상단 «모니터링»을 누르면 아이콘에 표시됩니다. 지금 이 구성도는 **${S.util && UTROWS ? '구조 + 사용률' : '구조만'}**입니다.`, dev: '' })
      } else if (!os.rows) {
        rows.push({ kind: 'mon', text: '모니터링 — 상태 문서에 관측 항목이 없습니다 · 아이콘은 켜기 전과 같습니다',
          dev: '**아이콘 테두리는 켜기 전과 똑같다**(없는 상태를 지어내지도, 「전부 미측정」으로 지우지도 ' +
            '않는다) · 흐르는 선도 0개다.' })
      } else {
        // 사람 문장 = 기준 시각 + 판정 분포 + 「일부 못 읽은 채 판정」 수(§3k K1 🔴 — 못 읽은 것의 **존재와 수**는
        // 사람 문장에 남는다). 나머지(층·초록의 근거·앉지 못한 관측의 회계)는 개발 설명이다.
        rows.push({ kind: 'mon', text: `모니터링 — 기준 ${kstAt(OBS.run.observed_at)} · ` +
          `아이콘 ${os.cards}곳: 흐름 ${os.live} · 정상 ${os.up} · 이상 ${os.down} · ` +
          `관측 불가 ${os.none} · 미측정 ${os.unobserved}` +
          (os.cardPartial
            ? ` · 그중 ${os.cardPartial}곳은 **근거의 일부를 읽지 못한 상태로** 판정했습니다 — 「정상」과 같게 보지 마세요`
            : ''),
        dev: (OBS.layers && OBS.layers.metric
          ? '지표 층까지 읽었다(호출당 과금)'
          : '구조 조회만으로 판정했다 — 「흐르는가」는 지표 층을 켜야 나온다') +
          ' · 「관측 불가」는 AWS 가 값을 주지 않는다는 뜻이고(`?`), 권한이 없어 못 읽은 것은 ' +
          '위 «권한» 줄이 말한다(다른 어휘다)' +
          // 🔴 흐르는 선의 **근거를 말한다**. 초록은 **그 구간 자신을 잰 것**뿐이다
          //    (종단 추론 폐기 · ADR 0008 · S2). `flowByEnds` 가 0 이 아니면 회귀다.
          (os.flows
            ? ` · 초록으로 흐르는 선 ${os.flows}개(전부 **그 선 자신의 관측** ${os.flowByEdge}` +
              `${os.flowByEnds ? ` · 🔴 근거 없는 초록 ${os.flowByEnds}` : ''})`
            : ' · 초록으로 흐르는 선은 0개다 — 어느 경로도 「지금 흐른다」로 관측되지 않았다')
          +
          // 🔴 **거절한 것을 말한다.** 양 끝이 흐르는데 선이 회색이면 사람은 「고장인가」를 묻는다 —
          //    답은 「그 구간 자신을 잰 근거가 없다」다.
          (os.flowEndsOnly
            ? ` · 양 끝 자원만 흐르는 선 ${os.flowEndsOnly}개는 **초록으로 칠하지 않았다** — 그 ` +
              '구간 자신을 잰 근거가 없다(양 끝의 트래픽이 이 경로로 갔다는 보장이 없다)'
            : '')
          +
          // 🔴 「판정은 났지만 일부를 못 읽었다」를 정상에 섞지 않는다(계약 `partial`).
          (os.cardPartial
            ? ` · 일부를 못 읽은 채 판정된 ${os.cardPartial}곳(\`~\`) — 지표 호출이 실패해도 판정은 남기기 때문이다`
            : '') +
          (os.containers
            ? ` · 컨테이너 ${os.containers}곳의 상태는 그리지 않는다 — 테두리는 «컨테이너 유형» ` +
              '채널이 쓰고 있고, 그 상태는 안쪽 아이콘이 이미 말한다'
            : '') +
          // 🔴 **라우트는 실제로 선으로 그려진다**(어댑터가 서브넷→대상으로 재투영한다) — 그것까지
          //    「선을 그리지 않는 관계」로 말하면 거짓이다(2026-09-24 수정).
          (os.edgeOnWire
            ? ` · 관계 관측 ${os.edgeOnWire}건은 그려진 선에 앉았다`
            : '') +
          (os.edgeUndrawn
            // 새 도식은 인터넷·LB 배치·온프렘 쪽 관계도 선으로 그리지 않는다(§17.20) — CloudFront 원본은 선이지만 관측 행이 없다(#43 · NO_STATE_EDGES)
            ? ` · 관계 관측 ${os.edgeUndrawn}건은 이 화면이 선으로 그리지 않는 관계다(인터넷 쪽 길 · ` +
              'LB 배치 · 서브넷 연관 · 보안그룹 참조 등) — 없는 선에 상태를 붙이지 않는다'
            : '') +
          // 🔴 카드에도 컨테이너에도 앉지 못한 **노드** 관측. 이것을 안 세면 화면 어디에도 없는
          //    보안그룹·NACL 수십~수백 건이 사라지고 사람은 「나머지는 다 정상」으로 읽는다.
          (os.nodeUnplaced
            ? ` · 자원 관측 ${os.nodeUnplaced}건은 이 화면에 그려지는 아이콘이 아예 없는 종류다` +
              '(보안그룹·NACL 등) — 그 판정은 어디에도 얹히지 않았다'
            : '') })
      }
    }
    // ---- 지표 대상 두 숫자(§3h H2 둘째 줄 · ADR 0005 D5) ----
    // 🔴 토글과 **무관하게** 말한다 — 켜기 전에 「켜면 어느 계정이 움직이는가」를 알아야 한다.
    //    제외된 계정은 켜도 영원히 움직이지 않고, 그 이유가 화면 어디에도 없으면 「고장」으로 읽힌다.
    // 🔴 짝이 어긋난 문서에서는 말하지 않는다(위 `OBS_STALE` 줄과 같은 이유 — 수치가 거짓이 된다).
    const mt = OBS && OBS.run && !OBS_STALE ? monTargets() : null
    if (mt) {
      const ex = mt.excluded.map(acctText)
      rows.push({ kind: 'montgt', text: `지표 대상 ${mt.target.length} 계정 · ` +
        `제외 ${mt.excluded.length} 계정(스택 B 없음)` +
        (mt.unknown.length ? ` · 확인 못함 ${mt.unknown.length} 계정` : '') +
        (ex.length ? ` · 제외: ${ex.slice(0, 4).join(', ')}${ex.length > 4 ? ` 외 ${ex.length - 4}` : ''}` +
          ' — 스택 B 를 배포하면 다음 관측부터 대상이 됩니다' : ''),
      dev: '모니터링은 멤버 역할(스택 B)에 들어간 계정만 부른다. 들어가지 못한 계정에 지표를 부르면 ' +
        '거부되고, 거부된 호출도 과금된다' +
        (mt.unknown.length ? ' · 「확인 못함」은 역할 진입이 거부도 성공도 아니었던 계정이다' +
          '(요청 제한·세션 생성 실패 등) — 제외로 세지 않았다' : '') })
    }
    // ---- 옵션형 근거 «출처»(ADR 0009 D6 · 사용자 지시 2026-09-24) ----
    // 🔴 모니터링을 **켠** 화면에서만 말한다: 이 문장은 「지금 보고 있는 판정이 무엇을 근거로
    //    나왔나」이므로, 판정을 얹지 않은 화면에서 말하면 결론을 먼저 말하는 것이 된다.
    if (S.monitor && !OBS_STALE && srcStats().rows) {
      const ss = srcStats()
      const list = (xs) => xs.map(srcLabel).join(' · ')
      // 🔴 사유는 **문서가 준 것**(`why`)만 쓴다 — 추측해 적으면 고객이 못 고칠 조치를 한다.
      const why = (xs) => { const w = xs.map((s) => s.why).find(Boolean); return w ? ` — ${w}` : '' }
      // 🔴 **「활성화되어 있지 않아」는 `unknown` 이 0 일 때만 쓴다.** 켜짐을 확인 못한 것이 하나라도
      //    있으면 그 문장은 이미 켠 고객에게 「켜세요」라고 말하는 것이 된다(ADR 0009 D2 의 규율).
      // 🔴 `!ss.off.length` 도 게이트다 — 꺼진 근거가 **하나도 없으면**(전부 「해당 자원 없음」)
      //    「활성화되어 있지 않아」는 고객이 켤 수 없는 것을 켜라는 말이 된다(실측 2026-09-25).
      const head = ss.used.length
        ? `추가 근거 ${ss.used.length}종을 함께 반영했습니다`
        : (ss.unread.length || ss.unknown.length || !ss.off.length)
            ? '지표만으로 판정했습니다'
            : 'VPC 흐름 로그·Container Insights 가 꺼져 있어 지표만으로 ' +
              '판정했습니다'
      const parts = []
      if (ss.used.length) parts.push(`판정에 쓴 근거: ${list(ss.used)}`)
      if (ss.unread.length) {
        // 고칠 주체가 **우리**다(고객이 이미 켰다) — 그래서 「켜세요」가 아니라 사유를 적는다.
        // 🔴 「사용」과 겹칠 수 있다(한 종류 안에 읽은 로그와 못 읽은 로그) — 로그별 사유는 아래 `srclog` 줄이 말한다.
        parts.push(`켜져 있지만 읽지 않은 곳이 있는 근거 ${ss.unread.length}종: ` +
          list(ss.unread) + why(ss.unread))
      }
      if (ss.unknown.length) {
        parts.push(`켜짐을 확인하지 못한 근거 ${ss.unknown.length}종: ` +
          list(ss.unknown) + why(ss.unknown))
      }
      if (ss.off.length) parts.push(`꺼져 있는 근거 ${ss.off.length}종: ${list(ss.off)}`)
      // 🔴 여기는 **조치할 것이 없는** 목록이다 — 그래서 「0/0」을 적지 않는다(분모 0 을 보여 주면
      //    「0개가 켜져 있다」로 읽히고, 그것이 곧 「켜세요」가 된다). 이름 + 「자원 0개」만 말한다.
      if (ss.na.length) {
        parts.push(`해당 자원이 없어 판단에서 제외한 근거 ${ss.na.length}종: ` +
          ss.na.map((s) => `${SRC_LABEL[s.kind] || s.kind}(${SRC_UNIT[s.scope.unit] ||
            s.scope.unit} 0개)`).join(' · '))
      }
      // §3k: 머리 문장은 사람 문장(ADR 0009 D6 — 사용자 결정 2026-09-24) · 근거 조각 목록은 개발 설명.
      rows.push({ kind: 'src', text: '옵션형 근거 — ' + head,
        dev: (parts.length ? parts.join(' · ') + ' · ' : '') +
        '켜기는 고객의 결정이고 이 툴은 아무것도 켜지 않는다(읽기 전용) · 위 «권한»·«수집 실패» ' +
        '줄과 다른 축이다 — 그쪽은 우리 권한, 이쪽은 고객이 켠 것이다' })
      // 🔴 못 읽은(또는 일부만 읽은) 로그 **한 개당 한 줄**(§3k K3 추가 · 대장 108 · 검사 120). 같은 이유끼리도 합치지
      //    않는다 — 합치면 어느 로그를 고칠지가 화면에서 사라진다. 코드·엔진 문장·`detail` 은 `title` 에만 간다.
      //    `duplicate_covered` 는 줄을 만들지 않는다(경고가 아니다).
      for (const s of srcRows().slice().sort((a, b) => a.kind.localeCompare(b.kind))) {
        for (const r of flowLogsOf(s).slice().sort((a, b) => a.id.localeCompare(b.id))) {
          if (!unreadLog(r) && !partialLog(r)) continue
          const where = r.attached_to ? nameOfId(r.attached_to) : (r.level === 'network_interface' ? 'ENI 단위' : '연결 대상 미확인')
          rows.push({ kind: 'srclog',
            text: `${SRC_LABEL[s.kind] || '흐름 로그'} ${r.id}(${where}) — ` +
              `${partialLog(r) ? '일부만 읽음' : '켜져 있지만 못 읽음'}: ${flowWhy(r.why_code)}`,
            title: flowLogTitle(r) })
        }
      }
    }
    // 과거를 고른 사실은 배너가 아니라 **오른쪽 비교 칸의 머리**가 말한다(§3j J1) — 이 배너는 왼쪽 = 「지금」의
    // 배너이고, 과거를 골라도 왼쪽 그림은 그대로다. 되짚지 못한 것은 그 칸의 글자 목록에 나온다.
    // §3g G1: 「무엇을 좁혔나」를 화면이 말한다. 🔴 마스킹 계정ID 는 **여기**에 낸다 — 상단바에 두면
    // 검사 68 이 `#top.textContent` 에서 그 패턴을 세어 FAIL 한다(`#bans` 는 `#top` 밖이다).
    // 🔴 「수집 범위는 그대로다」를 같은 줄에서 말한다(G3) — 안 말하면 「화면만 좁혔는데 이력이
    //    비었다」를 나중에 아무도 설명하지 못한다.
    // 🔁 2026-10-20(대장 134): 고른 계정 **전부**를 마스킹 ID 로 늘어놓는다(팝오버 순서 = `accounts`). 수는 말하지 않는다(§17.18-4 ·
    //    「N개」는 버튼이 말한다 — 여기서 또 세면 개수 문장이 된다).
    if (SCOPE) {
      const names = accounts.map((a, i) => (SCOPE_SET.has(a.id) ? acctLabel(i) : null)).filter(Boolean).join(', ')
      rows.push({ kind: 'scope', text: `${names} 만 보고 있습니다 · 외부 연결 ${xwireCount(L)} — ` +
        '화면만 좁혔고 수집 범위와 비용은 그대로입니다. 「계정: 전체」를 누르면 돌아갑니다.',
      dev: '범위 밖과 잇는 선은 흐린 TGW 에 닿거나 「못 그린 관계」로 남는다 — 지우지 않는다(§17.22)' })
    }
    // §3k: 아래 unplaced·unwired 두 줄은 **개발 설명**이다(글자는 개발자 화면·`dump().bans`).
    //    🔴 그러나 「그리지 못한 것이 있다」는 사실과 그 수는 사람 문장 한 줄(`undrawn`)에 남긴다 —
    //    수를 지우면 있는데 없다고 그린 화면이 된다(불변식 ④).
    // §3i: 「확인하지 못했다」(partial) 와 **다른 어휘**다 — 이쪽은 이 화면의 규칙 공백이고,
    // 조치 주체가 고객이 아니라 개발이다. 합치면 고객이 권한을 줘도 화면이 안 변한다.
    if ((S.unplaced || []).length) {
      rows.push({ kind: 'unplaced', text: '', dev: `그릴 규칙이 없어 캔버스에 세우지 못한 리소스 ` +
        `${S.unplaced.length}개 — 표에서 본다(위치를 날조하지 않는다). 종류: ` +
        [...new Set(S.unplaced.map((r) => r.type))].sort().join(', ') +
        ' · 규칙을 늘리는 것은 §3 개정이다' })
    }
    // 🔴 관계는 **따로 센다.** 「리소스 N개」에 선을 섞으면 그 수가 거짓이 되고, 고칠 곳도 다르다
    //    (노드는 타입 규칙, 선은 종류 규칙·끝점). 사람 문장에서도 두 수를 나눠 말한다.
    if ((S.unwired || []).length) {
      rows.push({ kind: 'unwired', text: '', dev: `그릴 규칙이 없어 선을 긋지 못한 관계 ` +
        `${S.unwired.length}개 — 표에서 본다(없는 관계를 그리지 않는다). 종류: ` +
        [...new Set(S.unwired.map((r) => r.type))].sort().join(', ') +
        ' · 잔차에 걸린 선을 조상으로 환산하면 존재하지 않는 관계가 된다' })
    }
    // 🔁 2026-10-20(§17.26 C-7): 자리 = 왼쪽 아래 「!」 상자 「캔버스에 없는 리소스」 절의 머리. 수는 그 표(`listRows`)에서 센다
    const nAll = listRows().length; const nRel = (S.unwired || []).length; const nNote = (S.notes || []).length; const nNode = nAll - nRel - nNote
    if (nNode || nRel || nNote) {
      rows.push({ kind: 'undrawn', text:
        [nNode ? `캔버스에 없는 리소스 ${nNode}개` : '', nRel ? `그림에 없는 연결 ${nRel}개` : '', nNote ? `그리지 못한 Global Accelerator 대상 ${nNote}곳` : ''].filter(Boolean).join(' · ') })
    }
    if (S.mode === 'snapshot') {
      rows.push({ kind: 'mode', text: `편집한 구성도입니다(${S.history.length}건) — «원래대로»를 누르면 편집이 취소됩니다` })
    }
    // 지연 문구는 경고가 아니라 **출처 표기**다(그래서 상시). 언제 찍은 그림인지 같이 밝힌다 —
    // 스냅샷을 라이브로 오독하는 것이 이 화면의 가장 큰 오판 경로다. 수집 소요 시간은 사양 문장이다
    // (§3k — `docs/product-spec-notes.md` §2).
    rows.push({ kind: 'slow', text: `${COPY.slow} (${kstAt(T.run.collected_at)})`, dev: COPY.slowDev })
    return rows
  }
  function syncChrome (L) {
    if (bansEl) {
      // §13-54: 요약 1줄 + 접힌 상세. 경고가 상시로 캔버스 높이를 먹으면 그 다음부터 안 읽힌다
      // (v8 실측: 배너 4줄이 상단을 먹어 그림이 지저분했다). **개수는 요약에 보여야 한다.**
      const rows = bannerRows()
      // 🔁 2026-10-20(사용자 결정 · §17.26 C-3·C-7 · 대장 129): 「⚠ 수집 실패 N건」·「⚠ 권한 없어 못 읽은 계정 N」·「⚠ 못 그린 …」 알약과
      //    그 줄(`partial`·`deny`·`undrawn`)은 상단 배너에 **없다** — 왼쪽 아래 빨간 「!」 상자가 같은 줄을 고객 말로 말한다(`renderList`).
      const pills = []
      // ADR 0006: 상세는 접혀 있는 것이 기본이므로(§13-54) **켤 수 있다는 사실과 이상 건수는
      // 요약에** 있어야 읽힌다. 🔴 「흐름」과 「이상」을 한 알약에 합치지 않는다 — 합치면 이상 0
      // 인 화면과 이상이 있는 화면이 같은 색으로 보인다.
      // 실시간 알약 1개(문장은 위 `liveRows` 몫). 🔴 읽기 실패·멈춤은 warn 이다 — 상태가 지워진 화면을
      //    정상 화면과 같은 색으로 두면 「이상 0」으로 읽힌다.
      if (LIVE.busy) pills.push('<span class="bpill">실시간 켜는 중</span>')
      else if (LIVE.on) {
        if (LIVE.phase === 'fail') pills.push('<span class="bpill warn">⚠ 상태 못 읽음</span>')
        else if (LIVE.phase === 'stale') pills.push('<span class="bpill warn">⚠ 상태 갱신 멈춤</span>')
        // 🆕 B-13 — 구조가 바뀌었다. 알약이 곧 버튼이다(누를 때만 이 탭이 최신 화면으로 넘어간다 · 검사 106).
        else if (LIVE.phase === 'moved') pills.push(canGoLatest() ? '<span class="bpill warn bgo" data-golatest>⚠ 구조 바뀜 — 최신 화면 열기 ▸</span>' : '<span class="bpill warn">⚠ 구조 바뀜 — 최신 index.html</span>')
        else if (LIVE.renewWhy) pills.push(`<span class="bpill warn">⚠ 연장 실패 — ${esc(msHm(LIVE.lease))} 까지</span>`)
        // 🆕 제어 키를 못 읽었다(보강 4) — 화면은 그대로지만 다른 창의 변경을 모른다
        else if (LIVE.ctlWhy) pills.push('<span class="bpill warn">⚠ 다른 창 변경 확인 못 함</span>')
        // 🆕 진행 표시(사용자 결정 2026-10-28) — 상세는 접혀 있는 것이 기본이라 경과는 알약에도 둔다(8분 창 안에서만 · 기준 = 마지막 읽기)
        else if (LIVE.phase === 'wait') pills.push(LIVE.pairWait ? '<span class="bpill">실시간 — 새 구조 관측 대기</span>'
          : liveWaiting(Date.now()) ? `<span class="bpill">실시간 — 관측 중 · 경과 ${esc(mss((LIVE.lastCheck || LIVE.asked) - LIVE.asked))}</span>`
            : '<span class="bpill">실시간 — 첫 관측 대기</span>')
        // 🆕 따라 보는 창(보강 4 H) — 「다른 창에서 켠 실시간 · 약 N분 뒤 자동 멈춤」
        else if (LIVE.mode === 'follow') pills.push(`<span class="bpill">다른 창에서 켠 실시간 · ${esc(aboutMin((LIVE.end || LIVE.lease) - (LIVE.ctlAt || Date.now())))} 뒤 자동 멈춤</span>`)
        else pills.push(`<span class="bpill">실시간 ~${esc(msHm(LIVE.until))}</span>`)
      } else if (LIVE.notePill) {
        pills.push(`<span class="bpill${LIVE.noteWarn ? ' warn' : ''}">${esc(LIVE.notePill)}</span>`)
      } else if (LIVE_SLOT && !S.monitor && liveUrlDead(Date.now())) {
        pills.push('<span class="bpill warn">⚠ 주소 만료 — 최신 index.html</span>')
      } else if (LIVE_SLOT && !S.monitor) {
        pills.push('<span class="bpill">실시간 가능 — «모니터링»</span>')
      } else if (UNSIGNED_PAGE) {
        pills.push('<span class="bpill warn">⚠ 서명 전 화면 파일 — 다시 받기</span>')
      }
      if (OBS && OBS.run) {
        const os = obsStats(L)
        // 🔴 **짝이 어긋난 문서에서는 수치를 말하지 않는다.** 예전에는 `OBS_STALE` 을 보지 않아
        //    「흐름 0 · 정상 0」이라 적었는데, 그건 「거부했다」가 아니라 **「전부 죽었다」**로 읽힌다.
        //    꺼진 상태에서 버튼을 권하지도 않는다 — 눌러도 얹히지 않는다(이유는 배너가 말한다).
        // 「구조 바뀜」 알약이 같은 사실을 이미 말하면 한 번만 말한다(정보의 출처는 하나 — canvas-principles §16 2).
        if (OBS_STALE) pills.push(LIVE.on && LIVE.phase === 'moved' ? '' : '<span class="bpill warn">⚠ 상태 데이터 짝 안 맞음</span>')
        else if (!S.monitor) pills.push('<span class="bpill">상태 데이터 있음 — «모니터링»</span>')
        else if (!os.rows) pills.push('<span class="bpill">관측 항목 0</span>')
        else {
          // 사용자 결정 2026-09-24: 알약은 **키워드 + 수치**만이다(문장·기준 시각·근거는 배너 몫).
          pills.push(`<span class="bpill">흐름 ${os.live} · 정상 ${os.up}</span>`)
          if (os.down) pills.push(`<span class="bpill warn">⚠ 이상 ${os.down}</span>`)
          if (os.none) pills.push(`<span class="bpill">관측 불가 ${os.none}</span>`)
          if (os.cardPartial) pills.push(`<span class="bpill warn">~ 일부 못 읽음 ${os.cardPartial}</span>`)
        }
        // ADR 0009 D6 — 옵션형 근거 알약 **1개**(문장은 배너 몫이다). 🔴 위 `수집 실패`·
        // `권한 없어 못 읽은 계정` 과 **어휘를 합치지 마라**: 저것을 본 사람은 「스택 B 를 배포해야
        // 하나」를 묻고 이것을 본 사람은 「흐름 로그를 켜야 하나」를 묻는다 — 섞으면 둘 다 틀린
        // 행동을 한다. 🔴 `sources` 가 빈 배열이면(탐지 안 함) 알약을 띄우지 않는다.
        // §3h H2 둘째 줄 — 상세는 접혀 있으므로 **두 숫자는 요약에** 있어야 읽힌다(토글 무관).
        const mt = OBS_STALE ? null : monTargets()
        if (mt) {
          pills.push(`<span class="bpill${mt.excluded.length || mt.unknown.length ? ' warn' : ''}">` +
            `지표 대상 ${mt.target.length} · 제외 ${mt.excluded.length}` +
            `${mt.unknown.length ? ` · 확인 못함 ${mt.unknown.length}` : ''}</span>`)
        }
        const ss = srcStats()
        if (S.monitor && !OBS_STALE && ss.rows) {
          if (ss.pill === 'mixed') {
            // ADR 0009 D6 추가(2026-09-28) — 못 읽음이 사용을 가리지 않는다. 알약은 여전히 **1개**다.
            pills.push(`<span class="bpill warn">추가 근거 ${ss.used.length}종 · ⚠ 못 읽음 ${ss.unread.length}종</span>`)
          } else if (ss.pill === 'unread') {
            pills.push(`<span class="bpill warn">⚠ 근거 못 읽음 ${ss.unread.length}종</span>`)
          } else if (ss.pill === 'used') {
            pills.push(`<span class="bpill">추가 근거 ${ss.used.length}종</span>`)
          } else if (ss.pill === 'unknown') {
            // 🔴 「꺼짐」이 아니라 「확인 못함」이다 — 이미 켠 고객에게 「켜세요」라고 말하지 않는다.
            pills.push(`<span class="bpill">근거 확인 못함 ${ss.unknown.length}종</span>`)
          } else {
            pills.push('<span class="bpill">지표 기반만</span>')
          }
        }
      }
      // §3k: 상한 미달성은 알약을 띄우지 않는다 — 사람이 할 일이 없다(개발 설명은 `dump().bans`).
      if (S.mode === 'snapshot') pills.push(`<span class="bpill snap">편집한 그림 ${S.history.length}건</span>`)
      pills.push(`<span class="bsrc">${esc(kstAt(T.run.collected_at))} 스냅샷</span>`)
      bansEl.className = S.bansOpen ? 'open' : ''
      bansEl.innerHTML = `<div class="bansum" data-bantoggle>${pills.join('')}` +
        `<span class="bmore">${S.bansOpen ? '접기 ▴' : '자세히 ▾'}</span></div>` +
        '<div class="bandetail">' + rows.filter((r) => r.text && !POP_KINDS.has(r.kind)).map((r) =>
          `<div class="ban ${r.kind}" data-kind="${r.kind}"${r.title ? ` title="${esc(r.title)}"` : ''}>` +
          `${fmtBan(r.text)}</div>`).join('') + '</div>'
    }
    // 빈 상태는 «그려진 아이콘 0»(`cardCount`) 이다 — 틀이 남아 있어도 아이콘이 없으면 빈 상태다.
    if (emptyEl) {
      emptyEl.textContent = COPY.empty
      emptyEl.classList.toggle('on', cardCount(L) === 0)
    }
    // §3k K4: 편집이 없으면 칩·버튼을 숨긴다(DOM 은 남긴다 — 검사 84 가 순서·id 집합을 잰다).
    //    「라이브」 초록 칩은 실시간 모니터링으로 오독됐다 — 편집한 그림일 때만 무엇을 할 수 있는지 말한다.
    const chip = document.getElementById('modechip')
    if (chip) {
      chip.textContent = `편집한 그림 ${S.history.length}건`
      chip.className = 'chip ' + S.mode
      chip.hidden = S.mode === 'live'
    }
    const back = document.getElementById('btn-live')
    if (back) { back.textContent = '원래대로'; back.hidden = S.mode === 'live' }
    renderList()   // 왼쪽 아래 「!」 — 배너와 같은 때 같은 줄로 다시 쓴다(줄 출처가 하나다)
  }
  let INIT_VIEW = null
  function zoomBy (f) {
    const r = svgEl.getBoundingClientRect()
    const z = Math.max(0.12, Math.min(3, S.zoom * f))
    const mx = r.width / 2; const my = r.height / 2
    S.tx = mx - (mx - S.tx) * (z / S.zoom); S.ty = my - (my - S.ty) * (z / S.zoom)
    S.zoom = z; applyTransform()
    return { zoom: S.zoom, tx: S.tx, ty: S.ty }
  }
  function resetView () {
    if (!INIT_VIEW) return null
    S.zoom = INIT_VIEW.zoom; S.tx = INIT_VIEW.tx; S.ty = INIT_VIEW.ty
    applyTransform(); return { zoom: S.zoom, tx: S.tx, ty: S.ty }
  }
  // 프로토타입의 «새로고침»은 같은 스냅샷 재배치다 — 재수집은 엔진 몫이고 여기 없다.
  function refresh () { return relayout() }
  function toggleFull () {
    const el = document.getElementById('stage')
    try {
      if (document.fullscreenElement) { document.exitFullscreen(); return false }
      if (el && el.requestFullscreen) { el.requestFullscreen(); return true }
    } catch (e) { /* 전체 화면을 못 여는 브라우저 — 그림은 그대로 둔다 */ }
    return false
  }

  // ---- §13-60 내보내기: 캔버스만 ----
  const tsName = (ext) =>
    'nettopo-' + new Date().toISOString().replace(/[-:]/g, '').replace(/\.\d+Z$/, 'Z') + '.' + ext
  function exportSvgText () {
    const src = svgEl.cloneNode(true)
    src.removeAttribute('style'); src.removeAttribute('class'); src.removeAttribute('id')
    src.setAttribute('xmlns', 'http://www.w3.org/2000/svg')
    src.setAttribute('xmlns:xlink', 'http://www.w3.org/1999/xlink')
    // 새 도식(§17)은 범례를 그림 안에 넣는다(§17.18-2) — 화면의 범례는 SVG 밖 HTML 이라 복제본에 없다. 오른쪽에 붙이고 판을 넓힌다
    const lg = NT_AWS.legendSvg()
    const LG_GAP = 48; const cw = lg ? L.canvasW + LG_GAP + lg.w : L.canvasW; const ch = lg ? Math.max(L.canvasH, lg.h) : L.canvasH
    const h = Math.round(EXPORT_W * ch / cw)
    src.setAttribute('width', String(EXPORT_W)); src.setAttribute('height', String(h))
    src.setAttribute('viewBox', `0 0 ${cw} ${ch}`)
    const vp = src.querySelector('#vp'); if (vp) vp.removeAttribute('transform')
    // 사용률 세모는 화면에서 겹친 층(`#awsutl`)에 있다 — 내보낸 그림에는 그 순간 자리 그대로 사용률 층 끝에 넣는다(층을 나누기 전과 같은 바이트 · §17.25)
    const utt = document.querySelectorAll('#awsutl .aw-utt')
    if (vp && utt.length) {
      let ut = vp.querySelector('#aw-ut')
      if (!ut) { ut = document.createElementNS('http://www.w3.org/2000/svg', 'g'); ut.setAttribute('id', 'aw-ut'); ut.setAttribute('pointer-events', 'none'); vp.appendChild(ut) }
      for (const g of utt) ut.appendChild(g.cloneNode(true))
    }
    for (const el of src.querySelectorAll('foreignObject')) el.remove()
    // 화면 배경은 CSS 가 칠한다 — 내보낸 파일에는 CSS 가 없으므로 판을 하나 깔아준다.
    const bg = document.createElementNS('http://www.w3.org/2000/svg', 'rect')
    bg.setAttribute('x', '0'); bg.setAttribute('y', '0')
    bg.setAttribute('width', String(cw)); bg.setAttribute('height', String(ch))
    bg.setAttribute('fill', C.bg)
    src.insertBefore(bg, src.firstChild)
    if (lg) {
      const g = new DOMParser().parseFromString(`<svg xmlns="http://www.w3.org/2000/svg">${lg.svg}</svg>`, 'image/svg+xml').documentElement.firstChild
      if (g && g.nodeName === 'g') { g.setAttribute('transform', `translate(${L.canvasW + LG_GAP},0)`); src.appendChild(document.importNode(g, true)) }
    }
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + new XMLSerializer().serializeToString(src)
  }
  function dl (blob, name) {
    const u = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = u; a.download = name; document.body.appendChild(a); a.click(); a.remove()
    setTimeout(() => URL.revokeObjectURL(u), 2000)
  }
  function exportSvg (save) {
    const text = exportSvgText(); const name = tsName('svg')
    if (save !== false) dl(new Blob([text], { type: 'image/svg+xml;charset=utf-8' }), name)
    return { text, name, bytes: text.length }
  }
  function exportPng (save) {
    const text = exportSvgText(); const name = tsName('png')
    return new Promise((res) => {
      const img = new Image()
      img.onload = () => {
        try {
          const cv = document.createElement('canvas')
          cv.width = EXPORT_W; cv.height = +(/<svg[^>]*\sheight="(\d+)"/.exec(text) || [0, Math.round(EXPORT_W * L.canvasH / L.canvasW)])[1]   // 범례로 넓어진 판(§17.18-2)과 같은 비
          cv.getContext('2d').drawImage(img, 0, 0, cv.width, cv.height)
          cv.toBlob((b) => {
            if (b && save !== false) dl(b, name)
            res(b ? { name, size: b.size, type: b.type } : { name, size: 0, type: '', err: 'toBlob null' })
          }, 'image/png')
        } catch (e) { res({ name, size: 0, type: '', err: String(e && e.message) }) }
      }
      img.onerror = () => res({ name, size: 0, type: '', err: 'SVG 이미지 로드 실패' })
      img.src = 'data:image/svg+xml;base64,' + btoa(unescape(encodeURIComponent(text)))
    })
  }

  // ---- §13-63 라이브 / 스냅샷 게이트 ----
  // 편집한 그림을 «라이브»라고 부르면 사람이 오판한다(불변식 ④와 같은 이유).
  function hideNode (id) {
    if (!byId.has(id) || HIDDEN.has(id)) return null
    HIDDEN.add(id)
    if (S.mode === 'live') S.mode = 'snapshot'
    S.history.push({ at: T.run.collected_at, action: 'hide', target: id, hidden: HIDDEN.size })
    relayout()
    return { mode: S.mode, hidden: HIDDEN.size, history: S.history.length }
  }
  function toLive () {
    const had = HIDDEN.size
    HIDDEN.clear(); S.mode = 'live'
    S.history.push({ at: T.run.collected_at, action: 'discard', target: null, hidden: 0, was: had })
    relayout()
    return { mode: S.mode, discarded: had, history: S.history.length }
  }
  function versions () {
    const snaps = [window.TOPO, window.TOPO_T2].filter(Boolean)
    const ids = snaps.map((s) => new Set(s.nodes.map((n) => n.id)))
    return snaps.map((s, i) => {
      const prev = i ? ids[i - 1] : null
      let add = 0; let del = 0
      if (prev) {
        for (const id of ids[i]) if (!prev.has(id)) add++
        for (const id of prev) if (!ids[i].has(id)) del++
      }
      return { collected_at: s.run.collected_at, nodes: s.nodes.length, added: add, removed: del,
        delta: add + del, current: s === T }
    })
  }

  // ---- 대장 94·101 독(오른쪽 패널) · 변경 이력 ----
  // 🔴 이력은 **격자가 아니라 목록**이다 — 하단 24슬롯 5분 격자는 대장 94 로 폐기됐다. 화면
  //    어디에도 「슬롯」이라는 낱말과 5분 칸 DOM 을 만들지 마라(검사 85 ④⑤ 가 그것을 잰다).
  // 🔴 변경점을 만드는 것은 **수집기의 차분**(`changes[]`)이고 렌더러가 아니다(불변식 ③).
  //    여기서 노드 두 벌을 비교해 «변경»을 발명하면 계약 밖에서 사실을 만드는 것이 된다.
  const DOCK_TABS = [['hist', '변경 이력'], ['notes', '특이사항']]
  const hhmm = (iso) => {
    const t = Date.parse(iso)
    return Number.isFinite(t) ? msHm(t) : '—'
  }
  // 🆕 접힌 반복은 양 끝을 말한다(§3j J0 「반복」 · QA B-20 · 사용자 승인 2026-10-21) — 줄 시각은 **마지막** 변화다. 시작(`first_at`)을
  //    아는 줄만 `08:52 KST부터 09:11 KST까지 4회 반복` · 시작의 KST 날짜가 끝과 다르면 시작 앞에 `MM-DD`. 모르면 `N회 반복` 만(지어내지 않는다).
  const histRepeat = (r) => {
    const n = `${r.repeat}회 반복`
    const a = r.first ? Date.parse(r.first) : NaN; const b = Date.parse(r.at)
    if (!Number.isFinite(a) || !Number.isFinite(b)) return n
    const day = kstIso(a).slice(0, 10) === kstIso(b).slice(0, 10) ? '' : kstIso(a).slice(5, 10) + ' '
    return `${day}${msHm(a)}부터 ${msHm(b)}까지 ${n}`
  }
  // 줄의 title 은 **AWS 식별자**(콘솔에서 찾을 수 있는 것)를 댄다 — 엔진 id 의 `종류/계정/리전/` 머리는 원어라
  // 화면에 내지 않는다(§3j J0). 관계는 양 끝의 식별자를 댄다.
  const nativeOf = (id) => String(id || '').split('/').pop()
  // 🔴 흐름은 **늦게 확인된다**(제품 사양 — 시작 약 10분 · 멈춤 최대 약 21분, ADR 0008 D6 보강).
  //    목록의 시각(`at`)은 확인한 때이고 정렬·되짚기의 기준이라 바꾸지 않는다. 실제로 멈춘/시작한
  //    분(`occurred_at`)은 **따로** 적는다 — `at` 자리에 넣으면 되짚기가 엉뚱한 변경까지 되돌린다.
  //    엔진이 모르면 키가 없다 → 그때는 아무것도 적지 않는다(추정하지 않는다).
  const OCC_VERB = { 'live→up': ['멈춤', '멈춘'], 'up→live': ['시작', '시작한'] }
  // ---- 이력 줄은 **무엇이** 바뀌었는지 이름으로 말한다 (§3j J0 · 검사 113) ----
  // 🔴 배포본 실측 2026-09-27: `02:19Z` 16줄이 전부 「관계 endpoint_connection 생겼다」로 찍혀 **같은 줄이
  //    반복되는 것처럼** 보였다(실제로는 서로 다른 연결 16개). 줄이 이름 없이 엔진 원어만 말했고 묶는 규칙이
  //    없었다. 그래서 ① 종류는 한글로 ② 대상은 이름으로 ③ 같은 시각·같은 변경·같은 종류는 한 줄로 묶는다.
  // 🔴 원어(`endpoint_connection`·`nat_gateway`)는 화면 글자에 0개다 — 모르는 종류는 「리소스」·「관계」로 만다.
  const HIST_VERB = { resource_added: '생김', resource_removed: '없어짐' }
  const HIST_TYPE = { account: '계정', region: '리전', vpc: 'VPC', subnet: '서브넷', route_table: '라우팅 테이블',
    internet_gateway: '인터넷 게이트웨이', nat_gateway: 'NAT 게이트웨이', transit_gateway: 'Transit Gateway',
    transit_gateway_attachment: 'TGW 어태치먼트', vpc_peering_connection: 'VPC 피어링',
    vpc_endpoint: 'VPC 엔드포인트', security_group: '보안 그룹', network_acl: '네트워크 ACL',
    load_balancer: '로드밸런서',
    // 🔴 스키마 노드 종류 전부(`topology.schema.json` node_type) — 빠지면 `typeWord` 가 영어 표(TYPE_LABEL)나 「리소스」로 떨어진다
    //    (QA B B-03 실측: VPN · VGW · EIC · ECS · RDS · SageMaker = 「리소스 …」, 「Customer gateway」 · 「EC2 instance」). 검사 113 이 전수를 잰다.
    transit_gateway_route_table: 'TGW 라우팅 테이블', ec2_instance: 'EC2 인스턴스', auto_scaling_group: 'Auto Scaling 그룹',
    eks_cluster: 'EKS 클러스터', ecs_service: 'ECS 서비스', rds_db_instance: 'RDS 인스턴스', network_firewall: 'Network Firewall',
    cloudfront: 'CloudFront 배포', ec2_instance_connect_endpoint: 'EC2 Instance Connect 엔드포인트',
    sagemaker_notebook_instance: 'SageMaker 노트북', sagemaker_domain: 'SageMaker 도메인', sagemaker_endpoint: 'SageMaker 엔드포인트',
    vpn_gateway: 'VPN 게이트웨이', customer_gateway: '고객 게이트웨이', vpn_connection: 'VPN 연결',
    direct_connect_connection: 'Direct Connect 연결', direct_connect_virtual_interface: 'Direct Connect 가상 인터페이스',
    direct_connect_gateway: 'DX 게이트웨이',
    // 파생 노드 2종(스키마 2.7.0 · LB 대상 해소가 짓는다 · §17.27)
    lambda_function: 'Lambda 함수', external_ip: '외부 IP',
    // #114(스키마 2.9.0 · §17.32) — VPC 안 IP 를 쓰는 서비스
    elasticache_cluster: 'ElastiCache 캐시 클러스터', rds_db_cluster: 'RDS DB 클러스터', opensearch_domain: 'OpenSearch 도메인', msk_cluster: 'MSK 클러스터',
    // 2.8.0(P2-4 · §17.30)
    route53_hosted_zone: 'Route 53 호스팅 영역',
    // 2.8.0(P2-3 · §17.31) — 파생 노드(CloudFront S3 원본)
    s3_bucket: 'S3 버킷',
    // 2.10.0(배치 D · §17.34~37)
    elastic_ip: '탄력적 IP', api_gateway: 'API Gateway', rds_db_proxy: 'RDS 프록시', global_accelerator: 'Global Accelerator',
    // 2.11.0(#129 · §17.38~40)
    core_network: 'Cloud WAN 코어 네트워크', lattice_service_network: 'VPC Lattice 서비스 네트워크', lattice_service: 'VPC Lattice 서비스',
    // 2.12.0(ADR 0014 · §17.41) — 노드 종류는 하나 · 수집 종류는 둘(CloudFront 범위 = `waf_web_acl_global` · coverage 줄만 이 이름을 쓴다 —
    //   「!」 목록이 「리소스」로 떨어지면 무엇을 못 읽었는지 말하지 않는다)
    waf_web_acl: 'WAF 웹 ACL', waf_web_acl_global: 'WAF 웹 ACL(CloudFront)' }
  // 엔진 관계 종류 전수(`schema/topology.schema.json` `edge_type`). 늘면 여기도 는다 — 없으면 「관계」로 만다.
  const REL_LABEL = { route: '라우트', association: '연관', attachment: '어태치먼트', peering: '피어링',
    endpoint_connection: '엔드포인트 연결', security_group_reference: '보안 그룹 참조',
    load_balancer_placement: '로드밸런서 배치', origin: 'CloudFront 오리진', load_balancer_target: 'LB 대상',
    // 2.8.0(P1-3·P2-4 · §17.28·§17.30)
    endpoint_provider: '엔드포인트 제공자', dns_alias: 'Route 53 별칭', zone_association: '프라이빗 호스팅 영역 연결',
    // 2.10.0(배치 D · §17.34~37)
    eip_association: '탄력적 IP 연결', vpc_link: 'VPC 링크', proxy_target: 'RDS 프록시 대상', accelerator_endpoint: 'Global Accelerator 엔드포인트',
    // 2.11.0(#129 · §17.38~40)
    connect_transport: 'TGW Connect 전송', core_network_attachment: 'Cloud WAN 연결', lattice_vpc_association: 'VPC Lattice VPC 연결',
    lattice_service_association: 'VPC Lattice 서비스 연결', lattice_target: 'VPC Lattice 대상',
    // 2.12.0(ADR 0014)
    waf_protection: 'WAF 보호' }
  // 상태 4어휘(`observation.schema.json` `status`)의 줄 표기 — 상단 알약과 같은 말을 쓴다.
  const ST_WORD = { live: '흐름', up: '정상', down: '이상', none: '관측 불가' }
  const typeWord = (t) => (t && (HIST_TYPE[t] || TYPE_LABEL[t])) || '리소스'
  const relWord = (t) => (t && REL_LABEL[t]) || '관계'
  // 🔴 `changes[].target_kind` 는 **«선이냐 상자냐»**뿐이다(계약 enum `node|edge`) — 리소스 종류가
  //    아니다. 종류는 캔버스 카드 → 엔진 id 첫 구획(`<종류>/<계정>/<리전>/<id>`, `engine/src/nettopo/model.py:24-38`)
  //    → 없어진 것의 모습(`was`) 순으로 찾는다. 슬러그 id(픽스처·합성)에는 구획이 없다.
  // 🔴 **관계 id 는 `/` 가 아니라 `|` 로 구획된다**(엔진: `"<관계종류>|<출발>|<도착>[|<구분>]"`,
  //    `engine/src/nettopo/collect.py:388-393`). `/` 로 쪼개면 `association|route_table` 이 종류 자리에 찍힌다.
  const relOf = (id) => {
    const p = String(id || '').split('|')
    return p.length >= 3 ? { t: p[0] || null, a: p[1], b: p[2] } : null
  }
  const idNote = (id) => { const r = relOf(id); return r ? `${nativeOf(r.a)} → ${nativeOf(r.b)}` : nativeOf(id) }
  const idTail = (id, name) => { const n = idNote(id); return n === name ? '' : ` (${n})` }
  // 목록만 있는 리소스(라우팅 테이블·NACL·SG, §13-55)는 카드가 없다 — 표의 행이 native id 로 이름을 든다.
  const LIST_NAME = new Map((T.listOnly || []).filter((r) => r && r.id).map((r) => [String(r.id), r.name]))
  // 없어진 것의 이름: 수집기가 없어짐을 발견할 때 적은 모습(`was`, ADR 0006 규칙 11). 이력 전체에서 모은다 —
  // 없어진 연결의 끝 카드는 **다른 줄**(카드 없어짐)의 `was` 에만 있다.
  //    한 번 모은 것을 **같은 이력 배열인 동안** 다시 쓴다(#31·#32 리뷰 후속 — 비교 열기 한 번에 세 번 모았다).
  //    `OBS` 는 통째로 갈리고(`OBS = doc`) 배열을 제자리에서 고치는 곳이 없으므로 배열 자체가 열쇠다.
  let wasMemo = null
  function wasIndex () {
    const cs = (OBS && OBS.changes) || null
    if (wasMemo && wasMemo.cs === cs) return wasMemo.m
    const m = new Map()
    for (const c of (cs || [])) {
      for (const n of ((c && c.was && c.was.nodes) || [])) if (n && n.id && !m.has(n.id)) m.set(n.id, n)
    }
    wasMemo = { cs, m }
    return m
  }
  const lastSeg = (id) => { const s = String(id || '').split('/').pop(); return s.length > 24 ? '…' + s.slice(-23) : s }
  // 🆕 한 자원 = 한 이름 (#138 · QA C Q-6 · §3j J0). 지금 카드(어댑터가 지은 것)와 `was`(엔진 노드 그대로)는 이름 칸이 다를 수 있다 —
  //    인터페이스 엔드포인트 카드는 이름 자리에 **서비스 짧은 이름**을 싣는다(`adapt-core.js` `svcShort`). 그래서 같은 엔드포인트가
  //    구조 수집 전에는 「logs」(카드) · 뒤에는 「qa-single-h-vpce-logs」(`was` 의 Name 태그)로 바뀌었다. 규칙: **Name 태그가 있으면 그것**
  //    (지금 카드의 태그 → `was` 의 태그) · 없으면 두 출처가 같은 규칙으로 짓는다(엔드포인트 = 서비스 짧은 이름 — 어댑터와 같은 식).
  //    🔴 목록 자원(SG·NACL·RT)은 태그를 보지 않는다 — 표 행에 태그가 없어(`adapt-core.js` list 행) 지금은 표 이름 · 지운 뒤엔 태그가 된다.
  const HIST_LIST_KIND = new Set(['route_table', 'network_acl', 'security_group'])
  const nameTag = (x) => { const v = x && x.tags && x.tags.Name; return typeof v === 'string' && v.trim() ? v : null }
  const svcShortOf = (s) => { const m = /^com\.amazonaws\.[a-z0-9-]+\.(.+)$/.exec(String(s || '')); return m ? m[1] : String(s || '').replace(/^com\.amazonaws\.vpce\./, '') }
  function wasName (w) {
    const a = w.attributes || {}
    if (w.type === 'security_group' && a.group_name) return a.group_name
    if (w.type === 'vpc_endpoint' && a.service_name) return svcShortOf(a.service_name)
    return w.name || w.native_id || null
  }
  function histName (id, was) {
    const n = byId.get(id)
    const w = was && was.get(id)
    const tag = nameTag(n) || (w && !HIST_LIST_KIND.has(w.type) ? nameTag(w) : null)
    if (tag) return tag
    if (n && n.name) return n.name
    const seg = String(id || '').split('/').pop()
    if (LIST_NAME.has(seg)) return LIST_NAME.get(seg)
    if (w) return wasName(w) || lastSeg(id)
    return lastSeg(id)
  }
  const idTypeOf = (id, was) => {
    const n = byId.get(id)
    if (n && n.type) return n.type
    const w = was && was.get(id)
    if (w && w.type) return w.type
    const p = String(id || '').split('/')
    return p.length > 1 ? p[0] : null
  }
  // 한 대상의 사람 말: 카드 = 「종류 이름」 · 관계 = 「관계 이름 출발 → 도착」.
  function histWho (c, was) {
    const r = c.target_kind === 'edge' ? relOf(c.target_id) : null
    if (r) return { word: relWord(r.t), name: `${histName(r.a, was)} → ${histName(r.b, was)}` }
    if (c.target_kind === 'edge') return { word: '관계', name: lastSeg(c.target_id) }
    return { word: typeWord(idTypeOf(c.target_id, was)), name: histName(c.target_id, was) }
  }
  const HIST_OPEN = new Set()           // 펼친 묶음 줄(열쇠 = 시각|변경|대상|종류)
  // 목록 = 「지금」(기준 스냅샷) 한 줄 + 변경이 일어난 시각들. **최신이 위**다.
  // 🔴 이력의 목적은 **① 변경 이력 ② 끊김 기록 ③ 누르면 그때 상태**다(사용자 결정 2026-09-24) →
  //    `down` 으로 바뀐 변경에는 `cut` 을 세워 목록에서 눈에 띄게 한다. 나머지 설명은 `title` 로 민다.
  // 줄 만드는 순서(같은 시각 안): ① 바뀜 짝 ② 생김·없어짐 묶음(1건이면 그냥 줄) ③ 상태 — 상태는 묶지 않는다
  //    (대상마다 실제로 멈춘 분이 달라 한 줄에 담으면 그 분이 사라진다).
  // 「지금」 줄의 시각 = 지금 화면이 말하는 가장 새 사실 — 구조 수집과 관측 중 **늦은 쪽**(§3j J0 · 2026-10-15 데모 발견).
  //   구조 시각만 찍으면 그 뒤 관측이 적은 변경 줄(더 늦은 시각)이 「지금」 아래에 와서 시간이 거꾸로 읽혔다.
  function histNowAt () {
    const st = T.run.collected_at
    const ob = OBS && OBS.run && typeof OBS.run.observed_at === 'string' ? OBS.run.observed_at : null
    return ob && Date.parse(ob) > Date.parse(st) ? ob : st
  }
  // ---- 바뀜 짝 (§3j J0 · J4) — 같은 출발 · 같은 관계 종류의 없어짐 1 + 생김 1 ----
  // 짝은 시각 하나 안에서만 짓지 않는다(🆕 늦은 짝 · 2026-10-15 QA B B-11): 새 라우트는 구조 문서에 없어 관측이 버리므로
  // (`engine/src/nettopo/observe.py` `build_observation`) 다음 구조 수집 뒤 첫 관측에야 「생김」으로 나온다 → 대상 변경 하나가
  // 「없어짐」(t1)·「생김」(t2) 두 시각에 갈라진다. 그래서 짝을 **목록 전체에서 먼저** 짓고, 줄은 생김의 시각(t2)에 한 줄로 낸다.
  const pairKey = (c) => {
    const r = c && c.target_kind === 'edge' && HIST_VERB[c.kind] ? relOf(c.target_id) : null
    return r ? `${r.t}|${r.a}` : null
  }
  // 관계의 목적지(라우트 = `attributes.destinations`). 모르면 null — 대조를 건너뛴다(지어내지 않는다).
  let shapeMemo = null
  function edgeShape (id) {
    const S0 = window.NT_STRUCT || null; const cs = (OBS && OBS.changes) || null
    if (!shapeMemo || shapeMemo.S0 !== S0 || shapeMemo.cs !== cs) {
      const m = new Map()
      for (const e of ((S0 && S0.edges) || [])) if (e && e.id) m.set(e.id, e)
      for (const c of (cs || [])) for (const e of ((c && c.was && c.was.edges) || [])) if (e && e.id && !m.has(e.id)) m.set(e.id, e)
      shapeMemo = { S0, cs, m }
    }
    return shapeMemo.m.get(id) || null
  }
  const destsOf = (e) => {
    const a = (e && (e.attributes || e.attrs)) || {}
    const ds = [].concat(a.destinations || [], a.destination_cidr_block || [], a.destination_ipv6_cidr_block || [])
    return ds.length ? new Set(ds.map(String)) : null
  }
  function destsMeet (goneId, comeId) {
    const o = destsOf(edgeShape(goneId)); const n = destsOf(edgeShape(comeId))
    return !o || !n || [...o].some((d) => n.has(d))
  }
  // 돌려주는 것: 짝 목록({ at = 줄의 시각, gone, come, late }) · 짝에 쓴 변경 집합. 같은 시각 짝을 먼저 짓는다.
  function histPairs (cs) {
    const pairs = []; const used = new Set()
    const byAt = new Map()            // at → (k → 변경들)
    for (const c of cs) {
      const k = pairKey(c)
      if (!k) continue
      if (!byAt.has(c.at)) byAt.set(c.at, new Map())
      const m = byAt.get(c.at)
      if (!m.has(k)) m.set(k, [])
      m.get(k).push(c)
    }
    for (const [at, m] of byAt) {
      for (const list of m.values()) {
        const gone = list.filter((c) => c.kind === 'resource_removed')
        const come = list.filter((c) => c.kind === 'resource_added')
        if (gone.length !== 1 || come.length !== 1) continue
        const o = relOf(gone[0].target_id); const n = relOf(come[0].target_id)
        if (o.b === n.b) continue
        used.add(gone[0]); used.add(come[0])
        pairs.push({ at, gone: gone[0], come: come[0], late: false })
      }
    }
    // 늦은 짝 — 창 = t1 뒤 처음으로 생김이 나온 시각(= 새 구조로 잰 첫 관측의 흔적). 그 시각의 (k, a) 생김만 짝이 된다.
    const left = (at, k, kind) => (((byAt.get(at) || new Map()).get(k)) || []).filter((c) => c.kind === kind && !used.has(c))
    const addAts = [...new Set(cs.filter((c) => c.kind === 'resource_added').map((c) => c.at))].sort()
    const gones = cs.filter((c) => c.kind === 'resource_removed' && pairKey(c) && !used.has(c))
      .sort((a, b) => (a.at < b.at ? -1 : a.at > b.at ? 1 : a.target_id < b.target_id ? -1 : 1))
    for (const g of gones) {
      if (used.has(g)) continue
      const k = pairKey(g)
      if (left(g.at, k, 'resource_removed').length !== 1) continue
      const t2 = addAts.find((t) => t > g.at)
      if (!t2) continue
      const between = [...byAt.keys()].some((t) => t > g.at && t < t2 && left(t, k, 'resource_removed').length)
      const come = left(t2, k, 'resource_added')
      if (between || come.length !== 1 || left(t2, k, 'resource_removed').length) continue
      if (relOf(g.target_id).b === relOf(come[0].target_id).b || !destsMeet(g.target_id, come[0].target_id)) continue
      used.add(g); used.add(come[0])
      pairs.push({ at: t2, gone: g, come: come[0], late: true })
    }
    return { pairs, used }
  }
  // ---- 같은 시각 · 같은 글자 줄을 식별자 끝으로 가른다 (§3j J0 · 사용자 결정 2026-10-21 · QA C Q-4 · 검사 157) ----
  // 이름(Name 태그)이 같은 대상 둘의 줄이 글자까지 같으면 고객은 어느 것인지 고를 수 없다 → **겹친 줄에만** 이름 뒤에 `(…3a9f)`.
  // `tid` = 가를 때 쓸 식별자 후보(관계 = [도착, 출발, 관계 id] · 카드 = [자기]) — 겹친 줄들 사이에서 **값이 가장 많이 갈리는** 후보를 쓴다.
  // 꼬리 = AWS 식별자(native id) 끝 4자 · 그래도 같으면 갈릴 때까지 늘린다(결정론 — 입력만 본다).
  // 🔴 12자리 숫자(계정 ID 꼴)는 빼고 센다 — 계정 ID 조각을 줄에 내지 않는다.
  const histTid = (id) => { const r = relOf(id); return r ? [r.b, r.a, id] : [id] }
  const tailSrc = (id) => nativeOf(String(id || '').split('|').pop()).replace(/\d{12}/g, '')
  function histSplit (rs) {
    const by = new Map()
    for (const r of rs) { if (r.mk) { if (!by.has(r.text)) by.set(r.text, []); by.get(r.text).push(r) } }
    for (const same of by.values()) {
      if (same.length < 2 || new Set(same.map((r) => r.targets.join('\n'))).size < 2) continue
      const nT = Math.min(...same.map((r) => r.tid.length))
      let k = 0; let best = -1
      for (let i = 0; i < nT; i++) { const d = new Set(same.map((r) => r.tid[i])).size; if (d > best) { best = d; k = i } }
      const src = same.map((r) => tailSrc(r.tid[k]))
      const max = Math.max(...src.map((x) => x.length))
      let tails = src
      for (let n = 4; n <= max; n++) { tails = src.map((x) => x.slice(-n)); if (new Set(tails).size === tails.length) break }
      same.forEach((r, i) => { if (tails[i]) { r.split = `(…${tails[i]})`; r.text = r.mk(`${r.nm} ${r.split}`) } })
    }
    return rs
  }
  function histRows () {
    const nowAt = histNowAt()
    const rows = [{ at: nowAt, kind: 'now', text: '지금', repeat: 1,
      full: nowAt === T.run.collected_at ? `기준 스냅샷 ${kstAt(T.run.collected_at)}`
        : `지금 화면 — 구조 ${kstAt(T.run.collected_at)} 수집 · 상태 ${kstAt(nowAt)} 관측` }]
    if (!OBS) return rows
    const was = wasIndex()
    const cs = (OBS.changes || []).slice().sort((a, b) => (a.at < b.at ? 1 : a.at > b.at ? -1 : 0))
    const ats = [...new Set(cs.map((c) => c.at))]
    const { pairs, used } = histPairs(cs)
    for (const at of ats) {
      const here = cs.filter((c) => c.at === at)
      // ① 바뀜 = 짝 하나가 한 줄. 늦은 짝은 옛 대상이 없어진 시각을 줄 끝에 따로 적는다(목록 시각 = 새 대상을 확인한 때).
      for (const { gone, come, late } of pairs.filter((x) => x.at === at)) {
        const o = relOf(gone.target_id); const n = relOf(come.target_id)
        const src = histName(o.a, was)
        const nm = `${histName(o.b, was)} → ${histName(n.b, was)}`
        rows.push({ at, kind: 'replaced', target: come.target_id, targets: [gone.target_id, come.target_id],
          cut: false, text: `${relWord(o.t)} 대상 ${nm} 로 바뀜`, nm, mk: (x) => `${relWord(o.t)} 대상 ${x} 로 바뀜`, tid: [o.a],
          full: `${src} 의 ${relWord(o.t)} 대상이 바뀌었습니다 — ${histName(o.b, was)} 없어짐 · ${histName(n.b, was)} 생김 ` +
            `(${nativeOf(o.a)}: ${nativeOf(o.b)} → ${nativeOf(n.b)})` +
            (late ? ` · 옛 대상이 없어진 것은 ${kstAt(gone.at)} 에, 새 대상은 다음 구조 수집 뒤 ${kstAt(at)} 에 확인했습니다` : ''),
          occ: late ? `옛 대상 ${hhmm(gone.at)} 없어짐` : null, repeat: 1, lateGone: late ? gone : null })
      }
      // ② 생김·없어짐 — 같은 변경 · 같은 대상(선/상자) · 같은 종류는 한 줄로 묶는다
      const groups = new Map()
      for (const c of here) {
        if (used.has(c) || !HIST_VERB[c.kind]) continue
        const r = c.target_kind === 'edge' ? relOf(c.target_id) : null
        const t = c.target_kind === 'edge' ? (r ? r.t : null) : idTypeOf(c.target_id, was)
        const k = `${at}|${c.kind}|${c.target_kind}|${t || ''}`
        if (!groups.has(k)) groups.set(k, [])
        groups.get(k).push(c)
      }
      for (const [key, list] of groups) {
        const c = list[0]
        const who = histWho(c, was)
        if (list.length === 1) {
          rows.push({ at, kind: c.kind, target: c.target_id, targets: [c.target_id], cut: false,
            text: `${who.word} ${who.name} ${HIST_VERB[c.kind]}`, nm: who.name, mk: (x) => `${who.word} ${x} ${HIST_VERB[c.kind]}`, tid: histTid(c.target_id),
            full: `${who.word} ${who.name}${idTail(c.target_id, who.name)} — ${HIST_VERB[c.kind]}`,
            occ: null, repeat: c.repeat_count || 1 })
          continue
        }
        // 펼친 이름 줄도 같은 글자면 식별자 끝으로 가른다(§3j J0 · QA C Q-4)
        const names = histSplit(list.map((x) => ({ text: histWho(x, was).name, nm: histWho(x, was).name, mk: (y) => y,
          targets: [x.target_id], tid: histTid(x.target_id) }))).map((x) => x.text)
        rows.push({ at, kind: c.kind, target: c.target_id, targets: list.map((x) => x.target_id), cut: false,
          group: key, items: names, itemIds: list.map((x) => x.target_id),
          text: `${who.word} ${list.length}개 ${HIST_VERB[c.kind]}`,
          full: `${who.word} ${list.length}개 ${HIST_VERB[c.kind]} — ${names.join(', ')}`,
          occ: null, repeat: Math.max(...list.map((x) => x.repeat_count || 1)) })
      }
      // ③ 상태
      for (const c of here) {
        if (c.kind !== 'status_changed') continue
        const who = histWho(c, was)
        const extra = ` (${ST_WORD[c.from] || '모름'} → ${ST_WORD[c.to] || '모름'})`
        const cut = c.to === 'down'
        const occ = typeof c.occurred_at === 'string' ? c.occurred_at : null
        const verb = (occ && OCC_VERB[`${c.from}→${c.to}`]) || ['', '바뀐']
        const fv = OCC_VERB[`${c.from}→${c.to}`]
        const tail = fv ? `흐름 ${fv[0]}` : `${ST_WORD[c.from] || '모름'} → ${ST_WORD[c.to] || '모름'}`
        rows.push({ at: c.at, kind: c.kind, target: c.target_id, cut, targets: [c.target_id],
          text: `${who.word} ${who.name} · ${tail}`, nm: who.name, mk: (x) => `${who.word} ${x} · ${tail}`, tid: histTid(c.target_id),
          full: `${who.word} ${who.name}${idTail(c.target_id, who.name)} — 상태가 바뀌었다${extra}` +
            (occ ? ` · 실제로 ${verb[1]} 분 ${kstAt(occ)} (목록 시각 ${kstAt(c.at)} = 확인한 때)` : '') +
            ((c.repeat_count || 1) > 1 && typeof c.first_at === 'string' ? ` · 반복 ${kstAt(c.first_at)} 부터 ${kstAt(c.at)} 까지` : ''),
          occ: occ ? `실제 ${hhmm(occ)}${verb[0] ? ' ' + verb[0] : ''}` : null,
          repeat: c.repeat_count || 1, first: (c.repeat_count || 1) > 1 && typeof c.first_at === 'string' ? c.first_at : null })
      }
      histSplit(rows.filter((r) => r.at === at && r.kind !== 'now'))
    }
    for (const r of rows) delete r.mk   // 글자를 다 지었다 — 줄은 데이터만 든다(하네스·부르는 쪽이 그대로 옮긴다)
    return rows
  }
  // ---- 흐르다가 멈춘 선 (§8.3 「선의 초록」 개정 · 사용자 결정 2026-10-14 QA B P8 · 검사 132·114) ----
  // 이력에 그 관계의 `live → up`(흐름 멈춤)이 있고 **그 뒤에** 다시 흐른 기록(`→ live`)이 없다. 한 번도 흐르지 않은 선은
  // 여기에 없다(회색 그대로). `upTo` = 그 시각까지의 이력만(비교 칸 = 그때). 같은 시각이면 문서 순서가 뒤인 것이 이긴다.
  function stoppedOf (cs, upTo) {
    const last = new Map()
    for (const c of (cs || [])) {
      if (!c || c.kind !== 'status_changed' || c.target_kind !== 'edge') continue
      if (upTo && c.at > upTo) continue
      if (!(c.from === 'live' && c.to === 'up') && c.to !== 'live') continue
      const cur = last.get(c.target_id)
      if (!cur || !(c.at < cur.at)) last.set(c.target_id, c)
    }
    return new Set([...last].filter(([, c]) => c.to === 'up').map(([id]) => id))
  }
  // 오른쪽 칸은 부모가 그 시각으로 잘라 넘긴 집합을 쓴다 — 칸의 관측 문서에는 이력이 없다(`cmpObs` 의 `changes: []`).
  let stopMemo = null
  function flowStopped () {
    if (CMP) { if (!stopMemo) stopMemo = { s: new Set(CMP.stopped || []) }; return stopMemo.s }
    const cs = (OBS && OBS.changes) || null
    if (!stopMemo || stopMemo.cs !== cs) stopMemo = { cs, s: stoppedOf(cs, null) }
    return stopMemo.s
  }
  // ---- 바뀐 곳으로 카메라를 옮긴다 (§3j J3 개정 · 사용자 결정 2026-10-14 QA B P8 · 검사 114 ⑦) ----
  // 두 칸(왼쪽 = 지금 · 오른쪽 = 그때)이 **같은 함수**로 각자의 그림에서 바뀐 곳을 찾는다. 자리 = 표시가 앉는 자리
  // (선 = 두 끝 + 꺾인 점 · 아이콘·상자 · 끝 자리). 그 그림에 없으면(왼쪽의 없어진 것) `near` = 들어 있던 곳·양 끝의 사슬에서
  // 처음 찾은 자리. 아무것도 없으면 null — 지어내지 않는다(부르는 쪽은 평소 맞춤 그대로 둔다).
  function cmpSpot (marks, seats) {
    const rs = []
    const box = (a) => { if (a) rs.push({ x: a.x, y: a.y, w: a.w, h: a.h }) }
    const lines = (ls) => { for (const l of ls) { l.ends.forEach(box); for (const p of l.pts || []) rs.push({ x: p[0], y: p[1], w: 0, h: 0 }) } }
    for (const m of marks || []) {
      const n0 = rs.length
      lines(NT_AWS.markLines(m.id))
      if (rs.length === n0) box(NT_AWS.markAt(m.id))
      if (rs.length === n0) { const s = NT_AWS.markSeat(m.id); if (s && s.at) box(s.at); else if (s && s.lines) lines(s.lines) }
    }
    // `near` 는 **하나도 못 찾았을 때만** 쓴다 — 찾은 자리가 있는데 옛 부모까지 넣으면 상자가 쓸데없이 커진다
    // 🆕 `seats` = 모습(`was`)이 없어 그리지 못한 없어짐의 사슬(§3j J3 · QA B 2차 N-01) — 그리지는 않고 카메라만 그곳으로 간다
    // `real` = 대상 **자신**을 이 그림에서 찾았다(표시 자리 · 사슬 첫 칸 = 그 자원 자신). 아니면 들어 있던 곳(대신 본 곳)이다
    let real = rs.length > 0
    if (!rs.length) {
      for (const m of [...(marks || []), ...(seats || [])]) {
        for (const chain of m.near || []) for (let i = 0; i < chain.length; i++) { const a = NT_AWS.markAt(chain[i]); if (a) { box(a); if (i === 0 && chain[i] === m.id) real = true; break } }
      }
    }
    if (!rs.length) return null
    const x0 = Math.min(...rs.map((q) => q.x)); const y0 = Math.min(...rs.map((q) => q.y))
    const x1 = Math.max(...rs.map((q) => q.x + q.w)); const y1 = Math.max(...rs.map((q) => q.y + q.h))
    return { x: x0, y: y0, w: x1 - x0, h: y1 - y0, real }
  }
  // 두 칸이 **같은 곳**을 말하게 하는 닻(§3j J3 「두 칸 같은 배율 · 같은 곳」 · QA C 4단계 N-01 · 2026-10-21) — 바뀐 곳 가운데가
  // 사슬(들어 있던 곳)의 각 상자 안 어디(비율)인가. 대상이 한쪽 그림에만 있으면(없어짐 = 오른쪽만 · 나중에 지운 생김 = 오른쪽만 ·
  // 모습 없는 없어짐 = 왼쪽만) 다른 칸은 두 그림에 다 있는 첫 상자에서 같은 비율 자리를 가운데로 쓴다.
  function cmpAnchors (marks, seats, cx, cy) {
    const out = []; const seen = new Set()
    for (const m of [...(marks || []), ...(seats || [])]) {
      for (const chain of m.near || []) {
        for (const id of chain) {
          if (seen.has(id)) continue
          const a = NT_AWS.markAt(id)
          if (a && a.w > 0 && a.h > 0) { seen.add(id); out.push({ id, fx: (cx - a.x) / a.w, fy: (cy - a.y) / a.h }) }
        }
      }
    }
    return out
  }
  const cmpMapped = (anchors) => {
    for (const a of anchors || []) { const b = NT_AWS.markAt(a.id); if (b && b.w > 0 && b.h > 0) return { cx: b.x + a.fx * b.w, cy: b.y + a.fy * b.h, via: a.id } }
    return null
  }
  // 배율 = max(전체 맞춤 × 1.5, 0.6) — 「조금 당긴다」 · 상한 1.4 · 바뀐 곳이 칸의 80% 를 넘으면 그만큼 줄인다 · 전체 맞춤보다 작아지지 않는다.
  // 그 뒤 끌기·확대·맞춤은 평소 그대로다(확대 창 없음 — 옛 J3 폐기).
  const FOCUS_K = 1.5; const FOCUS_MIN = 0.6; const FOCUS_MAX = 1.4
  function cmpFocus (marks, seats) {
    const R = cmpSpot(marks, seats)
    const r = svgEl.getBoundingClientRect()
    if (!R || !L || !(r.width > 0) || !(r.height > 0)) return null
    const pad = 16
    const fitZ = Math.min((r.width - 2 * pad) / L.canvasW, (r.height - 2 * pad) / L.canvasH, 1)
    let z = Math.min(Math.max(fitZ * FOCUS_K, FOCUS_MIN), FOCUS_MAX)
    if (R.w > 0) z = Math.min(z, (0.8 * r.width) / R.w)
    if (R.h > 0) z = Math.min(z, (0.8 * r.height) / R.h)
    z = Math.max(z, fitZ)
    const cx = R.x + R.w / 2; const cy = R.y + R.h / 2
    cmpAim(z, cx, cy)
    return { spot: R, fit: fitZ, zoom: S.zoom, tx: S.tx, ty: S.ty, real: R.real, cx, cy, anchors: cmpAnchors(marks, seats, cx, cy) }
  }
  function cmpAim (z, cx, cy) {
    const r = svgEl.getBoundingClientRect()
    S.zoom = z
    S.tx = Math.round(r.width / 2 - cx * z)
    S.ty = Math.round(r.height / 2 - cy * z)
    applyTransform()
  }
  // 두 칸 맞추기(§3j J3 · 2026-10-21 · QA C 4단계 N-01) — 두 칸은 **같은 배율**이다. 배율 = 대상 자신을 찾은 칸의 배율(둘 다면 작은 쪽 —
  //   두 칸 다 그 상자가 80% 안에 든다) · 둘 다 대신 본 곳이면 작은 쪽. 가운데 = 대상 자신(찾았으면) → 다른 칸의 닻(같은 비율 자리) →
  //   제 들어 있던 곳. 🔴 각 칸이 제 그림 크기로 따로 배율을 재면 두 칸이 85%/24% 로 갈렸다(QA C 실측 9장 중 8장).
  //   `me` = 이 칸의 계획(`cmpFocus` 값 · 없으면 null) · `other` = 다른 칸 계획의 요약. 돌려줌 = 이 칸이 실제로 선 자리.
  const cmpShare = (me, other) => {
    const ps = [me, other].filter(Boolean)
    if (!ps.length) return null
    const real = ps.filter((x) => x.real)
    const z = Math.min(...(real.length ? real : ps).map((x) => x.zoom))
    const at = me && me.real ? { cx: me.cx, cy: me.cy, via: null } : (other && other.real && cmpMapped(other.anchors)) || (me ? { cx: me.cx, cy: me.cy, via: null } : null)
    if (!at) return me
    cmpAim(z, at.cx, at.cy)
    return { ...(me || {}), zoom: S.zoom, tx: S.tx, ty: S.ty, cx: at.cx, cy: at.cy, via: at.via, shared: true }
  }
  const cmpBrief = (f) => (f ? { real: !!f.real, zoom: f.zoom, cx: f.cx, cy: f.cy, anchors: f.anchors || [] } : null)
  // ---- §3j 이력 비교 화면 — 부모 쪽 (J1·J2 · 검사 114) ----
  const byAtDesc = (a, b) => (a.at < b.at ? 1 : a.at > b.at ? -1 : 0)
  // t 의 변경 하나 → 오른쪽 그림 위의 표시 하나(J4). `id` 는 엔진 id 다 — 카드 id 와 같고(계정·리전 제외),
  // 선은 오른쪽 칸이 `obsIds`(어댑터가 실어 보낸 엔진 관계 id)로 찾는다.
  function cmpMarkOf (c, replaced) {
    const edge = c.target_kind === 'edge'
    if (c.kind === 'resource_added') return { id: c.target_id, edge, kind: 'add', label: replaced ? '바뀜' : '생김' }
    if (c.kind === 'resource_removed') return { id: c.target_id, edge, kind: 'gone', label: '없어짐' }
    if (c.kind !== 'status_changed') return null
    const v = OCC_VERB[`${c.from}→${c.to}`]
    const w = c.to === 'down' ? '이상' : v ? v[0] : `${ST_WORD[c.from] || '모름'} → ${ST_WORD[c.to] || '모름'}`
    return { id: c.target_id, edge, kind: 'status', label: `${w} ${hhmm(c.occurred_at || c.at)}` }
  }
  // 상태 되짚기(J2): 대상마다 t **뒤** 가장 이른 변경의 `from` 이 「그 변경 직후」의 판정이다. 없으면 지금 값.
  // 🔴 문서 정렬을 믿지 않고 `at` 을 잰다 — 나중 것의 `from` 을 쓰면 중간 상태를 「그때」로 그린다.
  // 🔴 접힌 줄(`N회 반복` · ADR 0006 결정 12 · 2026-10-15 데모 발견)의 `from` 은 **마지막** 뒤집힘의 앞이지 반복 전 상태가 아니다 —
  //    그것을 쓰면 짝수 번 뒤집힌 VPN 이 반복 전 시각에 「이상」으로 그려졌다. 접힌 줄은 **시작**(`first_at`)으로 세고, t 가 그
  //    앞이면 `first_from` · 반복 안이거나 시작이 없으면(1.7.0 전 문서) 「모른다」(`folded`) — 상태를 지어내지 않는다(불변식 ④).
  function cmpStatusBack (at) {
    const back = new Map()
    for (const c of (OBS.changes || [])) {
      if (c.kind !== 'status_changed' || !(c.at > at)) continue
      const k = `${c.target_kind}|${c.target_id}`
      let e = c
      if ((c.repeat_count || 1) > 1) {
        const fa = typeof c.first_at === 'string' ? c.first_at : null
        e = fa && fa > at ? { ...c, from: c.first_from, start: fa } : { ...c, from: null, folded: true, start: at }
      }
      const cur = back.get(k)
      if (!cur || (e.start || e.at) < (cur.start || cur.at)) back.set(k, e)
    }
    return back
  }
  // 오른쪽 칸이 받을 관측 문서 = 지금 문서에서 상태만 그때 값으로 되짚은 것. 짝은 오른쪽 구조에 맞춘다.
  // 🔴 되짚은 행은 **판정만** 든다 — 근거(`evidence`)는 지금 것이라 옮기지 않고, 지어내지도 않는다.
  function cmpObs (at, topo, lost) {
    const back = cmpStatusBack(at)
    const row = (c) => ({ target_id: c.target_id, target_kind: c.target_kind, status: c.from,
      error_count: null, partial: false, hist: true })
    const rows = []
    const seen = new Set()
    for (const o of (OBS.observations || [])) {
      const k = `${o.target_kind}|${o.target_id}`
      seen.add(k)
      const c = back.get(k)
      if (!c) { rows.push(o); continue }
      if (c.from) rows.push(row(c)); else lost.push(c)
    }
    // 지금은 없지만 그때는 있던 대상(t 뒤에 없어졌다) — 그 사이 상태 변화의 `from` 이 그때 판정이다.
    for (const [k, c] of back) if (!seen.has(k)) { if (c.from) rows.push(row(c)); else lost.push(c) }
    return { ...OBS,
      run: { ...OBS.run, topology_collected_at: topo.run.collected_at,
        topology_content_digest: topo.run.content_digest },
      observations: rows, changes: [] }
  }
  // 엔진 구조 문서에서 「그 변경 직후」를 되짚는다(J2). t 보다 뒤의 변경을 **최신부터** 되돌린다 —
  // 생김 → 뺀다 / 없어짐 → `was` 로 되살린다. t 의 없어짐은 되살려 유령으로 그린다. 🔴 순서가 뜻이다:
  // 「없어진 카드의 `was` 에 든 연결이 그 뒤에 생긴 것」이면 최신부터 되돌려야 그 연결이 다시 빠진다.
  function cmpStruct (at, R, late) {
    const S0 = window.NT_STRUCT
    const nodes = new Map(S0.nodes.map((n) => [n.id, n]))
    const edges = new Map(S0.edges.map((e) => [e.id, e]))
    const cut = new Set()             // 되돌리며 뺀 카드 — 여기에 닿은 연결도 그때는 없었다
    const back = new Set()            // `was` 로 되살린 연결 — 한쪽 끝이 없으면 버린다
    const cs = (OBS.changes || []).filter((c) => c.at >= at && HIST_VERB[c.kind]).sort(byAtDesc)
    for (const c of cs) {
      const tab = c.target_kind === 'edge' ? edges : nodes
      if (c.kind === 'resource_added') {
        if (c.at > at && tab.delete(c.target_id) && tab === nodes) cut.add(c.target_id)
        continue
      }
      if (!c.was) { (c.at === at ? R.noShape : R.noShapeLater).push(c); continue }
      for (const n of c.was.nodes) { nodes.set(n.id, n); cut.delete(n.id); if (c.at === at) R.ghosts.add(n.id) }
      for (const e of c.was.edges) { edges.set(e.id, e); back.add(e.id); if (c.at === at) R.ghosts.add(e.id) }
    }
    // 늦은 바뀜 짝(§3j J0 · J4 · B-11) — t 보다 앞(t1)에 없어진 옛 선도 이 그림의 유령이다. 옛 선의 끝 카드가 t1~t 사이에
    // 없어졌으면 그 카드도 `was` 로 되살린다(같은 시각 짝이었다면 위 규칙으로 같이 유령이었다). 모습이 없으면 글자 목록.
    for (const g of late || []) {
      if (!g.was) { R.noShape.push(g); continue }
      for (const ge of g.was.edges) {
        for (const end of [ge.source, ge.target]) {
          if (nodes.has(end)) continue
          const nc = (OBS.changes || []).find((c) => c.kind === 'resource_removed' && c.target_kind === 'node' &&
            c.target_id === end && c.at >= g.at && c.at < at && c.was)
          for (const n of (nc ? nc.was.nodes : [])) { nodes.set(n.id, n); cut.delete(n.id); R.ghosts.add(n.id) }
        }
        edges.set(ge.id, ge); back.add(ge.id); R.ghosts.add(ge.id)
      }
    }
    // 들어 있던 곳이 그때 없었던 카드 — 그릴 자리가 없다(§3i 잔차 · J2 마지막 🔴). 없어질 때까지 반복한다.
    for (let again = true; again;) {
      again = false
      for (const [id, n] of nodes) {
        if (n.parent_id && !nodes.has(n.parent_id)) { nodes.delete(id); cut.add(id); R.orphans.push(n); again = true }
      }
    }
    for (const [id, e] of edges) {
      const miss = !nodes.has(e.source) || !nodes.has(e.target)
      if (cut.has(e.source) || cut.has(e.target) || (back.has(id) && miss)) edges.delete(id)
    }
    for (const id of [...R.ghosts]) if (!nodes.has(id) && !edges.has(id)) R.ghosts.delete(id)
    // 🔴 지금 문서의 순서를 그대로 두고 되살린 것만 뒤에 붙인다 — 되돌릴 것이 0 이면 오른쪽 입력은 왼쪽과
    //    바이트까지 같다(같은 어댑터 · 같은 배치 = 같은 그림, 결정론).
    const r = globalThis.NT_ADAPT({ ...S0, nodes: [...nodes.values()], edges: [...edges.values()] }, null)
    R.bad = r.bad
    return r.topo
  }
  // 엔진 문서가 페이지에 없을 때(데모 빌드 · 옛 빌드): 캔버스 모델에서 **빼기만** 한다. 되살릴 모습이 없으니
  // 없어진 것은 전부 글자 목록이다(J2 마지막 🔴). 생김·상태 표시는 그대로 된다.
  function cmpFallback (at, R, late) {
    const cut = new Set(); const cutE = new Set()
    for (const g of late || []) R.noShape.push(g)
    for (const c of (OBS.changes || [])) {
      if (c.kind === 'resource_added' && c.at > at) (c.target_kind === 'edge' ? cutE : cut).add(c.target_id)
      if (c.kind === 'resource_removed' && c.at >= at) (c.at === at ? R.noShape : R.noShapeLater).push(c)
    }
    for (let again = true; again;) {
      again = false
      for (const n of T.nodes) if (!cut.has(n.id) && n.parent_id && cut.has(n.parent_id)) { cut.add(n.id); again = true }
    }
    const edges = T.edges.filter((e) => {
      if (cut.has(e.source) || cut.has(e.target) || cutE.has(e.id)) return false
      const oe = [].concat((e.attrs && e.attrs.obs_edges) || [])
      return !(oe.length && oe.every((id) => cutE.has(id)))
    })
    return { ...T, nodes: T.nodes.filter((n) => !cut.has(n.id)), edges }
  }
  // 칸 머리 아래 글자 목록 — 그리지 못한 것을 **세어서 이름으로** 말한다(불변식 ④: 조용히 빼면 「그때 없었다」로 읽힌다).
  function cmpListHtml () {
    const V = CMPV
    const was = wasIndex()
    const who = (c) => { const w = histWho(c, was); return `${w.word} ${w.name}` }
    const nodeWho = (n) => `${typeWord(n.type)} ${n.name || lastSeg(n.id)}`
    const out = []
    const put = (head, xs) => {
      if (xs.length) out.push(`<div class="cl">${esc(head)} ${xs.length}개: ${esc(xs.slice(0, 8).join(', '))}` +
        (xs.length > 8 ? ` 외 ${xs.length - 8}개` : '') + '</div>')
    }
    if (V.error) out.push(`<div class="cl err">${esc(V.error)}</div>`)
    if (!V.struct) out.push('<div class="cl">이 화면에는 이전 구조가 없어 없어진 리소스를 그리지 못합니다.</div>')
    put('그릴 정보가 없는 없어진 것', V.R.noShape.map(who))
    put('그때 있었지만 그릴 정보가 없는 것', V.R.noShapeLater.map(who))
    put('들어 있던 곳의 모습을 몰라 그리지 못한 것', V.R.orphans.map(nodeWho))
    put('그때 상태를 되짚지 못한 것', V.lost.filter((c) => !c.folded).map(who))
    // 접힌 반복 안의 시각(J2 「접힌 줄」) — 상태를 얹지 않은 이유를 이름으로 말한다(조용히 빼면 「관측 없음」으로만 읽힌다)
    put('상태가 여러 번 바뀐 기록이 한 줄로 접혀 그때 상태를 모르는 것', V.lost.filter((c) => c.folded).map(who))
    const ud = (V.undrawn || []).map((id) => V.marks.find((m) => m.id === id)).filter(Boolean)
    put('그림에 선이 없는 관계라 표시하지 못한 변경', ud.filter((m) => m.edge).map((m) => m.who))
    put('그림에서 자리를 찾지 못한 변경', ud.filter((m) => !m.edge).map((m) => m.who))
    return out.join('')
  }
  function cmpClose () {
    if (!CMPV) return false
    CMPV.el.remove()
    // 왼쪽 카메라는 열기 전 자리로 돌아간다(§3j J1 개정 · P8) — 칸을 여는 동안만 바뀐 곳을 보여 줬다
    const cam = CMPV.cam
    CMPV = null
    S.histAt = null
    if (cam) { S.zoom = cam.zoom; S.tx = cam.tx; S.ty = cam.ty; applyTransform() }
    applyDim(L)
    return true
  }
  // 그 그림에 자리가 없을 때 카메라가 대신 볼 곳(§3j J3 개정 · P8) — 자원 = 들어 있던 곳의 사슬, 관계 = 양 끝(각자 사슬).
  //   사슬은 지금 모델 → 엔진 구조 문서 → 옛 모습(`was`) 순으로 부모를 찾는다. 왼쪽(지금)에는 없어진 것이 없어서 필요하다.
  function cmpNear (m) {
    const was = wasIndex()
    const S0 = window.NT_STRUCT ? new Map(window.NT_STRUCT.nodes.map((n) => [n.id, n])) : null
    const up = (id) => {
      const out = []
      for (let cur = id, g = 0; cur && g < 8; g++) {
        const n = mById.get(cur) || (S0 && S0.get(cur)) || was.get(cur)
        // 🔴 엔진 계정·리전은 캔버스 id 가 다르다(`acct-*`·`r-*` — 어댑터 한 벌). 안 바꾸면 VPC 를 통째로 지운 줄이 지금 리전 상자를
        //    못 찾는다(QA B 2차 N-01 · 0단계: 왼쪽 칸이 통째로 비었다)
        out.push(n && !mById.has(cur) ? globalThis.NT_ADAPT.canvasIdOf(n) : cur)
        cur = n && n.parent_id
      }
      return out
    }
    const r = m.edge ? relOf(m.id) : null
    return r ? [up(r.a), up(r.b)] : [up(m.id)]
  }
  // `foc` = 누른 줄의 대상 id 들(§3j J3 「줄 하나를 누르면 그 줄의 대상으로 간다」 · 사용자 결정 2026-10-21 · QA C Q-5).
  //   없으면(칸 머리 · 코드가 시각만 준 열기) 그 시각 변경 전부다. 🔴 표시(`marks`)는 어느 쪽이든 그 시각 **전부**다 — 카메라만 고른다.
  function cmpOpen (at, foc) {
    cmpClose()
    const cam = { zoom: S.zoom, tx: S.tx, ty: S.ty }
    const repRows = histRows().filter((r) => r.at === at && r.kind === 'replaced')
    const rep = new Set(repRows.map((r) => r.targets[1]))
    // 늦은 바뀜 짝의 옛 쪽(t1 < at) — 이 줄의 변경이다(J0 · B-11). 칸 머리 수·표시·되살림에 같이 든다.
    const late = repRows.map((r) => r.lateGone).filter(Boolean)
    const here = [...(OBS.changes || []).filter((c) => c.at === at), ...late]
    const was = wasIndex()
    const R = { ghosts: new Set(), noShape: [], noShapeLater: [], orphans: [], bad: [] }
    const struct = !!window.NT_STRUCT
    const topo = struct ? cmpStruct(at, R, late) : cmpFallback(at, R, late)
    const lost = []
    const marks = []
    const seen = new Set()
    const push = (m, c) => {
      const k = `${m.edge ? 'edge' : 'node'}|${m.id}`
      if (seen.has(k)) return
      seen.add(k)
      const w = histWho(c, was)
      marks.push({ ...m, who: `${w.word} ${w.name}`, near: cmpNear(m) })
    }
    for (const c of here) {
      const m = cmpMarkOf(c, rep.has(c.target_id))
      // 모습이 없는 없어짐은 그리지 않는다 — 글자 목록이 말한다(J2).
      if (m && !(m.kind === 'gone' && !R.ghosts.has(c.target_id))) push(m, c)
    }
    // 없어진 카드에 붙어 있던 연결도 같이 없어졌다 — 목록 줄은 따로 없어도 그림에서는 유령이다.
    for (const id of [...R.ghosts].sort()) {
      const c = { target_id: id, target_kind: relOf(id) ? 'edge' : 'node' }
      push({ id, edge: c.target_kind === 'edge', kind: 'gone', label: '없어짐' }, c)
    }
    const el = document.createElement('div')
    el.id = 'cmp'
    el.innerHTML = '<div class="chead">' +
      // 칸 머리를 누르면 그 시각 변경 전부로 맞춘다(§3j J3 · 2026-10-21) — 줄 하나를 누른 뒤 같은 시각의 나머지를 보는 길
      `<span class="ctitle" data-call title="누르면 이 시각의 변경 ${here.length}건 전부를 화면에 맞춥니다">${esc(hhmm(at))} 변경 ${here.length}건 — 그 변경 직후의 모습</span>` +
      '<span class="close" data-cclose title="닫기 (Esc)">✕</span></div><div class="clist"></div>'
    // 그리지 못한 없어짐(모습 없음)도 카메라가 갈 자리는 있다 — 지금 그 자원이 있던 곳(§3j J3 · N-01). 두 칸이 같은 목록을 받는다
    const seats = R.noShape.map((c) => ({ id: c.target_id, near: cmpNear({ id: c.target_id, edge: c.target_kind === 'edge' }) }))
    // 카메라가 감쌀 것 — 누른 줄의 대상만. 표시가 없는 대상(가지치기된 유령 등)은 「들어 있던 곳」 사슬로만 간다(J3 · 지어내지 않는다)
    const fids = foc && foc.length ? [...new Set(foc.map(String))].sort() : null
    let fm = null
    if (fids) {
      const fs = new Set(fids)
      fm = { marks: marks.filter((m) => fs.has(m.id)), seats: seats.filter((x) => fs.has(x.id)) }
      for (const id of fids) {
        if (!fm.marks.some((m) => m.id === id) && !fm.seats.some((x) => x.id === id)) fm.seats.push({ id, near: cmpNear({ id, edge: !!relOf(id) }) })
      }
    }
    CMPV = { at, n: here.length, el, frame: null, marks, seats, lost, R, struct, undrawn: null, ready: false, cam, focus: null, foc: fids, fm,
      error: R.bad.length ? `이 시각의 구성도를 만들지 못했습니다 — ${R.bad.slice(0, 2).join(' · ')}` : '' }
    if (!CMPV.error) {
      const obs = cmpObs(at, topo, lost)
      // 그 시각까지 흐르다가 멈춘 선(§3j J4 개정 · P8) — 칸의 관측 문서에는 이력이 없으므로 부모가 잘라 넘긴다
      const cfg = { v: 1, at, topo, obs, marks, seats, focus: fm, stopped: [...stoppedOf(OBS.changes, at)].sort() }
      // 칸은 왼쪽 화면의 체크박스를 따른다(§17.23)
      cfg.aws = { az: S.awsAz !== false, sn: S.awsSn !== false }
      // 설정 태그는 실행되지 않는다(JSON) — CSP 해시 대상이 아니다. `<` 를 이스케이프해 태그를 닫지 못하게 한다.
      const tag = '<script type="application/json" id="nettopo-cmp">' +
        JSON.stringify(cfg).replace(/</g, '\\u003c') + '<' + '/script>'
      const fr = document.createElement('iframe')
      fr.setAttribute('sandbox', 'allow-scripts')
      fr.setAttribute('title', `${hhmm(at)} 변경 직후의 모습`)
      fr.srcdoc = DOC_SRC.replace('</head>', () => tag + '</head>')
      el.appendChild(fr)
      CMPV.frame = fr
    }
    el.querySelector('.clist').innerHTML = cmpListHtml()
    el.addEventListener('click', (ev) => {
      if (ev.target.closest('[data-cclose]')) { cmpClose(); renderDock(); return }
      if (ev.target.closest('[data-call]') && CMPV) { const t = CMPV.at; cmpOpen(t, null); S.histAt = t; renderDock() }
    })
    document.getElementById('stage').after(el)
    // 왼쪽(지금)도 바뀐 곳으로 간다(§3j J1·J3 개정 · P8) — 그림 내용은 그대로, 카메라만. 칸이 왼쪽을 반으로 줄인 뒤에 잰다
    CMPV.focus = fm ? cmpFocus(fm.marks, fm.seats) : cmpFocus(marks, seats)
    // 갈 곳을 모르면 왼쪽 칸 폭으로 다시 맞춘다 — 열기 전 전체 폭 카메라에 머물면 그림 절반이 칸 뒤에 가려진다(N-01 · 6단계)
    if (!CMPV.focus) fit()
    // 비교가 열려 있는 동안 검색 흐림은 꺼진다(J1) — 회색화 채널이 두 뜻을 동시에 말하지 않게 한다.
    applyDim(L)
    return { at, open: true, n: here.length, marks: marks.length, struct, error: CMPV.error || null }
  }
  function toggleHistGroup (key) {
    if (HIST_OPEN.has(key)) HIST_OPEN.delete(key); else HIST_OPEN.add(key)
    renderDock()
    return HIST_OPEN.has(key)
  }
  // 줄 고르기 = 오른쪽 칸 열기(J1). 「지금」 줄은 닫기다. 평소 그림은 다시 흘리지 않는다.
  // `foc` = 누른 줄의 대상(없으면 그 시각 전부 — §3j J3 · 2026-10-21). 하네스·코드가 시각만 주면 옛 뜻 그대로다.
  function pickTime (at, foc) {
    // 🔴 짝이 어긋나면 **고를 수 없다** — 다른 구조를 보고 만든 차분을 이 구조에 되짚으면 엉뚱한 카드를 뺀다.
    if (OBS_STALE) return null
    // `null` 도 「지금」이다(닫기) — 부르는 쪽이 지금 시각을 몰라도 닫을 수 있게 한다.
    // 🔴 변경 줄을 **먼저** 찾는다 — 「지금」 줄은 맨 위 변경 줄과 시각이 같을 수 있고(마지막 관측이 변경을 적은 때),
    //    시각으로 「지금」을 먼저 찾으면 그 변경 줄을 눌러도 비교가 닫힌다(§3j J0 · 2026-10-15 데모 발견).
    const rs = at == null ? null : histRows()
    const r = at == null ? { kind: 'now' } : rs.find((x) => x.kind !== 'now' && x.at === at) || rs.find((x) => x.at === at)
    if (!r) return null
    if (r.kind === 'now') { cmpClose(); renderDock(); return { at: null, open: false } }
    const v = cmpOpen(r.at, foc || null)
    S.histAt = r.at
    renderDock()
    return v
  }
  if (!CMP) {
    window.addEventListener('message', (ev) => {
      // 🔴 **열린 칸의 틀이 보낸 것만** 듣는다 — 다른 창·다른 틀의 메시지로 칸이 닫히거나 목록이 바뀌면 안 된다.
      if (!CMPV || !CMPV.frame || ev.source !== CMPV.frame.contentWindow) return
      const d = ev.data || {}
      if (d.nettopo === 'cmp-close') { cmpClose(); renderDock() } else if (d.nettopo === 'cmp-ready') {
        CMPV.undrawn = Array.isArray(d.undrawn) ? d.undrawn.map(String) : []
        CMPV.el.querySelector('.clist').innerHTML = cmpListHtml()
        // 두 칸을 같은 배율 · 같은 곳으로(§3j J3 · N-01) — 칸이 제 계획을 알려 오면 왼쪽을 맞추고 칸에 맞출 값을 보낸다.
        //   칸이 받아 섰다고 답하면(`cmp-synced`) 그때가 「준비됨」이다
        const kid = d.focus && typeof d.focus === 'object' ? d.focus : null
        const mine = cmpBrief(CMPV.focus)
        const got = cmpShare(CMPV.focus, kid)
        if (got) CMPV.focus = got
        CMPV.frame.contentWindow.postMessage({ nettopo: 'cmp-cam', other: mine }, '*')
      } else if (d.nettopo === 'cmp-synced') {
        CMPV.ready = true
      }
    })
  }
  // ---- §3j 이력 비교 화면 — 오른쪽 칸 쪽 (J3·J4 · 검사 114) ----
  // 이 문서가 비교 칸의 틀 안에서 돌 때만 쓴다(`CMP`). 그림 자체는 평소와 같은 배치·같은 그리기를 지난다 —
  // 여기 있는 것은 그 위에 얹는 **모양**(생김·없어짐)뿐이다. 🔁 개정(사용자 결정 2026-10-14 · QA B P8): 그림 위 글자 0
  // (알약·시각 칩 폐기 — 목록 줄이 글로 말한다) · 확대 창 폐기(카메라가 바뀐 곳으로 간다 — `cmpFocus`).
  let CMPD = { marks: [], undrawn: [], keep: null }
  // 생긴 것 = 제 색 그대로 조금 선명·굵게(§3j J4 개정). 🔴 색을 바꾸지 않는다 — 흰색(선택 채널)·초록(흐름) 어느 쪽도 빌리지 않는다.
  //   인라인 `style` 로 둔다(그림 바이트에 남는다 — CSS 파일에만 두면 내보낸 그림에서 빠진다, 브레이크 `125-모양이CSS에만` 과 같은 이유).
  const CMP_VIVID = 'filter:saturate(1.5) brightness(1.2)'
  // 그린 뒤에 모양을 얹는다. 🔴 선은 **엔진 관계 id(`obsIds`)로만** 찾는다 — 끝점 카드로 찾으면 같은 두 카드
  //    사이의 다른 관계에 표시가 앉는다. 선 하나가 관계 여럿을 묶고 있으면 **전부가 같은 변화일 때만** 선
  //    모양을 바꾼다. 일부만이면 모양은 그대로 두고 판정 기록(`label`)이 수를 말한다(묶인 나머지는 그대로 있다).
  // `label` 은 **그리지 않는다** — 표시 판정의 기록(`cmpDump().marks`)으로만 남는다(검사 114·135 가 읽는다).
  function cmpDecorate (L) {
    const vp = document.getElementById('vp')
    // 관계 id(`|`)와 자원 id(`/`)는 겹치지 않는다 — 자원도 넣는 것은 피어링 선(자원 하나 = 선 하나)을 같은 규칙으로 재려고
    const kindOf = new Map(CMP.marks.map((m) => [m.id, m.kind]))
    const svg = []
    const out = []
    const undrawn = []
    const keep = new Set()            // 「생김」 시각에 흐리지 않을 자리(상자 키) — 표시가 앉은 자리 전부
    const keepLn = new Set()          // 흐리지 않을 선(선 키) — 표시가 앉은 선 + 생긴 것에 닿는 선
    const born = new Set()            // 생긴 상자 키(묶음 안 · 끝 자리 제외) — 그 선도 흐리지 않는다
    let nAdd = 0
    // 선에 앉는 표시 — 관계 선 · 새 틀의 피어링 선(자원)
    const onLines = (m, ws) => {
      for (const w of ws) {
        const same = w.obsIds.filter((id) => kindOf.get(id) === m.kind).length
        const whole = same === w.obsIds.length
        const p = svgEl.querySelector(`.aw-ln[data-k="${CSS.escape(w.id)}"] path.aw-vis`)
        if (p && whole && m.kind === 'gone') {
          p.setAttribute('stroke', C.dim); p.setAttribute('stroke-opacity', '0.6')
          p.setAttribute('stroke-dasharray', DASH.ghost); p.classList.remove('flowlive')
          p.setAttribute('data-cmp', 'gone')
          // 없어진 선에는 흐름 초록 겹도 멈춤 빨간 점선도 남기지 않는다 — 그때 그 선은 없어졌다
          for (const f of svgEl.querySelectorAll(`.aw-fl[data-k="${CSS.escape(w.id)}"], .aw-fs[data-k="${CSS.escape(w.id)}"]`)) f.remove()
        } else if (p && whole && m.kind === 'add') {
          // 제 색 그대로 · 굵기 ×1.6 · 조금 선명하게
          const sw = Number(p.getAttribute('stroke-width')) || 1.3
          p.setAttribute('stroke-width', String(Math.round(sw * 16) / 10)); p.setAttribute('style', CMP_VIVID)
          p.setAttribute('data-cmp', 'add')
        }
        if (m.kind === 'add' && whole) nAdd++
        keepLn.add(w.id)
        const text = whole ? m.label : `${m.label} · 선 ${w.obsIds.length}개 중 ${same}개`
        out.push({ id: m.id, kind: m.kind, edge: !!m.edge, wire: w.id, whole, label: text })
      }
    }
    for (const m of CMP.marks) {
      // 선으로도 상자로도 못 받는 것의 자리(§17.23 W4 개정) — 끝 자리 · 그 VPC · 피어링 선
      const seat = NT_AWS.markSeat(m.id)
      let tag = ''
      let at = null
      if (m.edge) {
        // 찾는 끈은 엔진 관계 id(`obsIds`)다(§17.23)
        const ws = NT_AWS.markLines(m.id).map((l) => ({ id: l.k, obsIds: l.ids, pts: l.pts, ends: l.ends }))
        if (ws.length) { onLines(m, ws); continue }
        // 선을 긋지 않는 관계 — 끝 자리에 앉는다. 모양은 걸지 않는다(그 자원은 그대로다) · 판정 기록이 관계 이름을 말한다
        if (!seat || !seat.at) { undrawn.push(m.id); continue }
        const r = relOf(m.id)
        at = seat.at; tag = r && r.t === 'association' ? `${typeWord(r.a.split('/')[0])} 연관` : relWord(r && r.t)
      } else {
        at = NT_AWS.markAt(m.id)
        if (!at && seat && seat.lines) { onLines(m, seat.lines.map((l) => ({ id: l.k, obsIds: l.ids, pts: l.pts, ends: l.ends }))); continue }
        if (!at && seat && seat.at) { at = seat.at; tag = typeWord(String(m.id).split('/')[0]) }
      }
      const hit = at && { b: { id: at.key, x: at.x, y: at.y, w: at.w, h: at.h }, inside: at.inside }
      if (!hit) { undrawn.push(m.id); continue }
      const { b, inside } = hit
      const g = svgEl.querySelector(`#vp ${at.sel}`)
      if (m.kind === 'gone' && !inside) {
        if (g) { g.setAttribute('opacity', '0.45'); g.setAttribute('data-cmp', 'gone') }
        svg.push(`<rect x="${b.x - 3}" y="${b.y - 3}" width="${b.w + 6}" height="${b.h + 6}" rx="8" fill="none"` +
          ` stroke="${C.dim}" stroke-opacity="0.8" stroke-width="2" stroke-dasharray="${DASH.ghost}" vector-effect="non-scaling-stroke"/>`)
      } else if (m.kind === 'add' && !inside) {
        if (g) {
          g.setAttribute('data-cmp', 'add'); g.setAttribute('style', CMP_VIVID)
          // 조금 굵게 — 아이콘 모양에 **자기 색** 테를 두르고(`use`), 상자(서브넷·VPC)는 테를 굵게 한다
          for (const u of g.querySelectorAll('use[fill]')) {
            const f = u.getAttribute('fill')
            if (f && f !== '#fff' && f !== 'none') { u.setAttribute('stroke', f); u.setAttribute('stroke-width', '2'); u.setAttribute('stroke-linejoin', 'round') }
          }
          const fr = g.querySelector(':scope > rect[stroke]')
          if (fr) fr.setAttribute('stroke-width', String(Math.round((Number(fr.getAttribute('stroke-width')) || 1) * 16) / 10))
        }
        born.add(b.id); nAdd++
      }
      keep.add(b.id)
      const text = tag ? `${m.label} · ${tag}` : inside ? `${m.label} · 묶음 안` : m.label
      out.push({ id: m.id, kind: m.kind, edge: !!m.edge, box: b.id, inside, label: text, ...(tag ? { seat: true } : {}) })
    }
    if (vp && svg.length) vp.insertAdjacentHTML('beforeend', `<g id="cmpmarks" pointer-events="none">${svg.join('')}</g>`)
    // 「생김」 시각 = 나머지를 흐린다(§3j J4 개정 · §8.3 diff 베이스라인). 생긴 것에 닿는 선도 남긴다
    let kept = null
    if (nAdd) {
      for (const w of NT_AWS.obsLines()) if (born.has(w.a) || born.has(w.b)) keepLn.add(w.k)
      kept = NT_AWS.keepOnly([...keep], [...keepLn])
    }
    CMPD = { marks: out, undrawn: [...new Set(undrawn)].sort(), keep: kept }
  }
  function cmpFit () {
    const r = svgEl.getBoundingClientRect()
    const pad = 16
    S.zoom = Math.min((r.width - 2 * pad) / L.canvasW, (r.height - 2 * pad) / L.canvasH, 1)
    S.tx = (r.width - L.canvasW * S.zoom) / 2
    S.ty = (r.height - L.canvasH * S.zoom) / 2
    applyTransform()
  }
  // 초 → 사람 단위(검사 118 ③). 「240000초」는 읽히지 않는다(배포본 실측 2026-09-28).
  const durKo = (s) => s < 120 ? `${s}초` : s < 7200 ? `${Math.round(s / 60)}분`
    : s < 259200 ? `약 ${Math.round(s / 3600)}시간` : `약 ${Math.round(s / 86400)}일`
  // 대장 94 가 요구하는 「차분의 한계 3개」. 접지 않는다 — 접으면 아무도 안 읽는다.
  function histLimits () {
    const h = (OBS && OBS.history) || {}
    return [
      '누가 바꿨는지는 이 목록으로 알 수 없습니다 — 관측 결과의 차이이며 감사 로그가 아닙니다.',
      // 🔴 이 값은 수집 주기가 아니라 목록 구간 안의 **가장 긴 확인 간격**이다(수집기가 가장 거친 쪽을
      //    적는다 — 모니터링이 꺼져 있던 동안도 한 간격). 188 배포본이 「수집 주기(240000초)」라고 말했다(§3j J0).
      h.resolution_seconds
        ? `확인 간격이 길게는 ${durKo(h.resolution_seconds)}입니다 — 그 사이에 여러 번 바뀐 것은 한 줄로 표시됩니다.`
        : '확인 사이에 여러 번 바뀐 것은 한 줄로 표시됩니다.',
      h.baseline_at ? `기준 시각 ${kstAt(h.baseline_at)} 이전의 변경은 확인할 수 없습니다.`
        : '기준 시각 이전의 변경은 확인할 수 없습니다.'
    ]
  }
  // 목록이 잘렸으면 **목록 끝에서** 그렇다고 말한다(ADR 0006 O4 · 대장 19). 잘림은 원인이 둘이다 —
  // 보존 창(오래된 것) · 줄 수 상한(많은 것). 문서는 `truncated` 하나만 실으므로 창 쪽은 하네스처럼
  // `baseline_at` 으로 되짚고, 창으로 설명되지 않으면 상한이다. 둘 다면 둘 다 말한다.
  // 🔴 맨 아래 줄을 「처음」으로 읽으면 사람이 「그 전엔 아무 변화도 없었다」로 오판한다.
  function histCutoff () {
    const h = (OBS && OBS.history) || {}
    if (h.truncated !== true) return ''
    const obsAt = OBS.run && OBS.run.observed_at
    const win = !!(h.baseline_at && obsAt && h.retention_seconds &&
      Date.parse(h.baseline_at) < Date.parse(obsAt) - h.retention_seconds * 1000)
    const out = []
    if (win) out.push(`${durKo(h.retention_seconds).replace(/^약 /, '')}보다 오래된 변경은 목록에서 제외했습니다`)
    // 🔴 상한 문장은 「창으로 설명이 안 될 때」 또는 「목록이 상한에 닿았을 때」. 상한이 없는 문서(상한 전
    //    상태 함수가 쓴 것)도 잘림 자체는 말한다 — 숫자만 뺀다.
    const n = (OBS.changes || []).length
    if (!win || (h.max_changes && n >= h.max_changes)) {
      out.push(h.max_changes
        ? `목록이 ${h.max_changes.toLocaleString('en-US')}줄을 넘어 오래된 변경부터 제외했습니다`
        : '목록 줄 수 상한을 넘어 오래된 변경부터 제외했습니다')
    }
    return `<div class="hcutoff">${esc(out.join(' · '))} — 맨 아래 줄이 처음이 아닙니다.</div>`
  }
  function histHtml () {
    // 사람이 할 일을 말한다(§3j J0 · 검사 118 ②) — 옛 문장은 「렌더러가 만들 수 없다(불변식 ③)」였다.
    if (!OBS) {
      // 켠 채 기다리는 중이면 「켜면」이 아니라 무엇을 기다리는지 말한다(B-09 — 이 그림이 더 새 구조면 새 구조의 첫 관측).
      if (LIVE.on && LIVE.phase === 'wait') {
        return `<div class="off">${LIVE.pairWait ? '이 구성도가 더 최신입니다 — 새 구성으로 측정한 첫 관측' : '첫 관측'}을 기다리는 중입니다 — 도착하면 변경 이력이 표시됩니다.</div>`
      }
      return LIVE_SLOT
        ? '<div class="off">아직 받은 변경 이력이 없습니다 — 상단 «모니터링»을 켜면 여기에 표시됩니다.</div>'
        : '<div class="off">변경 이력이 없습니다 — 이 파일에는 이력 데이터가 포함되지 않았습니다.</div>'
    }
    // 🔴 목록을 그려 놓고 클릭이 아무 일도 안 하게 두지 마라 — 짝이 어긋나면 되짚기가 멈추므로
    //    (위 `pickTime`) 행은 **죽은 조작**이 된다. 이유를 말하고 목록을 내지 않는다.
    if (OBS_STALE) {
      return `<div class="off">관측 문서가 이 그림과 <b>맞지 않습니다</b> — 관측은 ${esc(kstAt(OBS_PAIR))} ` +
        `구조를, 지금 구성도는 ${esc(kstAt(T.run.collected_at))} 구조를 기준으로 합니다. 다른 구조의 변경 이력을 이 구성도에 ` +
        `적용하면 엉뚱한 아이콘이 사라지므로 목록을 표시하지 않습니다. ${esc(PAIR_FIX)}.</div>`
    }
    // 밝은 줄 = 누른 줄(§3j J3 · 2026-10-21) · 칸 머리로 연 칸이면 그 시각 줄 전부
    const fk = CMPV && CMPV.foc ? CMPV.foc.join('\n') : null
    const lit = (at, ids) => S.histAt === at && (!fk || [...new Set(ids)].sort().join('\n') === fk)
    const body = histRows().map((r, i) => {
      const on = r.kind === 'now' ? !S.histAt : lit(r.at, r.targets)
      // 가른 줄(`split`)은 글자를 자르지 않고 접어 내린다 — 끝의 `(…xxxx)` 가 잘리면 가른 뜻이 없다(§3j J0 · 2026-10-21)
      return `<div class="hrow${on ? ' on' : ''}${r.kind === 'now' ? '' : ' past'}${r.cut ? ' cut' : ''}${r.split ? ' hsplit' : ''}" ` +
        `data-at="${esc(r.at)}"${r.kind === 'now' ? ' data-now' : ` data-ri="${i}"`} title="${esc(r.full || r.text)}">` +
        `<span class="hat">${esc(hhmm(r.at))}</span>` +
        (r.cut ? '<span class="hcut">끊김</span>' : '') +
        `<span class="hwhat">${r.split ? esc(r.text).replace(esc(r.split), `<span class="htail">${esc(r.split)}</span>`) : esc(r.text)}</span>` +
        (r.occ ? `<span class="hocc">${esc(r.occ)}</span>` : '') +
        (r.repeat > 1 ? `<span class="hn${r.first ? ' hfold' : ''}">${esc(histRepeat(r))}</span>` : '') +
        (r.group ? `<span class="hexp" data-hexp="${esc(r.group)}">${HIST_OPEN.has(r.group) ? '▾' : '▸'}</span>` : '') +
        '</div>' +
        // 묶음을 펼치면 이름이 줄 수만큼 나온다(검사 113 ⑤). 이름 줄도 같은 시각을 고른다.
        (r.group && HIST_OPEN.has(r.group)
          ? '<div class="hsub">' + r.items.map((x, j) =>
            `<div class="hitem${lit(r.at, [r.itemIds[j]]) ? ' on' : ''}" data-at="${esc(r.at)}" data-ri="${i}" data-ii="${j}" title="${esc(x)}">${esc(x)}</div>`).join('') + '</div>'
          : '')
    }).join('')
    // 🔴 목록이 주인공이다 — 차분 자체의 한계 3문장은 **접는다**(사용자 결정 2026-09-24: 「쓸데없는 정보가
    //    너무 많다」). 지우지는 않는다 — 없애면 사람이 「그 시각엔 이랬다」로 오판한다. 고른 시각에서
    //    **그리지 못한 것**은 여기가 아니라 오른쪽 칸 머리 아래가 말한다(§3j J2 — 그 그림 바로 옆이어야 읽힌다).
    return '<div class="sec">변경·끊김이 일어난 시각 — 누르면 그때 상태를 오른쪽에 표시합니다</div>' + body + histCutoff() +
      '<details class="hmore"><summary>이 목록으로 알 수 없는 것</summary>' +
      `<div class="hlim">${histLimits().map(esc).join('<br>')}</div></details>`
  }
  // 「특이사항」 = 같은 질문의 **현재형**(대장 94). 원인·API·코드·사유가 붙어 글이 길다 —
  // 그래서 팝오버가 아니라 독이다(대장 101 ①).
  function notesHtml () {
    const P = T.partial || []
    const out = [`<div class="sec">확인하지 못한 영역 ${P.length}건</div>`]
    if (!P.length) out.push('<div class="off">권한·한도로 확인하지 못한 영역이 없습니다.</div>')
    else {
      out.push('<table>' + P.map((p) =>
        // §3k K3: 사람 말만 글자로 — 원문 코드·AWS 메시지는 마우스를 올렸을 때(`title`).
        // §3k K5: 칸 = 곳(사람 말) | 못 읽은 종류(모르면 「일부」) · 이유 — 대상 id·`scope` 원어는 글자에 없다.
        `<tr title="${esc(p.code)} — ${esc(p.reason)}"><td class="k">${esc(partialPlace(p))}</td>` +
        `<td>${esc(p.rtype ? typeWord(p.rtype) : '일부')} · ${esc(reasonWord(p.code))}</td></tr>`).join('') + '</table>')
    }
    // §3i: 어휘를 섞지 마라 — 「확인하지 못했다」(고객 권한)와 「아직 그리지 못하는 종류」(개발)는 다르다.
    out.push('<div class="sec">이 화면에 못 그린 것</div>')
    out.push(`<div class="off">캔버스에 세우지 못한 리소스 ${(S.unplaced || []).length}개 · ` +
      `긋지 못한 관계 ${(S.unwired || []).length}개. ` +
      (listRows().length ? '<span class="wlink" data-openlist>왼쪽 아래 「!」에서 확인하세요</span>' : '') + '</div>')
    return out.join('')
  }
  function dockHead () {
    return '<div class="dock">' + DOCK_TABS.map(([k, t]) =>
      `<span class="dtab${k === S.dock ? ' on' : ''}" data-dtab="${k}">${esc(t)}</span>`).join('') +
      '<span class="close" data-close>✕</span></div>'
  }
  function renderDock () {
    sideEl.classList.toggle('on', !!S.dock)
    // 🔴 버튼의 `.on` 을 **여기서** 맞춘다 — 클릭 핸들러에서만 맞추면 `openDock()`·카드 선택·
    //    빈 곳 클릭으로 독이 바뀔 때 버튼이 거짓말을 한다(「열려 있다」고 표시된 채 닫힘).
    const hb = document.getElementById('btn-hist')
    if (hb) hb.classList.toggle('on', !!S.dock)
    if (!S.dock) { sideEl.innerHTML = ''; return }
    sideEl.innerHTML = dockHead() + (S.dock === 'hist' ? histHtml() : notesHtml())
  }
  function openDock (tab) {
    if (!DOCK_TABS.some(([k]) => k === tab)) return null
    S.dock = tab; renderDock(); return S.dock
  }
  function closeDock () { S.dock = ''; renderDock(); return S.dock }

  // ---- §13-58 딥링크: `#sel=<id>` ↔ 선택 ----
  let hashLock = false
  // §3g G4: 범위도 해시에 싣는다 → 「acct-2222 뷰」 링크를 팀에 줄 수 있다(산출물이 서명 URL 하나로
  // 공유되는 구조 — ADR 0003 — 와 맞물려 **같은 HTML 로 다른 범위를 가리키는 링크**가 생긴다).
  // 🔴 `scope` 가 **먼저**다. `sel` 을 먼저 읽고 적용하면 범위가 그 선택을 다시 지운다.
  function setHash (id) {
    // 비교 칸은 주소창이 없다 — 틀 안의 해시를 바꾸면 부모의 뒤로 가기 기록만 늘어난다.
    if (CMP) return
    hashLock = true
    const p = []
    // 🔁 대장 134 · G4: `scope=<id>,<id>` — id 마다 인코딩하고 쉼표로 잇는다(정렬은 `scopeSet` 이 이미 했다)
    if (SCOPE) p.push('scope=' + SCOPE.map(encodeURIComponent).join(','))
    if (id) p.push('sel=' + encodeURIComponent(id))
    const h = p.length ? '#' + p.join('&') : ''
    if (location.hash !== h) location.hash = h
    setTimeout(() => { hashLock = false }, 0)
  }
  // 깨진 퍼센트 인코딩(`%E0%A4%A`)은 `decodeURIComponent` 가 던진다 — 그 조각만 버린다(페이지를 죽이지 않는다 · G4)
  const hashDec = (x) => { try { return decodeURIComponent(x) } catch (e) { return null } }
  function applyHash () {
    // 🔴 `scope`·`sel` 을 **맨 앞에서 한 번에** 읽어 들고 간다(QA A-07 · 대장 133 · §17.26). 범위를 적용하면(`scopeApply` → `setHash(S.sel)`)
    //    주소가 다시 써지는데 로딩 중에는 `S.sel` 이 `null` 이라 `sel` 이 지워진다 — 그 뒤 주소를 **다시** 읽으면 빈손이었다
    //    (도구가 스스로 만든 `#scope=…&sel=…` 주소를 스스로 못 읽었다). 순서(`scope` 먼저)는 그대로다.
    const h = location.hash || ''
    const sc = /(?:^|#|&)scope=([^&]+)/.exec(h)
    const m = /(?:^|#|&)sel=([^&]+)/.exec(h)
    const id = m ? hashDec(m[1]) : null
    // 🔴 링크의 계정 id 가 이 스냅샷에 없을 수 있다(다른 수집에서 만든 링크) → `scopeSet` 이
    //    검증해 **그 id 만** 버린다 · 남는 것이 없으면 `null`(전체). 조용히 「그 계정만」인 척하면 빈 화면이 정답이 된다.
    // 🔁 대장 134 · G4: 쉼표로 나눈 조각마다 푼다 — 옛 링크(id 하나)는 「조각 하나」라 그대로 열린다. 주소가 실제로 적용한
    //    집합(정렬 · 버린 조각 없음)과 글자가 다르면 그 집합으로 고쳐 쓴다(`setHash`) — 같은 범위는 주소도 한 벌이다(불변식 ②).
    //    범위가 바뀌면 `scopeApply` 가 이미 고쳐 쓰지만, 조각이 **전부** 못 쓰는 것이면 범위가 안 바뀌어(전체 → 전체) 여기서만 고친다.
    if (sc) {
      scopeApply(sc[1].split(',').map(hashDec).filter(Boolean))
      if (sc[1] !== (SCOPE ? SCOPE.map(encodeURIComponent).join(',') : '')) setHash(S.sel)
    }
    if (!id) return null
    if (!NT_AWS.open(id)) return null
    // 연 선택을 상태와 주소에 되돌린다 — 범위 적용이 지운 `sel` 이 주소에 다시 서야 그 주소를 또 나눌 수 있다(왕복)
    S.sel = id; setHash(id)
    return id
  }

  // ---------------- 인터랙션 ----------------
  let L = null
  // §17 새 도식의 문맥 — 배치·그림은 aws-canvas.js, 팬·줌·검색·범위·잔차는 여기 것을 그대로 쓴다
  const awsCtx = () => ({ T, mById, accounts, acctOf, acctLabel, acctMask, acctText, acctNameOf, acctNumOf, regionRank, goneByHand, denyOf, wipedOf, S, svgEl,
    stage: document.getElementById('stage'), esc, applyTransform, applyDim: (LL) => applyDim(LL), syncChrome, relayout, fitAll,
    // 대장 58(승인 2026-09-14) 「카드 → AWS 콘솔 URL」 — 옛 화면에 있던 링크가 W5-3 에서 조용히 빠졌다(QA 기능개발 7).
    //   경로표·함수는 그대로 남아 있었고 그리는 곳만 없었다. 검사 58 이 `api.consoleUrl()` 만 재서 못 잡았다.
    consoleUrl,
    // 태그 칸(§17.11) — 묶음 판정은 **필터와 같은 함수**를 쓴다. 두 곳에서 따로 분류하면 갈린다.
    tagRows: (n) => {
      const t = tagsOf(n); const ks = Object.keys(t)
      if (!ks.length) return []
      const by = new Map(TAG_BUCKETS.filter((b) => b !== 'Untagged').map((b) => [b, []]))
      for (const k of ks.sort()) { const b = bucketOf(k); if (by.has(b)) by.get(b).push(k) }
      const out = []
      for (const [b, keys] of by) for (const k of keys) out.push({ a: k, b: String(t[k]), h: b })
      return out
    },
    // `SCOPE` = 정렬한 id 를 쉼표로 이은 글자 | null(`S.scope`) — 새 도식은 「좁혔나」만 보고 판정은 `outScope` 로 한다(대장 134)
    TYPE_LABEL, SCOPE: S.scope, popReason, onOpt: () => cmpFollow(), onSel: (k) => { S.sel = k; setHash(k) }, rerender: () => render(L),
    // 사용률(§17.25) — 「사용률」 켬 · 모니터링과 **무관** · 「지금」 화면만(비교 칸 = 과거 시각에는 그리지 않는다 · 대장 95 와 같은 이유)
    util: { rows: () => (!CMP && S.util && !OBS_STALE && UTROWS && UTROWS.length ? UTROWS : null),
      on: () => !CMP && !!S.util, can: () => utilCan(), click: () => utilClick() },
    // §17.22 — 보기 범위 **때문에만** 빠진 모델 노드(필터·손으로 숨긴 것은 아니다)
    outScope: (id) => !!SCOPE && mById.has(id) && !inScope(id) && !((FILTER && !FILTER.has(id)) || HIDDEN.has(id)),
    // 지금 그림의 구조 수집 시각(KST 글자) — 없으면 `null`(시각을 짓지 않는다 · §17.11 LB 「대상」 줄 꺼짐)
    structAt: () => (T.run && T.run.collected_at ? kstAt(T.run.collected_at) : null),
    // 실시간 모니터링(§17.20) — 판정은 이 파일이 한다(`obsOf`·`wireObs`). 새 도식은 자리와 모양만 정한다
    obs: { on: () => !!S.monitor, C, node: (b) => (!obsIndex().size ? null : obsOf(b.node, b) || ''), line: (w) => wireObs(w, L),
      // 흐르다가 멈춘 선(§8.3 개정 · 사용자 결정 2026-10-14 QA B P8) — 빨간 점선. 지금 초록이면 초록이 이긴다(도식이 초록을 먼저 본다)
      stopped: (w) => !!S.monitor && !OBS_STALE && (w.obsIds || []).some((id) => flowStopped().has(id)),
      // 정보창 「상태」 칸(§17.20). 아이콘 = `obsRows` 의 줄 · 선 = 판정 + 판정 근거
      rows: (b) => obsRows(b.node, b),
      // 모델 노드 **하나**의 관측 행(대표 묶음 없이 · §17.11 DX 행 — 연결 안에 든 VIF 줄마다). `null` = 꺼짐·짝 어긋남·관측 문서 없음
      //   (부르는 쪽이 구조 상태로 떨어진다) · `''` = 문서는 있는데 이 대상의 행이 없다. 판정·문구는 여기서만 정한다
      of: (id) => (!S.monitor || OBS_STALE || !obsIndex().size ? null : obsIndex().get(id) || ''),
      word: (st, o) => (o && o.deleting === true ? OBS_DELETING : OBS_WORD[st] || st),   // 지우는 중(관측 1.12.0) — VIF 줄도 정보창 판정과 같은 말
      // DX VIF 의 BGP 피어 수(§17.11 DX 행) — 글은 `obsBgp` 한 곳
      bgp: (o) => obsBgp(o),   // 글은 `obsBgp` 한 곳(「모니터링 이상」 절의 상태 칸도 같은 글 · §17.26 C-8)
      // 지금 얹힌 관측 문서의 시각(KST 글자) — 없으면 `null`(시각을 짓지 않는다 · §17.11 LB 「대상」 줄)
      at: () => (OBS && OBS.run && OBS.run.observed_at ? kstAt(OBS.run.observed_at) : null),
      lineRows: (w) => {
        if (!S.monitor || OBS_STALE || !OBSROWS.length) return null
        const row = wireRow(w)
        // 판정할 행이 없는 선도 칸이 있다 — 「잴 수 없다」 + 이유(§17.20 · 로드맵 §11.1 「모든 선이 셋 중 하나」). 색·배지는 그대로다
        if (!row) return [['판정', LINE_UNMEASURED], ['이유', lineNoRowWhy(w)]]
        // 🔴 「판정」이 첫 줄이다(검사 132) — 그 뒤는 `wireGradeRows` 의 줄. 줄 = `[이름, 값, title, data-row]`
        // LB → 대상 선(§17.27) — 「대상」 줄 = 그 선 등록의 헬스 · 못 읽었으면 짓지 않는다.
        //   🔴 수 절은 LB 정보창 「대상」 줄과 **같은 함수**(`NT_AWS.targetsWord` · §17.11 · 사용자 결정 2026-10-22) — 여기서 따로 지으면
        //      헬스 체크를 끈 Lambda 선이 판정 「정상」 옆에 「1개 중 0개 정상」을 적는다(고객이 헷갈렸다). 시각은 LB 정보창이 말한다
        const lt = lineTargets(w)
        const tg = !lt ? [] : lt.unread ? [['대상', '대상 상태를 읽지 못했습니다 — 개수를 표시하지 않습니다']]
          : [['대상', lt.total ? NT_AWS.targetsWord(lt)[0] : '대상 없음(확인함)']]
        // 「사유」 줄(#150) — 엔진이 이유를 실어 보낸 행에만 · 고객 말 + 원문은 title(`lineWhy`)
        const lw = lineWhy(row)
        return [['판정', wireObs(w, L) ? OBS_WORD.live : OBS_WORD[row.status] || row.status], ...tg,
          ...(lw ? [['사유', lw.t, lw.title, 'linewhy']] : []),
          ...wireGradeRows(w, L).map(([k, v, r, t]) => [k, v, t, r])]
      },
      // 선 위 등급 배지(§17.20) — 글자는 `wireBadge`. 자리는 도식이 정한다
      badge: (w) => wireBadge(w) } })
  // 열린 비교 칸은 왼쪽 화면을 따른다(§17.23) — 같은 시각으로 다시 연다
  function cmpFollow () {
    if (CMP || !CMPV) return
    const at = CMPV.at
    cmpOpen(at, CMPV.foc); S.histAt = at; renderDock()
  }
  function relayout () {
    L = layout()
    S.unplaced = unplacedRows(L)
    S.unwired = unwiredRows(L)
    // 그린 자원의 그리지 못한 대상(배치 D · Global Accelerator · 사용자 결정 2026-10-21) — 「!」 표의 셋째 묶음 · 줄을 누르면 그 자원의 정보창
    //   #129(2.11.0) — Cloud WAN · VPC Lattice 도 같은 묶음(줄마다 `type` · `open` 을 든다 · 없으면 Global Accelerator)
    S.notes = (L.awsNotes || []).map((x) => ({ id: x.open || x.ga, key: x.id, type: x.type || 'global_accelerator', name: x.name, vpc: '—', region: x.region, fixedSay: x.text, why: x.text, detail: '', open: x.open || x.ga }))
    render(L)
    if (window.__vfy) window.__vfy.onLayout()   // §15 오버레이 재수집(배치가 바뀌었다) — 하네스가 주입했을 때만 있다(대장 82 개정 2026-10-20)
    return L
  }

  // 첫 화면 배율은 도식이 정한다(aws-canvas.js `fit`).
  function fit () {
    // 비교 칸의 「맞춤」 = 그림 전체(평소와 같은 뜻). 첫 화면은 바뀐 곳이다(§3j J3 개정 · 시작 블록의 `cmpFocus`)
    if (CMP) return cmpFit()
    return NT_AWS.fit(L, awsCtx())
  }
  function fitAll () {
    const r = svgEl.getBoundingClientRect()
    S.zoom = Math.min(r.width / L.canvasW, r.height / L.canvasH, 1.4)
    S.tx = (r.width - L.canvasW * S.zoom) / 2
    S.ty = (r.height - L.canvasH * S.zoom) / 2
    applyTransform()
  }
  function applyTransform () {
    const vp = document.getElementById('vp')
    if (vp) vp.setAttribute('transform', `translate(${S.tx},${S.ty}) scale(${S.zoom})`)
    const uvp = document.getElementById('aw-utl-vp')   // 사용률 세모 층도 같이(§17.25 「성능」)
    if (uvp) uvp.setAttribute('transform', `translate(${S.tx},${S.ty}) scale(${S.zoom})`)
    // §4c 대장 80: 배율은 화면에 숫자로 보인다. 피드백이 없으면 조작이 먹었는지 알 수 없다.
    const zr = document.getElementById('czoom')
    if (zr) zr.textContent = Math.round(S.zoom * 100) + '%'
    // §15: 검증 오버레이는 **읽기만** 하는 구경꾼이다. 고객 화면에는 없고(사용자 결정 2026-10-20) 하네스가 주입했을 때만 있다.
    if (window.__vfy) window.__vfy.onView()
    NT_AWS.place()   // 정보창은 아이콘을 따라간다(§17.6)
  }

  let drag = null
  svgEl.addEventListener('mousedown', (ev) => {
    drag = { x: ev.clientX, y: ev.clientY, tx: S.tx, ty: S.ty, moved: false }
    svgEl.classList.add('drag')
  })
  window.addEventListener('mousemove', (ev) => {
    if (!drag) return
    S.tx = drag.tx + (ev.clientX - drag.x); S.ty = drag.ty + (ev.clientY - drag.y)
    if (Math.abs(ev.clientX - drag.x) + Math.abs(ev.clientY - drag.y) > 3) drag.moved = true
    applyTransform()
  })
  window.addEventListener('mouseup', () => { drag = null; svgEl.classList.remove('drag') })
  svgEl.addEventListener('wheel', (ev) => {
    ev.preventDefault()
    const r = svgEl.getBoundingClientRect()
    const mx = ev.clientX - r.left; const my = ev.clientY - r.top
    const f = Math.exp(-ev.deltaY * 0.0015)
    const z = Math.max(0.12, Math.min(3, S.zoom * f))
    S.tx = mx - (mx - S.tx) * (z / S.zoom); S.ty = my - (my - S.ty) * (z / S.zoom)
    S.zoom = z
    applyTransform()
  }, { passive: false })

  // §4c 대장 80: 키보드도 조작 경로다. 세 경로(버튼·휠·키보드)가 **같은 함수**를 통과해야
  // 「버튼으로는 되는데 키로는 안 되는」 갈림이 생기지 않는다(검사 75).
  // 🔴 입력창·모달 안에서는 발동하지 않는다 — 검색어 "-stage" 를 타이핑하면 축소되면 안 된다.
  window.addEventListener('keydown', (ev) => {
    if (ev.metaKey || ev.ctrlKey || ev.altKey) return
    const t = ev.target
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return
    if (modal && modal.classList.contains('on')) return
    const k = ev.key
    // Escape 는 **열려 있는 것 하나만** 닫는다 — 안 열려 있으면 먹지 않는다(조건 없이 preventDefault 하면 다음 것이 못 받는다).
    // 🔴 순서 = 맨 앞에 떠 있는 것부터(대장 132·134 · §17.26 A-05): ① 범위 팝오버 → ② 왼쪽 아래 「!」 상자 → ③ 정보창·범례·ⓘ →
    //    ④ 사용률 메뉴 → ⑤ 모니터링 팝오버 → ⑥ 비교 칸. 옛 결함(QA A-05): 화면을 덮은 목록이 열린 채 Esc 를 누르면 **뒤에 가려진 정보창**이 닫혔다.
    // 🔁 2026-10-20(대장 134 · A-05 개정): 범위 팝오버가 **맨 먼저**다 — 여러 계정을 고르는 동안 열려 있고, 고정층(z 40)이라 정보창·상자보다 앞에 떠 있다
    if (k === 'Escape' && scEl && scEl.classList.contains('on')) { toggleScopes(false); ev.preventDefault(); return }
    if (k === 'Escape' && S.listOn) { toggleList(false); ev.preventDefault(); return }
    if (k === 'Escape' && NT_AWS.closeAll()) { ev.preventDefault(); return }
    // 사용률(§17.25): 열린 기간·목록 메뉴 → 고른 줄 순으로 푼다. 먹은 게 없으면 다음 오버레이로
    if (k === 'Escape' && NT_AWS.utEsc()) { ev.preventDefault(); return }
    // §3g 범위 팝오버도 같은 세 경로로 닫힌다(B2) — `Esc` 는 위 맨 앞 줄이다.
    if (k === 'Escape' && monPopEl && monPopEl.classList.contains('on')) { toggleMonPop(false); ev.preventDefault(); return }
    // §3j J1: 비교 칸의 닫는 경로 셋(✕ · 「지금」 줄 · Esc). 칸 안에 초점이 있으면 칸이 부모에게 닫아 달라고 한다.
    if (k === 'Escape' && CMP) { parent.postMessage({ nettopo: 'cmp-close' }, '*'); ev.preventDefault(); return }
    if (k === 'Escape' && CMPV) { cmpClose(); renderDock(); ev.preventDefault(); return }
    if (k === '+' || k === '=') { zoomBy(1.25); ev.preventDefault() } else if (k === '-' || k === '_') { zoomBy(0.8); ev.preventDefault() } else if (k === '0') { resetView(); ev.preventDefault() } else if (k === 'f' || k === 'F') { fit(); ev.preventDefault() } else if (k === 'a' || k === 'A') { fitAll(); ev.preventDefault() }
  })

  // 누른 줄의 대상(§3j J3 · 2026-10-21) — 낱줄 = 그 대상 · 묶음 줄 = 항목 전부 · 펼친 이름 줄 = 그 항목. 줄이 그린 뒤 바뀌었으면(새 관측) 그 시각 전부
  function rowFocus (el) {
    const r = histRows()[Number(el.dataset.ri)]
    if (!r || r.kind === 'now' || r.at !== el.dataset.at) return null
    if (el.dataset.ii != null) { const id = (r.itemIds || [])[Number(el.dataset.ii)]; return id ? [id] : null }
    return r.targets.slice()
  }
  sideEl.addEventListener('click', (ev) => {
    if (ev.target.closest('[data-close]')) {
      closeDock(); setHash(null); render(L); return
    }
    const dt = ev.target.closest('[data-dtab]')
    if (dt) { openDock(dt.dataset.dtab); return }
    // 🔴 펼침 표시는 줄 **안에** 있다 — `[data-at]` 보다 먼저 봐야 펼치려던 클릭이 과거 고르기가 되지 않는다.
    const hx = ev.target.closest('[data-hexp]')
    if (hx) { toggleHistGroup(hx.dataset.hexp); return }
    const hr = ev.target.closest('[data-at]')
    // 「지금」 줄은 시각이 아니라 닫기다 — 같은 시각의 변경 줄과 헷갈리지 않게 표지로 구분한다(§3j J0)
    if (hr) { if (hr.hasAttribute('data-now')) pickTime(null); else pickTime(hr.dataset.at, rowFocus(hr)); return }
    if (ev.target.closest('[data-openlist]')) { ev.stopPropagation(); toggleList(true) }
  })

  document.getElementById('search').addEventListener('input', (ev) => {
    S.search = ev.target.value.trim()
    applyDim(L)   // 새 틀은 흐림만 바꾼다 — 다시 그리면 열어 둔 정보창·고른 길이 닫힌다(§17.21)
  })

  // ---- 햄버거 메뉴는 **없다**(사용자 결정 2026-10-20 · 대장 130 · QA A-06) ----
  // 「흐름 애니메이션」 토글은 기능째로 뺐다 — 움직임은 `S.anim`(= OS 「동작 줄이기」가 꺼져 있으면 켜짐 · 열 때 한 번)만 따른다.
  // 「전체 보기」는 오른쪽 아래 버튼 줄의 `data-act="fitall"` 이고 키 A 와 **같은 함수**(`fitAll`)를 지난다(CHROME_ACT).

  // ---- §3g 범위 축: 상단바 버튼 1개 + `#top` **밖** 체크박스 팝오버 (대장 93·134 · G1) ----
  // 🔴 목록을 `#top` 안에 두면 안 된다. 검사 68(`verify-flow.mjs`)이 `#top.textContent` 의 마스킹
  //    계정ID(`\d{4}…\d{4}`) 출현 수를 세어 0 을 요구하므로 `display:none` 이어도 FAIL 한다.
  //    선례: 옛 햄버거(2026-10-20 삭제) — **버튼은 `#top` 안**, **패널은 밖**. 그래서 버튼 라벨에도 계정 이름을
  //    쓰지 않는다 — 고른 계정은 **캔버스 배너**가 말한다(`#bans` 는 `#top` 밖이다).
  // 🔁 2026-10-20(사용자 결정 · 대장 134): ~~라디오(단일 선택)~~ → **체크박스(여러 계정)**. 계정 줄 = 그 계정만 켜고 끈다 ·
  //    팝오버는 열린 채 · 「전체」 = 다 지우고 닫는다 · 마지막을 끄면 전체(`scopeSet` 이 빈 집합을 `null` 로 접는다).
  //    되돌릴 수 있는 **이름 있는 범위**이므로 **확인 모달이 없다**(대장 49 의 필터와 구별 — 이것은 삭제가 아니다).
  const scEl = document.getElementById('scopes')
  const scBtn = document.getElementById('ax-scope')
  const syncScope = () => {
    if (scBtn) {
      // 계정 ID·이름을 붙이지 마라(위 🔴). 「몇 개」만 말하고 `.on` 으로 좁혀졌음을 표시한다.
      scBtn.textContent = SCOPE ? `계정: ${SCOPE.length}개` : '계정: 전체'
      scBtn.classList.toggle('on', !!SCOPE)
    }
    if (!scEl) return
    for (const it of scEl.querySelectorAll('.sitem')) {
      const id = it.dataset.scope || ''
      const on = id ? !!SCOPE && SCOPE_SET.has(id) : !SCOPE
      it.classList.toggle('on', on)
      it.setAttribute('aria-checked', String(on))
      const cb = it.querySelector('input'); if (cb) cb.checked = on
    }
  }
  // 목록은 **데이터**다(불변식 ③) — 계정 목록을 코드에 적지 않는다. `accounts` 는 모델에서 왔다.
  // 🔴 계정 줄은 `<label>` 이 아니라 `<div>` 다 — label 을 누르면 브라우저가 안의 체크박스에 클릭을 한 번 더 보내 **두 번 켜고 끈다**(= 안 바뀜).
  //    체크박스는 표시일 뿐이다(`pointer-events: none` · 값은 `syncScope` 가 상태에서 쓴다).
  if (scEl) {
    scEl.innerHTML = '<div class="sitem all" role="menuitemradio" data-scope="">전체</div><div class="msep"></div>' +
      accounts.map((a, i) => `<div class="sitem" role="menuitemcheckbox" data-scope="${esc(a.id)}">` +
        `<input type="checkbox" tabindex="-1" aria-hidden="true">${esc(acctLabel(i))}</div>`).join('')
  }
  const toggleScopes = (on) => {
    if (!scEl) return false
    const next = on === undefined ? !scEl.classList.contains('on') : !!on
    scEl.classList.toggle('on', next)
    if (next) syncScope()
    return next
  }
  // 사람이 하는 범위 변경의 **유일한 경로**다 — 상태·배치·해시·상단바를 한 번에 맞춘다.
  // 받는 것 = id 배열 | id 하나 | null(전체) — `scopeSet` 과 같다. 돌려주는 것 = `S.scope`(정렬한 id 를 쉼표로 · 없으면 null).
  // 🔴 `S.sel` 을 지우지 않는다: 고른 카드가 범위 밖이면 배치에서 사라지고 패널이 빈다. 그래서
  //    범위 밖으로 나간 선택만 접는다(선택은 있는데 그 카드가 없는 상태를 만들지 않는다).
  function scopeApply (ids) {
    const before = S.scope
    const now = scopeSet(ids)
    if (now !== before) {
      // 🔴 `mById.has` 로 **모델 노드만** 판정한다 — 틀(리전 등) 키는 모델 노드가 아니라 `inScope` 가
      //    항상 false 다. 그것까지 지우면 리전 헤더를 고른 사람이 범위를 바꿀 때마다 패널이 닫힌다.
      relayout()
      fit()   // §17.22 — 새 틀은 계정 상자 크기가 바뀐다
      renderDock()
      setHash(S.sel)
    }
    syncScope()
    return now
  }
  // 계정 줄 하나 = 그 계정만 켜고 끈다(나머지는 그대로 · 대장 134). 빈 집합은 `scopeSet` 이 전체로 접는다.
  function scopeToggle (id) {
    const cur = SCOPE ? SCOPE.slice() : []
    const i = cur.indexOf(id)
    if (i >= 0) cur.splice(i, 1); else cur.push(id)
    return scopeApply(cur)
  }
  if (scBtn) scBtn.onclick = (ev) => { ev.stopPropagation(); toggleScopes() }
  if (scEl) {
    scEl.addEventListener('click', (ev) => {
      const it = ev.target.closest('.sitem')
      if (!it) return
      const id = it.dataset.scope || ''
      // 「전체」만 닫는다 — 계정 줄은 여러 개를 고르는 중이라 열어 둔다
      if (!id) { scopeApply(null); toggleScopes(false) } else scopeToggle(id)
    })
  }
  // 닫는 경로 셋(B2): 다시 클릭 · 밖 클릭 · Escape.
  document.addEventListener('click', (ev) => {
    if (!scEl || !scEl.classList.contains('on')) return
    if (ev.target.closest('#scopes') || ev.target.closest('#ax-scope')) return
    toggleScopes(false)
  })

  // ---------------- 토글 (§13-45·48·50·51) ----------------
  // 제품 버튼과 하네스 API 가 **같은 함수**를 지난다 — 하네스가 자기 전용 경로를 타면
  // 재는 대상이 제품이 아니라 하네스 자신이 된다.
  // 🔴 `monitor` 는 **`render`** 다 — 상태는 그림에 «덧붙이는» 것이고 치수를 바꾸지 않는다.
  //    `relayout` 로 두면 켜고 끌 때마다 배치가 다시 돌아 접힘·선택이 흔들릴 수 있고, 무엇보다
  //    「구조는 그대로」라는 이 기능의 약속이 코드에서 깨진다.
  const TOGGLE = { monitor: ['btn-mon', 'render'] }
  const syncToggles = () => {
    for (const k in TOGGLE) {
      const el = document.getElementById(TOGGLE[k][0])
      if (el) el.classList.toggle('on', !!S[k])
    }
  }
  function setFlag (k, on) {
    S[k] = !!on
    if (k === 'util' && !on) S.utPick = null
    syncToggles()
    render(L)
    return L
  }
  for (const k in TOGGLE) {
    const el = document.getElementById(TOGGLE[k][0])
    if (el) el.onclick = () => (k === 'monitor' ? monClick() : setFlag(k, !S[k]))
  }
  syncToggles()

  // ---------------- 실시간 채널 (S3 · ADR 0003 D2 · ADR 0005 · ADR 0006 O6) ----------------
  // 브라우저가 하는 일은 셋뿐이다: 제어 키 **쓰기**(켬·끔·바꿈·연장) · 상태 문서 **읽기**(1분마다 · 켠 층을 기다리는 동안 10초마다) ·
  // 🆕 제어 키 **읽기**(1분마다 — 따라 보기 · 사용자 결정 2026-10-28 · ADR 0005 D4 보강 4). 불변식 ⑥ 의 예외 경로이고(같은 버킷 오리진 하나),
  // 주소는 위 `LIVE_SLOT` 이 준 서명 URL 뿐이다(다른 곳으로 요청하지 않는다).
  // 🔴 읽기가 실패하면 **얹은 상태를 지운다**(O6) — 마지막 상태를 남기면 화면이 「지금」을 주장하는데
  //    그 시각이 거짓이 된다. 구조(카드·선)는 그대로 선다(불변식 ④).
  // 🔴 제품 버튼·타이머·하네스 API 가 **같은 세 함수**(`liveOn`·`livePoll`·`liveOff`)를 지난다.
  const LIVE_POLL_MS = 60 * 1000          // 상태 함수는 5분마다 쓴다 — 1분 확인이면 늦어도 1분 안에 얹힌다
  // 「누르면 바로 깨우기」(2026-10-06): 제어 키를 쓰면 상태 함수가 그 자리에서 돈다(S3 이벤트 · 스택 A `StateWakeRule`).
  // 그 관측이 언제 끝날지 화면은 모른다(사용률을 켠 첫 실행 = 실측 173.8 초) → 켠 층을 기다리는 동안만 빨리 읽는다.
  // 1분 읽기면 다 된 관측을 최대 1분 늦게 얹는다.
  const LIVE_WAIT_POLL_MS = 10 * 1000
  // 빠른 읽기는 마지막 켬·층 변경 뒤 이만큼까지만(읽기 수 상한 = 48회). 8분 = 깨운 실행이 그 순간 돌던 5분 주기 실행 뒤에
  // 줄 선 최악(동시성 1 · 이벤트 수명 240 초) + 꺼져 있다 켠 첫 실행(≈3분). 깨우기가 없는 옛 스택도 다음 주기(0~5분) +
  // 첫 실행이 대개 이 안이다. 넘으면 1분 읽기로 돌아간다 — 그 뒤는 `liveJudge` 의 15분 「멈췄다」 판정이 맡는다.
  const LIVE_WAIT_FOR_MS = 8 * 60 * 1000
  // ADR 0005 D4 보강: 켤 때 **15분 임대**를 쓰고 창이 열려 있는 동안 5분마다 연장한다. 창을 닫거나 잠들면
  // 연장이 끊겨 약 20분(임대 15분 + 상태 함수 주기 5분) 안에 서버가 멈춘다. 상한은 켠 시각 + 4시간이다
  // (서버 천장은 마지막 PUT + 8시간 그대로 — 조작된 화면에 대한 천장이다).
  // 🔁 2026-10-28(사용자 결정): 상한 = 켠 시각 + **고른 시간**(`LIVE.dur` · 30분~8시간 · 기본 4시간) — 아래 `liveEnd` 한 곳.
  const LIVE_LEASE_MS = 15 * 60 * 1000
  const LIVE_RENEW_MS = 5 * 60 * 1000
  // 켜는 판의 끝 = 고른 시간 · 이 화면 파일의 주소 만료 중 이른 것. 켜기·따라 보던 창이 바꾸기(새 주인)·팝오버 머리줄이 같이 쓴다.
  const liveEnd = (now) => {
    const byCap = now + LIVE.dur <= LIVE_SLOT.urlUntil
    return { until: byCap ? now + LIVE.dur : LIVE_SLOT.urlUntil, by: byCap ? 'ttl' : 'url', cap: LIVE.dur }
  }
  // 1분 제어 읽기의 여유 — 1분 타이머가 응답 시간만큼 일찍 깨도 그 회차에 읽는다(10초 빠른 읽기 중에는 여섯 번째마다).
  const LIVE_CTL_SLACK_MS = 2 * 1000
  // 켜기 **전에** 관측된 문서는 얹지 않는다 — 꺼져 있던 동안 저장소에 남은 마지막 문서(며칠 전일 수
  // 있다)를 「지금」으로 그리게 된다. 6분 = 5분 주기 한 번 + 여유(누가 먼저 켜 둔 경우의 최신 문서는 받는다).
  const LIVE_FRESH_MS = 6 * 60 * 1000
  // 이만큼 새 관측이 없으면 「멈췄다」로 보고 얹은 것을 지운다(5분 주기 세 번을 놓쳤다).
  const LIVE_STALE_MS = 15 * 60 * 1000
  // 🔵 함수 선언이다(호이스팅) — `dump()` 가 이 블록보다 먼저 불려도 죽지 않는다.
  function liveInfo () {
    return { slot: !!LIVE_SLOT, unsigned: UNSIGNED_PAGE, urlUntil: LIVE_SLOT ? msIso(LIVE_SLOT.urlUntil) : null,
      on: LIVE.on, busy: LIVE.busy, layer2: LIVE.layer2, util: LIVE.util, setWhy: LIVE.setWhy, phase: LIVE.phase, why: LIVE.why, note: LIVE.note,
      since: LIVE.since ? msIso(LIVE.since) : null, until: LIVE.until ? msIso(LIVE.until) : null,
      untilBy: LIVE.untilBy, etag: LIVE.etag, reads: LIVE.reads, writes: LIVE.writes,
      mode: LIVE.mode, lease: LIVE.lease ? msIso(LIVE.lease) : null, ctlEtag: LIVE.ctlEtag, renewWhy: LIVE.renewWhy,
      docAt: LIVE.docAt ? msIso(LIVE.docAt) : null, lastCheck: LIVE.lastCheck ? msIso(LIVE.lastCheck) : null,
      asked: LIVE.asked ? msIso(LIVE.asked) : null, fast: LIVE.on ? liveWaiting(Date.now()) : false,
      monitor: S.monitor, utilShown: S.util, latest: canGoLatest(), pairWait: LIVE.pairWait,
      ctlGet: !!(LIVE_SLOT && LIVE_SLOT.ctlGetUrl), ctlReads: LIVE.ctlReads, ctlWhy: LIVE.ctlWhy, ctlSeen: LIVE.ctlSeen,
      ctlAt: LIVE.ctlAt ? msIso(LIVE.ctlAt) : null, end: LIVE.end ? msIso(LIVE.end) : null,
      dur: LIVE.dur, cap: LIVE.cap, follow: LIVE.mode === 'follow' }
  }
  // 🆕 켜는 시간 고르기(사용자 결정 2026-10-28) — 팝오버의 다섯 칸. 🔴 8시간이 끝이다: 서버 천장(`handler.MAX_TTL` = 마지막 쓰기 + 8시간)과
  //    같은 값이라 옛 저장소(조건부 연장 불가 · 상한을 한 번에 쓴다)에서도 고른 시간이 그대로 지켜진다.
  const LIVE_DURS = [30 * 60e3, 3600e3, 2 * 3600e3, 4 * 3600e3, 8 * 3600e3]
  // 사람 말 — 30분 · 1시간 · 2시간 … (함수 선언 — 배너가 첫 렌더에서 부른다)
  function durWord (ms) { return ms < 3600e3 ? `${Math.round(ms / 60e3)}분` : `${Math.round(ms / 3600e3 * 10) / 10}시간` }
  // 실시간을 끄는 버튼 — 켜 둔 것만 말한다(둘 다 켰으면 둘 다 꺼야 꺼진다)
  const liveBtns = () => (S.monitor && S.util ? '«모니터링»·«사용률» 둘 다' : S.util ? '«사용률»' : '«모니터링»')
  // 🔴 제어 문서를 **통째로** 쓴다. 핸들러가 읽는 것은 `live`·`layer2`·`utilization`·`expiresAt` 넷뿐이라
  //    (`infra/state-lambda/handler.py` 의 `decide`) 지금은 잃는 것이 없다(`monitor`·`until` 은 화면만 읽는다 — 따라 보기).
  //    ADR 0005 의 `scope`·`accounts.extra` 를 핸들러가 읽게 되는 날 이 함수는 읽고-고쳐-쓰기로 바뀌어야 한다(제어 키 GET
  //    주소는 2026-10-28 부터 칸에 있다 — `LIVE_SLOT.ctlGetUrl`).
  // `ifMatch` = 연장 PUT 이 싣는 **내가 마지막으로 쓴 ETag**. 412(판이 바뀌었다)·409(동시에 쓰는 중) = 다른 창이
  // 그 사이 썼다 → `conflict`. fetch 예외 = `blocked` — 네트워크 끊김과 사전 요청 거절을 화면은 가리지 못한다.
  // 🆕 보낸 본문의 지문을 `LIVE.mine` 에 **보내기 전에** 적는다(보강 1 — 응답이 끊겨도 「내가 보낸 것」은 남는다). 성공하면 그 하나만 남긴다.
  async function liveWrite (body, ifMatch) {
    LIVE.writes++
    const headers = { 'content-type': 'application/json' }
    if (ifMatch) headers['if-match'] = ifMatch
    const key = ctlKey(body)
    LIVE.mine = LIVE.mine.filter((k) => k !== key).concat([key]).slice(-8)
    LIVE.wseq++; LIVE.wIn++
    try {
      const r = await fetch(LIVE_SLOT.controlUrl, { method: 'PUT', cache: 'no-store',
        headers, body: JSON.stringify(body) })
      if (r.ok) {
        LIVE.mine = [key]
        const etag = r.headers.get('etag')
        LIVE.ctlSeen = etag || null
        return { ok: true, etag }
      }
      if (ifMatch && (r.status === 412 || r.status === 409)) {
        return { ok: false, conflict: true, why: `다른 창에서 먼저 변경했습니다(${r.status})` }
      }
      if (r.status === 403) {
        return { ok: false, why: Date.now() >= LIVE_SLOT.urlUntil
          ? '주소 서명이 만료됐습니다' : '저장소가 쓰기를 거부했습니다(403)' }
      }
      return { ok: false, why: `저장소 응답 ${r.status}` }
    } catch (e) {
      return { ok: false, blocked: true, why: '요청이 차단됐습니다(네트워크 또는 CORS)' }
    } finally {
      LIVE.wIn--; LIVE.wseq++
    }
  }
  // 제어 문서의 지문 — 보강 1 의 「내 쓰기인가」 비교 재료. 핸들러가 읽는 넷(`live`·`layer2`·`utilization`·`expiresAt`) + 화면만 읽는
  // 둘(`monitor` = 모니터링을 보이나 · `until` = 켠 창이 고른 끝 시각 · 핸들러는 읽지 않는다 — `handler.decide`). 없는 값은 null.
  function ctlKey (d) {
    const v = (x) => (x === undefined ? null : x)
    return JSON.stringify([v(d.live), v(d.layer2), v(d.utilization), v(d.monitor), v(d.expiresAt), v(d.until)])
  }
  // 🆕 제어 키 읽기(따라 보기 · 보강 4) — 결과 = 'same'(내가 마지막으로 쓴 판 · 또는 마지막으로 본 판 그대로) | 'mine'(판은 바뀌었지만
  //    이 창이 보낸 본문이다 = 응답만 끊긴 내 쓰기) | 'other'(다른 창의 판) | 'fail'(못 읽음 — 화면은 그대로) | 'stale'(읽는 사이 이 창이
  //    썼다 — 결과를 버린다). `fresh` = 조건 없이 읽는다(412 뒤 — 「무엇이 바뀌었나」를 봐야 한다).
  async function liveCtlRead (fresh) {
    LIVE.ctlReads++
    LIVE.ctlAt = Date.now()
    const wseq = LIVE.wseq
    let r = null
    try {
      r = await fetch(LIVE_SLOT.ctlGetUrl, { cache: 'no-store',
        headers: !fresh && LIVE.ctlSeen ? { 'if-none-match': LIVE.ctlSeen } : {} })
    } catch (e) {
      return { kind: 'fail', why: '요청이 차단됐습니다(네트워크 또는 CORS)' }
    }
    if (wseq !== LIVE.wseq || LIVE.wIn) return { kind: 'stale' }
    if (r.status === 304) return { kind: 'same' }
    if (r.status === 403) {
      return { kind: 'fail', why: Date.now() >= LIVE_SLOT.urlUntil ? '주소 서명이 만료됐습니다' : '저장소가 읽기를 거부했습니다(403)' }
    }
    if (!r.ok) return { kind: 'fail', why: `저장소 응답 ${r.status}` }
    let doc = null
    try { doc = JSON.parse(await r.text()) } catch (e) { doc = null }
    if (wseq !== LIVE.wseq || LIVE.wIn) return { kind: 'stale' }
    if (!doc || typeof doc !== 'object' || Array.isArray(doc)) return { kind: 'fail', why: '설정 문서의 형식이 올바르지 않습니다' }
    const etag = r.headers.get('etag')
    if (etag && LIVE.ctlEtag && etag === LIVE.ctlEtag) return { kind: 'same', doc, etag }
    // 🔵 따라 보는 창의 `mine` 은 비어 있다(`liveFollow` 가 비운다) — 못 읽어 물러난 창만 자기 쓰기를 여기서 되찾는다.
    if (LIVE.mine.includes(ctlKey(doc))) return { kind: 'mine', doc, etag }
    if (LIVE.mode === 'follow' && etag && etag === LIVE.ctlSeen) return { kind: 'same', doc, etag }
    return { kind: 'other', doc, etag }
  }
  // 읽은 판이 내 쓰기다 — 그 ETag 를 이어받아 주인으로 계속한다(보강 1 · 거짓 「다른 창」을 없앤다).
  function liveAdopt (c) {
    // 못 읽어 따라 보기로 물러났던 창이면 주인으로 돌아간다 — 연장을 다시 건다(판은 내 것이었다)
    if (LIVE.mode === 'follow') { LIVE.mode = 'lease'; LIVE.ctlWhy = ''; liveRenewAt(LIVE_POLL_MS) }
    if (c.etag) { LIVE.ctlEtag = c.etag; LIVE.ctlSeen = c.etag }
    if (c.doc) LIVE.mine = [ctlKey(c.doc)]
    const exp = c.doc ? Date.parse(c.doc.expiresAt) : NaN
    if (LIVE.mode === 'lease' && Number.isFinite(exp)) LIVE.lease = Math.max(LIVE.lease, exp)
  }
  // 🆕 다른 창의 판을 받았다 — 꺼짐이면 끄고(보강 4 C), 켜짐이면 그 설정 그대로 **읽기만** 하며 따라 본다(B). 🔴 쓰지 않는다.
  function liveCtlApply (c) {
    LIVE.ctlWhy = ''
    if (c.etag) LIVE.ctlSeen = c.etag
    if (!c.doc || c.doc.live !== true) { liveOff('remote'); return 'off' }
    return liveFollow(c.doc)
  }
  function liveFollow (doc) {
    const now = Date.now()
    const exp = Date.parse(doc.expiresAt)
    // 만료가 없거나 이미 지났다 = 서버가 받지 않는 판이다(`handler.decide`) — 켜진 척 따라가지 않는다.
    if (!Number.isFinite(exp) || exp <= now) { liveOff('stopped'); return 'off' }
    const layer2 = doc.layer2 === true
    // 핸들러와 같은 읽기 — `utilization` 이 없으면 옛 화면 파일의 판이다(2층을 따른다)
    const util = doc.utilization === undefined || doc.utilization === null ? layer2 : doc.utilization === true
    // `monitor` 는 화면만 읽는 칸이다 — 없으면(옛 화면 파일) 사용률만 켠 판(지표 없음 · 사용률 켬)이 아닌 한 모니터링이다.
    const monitor = typeof doc.monitor === 'boolean' ? doc.monitor || !util : layer2 || !util
    const end = Date.parse(doc.until)
    const changed = layer2 !== LIVE.layer2 || util !== LIVE.util || monitor !== S.monitor
    clearTimeout(LIVE.renewT)
    // 이 창의 상한 = 그 창이 고른 끝 · 이 화면 파일의 주소 만료 중 이른 것. 끝을 모르는 옛 판이면 주소 만료만 — 멈춤은 임대(`lease`)가 말한다.
    const byUrl = !Number.isFinite(end) || LIVE_SLOT.urlUntil <= end
    Object.assign(LIVE, { mode: 'follow', lease: exp, ctlEtag: null, mine: [], renewWhy: '', renewT: null, setWhy: '', ctlWhy: '',
      layer2, util, end: Number.isFinite(end) ? end : 0, until: byUrl ? LIVE_SLOT.urlUntil : end, untilBy: byUrl ? 'url' : 'end' })
    clearTimeout(LIVE.offT)
    LIVE.offT = LIVE.on ? setTimeout(() => { liveOff(LIVE.untilBy) }, Math.max(0, LIVE.until - now)) : null
    if (changed) {
      // 새 층을 기다린다 — 켠 창이 쓴 그 쓰기가 상태 함수를 깨웠다(빠른 읽기 8분 창 · 진행 표시도 같은 시각부터)
      LIVE.asked = now; LIVE.doneWhat = ''; LIVE.doneReads = -1
      S.monitor = monitor; S.util = util
      syncToggles()
      liveRender()
    } else {
      syncChrome(L)
    }
    return 'follow'
  }
  // 🆕 412·409 = 판이 바뀌었다(보강 1). 🔴 누가 바꿨는지 **읽어서** 정한다 — 응답만 끊긴 내 쓰기면 그 판을 이어받아 다시 하고('mine'),
  //    다른 창이면 따라 보거나 끈다('handled'), 판이 아직 내 것이면(409 = 누가 쓰는 중) 잠시 뒤 다시 한다('busy'). 못 읽으면 덮지 않고
  //    따라 보기로 물러난다(쓰기 0 — 다음 1분 읽기가 판정한다 · 'handled'). 읽을 주소가 없는 화면(옛 서명 함수) = 옛 동작(이 창만 끈다).
  async function liveConflict () {
    if (!LIVE_SLOT.ctlGetUrl) { liveOff('conflict'); return 'handled' }
    const seq = LIVE.seq
    const c = await liveCtlRead(true)
    if (!LIVE.on || seq !== LIVE.seq) return 'handled'
    if (c.kind === 'mine') { liveAdopt(c); return 'mine' }
    if (c.kind === 'same' || c.kind === 'stale') return 'busy'
    if (c.kind === 'other') { liveCtlApply(c); return 'handled' }
    clearTimeout(LIVE.renewT)
    Object.assign(LIVE, { mode: 'follow', renewT: null, renewWhy: '', ctlWhy: c.why })
    syncChrome(L)
    return 'handled'
  }
  // 🆕 1분마다 제어 키를 읽는다(보강 4 G) — 주인 창도 따라 보는 창도. 다른 창이 바꾼 것을 5분 연장을 기다리지 않고 1분 안에 안다.
  //    🔴 읽기 실패는 화면을 바꾸지 않는다(불변식 ④) — 경고 꼬리 1개(`ctlWhy`)만 단다. 상태 문서의 10초 빠른 읽기와 무관하다.
  async function liveCtlCheck () {
    if (!LIVE.on || !LIVE_SLOT.ctlGetUrl || LIVE.wIn || LIVE.busy) return null
    const seq = LIVE.seq
    const c = await liveCtlRead(false)
    if (!LIVE.on || seq !== LIVE.seq) return null
    if (c.kind === 'stale') return null
    if (c.kind === 'fail') { LIVE.ctlWhy = c.why; return null }
    LIVE.ctlWhy = ''
    if (c.kind === 'same') return null
    if (c.kind === 'mine') { liveAdopt(c); return null }
    return liveCtlApply(c)
  }
  // 상태가 바뀌면 그림을 다시 얹는다. 🔴 비교 칸이 열려 있는데 새 문서에 그 시각이 없으면(또는 짝이 어긋나면)
  // 칸을 닫는다 — 그대로 두면 오른쪽은 목록에서 사라진 시각을 「그때」라고 보여 준다. 열린 칸은 다시 되짚지
  // 않는다: t 뒤에 새 변경이 와도 「t 직후」는 그대로다(새 변경의 `from` = 이미 그린 값 · 새 생김은 원래 뺐다).
  function liveRender () {
    if (CMPV && (OBS_STALE || !histRows().some((x) => x.at === CMPV.at))) cmpClose()
    render(L)
    renderDock()
  }
  // 🔴 문서를 얹을지 판정하는 곳은 **여기 하나**다(200 과 304 가 같은 판정을 지난다).
  function liveJudge (now) {
    const doc = LIVE.doc
    if (!doc) {
      return now - LIVE.since > LIVE_STALE_MS
        ? { phase: 'stale', why: `켠 지 ${Math.round((now - LIVE.since) / 60000)}분째 첫 관측이 오지 않습니다` }
        : { phase: 'wait', why: '첫 관측을 기다리는 중 — 켜는 쓰기가 상태 함수를 바로 깨운다(옛 스택이면 다음 5분 주기)' }
    }
    if (LIVE.docAt < LIVE.since - LIVE_FRESH_MS) {
      const old = `저장소의 마지막 문서는 ${msHm(LIVE.docAt)} 것이라 표시하지 않았습니다(켜기 전의 관측입니다)`
      return now - LIVE.since > LIVE_STALE_MS
        ? { phase: 'stale', why: `켠 지 ${Math.round((now - LIVE.since) / 60000)}분째 새 관측이 오지 않습니다 — ${old}` }
        : { phase: 'wait', why: `첫 관측을 기다리는 중 — ${old}` }
    }
    if (now - LIVE.docAt > LIVE_STALE_MS) {
      return { phase: 'stale', why: `마지막 관측 ${msHm(LIVE.docAt)} 뒤로 ` +
        `${Math.round((now - LIVE.docAt) / 60000)}분째 새 관측이 오지 않습니다` }
    }
    // 🆕 짝의 **방향**(QA B P3 · 사용자 결정 2026-10-14). 짝 판정 자체는 그대로 `obsPairUp` 한 곳이다 — 여기서는 사람에게
    //    무엇을 말할지(기다림 / 최신 화면)만 고른다. 방향을 모르면(시각 판정 · 같은 시각) 옛 동작(`ok` + 짝 안 맞음 배너).
    const pp = pairOf(doc)
    if (pp.stale && pp.newer === 'page') {
      const w = `이 구성도(${kstAt(T.run.collected_at)} 구조)가 상태 문서(${kstAt(doc.run.topology_collected_at)} 구조)보다 최신입니다 — ` +
        '새 구성으로 다시 측정하는 중입니다'
      return now - LIVE.since > LIVE_STALE_MS
        ? { phase: 'stale', why: `켠 지 ${Math.round((now - LIVE.since) / 60000)}분째 새 구성의 관측이 오지 않습니다 — ${w}` }
        : { phase: 'wait', why: w, pairWait: true }
    }
    if (pp.stale && pp.newer === 'obs') {
      return { phase: 'moved', why: `상태 문서는 ${kstAt(doc.run.topology_collected_at)} 구조를, 이 그림은 ${kstAt(T.run.collected_at)} 구조를 보고 있다` }
    }
    return { phase: 'ok', why: '' }
  }
  // 지금 문서의 층이 켜 달라고 쓴 층과 다르다 = 깨운 관측이 오는 중이다. 서버의 `wake_is_redundant`
  // (`infra/state-lambda/handler.py`)와 같은 비교다 — 지표·사용률 두 층만(흐름 로그는 스택 파라미터라 화면이 모른다).
  // 🔵 끈 쪽(문서엔 아직 있다)도 「다르다」다 — 그 층을 뺀 관측도 서버가 바로 낸다.
  // 켜 달라고 했는데 지금 문서에 아직 없는 층(사람 말) — 진행 표시와 「새 관측을 만드는 중」이 같이 쓴다.
  function livePendWords () {
    if (!LIVE.on || !LIVE.doc) return []
    const ly = LIVE.doc.layers || {}
    return [S.monitor && LIVE.layer2 && ly.metric !== true ? '모니터링 지표' : '', LIVE.util && ly.utilization !== true ? '사용률' : ''].filter(Boolean)
  }
  function liveLayersPending () {
    const ly = (LIVE.doc && LIVE.doc.layers) || {}
    return (ly.metric === true) !== !!LIVE.layer2 || (ly.utilization === true) !== !!LIVE.util
  }
  // 🔵 함수 선언이다 — 배너(`liveRows`, 위쪽)가 개발 설명에서 부른다
  function liveWaiting (now) {
    return LIVE.on && now - LIVE.asked < LIVE_WAIT_FOR_MS &&
      (LIVE.phase === 'wait' || (LIVE.phase === 'ok' && liveLayersPending()))
  }
  const liveSchedule = () => {
    clearTimeout(LIVE.pollT)
    LIVE.pollT = LIVE.on
      ? setTimeout(() => { livePoll() }, liveWaiting(Date.now()) ? LIVE_WAIT_POLL_MS : LIVE_POLL_MS) : null
  }
  async function livePoll () {
    if (!LIVE.on) return liveInfo()
    clearTimeout(LIVE.pollT); LIVE.pollT = null
    if (liveLapsed(Date.now())) return liveInfo()
    // 🆕 1분마다 제어 키(보강 4 G) — 상태 읽기보다 **먼저**: 다른 창이 껐으면 여기서 끝나고, 바꿨으면 새 층으로 상태를 읽는다.
    //    🔴 빠른 읽기(10초)는 상태 문서만이다 — 제어 키는 그 사이에도 1분에 한 번이다.
    if (LIVE_SLOT.ctlGetUrl && Date.now() - LIVE.ctlAt >= LIVE_POLL_MS - LIVE_CTL_SLACK_MS) {
      await liveCtlCheck()
      if (!LIVE.on) return liveInfo()
    }
    const seq = LIVE.seq
    const before = LIVE.phase + '|' + LIVE.etag
    // 진행 표시(사용자 결정 2026-10-28) — 이 읽기 전에 무엇을 기다리고 있었나(첫 관측 · 막 켠 층)
    const waited = LIVE.phase === 'wait' && !LIVE.pairWait ? '첫 관측' : LIVE.phase === 'ok' ? livePendWords().join('·') : ''
    let fail = null
    let res = null
    LIVE.reads++
    try {
      res = await fetch(LIVE_SLOT.stateUrl, { cache: 'no-store',
        headers: LIVE.etag ? { 'if-none-match': LIVE.etag } : {} })
    } catch (e) {
      fail = '요청이 차단됐습니다(네트워크 또는 CORS)'
    }
    // 끄거나 다시 켠 사이에 온 응답은 버린다 — 꺼진 화면에 상태를 다시 얹으면 안 된다.
    if (!LIVE.on || seq !== LIVE.seq) return liveInfo()
    if (res && res.status === 304) {
      // 같은 문서다. 판정은 시간이 흘렀으므로 다시 한다(아래 `liveJudge`).
    } else if (res && res.status === 404) {
      LIVE.doc = null; LIVE.docAt = 0; LIVE.etag = null
    } else if (res && res.status === 403) {
      fail = Date.now() >= LIVE_SLOT.urlUntil ? '주소 서명이 만료됐습니다 — 최신 index.html 을 다시 받아 주세요'
        : '저장소가 읽기를 거부했습니다(403)'
    } else if (res && !res.ok) {
      fail = `저장소 응답 ${res.status}`
    } else if (res) {
      let doc = null
      try { doc = JSON.parse(await res.text()) } catch (e) { doc = null }
      if (!LIVE.on || seq !== LIVE.seq) return liveInfo()
      const at = doc && doc.run ? Date.parse(doc.run.observed_at) : NaN
      if (!doc || !Array.isArray(doc.observations) || !Number.isFinite(at)) {
        fail = '상태 문서의 형식이 올바르지 않습니다(관측 시각·관측 목록이 없습니다)'
      } else {
        LIVE.doc = doc; LIVE.docAt = at; LIVE.etag = res.headers.get('etag')
      }
    }
    const now = Date.now()
    LIVE.lastCheck = now
    const j = fail ? { phase: 'fail', why: fail } : liveJudge(now)
    if (fail) { LIVE.doc = null; LIVE.docAt = 0; LIVE.etag = null }
    LIVE.phase = j.phase
    LIVE.why = j.why
    LIVE.pairWait = !!j.pairWait
    if (waited && j.phase === 'ok' && !livePendWords().length) { LIVE.doneWhat = waited; LIVE.doneReads = LIVE.reads }
    // 「구조 바뀜」은 문서를 **앉힌다** — 짝 판정(`obsPairUp`)이 겹쳐 그리기를 거부하고 배너·이력이 그 사실을 말한다.
    const want = j.phase === 'ok' || j.phase === 'moved' ? LIVE.doc : null
    // 문서도 판정도 그대로면 그림을 다시 그리지 않는다(배너의 「마지막 확인」만 바뀐다).
    if (before !== LIVE.phase + '|' + LIVE.etag || OBS !== want) {
      obsAdopt(want)
      liveRender()
    } else {
      syncChrome(L)
    }
    liveSchedule()
    return liveInfo()
  }
  // 🔴 늦게 깬 타이머(노트북 잠자기·탭 멈춤)를 여기서 가린다 — 셋(읽기·연장·상한) 중 무엇이 먼저 깨도 같은 판정이다.
  //    상한이 지났으면 상한으로, 임대가 지났으면 「자는 동안 꺼졌다」로 끈다. **다시 켜지 않는다**(쓰기 0) —
  //    사람이 모르는 새 요금이 다시 시작되면 안 된다(ADR 0005 D4 보강).
  function liveLapsed (now) {
    if (now >= LIVE.until) { liveOff(LIVE.untilBy); return true }
    if (LIVE.mode === 'lease' && now >= LIVE.lease) { liveOff(LIVE.renewWhy ? 'renew' : 'slept'); return true }
    // 🆕 따라 보는 판의 임대가 지났다 = 켠 창이 연장하지 않았다(닫았거나 잠들었다 · 보강 4 E) — 서버는 거기서 멈춘다.
    if (LIVE.mode === 'follow' && now >= LIVE.lease) { liveOff('stopped'); return true }
    return false
  }
  const liveRenewAt = (ms) => {
    clearTimeout(LIVE.renewT)
    LIVE.renewT = LIVE.on && LIVE.mode === 'lease' ? setTimeout(() => { liveRenew() }, ms) : null
  }
  // 임대 연장. 🔴 조건부(`If-Match`)로만 연장한다 — 다른 창이 그 사이 켜거나 껐으면 **덮지 않는다**.
  //    ETag 를 못 읽은 저장소만 **옛 동작**으로 떨어진다: 조건 없이 상한 시각을 한 번 쓰고 그 사실을 말한다.
  //    🔴 조건부 연장이 **막힌 것**(`blocked`)은 옛 동작의 근거가 아니다 — 주소 칸(서명 함수)과 CORS `if-match`·
  //    `ETag` 노출은 같은 템플릿에 함께 들어왔다(ADR 0005 D4 보강). 막힘 = 와이파이 끊김 같은 네트워크 사건이라
  //    다른 실패와 같이 경고하고 1분마다 다시 해 보며, 임대가 끝나면 끈다. 한 번의 끊김이 조건 없는 4시간 쓰기로
  //    바뀌면 창을 닫아도 서버가 4시간 돌고, 그 사이 다른 창이 끈 것까지 다시 켠다(#32 리뷰 1, 2026-09-28).
  //    한계: 쓰기가 닿고 응답만 끊겼다면 다음 조건부 연장이 자기 쓰기와 부딪혀(412) 「다른 창에서 바꿨다」로 끈다 —
  //    잘못 말하지만 요금을 멈추는 쪽이다.
  //    🆕 그 한계를 보강 1(2026-10-28)이 닫았다: 412·409 면 제어 키를 읽어 **내가 보낸 본문**이면 그 판을 이어받고 다시 연장한다.
  //    🔴 따라 보는 창(`mode` = 'follow')은 연장하지 않는다(보강 4 (가)) — 연장은 마지막에 켜거나 바꾼 창 하나만 한다.
  async function liveRenew (again) {
    if (!LIVE.on || LIVE.mode !== 'lease') return liveInfo()
    clearTimeout(LIVE.renewT); LIVE.renewT = null
    const now = Date.now()
    if (liveLapsed(now)) return liveInfo()
    const seq = LIVE.seq
    const body = { live: true, layer2: LIVE.layer2, utilization: LIVE.util }
    Object.assign(body, { monitor: !!S.monitor, until: msIso(LIVE.until) })
    const lease = Math.min(now + LIVE_LEASE_MS, LIVE.until)
    let w = LIVE.ctlEtag ? await liveWrite({ ...body, expiresAt: msIso(lease) }, LIVE.ctlEtag) : null
    if (!LIVE.on || seq !== LIVE.seq) return liveInfo()
    if (w && w.conflict) {
      const k = await liveConflict()
      if (!LIVE.on || seq !== LIVE.seq || k === 'handled') return liveInfo()
      if (k === 'mine' && !again) return liveRenew(true)
      w = { ok: false, why: w.why }
    }
    if (w && w.ok && w.etag) {
      Object.assign(LIVE, { ctlEtag: w.etag, lease, renewWhy: '' })
      liveRenewAt(LIVE_RENEW_MS)
      syncChrome(L)
      return liveInfo()
    }
    if (!w || w.ok) {
      const f = await liveWrite({ ...body, expiresAt: msIso(LIVE.until) })
      if (!LIVE.on || seq !== LIVE.seq) return liveInfo()
      if (f.ok) {
        Object.assign(LIVE, { mode: 'fixed', lease: LIVE.until, ctlEtag: null, renewWhy: '' })
        syncChrome(L)
        return liveInfo()
      }
      w = f
    }
    LIVE.renewWhy = w.why
    liveRenewAt(LIVE_POLL_MS)
    syncChrome(L)
    return liveInfo()
  }
  // `layer2` = 모니터링의 지표 층 · `opt` = 무엇을 켜나(기본 = 모니터링만 · 「사용률」 버튼은 `{ monitor: false, util: true }`)
  async function liveOn (layer2, opt = { monitor: true, util: false }) {
    if (!LIVE_SLOT || LIVE.on || LIVE.busy) return liveInfo()
    const now = Date.now()
    if (liveUrlDead(now)) {
      LIVE.note = '이 파일의 주소가 만료됐습니다 — 최신 index.html 을 다시 받으면 켤 수 있습니다'
      LIVE.notePill = '⚠ 주소 만료 — 최신 index.html'
      LIVE.noteWarn = true
      syncChrome(L)
      return liveInfo()
    }
    const { until, by: untilBy, cap } = liveEnd(now)
    const lease = Math.min(now + LIVE_LEASE_MS, until)
    LIVE.busy = true; LIVE.note = ''; LIVE.notePill = ''; LIVE.noteWarn = false
    syncChrome(L)
    // 켜기는 **조건 없이** 쓴다 — 켜는 사람이 지금 이 창의 주인이다(다른 창의 판을 이어받는다).
    // 🔴 `utilization` 은 **늘 명시한다** — 키가 없으면 상태 함수가 옛 화면 파일로 보고 2층을 따른다(`handler.decide`).
    // 🆕 `monitor`·`until` 은 화면만 읽는다(따라 보는 창이 무엇을 보이고 언제 끝나는지 · 핸들러는 무시한다 — `handler.decide`).
    const w = await liveWrite({ live: true, layer2: !!layer2, utilization: !!opt.util, expiresAt: msIso(lease), monitor: !!opt.monitor, until: msIso(until) })
    LIVE.busy = false
    if (!w.ok) {
      LIVE.note = `실시간을 켜지 못했습니다 — ${w.why} · ${opt.monitor ? '모니터링' : '사용률'}은 꺼진 상태 그대로입니다`
      LIVE.notePill = '⚠ 실시간 못 켬'
      LIVE.noteWarn = true
      syncChrome(L)
      return liveInfo()
    }
    LIVE.seq++
    Object.assign(LIVE, { on: true, layer2: !!layer2, util: !!opt.util, setWhy: '', since: now, asked: now, until, untilBy, cap,
      etag: null, doc: null, docAt: 0, phase: 'wait', why: '첫 관측을 기다리는 중', lastCheck: 0,
      mode: 'lease', lease, ctlEtag: w.etag || null, renewWhy: '', ctlAt: now, ctlWhy: '', end: 0, doneWhat: '', doneReads: -1 })
    clearTimeout(LIVE.offT)
    LIVE.offT = setTimeout(() => { liveOff(LIVE.untilBy) }, until - now)
    // 🔴 구운 문서를 먼저 내린다 — 켠 순간부터 화면의 상태는 **저장소의 것**이어야 한다.
    obsAdopt(null)
    S.monitor = !!opt.monitor; S.util = !!opt.util
    syncToggles()
    liveRender()
    // ETag 를 못 읽었으면 지금 옛 동작으로 떨어진다(`liveRenew` 가 조건 없는 PUT 한 번으로 상한을 쓴다).
    if (LIVE.ctlEtag) liveRenewAt(LIVE_RENEW_MS)
    else await liveRenew()
    return livePoll()
  }
  async function liveOff (why) {
    if (!LIVE.on) return liveInfo()
    clearTimeout(LIVE.pollT); clearTimeout(LIVE.offT); clearTimeout(LIVE.renewT)
    LIVE.seq++
    const until = LIVE.until
    // 서버가 스스로 멈추는 시각 = 마지막으로 쓴 만료(임대면 그 임대 · 옛 동작이면 상한).
    const lease = LIVE.lease || until
    const renewWhy = LIVE.renewWhy
    const cap = LIVE.cap
    Object.assign(LIVE, { on: false, util: false, setWhy: '', pollT: null, offT: null, renewT: null, phase: null, why: '', etag: null,
      doc: null, docAt: 0, mode: null, lease: 0, ctlEtag: null, renewWhy: '',
      ctlAt: 0, ctlSeen: null, ctlWhy: '', mine: [], end: 0, cap: 0, doneWhat: '', doneReads: -1 })
    const again = ' · 다시 켜려면 «모니터링» 또는 «사용률»'
    // [줄 · 알약 · warn]. `user` 는 줄이 없다(누른 사람이 안다).
    const NOTE = {
      // 🆕 고른 시간을 말한다(사용자 결정 2026-10-28 — 「설정한 1시간이 지나 실시간이 꺼졌습니다」)
      ttl: [`설정한 ${durWord(cap)}이 지나 ${msHm(until)} 에 실시간이 자동으로 꺼졌습니다${again}`, `실시간 꺼짐(설정한 ${durWord(cap)})`, false],
      // 🆕 따라 보기(보강 4): 그 창이 고른 끝 · 다른 창이 끔 · 켠 창이 연장하지 않아 서버가 멈춤
      end: [`켠 창이 정한 끝 시각(${msHm(until)})이 되어 실시간이 꺼졌습니다${again}`, '실시간 꺼짐(정한 시각)', false],
      remote: [`다른 창에서 실시간을 껐습니다${again}`, '실시간 꺼짐(다른 창에서 끔)', false],
      stopped: [`실시간이 멈췄습니다 — 켠 창이 ${msHm(lease)} 까지 연장하지 않았습니다(그 창을 닫았거나 잠들었습니다)${again}`,
        '실시간 멈춤(켠 창이 연장 안 함)', false],
      url: ['이 화면 파일의 주소가 만료돼 실시간이 꺼졌습니다 — 최신 index.html 을 다시 받으면 켤 수 있습니다',
        '실시간 꺼짐(주소 만료)', false],
      slept: [`절전 중에 실시간이 꺼졌습니다 — 이 창이 ${msHm(lease)} 까지 연장하지 못했습니다(컴퓨터 잠자기·탭 멈춤)${again}`,
        '실시간 꺼짐(자는 동안)', false],
      renew: [`연장 요청이 닿지 않아 ${msHm(lease)} 에 실시간이 꺼졌습니다 — ${renewWhy}${again}`,
        '⚠ 실시간 꺼짐(연장 실패)', true],
      conflict: ['다른 창에서 실시간 설정을 바꿨습니다 — 이 창은 덮어쓰지 않고 실시간 표시를 껐습니다 · 서버는 그 창이 설정한 대로 동작합니다' +
        again, '실시간 꺼짐(다른 창에서 바꿈)', false],
    }[why] || ['', '', false]
    LIVE.note = NOTE[0]; LIVE.notePill = NOTE[1]; LIVE.noteWarn = NOTE[2]
    // 끄면 **구운 문서**로 돌아간다(배포본에는 없다 → 구조만).
    obsAdopt(OBS_BAKED)
    S.monitor = false; S.util = false
    syncToggles()
    liveRender()
    // 🔵 자동 꺼짐(ttl·url·slept·renew·conflict · 🆕 end·remote·stopped)은 쓰지 않는다 — 서버는 마지막으로 쓴 만료에 스스로 멈추고,
    //    conflict 는 남의 판이라 덮지 않는다. 🔴 사람이 끈 것만 **조건 없이** 쓴다 — 끄기는 요금을 멈추는
    //    방향이라 다른 창의 켜짐을 덮어도 사람이 모르는 요금이 생기지 않는다.
    if (why === 'user') {
      const w = await liveWrite({ live: false })
      if (!w.ok && !LIVE.on) {
        LIVE.note = `끄기 요청이 실패했습니다 — ${w.why} · 서버는 ${msHm(lease)} 에 자동으로 멈춥니다` +
          '(그때까지 관측과 요금이 계속됩니다)'
        LIVE.notePill = `⚠ 끄기 실패 — 서버 ${msHm(lease)} 까지`
        LIVE.noteWarn = true
        syncChrome(L)
      }
    }
    return liveInfo()
  }
  // ---- «모니터링» 버튼 + 층 고르기 팝오버(`#monpop`) ----
  // 🔴 새 상단바 버튼을 만들지 않는다(`flow.html` 의 `#btn-mon` 주석) — 켜는 선택지는 팝오버에 둔다.
  //    팝오버는 `#top`·`#svg` **밖**이다(검사 68·60 — 범위 팝오버와 같은 이유).
  const monPopEl = document.getElementById('monpop')
  const toggleMonPop = (on) => {
    if (!monPopEl) return false
    const next = on === undefined ? !monPopEl.classList.contains('on') : !!on
    if (next && LIVE_SLOT) {
      const now = Date.now()
      const until = liveEnd(now).until
      const head = monPopEl.querySelector('.phead')
      for (const c of monPopEl.querySelectorAll('[data-dur]')) c.classList.toggle('on', Number(c.dataset.dur) === LIVE.dur)
      // 🔴 켜기 **전에** 말한다: 언제까지 도는지 · 창을 닫으면 언제 멈추는지(요금도 그때까지다). 옛 저장소라
      //    조건부 연장이 안 되면 켠 뒤의 배너가 「창을 닫아도 그때까지 켜져 있다」로 고쳐 말한다(`liveRenew`).
      // 🆕 이 창이 주인으로 켜 둔 동안은 보기만 바꾼다 — 끝 시각은 켤 때 고른 그대로다(시간 칸을 숨긴다). 따라 보는 창이 바꾸면 새로 켜는 것과 같다.
      const own = LIVE.on && LIVE.mode !== 'follow'
      monPopEl.classList.toggle('own', own)
      if (head && own) head.textContent = `실시간 갱신 — ${msHm(LIVE.until)} 까지(켤 때 고른 그대로) · 보기만 바꿉니다`
      else if (head) {
        head.textContent = `실시간 갱신 — ${msHm(until)} 까지(${durWord(LIVE.dur)}) · 켜면 바로 첫 관측 · 그 뒤 5분마다 · ` +
          '창을 닫으면 약 20분 안에 멈춥니다'
      }
    }
    monPopEl.classList.toggle('on', next)
    return next
  }
  function monClick () {
    // 주소가 없는 화면(로컬·픽스처)은 예전 그대로 구운 문서를 얹었다 내렸다 한다.
    if (!LIVE_SLOT) return setFlag('monitor', !S.monitor)
    if (LIVE.busy) return null
    // 사용률만 켜져 있으면 실시간은 그대로 두고 모니터링만 얹는다/내린다(사용자 결정 2026-10-06 「아예 별개」)
    if (LIVE.on && S.monitor) { liveSet(false, false, S.util); return null }
    if (S.monitor) return setFlag('monitor', false)
    return toggleMonPop()
  }
  // 「사용률」 버튼 — 모니터링과 **따로** 켠다. 실시간 주소가 있는 화면이면 눌러서 바로 실시간을 켠다(지표 층 없이 · 사용률만)
  function utilCan () {
    if (CMP) return false
    if (!LIVE_SLOT) return !OBS_STALE && !!(UTROWS && UTROWS.length)
    return LIVE.on || (!LIVE.busy && !liveUrlDead(Date.now()))
  }
  function utilClick () {
    if (!utilCan() && !S.util) return null
    if (!LIVE_SLOT || (!LIVE.on && S.util)) return setFlag('util', !S.util)
    if (LIVE.busy) return null
    if (LIVE.on) { liveSet(S.monitor, S.monitor && LIVE.layer2, !S.util); return null }
    liveOn(false, { monitor: false, util: true })
    return null
  }
  // 켠 채로 무엇을 보나만 바꾼다 — 제어 키를 다시 쓴다(지금 판의 ETag 로 조건부 · 다른 창이 바꿨으면 덮지 않는다).
  //   둘 다 끄면 실시간을 끈다. 모니터링을 내리면 지표 층도 내린다(사용률만 보는 동안 흐름 지표 요금이 들지 않게)
  // 🆕 따라 보던 창이 바꾸면 **새 주인**이다(보강 4 D · 사용자 결정 2026-10-28) — 켜기와 같다: 조건 없이 쓰고(`ctlEtag` 가 비어 있다),
  //    고른 시간으로 새 상한·새 임대를 잡고 연장을 건다. 주인 창의 조건부 쓰기가 부딪히면(412·409) 읽어서 정한다(`liveConflict`).
  async function liveSet (monitor, layer2, util, again) {
    if (!LIVE.on || LIVE.busy) return liveInfo()
    if (!monitor && !util) return liveOff('user')
    const seq = LIVE.seq
    const take = LIVE.mode === 'follow'
    const now = Date.now()
    const nu = take ? liveEnd(now) : null
    const until = take ? nu.until : LIVE.until
    const lease = take || LIVE.mode === 'lease' ? Math.min(now + LIVE_LEASE_MS, until) : LIVE.until
    if (take) LIVE.ctlEtag = null
    LIVE.busy = true
    syncChrome(L)
    const w = await liveWrite({ live: true, layer2: !!layer2, utilization: !!util, monitor: !!monitor, until: msIso(until), expiresAt: msIso(lease) }, LIVE.ctlEtag)
    LIVE.busy = false
    if (!LIVE.on || seq !== LIVE.seq) return liveInfo()
    if (w.conflict) {
      const k = await liveConflict()
      if (!LIVE.on || seq !== LIVE.seq || k === 'handled') return liveInfo()
      if (k === 'mine' && !again) return liveSet(monitor, layer2, util, true)
      LIVE.setWhy = w.why; syncChrome(L); return liveInfo()
    }
    if (!w.ok) { LIVE.setWhy = w.why; syncChrome(L); return liveInfo() }
    if (take) {
      clearTimeout(LIVE.offT)
      Object.assign(LIVE, { mode: 'lease', until, untilBy: nu.by, cap: nu.cap, end: 0, ctlWhy: '', ctlAt: Date.now() })
      LIVE.offT = setTimeout(() => { liveOff(LIVE.untilBy) }, Math.max(0, until - Date.now()))
    }
    Object.assign(LIVE, { layer2: !!layer2, util: !!util, setWhy: '', ctlEtag: w.etag || LIVE.ctlEtag, asked: Date.now(), doneWhat: '', doneReads: -1 })
    if (LIVE.mode === 'lease') LIVE.lease = lease
    S.monitor = !!monitor; S.util = !!util
    syncToggles()
    liveRender()
    // 새 주인이 됐다 — 연장을 건다(ETag 를 못 읽었으면 켜기와 같이 옛 동작으로 떨어진다)
    if (take) {
      if (LIVE.ctlEtag) liveRenewAt(LIVE_RENEW_MS)
      else await liveRenew()
    }
    // 이 쓰기도 상태 함수를 깨운다(층이 바뀌었다) → 다음 읽기를 빠른 쪽으로 다시 건다
    liveSchedule()
    return liveInfo()
  }
  if (monPopEl) {
    monPopEl.addEventListener('click', (ev) => {
      // 🆕 켜 둘 시간 칸 — 고르기만 한다(팝오버는 열린 채 · 머리줄의 「까지」가 바뀐다). 지금 켜진 판의 상한은 바꾸지 않는다.
      const du = ev.target.closest('[data-dur]')
      if (du) {
        const ms = Number(du.dataset.dur)
        if (LIVE_DURS.includes(ms)) { LIVE.dur = ms; toggleMonPop(true); syncChrome(L) }
        return
      }
      const it = ev.target.closest('[data-live]')
      if (!it) return
      toggleMonPop(false)
      if (LIVE.on) liveSet(true, it.dataset.live === '1', S.util)
      else liveOn(it.dataset.live === '1')
    })
  }
  document.addEventListener('click', (ev) => {
    if (!monPopEl || !monPopEl.classList.contains('on')) return
    if (ev.target.closest('#monpop') || ev.target.closest('#btn-mon')) return
    toggleMonPop(false)
  })

  // ---------------- 필터 (§13-49) ----------------
  // 필터는 **파괴적**이다 → ① 확인 모달, ② 적용 후 상시 배너. 불변식 ④ 와 같은 이유로
  // «지금 무엇을 안 보여주고 있는지»를 화면에 남긴다 — 있는데 없다고 그리면 사람이 오판한다.
  const fbar = document.getElementById('fbar')
  const modal = document.getElementById('modal')
  function filterSet (q) {
    const lq = String(q).toLowerCase()
    const keep = new Set()
    for (const n of T.nodes) {
      if (!matchQ(n, lq)) continue
      for (let p = n; p; p = p.parent_id ? byId.get(p.parent_id) : null) keep.add(p.id)  // 조상=맥락
      // 걸친 서브넷도 맥락이다 — NAT·LB 의 서브넷은 조상 사슬이 아니라 `attachedTo`·`spans` 로만 안다. 빼면 새 도식이
      // 그 자원을 앉힐 서브넷이 없어 「못 그린 리소스」로 떨어진다(검사 48)
      for (const sid of [n.attachedTo, ...(n.spans || [])]) if (sid && byId.has(sid)) keep.add(sid)
      const st = [n.id]                                                                  // 자손=내용
      while (st.length) {
        for (const c of (kids.get(st.pop()) || [])) { keep.add(c.id); st.push(c.id) }
      }
    }
    return keep
  }
  // 검색·필터에 `상태:` 원자가 있는데 모니터링이 꺼져 있다(§17.26 C-8) — 아무것도 맞지 않는 이유를 필터 줄이 말한다
  const qMonOff = () => !monOn() && [S.search, FILTER ? S.filter : ''].some((q) => !!q && MON_Q_RE.test(q) && parseQuery(String(q).toLowerCase()).ok)
  function syncFbar () {
    if (!fbar) return
    const note = !S.qErr && qMonOff() ? `<span class="qnote">${esc(COPY.monOff)}</span>` : ''
    fbar.classList.toggle('on', !!FILTER || !!S.qErr || !!note)
    fbar.innerHTML = S.qErr
      ? `<span class="qerr">${esc(S.qErr)}</span>`
      : (FILTER
          ? `필터 «<b>${esc(S.filter)}</b>» 적용 중 — 노드 <b>${T.nodes.length - FILTER.size}</b>개를 ` +
            '화면에서 제거했습니다 <span class="x" data-unfilter>필터 해제</span>'
          : '') + (note && FILTER ? ' · ' : '') + note
  }
  // 파싱 실패는 조용한 «0건»이 되면 안 된다 — 그러면 사용자가 «없다»고 오판한다(§13-56·62).
  // 실패면 문구만 남고 배치는 **하나도 바뀌지 않는다**.
  function applyFilter (q) {
    const p = parseQuery(String(q).toLowerCase())
    if (!p.ok) { S.qErr = p.err; syncFbar(); return { ok: false, err: p.err, kept: 0, hidden: 0 } }
    S.qErr = ''
    const keep = filterSet(q)
    FILTER = keep; S.filter = String(q)
    relayout(); syncFbar()
    return { ok: true, kept: keep.size, hidden: T.nodes.length - keep.size }
  }
  function clearFilter () { FILTER = null; S.filter = ''; S.qErr = ''; relayout(); syncFbar() }
  function askFilter (q) {
    const s = q === undefined ? S.search : String(q)
    if (!s || !modal) return null
    const p = parseQuery(s.toLowerCase())
    if (!p.ok) { S.qErr = p.err; syncFbar(); return { ok: false, err: p.err } }
    S.qErr = ''; syncFbar()
    const keep = filterSet(s)
    modal.querySelector('[data-msg]').innerHTML =
      '이 조건에 맞지 않는 리소스를 <b>화면에서 제거</b>합니다.<br>' +
      `«${esc(s)}» → 남는 노드 <b>${keep.size}</b>개 · 숨는 노드 <b>${T.nodes.length - keep.size}</b>개.` +
      (keep.size ? '' : `<br><span class="qerr">${esc(COPY.empty)}</span>`)
    const tb = modal.querySelector('[data-buckets]')
    if (tb) {
      tb.innerHTML = tagBuckets().map((b) =>
        `<div class="bk" data-bucket="${esc(b.bucket)}"><b>${esc(b.bucket)}</b> <span class="off">${b.n}</span>` +
        b.keys.slice(0, 6).map((k) =>
          `<span class="bkey" data-key="${esc(k.key)}">${esc(k.key)} <span class="off">${k.n}</span></span>`).join('') +
        '</div>').join('')
    }
    modal.classList.add('on')
    return { ok: true, kept: keep.size, hidden: T.nodes.length - keep.size }
  }
  const fbtn = document.getElementById('btn-filter')
  if (fbtn) fbtn.onclick = () => askFilter()
  if (modal) {
    modal.addEventListener('click', (ev) => {
      const bk = ev.target.closest('[data-key]')
      if (bk) {                              // 태그 버킷 클릭 → 그 키로 쿼리를 채운다(§13-62)
        const k = bk.dataset.key
        if (k !== '(태그 없음)') {
          S.search = 'tag:' + k
          document.getElementById('search').value = S.search
          askFilter(S.search)
        }
        return
      }
      if (ev.target.closest('[data-ok]')) { modal.classList.remove('on'); applyFilter(S.search) }
      else if (ev.target.closest('[data-cancel]') || ev.target === modal) modal.classList.remove('on')
    })
  }
  if (fbar) {
    fbar.addEventListener('click', (ev) => { if (ev.target.closest('[data-unfilter]')) clearFilter() })
  }

  // ---------------- 웨이브 2 DOM 배선 (§13-55·59·62·63·64) ----------------
  // 제품 컨트롤과 하네스 API 가 **같은 함수**를 지난다(웨이브 1과 같은 이유).
  const CHROME_ACT = {
    zin: () => zoomBy(1.25), zout: () => zoomBy(0.8), fit, fitall: fitAll,
    reset: resetView, refresh, full: toggleFull,
    'export': () => exportSvg(), png: () => exportPng()
    // 「목록」(`list`)은 없다 — 그 표는 왼쪽 아래 「!」 상자다(대장 129 · 사용자 결정 2026-10-20)
    // §15 검증 오버레이의 「검증」 버튼·키 V 는 없다 — 사용자 결정 2026-10-20 「고객 화면에서 뺀다」(대장 82 개정 · 검사 77)
  }
  if (chromeEl) {
    chromeEl.addEventListener('click', (ev) => {
      const b = ev.target.closest('[data-act]')
      if (b && CHROME_ACT[b.dataset.act]) CHROME_ACT[b.dataset.act]()
    })
  }
  // 닫는 경로 넷(§17.26 C-3): ✕ · 원을 다시 누름 · 상자 **밖** 클릭 · Esc(위 keydown — 맨 먼저)
  if (probsEl) {
    probsEl.addEventListener('click', (ev) => { ev.stopPropagation(); toggleList() })
    probsEl.addEventListener('keydown', (ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); toggleList() } })
  }
  document.addEventListener('click', (ev) => {
    if (!S.listOn) return
    if (ev.target.closest('#probpop') || ev.target.closest('#probs')) return
    toggleList(false)
  })
  if (listEl) {
    listEl.addEventListener('click', (ev) => {
      // 🔴 상자 안 클릭은 문서까지 올라가지 않는다 — 정렬이 상자를 다시 그리면 눌린 칸이 문서에서 떨어져 나가 「밖 클릭」으로 읽힌다
      ev.stopPropagation()
      if (ev.target.closest('[data-lclose]')) { toggleList(false); return }
      if (ev.target.closest('[data-csv]')) {
        dl(new Blob(['﻿' + listCsv()], { type: 'text/csv;charset=utf-8' }), tsName('csv')); return
      }
      const th = ev.target.closest('[data-col]')
      if (th) { sortList(th.dataset.col); return }
      // 「모니터링 이상」 절의 줄(§17.26 C-8) = 상자를 닫고 그 리소스로 간다(가운데 + 고름) — 정보창이 상자 뒤에 숨지 않게 먼저 닫는다
      const mr = ev.target.closest('.pmr[data-go]')
      if (mr) { toggleList(false); monFocus(mr.dataset.go); return }
      // 그린 자원의 줄(Global Accelerator · 배치 D) = 그 자원의 정보창을 연다 — 상자를 닫고 연다(정보창이 상자 뒤에 숨지 않게)
      const tr = ev.target.closest('tr[data-open]')
      if (tr && window.NT_AWS && window.NT_AWS.open) { toggleList(false); window.NT_AWS.open(tr.dataset.open) }
    })
  }
  const modeBtn = document.getElementById('btn-live')
  if (modeBtn) modeBtn.onclick = () => toLive()
  // 대장 101 ②: 독은 기본 닫힘이고 이 버튼이 유일한 진입점이다. 🔴 무가드로 쓰지 마라 —
  // 상단바에서 이 버튼을 빼면 `null.onclick` 에서 모듈 전체가 죽는다(대장 93 때 실측).
  const histBtn = document.getElementById('btn-hist')
  if (histBtn) {
    // 여닫기다. 열 때 기본 탭은 「변경 이력」 — 이 버튼의 이름이 그것이다.
    histBtn.onclick = () => { S.dock = S.dock ? '' : 'hist'; renderDock(); return S.dock }
  }
  if (bansEl) {
    bansEl.addEventListener('click', (ev) => {
      // «최신 화면 열기» 는 요약 줄 안에 있다 — 접기·펴기보다 먼저 본다.
      if (ev.target.closest('[data-golatest]')) { goLatest(); return }
      if (ev.target.closest('[data-bantoggle]')) { S.bansOpen = !S.bansOpen; syncChrome(L) }
    })
  }

  // ---- 최신 화면 열기(QA B P3 · 사용자 결정 2026-10-14) ----
  // 🔴 **사람이 누를 때만** 이 탭이 넘어간다 — 화면이 스스로 넘어가면 보던 것(고른 카드·펼친 독)을 예고 없이 잃고,
  //    불변식 ⑥ 의 세 번째 길(클릭 전용 이동)이 「로드 중 요청 0」을 깨뜨린다(검사 106). fetch 가 아니라 이동이다 —
  //    CSP `connect-src` 와 무관하고, 새 화면 파일은 자기 CSP·자기 서명을 갖고 온다.
  // 누름 = 모니터링을 이어 가겠다는 동의다(사용자 결정 2026-10-14): 켜 둔 층을 `#resume=` 에 실어 새 화면이 같은 층으로
  // 다시 켠다. 보던 범위·선택(`scope`·`sel`)도 그대로 싣는다 — 새 구조에 그 id 가 없으면 `applyHash` 가 떨어뜨린다.
  function goLatest () {
    if (!canGoLatest()) return null
    const keep = (location.hash || '').replace(/^#/, '').split('&').filter((x) => /^(scope|sel)=/.test(x))
    const rs = LIVE.on ? [S.monitor ? 'mon' : '', LIVE.layer2 ? 'l2' : '', LIVE.util ? 'util' : ''].filter(Boolean) : []
    if (rs.length) keep.push('resume=' + rs.join('.'))
    const url = LIVE_SLOT.indexUrl + (keep.length ? '#' + keep.join('&') : '')
    location.assign(url)
    return url
  }
  // `#resume=` 를 읽고 **지운다**(다시 읽기·북마크가 같은 켜기를 되풀이하지 않게). 주소 칸이 없는 화면에서는 켜지 않는다.
  function resumeFromHash () {
    const m = /(?:^|#|&)resume=([^&]+)/.exec(location.hash || '')
    if (!m) return null
    const rest = (location.hash || '').replace(/^#/, '').split('&').filter((x) => x && !/^resume=/.test(x))
    // 🔴 절대 주소로 쓴다 — 상대 주소는 `<base>` 기준으로 풀려 다른 오리진이 되면 거절된다(서명 쿼리도 그대로 남긴다).
    try { history.replaceState(null, '', location.href.split('#')[0] + (rest.length ? '#' + rest.join('&') : '')) } catch (e) { /* 해시가 남아도 켜기는 이 한 번이다 */ }
    const t = new Set(decodeURIComponent(m[1]).split('.'))
    if (!LIVE_SLOT || !(t.has('mon') || t.has('util'))) return null
    return liveOn(t.has('l2'), { monitor: t.has('mon'), util: t.has('util') })
  }

  // ---------------- 시작 ----------------
  if (CMP) {
    // 비교 칸은 그림만 보인다 — 상단바·독·배너는 부모 창의 것이다(§3j J1).
    document.body.classList.add('cmp')
    // 칸은 왼쪽 화면의 보기 선택(AZ·서브넷)을 따른다(§17.23)
    if (CMP.aws) { S.awsAz = CMP.aws.az !== false; S.awsSn = CMP.aws.sn !== false }
  }
  { const st = document.getElementById('stage'); if (st) st.classList.add('aws') }
  // 상태 버튼 셋(대장 128 · 위 `STATE_WAY`) — DOM 은 남긴다(검사 84 의 순서·id 집합) · `hidden` 으로만 숨긴다
  for (const id of ['btn-mon', 'btn-util', 'btn-hist']) { const b = document.getElementById(id); if (b) b.hidden = !STATE_WAY }
  L = relayout()
  // 켜지 않고 열어 둔 창도 주소가 죽는 순간 줄·알약을 바꾼다 — 한 번 다시 그릴 뿐 요청 0 이다. 켜진 창은
  // 자기 자동 꺼짐 타이머(`offT`)가 따로 말한다. `setTimeout` 상한(약 24.8일)을 넘는 주소는 걸지 않는다.
  if (LIVE_SLOT) {
    const ms = LIVE_SLOT.urlUntil - LIVE_MIN_URL_MS - Date.now() + 1000
    if (ms >= 1000 && ms < 2 ** 31) setTimeout(() => { syncChrome(L) }, ms)
  }
  // 비교 칸의 첫 화면 = 바뀐 곳 가운데 + 조금 당김(§3j J3 개정 · P8). 바뀐 곳을 못 찾으면 전체 맞춤
  let CMP_FOCUS = null
  // 누른 줄이 있으면 그 대상만 감싼다(§3j J3 · 2026-10-21) — 표시는 그 시각 전부 그대로다
  if (CMP) { const F = CMP.focus; CMP_FOCUS = F ? cmpFocus(F.marks, F.seats) : cmpFocus(CMP.marks, CMP.seats); if (!CMP_FOCUS) fit() } else fit()
  // 리셋은 «처음 본 그림»으로 돌아가는 것이다 → 초기 뷰를 그대로 보관한다(검사 59).
  INIT_VIEW = { zoom: S.zoom, tx: S.tx, ty: S.ty }
  if (!CMP) {
    window.addEventListener('hashchange', () => { if (!hashLock) applyHash() })
    applyHash()
    resumeFromHash()
  } else {
    // 그림에 자리를 못 찾은 표시를 부모에게 알린다 — 부모의 글자 목록이 그것을 센다(불변식 ④).
    // 제 계획(배율 · 가운데 · 닻)을 같이 보낸다 — 부모가 두 칸을 같은 배율 · 같은 곳으로 맞춘다(§3j J3 · N-01)
    window.addEventListener('message', (ev) => {
      if (ev.source !== window.parent) return
      const d = ev.data || {}
      if (d.nettopo !== 'cmp-cam') return
      const got = cmpShare(CMP_FOCUS, d.other && typeof d.other === 'object' ? d.other : null)
      if (got) CMP_FOCUS = got
      parent.postMessage({ nettopo: 'cmp-synced', zoom: S.zoom }, '*')
    })
    parent.postMessage({ nettopo: 'cmp-ready', undrawn: CMPD.undrawn, focus: cmpBrief(CMP_FOCUS) }, '*')
  }

  window.__flowApi = {
    S, byId, relayout, fit, fitAll, aws: () => NT_AWS,
    // 🔴 **하네스 주입구**다 — 화면에는 켜고 끄는 길이 없다(☰ 「흐름 애니메이션」 삭제 · 사용자 결정 2026-10-20 · 대장 130).
    //    기본값은 OS 「동작 줄이기」(`S.anim` 초기값)이고 하네스가 움직임을 세우고 재기 위해서만 부른다.
    setAnim: (on) => { S.anim = !!on; return render(L) },
    search: (q) => {
      S.search = q; document.getElementById('search').value = q
      const t0 = performance.now(); const nMatch = applyDim(L); return { renderMs: performance.now() - t0, nMatch }
    },
    // ---- 대장 94·101 독 · 변경 이력 ----
    // 🔴 `histRows()` 를 하네스가 오라클로 쓰면 동어반복이 된다 — 하네스는 `window.OBS.changes`
    //    에서 직접 세고 이 값과 맞춰 본다(검사 85 ①).
    openDock, closeDock, dockTab: () => S.dock, histRows, pickTime, histLimits, toggleHistGroup,
    // §3j 비교 칸(검사 114). 부모에서는 `cmp` = 열린 칸의 요약(`dump().cmp`), 칸 안에서는 그린 표시·카메라(바뀐 곳).
    cmpClose: () => { const v = cmpClose(); renderDock(); return v },
    cmpDump: () => (CMP ? { at: CMP.at, marks: CMPD.marks.map((m) => ({ ...m })), undrawn: CMPD.undrawn.slice(),
      keep: CMPD.keep, focus: CMP_FOCUS, foc: CMP.focus ? CMP.focus.marks.concat(CMP.focus.seats).map((m) => m.id).sort() : null, zoom: S.zoom, tx: S.tx, ty: S.ty } : null),
    askFilter, applyFilter, clearFilter, filterSet,
    modalOn: () => !!(modal && modal.classList.contains('on')),
    filterOn: () => !!FILTER,
    // ---- 웨이브 2 (§13-53~64). 전부 제품 컨트롤과 같은 함수다 ----
    toggleList, sortList, listCsv, listRows,                                 // 55
    // 「모니터링 이상」 절(§17.26 C-8 · 검사 172) — 줄 재료 · 누르기와 같은 함수. 🔴 하네스는 이것을 오라클로 쓰지 않는다(관측 행에서 따로 센다)
    monRows: () => monRows(), monFocus: (k) => monFocus(k),
    copy: () => ({ ...COPY }),                                               // 56
    consoleUrl: (id) => consoleUrl(byId.get(id)),                            // 58
    setHash, applyHash, hash: () => location.hash,
    zoomBy, resetView, refresh, toggleFull, initView: () => ({ ...INIT_VIEW }), // 59
    exportSvg, exportPng,                                                    // 60
    parseQuery, matchQ, tagBuckets,                                          // 62
    hideNode, toLive, versions,                                              // 63
    setZoom: (z) => { S.zoom = z; applyTransform() },
    // §3h 하네스 주입구(검사 92). 픽스처에는 `coverage` 가 0행이므로 이것 없이는 권한 축을
    // **원리적으로 못 잰다**(위 `covSet` 🔴). 제품 경로(`T.coverage`)와 같은 코드를 지난다.
    setCoverage: (rows) => { covSet(rows); relayout(); return covAccounts() },
    // ADR 0006 하네스 주입구(검사 94). 🔴 픽스처에는 `window.OBS` 가 **없고**, 실계정 스냅샷에도
    //    `down` 이 0건이다(실측 2026-09-24: up 138 · live 17 · none 0 · down 0) → 이것 없이는
    //    「이상」·「관측 불가」 경로가 **원리적으로 미측정**이다. 제품 경로와 같은 코드를 지난다.
    setObservations: (rows) => { obsSet(rows); render(L); return obsStats(L) },
    setMonitor: (on) => setFlag('monitor', on),
    setUtil: (on) => setFlag('util', on),
    // 사용률 주입구(검사 140~145) — 픽스처 토폴로지에 관측 문서가 없으므로 이것 없이는 원리적으로 못 잰다. 제품 경로와 같은 코드
    setUtilization: (rows) => { UTROWS = Array.isArray(rows) ? rows.slice() : null; render(L); return (UTROWS || []).length },
    // 보기 셋 = 두 버튼의 조합(하네스용 — 실시간 주소가 없는 화면에서만 쓴다). 'mon' = 모니터링만 · 'ut' = 사용률만 · 'both'
    setUtView: (v) => { S.monitor = v !== 'ut'; S.util = v !== 'mon'; if (!S.util) S.utPick = null; syncToggles(); render(L); return v },
    setUtWin: (m) => { S.utWin = +m; render(L); return S.utWin },
    utPick: (k) => NT_AWS.utPick(k),
    utList: () => NT_AWS.utList(),
    monitor: () => S.monitor,
    util: () => S.util,
    // 실시간 채널(검사 101~103). 🔴 타이머·버튼과 **같은 함수**다 — 하네스가 1분을 기다리지 않고
    //    `livePoll` 을 부르는 것은 「언제」만 바꾸고 「무엇을」은 바꾸지 않는다.
    liveInfo: () => liveInfo(),
    stateWay: () => STATE_WAY,   // 상태 버튼 셋을 보이나(대장 128 · 검사 147 — 하네스는 이 값을 오라클로 쓰지 않는다. 화면의 hidden·보임을 잰다)
    liveOn: (layer2) => liveOn(!!layer2),
    utilClick: () => utilClick(),
    liveOff: () => liveOff('user'),
    livePoll: () => livePoll(),
    liveRenew: () => liveRenew(),
    obsStats: () => obsStats(L),
    // 하네스용 직렬화 (docs/canvas-principles.md §10). 그림 자체(틀·칸·선)는 `aws` 가 말한다
    // 이력 줄의 종류 이름(검사 113 — 스키마 종류 전수가 「리소스」 · 영어 표로 떨어지지 않는지)
    histWord: (t) => typeWord(t),
    dump: () => ({
      canvasW: L.canvasW, canvasH: L.canvasH,
      zoom: S.zoom, viewW: Math.round(svgEl.getBoundingClientRect().width),
      tokens: C,
      aws: NT_AWS.dump(),
      // §3k K1: 배너 줄 전부 — 사람 문장(`text`, 빈 글자 = 화면에 줄 없음)과 개발 설명(`dev`). 하네스가
      //    재던 사실(회계·삼킨 홉·짝 설명 …)은 여기서 잰다. 🔴 개발자 화면(V)과 **같은 출처**다(검사 115 ②).
      bans: bannerRows().map((r) => ({ kind: r.kind, text: r.text || '', dev: r.dev || '', title: r.title || '' })),
      nAccounts: accounts.length,
      nEdges: L.nEdges, nRawEdges: L.nRawEdges, layoutMs: L.ms, anim: S.anim,
      nNodes: T.nodes.length,
      filter: FILTER ? { q: S.filter, kept: FILTER.size } : null,
      // §3h H2(검사 92): 배너가 낸 숫자와 **같은 출처**여야 한다. 🔴 `found` 는 계약에 없어서
      // 보통 `null` 이다 — 그 자리에 `read` 를 복사하면 화면이 「전부 읽었다」고 거짓말한다.
      cov: covAccounts(),
      nList: (T.listOnly || []).length, listOn: S.listOn, listSort: { ...S.listSort },
      // 「모니터링 이상」 절의 갈래별 id(§17.26 C-8) — 꺼져 있으면 `null`(절 없음)
      mon: (() => { const M = monRows(); return M ? { down: M.down.map((r) => r.id), partial: M.partial.map((r) => r.id), none: M.none.map((r) => r.id) } : null })(),
      // §3i 대장 97: 잔차를 **내놓는다**. 하네스가 「모델 − (그려짐 ∪ 대표됨)」을 독립으로
      // 다시 세서 이 값과 맞춰 볼 수 있어야 검사 88 이 동어반복을 면한다.
      unplaced: S.unplaced.map((r) => r.id), nUnplaced: S.unplaced.length,
      // 엣지 쪽(대장 98). 🔴 `why` 까지 내놓는다 — 「그리지 않았다」만으로는 **왜**를 하네스가
      // 다시 셀 수 없고, 사유 어휘가 §7 과 뭉개지는 것을 잡을 수 없다(§3i I4).
      unwired: S.unwired.map((r) => ({ id: r.id, a: r.a, b: r.b, type: r.type, why: r.why })),
      nUnwired: S.unwired.length,
      // 그린 자원의 그리지 못한 대상(배치 D · Global Accelerator) — 「!」 표의 셋째 묶음
      notes: (S.notes || []).map((r) => ({ key: r.key, region: r.region, open: r.open, say: r.fixedSay })), nNotes: (S.notes || []).length,
      qErr: S.qErr,
      // ---- 대장 94·101 독 · 변경 이력 (검사 85) ----
      hasObs: !!OBS, dock: S.dock, histAt: S.histAt, nHistRows: histRows().length,
      nHistLimits: histLimits().length,
      // 오른쪽 비교 칸(§3j · 검사 114). 칸 **안의** 그림은 그 틀의 `__flowApi.cmpDump()` 가 말한다.
      cmp: CMPV ? { at: CMPV.at, n: CMPV.n, struct: CMPV.struct, ready: CMPV.ready, error: CMPV.error || null,
        marks: CMPV.marks.map((m) => ({ ...m })), seats: CMPV.seats.map((m) => ({ ...m })), undrawn: CMPV.undrawn, cam: CMPV.cam, focus: CMPV.focus, foc: CMPV.foc,
        noShape: CMPV.R.noShape.map((c) => c.target_id), noShapeLater: CMPV.R.noShapeLater.map((c) => c.target_id),
        orphans: CMPV.R.orphans.map((n) => n.id), lost: CMPV.lost.map((c) => c.target_id),
        folded: CMPV.lost.filter((c) => c.folded).map((c) => c.target_id),
        list: [...CMPV.el.querySelectorAll('.clist .cl')].map((x) => x.textContent) } : null,
      mode: S.mode, nHistory: S.history.length, hidden: [...HIDDEN].sort(), copy: COPY,
      // ---- 모니터링 축(ADR 0006 / 검사 94) ----
      // 🔴 집계는 **화면이 배너에 쓰는 것과 같은 함수**에서 나온다. 하네스 전용으로 다시 세면
      //    「배너는 맞고 그림은 틀린」 상태를 못 잡는다.
      monitor: S.monitor, obs: obsStats(L), obsStale: OBS_STALE, obsPair: OBS_PAIR, obsPairBy: OBS_PAIRING.by, obsPairNewer: OBS_PAIRING.newer || null,
      live: liveInfo(),
      // ---- 옵션형 근거 축(ADR 0009 D6 / 검사 100) ----
      // 🔴 **화면이 쓰는 같은 함수**에서 나온다. `shown` 은 **실제로 띄운** 알약이다(게이트까지 포함한다) —
      //    `null` 은 「말하지 않았다」이고 그것이 탐지 안 한 문서의 정답이다.
      src: (() => {
        const s = srcStats()
        return {
          rows: s.rows, pill: s.pill,
          shown: S.monitor && !OBS_STALE && s.rows ? s.pill : null,
          used: s.used.map((x) => x.kind), unread: s.unread.map((x) => x.kind),
          unknown: s.unknown.map((x) => x.kind), off: s.off.map((x) => x.kind),
          na: s.na.map((x) => x.kind)
        }
      })(),
      // ---- §3g 범위 축 (검사 91) ----
      // 🔴 `scope` 는 **배치가 실제로 쓴 값**(`L.scope`)이다. `S.scope` 를 쓰면 없는 계정 id 를
      //    요청했을 때 화면과 dump 가 갈린다.
      scope: L.scope || null,
      scopeAccts: accounts.map((a) => a.id),
      nXwire: L.nXwire
    })
  }
  window.__ready = true
})()
