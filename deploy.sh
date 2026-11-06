#!/usr/bin/env bash
# 모니터링 계정 설치·갱신·삭제를 한 번에 — 설치 안내서(docs/install-guide.md) 1~3·5·6단계와 삭제를 대신 친다(#82).
#
# 🔵 배포 모양은 그대로다: 스택 A(수집기) · 선택 스택 C(뷰어). 수집기는 **고객 버킷의 zip 에서만** 코드를
#    가져온다(ADR 0003 D3) — 이 스크립트는 그 zip 을 만들고 올리고 스택을 올리는 손작업만 대신한다.
# 🔵 소스 계정에는 이 스크립트를 쓰지 않는다. 끝에 출력하는 스택 B 명령(CLI 한 줄 · 콘솔 · StackSet)을
#    그 계정(또는 조직) 관리자가 실행한다 — 저장소도 bash 도 요구하지 않는다.
#
# 🔴 지키는 것 (infra/tests/test_deploy_script.py 가 잰다)
#    - 모든 `aws` 호출에 `--profile "$PROFILE" --region "$REGION"` 을 적는다. 둘 다 필수 인자이고 기본값이 없다 —
#      셸 기본 프로필·리전으로 조용히 돌면 다른 계정·리전에 스택이 선다.
#    - 계정 ID 를 파일에 쓰지 않는다. 화면에 찍히는 줄의 12자리는 끝 4자리만 남긴다(`mask`).
#    - `--destroy` 는 확인 입력이 맞아야만 지운다. 건너뛰는 옵션이 없다.
#    - IAM 사용자·액세스 키를 만들지 않는다. 자격 증명은 이름 있는 프로필(IAM Identity Center 권장)뿐이다.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")" && pwd)
OUT=${OUT:-$ROOT/out}
POLL_SECONDS=${POLL_SECONDS:-15}
# 시험이 가짜 빌더를 꽂는 자리 — 기본은 저장소의 빌더다(그 빌더를 복제하지 않는다).
STATE_LAMBDA_BUILDER=${STATE_LAMBDA_BUILDER:-$ROOT/infra/build-state-lambda.sh}
LINK_ISSUER=$ROOT/infra/issue-viewer-link.sh

STACK_A=nettopo-collector
STACK_B=nettopo-member-role
STACK_C=nettopo-viewer
TEMPLATE_A=$ROOT/infra/stack-a-collector.yaml
TEMPLATE_B=$ROOT/infra/stack-b-member-role.yaml
TEMPLATE_C=$ROOT/infra/stack-c-viewer.yaml
PKG_KEY=nettopo-source.zip
CFN_PREFIX=cfn-templates
# CloudFormation 이 본문(`TemplateBody`)으로 받는 상한. 넘는 템플릿은 `--s3-bucket` 을 거쳐야 한다.
TEMPLATE_BODY_LIMIT=51200
GUIDE_TROUBLE="docs/install-guide.md#문제-해결"

usage() {
  cat << 'USAGE'
사용: ./deploy.sh --profile <프로필> --region <리전> [작업] [옵션]

모니터링 계정에 NetworkTopology 를 설치합니다(스택 A, 선택 스택 C). 소스 계정에서는 이 스크립트를 쓰지 않습니다 —
끝에 출력되는 스택 B 명령(또는 콘솔 · StackSet)을 그 계정에서 실행합니다.

작업 (생략하면 설치·갱신입니다. 다시 실행하면 갱신됩니다):
  (없음)               안내서 1~3단계: 패키지 버킷(없으면 생성) → 배포 패키지 업로드 → 스택 A 배포
                       → 수집 1회 → out/index.html
    --monitoring       위에 더해 5단계: 상태 Lambda 패키지 빌드·업로드 → 스택 A StateLambdaSourceKey
    --viewer --days N  위에 더해 6단계: 서명 키(없으면 생성) → 스택 C 배포 → N일짜리 보는 링크
  --collect            3단계만 다시: 수집 1회 → out/index.html
  --download           최신 다이어그램만 받기: S3 의 최신 index.html → out/index.html (수집·배포 없음)
                       설치와 --collect 는 끝에 이미 받아 옵니다 — 한 시간마다 자동 갱신된 그림을 다시 받을 때 씁니다
  --destroy            스택 C → 스택 A → 로그·산출물·패키지 버킷(모든 버전) 삭제. 확인 입력을 받습니다

스택 C 서명 키 교체 (스택 C 만 바꿉니다 — 스택 A·수집은 건드리지 않습니다):
  --viewer --rotate-key --days N
                       새 키를 빈 자리(k1↔k2)에 만들어 넣고 옛 키는 키 그룹에 남깁니다(겹침) → 새 키로 N일 링크.
                       옛 링크는 만료까지 열립니다. 옛 키로 낸 링크의 가장 늦은 만료(이 PC 의 발급 기록)를 알려 드립니다
  --viewer --retire-old-key
                       겹침을 끝냅니다: 옛 키를 키 그룹에서 뺍니다 → 옛 링크는 만료 전이라도 403. 확인 입력을 받습니다
  --viewer --rotate-key --no-overlap --days N
                       유출 대응: 새 키로 바꾸면서 옛 키를 같은 갱신에서 뺍니다 → 지금까지 낸 링크 전부 403.
                       겹침 중이면 물러나던 키까지 둘 다 뺍니다(갱신 두 번). 확인 입력을 받습니다

필수 (기본값 없음):
  --profile NAME       모니터링 계정 프로필. IAM Identity Center 권장, 장기 액세스 키 금지
                       AWS CloudShell 에서는 생략합니다(콘솔에 로그인한 권한을 그대로 씁니다)
  --region REGION      스택 A·C 와 패키지 버킷을 둘 리전

옵션:
  --bucket NAME        패키지 버킷(전 세계 유일). 처음 설치에 필수, 이후에는 스택 A 의 SourceLocation 에서 읽습니다
  --regions LIST       수집 리전(쉼표 구분). 처음 설치에 필수, 생략하면 이전 값 유지
  --accounts LIST      TGW 가 모르는 소스 계정 ID(쉼표 구분) → TargetAccounts. 생략하면 이전 값 유지
  --tags LIST          스택 태그 Key=Value[,Key=Value]. 생략하면 기존 스택의 태그를 그대로 다시 붙입니다
  --package ZIP        직접 만든 배포 패키지 zip 을 쓸 때 (기본: git archive HEAD engine renderer buildspec.yml)
  --viewer-key PEM     스택 C 링크 서명 비밀키 (기본: out/viewer-<스택의 KeyLabel>.pem, 처음 설치는 k1 · 없으면 만듭니다).
                       --rotate-key 와 함께면 **새** 키 파일(있으면 그 키를 넣고, 없으면 만듭니다)
  --log-readers ARNS   스택 C 액세스 로그를 읽을 IAM 역할·사용자 ARN(쉼표 구분 · 와일드카드·세션 ARN 불가)
                       → LogReaderPrincipalArns. 로그에 서명 링크 전체가 남아 읽는 사람은 링크를 다시 쓸 수 있습니다.
                       생략하면 이전 값 유지(처음 설치는 비어 있음 = 계정 루트만 읽습니다)
  --dry-run            변경 세트만 만들고 실행하지 않습니다. 버킷 생성·업로드·빌드·링크·삭제를 하지 않습니다
  -h, --help           이 도움말

선택 보강 (기본 꺼짐 · 안내서 「선택 보강」을 이 실행 한 번으로 합니다. 주지 않으면 지금 동작 그대로입니다):
  --external-id ID     소스 계정 역할에 외부 ID 조건을 겁니다 → 스택 A MemberExternalId, 그리고 끝에 출력되는 스택 B
                       명령(CLI 한 줄 · StackSet)의 ExternalId. 영문·숫자와 _+=,.@:/- 로 된 2~1224자입니다.
                       생략하면 스택 A 의 이전 값을 유지하고, 출력되는 스택 B 명령에도 그 값을 넣습니다.
                       스택 B 에만 넣으면 수집이 거부됩니다 — 이 옵션으로 스택 A 를 먼저 갱신한 뒤 소스 계정의 스택 B 를 갱신해 주세요
  --org-id o-ID        끝에 출력되는 스택 B 명령(CLI 한 줄 · StackSet)에 PrincipalOrgId 를 넣습니다 → 그 AWS Organizations
                       조직의 주체만 역할에 들어올 수 있습니다(모니터링 계정도 같은 조직이어야 합니다).
                       어디에도 저장하지 않으니 스택 B 명령을 다시 받을 때도 함께 주세요
  --narrow-viewer      --viewer 와 함께 씁니다. 스택 C 를 올린 뒤 그 출력 ViewerDomain·DistributionId 로 스택 A 를 갱신합니다
                       (ViewerOrigin=https://<ViewerDomain> · ViewerDistributionId) → 산출물 버킷 CORS 를 그 주소 하나로,
                       CloudFront 의 index.html 읽기를 그 배포 하나로 좁힙니다. 한 번 넣은 값은 이후 생략해도 유지됩니다

out/ 에 남는 것 (커밋하지 마세요):
  index.html                 다이어그램
  templates/                 소스 계정·조직 관리자에게 넘길 스택 B·C 템플릿 + SHA256SUMS
  viewer-k1.pem · viewer-k2.pem  링크 서명 비밀키 — 키 자리마다 하나 (이 PC 밖으로 보내지 마세요)

out/ 밖 (이 PC 의 사용자 폴더):
  ~/.local/state/nettopo/viewer-links.log
                             발급 기록: 링크마다 「만료 · Key-Pair-Id · 발급 시각」(링크·서명은 없습니다) — 키 교체가
                             「옛 키를 빼도 되는 때」를 여기서 읽습니다(XDG_STATE_HOME 이 있으면 그 아래)

실패하면 「N단계 <무엇> 실패: <이유>」 한 줄과 안내서 문제 해결 표의 행을 알려 드립니다.
USAGE
}

# ── AWS CloudShell ──────────────────────────────────────────────────────────────────────────────
# CloudShell 에는 이름 있는 프로필이 없다 — 콘솔에 로그인한 권한이 기본 자격 증명이다. 그래서 거기서만 --profile 을
# 생략할 수 있다(그 밖에서는 지금처럼 필수 — 셸 기본 자격 증명으로 조용히 다른 계정에 서는 것을 막는다).
#   신호 두 가지: AWS_EXECUTION_ENV=CloudShell(AWS CLI 사용자 에이전트가 쓰는 값 · 문서로는 확인하지 못했다) ·
#   홈 폴더 /home/cloudshell-user(사용자 실측 2026-10-26: 설치 출력의 경로가 /home/cloudshell-user/… 였다).
#   🔴 프로필 없는 셸에서 `--profile default` 는 「The config profile (default) could not be found」로 멈춘다(실측 재현).
IN_CLOUDSHELL=0
if [ "${AWS_EXECUTION_ENV:-}" = CloudShell ] || [ "$HOME" = /home/cloudshell-user ]; then IN_CLOUDSHELL=1; fi
# 모든 aws 호출은 `--profile "$PROFILE" --region "$REGION"` 을 적는다(시험이 잰다). 프로필을 생략한 CloudShell 실행이면
# 이 감싸개가 빈 `--profile ""` 한 쌍만 빼고 넘긴다 — 호출 자리마다 분기를 두지 않는다.
aws() {
  if [ -n "$PROFILE" ]; then
    command aws "$@"
    return
  fi
  local args=() skip=0 x
  for x in "$@"; do
    if [ "$skip" = 1 ]; then skip=0; continue; fi
    if [ "$x" = --profile ]; then skip=1; continue; fi
    args+=("$x")
  done
  command aws "${args[@]}"
}

# ── 출력 ────────────────────────────────────────────────────────────────────────────────────────
# 12자리 숫자(계정 ID 모양)는 끝 4자리만 남긴다. 더 긴 숫자열(타임스탬프 등)은 건드리지 않는다.
ACCOUNT_LIKE='(^|[^0-9])([0-9]{12})([^0-9]|$)'
mask() {
  local s=$1 out="" m
  while [[ $s =~ $ACCOUNT_LIKE ]]; do
    m=${BASH_REMATCH[0]}
    out+="${s%%"$m"*}${BASH_REMATCH[1]}…${BASH_REMATCH[2]:8}"
    s="${BASH_REMATCH[3]}${s#*"$m"}"
  done
  out="$out$s"
  # 프로필을 생략한 CloudShell 실행 — 안내 명령에 남는 빈 `--profile ` 을 지운다(그대로 따라 치면 그 명령이 멈춘다).
  [ -n "$PROFILE" ] || out=${out//--profile  /}
  printf '%s\n' "$out"
}
mask_stream() {
  local line
  while IFS= read -r line || [ -n "$line" ]; do mask "$line"; done
}
say() { mask "$*"; }
step() { echo; say "== $* =="; }
die() {
  say "deploy.sh: $*" >&2
  echo "도움말: ./deploy.sh --help" >&2
  exit 2
}
# 실패 한 줄: 「N단계 <무엇> 실패: <이유>」 + 안내서 문제 해결 표의 행.
fail() {
  local where=$1 what=$2 reason=$3 row=$4
  reason=$(printf '%s' "$reason" | tr '\n\t' '  ' | sed 's/  */ /g; s/^ //; s/ $//')
  echo >&2
  say "🔴 ${where} ${what} 실패: ${reason:-이유를 읽지 못했습니다}" >&2
  echo "   → 안내서 문제 해결 표 「${row}」 행: ${GUIDE_TROUBLE}" >&2
  exit 1
}

# ── 인자 ────────────────────────────────────────────────────────────────────────────────────────
PROFILE="" REGION="" BUCKET="" REGIONS="" ACCOUNTS="" TAGS_ARG="" PACKAGE="" VIEWER_KEY="" DAYS="" LOG_READERS=""
DRY_RUN=0 MONITORING=0 VIEWER=0 ACTION=install KEY_OP="" NO_OVERLAP=0
EXTERNAL_ID="" ORG_ID="" NARROW_VIEWER=0
need_value() { [ $# -ge 2 ] && [ -n "$2" ] && [ "${2#--}" = "$2" ] || die "$1 에 값이 없습니다"; }
while [ $# -gt 0 ]; do
  case "$1" in
    --profile) need_value "$@"; PROFILE=$2; shift 2 ;;
    --region) need_value "$@"; REGION=$2; shift 2 ;;
    --bucket) need_value "$@"; BUCKET=$2; shift 2 ;;
    --regions) need_value "$@"; REGIONS=$2; shift 2 ;;
    --accounts) need_value "$@"; ACCOUNTS=$2; shift 2 ;;
    --tags) need_value "$@"; TAGS_ARG=$2; shift 2 ;;
    --package) need_value "$@"; PACKAGE=$2; shift 2 ;;
    --viewer-key) need_value "$@"; VIEWER_KEY=$2; shift 2 ;;
    --days) need_value "$@"; DAYS=$2; shift 2 ;;
    --log-readers) need_value "$@"; LOG_READERS=$2; shift 2 ;;
    --external-id) need_value "$@"; EXTERNAL_ID=$2; shift 2 ;;
    --org-id) need_value "$@"; ORG_ID=$2; shift 2 ;;
    --narrow-viewer) NARROW_VIEWER=1; shift ;;
    --monitoring) MONITORING=1; shift ;;
    --viewer) VIEWER=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --rotate-key | --retire-old-key)
      [ -z "$KEY_OP" ] || die "--rotate-key 와 --retire-old-key 는 함께 쓸 수 없습니다"
      KEY_OP=${1#--}
      shift
      ;;
    --no-overlap) NO_OVERLAP=1; shift ;;
    --collect | --download | --destroy)
      [ "$ACTION" = install ] || die "--collect·--download·--destroy 는 함께 쓸 수 없습니다"
      ACTION=${1#--}
      shift
      ;;
    -h | --help) usage; exit 0 ;;
    *) die "모르는 인자: $1" ;;
  esac
