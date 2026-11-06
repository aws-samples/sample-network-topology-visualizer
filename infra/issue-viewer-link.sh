#!/usr/bin/env bash
# 스택 C 의 「보는 링크」(CloudFront 서명 URL, 고정 정책)를 만든다 — 안내서 docs/install-guide.md 6단계.
#
#   infra/issue-viewer-link.sh --stack nettopo-viewer --region <스택 A·C 리전> --profile <프로필> \
#     --key <비밀키.pem> --days <일수>  > link.txt
#
# - 링크는 **stdout 한 줄**, 만료 안내는 stderr 다. 🔴 링크는 비밀번호처럼 다룬다 — 가진 사람은 누구나 본다.
# - 서명은 이 PC 의 `openssl` 만 쓴다(CloudShell 포함). 비밀키는 AWS 로 가지 않는다. 만료는 ~/.local/state/nettopo/viewer-links.log 에 적는다(#141).
# - 링크가 여는 경로는 `/index.html` **하나**다. 수명은 관리자가 고른다(기본값을 두지 않는다).
# - `--domain`·`--key-pair-id` 를 주면 스택을 조회하지 않는다(오프라인 · `infra/tests` 가 이 경로를 쓴다).
#   `--expires <epoch>` 는 `--days` 대신 만료 시각을 직접 준다.
set -euo pipefail

die() { echo "issue-viewer-link: $*" >&2; exit 2; }

stack="" region="" profile="" key="" days="" domain="" keypair="" expires="" outputs=""
while [ $# -gt 0 ]; do
  case "$1" in
    --stack) stack=${2-}; shift 2 ;;
    --region) region=${2-}; shift 2 ;;
    --profile) profile=${2-}; shift 2 ;;
    --key) key=${2-}; shift 2 ;;
    --days) days=${2-}; shift 2 ;;
    --domain) domain=${2-}; shift 2 ;;
    --key-pair-id) keypair=${2-}; shift 2 ;;
    --expires) expires=${2-}; shift 2 ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) die "모르는 인자: $1" ;;
  esac
done

[ -n "$key" ] || die "--key <비밀키.pem> 가 없다"
[ -r "$key" ] || die "비밀키를 읽을 수 없다: $key"

if [ -n "$expires" ]; then
  case "$expires" in ''|*[!0-9]*) die "--expires 는 epoch 초(숫자)다" ;; esac
else
  [ -n "$days" ] || die "--days <일수> 가 없다 — 링크 수명은 관리자가 고른다(기본값 없음)"
  case "$days" in ''|*[!0-9]*) die "--days 는 1 이상의 정수다" ;; esac
  [ "$days" -ge 1 ] || die "--days 는 1 이상의 정수다"
  expires=$(( $(date +%s) + days * 86400 ))
fi
[ "$expires" -gt "$(date +%s)" ] || die "만료 시각이 이미 지났다"

if [ -z "$domain" ] || [ -z "$keypair" ]; then
  # AWS CloudShell 에는 이름 있는 프로필이 없다(콘솔 로그인 권한 = 기본 자격 증명) — 거기서만 --profile 을 생략할 수 있다(deploy.sh 와 같은 신호).
  cloudshell=0
  if [ "${AWS_EXECUTION_ENV:-}" = CloudShell ] || [ "$HOME" = /home/cloudshell-user ]; then cloudshell=1; fi
  [ -n "$stack" ] && [ -n "$region" ] && { [ -n "$profile" ] || [ "$cloudshell" = 1 ]; } \
    || die "--stack·--region·--profile 이 모두 필요합니다(또는 --domain·--key-pair-id · CloudShell 에서는 --profile 생략 가능)"
  outputs=$(aws cloudformation describe-stacks --stack-name "$stack" --query 'Stacks[0].Outputs[?OutputKey==`ViewerDomain` || OutputKey==`ViewerKeyPairId`].[OutputKey,OutputValue]' --output text ${profile:+--profile "$profile"} --region "$region")
  domain=$(printf '%s\n' "$outputs" | awk '$1 == "ViewerDomain" {print $2}')
  keypair=$(printf '%s\n' "$outputs" | awk '$1 == "ViewerKeyPairId" {print $2}')
  [ -n "$domain" ] && [ -n "$keypair" ] || die "스택 출력에 ViewerDomain·ViewerKeyPairId 가 없다: $stack"
