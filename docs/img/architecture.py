#!/usr/bin/env python3
"""설치 안내서의 아키텍처 그림(architecture.png · architecture.en.png)을 만든다.

AWS 공식 Architecture Icons 패키지(https://aws.amazon.com/architecture/icons/)의
그룹·서비스·리소스 아이콘을 쓰고, 배치는 Graphviz 가 한다.

준비:
  1. Graphviz 설치 (예: dnf install graphviz / brew install graphviz)
  2. 아이콘 패키지 zip 을 받아 풀고 그 폴더를 ICONS 환경 변수로 준다
     (안의 Architecture-Group-Icons_* 등 폴더 이름의 날짜는 ICON_RELEASE 로 맞춘다)

실행:
  ICONS=~/aws-icons python3 docs/img/architecture.py ko docs/img/architecture.png
  ICONS=~/aws-icons python3 docs/img/architecture.py en docs/img/architecture.en.png
"""

from __future__ import annotations

import os
import subprocess
import sys

ICONS = os.environ.get("ICONS", os.path.expanduser("~/aws-icons"))
ICON_RELEASE = os.environ.get("ICON_RELEASE", "07312026")
FONT = "Noto Sans CJK KR"

GRP = f"Architecture-Group-Icons_{ICON_RELEASE}"
SVC = f"Architecture-Service-Icons_{ICON_RELEASE}"
RES = f"Resource-Icons_{ICON_RELEASE}"
ICON = {
    "cloud": f"{GRP}/AWS-Cloud-logo_32.png",
    "account": f"{GRP}/AWS-Account_32.png",
    "stack": f"{RES}/Res_Management-Governance/Res_AWS-CloudFormation_Stack_48.png",
    "eventbridge": (
        f"{SVC}/Arch_Application-Integration/64/Arch_Amazon-EventBridge_64@5x.png"
    ),
    "codebuild": f"{SVC}/Arch_Developer-Tools/64/Arch_AWS-CodeBuild_64@5x.png",
    "s3": f"{SVC}/Arch_Storage/64/Arch_Amazon-Simple-Storage-Service_64@5x.png",
    "lambda": f"{SVC}/Arch_Compute/64/Arch_AWS-Lambda_64@5x.png",
    "cloudfront": (
        f"{SVC}/Arch_Networking-Content-Delivery/64/Arch_Amazon-CloudFront_64@5x.png"
    ),
    "role": (
        f"{RES}/Res_Security-Identity/Res_AWS-Identity-Access-Management_Role_48.png"
    ),
    "user": f"{RES}/Res_General-Icons/Res_48_Light/Res_User_48_Light.png",
    "users": f"{RES}/Res_General-Icons/Res_48_Light/Res_Users_48_Light.png",
}

TEXT = {
    "ko": {
        "cloud": "AWS Cloud",
        "monitoring": "모니터링 계정",
        "source": "소스 계정 (선택, 여러 개 가능)",
        "stack_a": "스택 A: 수집기 (nettopo-collector)",
        "stack_b": "스택 B: 소스 계정 역할<BR/>(nettopo-member-role)",
        "stack_c": "스택 C: 뷰어<BR/>(nettopo-viewer, 6단계, 선택)",
        "eb": ["EventBridge 규칙 (일정)", "1시간마다 수집 실행"],
        "cb": ["CodeBuild", "① 읽기 전용 API · 다이어그램 생성"],
        "pkg": ["S3 소스 코드 패키지 버킷", "비공개 · nettopo-source.zip (사전 준비)"],
        "s3": ["S3 아티팩트 버킷", "index.html"],
        "lm": [
            "상태 수집 Lambda (5단계, 선택)",
            "모니터링 또는 사용률 조회 중 5분마다 수집",
        ],
        "cf": ["CloudFront", "서명 URL"],
        "role": ["IAM 역할", "NetworkReadOnlyRole"],
        "dl": ["④ 다운로드 후 열기", "S3 콘솔 또는 aws s3 cp", "버킷 읽기 권한 필요"],
        "lk": ["④' 서명된 URL 로 열기", "AWS 계정 불필요"],
        "e_role": "② 소스 계정의 역할로 전환",
        "e_source": "소스 ZIP 읽기",
        "e_store": "③ 저장",
        "e_state": "상태 쓰기",
        "e_read": "읽기",
    },
    "en": {
        "cloud": "AWS Cloud",
        "monitoring": "Monitoring account",
        "source": "Source accounts (optional, one or more)",
        "stack_a": "Stack A: collector (nettopo-collector)",
        "stack_b": "Stack B: source account role<BR/>(nettopo-member-role)",
        "stack_c": "Stack C: viewer<BR/>(nettopo-viewer, step 6, optional)",
        "eb": ["EventBridge rule (schedule)", "runs collection every hour"],
        "cb": ["CodeBuild", "① read-only APIs, diagram generation"],
        "pkg": [
            "S3 source package bucket",
            "private · nettopo-source.zip (prepared beforehand)",
        ],
        "s3": ["S3 artifact bucket", "index.html"],
        "lm": [
            "State collection Lambda (step 5, optional)",
            "collects every 5 min when monitoring",
            "or utilization is enabled",
        ],
        "cf": ["CloudFront", "signed URL"],
        "role": ["IAM role", "NetworkReadOnlyRole"],
        "dl": [
            "④ Download and open",
            "S3 console or aws s3 cp",
            "bucket read permission required",
        ],
        "lk": ["④' Open with the signed URL", "no AWS account required"],
        "e_role": "② switch to the source account role",
        "e_source": "read source ZIP",
        "e_store": "③ store",
        "e_state": "write state",
        "e_read": "read",
    },
}