done

[ -n "$PROFILE" ] || [ "$IN_CLOUDSHELL" = 1 ] || die "--profile 이 없습니다 — 모니터링 계정 프로필을 적어 주세요(기본값 없음 · AWS CloudShell 에서는 생략할 수 있습니다)"
[ -n "$REGION" ] || die "--region 이 없습니다 — 스택을 둘 리전을 적어 주세요(기본값 없음)"
REGION_RE='^[a-z]{2}(-[a-z]+)+-[0-9]+$'
[[ $REGION =~ $REGION_RE ]] || die "--region 이 리전 이름 모양이 아닙니다: $REGION"
if [ -n "$REGIONS" ]; then
  IFS=, read -r -a _list <<< "$REGIONS"
  for r in "${_list[@]}"; do [[ $r =~ $REGION_RE ]] || die "--regions 에 리전이 아닌 값이 있습니다: '$r'"; done
fi
[ -z "$ACCOUNTS" ] || [[ $ACCOUNTS =~ ^[0-9]{12}(,[0-9]{12})*$ ]] \
  || die "--accounts 는 12자리 계정 ID 를 쉼표로 이어 적어 주세요"
[ -z "$BUCKET" ] || [[ $BUCKET =~ ^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$ ]] \
  || die "--bucket 이 S3 버킷 이름 모양이 아닙니다: $BUCKET"
[ -z "$TAGS_ARG" ] || [[ $TAGS_ARG =~ ^[^=,]+=[^,]*(,[^=,]+=[^,]*)*$ ]] \
  || die "--tags 는 Key=Value 를 쉼표로 이어 적어 주세요"
# 스택 B ExternalId · 스택 A MemberExternalId 의 AllowedPattern(`^[-\w+=,.@:/]{2,1224}$`)과 같은 모양 — 틀린 값을 변경 세트
# 거절까지 끌고 가지 않는다. `\w` 는 풀어 적고(bash 정규식에 없는 플랫폼이 있다) 길이는 따로 잰다(macOS 정규식은
# `{m,n}` 상한이 255 라 `{2,1224}` 를 오류로 본다 → 모든 값이 거절된다). 값은 오류 줄에 찍지 않는다.
if [ -n "$EXTERNAL_ID" ]; then
  [ "${#EXTERNAL_ID}" -ge 2 ] && [ "${#EXTERNAL_ID}" -le 1224 ] && [[ $EXTERNAL_ID =~ ^[-A-Za-z0-9_+=,.@:/]+$ ]] \
    || die "--external-id 는 영문·숫자와 _+=,.@:/- 로 된 2~1224자여야 합니다(스택 B ExternalId 와 같은 규칙)"
fi
[ -z "$ORG_ID" ] || [[ $ORG_ID =~ ^o-[a-z0-9]{10,32}$ ]] \
  || die "--org-id 는 AWS Organizations 조직 ID 모양(o- 뒤 영문 소문자·숫자 10~32자)이어야 합니다"
# 두 값은 출력되는 스택 B 명령에 들어간다 — 화면 출력은 12자리 숫자를 계정 ID 로 보고 가리므로(mask), 그런 숫자가 든 값은
# 따라 칠 명령이 조용히 틀어진다. 그 전에 멈춘다.
for _v in "$EXTERNAL_ID" "$ORG_ID"; do
  [[ ! $_v =~ $ACCOUNT_LIKE ]] \
    || die "--external-id·--org-id 에 12자리 숫자(계정 ID 모양)가 있습니다 — 화면에서 가려져 출력되는 스택 B 명령이 틀어집니다. 다른 값을 주세요"
done
if [ "$ACTION" != install ]; then
  [ "$MONITORING" = 0 ] && [ "$VIEWER" = 0 ] && [ -z "$PACKAGE" ] && [ -z "$REGIONS" ] \
    && [ -z "$ACCOUNTS" ] && [ -z "$TAGS_ARG" ] && [ -z "$DAYS" ] && [ -z "$VIEWER_KEY" ] && [ -z "$LOG_READERS" ] \
    && [ -z "$EXTERNAL_ID" ] && [ -z "$ORG_ID" ] \
    || die "--$ACTION 에는 --profile·--region·--bucket·--dry-run 만 함께 줄 수 있습니다"
fi
if [ "$VIEWER" = 1 ]; then
  # 링크 수명은 관리자가 고른다 — issue-viewer-link.sh 와 같은 원칙이라 기본값을 두지 않는다.
  if [ "$KEY_OP" = retire-old-key ]; then
    # 옛 키 빼기는 링크를 만들지 않는다 — 새 키 링크는 교체 때 이미 냈다.
    [ -z "$DAYS" ] && [ -z "$VIEWER_KEY" ] || die "--retire-old-key 는 링크를 만들지 않습니다 — --days·--viewer-key 를 빼고 실행해 주세요"
  else
    [ -n "$DAYS" ] || die "--viewer 에는 --days <일수> 가 필요합니다(링크 수명, 기본값 없음)"
    [[ $DAYS =~ ^[1-9][0-9]*$ ]] || die "--days 는 1 이상의 정수여야 합니다"
  fi
  # 스택 C 의 LogReaderPrincipalArns AllowedPattern 과 같은 모양 — 틀린 값을 변경 세트 거절까지 끌고 가지 않는다.
  # 와일드카드(`role/*`)를 받으면 ArnNotLike 예외가 계정 전체로 번진다(#81).
  READER_ARN='arn:aws[a-z-]*:iam::[0-9]{12}:(role|user)/[A-Za-z0-9+=.@_/-]+'
  [ -z "$LOG_READERS" ] || [[ $LOG_READERS =~ ^$READER_ARN(,$READER_ARN)*$ ]] \
    || die "--log-readers 는 IAM 역할·사용자 ARN 을 공백 없이 쉼표로 이어 적어 주세요(와일드카드·assumed-role 세션 ARN 불가): $LOG_READERS"
else
  [ -z "$DAYS" ] && [ -z "$VIEWER_KEY" ] && [ -z "$LOG_READERS" ] \
    || die "--days·--viewer-key·--log-readers 는 --viewer 와 함께 씁니다"
  # 좁힐 주소·배포 ID 는 스택 C 의 출력이다 — 스택 C 를 올리는 실행에서만 읽는다.
  [ "$NARROW_VIEWER" = 0 ] || die "--narrow-viewer 는 --viewer 와 함께 씁니다(스택 C 의 주소·배포 ID 로 스택 A 를 좁힙니다)"
fi
if [ -n "$KEY_OP" ] || [ "$NO_OVERLAP" = 1 ]; then
  [ "$VIEWER" = 1 ] || die "--rotate-key·--retire-old-key·--no-overlap 은 --viewer 와 함께 씁니다"
  [ "$NO_OVERLAP" = 0 ] || [ "$KEY_OP" = rotate-key ] || die "--no-overlap 은 --rotate-key 와 함께 씁니다"
  # 키 교체는 스택 C 만 바꾼다 — 유출 대응이 수집(수 분)을 기다리지 않게 스택 A·수집 단계를 타지 않는다.
  [ "$MONITORING" = 0 ] && [ -z "$PACKAGE" ] && [ -z "$REGIONS" ] && [ -z "$ACCOUNTS" ] && [ -z "$TAGS_ARG" ] && [ -z "$BUCKET" ] \
    || die "키 교체는 스택 C 만 바꿉니다 — --bucket·--regions·--accounts·--tags·--package·--monitoring 과 함께 쓸 수 없습니다"
  [ -z "$EXTERNAL_ID" ] && [ -z "$ORG_ID" ] && [ "$NARROW_VIEWER" = 0 ] \
    || die "키 교체는 스택 C 만 바꿉니다 — --external-id·--org-id·--narrow-viewer 와 함께 쓸 수 없습니다"
