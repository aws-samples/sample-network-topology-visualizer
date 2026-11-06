// 시나리오 v3: 계정 1개 · 리전 4개 · VPC 다수. VPC 안은 TGW / VPC Endpoint / ELB,
// 여기에 엔드포인트 확장 4종(원칙 §7b)을 더한다 —
//   ① 엔드포인트 기본 펼침  ② 대상 AWS 서비스(리전 열)  ③ PrivateLink 엔드포인트 서비스
//   ④ Route 53 Resolver 인바운드/아웃바운드 엔드포인트
// (흐름을 그리려면 없을 수 없는 subnet·IGW·NAT 는 포함)
// 계정ID는 마스킹된 가짜값(111122223333 계열) — 실계정 식별자는 쓰지 않는다.
//
// 확장은 이 파일에 리전/VPC를 더하거나 계정 축을 감싸는 방식으로만 한다.
// 캔버스 규칙은 docs/canvas-principles.md 가 정본이다.
;(function () {
  const ACCOUNT = { id: 'acct-prod-network', number: '111122223333', name: 'prod-network' }

  // 결정론적 가짜 리소스ID (§5 / 불변식 ②): 이름을 FNV-1a 로 해싱한다. random 금지.
  const rid = (prefix, seed) => {
    let h = 0x811c9dc5
    for (let i = 0; i < seed.length; i++) { h ^= seed.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0 }
    return prefix + '-' + h.toString(16).padStart(8, '0')
  }

  // 엔드포인트 짧은 이름 → 대상 AWS 서비스 카드 이름(§7b-②).
  // ecr.api / ecr.dkr 처럼 여러 엔드포인트가 한 서비스를 가리키면 카드는 하나고 선이 합쳐진다(§6.6).
  const SVC_LABEL = {
    s3: 'Amazon S3', dynamodb: 'Amazon DynamoDB',
    'ecr.api': 'Amazon ECR', 'ecr.dkr': 'Amazon ECR',
    logs: 'CloudWatch Logs', monitoring: 'Amazon CloudWatch',
    ssm: 'Systems Manager', ssmmessages: 'Systems Manager',
    secretsmanager: 'Secrets Manager', sts: 'AWS STS', kms: 'AWS KMS',  // pragma: allowlist secret
    elasticloadbalancing: 'ELB API'
  }
  const GATEWAY_EP = new Set(['s3', 'dynamodb'])
  const slug = (s) => s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')

  // 리전 정의. azs 는 VPC 별로 덮어쓸 수 있다.
  // lbs: [type, 이름, scheme] · eps: 엔드포인트 서비스 짧은 이름
  // plProvide: PrivateLink 제공(뒤에 붙는 NLB 이름) · plConsume: 소비할 엔드포인트 서비스 이름
  // resolver: Route 53 Resolver 인바운드/아웃바운드 엔드포인트를 이 VPC 에 둔다
  const REGIONS = [
    // onprem = 외부 열의 온프레미스 쪽(고객 게이트웨이 · Direct Connect). 둘 다 VGW 로 들어온다.
    // edge   = CloudFront 배포. ⚠️ CloudFront 는 글로벌 서비스인데 리전 레인 안에 그린다 —
    //          전용 글로벌 레인은 승인 대기 항목이다(docs §14).
    { code: 'us-east-1', label: '버지니아 북부', short: 'use1', asn: 64512, azs: ['a', 'b'],
      onprem: { ip: '203.0.113.24', asn: 65000,
                dx: { location: 'Equinix DC2 (Ashburn)', bw: '10 Gbps', vlan: 101 } },
      edge: { domain: 'd2k1ab34cdef.cloudfront.net', origin: 'edge-alb', waf: 'prod-edge-acl' },
      // 수집 범위 밖(다른 계정의 프랑크푸르트 리전 — REGIONS 에 없다)인데 TGW 피어링은
      // 실재한다 → 스텁으로 종단(§13-46). ⚠️ 여기 리전 코드는 REGIONS 에 없어야 한다.
      outOfScope: [{ region: 'eu-central-1', name: 'frankfurt-shared-vpc', cidr: '10.20.0.0/16',
                     via: 'TGW 피어링 · 다른 계정 (수집 대상 아님)' }],
      vpcs: [
      { name: 'egress-vpc', cidr: '10.0.0.0/16', igw: true, resolver: true, vgw: true,
        nfw: { name: 'egress-inspection', policy: 'egress-strict-policy' },
        ec2: [['bastion', 'public', 'a', 't3.small']],
        lbs: [['alb', 'edge-alb', 'internet-facing']], eps: [], sg: 14, nacl: 3, rtb: 6 },
      { name: 'app-vpc', cidr: '10.1.0.0/16', igw: false, azs: ['a', 'b', 'c'],
        asg: { name: 'app-asg', type: 'm6i.large', min: 3, desired: 6, max: 12 },
        eks: { name: 'orders-eks', version: '1.31', ng: 'ng-orders', ngType: 'm6i.xlarge', ngSize: 6 },
        ec2: [['batch-runner', 'private', 'a', 'c6i.large']],
        // 카디널리티 대조군 (§3d). 같은 서브넷에 EC2 137개 — 개별로 그리면 이 행 하나가
        // 9,308px 다. 135대(running)는 집합 카드 1개로 접히고, stopped 2대는 임계값(4) 아래라
        // 개별로 남는다 → "상태가 다르면 같은 집합이 아니다"가 눈에 보인다(검사 28·29).
        // 사용자 질문(2026-09-13): "134개 중 50여개가 어딘가와 연결돼 선이 그어져야 한다면?"
        // → 집합 키에 엣지 서명이 있으므로 «연결이 같은 것끼리» 집합이 갈린다: ×50(→ecr.api) ·
        //   ×30(→s3) · ×54(연결 없음). 모델 엣지 82개가 선 **4개**로 그려진다(검사 35·36).
        // 대장 88 판별력: 집합 카드 9개가 전부 「지배 개수 == 멤버 수」라 굵은 줄이 **어긋날 수
        // 없었다 = 미측정**이었다. `mix` 가 이질 타입 4대를 ×54 집합(82~135, 엣지 없음) 안에
        // 심어 `m6i.32xlarge ×50 +4` 를 만든다. 집합 키에 인스턴스 타입이 없으므로(§3d)
        // 카드 수·엣지·상태 분포는 불변이고, 굵은 줄만 어긋나는 상태가 된다.
        // 🔴 타입 이름이 `m6i.large`(라벨 16자)면 부착 카드 예산 17자에 들어가 **잘림 절이
        // 미측정**이다(`37-잘림` 브레이크가 PASS 했다). `m6i.32xlarge` 는 라벨을 19자로 만들어
        // 「잘림은 이름에서만」(대장 88 · `clipTail`)을 실제로 재게 한다 — 짧게 되돌리지 마라.
        ec2bulk: { tier: 'private', az: 'a', type: 'm6i.32xlarge', n: 135, stopped: 2,
          mix: { type: 'm6i.4xlarge', from: 82, to: 85 },
          wired: [['ecr.api', 2, 51], ['s3', 52, 81]], outlier: ['ecr.api', 's3'] },
        lbs: [['nlb', 'app-nlb', 'internal']], eps: ['s3', 'ecr.api', 'ecr.dkr', 'logs', 'ssm'],
        plProvide: { service: 'orders-api', lb: 'app-nlb' },
        sg: 31, nacl: 2, rtb: 5 },
      // AZ 정렬 노브(§13-64 azSort)의 대조군: 이 VPC 만 «b 가 a 보다 붐빈다».
      // 다른 VPC 는 모두 알파벳 첫 AZ 가 가장 붐벼서 이름순·밀도순 결과가 우연히 같았다
      // → 노브가 사실은 미측정이었다(실측: 검사 64 azSort 절이 «변화 없음»으로 FAIL).
      { name: 'data-vpc', cidr: '10.2.0.0/16', igw: false,
        ec2: [['data-etl', 'private', 'b', 'r6i.large']],
        lbs: [], eps: ['secretsmanager', 'monitoring'], plConsume: ['orders-api'],
        sg: 9, nacl: 2, rtb: 4 }
      ] },
    { code: 'us-west-2', label: '오레곤', short: 'usw2', asn: 64513, azs: ['a', 'b'], vpcs: [
      { name: 'dr-egress-vpc', cidr: '10.10.0.0/16', igw: true,
        lbs: [['alb', 'dr-alb', 'internet-facing']], eps: [], sg: 11, nacl: 2, rtb: 5 },
      { name: 'dr-app-vpc', cidr: '10.11.0.0/16', igw: false,
        lbs: [], eps: ['s3', 'logs'], sg: 17, nacl: 2, rtb: 4 }
    ] },
    { code: 'eu-west-1', label: '아일랜드', short: 'euw1', asn: 64514, azs: ['a', 'b'], vpcs: [
      { name: 'eu-app-vpc', cidr: '10.20.0.0/16', igw: false,
        lbs: [['alb', 'eu-alb', 'internal']], eps: ['s3', 'sts'], sg: 12, nacl: 2, rtb: 4 }
    ] },
    { code: 'ap-northeast-1', label: '도쿄', short: 'apne1', asn: 64515, azs: ['a', 'c'], vpcs: [
      { name: 'tokyo-egress-vpc', cidr: '10.30.0.0/16', igw: true,
        lbs: [['alb', 'tokyo-alb', 'internet-facing']], eps: [], sg: 10, nacl: 2, rtb: 5 },
      // 엔드포인트 9개 — 롤업 임계값(8)을 일부러 넘겨 접힘 축을 계속 검사할 수 있게 남긴다.
      { name: 'tokyo-app-vpc', cidr: '10.31.0.0/16', igw: false,
        lbs: [['nlb', 'tokyo-nlb', 'internal']],
        eps: ['s3', 'ecr.api', 'ecr.dkr', 'logs', 'monitoring', 'ssm', 'ssmmessages', 'sts', 'kms'],
        sg: 22, nacl: 2, rtb: 4 }
    ] }
  ]

  // TGW 피어링 (리전 간). 스파인 채널에서 세로로 그려진다.
  const PEERINGS = [
    ['us-east-1', 'us-west-2'],
    ['us-east-1', 'ap-northeast-1'],
    ['us-east-1', 'eu-west-1'],
    ['us-west-2', 'ap-northeast-1']
  ]

  const N = []
  const E = []
  let eid = 0
  const node = (o) => { N.push(o); return o.id }
  const link = (source, target, kind, attrs) => {
    E.push({ id: 'e' + (++eid), source, target, kind, attrs: attrs || {} })
  }

  node({ id: ACCOUNT.id, type: 'account', name: ACCOUNT.name, parent_id: null,
         attrs: { account: ACCOUNT.number } })

  for (const r of REGIONS) {
    const rid_ = 'r-' + r.code
    node({ id: rid_, type: 'region', name: r.code, parent_id: ACCOUNT.id, region: r.code,
           attrs: { label: r.label } })

    // 외부 열 = 리전 경계 **밖**. 인터넷은 인터넷 경계가 있을 때만 그린다 — 원칙 §2
    let ext = null
    if (r.vpcs.some((v) => v.igw)) {
      ext = 'ext-' + r.code
      node({ id: ext, type: 'internet', name: '인터넷', parent_id: rid_, region: r.code, tier: 'external',
             attrs: { CIDR: '0.0.0.0/0' } })
    }

    // 온프레미스 쪽: 고객 게이트웨이(VPN)와 Direct Connect 연결. 둘 다 VGW 로 들어간다.
    // 온프렘 장비는 우리가 수집하지 못하는 대상이므로 카드 1개로 요약한다(스텁).
    let cgw = null
    let dxc = null
    if (r.onprem) {
      cgw = 'cgw-' + r.code
      node({ id: cgw, type: 'customer_gateway', name: '온프레미스 (본사 DC)', parent_id: rid_,
             region: r.code, tier: 'external',
             attrs: { id: rid('cgw', cgw), 'public IP': r.onprem.ip, 'BGP ASN': r.onprem.asn,
                      'VPN 연결': '1개 · 터널 2 (서로 다른 AZ 종단)', '터널 상태': 'UP / UP',
                      '광고 프리픽스': '192.168.0.0/16' } })
      dxc = 'dx-' + r.code
      node({ id: dxc, type: 'dx_connection', name: 'DX ' + r.onprem.dx.bw, parent_id: rid_,
             region: r.code, tier: 'external',
             attrs: { id: rid('dxcon', dxc), '로케이션': r.onprem.dx.location,
                      '대역폭': r.onprem.dx.bw, 'VLAN': r.onprem.dx.vlan,
                      'virtual interface': 'private VIF → VGW', 'BGP ASN': r.onprem.asn,
                      // DX 게이트웨이는 리전이 없는 글로벌 자원이라 카드로 빼지 않고 줄로 적는다(§3c d)
                      'DX 게이트웨이': '미사용 (VGW 직결)' } })
    }

    // CloudFront: 원본(ALB)보다 앞이라 외부 열에 둔다. WAF 웹 ACL 은 노드가 아니라 속성 줄(§3c d).
    let cfd = null
    if (r.edge) {
      cfd = 'cf-' + r.code
      node({ id: cfd, type: 'cloudfront', name: r.edge.domain, parent_id: rid_, region: r.code,
             tier: 'external',
             attrs: { id: rid('E', cfd), '범위': '글로벌 (엣지 로케이션)', '원본': r.edge.origin,
                      '웹 ACL': r.edge.waf + ' (규칙 6)', 'OAC': '사용', '가격 클래스': 'All' } })
    }

    const tgw = 'tgw-' + r.code
    const tgwRid = rid('tgw', tgw)
    // TGW 라우트 테이블 — 자기 리전 VPC + 피어링 리전 요약 (§3b: 카드 안에서 접힘)
    const tgwRoutes = r.vpcs.map((v) => ({ d: v.cidr, t: v.name + ' attachment' }))
    for (const [a, b] of PEERINGS) {
      const other = a === r.code ? b : (b === r.code ? a : null)
      if (!other) continue
      const o = REGIONS.find((x) => x.code === other)
      tgwRoutes.push({ d: o.vpcs[0].cidr.replace(/\.\d+\.0\.0\//, '.0.0.0/'), t: 'peering ' + o.short })
    }
    node({ id: tgw, type: 'transit_gateway', name: 'tgw-' + r.short, parent_id: rid_, region: r.code,
           tier: 'spine',
           attrs: { id: tgwRid, asn: r.asn, attachments: r.vpcs.length,
                    route_table: rid('tgw-rtb', tgw), routes: tgwRoutes } })

    // --- 범위 밖 스텁 (§13-46) ---
    // 수집 범위(계정·리전 선택) 밖인데 **연결은 실재**하는 대상. 선을 캔버스 밖으로 흘리거나
    // 조용히 버리면 안 된다(불변식 ④: 있는데 없다고 그리면 사람이 오판한다) →
    // 자기 리전 레인 오른쪽 끝에 «범위 밖» 카드로 종단시킨다. 이 리전은 레인을 갖지 않는다.
    for (const os of (r.outOfScope || [])) {
      const sid = `stub-${r.code}-${slug(os.region)}`
      node({ id: sid, type: 'stub', name: os.name, parent_id: rid_, region: r.code, tier: 'stub',
             attrs: { region: os.region, via: os.via, CIDR: os.cidr,
                      '수집 여부': '아니오 — 대상 목록에 없는 계정/리전',
                      '알 수 있는 것': '연결의 존재와 광고 프리픽스뿐' } })
      link(tgw, sid, 'peering', { label: os.region })
    }

    // --- 리전 열: 엔드포인트 대상 AWS 서비스 (§7b-②) ---
    // VPC 밖·리전 안에 있는 것들이라 위치가 여기다. 같은 서비스는 리전에 카드 1개.
    const svcNodeOf = new Map()   // 짧은 이름 → 카드 id
    {
      const labels = new Set()
      for (const v of r.vpcs) for (const s of v.eps) labels.add(SVC_LABEL[s] || s)
      for (const label of [...labels].sort()) {
        const id = `svc-${r.code}-${slug(label)}`
        const users = r.vpcs.filter((v) => v.eps.some((s) => (SVC_LABEL[s] || s) === label)).length
        node({ id, type: 'aws_service', name: label, parent_id: rid_, region: r.code, tier: 'spine',
               attrs: { scope: r.code + ' 리전 서비스', 'VPC 엔드포인트': users + '개 VPC에서 연결' } })
      }
      for (const v of r.vpcs) for (const s of v.eps) svcNodeOf.set(s, `svc-${r.code}-${slug(SVC_LABEL[s] || s)}`)
    }

    // --- 리전 열: PrivateLink 엔드포인트 서비스 (§7b-③) ---
    // com.amazonaws.vpce.<region>.vpce-svc-… 는 리전 스코프다. 제공자 NLB 와 소비자 엔드포인트가
    // 양쪽에서 이 카드로 들어온다(VPC 안에 두면 소비자→제공자 선이 왼쪽으로 되짚어야 한다, §6.2).
    const esNodeOf = new Map()
    for (const v of r.vpcs) {
      if (!v.plProvide) continue
      const esid = `es-${r.code}-${slug(v.plProvide.service)}`
      const consumers = r.vpcs.filter((x) => (x.plConsume || []).includes(v.plProvide.service)).length
      node({ id: esid, type: 'endpoint_service', name: v.plProvide.service, parent_id: rid_,
             region: r.code, tier: 'spine',
             attrs: { id: rid('vpce-svc', esid), 'network load balancer': v.plProvide.lb,
                      'acceptance required': '예', consumers: consumers + '개 VPC' } })
      esNodeOf.set(v.plProvide.service, esid)
    }

    let vgwId = null              // 온프렘·DX 가 들어오는 문(리전에 하나만 둔다)
    const lbIds = new Map()       // 이름 → 로드밸런서 카드 id (CloudFront 원본 연결용)

    r.vpcs.forEach((v, vi) => {
      const azs = v.azs || r.azs
      const vid = `vpc-${r.code}-${v.name}`
      const vpcRid = rid('vpc', vid)
      node({ id: vid, type: 'vpc', name: v.name, parent_id: rid_, region: r.code,
             attrs: { id: vpcRid, cidr: v.cidr, 'AZ': azs.length,
                      'security group': v.sg, 'network ACL': v.nacl, 'route table': v.rtb } })

      let igw = null
      let igwRid = null
      if (v.igw) {
        igw = vid + '-igw'
        igwRid = rid('igw', igw)
        node({ id: igw, type: 'internet_gateway', name: v.name + '-igw', parent_id: vid,
               region: r.code, tier: 'edge', attrs: { id: igwRid, vpc: v.name } })
        link(ext, igw, 'flow')
      }

      // VGW 도 VPC 경계 부착이다(§3c c) — IGW 아래, VPC 왼쪽 변에 걸친다.
      // 서브넷 소속이 없다(API 에 SubnetId 가 없고 availabilityZone 은 비어 있을 수 있다).
      if (v.vgw) {
        vgwId = vid + '-vgw'
        node({ id: vgwId, type: 'virtual_private_gateway', name: 'vgw', parent_id: vid,
               region: r.code, tier: 'edge',
               attrs: { id: rid('vgw', vgwId), '터널': 2 } })
      }

      const priv = []
      azs.forEach((az, ai) => {
        const azName = r.code + az
        let natRid = null
        if (v.igw) {
          const sid = `${vid}-public-${az}`
          const nid = `${vid}-nat-${az}`
          natRid = rid('nat', nid)
          node({ id: sid, type: 'subnet', name: `${v.name}-public-${az}`, parent_id: vid,
                 region: r.code, az: azName, tier: 'public',
                 attrs: { id: rid('subnet', sid), cidr: v.cidr.replace('0.0/16', `${ai}.0/24`),
                          'public IP': 'auto', 'available IP': 251,
                          route_table: rid('rtb', sid),
                          routes: [
                            { d: v.cidr, t: 'local' },
                            { d: '0.0.0.0/0', t: igwRid },
                            { d: '10.0.0.0/8', t: tgwRid }
                          ] } })
          link(igw, sid, 'flow')
          // NAT 는 부착 노드다(§3c c): 자기 서브넷 카드의 우측 변에 걸쳐 그린다.
          // 소속을 선으로 잇지 않는다 — 위치가 소속을 말한다(사용자 2026-09-13).
          // 인터넷 경로는 그 서브넷의 IGW 선이, 사설 서브넷의 출구는 라우트 줄(t: natRid)이 보여준다.
          node({ id: nid, type: 'nat_gateway', name: `nat-${az}`, parent_id: vid,
                 region: r.code, az: azName, tier: 'public', attachedTo: sid,
                 attrs: { id: natRid, EIP: `3.212.${vi}.${10 + ai}` } })
        }
        // Network Firewall 은 AZ 별 **전용 서브넷**을 요구한다 → 서브넷 카드를 따로 만들고
        // 방화벽 엔드포인트를 그 카드에 부착한다(§3c c). 검사 대상 서브넷의 기본 경로가
        // 이 엔드포인트를 가리킨다 — 그래서 rid 를 서브넷보다 먼저 계산한다.
        const feid = v.nfw ? `${vid}-fwep-${az}` : null
        const feRid = feid ? rid('vpce', feid) : null

        const pid = `${vid}-private-${az}`
        const gwEp = v.eps.some((s) => GATEWAY_EP.has(s))
        node({ id: pid, type: 'subnet', name: `${v.name}-private-${az}`, parent_id: vid,
               region: r.code, az: azName, tier: 'private',
               attrs: { id: rid('subnet', pid), cidr: v.cidr.replace('0.0/16', `${10 + ai}.0/24`),
                        'available IP': 240 + ai,
                        route_table: rid('rtb', pid),
                        routes: [
                          { d: v.cidr, t: 'local' },
                          ...(feRid ? [{ d: '0.0.0.0/0', t: feRid }]
                            : natRid ? [{ d: '0.0.0.0/0', t: natRid }] : []),
                          { d: '10.0.0.0/8', t: tgwRid },
                          ...(gwEp ? [{ d: 'pl-63a5400a (S3)', t: rid('vpce', `${vid}-vpce-s3`) }] : [])
                        ] } })
        priv.push(pid)

        if (v.nfw) {
          const fsid = `${vid}-firewall-${az}`
          node({ id: fsid, type: 'subnet', name: `${v.name}-firewall-${az}`, parent_id: vid,
                 region: r.code, az: azName, tier: 'private',
                 attrs: { id: rid('subnet', fsid), cidr: v.cidr.replace('0.0/16', `${200 + ai}.0/28`),
                          '용도': 'Network Firewall 전용', 'available IP': 11,
                          route_table: rid('rtb', fsid),
                          routes: [{ d: v.cidr, t: 'local' },
                            ...(natRid ? [{ d: '0.0.0.0/0', t: natRid }] : [])] } })
          node({ id: feid, type: 'firewall_endpoint', name: `fw-${az}`, parent_id: vid,
                 region: r.code, az: azName, tier: 'private', attachedTo: fsid,
                 attrs: { id: feRid, AZ: az } })
        }
      })

      // --- compute 열 (§3c b): 여러 서브넷·AZ 에 걸쳐 있어 부착 노드가 될 수 없는 것들.
      // 소속을 선으로 잇지 않고 속성 줄로 적는다 — 라우팅 테이블과 같은 방식이다.
      let asgId = null
      if (v.asg) {
        asgId = `${vid}-asg`
        node({ id: asgId, type: 'auto_scaling_group', name: v.asg.name, parent_id: vid,
               region: r.code, tier: 'compute',
               attrs: { id: rid('asg', asgId), '인스턴스 타입': v.asg.type,
                        '최소/희망/최대': `${v.asg.min} / ${v.asg.desired} / ${v.asg.max}`,
                        '서브넷': `${azs.length}개 (AZ별 private)`, '헬스체크': 'ELB',
                        '조정 정책': 'target tracking · CPU 60%' } })
      }
      if (v.eks) {
        // 노드 그룹·제어 평면을 별개 카드로 빼지 않는다: 제어 평면은 AWS 관리형이라 계정에
        // 보이지 않고, 노드 그룹은 이 카드의 속성이다(라우팅 테이블 방식, 사용자 2026-09-13).
        const eid = `${vid}-eks`
        node({ id: eid, type: 'eks_cluster', name: v.eks.name, parent_id: vid, region: r.code,
               tier: 'compute',
               attrs: { id: rid('eks', eid), '버전': v.eks.version, '엔드포인트 접근': 'private',
                        '제어 평면': 'AWS 관리형 (계정 밖)',
                        '제어 평면 ENI': `${azs.length}개 (AZ별 private)`,
                        '노드 그룹': `${v.eks.ng} · ${v.eks.ngType} · ${v.eks.ngSize}대`,
                        'CNI': 'VPC CNI (파드가 서브넷 IP 사용)' } })
      }
      if (v.nfw) {
        const fid = `${vid}-nfw`
        node({ id: fid, type: 'network_firewall', name: v.nfw.name, parent_id: vid, region: r.code,
               tier: 'compute',
               attrs: { id: rid('firewall', fid), '정책': v.nfw.policy,
                        '엔드포인트': `${azs.length}개 (AZ별 전용 서브넷)`,
                        'stateful 규칙 그룹': 3, 'stateless 규칙 그룹': 1,
                        '로깅': 'flow + alert → CloudWatch Logs' } })
      }

      // EC2 인스턴스는 서브넷 하나에 매인다 → 그 서브넷 카드의 부착 노드다(§3c c).
      for (const [nm, tier, az, itype] of (v.ec2 || [])) {
        const iid = `${vid}-ec2-${nm}`
        node({ id: iid, type: 'ec2_instance', name: nm, parent_id: vid, region: r.code,
               az: r.code + az, tier, attachedTo: `${vid}-${tier}-${az}`,
               attrs: { id: rid('i', iid), type: itype } })
      }

      // 대량 EC2 (§3d 카디널리티). 이름은 zero-pad 해서 정렬이 결정론이 되게 한다.
      if (v.ec2bulk) {
        const bk = v.ec2bulk
        const mk = (i, state) => {
          const nm = `app-${String(i).padStart(3, '0')}`
          const iid = `${vid}-ec2-${nm}`
          // 대장 88 대조군: 구간 안이면 다른 인스턴스 타입. 집합 키는 안 바뀐다(§3d).
          const mx = bk.mix && i >= bk.mix.from && i <= bk.mix.to ? bk.mix.type : bk.type
          node({ id: iid, type: 'ec2_instance', name: nm, parent_id: vid, region: r.code,
                 az: r.code + bk.az, tier: bk.tier, attachedTo: `${vid}-${bk.tier}-${bk.az}`,
                 attrs: { id: rid('i', iid), type: mx, state } })
        }
        for (let i = 1; i <= bk.n; i++) mk(i, 'running')
        for (let i = 0; i < (bk.stopped || 0); i++) mk(bk.n + 1 + i, 'stopped')
        const inm = (i) => `${vid}-ec2-app-${String(i).padStart(3, '0')}`
        // 부분 연결 대조군: 구간별로 다른 엔드포인트에 붙는다 → 엣지 서명이 갈려 집합이 쪼개진다.
        for (const [ep, a, b] of bk.wired || []) {
          for (let i = a; i <= b; i++) link(inm(i), `${vid}-vpce-${ep}`, 'service')
        }
        // 아웃라이어 대조군 (§3d 정직성 규칙): 1대만 **두 곳에 동시에** 나간다 → 엣지 서명이
        // 유일해서 어느 집합에도 못 들어가고 개별 카드 + 선 2개로 남는다. 이 줄이 없으면
        // «연결이 달라도 묶는다»는 거짓말을 어떤 검사도 잡지 못한다(검사 29 의 엣지 절 판별력).
        for (const ep of [].concat(bk.outlier || [])) link(inm(1), `${vid}-vpce-${ep}`, 'service')
      }

      for (const [kind, nm, scheme] of v.lbs) {
        const lid = `${vid}-${nm}`
        lbIds.set(nm, lid)
        node({ id: lid, type: 'load_balancer', name: nm, parent_id: vid, region: r.code,
               tier: 'lb',
               attrs: Object.assign({ type: kind.toUpperCase(), scheme, AZ: azs.length,
                 listeners: kind === 'alb' ? 2 : 1,
                 'target group': asgId ? v.asg.name + ' (Auto Scaling)' : priv.length + '개 서브넷' },
                 // WAF 웹 ACL 은 노드가 아니라 이 카드의 속성이다(§3c d) — ALB 에 직접 연결된다
                 r.edge && scheme === 'internet-facing' ? { '웹 ACL': r.edge.waf } : {}) })
        if (igw && scheme === 'internet-facing') link(igw, lid, 'flow')
        // 대상이 Auto Scaling 그룹이면 서브넷마다 선을 뻗지 않는다 — 선이 3배로 늘고 뜻은 같다.
        if (asgId) link(lid, asgId, 'flow', { via: 'target group' })
        else for (const p of priv) link(lid, p, 'flow', { via: 'target group' })
        // PrivateLink 제공자: 이 NLB 가 리전 열의 엔드포인트 서비스 뒤에 붙는다(§7b-③)
        if (v.plProvide && v.plProvide.lb === nm) link(lid, esNodeOf.get(v.plProvide.service), 'service')
      }

      for (const svc of v.eps) {
        const gateway = GATEWAY_EP.has(svc)
        const pid2 = `${vid}-vpce-${svc}`
        const epRid = rid('vpce', pid2)
        node({ id: pid2, type: 'vpc_endpoint', name: `com.amazonaws.${r.code}.${svc}`, parent_id: vid,
               region: r.code, tier: 'service',
               attrs: Object.assign({ id: epRid, type: gateway ? 'Gateway' : 'Interface', service: svc },
                 gateway
                   // Gateway 엔드포인트는 라우트 테이블 항목으로만 존재한다 → 접히는 라우트 줄
                   ? { 'route table': priv.length + '개 연결',
                       routes: priv.map((p, i) => ({ d: 'pl-63a5400a (S3)', t: rid('rtb', p) })) }
                   : { ENI: priv.length, 'private DNS': '사용' }) })
        // Gateway 엔드포인트는 ENI 가 없다(라우트 테이블 기반) → 논리적 연결이라 점선
        link(priv[0], pid2, gateway ? 'logical' : 'service')
        // 엔드포인트 → 대상 AWS 서비스 (§7b-②)
        link(pid2, svcNodeOf.get(svc), 'service')
      }

      // PrivateLink 소비자: 이 VPC 안의 Interface 엔드포인트 → 리전 열의 엔드포인트 서비스
      for (const svcName of (v.plConsume || [])) {
        const cid = `${vid}-vpce-pl-${slug(svcName)}`
        node({ id: cid, type: 'vpc_endpoint', name: svcName, parent_id: vid, region: r.code,
               tier: 'service',
               attrs: { id: rid('vpce', cid), type: 'Interface (PrivateLink)', service: svcName,
                        ENI: priv.length, 'private DNS': '사용' } })
        link(priv[0], cid, 'service')
        link(cid, esNodeOf.get(svcName), 'service')
      }

      // Route 53 Resolver 엔드포인트 (§7b-④). 온프렘 DNS 대상은 지금 캔버스 밖이다(DX/VPN 없음).
      if (v.resolver) {
        for (const dir of ['inbound', 'outbound']) {
          const reid = `${vid}-r53-${dir}`
          node({ id: reid, type: 'route53_resolver_endpoint', name: `${v.name}-r53-${dir}`,
                 parent_id: vid, region: r.code, tier: 'service',
                 attrs: { id: rid('rslvr-' + (dir === 'inbound' ? 'in' : 'out'), reid),
                          direction: dir, 'IP 주소': priv.length + '개 (AZ별 ENI)',
                          '규칙': dir === 'outbound' ? '3개 (온프렘 존)' : '수신 전용',
                          '온프렘 대상': '캔버스 밖 — DX/VPN 미구성' } })
          link(priv[0], reid, 'service')
        }
      }

      // TGW attachment 는 VPC 오른쪽 경계에 걸치는 부착 노드다(§3c c) — VPC 가 TGW 로 나가는 문.
      // 어느 서브넷에 ENI 가 생겼는지는 선이 아니라 속성 줄(subnets)과 각 서브넷의 라우트 줄
      // (10.0.0.0/8 → tgw-attach-…)이 말한다. 밖으로 나가는 선 하나만 그린다.
      const att = `${vid}-tgwa`
      node({ id: att, type: 'transit_gateway_attachment', name: 'tgw attachment',
             parent_id: vid, region: r.code, tier: 'attach',
             attrs: { id: rid('tgw-attach', att), 'transit gateway': 'tgw-' + r.short,
                      subnets: priv.length } })
      link(att, tgw, 'attachment', { weight: priv.length })
    })

    // 외부 열 → 리전 안. 온프렘(VPN)과 DX(private VIF)는 같은 VGW 로 들어온다.
    if (vgwId) {
      if (cgw) link(cgw, vgwId, 'flow', { via: 'VPN' })
      if (dxc) link(dxc, vgwId, 'flow', { via: 'private VIF' })
    }
    // CloudFront → 원본 ALB. 인터넷→IGW 와 나란한 두 번째 진입 경로다 — 엔진과 같은 종류(`origin` · #43)라 새 도식이 화살표 선으로 긋는다
    if (cfd && lbIds.has(r.edge.origin)) link(cfd, lbIds.get(r.edge.origin), 'origin')
  }

  for (const [a, b] of PEERINGS) {
    link('tgw-' + a, 'tgw-' + b, 'peering', { state: 'available' })
  }

  // ---------------- 웨이브 2 픽스처 (§13-53~64) ----------------

  // §13-62 태그 (갭 26). 버킷 규칙 = 키 접두: `aws:` → AWS · `terraform` → Terraform ·
  // `kubernetes.io/`·`k8s.io/` → K8s · 나머지 → Custom. 태그가 0개면 Untagged.
  // 태그는 attrs 가 아니라 **top-level `tags`** 에 둔다 — attrs 에 넣으면 검색(matchQ)이 태그
  // 문자열까지 훑어 검사 48(검색 ≠ 필터)이 재는 집합이 조용히 바뀐다.
  const TAGGED = new Set(['vpc', 'subnet', 'ec2_instance', 'load_balancer', 'nat_gateway',
    'auto_scaling_group', 'eks_cluster', 'transit_gateway'])
  const PROD_R = new Set(['us-east-1', 'us-west-2'])
  for (const n of N) {
    if (!TAGGED.has(n.type)) continue
    const t = { Name: n.name, environment: PROD_R.has(n.region) ? 'prod' : 'stage',
      owner: 'network-team' }
    if (n.type === 'vpc' || n.type === 'transit_gateway') t['aws:cloudformation:stack-name'] = 'net-baseline'
    if (n.type === 'ec2_instance' || n.type === 'auto_scaling_group') t['terraform:module'] = 'ec2-app'
    if (n.type === 'eks_cluster' || (n.type === 'subnet' && n.tier === 'public')) t['kubernetes.io/role/elb'] = '1'
    n.tags = t
  }

  // §13-54 부분 실패 (갭 13 · 리포 불변식 ④). 수집이 못 본 영역은 **캔버스에 표식으로** 남는다.
  // 조용히 빈 그래프를 그리는 것이 최악이다 — 있는데 없다고 그리면 사람이 오판한다.
  const PARTIAL = [
    { target: 'vpc-us-west-2-dr-app-vpc', scope: 'vpc', code: 'access_denied',
      reason: 'ec2:DescribeNetworkInterfaces 거부 — ENI 수를 확인하지 못했다' },
    // `rtype` = 어댑터가 coverage 의 `resource_type` 을 넘기는 자리(§3k K5 · `adapt-core.js` 부분 실패).
    { target: 'r-ap-northeast-1', scope: 'region', code: 'throttled', rtype: 'route_table',
      reason: 'ec2:DescribeRouteTables 요청 한도 초과 — 라우트 일부가 누락됐을 수 있다' }
  ]

  // §13-55 비시각화 리소스 (갭 16). SG·NACL·라우트 테이블은 캔버스에 그리면 선이 폭발하고
  // (규칙은 위치가 없다) 없으면 조사가 끊긴다 → **캔버스 밖 표**로 뺀다. 상자 수는 불변이어야
  // 하므로 `nodes` 에 넣지 않는다(검사 55).
  const LIST = []
  const LIST_KIND = { security_group: ['sg', 'sg'], network_acl: ['acl', 'nacl'],
    route_table: ['rtb', 'rt'] }
  for (const n of N) {
    if (n.type !== 'vpc') continue
    const a = n.attrs
    const add = (type, cnt, det) => {
      for (let i = 0; i < cnt; i++) {
        const [pfx, sfx] = LIST_KIND[type]
        LIST.push({ id: rid(pfx, n.id + type + i), type, name: `${n.name}-${sfx}-${i + 1}`,
          vpc: n.name, vpcId: n.id, region: n.region, detail: det(i) })
      }
    }
    add('security_group', a['security group'], (i) => `인바운드 ${2 + (i % 4)} · 아웃바운드 1`)
    add('network_acl', a['network ACL'], (i) => i ? '서브넷 2개 연결' : '기본 ACL · 서브넷 전부')
    add('route_table', a['route table'], (i) => `라우트 ${3 + (i % 3)}개`)
  }

  window.TOPO = {
    run: { account: ACCOUNT.number, collected_at: '2026-09-13T00:00:00Z', scenario: 'single-account-multi-region' },
    nodes: N,
    edges: E,
    partial: PARTIAL,
    listOnly: LIST
  }

  // §13-63 두 번째 스냅샷 (갭 34). 라이브/스냅샷 게이트를 재려면 «다른 시각의 같은 계정»이
  // 있어야 한다. 델타는 3건뿐이다: 엔드포인트 1개 제거 · EC2 1대 추가 · 라우트 테이블 수 변경.
  // 노드 모양은 기존 노드를 복제해서 만든다 — 손으로 새 모양을 쓰면 픽스처만 틀린다.
  const T2 = { nodes: JSON.parse(JSON.stringify(N)), edges: JSON.parse(JSON.stringify(E)) }
  const drop = T2.nodes.filter((n) => n.type === 'vpc_endpoint')
    .sort((a, b) => a.id.localeCompare(b.id)).pop()
  if (drop) {
    T2.nodes = T2.nodes.filter((n) => n.id !== drop.id)
    T2.edges = T2.edges.filter((e) => e.source !== drop.id && e.target !== drop.id)
  }
  const proto = T2.nodes.find((n) => n.type === 'ec2_instance')
  if (proto) {
    const c = JSON.parse(JSON.stringify(proto))
    c.id = proto.id + '-t2'; c.name = 'app-new-001'
    T2.nodes.push(c)
  }
  const rtbVpc = T2.nodes.find((n) => n.type === 'vpc')
  if (rtbVpc) rtbVpc.attrs['route table'] = rtbVpc.attrs['route table'] + 1

  window.TOPO_T2 = {
    run: { account: ACCOUNT.number, collected_at: '2026-09-14T09:20:00Z', scenario: 'single-account-multi-region' },
    nodes: T2.nodes,
    edges: T2.edges,
    partial: PARTIAL,
    listOnly: LIST
  }

  // ---- 대장 94·101 관측 변경 이력 (ADR 0006 `changes[]`) ----
  // 🔴 `fixtures/observation/*.json` 을 그대로 읽어 쓰지 않는다. 그쪽 `target_id` 는 엔진의 복합
  //    id(`subnet/111122223333/us-east-1/subnet-0aaa…`)이고 이 픽스처의 노드 id 는 슬러그다 —
  //    **두 id 공간이 다른 것 자체가 엔진↔스키마 계약의 증거**라, 관측 픽스처를 렌더 id 로 고쳐
  //    쓰면 그 증거가 사라진다. 제품에서는 수집기가 두 공간을 맞춰 굽는다.
  // 🔴 id 를 손으로 적지 마라 — 노드 id 생성 규칙이 바뀌면 여기만 낡아 「모델에 없는 id」가 되고,
  //    그러면 검사 85 ③(그림이 실제로 달라진다)이 조용히 무측정이 된다.
  const pickId = (type) => {
    const c = N.filter((n) => n.type === type).map((n) => n.id).sort()
    return c.length ? c[c.length - 1] : null
  }
  // 기본 화면에서 **보이는** 리전(첫 리전)을 우선한다 — 접힌 리전에만 붙이면 글리프·초록 흐름을
  // 사람이 화면에서 확인할 수 없다(하네스는 DOM 으로 재므로 통과하고, 제품 화면은 미측정이 된다).
  const R0 = REGIONS[0].code
  const pickIdR0 = (type) => {
    const c = N.filter((n) => n.type === type && n.id.includes(R0)).map((n) => n.id).sort()
    return c.length ? c[c.length - 1] : pickId(type)
  }
  // 되짚기 가능성이 변경 종류마다 다르다 — 그 차이를 화면이 말해야 한다(대장 94 의 한계).
  const OBS_ADD = pickId('ec2_instance')                            // 되짚으면 숨긴다
  const OBS_ST = pickIdR0('nat_gateway') || pickId('vpc')           // 되짚으면 그때 판정으로 그린다
  const OBS_GONE = (pickId('vpc') || 'vpc') + '-retired-endpoint'   // 지금 모델에 없다 = 복원 불가
  const OBS_LIVE = pickIdR0('load_balancer')                        // 흐름이 관측된다(2층 근거)
  const OBS_PART = pickIdR0('transit_gateway')                      // 일부만 읽었다 → `~`
  const OBS_NONE = pickIdR0('vpc_endpoint')                         // 관측 수단이 없다 → `?`
  const OBS_NULLC = pickIdR0('eks_cluster') || pickIdR0('subnet')   // 오류 수를 **세지 못했다**(null ≠ 0)
  // ---- 흐름이 관측된 «선» (§8.3 「선의 초록 + 흐르는 점」 · §6.10 v4) ----
  // 🔴 선의 관측은 **노드와 다른 id 공간**이다(엔진 `"<edge_type>|<source>|<target>"`, 계약
  //    `$defs/edge_id`). 캔버스의 흐름 선은 라우트·타깃그룹 같은 관계를 **재투영**한 것이라 캔버스
  //    엣지 id 와 1:1 이 아니고, 그래서 제품에서는 어댑터가 「이 선이 어느 관계에서 왔나」를
  //    `attrs.obs_edges` 로 실어 보낸다(`adapt-engine-snapshot.mjs` 의 `routeEdgeIds`). 픽스처도
  //    **같은 경로**를 지나게 한다 — 안 그러면 초록 흐름은 하네스 주입에서만 나오고, 픽스처
  //    산출물(`flow-standalone.html`)에서는 원리적으로 한 번도 안 보인다.
  // 🔴 id 를 손으로 적지 마라(위 `pickId` 와 같은 이유) — **만들어진 엣지에서** 골라 붙이고,
  //    엔진 id 도 흉내내지 않고 **계약 규칙대로** 만든다(`$defs/edge_id` =
  //    `<edge_type>|<node_id>|<node_id>` · `$defs/node_id` = `<type>/<account>/…`. `edge_type` 은
  //    `topology.schema.json` `$defs/edge_type` enum 안의 값만 쓴다). 정렬은 `localeCompare` 가
  //    아니라 바이트 비교다 — 로케일에 따라 순서가 바뀌면 불변식 ②(바이트 동일)가 깨진다.
  const flowEdges = E.filter((e) => e.kind === 'flow')
    .sort((a, b) => (a.source + '|' + a.target < b.source + '|' + b.target ? -1 : 1))
  const nodeTypeOf = new Map(N.map((n) => [n.id, n.type]))
  const engineNid = (id) => (nodeTypeOf.get(id) || 'stub') + '/' + ACCOUNT.number + '/' + R0 + '/' + id
  const engineEid = (t, e) => t + '|' + engineNid(e.source) + '|' + engineNid(e.target)
  // 기본 화면에서 **보이는** 리전(첫 리전)의 선을 고른다 — 접힌 리전에 붙이면 픽스처 산출물에서
  // 초록 흐름을 눈으로 확인할 수 없다.
  const pickFlow = (via) => flowEdges.filter((e) => (e.attrs.via || null) === via &&
    (e.source.includes(R0) || e.target.includes(R0)))[0] || null
  const wRoute = pickFlow(null)               // 라우트 재투영 → 엔진 `route`
  const wTg = pickFlow('target group')        // 타깃그룹 → 엔진 `load_balancer_placement`
  // 첫째 = 흐름이 관측된다(초록 + 흐르는 점). 둘째 = 관측은 됐지만 `down` 이다 → **초록이 아니다**
  // (「엣지 관측이 붙었으면 초록」이 아니라 「`live` 여야 초록」임을 픽스처가 스스로 보인다).
  const OBS_W_LIVE = wRoute ? engineEid('route', wRoute) : null
  const OBS_W_DOWN = wTg ? engineEid('load_balancer_placement', wTg) : null
  if (wRoute) wRoute.attrs = { ...wRoute.attrs, obs_edges: [OBS_W_LIVE] }
  if (wTg) wTg.attrs = { ...wTg.attrs, obs_edges: [OBS_W_DOWN] }
  // 🔴 **선 자신의 관측이 `up` 인 두 경우를 구별해야 한다** — 실계정에서 이 구별이 화면을 좌우한다
  //    (2026-09-24 실측: 흐름선 57개 전부가 `ec2:DescribeRouteTables` 근거의 `up` 을 달고 있어
  //    「이 선을 못 봤다」가 「흐름 없음」으로 읽혔고, 종단에 `live` 가 13곳인데 초록 선이 0개였다).
  //    ① 구조 조회만 한 `up` = 흐름에 대해 **아무 말도 하지 않는다** → 종단 `live` 로 초록(근거 ②).
  //    ② 지표를 보고 난 `up` = 「봤고 흐름이 없었다」 → 종단이 `live` 여도 **초록이 아니다**.
  //    두 선이 없으면 `flow.js` 의 그 분기는 실패할 수 없는 어서션이 된다(검증 규칙).
  const etype = (e) => (e.attrs && e.attrs.via === 'target group' ? 'load_balancer_placement' : 'route')
  const liveEnd = (e) => [e.source, e.target].some((id) => id === OBS_LIVE || id === OBS_PART)
  const cand = flowEdges.filter((e) => e !== wRoute && e !== wTg && liveEnd(e) &&
    (e.source.includes(R0) || e.target.includes(R0)))
  const wUpRaw = cand[0] || null
  // 두 번째는 **첫째와 다른 상자 쌍**이어야 한다 — 같은 쌍이면 선 집계가 한 선으로 합쳐 두 행이
  // 같은 선에 붙고 구별이 사라진다.
  const wUpMet = cand.find((e) => wUpRaw && e.source !== wUpRaw.source && e.target !== wUpRaw.target) || null
  const OBS_W_UPRAW = wUpRaw ? engineEid(etype(wUpRaw), wUpRaw) : null
  const OBS_W_UPMET = wUpMet ? engineEid(etype(wUpMet), wUpMet) : null
  if (wUpRaw) wUpRaw.attrs = { ...wUpRaw.attrs, obs_edges: [OBS_W_UPRAW] }
  if (wUpMet) wUpMet.attrs = { ...wUpMet.attrs, obs_edges: [OBS_W_UPMET] }
  // 🔵 **비흐름선이 자기 지표로 초록이 되는 경우**(추가 2026-09-25 — 그 전까지 이 조건이 픽스처에
  //    없어서 해당 축이 미측정이었다). 부착선을 고르는 이유는 그것이 **실제로 직접 측정되는 구간**이라
  //    그렇다: TGW 어태치먼트 지표는 차원에 어태치먼트 id 를 달고 있어서 「이 부착을 지난 바이트」를
  //    구간 이름과 함께 센다(ADR 0008 D8-1). 라우트 재투영 선과 달리 **추론이 아니다.**
  // 🔴 이 한 줄이 없으면 「선 자신의 지표는 종류를 가리지 않는다」가 실패할 수 없는 어서션이 되고,
  //    제품 쪽 종류 게이트가 다시 ① 을 삼켜도 하네스가 초록으로 통과한다(검사 95 가 그 사실을 센다).
  const wAtt = E.filter((e) => e.kind === 'attachment' &&
    (e.source.includes(R0) || e.target.includes(R0)))
    .sort((a, b) => (a.source + '|' + a.target < b.source + '|' + b.target ? -1 : 1))[0] || null
  const OBS_W_ATT = wAtt ? engineEid('attachment', wAtt) : null
  if (wAtt) wAtt.attrs = { ...wAtt.attrs, obs_edges: [OBS_W_ATT] }
  // 🔵 **등급 B(연역)** — 지표를 «다른 자원에서» 재고 「이 구간을 지나야만 그 값이 나온다」로 판정한
  //    행. 엔진은 어태치먼트 지표 **한 벌**로 두 구간을 판정하고 둘째(어태치먼트↔VPC)에만
  //    `evidence.observed_on` 을 적는다(`engine/src/nettopo/metrics.py:819`·`:903`) — 그래서 이
  //    키의 **존재**가 곧 B 다(`observe.py:319`: 같으면 아예 안 적는다).
  // 🔴 `wAtt` 와 **다른 상자 쌍**이어야 한다(위 `:622` 와 같은 함정) — 같은 쌍이면 두 행이 한 선에
  //    앉고 상태 랭크가 센 쪽만 남아 B 가 사라진다. 이 행이 없으면 「B 를 A 로 말하지 않는다」는
  //    어서션은 실패할 수 없다(A 만 있는 화면에서 그 분기는 밟히지 않는다).
  // 🔴 조건은 「양 끝이 **둘 다** 다르다」가 아니라 「**쌍**이 다르다」다 — 부착선은 한쪽 끝이 전부
  //    같은 TGW 라서 전자로 쓰면 후보가 0개가 되고, 행은 `target_id: null` 이 돼 조용히 버려진다
  //    (`.filter((o) => o.target_id)`). 실측 2026-09-26: 그렇게 써서 등급 B 가 0개였다.
  const wAtt2 = E.filter((e) => e.kind === 'attachment' && e !== wAtt && wAtt &&
    (e.source !== wAtt.source || e.target !== wAtt.target) &&
    (e.source.includes(R0) || e.target.includes(R0)))
    .sort((a, b) => (a.source + '|' + a.target < b.source + '|' + b.target ? -1 : 1))[0] || null
  const OBS_W_DEDUCE = wAtt2 ? engineEid('attachment', wAtt2) : null
  if (wAtt2) wAtt2.attrs = { ...wAtt2.attrs, obs_edges: [OBS_W_DEDUCE] }
  // 🔵 **등급 C(경로 성립)** — 흐름을 재지는 못했고 흐를 수 있는 조건 8개를 **전부 읽어 전부 충족**
  //    했다(ADR 0008 D5). 계약 키는 엣지 행의 `path_ok`(`true` 만 허용 · `observation.schema.json:262`).
  // 🔴 종단이 `live` 가 **아닌** 흐름선을 고른다 — 「양 끝만 흐른다」 절과 섞이면 한 선이 두 이유를
  //    동시에 달아 어느 절이 그 선을 세는지 사람이 못 가른다.
  // 🔴 **엔진은 지금 이 키를 한 번도 쓰지 않는다**(실측 2026-09-26: `path_ok` 는 스키마에만 있다) →
  //    이 행은 「화면이 C 를 그릴 수 있나」만 재고, 「엔진이 C 를 낼 수 있나」는 **미측정**이다(S2-2).
  // 🔴 **같은 칸 쌍도 피한다**(#31·#32 리뷰 후속) — 객체만 다르고 양 끝 칸이 같은 엣지를 고르면 화면에서
  //    **한 선**으로 합쳐져 두 등급을 동시에 달고, 오라클은 그중 하나만 본다. 지금 픽스처에는 그런 쌍이
  //    없어 결과는 같다(출력 해시 불변 확인) — 픽스처가 자라도 조용히 겹치지 않게 막는다.
  const takenPair = (e) => [wRoute, wTg, wUpRaw, wUpMet].some((x) => x && x.source === e.source && x.target === e.target)
  const wPathOk = flowEdges.find((e) => e !== wRoute && e !== wTg && e !== wUpRaw && e !== wUpMet &&
    !takenPair(e) && !liveEnd(e) && (e.source.includes(R0) || e.target.includes(R0))) || null
  const OBS_W_PATHOK = wPathOk ? engineEid(etype(wPathOk), wPathOk) : null
  if (wPathOk) wPathOk.attrs = { ...wPathOk.attrs, obs_edges: [OBS_W_PATHOK] }
  // 🆕 **등급 A0(측정: 흐름 없음)** · 대조군 — ADR 0008 「D1 개정」(2026-10-02 · 랩 F5). 둘 다 `up` + 지표 + 0 아닌 점 0개이고
  //    갈리는 것은 **누구를 쟀나** 하나다: 그 구간 자신(A0) · 인접 자원(`observed_on` ≠ 대상 → E 그대로 — 「인접 > 0 ⇒ 지났다」의
  //    역은 성립하지 않는다). 대조군이 없으면 `observed_on` 조건을 지워도 아무것도 FAIL 하지 않는다.
  const takenQ = (e) => takenPair(e) || (wPathOk && e.source === wPathOk.source && e.target === wPathOk.target)
  const wQuiet = flowEdges.find((e) => !takenQ(e) && !liveEnd(e) && (e.source.includes(R0) || e.target.includes(R0))) || null
  const wQuietAdj = flowEdges.find((e) => e !== wQuiet && !takenQ(e) && !liveEnd(e) &&
    !(wQuiet && e.source === wQuiet.source && e.target === wQuiet.target) && (e.source.includes(R0) || e.target.includes(R0))) || null
  // 대조군 2 — 지표 이름은 있는데 점을 못 받았다(`datapoints: null`) → 「안 쟀음」이라 E. 「쟀고 0」과 섞이면 못 잰 선이 조용하다고 보인다.
  //    🔴 종단이 `live` 인 선에서 고른다 — `!liveEnd` 후보는 하나뿐이고 그것은 검사 95 판별 조건 ⓑ(종단 live 아닌 흐름선)의 몫이다(실측 2026-10-02:
  //    그것을 먹으면 ⓑ 가 0 이 된다). 종단 추론은 폐기됐으므로(ADR 0008) 종단이 live 여도 이 선은 초록이 아니어야 한다 — 그것도 같이 잰다.
  const wQuietNull = flowEdges.find((e) => !(e.attrs && e.attrs.obs_edges) && !takenQ(e) && liveEnd(e) &&
    ![wQuiet, wQuietAdj].some((w) => w && e.source === w.source && e.target === w.target) && (e.source.includes(R0) || e.target.includes(R0))) || null
  const OBS_W_QUIET = wQuiet ? engineEid(etype(wQuiet), wQuiet) : null
  const OBS_W_QUIETNULL = wQuietNull ? engineEid(etype(wQuietNull), wQuietNull) : null
  const OBS_W_QUIETADJ = wQuietAdj ? engineEid(etype(wQuietAdj), wQuietAdj) : null
  if (wQuiet) wQuiet.attrs = { ...wQuiet.attrs, obs_edges: [OBS_W_QUIET] }
  if (wQuietAdj) wQuietAdj.attrs = { ...wQuietAdj.attrs, obs_edges: [OBS_W_QUIETADJ] }
  if (wQuietNull) wQuietNull.attrs = { ...wQuietNull.attrs, obs_edges: [OBS_W_QUIETNULL] }
  // 🔴 **흐름선 종단에 `live` 가 아닌 관측**이 하나는 있어야 한다. 없으면 「`up` 을 흐름으로 승격하지
  //    않는다」(구조 조회가 됐다 ≠ 트래픽이 흐른다)는 규칙이 **실패할 수 없는 어서션**이 된다 —
  //    2026-09-24 실측: 브레이크 `95-v4 up승격` 이 픽스처에 그 조건이 없어서 PASS(죽은 브레이크)였다.
  const pickFlowEnd = (type) => {
    const c = []
    for (const e of flowEdges) {
      for (const id of [e.source, e.target]) if (nodeTypeOf.get(id) === type && id.includes(R0)) c.push(id)
    }
    const s = c.sort()
    return s.length ? s[s.length - 1] : null
  }
  const OBS_FLOWEND = pickFlowEnd('subnet')
  // 🔴 이것은 관측 문서의 **부분집합**이다 — 렌더러가 실제로 읽는 것(`run`·`layers`·`history`·
  //    `changes`·`observations`)만 담는다. 🔴 `run` 의 세 키는 계약이 **required** 로 박은 것이고
  //    (`$defs.run`, `additionalProperties: false`) 화면이 전부 읽는다: `observed_at` 은 배너의
  //    「기준 …」이고, `topology_collected_at` 은 **짝 검사**다. 2026-09-24 까지 여기엔 `account`·
  //    `collected_at` 이 들어 있었다 — 계약에 없는 키라서 배너가 「기준 undefined」를 찍고 있었다.
  //    `topology_collected_at` 은 `window.TOPO.run.collected_at` 과 **같아야** 한다(다르면 화면이
  //    겹쳐 그리기를 거부하는 것이 정답이다 → 그 경로는 검사 94 가 주입으로 잰다).
  window.OBS = {
    run: { observed_at: '2026-09-13T00:05:00Z', tool_version: 'fixture',
      topology_collected_at: '2026-09-13T00:00:00Z' },
    // 🔴 지표 층은 **켜져 있다**(2026-09-24 변경, 이전 판은 `metric: false`). 계약이
    //    「`metric: false` 면 어떤 observation 도 `live` 일 수 없다」로 못 박았으므로
    //    (`$defs/layers.metric`), 꺼 두면 아래 `live` 행들은 **엔진이 만들 수 없는 문서**가 되고
    //    초록 흐름 선은 픽스처에서 원리적으로 못 나온다. 호출당 과금은 **실계정 수집**의 이야기이고
    //    픽스처는 「2층까지 켜고 받은 문서」를 흉내내는 것이 맞다(ADR 0005 의 기본 꺼짐은 수집기
    //    기본값이다). 끈 쪽 배너 문장은 검사 94 ⑨ 가 문서를 바꿔 잰다.
    layers: { describe: true, metric: true },
    history: { baseline_at: '2026-09-12T22:00:00Z', resolution_seconds: 300,
      retention_seconds: 604800, truncated: true, flap_collapse_threshold: 4, max_changes: 5000 },
    // 시각 **오름차순**. 🔴 가장 최신 변경이 `resource_added` 여야 어떤 과거 행을 골라도 그림이
    //    실제로 달라진다(되짚기 = 「그 변경 직전」이므로 최신 변경은 항상 되짚힌다).
    // 🔴 `target_kind` 는 **리소스 종류가 아니다** — `schema/observation.schema.json` 의 enum 은
    //    `["node","edge"]` 뿐이고, `status_changed` 의 `from`/`to` 는 `$defs/status`
    //    `["live","up","down","none"]` 뿐이다. 2026-09-24 까지 이 픽스처는 `'nat_gateway'`·
    //    `'vpc_endpoint'`·`'degraded'` 를 쓰고 있었다 — **엔진이 만들 수 없는 문서**였고, 그래서
    //    검사 85 는 「되짚지 못한다」 경로만 재고 있었다(되짚기 조건 `target_kind === 'node'` 가
    //    항상 거짓이었다 = 통과가 아니라 미측정 — 검증 규칙). 계약 밖 값을 여기 되살리지 마라.
    changes: [
      { at: '2026-09-12T22:35:00Z', kind: 'status_changed', target_id: OBS_ST,
        target_kind: 'node', from: 'live', to: 'down', repeat_count: 2 },
      // 흐름 멈춤·시작 — 목록의 시각(`at`)은 **확인한 때**이고 실제로 멈춘/시작한 분은
      // `occurred_at` 이다(ADR 0008 D6 보강). 실측 간격 그대로다: 멈춤 19분 · 시작 9분 뒤에 확인했다.
      { at: '2026-09-12T23:00:00Z', kind: 'status_changed', target_id: OBS_LIVE,
        target_kind: 'node', from: 'live', to: 'up', repeat_count: 1,
        occurred_at: '2026-09-12T22:41:00Z' },
      { at: '2026-09-12T23:10:00Z', kind: 'resource_removed', target_id: OBS_GONE,
        target_kind: 'node', from: null, to: null, repeat_count: 1 },
      { at: '2026-09-12T23:30:00Z', kind: 'status_changed', target_id: OBS_LIVE,
        target_kind: 'node', from: 'up', to: 'live', repeat_count: 1,
        occurred_at: '2026-09-12T23:21:00Z' },
      { at: '2026-09-12T23:50:00Z', kind: 'resource_added', target_id: OBS_ADD,
        target_kind: 'node', from: null, to: null, repeat_count: 1 }
    ],
    // ---- 관측 행 (2026-09-24 신설) ----
    // 🔴 여기가 비어 있던 동안 픽스처 산출물은 **모니터링을 켜도 카드 테두리만 사라졌다** — 항목
    //    0개는 「그릴 상태가 없다」이므로 그것이 정답 동작이지만, 그래서 사용자가 보는 화면에서
    //    초록 흐름·`?`·`~`·`▲` 는 **하나도 나올 수 없었다**(하네스 주입에서만 났다 = 제품 화면은
    //    미측정 — 검증 규칙). 다섯 어휘 조합을 한 문서 안에 둔다.
    // 🔴 계약의 조건부 required 를 전부 지킨다(`$defs/observation.allOf`): `none` ⇒ `why` ·
    //    `live` ⇒ `evidence{api,metric,datapoints≥1}` · `partial:true` ⇒ `partial_reasons` 1개 이상.
    //    `error_count` 는 **required** 이고 `0`(에러 없음)과 `null`(세지 못했음)은 다른 사실이다.
    // 🔴 카드 행의 `target_id` 는 **캔버스 슬러그**라 계약의 `$defs/node_id` 패턴과 어긋난다 —
    //    위 `pickId` 주석의 이유로 **의도된 것**이다(두 id 공간이 다른 것 자체가 계약의 증거이고,
    //    `changes[]` 도 처음부터 같은 슬러그를 쓴다). 반대로 **선 행은 엔진 id 꼴**이다 — 제품에서
    //    어댑터가 엔진 엣지 id 를 `attrs.obs_edges` 로 그대로 실어 보내기 때문이다. 이 비대칭을
    //    「고쳐서」 양쪽을 같은 꼴로 만들면 선 관측이 지나는 경로가 사라진다.
    observations: [
      // 선: 흐름이 관측됐다 → 초록 + 흐르는 점(그 선 **자신의** 관측이므로 근거 2단 중 ①이다).
      { target_id: OBS_W_LIVE, target_kind: 'edge', status: 'live', why: null,
        error_count: 0, partial: false,
        evidence: { api: 'cloudwatch:GetMetricData', metric: 'BytesOutToDestination',
          datapoints: 11, window_seconds: 300 } },
      // 선: 관측은 됐지만 `down` → 초록이 **아니다**. 화면은 선 패널에서 그 사실을 말한다.
      { target_id: OBS_W_DOWN, target_kind: 'edge', status: 'down', why: null,
        error_count: 4, partial: false,
        evidence: { api: 'elasticloadbalancing:DescribeTargetHealth', metric: null,
          datapoints: null, window_seconds: null } },
      // 선: **구조 조회만** 한 `up`(지표 층 없음) → 흐름에 대해 아무 말도 하지 않으므로 종단의
      // `live` 로 초록이 된다(근거 ②). 실계정 흐름선 전부가 이 꼴이다.
      { target_id: OBS_W_UPRAW, target_kind: 'edge', status: 'up', why: null,
        error_count: 0, partial: false,
        evidence: { api: 'ec2:DescribeRouteTables', metric: null, datapoints: null,
          window_seconds: null } },
      // 선: **지표를 보고 난** `up` → 「봤고 흐름이 없었다」가 직접 관측이므로 종단이 `live` 여도
      // 초록이 **아니다**(추론이 직접 관측을 덮으면 없는 흐름을 그린다 = 불변식 ③).
      // 🔵 **버린 패킷은 「막혔다」가 아니다**(S1 · ADR 0008 D3). 갓 만든 NAT 이 `PacketsDropCount` 에
      //    값을 내는 것은 정상이고, 그것으로 차단 판정을 하면 화면이 정상 자원을 막혔다고 말한다.
      //    그래서 이 행은 `drops` 를 들고도 `status: 'up'` 이다 — 화면은 수를 **적기만** 한다.
      { target_id: OBS_W_UPMET, target_kind: 'edge', status: 'up', why: null,
        error_count: 0, partial: false, drops: 7,
        evidence: { api: 'cloudwatch:GetMetricData', metric: 'BytesOutToDestination',
          datapoints: 12, window_seconds: 300 } },
      // 선(**비흐름선**): 부착 구간 지표에 데이터포인트가 있다 → 이 선도 초록 + 흐르는 점이 된다.
      // 🔴 이것은 종단 추론이 아니라 **그 구간을 직접 센 것**이다(지표 차원이 어태치먼트를 이름 댄다).
      //    이 행이 초록으로 안 나오면 「지표를 가진 관계가 화면에 닿지 못한다」는 결함이 살아 있다.
      { target_id: OBS_W_ATT, target_kind: 'edge', status: 'live', why: null,
        error_count: 0, partial: false,
        evidence: { api: 'cloudwatch:GetMetricData', metric: 'BytesIn',
          datapoints: 15, window_seconds: 900 } },
      // 선(**등급 B = 연역**): 같은 어태치먼트 지표로 판정했지만 **잰 대상이 이 구간이 아니다** →
      // 화면은 「직접 측정」이라고 말하면 안 된다(`observed_on` 이 그 사실을 들고 온다).
      { target_id: OBS_W_DEDUCE, target_kind: 'edge', status: 'live', why: null,
        error_count: 0, partial: false,
        evidence: { api: 'cloudwatch:GetMetricData', metric: 'BytesIn',
          datapoints: 15, window_seconds: 900, observed_on: OBS_W_ATT } },
      // 선(**등급 C = 경로 성립**): 흐름은 못 쟀고 조건 8개를 전부 읽어 전부 충족했다 → 회색 실선 +
      // 「경로 성립」. 🔴 **초록으로 승격하지 않는다**(ADR 0008 D7-1) — 「흐를 수 있다」는 「흐른다」가
      // 아니다. 그래서 `status` 는 `up` 이고 `path_ok` 는 등급을 올릴 뿐 색을 바꾸지 않는다.
      { target_id: OBS_W_PATHOK, target_kind: 'edge', status: 'up', why: null,
        error_count: 0, partial: false, path_ok: true,
        evidence: { api: 'ec2:DescribeRouteTables', metric: null, datapoints: null,
          window_seconds: null } },
      // 선(🆕 **등급 A0 = 측정: 흐름 없음**): 그 구간 자신의 지표를 받았고 0 아닌 점이 0개 → 회색 그대로 + 「측정: 흐름 없음」.
      { target_id: OBS_W_QUIET, target_kind: 'edge', status: 'up', why: null,
        error_count: 0, partial: false,
        evidence: { api: 'cloudwatch:GetMetricData', metric: 'BytesOutToDestination',
          datapoints: 0, window_seconds: 900 } },
      // 선(대조군 — E): 같은 모양인데 **인접 자원**을 쟀다(`observed_on`) → 이 구간이 조용하다는 증명이 아니다.
      { target_id: OBS_W_QUIETADJ, target_kind: 'edge', status: 'up', why: null,
        error_count: 0, partial: false,
        evidence: { api: 'cloudwatch:GetMetricData', metric: 'BytesIn',
          datapoints: 0, window_seconds: 900, observed_on: OBS_W_ATT } },
      // 선(대조군 2 — E): 이 구간 자신의 지표인데 점을 못 받았다(`datapoints: null` = 안 쟀음) → 「측정: 흐름 없음」이 아니다.
      { target_id: OBS_W_QUIETNULL, target_kind: 'edge', status: 'up', why: null,
        error_count: 0, partial: false,
        evidence: { api: 'cloudwatch:GetMetricData', metric: 'BytesOutToDestination',
          datapoints: null, window_seconds: 900 } },
      // 카드: 흐름이 관측된 노드 → 종단이 `live` 라 이 카드에 닿는 선도 초록이 된다(근거 ②).
      // 🔵 LB 라 대상 수(계약 `targets` · ADR 0006 W3②)를 같이 든다 — 비정상이 있어도 정상이 하나라도
      //    있으면 판정은 그대로다(엔진 `_with_targets`). 패널 「대상」 줄은 검사 99 가 잰다.
      { target_id: OBS_LIVE, target_kind: 'node', status: 'live', why: null,
        error_count: 0, partial: false, targets: { total: 4, healthy: 3, unhealthy: 1 },
        evidence: { api: 'cloudwatch:GetMetricData', metric: 'ActiveFlowCount',
          datapoints: 9, window_seconds: 300,
          first_flow_at: '2026-09-13T00:00:00Z', last_flow_at: '2026-09-13T00:03:00Z' } },
      // 카드: 정상(1층 근거만). `changes` 의 `resource_added` 대상과 같은 노드다.
      { target_id: OBS_ADD, target_kind: 'node', status: 'up', why: null,
        error_count: 0, partial: false,
        evidence: { api: 'ec2:DescribeInstanceStatus', metric: null, datapoints: null,
          window_seconds: null } },
      // 카드: 이상 + 오류 수 → `▲4`. `changes` 의 `status_changed live→down` 대상과 같은 노드라
      // 이력을 되짚으면 이 카드가 `live` 로 돌아간다(그림이 실제로 달라진다 = 검사 85 ③).
      { target_id: OBS_ST, target_kind: 'node', status: 'down', why: 'NAT 게이트웨이 상태 필드가 실패 값입니다',
        error_count: 4, partial: false,
        evidence: { api: 'ec2:DescribeNatGateways', metric: null, datapoints: null,
          window_seconds: null } },
      // 카드: 일부만 읽었다 → `~`. 사유가 없으면 사람이 고칠 단서가 사라진다.
      // 🔴 이 자원은 **`live` 이고 비흐름선(부착·피어링)의 종단**이다 — 그래야 「초록과 흐르는 점은
      //    흐름선 전용」이 실패할 수 있는 어서션이 된다(2026-09-24 실측: 이 행이 `up` 이던 동안
      //    브레이크 `95-v4 종류무시` 가 PASS = 죽은 브레이크였다. 부착선의 종단이 `live` 여야
      //    「부착으로 트래픽이 지나간다」는 없는 사실을 그리는 회귀가 드러난다).
      //    지표는 흐르는데 부착 목록 조회만 throttle 된 상태 = 실계정에서 흔한 조합이다.
      { target_id: OBS_PART, target_kind: 'node', status: 'live', why: null,
        error_count: 0, partial: true,
        partial_reasons: [{ code: 'throttled', api: 'ec2:DescribeTransitGatewayAttachments',
          message: 'Rate exceeded' }],
        evidence: { api: 'cloudwatch:GetMetricData', metric: 'BytesIn', datapoints: 7,
          window_seconds: 300 } },
      // 카드: 구조 조회는 됐지만 **지표는 없다**(`up`) + 이 자원은 **흐름선의 종단**이다 →
      // 「`up` 을 흐름으로 승격하지 않는다」가 화면에서 실제로 측정된다(위 `pickFlowEnd` 주석).
      { target_id: OBS_FLOWEND, target_kind: 'node', status: 'up', why: null,
        error_count: 0, partial: false,
        evidence: { api: 'ec2:DescribeSubnets', metric: null, datapoints: null, window_seconds: null } },
      // 카드: 관측 수단이 **구조적으로** 없다 → `?`. 권한 없음(해치)과 **다른 어휘**다.
      { target_id: OBS_NONE, target_kind: 'node', status: 'none',
        why: '이 종류에는 상태·지표 API 가 없습니다 — 권한 문제가 아닙니다',
        error_count: null, partial: false, evidence: null },
      // 카드: 오류 수를 **세지 못했다**(`null` ≠ `0`). 패널이 둘을 다르게 말해야 한다.
      { target_id: OBS_NULLC, target_kind: 'node', status: 'up', why: null,
        error_count: null, partial: false,
        evidence: { api: 'eks:DescribeCluster', metric: null, datapoints: null, window_seconds: null } }
    ].filter((o) => o.target_id),
    // ---- 옵션형 근거 «출처»(ADR 0009 1차 · 2026-09-25 신설) ----
    // 🔴 **이번 버전의 엔진이 실제로 낼 수 있는 문서만** 담는다: 1차는 탐지만 하므로 `used` 는
    //    전부 `false` 이고, 켜져 있으면 스키마가 `why` 를 요구한다(`$defs/source.allOf`). `used: true`
    //    는 2차(흐름 로그 리더)의 산출이므로 여기 두면 「엔진이 만들 수 없는 문서」가 된다 —
    //    그 경로는 검사 100 이 주입으로 잰다(위 `target_kind` 주석과 같은 이유다).
    // 🔴 `kind` **오름차순**이다(엔진 `SourceScan.to_list` 가 정렬한다) · `state: unknown` 인 줄에는
    //    `scope` 가 **없다**(「0/3」은 「3개 다 꺼짐」으로 읽히므로 엔진이 지운다).
    // 네 어휘를 한 문서에 둔다: 켜졌지만 안 읽음 1 · 확인 못함 2 · 꺼짐 4 · **해당 자원 없음 1** →
    // 알약은 `⚠ 근거 못 읽음 1종`. 🔴 마지막 하나(`0/0`)는 실계정 실측(2026-09-25 개발 계정에
    // Resolver 엔드포인트 0개)을 그대로 옮긴 것이다 — 없으면 「없는 자원을 켜라」 절이 미측정이 된다.
    sources: [
      { kind: 'container_insights', state: 'unknown', used: false,
        detail: '2개 자리에서 탐지 실패',
        why: 'ecs:DescribeClusters: 권한이 없어 읽지 못했다(AccessDenied)' },
      { kind: 'ec2_detailed_monitoring', state: 'off', used: false,
        scope: { unit: 'ec2_instance', enabled_ids: 0, total_ids: 4 } },
      { kind: 'eks_network_policy_log', state: 'unknown', used: false,
        detail: '1개 클러스터는 vpc-cni add-on 밖(Helm 설치 가능) — 꺼짐이 아니라 모름',
        why: 'vpc-cni add-on 을 읽을 수 없는 클러스터가 있다(Helm 설치 가능) — 켜짐·꺼짐을 판정할 수 없다' },
      { kind: 'log_delivery', state: 'off', used: false },
      { kind: 'resolver_target_ns_metrics', state: 'off', used: false,
        scope: { unit: 'resolver_endpoint', enabled_ids: 0, total_ids: 0 } },
      { kind: 'tgw_flow_log', state: 'off', used: false,
        scope: { unit: 'transit_gateway', enabled_ids: 0, total_ids: 1 } },
      { kind: 'vpc_flow_log', state: 'on', used: false,
        scope: { unit: 'vpc', enabled_ids: 2, total_ids: 3 },
        detail: 'logFormat v2 기본 1건 — 구간을 특정하는 필드가 없다',
        why: '흐름 로그 읽기를 켜지 않았다 — 켜면 인터넷·피어링 선의 흐름을 이 로그의 왕복 기록으로 확인한다(스택 파라미터 FlowLogReading · 로컬은 --flow-logs)' },
      { kind: 'vpn_tunnel_bgp_log', state: 'off', used: false,
        scope: { unit: 'vpn_connection', enabled_ids: 0, total_ids: 1 },
        detail: 'BGP 로그는 0/1개 연결에서만 켜져 있다 — 구간의 양끝은 BGP 쪽에만 있다' }
    ]
  }
})()
