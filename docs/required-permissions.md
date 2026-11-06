# 필요한 IAM 권한

NetworkTopology 가 각 스택에 만드는 IAM 권한을 액션 하나씩 설명합니다. 보안 검토자가 템플릿 정책을 읽을 때 옆에 두고 보는 문서입니다. 정책의 정본은 템플릿(`infra/stack-a-collector.yaml` · `infra/stack-b-member-role.yaml` · `infra/stack-c-viewer.yaml`)이며, 이 문서의 액션 목록이 템플릿과 같은지는 저장소의 시험이 확인합니다.

## 원칙

- **소스 계정에서는 읽기만 합니다.** 수집기가 부르는 API 는 `Describe*` · `Get*` · `List*` · `Search*` 로 시작하는 것뿐이고, 그 밖의 호출은 수집 엔진이 실행 중에 막습니다(`engine/src/nettopo/awsguard.py`). 유일한 예외는 소스 계정으로 들어가는 `sts:AssumeRole` 이며, 들어간 뒤의 세션에도 같은 제한이 걸립니다.
- **허용 문장의 액션에 와일드카드가 없습니다.** 모든 액션을 이름으로 적습니다. 관리형 `ReadOnlyAccess` 보다 좁습니다. 와일드카드 액션(`s3:*`)은 TLS 가 아닌 요청을 막는 **거부** 문장에만 씁니다.
- **리소스가 `*` 인 이유.** EC2 등의 `Describe*` 계열 API 는 리소스 수준 권한을 지원하지 않습니다. 대신 `aws:RequestedRegion` 조건으로 수집 리전만 허용합니다(스택 A `TargetRegions` · 스택 B `AllowedRegions`). 리전이 고정된 API(CloudFront · Route 53 · CloudFront 범위 WAF · 계정 이름 = us-east-1, Global Accelerator · Cloud WAN = us-west-2)는 그 리전 하나로 고정한 별도 문장에 둡니다. 그래서 수집 리전에 그 리전을 넣지 않아도 해당 카드가 빠지지 않습니다.
- **쓰기는 모니터링 계정 안에서, 정해진 키에만 합니다.** [4. 읽기가 아닌 액션과 그 근거](#4-읽기가-아닌-액션과-그-근거)에 전부 적었습니다.

**거부되면 화면에 보이는 것** — 아래 표의 마지막 칸입니다. 권한이 없어 읽지 못한 곳을 빈 그림으로 그리지 않습니다.

- **목록**: 그 계정 · 리전의 해당 종류 전체를 화면 위 배너에 「권한이 없어 읽지 못했습니다」로 알립니다. 다른 종류는 그대로 그립니다.
- **후속**: 목록으로 읽은 리소스 하나에 대해 더 묻는 호출입니다. 거부되면 그 리소스만 「일부 못 읽음」으로 표시하고, 카드와 나머지 선은 그대로 둡니다.
- **이름**: 계정 상자 제목이 계정 이름 대신 지금처럼 계정 id 로 나옵니다. 그 계정의 네트워크 그림은 그대로이고 「확인하지 못했다」로 바뀌지 않습니다.

## 1. 스택 B — 소스 계정의 읽기 전용 역할 (`NetworkReadOnlyRole`)

소스 계정마다 하나 만드는 역할입니다. 모니터링 계정의 수집기가 이 역할로 전환해 읽습니다.

**신뢰 정책.** 모니터링 계정(`HubAccountId`)의 주체 중 다음만 이 역할을 맡을 수 있습니다(`aws:PrincipalArn` 조건).

- 스택 A 의 수집 역할(`HubCollectorRoleNamePattern`, 기본 `nettopo-collector-BuildRole-*`)
- 스택 A 의 모니터링 역할(`HubStateRoleNamePattern`, 기본 `nettopo-collector-StateRole-*`)
- 선택: 사람이 로컬 CLI 로 수집할 때 쓰는 IAM Identity Center 권한 세트(`HubPermissionSetName`). 비워 두면 들어가지 않습니다.

`Principal` 에 계정 루트를 쓰는 것은 IAM 이 `Principal` 안의 와일드카드를 허용하지 않기 때문입니다. 실제 경계는 `aws:PrincipalArn` 조건입니다. 조건 없는 루트 신뢰(계정의 모든 주체)와는 다릅니다. 세션 최대 길이는 1시간입니다.

### 1-1. 구조 수집 (항상)

다이어그램을 그리는 데 쓰는 읽기입니다. 수집은 기본 1시간마다 돕니다. 모니터링을 켜면 일부(상태가 있는 종류)를 5분마다 다시 읽습니다.

| 액션 | 왜 필요한가 | 리소스 범위 | 거부되면 |
|---|---|---|---|
| `ec2:DescribeVpcs` | VPC 와 CIDR | `*` · 수집 리전 | 목록 |
| `ec2:DescribeSubnets` | 서브넷 · AZ · CIDR | `*` · 수집 리전 | 목록 |
| `ec2:DescribeRouteTables` | 라우트 테이블 · 서브넷 연결 · 라우트 선 | `*` · 수집 리전 | 목록 |
| `ec2:DescribeInternetGateways` | 인터넷 게이트웨이와 VPC 부착 | `*` · 수집 리전 | 목록 |
| `ec2:DescribeNatGateways` | NAT 게이트웨이 | `*` · 수집 리전 | 목록 |
| `ec2:DescribeTransitGateways` | Transit Gateway 와 소유 계정 | `*` · 수집 리전 | 목록 |
| `ec2:DescribeTransitGatewayAttachments` | TGW 어태치먼트와 연결 선. 모니터링 계정에서는 소스 계정 자동 발견에도 쓴다 | `*` · 수집 리전 | 목록 |
| `ec2:DescribeVpcPeeringConnections` | VPC 피어링 | `*` · 수집 리전 | 목록 |
| `ec2:DescribeVpcEndpoints` | VPC 엔드포인트와 서브넷 배치 | `*` · 수집 리전 | 목록 |
| `ec2:DescribeVpcEndpointServiceConfigurations` | 이 계정이 제공하는 엔드포인트 서비스 뒤의 GWLB·NLB(GWLB 엔드포인트 → GWLB 선). GWLB·NLB 가 있을 때만 부른다 | `*` · 수집 리전 | 후속 |
| `ec2:DescribeVpcEndpointServices` | 다른 계정이 제공하는 엔드포인트 서비스의 제공자 계정 · Bedrock 엔드포인트 판별 | `*` · 수집 리전 | 후속 |
| `ec2:DescribeSecurityGroups` | 보안 그룹과 SG 참조 규칙 | `*` · 수집 리전 | 목록 |
| `ec2:DescribeNetworkAcls` | 네트워크 ACL 과 서브넷 연결 | `*` · 수집 리전 | 목록 |
| `ec2:DescribeNetworkInterfaces` | RDS 의 서브넷 자리 · LB 사설 IP 대상의 주인 · 흐름 로그 판정의 ENI 목록 | `*` · 수집 리전 | 후속 |
| `elasticloadbalancing:DescribeLoadBalancers` | ALB · NLB · GWLB 와 서브넷 배치 | `*` · 수집 리전 | 목록 |
| `elasticloadbalancing:DescribeListeners` | 리스너(프로토콜 · 포트) — LB 마다 1회 | `*` · 수집 리전 | 후속 |
| `elasticloadbalancing:DescribeTargetGroups` | 대상 그룹과 대상 종류 — LB 마다 1회 | `*` · 수집 리전 | 후속 |
| `elasticloadbalancing:DescribeTargetHealth` | 등록 대상(LB → 대상 선) · 모니터링의 대상 상태. 거부되면 대상 수를 0개로 적지 않는다 | `*` · 수집 리전 | 후속 |
| `elasticloadbalancing:DescribeTags` | 대상 그룹 태그로 EKS 클러스터를 잇는다 | `*` · 수집 리전 | 후속 |
| `elasticloadbalancing:DescribeRules` | ALB 리스너마다 규칙 수(콘솔 「리스너 및 규칙」의 규칙 칸) — ALB 리스너마다 1회. 거부되면 그 칸을 「0」이 아니라 빈 칸으로 두고 LB 에 「확인하지 못했습니다」 | `*` · 수집 리전 | 후속 |
| `elasticloadbalancing:DescribeListenerCertificates` | HTTPS · TLS 리스너의 인증서 전체(기본 + SNI) — 그 리스너마다 1회. 거부되면 기본 인증서만 보이고 LB 에 「확인하지 못했습니다」 | `*` · 수집 리전 | 후속 |
| `wafv2:GetWebACLForResource` | ALB 에 붙은 웹 ACL — ALB 마다 1회. 거부되면 「붙지 않음」으로 적지 않는다 | `*` · 수집 리전 | 후속 |
| `wafv2:GetWebACL` | 웹 ACL 의 기본 동작 · 규칙 수. `GetWebACLForResource` 도 붙은 ACL 에 대해 이 권한을 함께 확인한다 | `*` · 수집 리전 | 후속 |
| `wafv2:ListWebACLs` | 리전 범위 웹 ACL 카드(CloudFront 범위는 아래 us-east-1 행) | `*` · 수집 리전 | 목록 |
| `wafv2:ListResourcesForWebACL` | 리전 범위 웹 ACL 이 보호하는 API Gateway REST API 선. 거부되면 「보호하지 않음」으로 적지 않는다 | `*` · 수집 리전 | 후속 |
| `directconnect:DescribeConnections` | Direct Connect 연결 · 모니터링의 연결 상태 | `*` · 수집 리전 | 목록 |
| `directconnect:DescribeVirtualInterfaces` | 가상 인터페이스 · 모니터링의 BGP 상태 | `*` · 수집 리전 | 목록 |
| `directconnect:DescribeDirectConnectGateways` | Direct Connect 게이트웨이 | `*` · 수집 리전 | 목록 |
| `directconnect:DescribeDirectConnectGatewayAssociations` | DX 게이트웨이의 VGW·TGW 연결 — 게이트웨이마다 1회 | `*` · 수집 리전 | 후속 |
| `ec2:DescribeInstances` | EC2 인스턴스(타입 · 사설 IP · 상태) | `*` · 수집 리전 | 목록 |
| `autoscaling:DescribeAutoScalingGroups` | Auto Scaling 그룹 | `*` · 수집 리전 | 목록 |
| `eks:ListClusters` | EKS 클러스터 목록 | `*` · 수집 리전 | 목록 |
| `eks:DescribeCluster` | 클러스터 VPC · 서브넷 · 엔드포인트 | `*` · 수집 리전 | 후속 |
| `network-firewall:ListFirewalls` | Network Firewall 목록 | `*` · 수집 리전 | 목록 |
| `network-firewall:DescribeFirewall` | 방화벽 서브넷 · 엔드포인트 | `*` · 수집 리전 | 후속 |
| `ec2:DescribeTransitGatewayRouteTables` | TGW 라우트 테이블(TGW 소유 계정에서만 보인다) | `*` · 수집 리전 | 목록 |
| `ec2:GetTransitGatewayRouteTableAssociations` | 라우트 테이블을 쓰는 어태치먼트 | `*` · 수집 리전 | 후속 |
| `ec2:GetTransitGatewayRouteTablePropagations` | 경로를 전파하는 어태치먼트 | `*` · 수집 리전 | 후속 |
| `ec2:SearchTransitGatewayRoutes` | TGW 라우트와 블랙홀 목적지 | `*` · 수집 리전 | 후속 |
| `ec2:GetTransitGatewayPrefixListReferences` | TGW 라우트 테이블의 「접두사 목록 참조」 탭(콘솔과 같은 표) — 라우트 테이블마다 1회. 거부되면 그 표를 「참조 없음」으로 적지 않는다 | `*` · 수집 리전 | 후속 |
| `ec2:DescribeTransitGatewayVpcAttachments` | VPC 어태치먼트의 서브넷 · DNS 지원 · 어플라이언스 모드(콘솔 어태치먼트 상세) — VPC 어태치먼트가 있을 때만 리전마다 1회. 거부되면 「서브넷 없음」으로 적지 않는다 | `*` · 수집 리전 | 후속 |
| `ec2:DescribeVpcAttribute` | VPC 의 DNS 확인 · DNS 호스트 이름(콘솔 VPC 세부 정보) — VPC 마다 2회. 거부되면 「꺼짐」으로 적지 않는다 | `*` · 수집 리전 | 후속 |
| `ec2:DescribeDhcpOptions` | VPC 의 DHCP 옵션 세트 내용(도메인 이름 · 이름 서버) — 리전마다 1회 | `*` · 수집 리전 | 후속 |
| `ec2:DescribeInstanceConnectEndpoints` | EC2 Instance Connect 엔드포인트 | `*` · 수집 리전 | 목록 |
| `rds:DescribeDBInstances` | RDS DB 인스턴스 · 모니터링의 DB 상태 | `*` · 수집 리전 | 목록 |
| `ecs:ListClusters` | ECS 클러스터 목록 | `*` · 수집 리전 | 목록 |
| `ecs:ListServices` | 클러스터의 서비스 목록. 거부되면 ECS 전체를 「읽지 못함」으로 둔다(일부만 그리면 나머지가 「없음」처럼 보인다) | `*` · 수집 리전 | 목록 |
| `ecs:DescribeServices` | 서비스의 서브넷 · SG · 대상 그룹 | `*` · 수집 리전 | 후속 |
| `sagemaker:ListNotebookInstances` | SageMaker 노트북 인스턴스 | `*` · 수집 리전 | 목록 |
| `sagemaker:DescribeNotebookInstance` | 노트북 서브넷 · SG · 인터넷 직접 접근 | `*` · 수집 리전 | 후속 |
| `sagemaker:ListDomains` | SageMaker Studio 도메인 | `*` · 수집 리전 | 목록 |
| `sagemaker:DescribeDomain` | 도메인 VPC · 서브넷 | `*` · 수집 리전 | 후속 |
| `sagemaker:ListEndpoints` | SageMaker 추론 엔드포인트 | `*` · 수집 리전 | 목록 |
| `sagemaker:DescribeEndpoint` | 엔드포인트 설정 이름 | `*` · 수집 리전 | 후속 |
| `sagemaker:DescribeEndpointConfig` | 설정의 모델 이름 | `*` · 수집 리전 | 후속 |
| `sagemaker:DescribeModel` | 모델 서브넷 · SG | `*` · 수집 리전 | 후속 |
| `ec2:DescribeVpnGateways` | 가상 프라이빗 게이트웨이(VGW)와 VPC 부착 | `*` · 수집 리전 | 목록 |
| `ec2:DescribeCustomerGateways` | 고객 게이트웨이(공인 IP · BGP ASN) | `*` · 수집 리전 | 목록 |
| `lambda:ListFunctions` | VPC 에 붙은 Lambda 함수 | `*` · 수집 리전 | 목록 |
| `ec2:DescribeVpnConnections` | Site-to-Site VPN 연결. 응답에 오는 사전 공유 키 · 장비 설정은 저장하지 않는다 | `*` · 수집 리전 | 목록 |
| `elasticache:DescribeCacheClusters` | ElastiCache 캐시 클러스터 · 모니터링의 상태 | `*` · 수집 리전 | 목록 |
| `elasticache:DescribeCacheSubnetGroups` | 캐시의 VPC · 서브넷 — 리전마다 1회 | `*` · 수집 리전 | 후속 |
| `elasticache:DescribeReplicationGroups` | 복제 그룹 구성 | `*` · 수집 리전 | 후속 |
| `rds:DescribeDBClusters` | Aurora · 다중 AZ DB 클러스터 · 모니터링의 상태 | `*` · 수집 리전 | 목록 |
| `rds:DescribeDBSubnetGroups` | 클러스터의 VPC · 서브넷 — 리전마다 1회 | `*` · 수집 리전 | 후속 |
| `es:ListDomainNames` | OpenSearch Service 도메인 목록 | `*` · 수집 리전 | 목록 |
| `es:DescribeDomains` | 도메인 VPC · 서브넷 · 엔드포인트. 거부되면 「공개 도메인」으로 적지 않는다 | `*` · 수집 리전 | 후속 |
| `kafka:ListClustersV2` | Amazon MSK 클러스터 · 모니터링의 상태 | `*` · 수집 리전 | 목록 |
| `kafka:ListNodes` | 브로커 ENI(LB 대상 연결) — 프로비저닝 클러스터마다 1회 | `*` · 수집 리전 | 후속 |
| `ec2:DescribeManagedPrefixLists` | 라우트 목적지 접두사 목록의 이름 — 접두사 목록을 쓰는 라우트가 있을 때만 | `*` · 수집 리전 | 후속 |
| `ec2:GetManagedPrefixListEntries` | 고객 관리형 접두사 목록의 CIDR | `*` · 수집 리전 | 후속 |
| `ec2:DescribeAddresses` | 탄력적 IP 와 그 주인 | `*` · 수집 리전 | 목록 |
| `rds:DescribeDBProxies` | RDS 프록시 | `*` · 수집 리전 | 목록 |
| `rds:DescribeDBProxyTargets` | 프록시 대상(DB 인스턴스 · 클러스터) | `*` · 수집 리전 | 후속 |
| `ec2:DescribeTransitGatewayConnects` | TGW Connect 어태치먼트가 올라탄 전송 어태치먼트 — Connect 가 있을 때만 | `*` · 수집 리전 | 후속 |
| `ec2:DescribeTransitGatewayConnectPeers` | Connect GRE 피어(주소 · BGP ASN) | `*` · 수집 리전 | 후속 |
| `vpc-lattice:ListServiceNetworks` | VPC Lattice 서비스 네트워크 | `*` · 수집 리전 | 목록 |
| `vpc-lattice:ListServiceNetworkVpcAssociations` | 서비스 네트워크에 붙은 VPC | `*` · 수집 리전 | 후속 |
| `vpc-lattice:ListServiceNetworkServiceAssociations` | 서비스 네트워크에 붙은 서비스 | `*` · 수집 리전 | 후속 |
| `vpc-lattice:ListServices` | VPC Lattice 서비스 | `*` · 수집 리전 | 목록 |
| `vpc-lattice:ListTargetGroups` | Lattice 대상 그룹 | `*` · 수집 리전 | 후속 |
| `vpc-lattice:ListTargets` | 대상 그룹의 등록 대상(서비스 → EC2 · ALB · Lambda 선) | `*` · 수집 리전 | 후속 |
| `apigateway:GET` | API Gateway REST · HTTP · WebSocket API 와 VPC 링크 → LB 선. 액션이 하나뿐이라 리소스를 경로 다섯 개로 좁힌다(키 · 정책 · 스테이지 변수는 읽지 못한다) | API 경로 5개(`/restapis` · `/restapis/*/resources` · `/vpclinks` · `/apis` · `/apis/*/integrations`) · 수집 리전 | 목록 |
| `globalaccelerator:ListAccelerators` | Global Accelerator(API 가 us-west-2 에만 있다) | `*` · us-west-2 고정 | 목록 |
| `globalaccelerator:ListListeners` | 가속기의 리스너 | `*` · us-west-2 고정 | 후속 |
| `globalaccelerator:ListEndpointGroups` | 엔드포인트 그룹(ALB · NLB · EC2 · EIP 선) | `*` · us-west-2 고정 | 후속 |
| `networkmanager:ListCoreNetworks` | Cloud WAN 코어 네트워크(API 가 us-west-2 에만 있다) | `*` · us-west-2 고정 | 목록 |
| `networkmanager:ListAttachments` | 코어 네트워크의 VPC 연결 선 | `*` · us-west-2 고정 | 후속 |
| `cloudfront:ListDistributions` | CloudFront 배포 · 원본 · 붙은 웹 ACL | `*` · us-east-1 고정 | 목록 |
| `cloudfront:ListVpcOrigins` | CloudFront VPC 원본(사설 ALB · NLB · EC2 선) — VPC 원본을 쓰는 배포가 있을 때만 | `*` · us-east-1 고정 | 후속 |
| `cloudfront:ListCachePolicies` | 동작(Behaviors) 표의 캐시 정책 이름 — 캐시 정책을 쓰는 배포가 있을 때만. 거부되면 정책 id 만 보이고 그 배포에 「확인하지 못했습니다」 | `*` · us-east-1 고정 | 후속 |
| `cloudfront:ListOriginAccessControls` | 원본 표의 원본 접근 제어(OAC) 이름 — OAC 를 쓰는 원본이 있을 때만. 거부되면 OAC id 만 보이고 그 배포에 「확인하지 못했습니다」 | `*` · us-east-1 고정 | 후속 |
| `route53:ListHostedZones` | Route 53 호스팅 영역 | `*` · us-east-1 고정 | 목록 |
| `route53:ListResourceRecordSets` | LB · CloudFront 를 가리키는 별칭 레코드(선). 레코드 값은 저장하지 않는다 | `*` · us-east-1 고정 | 후속 |
| `route53:GetHostedZone` | 사설 영역의 연결 VPC | `*` · us-east-1 고정 | 후속 |
| `wafv2:ListWebACLs` | CloudFront 범위 웹 ACL 카드(전역 줄) | `*` · us-east-1 고정 | 목록 |
| `wafv2:GetWebACL` | CloudFront 범위 웹 ACL 의 기본 동작 · 규칙 수 | `*` · us-east-1 고정 | 후속 |
| `account:GetAccountInformation` | 계정 상자의 계정 이름. `AccountId` 없이 불러 **이 계정 자신**의 이름만 읽는다(다른 계정 이름은 묻지 않는다) — 구조 수집에서 계정마다 1회 · 5분 관측은 부르지 않는다(관측 역할에는 권한 없음) | 자기 계정 하나(`arn:aws:account::<이 계정>:account`) · us-east-1 고정 | 이름 |

### 1-2. 지표 (모니터링 「지표까지」 · 사용률을 켤 때)

| 액션 | 왜 필요한가 | 리소스 범위 | 거부되면 |
|---|---|---|---|
| `cloudwatch:GetMetricData` | 트래픽 · 드롭 지표(선 위 흐름 표시)와 사용률 수치 | `*` · 수집 리전 | 그 리소스의 숫자가 「?」로 남습니다(구조는 그대로) |
| `cloudwatch:GetMetricData` | CloudFront 지표(`AWS/CloudFront` 는 us-east-1 에만 있다) · Global Accelerator 사용량(`AWS/GlobalAccelerator` 는 us-west-2 에서만 조회된다) | `*` · us-east-1 · us-west-2 고정 | 그 CloudFront · 가속기의 숫자만 「?」 |

CloudWatch 지표는 네임스페이스로 권한을 좁힐 수 없습니다. 지표 읽기만 허용하며, 지표 요청 요금이 생깁니다(README 「비용」).

### 1-3. 흐름 로그 읽기 (`FlowLogReading=ENABLED` 일 때만)

스택 파라미터로 켤 때만 정책이 생깁니다. 꺼져 있으면 아래 액션은 역할에 없습니다. 흐름 로그를 KMS 고객 키로 암호화했다면 [1-6](#1-6-흐름-로그-kms-복호화-flowlogkmskeyarns-를-줄-때만)의 복호화 권한도 함께 받습니다.

| 액션 | 왜 필요한가 | 리소스 범위 | 거부되면 |
|---|---|---|---|
| `logs:DescribeLogGroups` | 흐름 로그를 보내는 로그 그룹의 등급(드문 접근 등급이면 읽기 전에 「읽지 못함」) | 이 계정의 로그 그룹(이름 · 등급만, 내용 없음) · 수집 리전 | 그 구간의 흐름 근거가 「읽지 못함」 |
| `logs:DescribeLogStreams` | 최근 로그 스트림을 고른다 | **`FlowLogGroupNamePrefixes` 로 지정한 그룹만** · 이 계정만(다른 계정 그룹은 거부 문장으로 막는다) | 같음 |
| `logs:GetLogEvents` | 판정 창 안의 레코드 | 같음 | 같음 |
| `s3:GetBucketLocation` | S3 로 보내는 흐름 로그 버킷의 리전 | 이 계정 버킷 + `FlowLogCentralBucketArns` 로 지정한 버킷 | 같음 |
| `s3:ListBucket` | 판정 창의 날짜 · 시간 접두 목록 | 접두가 `AWSLogs/<이 계정>/vpcflowlogs/` 인 경로만 · 이 계정 버킷 + 지정한 버킷 | 같음 |
| `s3:GetObject` | 흐름 로그 객체 | 위 흐름 로그 키 모양만 · 다른 계정 버킷에서는 이 계정 번호가 든 키만(거부 문장) | 같음 |

로그 그룹 이름은 전체 이름으로 적습니다. 짧은 접두를 주면 앱 로그까지 열리므로, 스택이 `*` 와 빈 값을 거부합니다.

### 1-4. 근거 탐지 (모니터링을 켤 때)

「이 구간을 지표나 로그로 확인할 수 있는가」를 판단하기 위한 설정 읽기입니다. 데이터는 읽지 않습니다.

| 액션 | 왜 필요한가 | 리소스 범위 | 거부되면 |
|---|---|---|---|
| `ec2:DescribeFlowLogs` | VPC · TGW 흐름 로그가 켜져 있는지와 형식 | `*` · 수집 리전 | 그 근거의 출처 줄이 「확인하지 못함」 |
| `ec2:DescribeInstances` | EC2 상세 모니터링 여부(지표 조회 간격) | `*` · 수집 리전 | 같음 |
| `ec2:DescribeVpnConnections` | VPN 터널 로그 · BGP 로그 설정 | `*` · 수집 리전 | 같음 |
| `ecs:ListClusters` | Container Insights 판단의 대상 클러스터 | `*` · 수집 리전 | 같음 |
| `ecs:DescribeClusters` | Container Insights 설정 | `*` · 수집 리전 | 같음 |
| `eks:ListClusters` | EKS 네트워크 정책 로그 판단의 대상 클러스터 | `*` · 수집 리전 | 같음 |
| `eks:DescribeAddon` | `vpc-cni` 애드온의 네트워크 정책 설정 | `*` · 수집 리전 | 같음 |
| `logs:DescribeDeliverySources` | 로그 전달 설정(흐름 로그 · LB 액세스 로그) | `*` · 수집 리전 | 같음 |
| `route53resolver:ListResolverEndpoints` | Resolver 엔드포인트의 지표 설정 | `*` · 수집 리전 | 같음 |

### 1-5. 서비스 한도 (사용률을 켤 때)

| 액션 | 왜 필요한가 | 리소스 범위 | 거부되면 |
|---|---|---|---|
| `servicequotas:GetServiceQuota` | 이 계정에 적용된 한도(VPC · EC2) | `*` · 수집 리전 | 계정 한도 대신 AWS 기본 한도로 계산하고 그 사유를 함께 표시합니다(한도를 늘린 계정은 실제보다 높은 % 가 됩니다) |
| `servicequotas:GetAWSDefaultServiceQuota` | 적용 값이 없을 때의 AWS 기본 한도 | `*` · 수집 리전 | 위 호출도 값을 주지 못하면 공개 문서의 기본 한도로 계산하고 사유를 함께 표시합니다 |

### 1-6. 흐름 로그 KMS 복호화 (`FlowLogKmsKeyArns` 를 줄 때만)

| 액션 | 왜 필요한가 | 리소스 범위 | 거부되면 |
|---|---|---|---|
| `kms:Decrypt` | SSE-KMS 로 암호화된 흐름 로그 객체를 S3 가 내줄 때 필요하다. 수집기는 KMS 를 직접 부르지 않는다 | **지정한 키 ARN 만** · `kms:ViaService` = S3 일 때만 | 그 버킷의 흐름 로그가 「KMS 키로 풀 수 없다」로 표시 |

키 정책도 이 역할을 허용해야 합니다. SSE-S3 버킷과 KMS 로 암호화된 로그 그룹은 이 값 없이 읽습니다.

## 2. 스택 A — 모니터링 계정의 수집기

모니터링 계정에 역할 네 개를 만듭니다. 모두 서비스 주체(CodeBuild · EventBridge · Lambda)만 맡을 수 있습니다.

### 2-1. 수집 역할 (CodeBuild `BuildRole`)

모니터링 계정 자신을 읽기 위해 [1-1](#1-1-구조-수집-항상)과 **같은 액션 · 같은 조건**을 가집니다. 그 밖에 다음을 가집니다.

| 액션 | 왜 필요한가 | 리소스 범위 |
|---|---|---|
| `sts:GetCallerIdentity` | 지금 어느 계정인지 확인 | `*`(리소스 개념 없음) |
| `sts:AssumeRole` | 소스 계정의 읽기 전용 역할로 전환 | **역할 이름 `MemberRoleName` 하나**(`arn:aws:iam::*:role/<이름>`). 실제로 들어갈 수 있는 계정은 그 역할의 신뢰 정책이 정한다 |
| `s3:GetObject` | 배포 패키지(`nettopo-source.zip`) 읽기 | `SourceLocation` 버킷의 객체 |
| `s3:GetObjectVersion` | 같은 패키지의 버전 읽기(버전 관리 버킷) | 같음 |
| `kms:Decrypt` | 패키지 버킷이 KMS 로 암호화된 경우 S3 가 대신 복호화 | `kms:ViaService` = 이 리전의 S3 일 때만 |
| `kms:DescribeKey` | 위와 같음 | 같음 |
| `s3:PutObject` | 다이어그램 · 스냅샷 저장 | 산출물 버킷. 모니터링을 켠 설치에서는 `index.html` 쓰기를 **거부**한다(서명 함수만 게시) |
| `s3:GetObject` | 직전 스냅샷 읽기(변경 비교) | 산출물 버킷 |
| `s3:ListBucket` | 같음 | 산출물 버킷 |
| `logs:CreateLogStream` | 빌드 로그 | 이 스택의 빌드 로그 그룹 |
| `logs:PutLogEvents` | 빌드 로그 | 같음 |

### 2-2. 모니터링 역할 (Lambda `StateRole` · 모니터링을 켤 때만)

모니터링 계정 자신을 읽기 위해 [1-1](#1-1-구조-수집-항상)~[1-6](#1-6-흐름-로그-kms-복호화-flowlogkmskeyarns-를-줄-때만)과 **같은 액션 · 같은 조건**을 가지고, 위 2-1 의 `sts:GetCallerIdentity` · `sts:AssumeRole` 도 같은 범위로 가집니다. 그 밖에 다음을 가집니다.

| 액션 | 왜 필요한가 | 리소스 범위 |
|---|---|---|
| `s3:GetObject` | 화면의 켜기 · 끄기 신호와 최신 구조 읽기 | 산출물 버킷의 키 다섯 개(`control/desired.json` · `structure-latest.json` · `structure-previous.json` · `state-latest.json` · `snapshots/structure/*`) |
| `s3:ListBucket` | 없는 키와 권한 거부를 구별 | 산출물 버킷 |
| `s3:PutObject` | 상태 기록 | `snapshots/state/*` · `state-latest.json` 만 |
| `logs:CreateLogStream` | 함수 로그 | 이 함수의 로그 그룹 |
| `logs:PutLogEvents` | 함수 로그 | 같음 |
| `sqs:SendMessage` | 실패한 주기 보관 | 이 스택의 실패 보관 큐 |

### 2-3. 서명 역할 (Lambda `SignerRole` · 모니터링을 켤 때만)

화면 안에 넣는 서명 URL 을 만드는 역할입니다. S3 는 서명 URL 요청을 서명한 주체의 권한으로 판정하므로, 브라우저가 할 수 있는 일은 이 역할의 권한을 넘지 않습니다. 모니터링 역할과 분리되어 있어 모니터링 함수가 자기 신호를 켤 수 없습니다.

| 액션 | 왜 필요한가 | 리소스 범위 |
|---|---|---|
| `s3:GetObject` | 화면이 읽는 상태(서명) | `state-latest.json` |
| `s3:PutObject` | 화면이 쓰는 켜기 · 끄기 신호(서명). **화면의 유일한 쓰기** | `control/desired.json` |
| `s3:GetObject` | 신호의 조건부 쓰기(현재 값 확인) | `control/desired.json` |
| `s3:ListBucket` | 상태가 아직 없을 때 화면이 404 를 받게 한다 | 산출물 버킷 |
| `s3:GetObject` | 서명 전 다이어그램 읽기 | `unsigned/index.html` |
| `s3:GetObject` | 게시된 다이어그램 읽기 · 「최신 화면 열기」 서명 | `index.html` |
| `s3:PutObject` | 서명 URL 을 넣은 다이어그램 게시 | `index.html` |
| `logs:CreateLogStream` | 함수 로그 | 이 함수의 로그 그룹 |
| `logs:PutLogEvents` | 함수 로그 | 같음 |
| `sqs:SendMessage` | 실패한 주기 보관 | 이 스택의 실패 보관 큐 |

### 2-4. 스케줄 역할 (EventBridge `ScheduleRole`)

| 액션 | 왜 필요한가 | 리소스 범위 |
|---|---|---|
| `codebuild:StartBuild` | 스케줄(기본 1시간)마다 수집 빌드를 시작 | 이 스택의 수집 프로젝트 하나 |

### 2-5. 버킷 · 큐 정책

- 산출물 버킷: 공개 접근 차단 전부 · 기본 암호화 · TLS 가 아닌 요청 거부. CloudFront 읽기 문장(`AllowViewerDistributionReadIndex`)은 **스택 C 를 올리지 않아도 항상 있습니다** — 원본 액세스 제어(OAC)로 오는 **이 계정의 CloudFront 배포**가 `index.html` 하나만 읽을 수 있습니다(`aws:SourceAccount` · `aws:SourceArn` 조건). 기본은 이 계정의 배포 전부이고, 스택 A `ViewerDistributionId` 에 스택 C 의 배포 ID 를 주면 그 배포 하나로 좁혀집니다([7](#7-선택-강화-조건-기본-꺼짐)).
- 실패 보관 큐: TLS 가 아닌 요청 거부.

## 3. 스택 C — 뷰어 (선택)

**IAM 역할을 만들지 않습니다.** CloudFront 는 원본 액세스 제어(OAC)로 산출물 버킷의 `index.html` 하나만 읽습니다(위 2-5 의 버킷 정책). 서명 없는 요청 · 만료된 링크 · 다른 경로는 403 입니다.

액세스 로그 버킷 정책:

| 문장 | 효과 |
|---|---|
| TLS 가 아닌 요청 거부 | 모든 S3 요청 |
| 지정한 사람 외 로그 읽기 거부 | 로그에 서명 링크 전체가 남으므로, 계정 루트와 `LogReaderPrincipalArns` 에 적은 역할 · 사용자 외에는 로그 객체 읽기 · 목록을 거부합니다 |

## 4. 읽기가 아닌 액션과 그 근거

| 액션 | 어디에 | 근거 |
|---|---|---|
| `codebuild:StartBuild` | 스택 A 스케줄 역할 | 이 스택의 수집 프로젝트 하나만 시작한다 |
| `sts:AssumeRole` | 스택 A 수집 · 모니터링 역할 | 소스 계정으로 들어가는 유일한 방법. 역할 이름 하나로 좁히고, 들어간 세션에도 읽기 전용 제한이 그대로 걸린다 |
| `kms:Decrypt` · `kms:DescribeKey` | 스택 A 수집 역할 · 흐름 로그 정책(스택 A · B) | S3 객체를 내줄 때 S3 가 대신 부른다(`kms:ViaService` = S3). 흐름 로그는 지정한 키만 |
| `s3:PutObject` | 스택 A 세 역할 | 모니터링 계정의 산출물 버킷에만. 역할마다 쓸 수 있는 키가 다르다(2-1 ~ 2-3) |
| `logs:CreateLogStream` · `logs:PutLogEvents` | 스택 A 세 역할 | 자기 로그 그룹에만 |
| `sqs:SendMessage` | 스택 A 모니터링 · 서명 역할 | 이 스택의 실패 보관 큐에만 |

소스 계정의 역할(스택 B)에는 쓰기 액션이 없습니다. 위 `kms:Decrypt` 도 흐름 로그를 읽을 때 S3 를 거쳐서만 쓰입니다.

## 5. 요구하지 않는 것

- 리소스를 만들거나 바꾸거나 지우는 권한, 태그를 붙이는 권한, 흐름 로그를 켜는 권한은 요구하지 않습니다. 이 툴은 관측만 합니다.
- 관리형 `ReadOnlyAccess` · `ViewOnlyAccess` 를 붙이지 않습니다. 소스 계정 역할에 넓은 관리형 정책을 붙이면 이 문서의 목적이 무너집니다.
- IAM 사용자 · 장기 액세스 키를 만들지 않습니다. 사람의 자격 증명은 IAM Identity Center 같은 임시 자격 증명을 씁니다.
- 브라우저가 호출하는 API · Lambda 함수 URL · 공개 버킷이 없습니다.

## 6. 배포하는 사람에게 필요한 권한

위 1~5 절은 **스택이 수집기에 주는** 권한입니다. 이 절은 **스택을 올리는 사람(배포자)** 의 자격 증명에 무엇이 있어야 하는지입니다. README 와 배포 가이드의 「사전 요구사항」은 「IAM 역할 · S3 버킷 · CodeBuild · EventBridge · Lambda · CloudWatch Logs · SQS · CloudFront 를 만들 수 있는 자격 증명」이라고만 적습니다. 아래는 그 한 줄을 템플릿이 만드는 리소스와 `deploy.sh` · 빠른 시작 명령이 부르는 API 로 풀어 쓴 것입니다.

배포자는 IAM Identity Center 권한 세트 같은 임시 자격 증명을 씁니다(장기 액세스 키 금지 · [5 절](#5-요구하지-않는-것)). 실무에서는 `AdministratorAccess` 권한 세트로 올리는 경우가 대부분이고, 아래 표는 그보다 좁은 권한 세트를 만들려는 보안 검토자를 위한 것입니다. 배포자 권한은 **배포 시점**에만 쓰이고, 그 뒤의 수집은 스택 A 의 역할(§2)이 합니다.

### 6-1. 모니터링 계정 — 스택 A (수집기)

빠른 시작 1~4 단계와 `deploy.sh` 가 부르는 API 입니다. 스택 A 는 IAM 역할 4개 · CodeBuild 프로젝트 1개 · EventBridge 규칙 5개 · Lambda 함수 2개 · CloudWatch Logs 로그 그룹 3개 · SQS 큐 1개 · S3 버킷 1개를 만듭니다. 역할 이름을 지정하지 않으므로 `--capabilities CAPABILITY_IAM` 으로 충분합니다.

| 영역 | 필요한 액션 | 어디서 쓰이나 |
|---|---|---|
| CloudFormation | `cloudformation:CreateStack` · `UpdateStack` · `DeleteStack` · `CreateChangeSet` · `ExecuteChangeSet` · `DescribeChangeSet` · `DeleteChangeSet` · `DescribeStacks` · `DescribeStackEvents` | `aws cloudformation deploy` 는 변경 세트로 올립니다(`deploy.sh` 는 `--no-fail-on-empty-changeset` 이라 빈 변경 세트를 `DeleteChangeSet` 으로 치웁니다). `deploy.sh` 0단계는 `DescribeStacks` 로 자격 증명을 확인합니다 |
| IAM | `iam:CreateRole` · `DeleteRole` · `GetRole` · `UpdateAssumeRolePolicy` · `PutRolePolicy` · `DeleteRolePolicy` · `GetRolePolicy` · **`iam:PassRole`** | 스택 A 의 역할 4개(§2-1 ~ 2-4)와 인라인 정책. CodeBuild 프로젝트 · Lambda 함수 · EventBridge 규칙에 역할을 붙이므로 `PassRole` 이 없으면 생성이 실패합니다 |
| S3 — 패키지 버킷 | `s3:CreateBucket` · `PutPublicAccessBlock` · `GetPublicAccessBlock` · `PutEncryptionConfiguration` · `PutBucketVersioning` · `PutBucketPolicy` · `PutBucketTagging` · `GetBucketLocation` · `ListBucket` · `PutObject` · `GetObject` | 1단계에서 배포자가 직접 만들고 `nettopo-source.zip`(모니터링을 켤 때는 Lambda zip 도) 을 올립니다. `deploy.sh` 는 템플릿도 이 버킷을 거쳐(`--s3-bucket`) 올립니다. CloudFormation 이 Lambda 코드를 이 버킷에서 읽을 때도 **배포자의** `GetObject` 를 씁니다 |
| S3 — 아티팩트 버킷 | `s3:CreateBucket` · `PutBucketPolicy` · `PutEncryptionConfiguration` · `PutBucketVersioning` · `PutLifecycleConfiguration` · `PutPublicAccessBlock` · `GetObject` | 스택이 만듭니다(§2-5 버킷 정책). 4단계 `index.html` 내려받기가 `GetObject` |
| CodeBuild | `codebuild:CreateProject` · `UpdateProject` · `DeleteProject` · `BatchGetProjects` · **`StartBuild`** · **`BatchGetBuilds`** | 3단계에서 스케줄을 기다리지 않고 첫 수집을 직접 시작하고 끝나기를 기다립니다 |
| EventBridge | `events:PutRule` · `DeleteRule` · `DescribeRule` · `PutTargets` · `RemoveTargets` | 규칙 5개(1시간 수집 · 5분 모니터링 등) |
| Lambda | `lambda:CreateFunction` · `DeleteFunction` · `GetFunction` · `UpdateFunctionCode` · `UpdateFunctionConfiguration` · `AddPermission` · `RemovePermission` · `PutFunctionEventInvokeConfig` · `DeleteFunctionEventInvokeConfig` · `PutFunctionConcurrency` · `DeleteFunctionConcurrency` | 모니터링 역할 · 서명 역할 함수 2개(`StateLambdaSourceKey` 를 줄 때만 생성) |
| CloudWatch Logs | `logs:CreateLogGroup` · `DeleteLogGroup` · `DescribeLogGroups` · `PutRetentionPolicy` | 로그 그룹 3개. (`deploy.sh` 는 로그를 직접 읽지 않습니다 — 수집이 실패하면 확인용 `aws logs tail …` 명령을 **안내만** 합니다. 그 명령을 직접 실행하려면 `logs:StartLiveTail` 또는 `FilterLogEvents` 가 더 필요합니다) |
| SQS | `sqs:CreateQueue` · `DeleteQueue` · `GetQueueAttributes` · `SetQueueAttributes` | 실패 보관 큐 1개와 큐 정책 |
| STS | `sts:GetCallerIdentity` | `deploy.sh` 0단계가 계정 ID 를 확인합니다 |
| 태그(`deploy.sh --tags` 를 줄 때만) | `iam:TagRole` · `UntagRole` · `lambda:TagResource` · `UntagResource` · `logs:TagResource` · `UntagResource` · `sqs:TagQueue` · `UntagQueue` · `events:TagResource` · `UntagResource` · `s3:PutBucketTagging` | 스택 태그는 CloudFormation 이 각 리소스에 전파하므로 리소스마다 태그 액션이 필요합니다. 템플릿 자체에는 `Tags` 가 없습니다 |

### 6-2. 소스 계정 — 스택 B (읽기 전용 역할)

역할에 이름을 붙이므로(파라미터 `RoleName`, 기본 `NetworkReadOnlyRole`) **`--capabilities CAPABILITY_NAMED_IAM`** 이 필요합니다. 콘솔에서는 마지막 화면의 「IAM 리소스를 사용자 지정 이름으로 생성할 수 있음을 승인」에 체크합니다.

| 방식 | 필요한 권한 |
|---|---|
| 계정마다 직접(4-B) | 6-1 의 CloudFormation 액션 + `iam:CreateRole` · `DeleteRole` · `GetRole` · `UpdateAssumeRolePolicy` · `PutRolePolicy` · `DeleteRolePolicy` · `GetRolePolicy`. |
| Organizations StackSet(4-C) | Organizations 관리 계정 또는 StackSets **위임 관리자** 자격 증명으로 `cloudformation:CreateStackSet` · `UpdateStackSet` · `CreateStackInstances` · `ListStackInstances` · `DescribeStackSet*`. 위임 관리자는 명령마다 `--call-as DELEGATED_ADMIN`. 대상 계정 안의 역할 생성은 서비스 관리형 권한이 대신하므로 대상 계정 자격 증명은 필요 없습니다 |

### 6-3. 모니터링 계정 — 스택 C (뷰어 · 선택)

IAM 역할을 만들지 않습니다(§3). CloudFront 배포 · 키 그룹 · 공개 키 · 원본 액세스 제어 · 응답 헤더 정책과 액세스 로그 버킷을 만듭니다. 아티팩트 버킷은 건드리지 않습니다 — CloudFront 가 읽을 수 있게 하는 버킷 정책은 스택 A 가 이미 갖고 있습니다(§2-5).

| 영역 | 필요한 액션 |
|---|---|
| CloudFront | `cloudfront:CreateDistribution` · `UpdateDistribution` · `DeleteDistribution` · `GetDistribution` · `TagResource` · `CreatePublicKey` · `UpdatePublicKey` · `DeletePublicKey` · `GetPublicKey` · `CreateKeyGroup` · `UpdateKeyGroup` · `DeleteKeyGroup` · `GetKeyGroup` · `CreateOriginAccessControl` · `UpdateOriginAccessControl` · `DeleteOriginAccessControl` · `GetOriginAccessControl` · `CreateResponseHeadersPolicy` · `UpdateResponseHeadersPolicy` · `DeleteResponseHeadersPolicy` · `GetResponseHeadersPolicy` |
| S3 | 6-1 의 아티팩트 버킷 액션과 같음(액세스 로그 버킷 생성과 그 정책) |

### 6-4. 삭제할 때

[배포 가이드의 삭제 절](install-guide.md#삭제) 순서대로 `cloudformation:DeleteStack` 외에 아티팩트 · 패키지 · 액세스 로그 버킷을 비우고 지우는 `s3:ListBucketVersions` · `DeleteObject` · `DeleteObjectVersion` · `DeleteBucket` 이 필요합니다. 버킷은 스택을 지워도 남습니다(산출물 보존).

## 7. 선택 강화 조건 (기본 꺼짐)

값을 비워 두면 위 1~3 절 그대로 동작합니다. 액션은 늘거나 줄지 않고 조건만 붙습니다.

- **스택 B `ExternalId`** — 읽기 전용 역할 신뢰 정책에 `sts:ExternalId` 조건을 붙입니다. 같은 값을 **스택 A `MemberExternalId`** 에 주면 수집기 · 모니터링 함수가 전환할 때마다 그 값을 넘깁니다. 스택 B 에만 주면 전환이 거부됩니다.
- **스택 B `PrincipalOrgId`** — 신뢰 정책에 `aws:PrincipalOrgID` 조건을 붙입니다. 모니터링 계정이 같은 조직에 있어야 합니다.
- **스택 A `ViewerOrigin`** — 산출물 버킷 CORS 의 허용 원본을 「모든 CloudFront 도메인(`https://*.cloudfront.net`)」에서 `https://` + 그 도메인 하나로 좁힙니다(`null` 원본 = 파일로 연 화면은 그대로). 사용자 지정 도메인을 쓸 때도 여기에 그 도메인을 줍니다.
- **스택 A `ViewerDistributionId`** — 위 2-5 의 CloudFront 읽기 문장을 그 배포 하나로 좁힙니다.

값과 무관하게 항상 붙는 조건:

- **서비스 역할 신뢰 정책의 `aws:SourceAccount`** — 스택 A 의 역할 네 개(`BuildRole` · `ScheduleRole` · `StateRole` · `SignerRole`)는 서비스 주체가 **이 계정의 리소스를 대신할 때만** 맡을 수 있습니다(혼동된 대리인 방지).
- **`s3:ResourceAccount`** — 산출물 버킷 읽기 · 쓰기 문장과 배포 패키지(`nettopo-source.zip`) 읽기 문장은 버킷이 **이 계정 소유일 때만** 허용됩니다. 같은 이름의 남의 버킷으로 가는 요청은 거부됩니다.