fi
# 기본 비밀키 경로(out/viewer-<자리>.pem)는 스택 C 의 KeyLabel 을 읽은 뒤 정한다(viewer · rotate_key).

# ── 0단계 · 준비물과 자격 증명 ───────────────────────────────────────────────────────────────────
ACCOUNT="" PARTITION=""
preflight() {
  step "0단계 · 준비물과 자격 증명 확인"
  type -P aws > /dev/null || fail "0단계" "준비물 확인" "aws 명령이 없습니다(AWS CLI v2 를 설치해 주세요)" "deploy.sh 가 0단계 준비물 확인에서 멈춤"
  case "$(aws --version 2>&1)" in
    aws-cli/2.*) ;;
    *) fail "0단계" "준비물 확인" "AWS CLI v2 가 아닙니다: $(aws --version 2>&1 | head -1)" "deploy.sh 가 0단계 준비물 확인에서 멈춤" ;;
  esac
  if [ "$VIEWER" = 1 ]; then
    command -v openssl > /dev/null || fail "0단계" "준비물 확인" "openssl 이 없습니다(--viewer 에 필요)" "deploy.sh 가 0단계 준비물 확인에서 멈춤"
  fi
  local ident
  if ! ident=$(aws sts get-caller-identity --query '[Account,Arn]' --output text \
    --profile "$PROFILE" --region "$REGION" 2>&1); then
    local hint="~/.aws/config 에 그 프로필이 있는지 확인해 주세요 · 만료면 aws sso login --profile $PROFILE --region $REGION"
    [ -n "$PROFILE" ] || hint="CloudShell 을 연 콘솔 로그인이 만료됐으면 콘솔에 다시 로그인한 뒤 CloudShell 을 새로 열어 주세요"
    fail "0단계" "자격 증명 확인" "$(printf '%s' "$ident" | grep -v '^$' | tail -1) ($hint)" "deploy.sh 가 0단계 자격 증명 확인에서 멈춤"
  fi
  ACCOUNT=$(printf '%s' "$ident" | awk '{print $1}')
  PARTITION=$(printf '%s' "$ident" | awk '{print $2}' | cut -d: -f2)
  [[ $ACCOUNT =~ ^[0-9]{12}$ ]] || fail "0단계" "자격 증명 확인" "계정 ID 를 읽지 못했습니다" "deploy.sh 가 0단계 자격 증명 확인에서 멈춤"
  # 계정 ID 는 이 셸 변수에만 있다 — 파일에 쓰지 않고, 화면에는 mask 를 거친다.
  say "모니터링 계정 $ACCOUNT · 리전 $REGION · $([ -n "$PROFILE" ] && echo "프로필 $PROFILE" || echo "CloudShell 로그인 권한")$([ "$DRY_RUN" = 1 ] && echo ' · 시험 실행(--dry-run)')"
}

# ── 스택 조회 ───────────────────────────────────────────────────────────────────────────────────
# NONE = 스택 없음. 없음이 아닌 조회 실패(권한 등)는 멈춘다 — 「없음」으로 오판하면 새로 만들려 든다.
stack_status() {
  local out
  if out=$(aws cloudformation describe-stacks --stack-name "$1" --query 'Stacks[0].StackStatus' \
    --output text --profile "$PROFILE" --region "$REGION" 2>&1); then
    printf '%s\n' "$out"
  elif printf '%s' "$out" | grep -q 'does not exist'; then
    echo NONE
  else
    fail "0단계" "스택 $1 조회" "$out" "deploy.sh 가 0단계 자격 증명 확인에서 멈춤"
  fi
}
# 스택의 출력·파라미터 하나. 조회가 실패하면 aws 오류를 한 줄로 알리고 멈춘다(빈 값으로 넘어가지 않는다).
stack_field() { # <스택> <Outputs|Parameters> <키>
  local kind=$2 field=OutputKey value=OutputValue out
  [ "$kind" = Outputs ] || { field=ParameterKey; value=ParameterValue; }
  out=$(aws cloudformation describe-stacks --stack-name "$1" \
    --query "Stacks[0].${kind}[?${field}=='$3'].${value}" --output text \
    --profile "$PROFILE" --region "$REGION" 2>&1) \
    || fail "스택 조회" "$1 의 $3" "$out" "deploy.sh 가 0단계 자격 증명 확인에서 멈춤"
  [ "$out" = None ] || printf '%s\n' "$out"
}
stack_output() { stack_field "$1" Outputs "$2"; }
stack_parameter() { stack_field "$1" Parameters "$2"; }
# 스택 이벤트에서 처음 실패한 리소스의 이유(가장 최근 것부터 본다).
stack_failure_reason() {
  aws cloudformation describe-stack-events --stack-name "$1" \
    --query "StackEvents[?contains(ResourceStatus, 'FAILED') && ResourceStatusReason != null] | [0].[LogicalResourceId, ResourceStatusReason]" \
    --output text --profile "$PROFILE" --region "$REGION" 2> /dev/null | sed 's/^None.*$//' || true
}
# 스택이 지금 손댈 수 있는 상태인가. 진행 중·생성 실패 상태면 이유와 함께 멈춘다.
check_stack_usable() {
  local stack=$1 where=$2 status
  status=$(stack_status "$stack")
  case "$status" in
    *_IN_PROGRESS)
      [ "$status" = REVIEW_IN_PROGRESS ] && return 0
      fail "$where" "스택 $stack 확인" "다른 작업이 진행 중입니다($status). 끝난 뒤 다시 실행해 주세요" "스택이 진행 중 또는 ROLLBACK_COMPLETE"
      ;;
    ROLLBACK_COMPLETE | ROLLBACK_FAILED | DELETE_FAILED)
      fail "$where" "스택 $stack 확인" "스택이 $status 상태라 갱신할 수 없습니다 — 이유: $(stack_failure_reason "$stack")" "스택이 진행 중 또는 ROLLBACK_COMPLETE"
      ;;
  esac
}

# ── 태그 ────────────────────────────────────────────────────────────────────────────────────────
# `deploy` 는 --tags 를 빼면 기존 태그를 지운다. --tags 를 주면 그것을, 안 주면 기존 스택 태그를 다시 넘긴다.
TAGS=()
resolve_tags() {
  TAGS=()
  local k v pair
  if [ -n "$TAGS_ARG" ]; then
    IFS=, read -r -a _pairs <<< "$TAGS_ARG"
    for pair in "${_pairs[@]}"; do TAGS+=("$pair"); done
    return
  fi
  local status
  status=$(stack_status "$1")
  case "$status" in
    NONE | REVIEW_IN_PROGRESS)
      # 새로 만드는 스택(예: 나중에 얹는 스택 C)은 다시 읽을 태그가 없다 — 스택 A 의 태그를 물려받는다(실측 2026-10-22: 스택 C Tags=[])
      if [ "$1" != "$STACK_A" ]; then resolve_tags "$STACK_A"; fi
      return ;;
  esac
  while IFS=$'\t' read -r k v; do
    [ -n "$k" ] && [ "$k" != None ] && TAGS+=("$k=$v")
  done < <(aws cloudformation describe-stacks --stack-name "$1" --query 'Stacks[0].Tags[].[Key,Value]' \
    --output text --profile "$PROFILE" --region "$REGION")
}