CLOUD = "#232F3E"
ACCOUNT = "#E7157B"
STACK = "#7D8998"
TEXT_GRAY = "#545B64"
LINE = "#545B64"


def icon(key: str) -> str:
    return os.path.join(ICONS, ICON[key])


def group_label(key: str, text: str, color: str) -> str:
    return (
        '<<TABLE border="0" cellborder="0" cellspacing="0" cellpadding="2"><TR>'
        f'<TD fixedsize="true" width="26" height="26">'
        f'<IMG scale="true" src="{icon(key)}"/></TD>'
        f'<TD align="left" balign="left" cellpadding="8">'
        f'<FONT color="{color}" point-size="12">{text}</FONT></TD>'
        "</TR></TABLE>>"
    )


def group(name: str, key: str, text: str, color: str, dashed: bool) -> str:
    style = "dashed" if dashed else "solid"
    return (
        f"subgraph cluster_{name} {{ label={group_label(key, text, color)}; "
        f'labeljust=l; color="{color}"; style={style}; penwidth=1.3; margin=24;'
    )


def service(name: str, key: str, lines: list[str]) -> str:
    """서비스 아이콘 64px 아래에 설명. AWS 다이어그램의 기본 모양."""
    rows = "<BR/>".join(lines)
    return (
        f'{name} [shape=plain, margin=0, label=<<TABLE border="0" cellborder="0" '
        'cellspacing="0" cellpadding="2">'
        f'<TR><TD fixedsize="true" width="64" height="64">'
        f'<IMG scale="true" src="{icon(key)}"/></TD></TR>'
        f"<TR><TD>{rows}</TD></TR></TABLE>>];"
    )


def person(name: str, key: str, lines: list[str]) -> str:
    """사용자 아이콘 왼쪽, 설명 오른쪽. 선이 아이콘에 닿게 한다."""
    rows = "<BR/>".join(lines)
    return (
        f'{name} [shape=plain, margin=0, label=<<TABLE border="0" cellborder="0" '
        'cellspacing="0" cellpadding="4"><TR>'
        f'<TD fixedsize="true" width="56" height="56">'
        f'<IMG scale="true" src="{icon(key)}"/></TD>'
        f'<TD align="left" balign="left">{rows}</TD></TR></TABLE>>];'
    )


def edge_label(text: str, right_gap: int) -> str:
    """선 왼쪽의 글자와 선 사이에 여백을 둔다."""
    return (
        '<<TABLE border="0" cellborder="0" cellspacing="0" cellpadding="0"><TR>'
        f'<TD>{text}</TD><TD width="{right_gap}"></TD>'
        "</TR></TABLE>>"
    )


def build(lang: str) -> str:
    t = TEXT[lang]
    parts = [
        "digraph G {",
        "  rankdir=LR; splines=ortho; nodesep=0.9; ranksep=1.5; pad=0.4; bgcolor=white;",
        f'  fontname="{FONT}"; newrank=true;',
        f'  node [fontname="{FONT}", fontsize=10];',
        f'  edge [color="{LINE}", fontname="{FONT}", fontsize=10, '
        'fontcolor="#232F3E", arrowsize=0.8, penwidth=1.2];',
        group("cloud", "cloud", t["cloud"], CLOUD, dashed=False),
        group("mon", "account", t["monitoring"], ACCOUNT, dashed=False),
        service("pkg", "s3", t["pkg"]),
        group("a", "stack", t["stack_a"], TEXT_GRAY, dashed=True).replace(
            f'color="{TEXT_GRAY}"', f'color="{STACK}"'
        ),
        service("eb", "eventbridge", t["eb"]),
        service("cb", "codebuild", t["cb"]),
        service("s3", "s3", t["s3"]),
        service("lm", "lambda", t["lm"]),
        "}",
        group("c", "stack", t["stack_c"], TEXT_GRAY, dashed=True).replace(
            f'color="{TEXT_GRAY}"', f'color="{STACK}"'
        ),
        service("cf", "cloudfront", t["cf"]),
        "}",
        "}",
        group("src", "account", t["source"], ACCOUNT, dashed=False),
        group("b", "stack", t["stack_b"], TEXT_GRAY, dashed=True).replace(
            f'color="{TEXT_GRAY}"', f'color="{STACK}"'
        ),
        service("role", "role", t["role"]),
        "}",
        "}",
        "}",
        "subgraph cluster_people { style=invis; margin=10;",
        person("lk", "users", t["lk"]),
        person("dl", "user", t["dl"]),
        "}",
        "{ rank=same; pkg; cb; }",
        "eb -> cb;",
        f"pkg -> cb [tailport=s, headport=n, xlabel={edge_label(t['e_source'], 20)}];",
        f"cb -> role [xlabel={edge_label(t['e_role'], 24)}];",
        f'cb -> s3 [xlabel="{t["e_store"]}"];',
        f'lm -> s3 [xlabel="{t["e_state"]}"];',
        f's3 -> cf [xlabel="{t["e_read"]}"];',
        "s3 -> dl;",
        "cf -> lk;",
        "}",
    ]
    return "\n".join(parts)


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in TEXT:
        print(__doc__)
        return 2
    lang, out = sys.argv[1], sys.argv[2]
    dot = build(lang)
    subprocess.run(
        ["dot", "-Tpng", "-Gdpi=110", "-o", out],
        input=dot.encode("utf-8"),
        check=True,
    )
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
