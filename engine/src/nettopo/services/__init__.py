"""서비스별 수집기 레지스트리 — 「이음매 2」(`docs/contract-seams.md`).

**수집 종류 하나 = 파일 하나.** 새 종류를 얹는 절차는 셋뿐이다:

1. `services/<종류>.py` 에 `SERVICE = Service(spec=…, build=…, enrich=…)` 를 만든다.
2. 아래 `REGISTRY` 에 한 줄 더한다.
3. `collect.py` 의 `SUPPORTED_RESOURCE_TYPES` 와 `schema/topology.schema.json` 의 `node_type` ·
   `resource_type` 에 그 종류를 더한다(계약이 먼저다 — 불변식 ③).

3번을 빼먹으면 수집이 **조용히 일어나지 않는다**. 그래서
`engine/tests/test_services.py::test_registry_types_are_supported_and_ordered` 가 그 침묵을 FAIL 로 바꾼다.

🔴 **파일 단위는 「AWS 서비스」가 아니라 「수집 종류」다.** EC2 하나가 종류 11개를 낳기 때문이다 —
서비스로 묶으면 `ec2.py` 가 600줄로 자라 이 이음매가 풀려고 만든 무더기가 그대로 복원된다.
`REGISTRY` 의 키도 종류이므로 **키 = 파일명**이고, `test_registry_module_matches_type` 이 그것을 잰다.
"""

from __future__ import annotations

from . import (
    api_gateway,
    api_gateway_v2,
    auto_scaling_group,
    cloudfront,
    core_network,
    customer_gateway,
    direct_connect_connection,
    direct_connect_gateway,
    direct_connect_virtual_interface,
    ec2_instance,
    ec2_instance_connect_endpoint,
    ecs_service,
    eks_cluster,
    elastic_ip,
    elasticache_cluster,
    global_accelerator,
    internet_gateway,
    lambda_function,
    lattice_service,
    lattice_service_network,
    load_balancer,
    msk_cluster,
    nat_gateway,
    network_acl,
    network_firewall,
    opensearch_domain,
    rds_db_cluster,
    rds_db_instance,
    rds_db_proxy,
    route53_hosted_zone,
    route_table,
    sagemaker_domain,
    sagemaker_endpoint,
    sagemaker_notebook_instance,
    security_group,
    subnet,
    transit_gateway,
    transit_gateway_attachment,
    transit_gateway_route_table,
    vpc,
    vpc_endpoint,
    vpc_peering_connection,
    vpn_connection,
    vpn_gateway,
    waf_web_acl,
    waf_web_acl_global,
)
from .base import Service