# ── 배포 ────────────────────────────────────────────────────────────────────────────────────────
# cfn_deploy <단계> <무엇> <문제 해결 행> <스택> <템플릿> <capability|-> [파라미터=값 ...]
# 멱등: 바뀐 것이 없으면 빈 변경 세트로 끝난다. 적지 않은 파라미터는 이전 값이 유지된다.
cfn_deploy() {
  local where=$1 what=$2 row=$3 stack=$4 template=$5 caps=$6
  shift 6
  local args=(--template-file "$template" --stack-name "$stack" --no-fail-on-empty-changeset)
  [ "$caps" = - ] || args+=(--capabilities "$caps")
  [ $# -eq 0 ] || args+=(--parameter-overrides "$@")
  resolve_tags "$stack"
  [ ${#TAGS[@]} -eq 0 ] || args+=(--tags "${TAGS[@]}")
  if [ "$BUCKET_READY" = 1 ]; then
    # 템플릿은 패키지 버킷을 거친다(본문 한도와 무관하게 늘 같은 길) — 영어 한 벌이라 S3 경유 시 `?` 문제가 없다.
    args+=(--s3-bucket "$BUCKET" --s3-prefix "$CFN_PREFIX")
  elif [ "$(wc -c < "$template")" -gt "$TEMPLATE_BODY_LIMIT" ]; then
    fail "$where" "$what" "템플릿이 본문 한도($TEMPLATE_BODY_LIMIT B)를 넘는데 올릴 버킷이 아직 없습니다(--dry-run 첫 설치)" "$row"
  fi
  [ "$DRY_RUN" = 0 ] || args+=(--no-execute-changeset)
  if [ "$DRY_RUN" = 1 ]; then say "$what — $stack (변경 세트만 만듭니다)"; else say "$what — $stack (수 분 걸립니다)"; fi
  # 출력은 변수로 받는다 — 변경 세트 ARN 에 계정 ID 가 있어 파일에도, 가리지 않은 화면에도 두지 않는다.
  local out rc=0
  out=$(aws cloudformation deploy "${args[@]}" --profile "$PROFILE" --region "$REGION" 2>&1) || rc=$?
  if [ "$rc" -ne 0 ]; then
    # 변경 세트 단계의 거절(검증 오류 등)은 deploy 출력에, 실행 단계의 실패는 스택 이벤트에 이유가 있다.
    local reason
    reason=$(printf '%s\n' "$out" | grep -E 'An error occurred|Status: FAILED|Waiter .* failed' | tail -1 || true)
    [ -n "$reason" ] || reason=$(stack_failure_reason "$stack")
    [ -n "$reason" ] || reason=$(printf '%s\n' "$out" | grep -v '^$' | tail -1 || true)
    fail "$where" "$what" "$reason" "$row"
  fi
  if [ "$DRY_RUN" = 0 ]; then
    printf '%s\n' "$out" | grep -E 'Successfully|No changes' | mask_stream || true
    return
  fi
  if printf '%s' "$out" | grep -q 'No changes'; then
    say "  바뀌는 것이 없습니다"
    return
  fi
  local changeset
  changeset=$(printf '%s\n' "$out" | grep -Eo 'arn:aws[a-z-]*:cloudformation:[^ ]+:changeSet/[^ ]+' | tail -1 || true)
  [ -n "$changeset" ] || { say "  변경 세트를 찾지 못했습니다"; return; }
  say "  변경 세트 ${changeset##*:changeSet/} — 실행하지 않았습니다. 바뀌는 리소스(동작 · 논리 ID · 종류 · 교체):"
  aws cloudformation describe-change-set --change-set-name "$changeset" \
    --query 'Changes[].ResourceChange.[Action, LogicalResourceId, ResourceType, Replacement]' --output text \
    --profile "$PROFILE" --region "$REGION" 2>&1 | sed 's/^/    /' | mask_stream
}

# ── 1단계 · 패키지 버킷 ─────────────────────────────────────────────────────────────────────────
BUCKET_READY=0
resolve_bucket() {
  [ -n "$BUCKET" ] && return
  local status
  status=$(stack_status "$STACK_A")
  case "$status" in
    NONE | REVIEW_IN_PROGRESS) ;;
    *) BUCKET=$(stack_parameter "$STACK_A" SourceLocation | cut -d/ -f1) ;;
  esac
  [ -n "$BUCKET" ] && say "패키지 버킷 = 스택 A 의 SourceLocation 버킷: $BUCKET"
  return 0
}
bucket_policy() {
  # 공개 정책이 아니다(Deny 뿐) — TLS 가 아닌 요청을 막는다(인프라 불변식 · 스택 A·C 버킷과 같은 규칙).
  printf '{"Version":"2012-10-17","Statement":[{"Sid":"DenyInsecureTransport","Effect":"Deny","Principal":"*","Action":"s3:*","Resource":["arn:%s:s3:::%s","arn:%s:s3:::%s/*"],"Condition":{"Bool":{"aws:SecureTransport":"false"}}}]}' \
    "$PARTITION" "$BUCKET" "$PARTITION" "$BUCKET"
}
json_escape() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }
bucket_tagging() {
  local pair sep="" body=""
  for pair in "${TAGS[@]}"; do
    body+="$sep{\"Key\":\"$(json_escape "${pair%%=*}")\",\"Value\":\"$(json_escape "${pair#*=}")\"}"
    sep=,
  done
  printf '{"TagSet":[%s]}' "$body"
}
ensure_bucket() {
  step "1단계 · 패키지 버킷 $BUCKET"
  local out
  if out=$(aws s3api head-bucket --bucket "$BUCKET" --expected-bucket-owner "$ACCOUNT" \
    --profile "$PROFILE" --region "$REGION" 2>&1); then
    # 있는 버킷이면 공개 차단 네 항목이 전부 켜져 있어야 쓴다 — 코드가 들어가는 버킷이다. 설정은 바꾸지 않는다.
    local pab
    pab=$(aws s3api get-public-access-block --bucket "$BUCKET" --expected-bucket-owner "$ACCOUNT" \
      --query 'PublicAccessBlockConfiguration.[BlockPublicAcls,IgnorePublicAcls,BlockPublicPolicy,RestrictPublicBuckets]' \
      --output text --profile "$PROFILE" --region "$REGION" 2>&1) || pab=""
    [ "$(printf '%s' "$pab" | tr -s ' \t' '\n' | grep -c '^True$')" = 4 ] \
      || fail "1단계" "버킷 확인" "$BUCKET 의 공개 차단(Block Public Access)이 전부 켜져 있지 않습니다. 켠 뒤 다시 실행해 주세요" "1단계 버킷 확인 실패"
    local loc
    loc=$(aws s3api get-bucket-location --bucket "$BUCKET" --expected-bucket-owner "$ACCOUNT" \
      --query LocationConstraint --output text --profile "$PROFILE" --region "$REGION" 2>&1) \
      || fail "1단계" "버킷 확인" "$loc" "1단계 버킷 확인 실패"
    [ "$loc" != None ] || loc=us-east-1 # LocationConstraint 가 비어 있으면 us-east-1 버킷이다
    [ "$loc" = "$REGION" ] \
      || fail "1단계" "버킷 확인" "$BUCKET 은 $loc 에 있습니다(스택 리전 $REGION 과 다릅니다). 수집기(CodeBuild)는 같은 리전 버킷의 소스만 읽습니다" "1단계 버킷 확인 실패"
    say "있는 버킷을 사용합니다(공개 차단 · 리전 확인)"
    BUCKET_READY=1
    return
  fi
  case "$out" in
    *404* | *"Not Found"*) ;;
    *403* | *Forbidden*) fail "1단계" "버킷 확인" "$BUCKET 은 다른 계정의 버킷이거나 접근이 막혀 있습니다. 다른 이름을 --bucket 으로 주세요" "1단계 버킷 확인 실패" ;;
    *301* | *Moved*) fail "1단계" "버킷 확인" "$BUCKET 이 다른 리전에 있습니다. CodeBuild 는 같은 리전의 버킷에서만 소스를 읽습니다" "1단계 버킷 확인 실패" ;;
    *) fail "1단계" "버킷 확인" "$out" "1단계 버킷 확인 실패" ;;
  esac
  if [ "$DRY_RUN" = 1 ]; then
    say "버킷이 없습니다 — 실제 실행이면 만듭니다(공개 차단 · SSE-S3 · 버전 관리 · TLS 강제). --dry-run 이라 건너뜁니다"
    return
  fi
  if [ "$REGION" = us-east-1 ]; then # us-east-1 은 LocationConstraint 를 받지 않는다(주면 거절된다)
    out=$(aws s3api create-bucket --bucket "$BUCKET" --profile "$PROFILE" --region "$REGION" 2>&1) \
      || fail "1단계" "버킷 만들기" "$out" "1단계 버킷 만들기 실패"
  else
    out=$(aws s3api create-bucket --bucket "$BUCKET" --create-bucket-configuration "LocationConstraint=$REGION" \
      --profile "$PROFILE" --region "$REGION" 2>&1) \
      || fail "1단계" "버킷 만들기" "$out" "1단계 버킷 만들기 실패"
  fi
  out=$(aws s3api put-public-access-block --bucket "$BUCKET" --expected-bucket-owner "$ACCOUNT" \
    --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true \
    --profile "$PROFILE" --region "$REGION" 2>&1) || fail "1단계" "공개 차단 켜기" "$out" "1단계 버킷 만들기 실패"
  out=$(aws s3api put-bucket-encryption --bucket "$BUCKET" --expected-bucket-owner "$ACCOUNT" \
    --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}' \
    --profile "$PROFILE" --region "$REGION" 2>&1) || fail "1단계" "기본 암호화 켜기" "$out" "1단계 버킷 만들기 실패"
  out=$(aws s3api put-bucket-versioning --bucket "$BUCKET" --expected-bucket-owner "$ACCOUNT" \
    --versioning-configuration Status=Enabled \
    --profile "$PROFILE" --region "$REGION" 2>&1) || fail "1단계" "버전 관리 켜기" "$out" "1단계 버킷 만들기 실패"
  out=$(aws s3api put-bucket-policy --bucket "$BUCKET" --expected-bucket-owner "$ACCOUNT" --policy "$(bucket_policy)" \
    --profile "$PROFILE" --region "$REGION" 2>&1) || fail "1단계" "TLS 강제 정책" "$out" "1단계 버킷 만들기 실패"
  resolve_tags "$STACK_A"
  if [ ${#TAGS[@]} -gt 0 ]; then
    out=$(aws s3api put-bucket-tagging --bucket "$BUCKET" --expected-bucket-owner "$ACCOUNT" --tagging "$(bucket_tagging)" \
      --profile "$PROFILE" --region "$REGION" 2>&1) || fail "1단계" "버킷 태그" "$out" "1단계 버킷 만들기 실패"
  fi
  say "버킷을 만들었습니다(공개 차단 · SSE-S3 · 버전 관리 · TLS 강제)"
  BUCKET_READY=1
}

# ── 1단계 · 배포 패키지 ─────────────────────────────────────────────────────────────────────────
put_object() { # <로컬 파일> <키> <단계 이름>
  local out
  if [ "$DRY_RUN" = 1 ]; then
    say "올리기를 건너뜁니다(--dry-run): s3://$BUCKET/$2"
    return
  fi
  out=$(aws s3api put-object --bucket "$BUCKET" --key "$2" --body "$1" --server-side-encryption AES256 \
    --expected-bucket-owner "$ACCOUNT" --query ETag --output text \
    --profile "$PROFILE" --region "$REGION" 2>&1) || fail "1단계" "$3 업로드" "$out" "1단계 버킷 확인 실패"
  say "올렸습니다: s3://$BUCKET/$2"
}
upload_package() {
  local zip
  mkdir -p "$OUT"
  if [ -n "$PACKAGE" ]; then
    [ -s "$PACKAGE" ] || fail "1단계" "배포 패키지" "파일이 없거나 비어 있습니다: $PACKAGE" "1단계 배포 패키지 실패"
    [ "$(head -c 2 "$PACKAGE")" = PK ] || fail "1단계" "배포 패키지" "zip 파일이 아닙니다: $PACKAGE" "1단계 배포 패키지 실패"
    zip=$PACKAGE
    say "받은 배포 패키지를 사용합니다: $zip"
  elif git -C "$ROOT" rev-parse --is-inside-work-tree > /dev/null 2>&1; then
    zip=$OUT/$PKG_KEY
    git -C "$ROOT" archive --format=zip -o "$zip" HEAD engine renderer buildspec.yml \
      || fail "1단계" "배포 패키지" "git archive 가 실패했습니다" "1단계 배포 패키지 실패"
    if [ -n "$(git -C "$ROOT" status --porcelain -- engine renderer infra buildspec.yml 2> /dev/null)" ]; then
      say "⚠ engine/·renderer/·infra/·buildspec.yml 에 커밋하지 않은 변경이 있습니다 — 패키지는 커밋된 HEAD 로 만듭니다"
    fi
    say "배포 패키지: $zip (git archive HEAD engine renderer buildspec.yml)"
  else
    fail "1단계" "배포 패키지" "git 저장소가 아닙니다. git clone 한 저장소 루트에서 실행하세요 (또는 --package <zip>)" "1단계 배포 패키지 실패"
  fi
  put_object "$zip" "$PKG_KEY" "배포 패키지"
}
STATE_KEY=""
upload_state_lambda() {
  step "1단계 · 상태 수집 Lambda 패키지 (--monitoring · 안내서 5단계)"
  local log
  log=$(OUT="$OUT" "$STATE_LAMBDA_BUILDER" 2>&1) || {
    printf '%s\n' "$log" | tail -5 | mask_stream >&2
    fail "5단계" "상태 Lambda 패키지 빌드" "$(printf '%s' "$log" | grep -v '^$' | tail -1)" "build-state-lambda.sh 가 \"pip 이 없다\"로 멈춤"
  }
  # 빌더가 출력하는 「S3 키」(내용 해시가 든 키 — 같은 키면 Lambda 코드가 갱신되지 않는다).
  STATE_KEY=$(printf '%s\n' "$log" | grep -Eo 'lambda/state-observer-[0-9a-f]{12}\.zip' | tail -1 || true)
  [ -n "$STATE_KEY" ] || fail "5단계" "상태 Lambda 패키지 빌드" "빌더 출력에서 S3 키를 찾지 못했습니다" "build-state-lambda.sh 가 \"pip 이 없다\"로 멈춤"
  [ -s "$OUT/${STATE_KEY#lambda/}" ] || fail "5단계" "상태 Lambda 패키지 빌드" "zip 이 없습니다: $OUT/${STATE_KEY#lambda/}" "build-state-lambda.sh 가 \"pip 이 없다\"로 멈춤"
  say "상태 Lambda 패키지: $STATE_KEY"
  put_object "$OUT/${STATE_KEY#lambda/}" "$STATE_KEY" "상태 Lambda 패키지"
}

# ── 2단계 · 스택 A ──────────────────────────────────────────────────────────────────────────────
deploy_stack_a() {
  step "2단계 · 스택 A (수집기)"
  check_stack_usable "$STACK_A" "2단계"
  local params=("SourceLocation=$BUCKET/$PKG_KEY")
  [ -z "$REGIONS" ] || params+=("TargetRegions=$REGIONS")
  [ -z "$ACCOUNTS" ] || params+=("TargetAccounts=$ACCOUNTS")
  [ -z "$STATE_KEY" ] || params+=("StateLambdaSourceKey=$STATE_KEY")
  # 선택 보강 — 준 때만 넘긴다. 생략하면 넘기지 않아 이전 값이 남는다(MemberExternalId · ViewerOrigin · ViewerDistributionId).
  [ -z "$EXTERNAL_ID" ] || params+=("MemberExternalId=$EXTERNAL_ID")
  cfn_deploy "2단계" "스택 A 배포" "2단계 스택 A 배포 실패" "$STACK_A" "$TEMPLATE_A" CAPABILITY_IAM "${params[@]}"
}

# ── 3단계 · 수집 1회 ─────────────────────────────────────────────────────────────────────────────
ARTIFACT_BUCKET=""
read_stack_a_outputs() {
  ARTIFACT_BUCKET=$(stack_output "$STACK_A" ArtifactBucketName)
  PROJECT=$(stack_output "$STACK_A" ProjectName)
  SIGNER=$(stack_output "$STACK_A" SignerFunctionName)
  [ -n "$ARTIFACT_BUCKET" ] && [ -n "$PROJECT" ] \
    || fail "3단계" "스택 A 출력 읽기" "ArtifactBucketName·ProjectName 출력이 없습니다" "2단계 스택 A 배포 실패"
}
s3_last_modified() {
  aws s3api head-object --bucket "$ARTIFACT_BUCKET" --key "$1" --query LastModified --output text \
    --profile "$PROFILE" --region "$REGION" 2> /dev/null || true
}
collect() {
  step "3단계 · 수집 1회"
  read_stack_a_outputs
  if [ "$DRY_RUN" = 1 ]; then
    say "빌드·다운로드를 건너뜁니다(--dry-run)"
    return
  fi
  local build_id status out waited=0
  build_id=$(aws codebuild start-build --project-name "$PROJECT" --query build.id --output text \
    --profile "$PROFILE" --region "$REGION" 2>&1) || fail "3단계" "수집 시작" "$build_id" "3단계 수집 빌드 실패"
  say "빌드 $build_id — 보통 2~5분"
  while :; do
    status=$(aws codebuild batch-get-builds --ids "$build_id" --query 'builds[0].buildStatus' --output text \
      --profile "$PROFILE" --region "$REGION" 2>&1) || fail "3단계" "빌드 상태 확인" "$status" "3단계 수집 빌드 실패"
    [ "$status" = IN_PROGRESS ] || break
    if [ "$waited" -ge 2700 ]; then # 프로젝트 제한 30분 + 대기열 여유
      fail "3단계" "수집 빌드" "45분이 지나도 끝나지 않았습니다($build_id)" "3단계 수집 빌드 실패"
    fi
    sleep "$POLL_SECONDS"
    waited=$((waited + POLL_SECONDS))
    printf '.' >&2
  done
  echo >&2
  if [ "$status" != SUCCEEDED ]; then
    # shellcheck disable=SC2016  # 역따옴표는 JMESPath 리터럴이다
    out=$(aws codebuild batch-get-builds --ids "$build_id" \
      --query 'builds[0].phases[?phaseStatus!=null && phaseStatus!=`SUCCEEDED`] | [0].[phaseType, contexts[0].message]' \
      --output text --profile "$PROFILE" --region "$REGION" 2>&1) || out=""
    say "로그: aws logs tail /aws/codebuild/$STACK_A-collector --since 1h --profile $PROFILE --region $REGION" >&2
    fail "3단계" "수집 빌드($status)" "$out" "3단계 수집 빌드 실패"
  fi
  if [ -n "$SIGNER" ]; then
    # 모니터링을 켠 설치는 빌드가 unsigned/index.html 을 쓰고, 빌드 성공 직후 서명 함수가 index.html 로 게시한다.
    # 빌드 산출물보다 먼저 쓰인 index.html(직전 판)을 받지 않도록 게시를 기다린다.
    local unsigned page tries=0
    unsigned=$(s3_last_modified unsigned/index.html)
    while :; do
      page=$(s3_last_modified index.html)
      if [ -n "$unsigned" ] && [ -n "$page" ] && [ "$page" != None ] && ! [[ $page < $unsigned ]]; then break; fi
      tries=$((tries + 1))
      [ "$tries" -le 20 ] || fail "3단계" "페이지 게시 대기" "서명 함수가 index.html 을 아직 게시하지 않았습니다(로그: /aws/lambda/$STACK_A-signer). 잠시 뒤 --download 로 다시 받아 주세요" "3단계 페이지 게시 대기 시간 초과"
      sleep "$POLL_SECONDS"
    done
  fi
  mkdir -p "$OUT"
  out=$(aws s3 cp "s3://$ARTIFACT_BUCKET/index.html" "$OUT/index.html" --only-show-errors \
    --profile "$PROFILE" --region "$REGION" 2>&1) || fail "3단계" "다이어그램 내려받기" "$out" "3단계 수집 빌드 실패"
  say "다이어그램을 받았습니다: $OUT/index.html"
}

# 최신 다이어그램만 받는다 — 수집·배포 없이 S3 의 index.html → out/index.html(사용자 요청 2026-10-26).
#   설치와 --collect 는 끝에 이미 받아 온다. 이건 그 뒤 한 시간마다 자동 갱신된 그림을 다시 받을 때 쓴다.
download() {
  step "최신 다이어그램 받기"
  check_stack_usable "$STACK_A" "다운로드"
  [ "$(stack_status "$STACK_A")" != NONE ] \
    || fail "다운로드" "최신 다이어그램 받기" "스택 A($STACK_A)가 없습니다. 먼저 ./deploy.sh 로 설치해 주세요" "2단계 스택 A 배포 실패"
  ARTIFACT_BUCKET=$(stack_output "$STACK_A" ArtifactBucketName)
  [ -n "$ARTIFACT_BUCKET" ] \
    || fail "다운로드" "최신 다이어그램 받기" "스택 A 에 ArtifactBucketName 출력이 없습니다" "2단계 스택 A 배포 실패"
  local when out
  when=$(s3_last_modified index.html)
  if [ "$DRY_RUN" = 1 ]; then
    say "받기를 건너뜁니다(--dry-run): s3://$ARTIFACT_BUCKET/index.html${when:+ (S3 갱신 시각 $when)}"
    return
  fi
  mkdir -p "$OUT"
  out=$(aws s3 cp "s3://$ARTIFACT_BUCKET/index.html" "$OUT/index.html" --only-show-errors \
    --profile "$PROFILE" --region "$REGION" 2>&1) || fail "다운로드" "최신 다이어그램 받기" "$out" "3단계 수집 빌드 실패"
  say "최신 다이어그램을 받았습니다${when:+ (S3 갱신 시각 $when)} — 수집은 한 시간마다 자동으로 돕니다"
}

# 마지막 안내 — 다이어그램을 어디서 여는지. CloudShell 안의 파일은 그 자리에서 브라우저로 열 수 없다.
diagram_hint() {
  [ "$DRY_RUN" = 0 ] && [ -f "$OUT/index.html" ] || return 0
  if [ "$IN_CLOUDSHELL" = 1 ]; then
    say "다이어그램: CloudShell 메뉴 「작업 → 파일 다운로드」에 $OUT/index.html 을 넣어 PC 로 받은 뒤 브라우저로 여세요."
    [ -z "$ARTIFACT_BUCKET" ] || say "  또는 S3 콘솔에서 $ARTIFACT_BUCKET 버킷의 index.html 을 바로 다운로드할 수 있습니다."
  else
    say "다이어그램: $OUT/index.html (브라우저로 여세요)"
  fi
}

# ── 6단계 · 스택 C 와 보는 링크 ─────────────────────────────────────────────────────────────────
viewer() {
  step "6단계 · CloudFront 공유 (스택 C)"
  check_stack_usable "$STACK_C" "6단계"
  local c_status pub current slot=k1 prev_id
  c_status=$(stack_status "$STACK_C")
  case "$c_status" in
    NONE | REVIEW_IN_PROGRESS) ;;
    *)
      slot=$(stack_parameter "$STACK_C" KeyLabel)
      slot=${slot:-k1}
      ;;
  esac
  # 자리마다 파일이 하나다 — k2 자리로 교체한 스택은 out/viewer-k2.pem 으로 서명한다.
  VIEWER_KEY=${VIEWER_KEY:-$OUT/viewer-$slot.pem}
  if [ ! -f "$VIEWER_KEY" ]; then
    case "$c_status" in
      NONE | REVIEW_IN_PROGRESS) ;;
      *) fail "6단계" "서명 키 확인" "비밀키가 없습니다($VIEWER_KEY). 스택 C 가 이미 있어 새 키를 만들면 기존 키와 어긋납니다. 원래 비밀키를 --viewer-key 로 주거나, 잃었으면 키를 교체해 주세요(--viewer --rotate-key --no-overlap)" "6단계 서명 키 확인 실패" ;;
    esac
    if [ "$DRY_RUN" = 1 ]; then
      say "서명 키가 없습니다 — 실제 실행이면 $VIEWER_KEY 에 만듭니다. --dry-run 이라 스택 C 변경 세트도 건너뜁니다"
      return
    fi
    mkdir -p "$(dirname "$VIEWER_KEY")"
    (umask 077 && openssl genrsa -out "$VIEWER_KEY" 2048 2> /dev/null) \
      || fail "6단계" "서명 키 만들기" "openssl genrsa 실패" "6단계 서명 키 확인 실패"
    say "서명 비밀키를 만들었습니다: $VIEWER_KEY (이 PC 에만 두고 AWS 에 올리지 않습니다)"
  fi
  pub=$(openssl rsa -in "$VIEWER_KEY" -pubout 2> /dev/null) \
    || fail "6단계" "서명 키 읽기" "RSA 비밀키가 아닙니다: $VIEWER_KEY" "6단계 서명 키 확인 실패"
  case "$c_status" in
    NONE | REVIEW_IN_PROGRESS) ;;
    *)
      current=$(stack_parameter "$STACK_C" ViewerPublicKeyPem)
      # 공개키만 바꾸면 갱신이 실패하고 롤백된다(KeyLabel 을 함께 바꿔야 한다) — 그 전에 멈춘다.
      [ "$(printf '%s' "$current" | flat)" = "$(printf '%s' "$pub" | flat)" ] \
        || fail "6단계" "서명 키 확인" "$VIEWER_KEY 가 스택 C 의 공개키(자리 $slot)와 짝이 아닙니다. 키 교체는 ./deploy.sh --viewer --rotate-key 로 합니다" "6단계 서명 키 확인 실패"
      ;;
  esac
  # PreviousPublicKeyPem·KeyLabel 은 넘기지 않는다(이전 값 유지) — 겹침 교체 중에 다시 실행해도 옛 키가 남는다.
  local params=("ArtifactBucketName=$ARTIFACT_BUCKET" "ViewerPublicKeyPem=$pub")
  # 적지 않으면 넘기지 않는다 — 콘솔에서 적어 둔 읽는 사람 목록이 다시 실행할 때 지워지지 않게(이전 값 유지).
  [ -z "$LOG_READERS" ] || params+=("LogReaderPrincipalArns=$LOG_READERS")
  cfn_deploy "6단계" "스택 C 배포" "6단계 스택 C 배포 실패" "$STACK_C" "$TEMPLATE_C" - "${params[@]}"
  [ "$NARROW_VIEWER" = 0 ] || narrow_viewer
  if [ "$DRY_RUN" = 1 ]; then
    say "링크 발급을 건너뜁니다(--dry-run)"
    return
  fi
  issue_link "$VIEWER_KEY"
  prev_id=$(stack_output "$STACK_C" PreviousKeyPairId)
  [ -z "$prev_id" ] || say "겹침 교체 중입니다 — 옛 키($prev_id)는 옛 링크가 만료된 뒤 ./deploy.sh --profile $PROFILE --region $REGION --viewer --retire-old-key 로 빼 주세요"
}