fi

resource="https://${domain}/index.html"
# 고정 정책(canned). 🔴 공백 없는 이 모양 그대로여야 CloudFront 가 같은 바이트로 다시 만들어 검사한다.
policy="{\"Statement\":[{\"Resource\":\"${resource}\",\"Condition\":{\"DateLessThan\":{\"AWS:EpochTime\":${expires}}}}]}"
# SHA-256 서명 + `Hash-Algorithm=SHA256` 쿼리(CloudFront 기본은 SHA-1 — 이 매개변수가 없으면 SHA-1 로 검증해 403 이다 · 2026-10-28)
signature=$(printf '%s' "$policy" | openssl dgst -sha256 -sign "$key" | openssl base64 -A | tr '+=/' '-_~')
[ -n "$signature" ] || die "서명 실패(openssl)"

# #141 — 키를 겹쳐 교체하는 동안 키 그룹에는 두 키가 있다. 새 링크는 **새 자리(KeyLabel) 키**로만 서명한다:
#   ViewerKeyPairId 와 짝이 아닌 비밀키로 서명한 링크는 403 이고, 물러나는 옛 키로 서명한 링크는 옛 키를 빼는 날 끊긴다.
#   그래서 스택을 조회한 경우에는 이 비밀키의 공개키가 스택의 ViewerPublicKeyPem 인지 확인한 뒤에만 링크를 내놓는다.
if [ -n "$stack" ] && [ -n "$outputs" ]; then
  flat() { tr -d ' \t\r\n'; }
  stack_pem() {
    aws cloudformation describe-stacks --stack-name "$stack" --query "Stacks[0].Parameters[?ParameterKey=='$1'].ParameterValue" --output text ${profile:+--profile "$profile"} --region "$region"
  }
  mine=$(openssl rsa -in "$key" -pubout 2> /dev/null | flat) || die "RSA 비밀키가 아니다: $key"
  active=$(stack_pem ViewerPublicKeyPem | flat)
  if [ "$mine" != "$active" ]; then
    previous=$(stack_pem PreviousPublicKeyPem | flat)
    [ -z "$previous" ] || [ "$previous" = None ] || [ "$mine" != "$previous" ] \
      || die "이 비밀키는 물러나는 옛 키다(PreviousPublicKeyPem) — 새 링크는 새 키(KeyLabel 자리)로 서명한다"
    die "이 비밀키는 스택 $stack 의 서명 키(ViewerPublicKeyPem)와 짝이 아니다 — 이 키로 낸 링크는 403 이다: $key"
  fi
fi

printf '%s?Expires=%s&Signature=%s&Key-Pair-Id=%s&Hash-Algorithm=SHA256\n' "$resource" "$expires" "$signature" "$keypair"
when=$(date -u -d "@$expires" '+%Y-%m-%d %H:%M UTC' 2>/dev/null || date -u -r "$expires" '+%Y-%m-%d %H:%M UTC')
echo "만료: $when · 이 링크를 가진 사람은 누구나 본다 · 전부 무효화 = 키 교체(docs/install-guide.md 6단계)" >&2

# 발급 기록(#141) — 「만료 · Key-Pair-Id · 발급 시각」 한 줄. 링크·서명은 적지 않는다.
# 키를 겹쳐 교체할 때 deploy.sh 가 이 기록에서 「옛 키로 낸 링크의 가장 늦은 만료」 = 옛 키를 빼도 되는 때를 읽는다.
# 자리 = 사용자 상태 폴더(XDG) 한 곳 — 비밀키 옆에 두면 안내서처럼 저장소 루트에서 서명할 때 추적 안 된 파일로 남는다.
# 🔵 이 PC(이 사용자)에서 낸 링크만 셈한다 — 다른 PC·CloudShell 에서 낸 링크는 그쪽 기록에 있다.
ledger="${XDG_STATE_HOME:-$HOME/.local/state}/nettopo/viewer-links.log"
if ! (umask 077 && mkdir -p "$(dirname "$ledger")" && printf '%s\t%s\t%s\n' "$expires" "$keypair" "$(date +%s)" >> "$ledger") 2> /dev/null; then
  echo "경고: 발급 기록을 남기지 못했다($ledger) — 키를 교체할 때 이 링크의 만료를 셈하지 못한다" >&2
fi