# 순서는 `collect.py` 의 `SUPPORTED_RESOURCE_TYPES` 와 같다 — 부모가 될 수 있는 것이 먼저 온다.
REGISTRY: dict[str, Service] = {
    "vpc": vpc.SERVICE,
    "subnet": subnet.SERVICE,
    "route_table": route_table.SERVICE,
    "internet_gateway": internet_gateway.SERVICE,
    "nat_gateway": nat_gateway.SERVICE,
    "transit_gateway": transit_gateway.SERVICE,
    "transit_gateway_attachment": transit_gateway_attachment.SERVICE,
    "vpc_peering_connection": vpc_peering_connection.SERVICE,
    "vpc_endpoint": vpc_endpoint.SERVICE,
    "security_group": security_group.SERVICE,
    "network_acl": network_acl.SERVICE,
    "load_balancer": load_balancer.SERVICE,
    # 2차 — Direct Connect 3종(ADR 0010). 연결이 VIF 의 부모이므로 연결이 먼저 온다.
    "direct_connect_connection": direct_connect_connection.SERVICE,
    "direct_connect_virtual_interface": direct_connect_virtual_interface.SERVICE,
    "direct_connect_gateway": direct_connect_gateway.SERVICE,
    "ec2_instance": ec2_instance.SERVICE,
    "auto_scaling_group": auto_scaling_group.SERVICE,
    "eks_cluster": eks_cluster.SERVICE,
    "network_firewall": network_firewall.SERVICE,
    # 글로벌 2번째 — DXGW 와 같은 규약(id 리전 자리 GLOBAL · region null · 부모=계정).
    "cloudfront": cloudfront.SERVICE,
    # W2 — 워크로드·TGW 라우트 테이블. 라우트 테이블의 부모(TGW)는 위에 있다.
    "transit_gateway_route_table": transit_gateway_route_table.SERVICE,
    "rds_db_instance": rds_db_instance.SERVICE,
    "ecs_service": ecs_service.SERVICE,
    "sagemaker_notebook_instance": sagemaker_notebook_instance.SERVICE,
    "sagemaker_domain": sagemaker_domain.SERVICE,
    "sagemaker_endpoint": sagemaker_endpoint.SERVICE,
    "ec2_instance_connect_endpoint": ec2_instance_connect_endpoint.SERVICE,
    # 2차 Site-to-Site VPN 3종(2.6.0). 연결이 두 게이트웨이를 가리키므로 게이트웨이가 먼저 온다.
    "vpn_gateway": vpn_gateway.SERVICE,
    "customer_gateway": customer_gateway.SERVICE,
    "vpn_connection": vpn_connection.SERVICE,
    # 2.8.0(P2-6) — VPC 에 붙은 함수를 그 서브넷에 세운다 · LB 대상 해소의 파생 노드와 같은 id
    "lambda_function": lambda_function.SERVICE,
    # 2.8.0(P2-4) — 글로벌(세 번째) · 별칭 → LB·CloudFront · 사설 영역 ↔ VPC
    "route53_hosted_zone": route53_hosted_zone.SERVICE,
    # #114(2.9.0 · ADR 0013) — VPC 안 IP 를 쓰는 서비스. 부모(서브넷·VPC)는 위에 있다.
    "elasticache_cluster": elasticache_cluster.SERVICE,
    "rds_db_cluster": rds_db_cluster.SERVICE,
    "opensearch_domain": opensearch_domain.SERVICE,
    "msk_cluster": msk_cluster.SERVICE,
    # 배치 D(2.10.0) — 탄력적 IP(주인 = 정보창 줄 · 주인을 못 찾은 EIP = 왼쪽 아래 「!」 · 연결 없는 EIP = 화면에 없다, 2026-10-21 오전)
    "elastic_ip": elastic_ip.SERVICE,
    # 배치 D — API Gateway REST(v1) · HTTP/WebSocket(v2) = 한 노드 종류 `api_gateway` · 목록 API 가 달라 수집 종류는 둘(거부가 따로 보인다)
    "api_gateway": api_gateway.SERVICE,
    "api_gateway_v2": api_gateway_v2.SERVICE,
    # 배치 D — RDS 프록시(VPC · 걸친 서브넷) → DB 인스턴스·클러스터
    "rds_db_proxy": rds_db_proxy.SERVICE,
    # 배치 D — 네 번째 글로벌(클라이언트 = us-west-2 고정 · `Spec.region`)
    "global_accelerator": global_accelerator.SERVICE,
    # #129(2.11.0) — Cloud WAN 코어 네트워크(다섯째 글로벌 · 클라이언트 = us-west-2 고정) · VPC Lattice 서비스 네트워크 · 서비스(리전 자원)
    "core_network": core_network.SERVICE,
    "lattice_service_network": lattice_service_network.SERVICE,
    "lattice_service": lattice_service.SERVICE,
    # 2.12.0(ADR 0014 · 사용자 결정 2026-10-23) — WAF 웹 ACL(리전 범위 · CloudFront 범위 = 여섯째 글로벌 · 클라이언트 = us-east-1 고정)
    "waf_web_acl": waf_web_acl.SERVICE,
    "waf_web_acl_global": waf_web_acl_global.SERVICE,
}

__all__ = ["REGISTRY", "Service"]