# --narrow-viewer — 스택 A 를 만들 때는 스택 C 의 주소·배포 ID 를 모른다. 그래서 스택 A 의 기본은 버킷 CORS 가 아무
# CloudFront 도메인을, 버킷 정책이 이 계정의 아무 배포의 index.html 읽기를 받는다(안내서 「선택 보강」). 스택 C 를 올린 뒤
# 그 출력으로 스택 A 를 한 번 더 갱신해 둘 다 이 배포 하나로 좁힌다. 다른 파라미터는 넘기지 않는다(이전 값 유지).
narrow_viewer() {
  step "6단계 · 스택 A 를 이 CloudFront 배포로 좁히기 (--narrow-viewer)"
  local domain dist
  domain=$(stack_output "$STACK_C" ViewerDomain)
  dist=$(stack_output "$STACK_C" DistributionId)
  if [ -z "$domain" ] || [ -z "$dist" ]; then
    if [ "$DRY_RUN" = 1 ]; then
      say "스택 C 가 아직 없어 건너뜁니다(--dry-run) — 실제 실행이면 스택 C 배포 뒤 그 출력으로 스택 A 를 좁힙니다"
      return
    fi
    fail "6단계" "스택 C 출력 읽기" "스택 C 에 ViewerDomain·DistributionId 출력이 없습니다" "6단계 스택 C 배포 실패"
  fi
  say "버킷 CORS 를 https://$domain 하나로, CloudFront 의 index.html 읽기를 배포 $dist 하나로 좁힙니다"
  cfn_deploy "6단계" "스택 A 좁히기" "2단계 스택 A 배포 실패" "$STACK_A" "$TEMPLATE_A" CAPABILITY_IAM \
    "ViewerOrigin=https://$domain" "ViewerDistributionId=$dist"
}

issue_link() { # <비밀키>
  local link
  link=$("$LINK_ISSUER" --stack "$STACK_C" --key "$1" --days "$DAYS" \
    --profile "$PROFILE" --region "$REGION") || fail "6단계" "링크 발급" "issue-viewer-link.sh 실패" "6단계 링크 발급 실패"
  echo
  # 🔴 `${DAYS}` 중괄호 필수 — macOS /bin/bash 3.2 는 UTF-8 로캘에서 「일」의 첫 바이트를 변수 이름으로 읽어 `set -u` 에 걸린다(실측 2026-10-22)
  echo "보는 링크 (${DAYS}일 · 가진 사람은 누구나 볼 수 있습니다 — 비밀번호처럼 다뤄 주세요):"
  # 링크는 mask 를 거치지 않는다 — 서명 안의 숫자열이 바뀌면 링크가 403 이 된다. 링크에는 계정 ID 가 없다.
  printf '%s\n' "$link"
  say "다시 발급: infra/issue-viewer-link.sh --stack $STACK_C --key $1 --days <일수> --profile $PROFILE --region $REGION"
}

# ── 6단계 · 서명 키 교체 (#141) ─────────────────────────────────────────────────────────────────
# 스택 C 에는 키 자리가 둘(k1·k2)이다. CloudFront 는 올린 공개키 값을 못 바꾸므로 교체 = 빈 자리에 새 키를 넣기다.
#   겹침:     KeyLabel=빈 자리 · ViewerPublicKeyPem=새 키 · PreviousPublicKeyPem=지금 키 → 키 그룹에 둘 다 · 새 링크 = 새 키
#   옛 키 빼기: PreviousPublicKeyPem="" → 옛 자리 삭제 · 옛 링크 403
#   겹침 없이: KeyLabel=빈 자리 · ViewerPublicKeyPem=새 키 · PreviousPublicKeyPem="" → 옛 키가 같은 갱신에서 빠진다(유출 대응)
# 🔴 PreviousPublicKeyPem 은 스택에 저장된 ViewerPublicKeyPem 과 **바이트까지 같아야** 한다. 한 글자라도 다르면
#    CloudFormation 이 옛 자리를 제자리 수정으로 계획하고 CloudFront 가 거절한다(롤백 — 옛 링크는 그대로 산다).
# 발급 기록 — issue-viewer-link.sh 가 링크마다 「만료 · Key-Pair-Id · 발급 시각」을 남기는 곳(같은 경로를 쓴다).
LINK_LEDGER="${XDG_STATE_HOME:-$HOME/.local/state}/nettopo/viewer-links.log"
KEY_TMP=""
trap '[ -z "$KEY_TMP" ] || rm -f "$KEY_TMP"' EXIT
flat() { tr -d ' \t\r\n'; }
other_slot() { if [ "$1" = k2 ]; then echo k1; else echo k2; fi; }
utc() { date -u -d "@$1" '+%Y-%m-%d %H:%M UTC' 2> /dev/null || date -u -r "$1" '+%Y-%m-%d %H:%M UTC'; }

# 파라미터 값을 바이트 그대로 EXACT 에 담는다. text 출력은 $( ) 가 끝 줄바꿈을 지워 저장값과 달라질 수 있어 JSON 으로 받는다.
EXACT=""
stack_parameter_exact() { # <스택> <키>
  local out
  out=$(aws cloudformation describe-stacks --stack-name "$1" \
    --query "Stacks[0].Parameters[?ParameterKey=='$2'].ParameterValue | [0]" --output json \
    --profile "$PROFILE" --region "$REGION" 2>&1) \
    || fail "스택 조회" "$1 의 $2" "$out" "deploy.sh 가 0단계 자격 증명 확인에서 멈춤"
  if [ "$out" = null ]; then
    EXACT=""
    return
  fi
  out=${out#\"}
  out=${out%\"}
  # PEM 은 base64·공백·줄바꿈뿐이다 — JSON 이스케이프는 \n \r \t 만 나와야 한다(다른 것이면 추측하지 않고 멈춘다).
  [[ $out != *\\[!nrt]* ]] || fail "스택 조회" "$1 의 $2" "값에 예상 밖 이스케이프가 있습니다" "deploy.sh 가 0단계 자격 증명 확인에서 멈춤"
  # 🔴 빈 값을 printf -v 로 넘기지 않는다 — macOS /bin/bash 3.2 는 `printf -v X '%b' ""` 에서 X 를 비우지 않고
  #    이전 값을 남긴다(실측 2026-10-23: 처음 설치한 스택의 --rotate-key 가 「이미 겹침 중」으로 거절됐다)
  if [ -n "$out" ]; then printf -v EXACT '%b' "$out"; else EXACT=""; fi
}

KEY_SLOT="" KEY_PEM="" KEY_PREV="" KEY_ID="" KEY_PREV_ID=""
read_key_state() {
  check_stack_usable "$STACK_C" "6단계"
  case "$(stack_status "$STACK_C")" in
    NONE | REVIEW_IN_PROGRESS)
      fail "6단계" "서명 키 교체" "스택 C($STACK_C)가 없습니다 — 먼저 ./deploy.sh --viewer --days <일수> 로 설치해 주세요" "6단계 스택 C 배포 실패"
      ;;
  esac
  KEY_SLOT=$(stack_parameter "$STACK_C" KeyLabel)
  KEY_SLOT=${KEY_SLOT:-k1}
  stack_parameter_exact "$STACK_C" ViewerPublicKeyPem
  KEY_PEM=$EXACT
  stack_parameter_exact "$STACK_C" PreviousPublicKeyPem # 이 기능 전의 스택에는 없다 = 빈 값
  KEY_PREV=$EXACT
  KEY_ID=$(stack_output "$STACK_C" ViewerKeyPairId)
  KEY_PREV_ID=$(stack_output "$STACK_C" PreviousKeyPairId)
  [ -n "$KEY_PEM" ] && [ -n "$KEY_ID" ] \
    || fail "6단계" "서명 키 교체" "스택 C 의 ViewerPublicKeyPem·ViewerKeyPairId 를 읽지 못했습니다" "6단계 스택 C 배포 실패"
  say "지금 키: 자리 $KEY_SLOT · $KEY_ID$([ -z "$KEY_PREV" ] || echo " · 겹침 중(옛 키 $KEY_PREV_ID)")"
}

# <Key-Pair-Id> → 「가장 늦은 만료(epoch) 건수」. 이 PC(이 사용자)에서 낸 링크만 셈한다.
ledger_latest() {
  { [ ! -f "$LINK_LEDGER" ] || cat "$LINK_LEDGER"; } \
    | awk -F'\t' -v id="$1" '$2 == id { n++; if ($1 + 0 > m) m = $1 + 0 } END { printf "%d %d\n", m, n }'
}

# 링크를 끊는 변경(겹침 없는 교체 · 옛 키 빼기)은 스택 이름을 그대로 입력해야 한다. 건너뛰는 옵션이 없다(--destroy 와 같다).
confirm_key_change() { # <무엇이 끊기나>
  local answer=""
  echo >&2
  say "🔴 $1" >&2
  printf '계속하려면 「%s」 를 그대로 입력해 주세요: ' "$STACK_C" >&2
  read -r answer || true
  [ "$answer" = "$STACK_C" ] || { echo "입력이 달라 아무것도 바꾸지 않았습니다." >&2; exit 1; }
}

rotate_key() {
  if [ "$NO_OVERLAP" = 1 ]; then
    step "6단계 · 서명 키 교체 — 겹침 없이(지금까지 낸 링크 전부 403)"
  else
    step "6단계 · 서명 키 교체 — 겹침(옛 링크는 만료까지 열립니다)"
  fi
  read_key_state
  if [ -n "$KEY_PREV" ] && [ "$NO_OVERLAP" = 0 ]; then
    fail "6단계" "서명 키 교체" "이미 겹침 교체 중입니다(두 자리가 다 찼습니다). 옛 링크가 만료된 뒤 --viewer --retire-old-key 로 옛 키를 먼저 빼 주세요. 유출이면 --viewer --rotate-key --no-overlap" "6단계 서명 키 확인 실패"
  fi
  local new_slot key pub mine
  new_slot=$(other_slot "$KEY_SLOT")
  key=${VIEWER_KEY:-$OUT/viewer-$new_slot.pem}
  if [ -n "$VIEWER_KEY" ] && [ -f "$VIEWER_KEY" ]; then
    # 받은 새 키 — 지금 키나 물러나던 키면 교체가 아니다.
    pub=$(openssl rsa -in "$VIEWER_KEY" -pubout 2> /dev/null) \
      || fail "6단계" "서명 키 읽기" "RSA 비밀키가 아닙니다: $VIEWER_KEY" "6단계 서명 키 확인 실패"
    mine=$(printf '%s' "$pub" | flat)
    if [ "$mine" = "$(printf '%s' "$KEY_PEM" | flat)" ] || { [ -n "$KEY_PREV" ] && [ "$mine" = "$(printf '%s' "$KEY_PREV" | flat)" ]; }; then
      fail "6단계" "서명 키 교체" "$VIEWER_KEY 는 스택 C 가 이미 쓰는 키입니다 — 새 키 파일을 주거나, --viewer-key 를 빼면 새로 만듭니다" "6단계 서명 키 확인 실패"
    fi
    say "받은 새 키를 넣습니다: $VIEWER_KEY → 자리 $new_slot"
  else
    if [ -f "$key" ]; then
      # 기본 경로의 남은 파일 = 키 그룹에 없는 옛 키(지난 교체의 잔재) 또는 지금 물러나는 키. 지금 키면 멈춘다.
      mine=$(openssl rsa -in "$key" -pubout 2> /dev/null | flat || true)
      [ "$mine" != "$(printf '%s' "$KEY_PEM" | flat)" ] \
        || fail "6단계" "서명 키 교체" "$key 가 지금 서명 키입니다 — 덮어쓰면 지금 링크를 더 낼 수 없습니다. --viewer-key 로 새 키 경로를 주세요" "6단계 서명 키 확인 실패"
    fi
    mkdir -p "$(dirname "$key")"
    # 새 키는 임시 파일에 만들고 스택 갱신이 끝난 뒤에 제자리로 옮긴다 — 갱신이 실패하면 원래 파일이 그대로 남는다.
    KEY_TMP=$(mktemp "$(dirname "$key")/.viewer-$new_slot.XXXXXX")
    (umask 077 && openssl genrsa -out "$KEY_TMP" 2048 2> /dev/null) \
      || fail "6단계" "서명 키 만들기" "openssl genrsa 실패" "6단계 서명 키 확인 실패"
    chmod 600 "$KEY_TMP"
    pub=$(openssl rsa -in "$KEY_TMP" -pubout 2> /dev/null) \
      || fail "6단계" "서명 키 만들기" "만든 키를 읽지 못했습니다" "6단계 서명 키 확인 실패"
    if [ "$DRY_RUN" = 1 ]; then
      say "새 키 — 변경 세트용 임시 키를 만들었습니다(끝나면 지웁니다 · 실제 실행이면 $key 에 만듭니다)"
    else
      say "새 키를 만들었습니다 → 자리 $new_slot (스택 갱신이 끝나면 $key 에 둡니다 · 이 PC 에만 두고 AWS 에 올리지 않습니다)"
    fi
  fi
  local latest count
  read -r latest count <<< "$(ledger_latest "$KEY_ID")"
  if [ "$NO_OVERLAP" = 1 ] && [ "$DRY_RUN" = 0 ]; then
    confirm_key_change "지금 키($KEY_ID)$([ -z "$KEY_PREV" ] || echo "와 물러나던 키($KEY_PREV_ID)")로 서명한 링크가 만료 전이라도 전부 403 이 됩니다(이미 나눠 준 링크 전부 · 다른 PC 에서 낸 링크 포함)."
  fi
  local leak_params=("PreviousPublicKeyPem=")
  [ -z "$LOG_READERS" ] || leak_params+=("LogReaderPrincipalArns=$LOG_READERS")
  if [ -n "$KEY_PREV" ]; then
    # 겹침 중의 유출 대응 — 빈 자리가 없다. 물러나던 키를 먼저 빼고(갱신 1) 새 키로 옮기며 지금 키를 뺀다(갱신 2).
    say "1/2 · 물러나던 옛 키($KEY_PREV_ID)를 먼저 뺍니다 — 두 자리가 다 차 있어 새 키를 넣을 자리가 없습니다"
    cfn_deploy "6단계" "옛 키 빼기" "6단계 스택 C 배포 실패" "$STACK_C" "$TEMPLATE_C" - "${leak_params[@]}"
    if [ "$DRY_RUN" = 1 ]; then
      say "2/2 는 1/2 가 실행된 뒤에만 변경 세트를 만들 수 있습니다 — 실제 실행이면 새 키를 자리 $new_slot 에 넣고 지금 키($KEY_ID)를 뺍니다"
      return
    fi
    say "2/2 · 새 키를 자리 $new_slot 에 넣고 지금 키($KEY_ID)를 뺍니다"
  fi
  local params=("KeyLabel=$new_slot" "ViewerPublicKeyPem=$pub")
  if [ "$NO_OVERLAP" = 1 ]; then params+=("PreviousPublicKeyPem="); else params+=("PreviousPublicKeyPem=$KEY_PEM"); fi
  [ -z "$LOG_READERS" ] || params+=("LogReaderPrincipalArns=$LOG_READERS")
  cfn_deploy "6단계" "서명 키 교체" "6단계 스택 C 배포 실패" "$STACK_C" "$TEMPLATE_C" - "${params[@]}"
  if [ "$DRY_RUN" = 1 ]; then
    say "키 파일·링크를 건너뜁니다(--dry-run)"
    return
  fi
  # 갱신이 끝났다 = 스택에 새 공개키가 있다 → 아래 검사보다 먼저 비밀키를 제자리에 둔다(검사가 멈춰도 키는 남는다).
  if [ -n "$KEY_TMP" ]; then
    mv -f "$KEY_TMP" "$key" || { KEY_TMP=""; fail "6단계" "서명 키 저장" "새 비밀키를 $key 로 옮기지 못했습니다(임시 파일은 남겨 두었습니다)" "6단계 서명 키 확인 실패"; }
    KEY_TMP=""
  fi
  # 갱신이 말한 대로 됐는지 출력으로 잰다 — 겹침이면 옛 키 ID 가 그대로 남아 있어야 옛 링크가 산다.
  local new_id prev_id
  new_id=$(stack_output "$STACK_C" ViewerKeyPairId)
  prev_id=$(stack_output "$STACK_C" PreviousKeyPairId)
  [ -n "$new_id" ] && [ "$new_id" != "$KEY_ID" ] \
    || fail "6단계" "서명 키 교체 확인" "ViewerKeyPairId 가 바뀌지 않았습니다(${new_id:-없음})" "6단계 스택 C 배포 실패"
  if [ "$NO_OVERLAP" = 1 ]; then
    [ -z "$prev_id" ] || fail "6단계" "서명 키 교체 확인" "옛 키($prev_id)가 아직 키 그룹에 있습니다" "6단계 스택 C 배포 실패"
  else
    [ "$prev_id" = "$KEY_ID" ] \
      || fail "6단계" "서명 키 교체 확인" "옛 키가 키 그룹에 남지 않았습니다(PreviousKeyPairId=${prev_id:-없음} · 교체 전 $KEY_ID) — 옛 링크가 끊겼을 수 있습니다. 새 키 $key 로 링크를 다시 나눠 주세요" "6단계 스택 C 배포 실패"
  fi
  say "새 키: 자리 $new_slot · $new_id · $key"
  issue_link "$key"
  echo
  if [ "$NO_OVERLAP" = 1 ]; then
    say "옛 링크는 전부 403 입니다. 링크가 필요한 사람에게 위 새 링크를 다시 나눠 주세요"
    say "옛 비밀키 $OUT/viewer-$KEY_SLOT.pem 은 이제 어떤 링크도 만들지 못합니다(지워도 됩니다)"
    return
  fi
  if [ "$count" -gt 0 ]; then
    say "옛 키($KEY_ID)로 이 PC 에서 낸 링크 ${count}개 · 가장 늦은 만료 $(utc "$latest")"
    say "그 시각이 지나면 옛 키를 빼 주세요: ./deploy.sh --profile $PROFILE --region $REGION --viewer --retire-old-key"
  else
    say "이 PC 에는 옛 키($KEY_ID)로 낸 링크의 기록($LINK_LEDGER)이 없습니다 — 옛 링크에 준 가장 긴 수명이 지난 뒤 빼 주세요:"
    say "  ./deploy.sh --profile $PROFILE --region $REGION --viewer --retire-old-key"
  fi
  say "다른 PC·CloudShell 에서 옛 키로 낸 링크는 이 셈에 없습니다. 그 전까지 옛 링크는 계속 열립니다"
}

retire_old_key() {
  step "6단계 · 옛 서명 키 빼기 (겹침 끝)"
  read_key_state
  if [ -z "$KEY_PREV" ]; then
    say "겹침 교체 중이 아닙니다 — 뺄 옛 키가 없습니다(키 그룹 = $KEY_ID 하나). 바꾼 것이 없습니다"
    return
  fi
  local latest count
  read -r latest count <<< "$(ledger_latest "$KEY_PREV_ID")"
  if [ "$count" -gt 0 ] && [ "$latest" -gt "$(date +%s)" ]; then
    say "🔴 옛 키($KEY_PREV_ID)로 낸 링크 중 아직 만료되지 않은 것이 있습니다 — 가장 늦은 만료 $(utc "$latest")(이 PC 기록 ${count}개)"
  elif [ "$count" -gt 0 ]; then
    say "옛 키($KEY_PREV_ID)로 이 PC 에서 낸 링크 ${count}개는 모두 만료됐습니다(가장 늦은 만료 $(utc "$latest"))"
  else
    say "이 PC 에는 옛 키($KEY_PREV_ID)로 낸 링크의 기록이 없습니다 — 옛 링크의 수명이 지났는지 직접 확인해 주세요"
  fi
  [ "$DRY_RUN" = 1 ] \
    || confirm_key_change "옛 키($KEY_PREV_ID)로 서명한 링크는 만료 전이라도 전부 403 이 됩니다. 새 키($KEY_ID) 링크는 그대로입니다."
  local params=("PreviousPublicKeyPem=")
  [ -z "$LOG_READERS" ] || params+=("LogReaderPrincipalArns=$LOG_READERS")
  cfn_deploy "6단계" "옛 키 빼기" "6단계 스택 C 배포 실패" "$STACK_C" "$TEMPLATE_C" - "${params[@]}"
  if [ "$DRY_RUN" = 1 ]; then
    return
  fi
  local new_id prev_id
  new_id=$(stack_output "$STACK_C" ViewerKeyPairId)
  prev_id=$(stack_output "$STACK_C" PreviousKeyPairId)
  [ -z "$prev_id" ] && [ "$new_id" = "$KEY_ID" ] \
    || fail "6단계" "옛 키 빼기 확인" "갱신 뒤 키가 예상과 다릅니다(ViewerKeyPairId=${new_id:-없음} · PreviousKeyPairId=${prev_id:-없음})" "6단계 스택 C 배포 실패"
  say "옛 키를 뺐습니다 — 키 그룹 = $KEY_ID 하나. 옛 비밀키 $OUT/viewer-$(other_slot "$KEY_SLOT").pem 은 이제 어떤 링크도 만들지 못합니다(지워도 됩니다)"
}

# ── 소스 계정용 템플릿과 명령 ───────────────────────────────────────────────────────────────────
# 소스 계정·조직 관리자는 저장소를 받지 않았다(#82 댓글) → 스택 B·C 템플릿을 out/templates/ 에 복사해 넘긴다.
# 서명 URL(자격 증명 세션과 함께 만료)·버킷 공유(패키지 버킷 정책을 다른 계정에 연다)는 쓰지 않는다.
sha256_of() {
  if command -v sha256sum > /dev/null; then sha256sum "$@"; else shasum -a 256 "$@"; fi
}
handoff() {
  local dir=$OUT/templates
  mkdir -p "$dir"
  cp "$TEMPLATE_B" "$TEMPLATE_C" "$dir/"
  (cd "$dir" && sha256_of stack-b-member-role.yaml stack-c-viewer.yaml > SHA256SUMS)
  local regions=${REGIONS:-} external=$EXTERNAL_ID status
  if [ -z "$regions" ] || [ -z "$external" ]; then
    status=$(stack_status "$STACK_A")
    case "$status" in
      NONE | REVIEW_IN_PROGRESS) ;;
      *)
        [ -n "$regions" ] || regions=$(stack_parameter "$STACK_A" TargetRegions)
        # 이전 실행에서 넣은 외부 ID 는 스택 A 에 남아 있다 — 생략한 실행의 스택 B 명령에서 빠지면 새로 붙는 소스 계정의
        # 역할만 외부 ID 조건 없이 선다.
        [ -n "$external" ] || external=$(stack_parameter "$STACK_A" MemberExternalId)
        ;;
    esac
  fi
  regions=${regions:-<수집 리전 목록>}
  # 계정 ID 는 화면에도 끝 4자리만 — 명령에는 자리표시만 두고, 전체 값은 받는 사람에게 따로 알린다.
  local hub="<모니터링 계정 ID …${ACCOUNT:8}>"
  # mask 를 거친다 — 프로필을 생략한 CloudShell 실행이면 안내 명령의 빈 `--profile ` 을 지운다.
  handoff_text "$hub" "$regions" "$external" "$ORG_ID" | mask_stream
}
handoff_text() { # <hub 자리표시> <리전 목록> <외부 ID|""> <조직 ID|"">
  local hub=$1 regions=$2 over="" json="" console=""
  # 선택 보강 — 값이 있을 때만 두 명령(CLI 한 줄 · StackSet)에 같은 파라미터를 붙인다. 없으면 출력은 이 기능 전과 바이트까지 같다.
  if [ -n "$3" ]; then
    over+=" ExternalId=$3"
    json+=",{\"ParameterKey\":\"ExternalId\",\"ParameterValue\":\"$3\"}"
    console+=" + ExternalId"
  fi
  if [ -n "$4" ]; then
    over+=" PrincipalOrgId=$4"
    json+=",{\"ParameterKey\":\"PrincipalOrgId\",\"ParameterValue\":\"$4\"}"
    console+=" + PrincipalOrgId"
  fi
  cat << EOF

소스 계정·조직 관리자에게 넘길 템플릿: $OUT/templates/ (스택 B·C + SHA256SUMS · 계정 정보 없음 · 저장소 불필요)
모니터링 계정 ID 는 화면에 끝 4자리(…${ACCOUNT:8})만 보여 드립니다. 전체 값은 아래 명령으로 확인해 받는 사람에게 알려 주세요:
  aws sts get-caller-identity --query Account --output text --profile $PROFILE --region $REGION

소스 계정을 추가하려면 그 계정에서(템플릿 폴더 안에서) 실행합니다 — 안내서 4-B:
  aws cloudformation deploy --template-file stack-b-member-role.yaml \\
    --stack-name $STACK_B --capabilities CAPABILITY_NAMED_IAM \\
    --parameter-overrides HubAccountId=$hub AllowedRegions=$regions$over \\
    --profile <소스 계정 프로필> --region $REGION
  (콘솔: CloudFormation → 스택 생성 → 템플릿 파일 업로드 → 위 두 값$console · 「IAM 리소스 … 승인」 체크)

조직 전체라면 조직 관리자가 StackSet 으로(안내서 4-C · activate-organizations-access 를 먼저 한 번):
  # StackSets 위임 관리자 계정에서 실행하면 두 명령 끝에 --call-as DELEGATED_ADMIN 을 붙입니다(관리 계정이면 그대로)
  aws cloudformation create-stack-set --stack-set-name $STACK_B \\
    --template-body file://stack-b-member-role.yaml --permission-model SERVICE_MANAGED \\
    --auto-deployment Enabled=true,RetainStacksOnAccountRemoval=false --capabilities CAPABILITY_NAMED_IAM \\
    --parameters '[{"ParameterKey":"HubAccountId","ParameterValue":"$hub"},{"ParameterKey":"AllowedRegions","ParameterValue":"$regions"}$json]' \\
    --profile <관리 계정 프로필> --region $REGION
  aws cloudformation create-stack-instances --stack-set-name $STACK_B \\
    --deployment-targets OrganizationalUnitIds=<OU ID> --regions $REGION \\
    --profile <관리 계정 프로필> --region $REGION

소스 계정이 모니터링 계정의 Transit Gateway 에 연결돼 있지 않거나 수집 리전에 Transit Gateway 가 없으면 자동으로 찾지 못합니다 — --accounts 에 그 계정을 넣어 다시 실행해 주세요.
EOF
}

# ── 삭제 ────────────────────────────────────────────────────────────────────────────────────────
# 버전까지 전부 지운다(1,000개씩 반복). 버전 관리가 없는 버킷도 같은 길로 지워진다.
empty_and_delete_bucket() {
  local bucket=$1 batch out
  while :; do
    # shellcheck disable=SC2016  # 역따옴표는 JMESPath 리터럴이다
    batch=$(aws s3api list-object-versions --bucket "$bucket" --max-items 1000 \
      --query '{Objects: [(Versions || `[]`)[].{Key: Key, VersionId: VersionId}, (DeleteMarkers || `[]`)[].{Key: Key, VersionId: VersionId}][], Quiet: `true`}' \
      --output json --profile "$PROFILE" --region "$REGION" 2>&1) || {
      printf '%s' "$batch" | grep -q NoSuchBucket && return 0
      fail "삭제" "버킷 $bucket 비우기" "$batch" "삭제 실패"
    }
    printf '%s' "$batch" | grep -q '"Key"' || break
    out=$(aws s3api delete-objects --bucket "$bucket" --delete "$batch" \
      --profile "$PROFILE" --region "$REGION" 2>&1) || fail "삭제" "버킷 $bucket 비우기" "$out" "삭제 실패"
    # Quiet 모드는 실패한 객체만 돌려준다 — 종료 코드는 0 이라 따로 본다(안 보면 같은 목록을 끝없이 돈다).
    if printf '%s' "$out" | grep -q '"Errors"'; then fail "삭제" "버킷 $bucket 비우기" "$out" "삭제 실패"; fi
  done
  out=$(aws s3api delete-bucket --bucket "$bucket" --profile "$PROFILE" --region "$REGION" 2>&1) \
    || fail "삭제" "버킷 $bucket 지우기" "$out" "삭제 실패"
  say "지웠습니다: 버킷 $bucket"
}
delete_stack() {
  local stack=$1 out
  out=$(aws cloudformation delete-stack --stack-name "$stack" --profile "$PROFILE" --region "$REGION" 2>&1) \
    || fail "삭제" "스택 $stack 지우기" "$out" "삭제 실패"
  out=$(aws cloudformation wait stack-delete-complete --stack-name "$stack" \
    --profile "$PROFILE" --region "$REGION" 2>&1) \
    || fail "삭제" "스택 $stack 지우기" "$(stack_failure_reason "$stack") $out" "삭제 실패"
  say "지웠습니다: 스택 $stack"
}
destroy() {
  step "삭제 대상"
  local c_status a_status log_bucket="" art_bucket="" pkg_present=0 answer token
  c_status=$(stack_status "$STACK_C")
  a_status=$(stack_status "$STACK_A")
  resolve_bucket
  if [ "$c_status" != NONE ]; then
    log_bucket=$(stack_output "$STACK_C" AccessLogBucketName)
    say "  스택 C $STACK_C ($c_status) · 로그 버킷 ${log_bucket:-없음}"
  fi
  if [ "$a_status" != NONE ]; then
    art_bucket=$(stack_output "$STACK_A" ArtifactBucketName)
    say "  스택 A $STACK_A ($a_status) · 산출물 버킷 ${art_bucket:-없음} (스냅샷 이력 포함 · 되돌릴 수 없습니다)"
  fi
  if [ -n "$BUCKET" ] && aws s3api head-bucket --bucket "$BUCKET" --expected-bucket-owner "$ACCOUNT" \
    --profile "$PROFILE" --region "$REGION" > /dev/null 2>&1; then
    pkg_present=1
    say "  패키지 버킷 $BUCKET (모든 버전)"
  fi
  if [ "$c_status" = NONE ] && [ "$a_status" = NONE ] && [ "$pkg_present" = 0 ]; then
    say "지울 것이 없습니다(스택 A·C 와 패키지 버킷이 없습니다). 패키지 버킷이 남아 있으면 --bucket 으로 이름을 주세요"
    return
  fi
  say "  소스 계정의 스택 B 는 지우지 않습니다 — 각 계정에서 aws cloudformation delete-stack --stack-name $STACK_B --profile <소스 계정 프로필> --region <그 스택 리전>"
  if [ "$DRY_RUN" = 1 ]; then
    say "--dry-run — 아무것도 지우지 않았습니다"
    return
  fi
  token=${BUCKET:-$STACK_A}
  echo
  printf '정말 지우려면 「%s」 를 그대로 입력해 주세요: ' "$token" >&2
  answer=""
  read -r answer || true
  [ "$answer" = "$token" ] || { echo "입력이 달라 아무것도 지우지 않았습니다." >&2; exit 1; }
  step "삭제"
  if [ "$c_status" != NONE ]; then
    delete_stack "$STACK_C"
    [ -z "$log_bucket" ] || empty_and_delete_bucket "$log_bucket"
  fi
  if [ "$a_status" != NONE ]; then
    delete_stack "$STACK_A"
    [ -z "$art_bucket" ] || empty_and_delete_bucket "$art_bucket"
  fi
  [ "$pkg_present" = 0 ] || empty_and_delete_bucket "$BUCKET"
  say "삭제가 끝났습니다. 서명 비밀키($OUT/viewer-k*.pem) · 발급 기록($LINK_LEDGER) · out/ 는 이 PC 에 남아 있습니다 — 필요 없으면 직접 지워 주세요"
}

# ── 실행 ────────────────────────────────────────────────────────────────────────────────────────
preflight
if [ -n "$KEY_OP" ]; then
  # 키 교체는 스택 C 만 — 스택 A·수집·소스 계정 안내를 타지 않는다(유출 대응이 수집을 기다리지 않게).
  if [ "$KEY_OP" = rotate-key ]; then rotate_key; else retire_old_key; fi
  exit 0
fi
case "$ACTION" in
  destroy)
    destroy
    ;;
  collect)
    check_stack_usable "$STACK_A" "3단계"
    A_STATUS=$(stack_status "$STACK_A")
    [ "$A_STATUS" != NONE ] || fail "3단계" "수집" "스택 A($STACK_A)가 없습니다. 먼저 ./deploy.sh 로 설치해 주세요" "2단계 스택 A 배포 실패"
    collect
    diagram_hint
    ;;
  download)
    download
    diagram_hint
    ;;
  install)
    resolve_bucket
    [ -n "$BUCKET" ] || die "--bucket 이 없습니다 — 처음 설치에는 패키지 버킷 이름이 필요합니다"
    A_STATUS=$(stack_status "$STACK_A")
    case "$A_STATUS" in
      NONE | REVIEW_IN_PROGRESS)
        [ -n "$REGIONS" ] || die "--regions 가 없습니다 — 처음 설치에는 수집 리전 목록이 필요합니다(적지 않은 리전은 화면에 나타나지 않습니다)"
        ;;
    esac
    ensure_bucket
    upload_package
    [ "$MONITORING" = 0 ] || upload_state_lambda
    deploy_stack_a
    A_STATUS=$(stack_status "$STACK_A")
    if [ "$DRY_RUN" = 1 ] && [ "$A_STATUS" = REVIEW_IN_PROGRESS ]; then
      say "스택 A 가 아직 없어 수집·스택 C 는 건너뜁니다. 변경 세트만 든 빈 스택(REVIEW_IN_PROGRESS)이 남습니다 — 실제 실행이 그대로 이어 쓰고, 지우려면 --destroy"
    else
      collect
      [ "$VIEWER" = 0 ] || viewer
    fi
    handoff
    diagram_hint
    ;;
esac
