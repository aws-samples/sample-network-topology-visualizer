# 배포 가이드

이 문서는 NetworkTopology 를 고객 AWS 계정에 올리는 절차입니다. 무엇을 하는 툴인지, 아키텍처, 비용은 [README](../README.ko.md) 에 있습니다.

용어: **모니터링 계정**은 수집기(스택 A)를 올리는 계정, **소스 계정**은 다이어그램에 넣을 리소스가 있는 계정(스택 B)입니다. 수집기는 소스 계정의 IAM 역할로 전환해 읽기 API 만 호출하고, 결과는 모니터링 계정의 S3 버킷에만 저장합니다.

- **스택 A** (`nettopo-collector`): 모니터링 계정의 수집기. 항상 필요합니다.
- **스택 B** (`nettopo-member-role`): 소스 계정마다 하나씩 두는 읽기 전용 IAM 역할. 소스 계정이 있을 때만.
- **스택 C** (`nettopo-viewer`): AWS 계정이 없는 사람에게 서명된 보는 링크를 주는 선택 CloudFront 뷰어.

## 목차

- [무엇이 만들어지나](#무엇이-만들어지나)
- [배포 옵션](#배포-옵션)
- [사전 준비](#사전-준비)
- [코드 받기](#코드-받기)
- [배포 방식 고르기](#배포-방식-고르기)
- [0. 값 정하기](#0-값-정하기)
- [한 번에 설치하기 (deploy.sh)](#한-번에-설치하기-deploysh) (권장. 이것만 하고 [다이어그램 열기](#다이어그램-열기)로 끝내도 됩니다)
- [직접 설치하기 (단계별)](#직접-설치하기-단계별)
- [다이어그램 열기](#다이어그램-열기)
- [소스 계정 추가](#소스-계정-추가)
- [선택 기능 (필요할 때만)](#선택-기능-필요할-때만)
  - [모니터링·사용률 추가 배포 (스택 A 갱신)](#5-모니터링사용률-추가-배포-스택-a-갱신)
  - [CloudFront로 공유하기 (스택 C 배포·링크 발급)](#6-cloudfront로-공유하기-스택-c-배포링크-발급)
- [새 버전으로 올리기](#새-버전으로-올리기)
- [삭제](#삭제)
- [문제 해결](#문제-해결)
- [파라미터](#파라미터)
- [보안](#보안)

## 무엇이 만들어지나

| | |
|---|---|
| **만들어지는 것** | 모니터링 계정에 스택 A: CodeBuild 프로젝트(수집기), 그것을 한 시간마다 실행하는 EventBridge 스케줄, 다이어그램을 담는 비공개 S3 산출물 버킷, 그리고 이들의 IAM 역할과 CloudWatch Logs 그룹. 배포 패키지용 비공개 S3 버킷 하나는 직접 만듭니다. 선택: 스택 B — 소스 계정마다 읽기 전용 IAM 역할(`NetworkReadOnlyRole`) 하나 · 스택 C — 모니터링 계정에 CloudFront 배포와 그 접근 로그 버킷 · 모니터링·사용률 — 스택 A 에 Lambda 함수 2개와 SQS 실패 대기열 |
| **결과물** | 내 컴퓨터의 `out/index.html`: 다이어그램이 든 HTML 파일 하나. 수집기가 S3 의 것을 한 시간마다 갱신합니다 |
| **걸리는 시간** | 스택 A: 수 분(시험에서는 약 2분). 첫 수집: 보통 2~5분(계정 2개·리전 3개 시험에서 1분 47초). 스택 B: 계정마다 약 30초. 스택 C: 수 분(시험에서는 약 3분 30초, 대부분 CloudFront) |
| **비용** | 템플릿 기본값으로 월 약 $7 ~ $18. 거의 전부 CodeBuild 실행 시간입니다(한 시간마다, 보통 회당 2~5분). 실시간 모니터링·CloudFront·S3 이력·로그는 사용량만큼 더해집니다. 자세한 내용: README 의 [비용](../README.ko.md#비용) |
| **필요한 권한** | 모니터링 계정: IAM 역할, S3 버킷, CodeBuild, EventBridge, Lambda, CloudWatch Logs, SQS, CloudFront 생성. 소스 계정마다: IAM 역할 하나 생성. 액션 단위 목록: [필요한 IAM 권한 §6](required-permissions.md#6-배포하는-사람에게-필요한-권한) |

## 배포 옵션

모니터링·사용률 조회와 CloudFront 공유는 선택 기능입니다. 모니터링·사용률 조회는 스택 A 의 `StateLambdaSourceKey` 를 설정해 배포하고, CloudFront 공유는 스택 C 를 추가로 배포합니다.

| 사용할 기능 | 필요한 배포 | 처음 설치할 때 | 기존 설치에 추가할 때 |
|---|---|---|---|
| 기본 다이어그램 | 스택 A, 소스 계정이 있으면 스택 B | 1~3단계, 소스 계정이 있으면 4단계 | 이미 사용 가능 |
| 모니터링·사용률 조회 | 스택 A 에 `StateLambdaSourceKey` 설정 | 1단계에서 Lambda 패키지도 업로드 → 2단계에서 함께 배포 → 수집 후 5단계의 배포 결과 확인 | [5단계](#5-모니터링사용률-추가-배포-스택-a-갱신)에서 스택 A 갱신 |
| CloudFront 공유 | 스택 C 와 서명 링크 | 기본 다이어그램 수집 후 [6단계](#6-cloudfront로-공유하기-스택-c-배포링크-발급) | 6단계에서 스택 C 배포·링크 발급 |
| 모니터링·사용률 조회 + CloudFront 공유 | 위 두 배포 모두 | 모니터링·사용률을 포함해 설치한 뒤 6단계 | 5단계 → 6단계 |

[deploy.sh](#한-번에-설치하기-deploysh) 로는 같은 선택을 `--monitoring` · `--viewer --days N` 옵션으로 합니다. 표의 단계 번호는 [직접 설치하기](#직접-설치하기-단계별)와 4~6단계를 가리킵니다.

## 사전 준비

시작하기 전에 하나씩 확인합니다.

- [ ] **AWS CLI v2**(`aws --version` 이 `aws-cli/2.…` 를 출력. v1 이면 `deploy.sh` 가 멈춥니다), **bash**(macOS·Linux·AWS CloudShell 기본), **git**
- [ ] **모니터링 계정의 이름 있는 프로필**(`--profile`). IAM Identity Center 프로필을 권장합니다. 아래를 봅니다
- [ ] **권한**: 모니터링 계정에서 IAM 역할, S3 버킷, CodeBuild, EventBridge, Lambda, CloudWatch Logs, SQS, CloudFront 를 만들 수 있는 자격 증명. 소스 계정마다 IAM 역할을 만들 수 있는 자격 증명. 각 스택이 만드는 권한은 [필요한 IAM 권한](required-permissions.md)에, 배포하는 사람에게 필요한 액션 목록은 [같은 문서 §6](required-permissions.md#6-배포하는-사람에게-필요한-권한)에 있습니다
- [ ] 모니터링·사용률 조회를 선택한 경우에만: **python3 와 pip**. pip 이 없으면 `python3 -m ensurepip --user`
- [ ] CloudFront 공유를 선택한 경우에만: **openssl**

이 문서의 명령은 저장소 최상위 폴더에서 실행하며, 자격 증명은 이름 있는 프로필(`--profile`)로 넘깁니다. IAM Identity Center 프로필을 권장합니다. 장기 액세스 키는 만들지 않습니다.

```sh
aws configure sso --profile my-hub
```

다른 프로필을 거쳐 역할을 전환하는 경우에는 `~/.aws/config` 에 `role_arn` 과 `source_profile` 을 적은 프로필을 씁니다.

**잘 됐다면:** `aws sts get-caller-identity --profile my-hub --region <리전>` 이 모니터링 계정 ID 를 출력합니다. IAM Identity Center 세션이 만료됐으면 `aws sso login --profile my-hub` 을 실행합니다([문제 해결](#문제-해결) 표 「deploy.sh 가 0단계 자격 증명 확인에서 멈춤」 행).

## 코드 받기

```sh
git clone https://github.com/aws-samples/sample-network-topology-visualizer.git
cd sample-network-topology-visualizer
```

**잘 됐다면:** 폴더에 `deploy.sh`, `infra/`, `engine/`, `renderer/` 가 있습니다. 이후 모든 명령은 이 폴더에서 실행합니다. 배포 패키지를 git clone 에서 만들기 때문에, zip 으로 내려받은 저장소로는 진행되지 않습니다([문제 해결](#문제-해결) 표 「1단계 배포 패키지 실패」 행).

## 배포 방식 고르기

| 상황 | 하는 단계 | 4단계 |
|---|---|---|
| A. 계정 하나만 그린다 | 1, 2, 3 | 건너뜀 |
| B. 소스 계정이 몇 개 있고 직접 관리한다 | 1, 2, 3, 4-B | 계정마다 스택 B 를 올림 |
| C. AWS Organizations 를 쓴다 | 1, 2, 3, 4-C | 조직 관리자가 StackSet 을 OU 에 배포 |

위 표는 계정 구성에 따른 기본 단계입니다. 모니터링·사용률 조회와 CloudFront 공유는 앞의 [배포 옵션 표](#배포-옵션)에 따라 추가합니다.

모니터링 계정에 Transit Gateway 가 있으면 소스 계정은 TGW 연결의 소유 계정으로 자동 발견됩니다. TGW 가 없거나 TGW 에 연결되지 않은 소스 계정이 있으면 2단계에서 `TargetAccounts` 에 그 계정을 적습니다. 적어도 자동 발견은 그대로 돌고 적은 계정이 거기에 더해지므로, 이미 TGW 가 아는 계정을 다시 적을 필요는 없습니다.

[deploy.sh](#한-번에-설치하기-deploysh) 를 쓰면 1~3단계를 스크립트가 대신 하고, 4단계는 어느 쪽이든 같습니다.

## 0. 값 정하기

터미널에서 한 번 정해 둡니다. `deploy.sh` 를 포함해 이 문서의 모든 명령이 이 값을 쓰므로, 명령을 그대로 복사해 붙여 넣으면 됩니다. 터미널을 새로 열면 다시 정합니다.

```sh
PROFILE=my-hub                              # 모니터링 계정 프로필
REGION=ap-northeast-2                       # 스택을 둘 리전 (아무 리전이나 가능)
PKG_BUCKET=nettopo-pkg-111122223333         # 배포 패키지를 둘 새 비공개 버킷. 전 세계에서 유일해야 함
REGIONS=ap-northeast-2,ap-northeast-1       # 수집할 리전 목록 (쉼표 구분)
```

`REGIONS` 에 적지 않은 리전은 수집하지 않습니다. 화면에 그 리전 칸이 아예 나타나지 않고 따로 안내도 없으니, 보려는 리전은 빠뜨리지 말고 모두 적어 주세요. 4단계의 소스 계정 역할에도 같은 목록을 줍니다.

스택 이름은 `nettopo-collector`, `nettopo-member-role`, `nettopo-viewer` 를 그대로 쓰는 것을 권장합니다. 소스 계정 역할의 신뢰 정책 기본값이 `nettopo-collector` 라는 이름의 스택을 가리킵니다.

## 한 번에 설치하기 (deploy.sh)

최상위 폴더의 `deploy.sh` 가 [직접 설치하기](#직접-설치하기-단계별)의 1~3단계, [5](#5-모니터링사용률-추가-배포-스택-a-갱신)·[6](#6-cloudfront로-공유하기-스택-c-배포링크-발급)단계와 [삭제](#삭제)를 대신 실행합니다. 처음 설치는 이 방법을 권장합니다. 모니터링 계정에서만 씁니다. 아래 단계별 명령은 스크립트가 무엇을 하는지 검토하거나 직접 실행하고 싶을 때 봅니다.

```sh
# 처음 설치. 같은 명령을 다시 실행하면 갱신입니다
./deploy.sh --profile "$PROFILE" --region "$REGION" \
  --bucket "$PKG_BUCKET" --regions "$REGIONS"
```

**잘 됐다면:** 스크립트가 단계(`== 0단계 …` ~ `== 3단계 …`)를 출력하면서 패키지 버킷이 없으면 만들고, 스택 A 를 올리고, 수집 한 번을 기다린 뒤 `🔴 … 실패` 줄 없이 끝납니다. `out/index.html`(다이어그램)이 생기고, 출력의 마지막 줄이 그 경로를 알려 줍니다. [다이어그램 열기](#다이어그램-열기)로 진행합니다. 계정 하나의 기본 다이어그램이면 거기서 끝내도 됩니다.

| 하려는 것 | 명령에 더할 것 |
|---|---|
| 모니터링·사용률 조회 | `--monitoring` (모니터링을 쓰는 설치는 갱신할 때도 매번 줍니다) |
| CloudFront 공유 | `--viewer --days 30` (링크 수명 일수. 기본값이 없어 꼭 적습니다) |
| TGW 에 연결되지 않은 소스 계정 추가 | `--accounts 111122223333,444455556666` |
| 스택 태그 | `--tags Owner=net,CostCenter=1234` (처음 한 번. 이후 생략하면 기존 태그를 다시 붙입니다) |
| 바뀌는 리소스만 미리 보기 | `--dry-run` |
| 수집만 다시 한 번 | `--collect` |
| 최신 다이어그램만 받기(수집·배포 없음) | `--download` (설치와 `--collect` 는 끝에 이미 받아 옵니다 — 한 시간마다 자동 갱신된 그림을 다시 받을 때 씁니다) |
| 서명 키 교체 | 계획된 교체 `--viewer --rotate-key --days 30`(옛 링크는 만료까지 열림) → 옛 링크가 만료된 뒤 `--viewer --retire-old-key` · 링크 유출 `--viewer --rotate-key --no-overlap --days 30`(지금까지 낸 링크 전부 403) |
| 모니터링 계정에서 모두 삭제 | `--destroy` (확인 입력을 받습니다) |
| [선택 보강](#선택-보강) · 외부 ID | `--external-id <외부 ID>` (스택 A `MemberExternalId` 에 넣고, 화면에 출력되는 스택 B 명령 두 개에도 `ExternalId` 로 넣습니다. 이후 생략하면 이전 값을 유지합니다) |
| [선택 보강](#선택-보강) · 조직 조건 | `--org-id o-<조직 ID>` (화면에 출력되는 스택 B 명령 두 개에 `PrincipalOrgId` 를 넣습니다. 저장하지 않으니 명령을 다시 받을 때도 줍니다) |
| [선택 보강](#선택-보강) · 버킷 CORS·CloudFront 읽기를 스택 C 배포 하나로 | `--viewer --days 30 --narrow-viewer` (스택 C 를 올린 뒤 그 주소·배포 ID 로 스택 A 를 한 번 더 갱신합니다. 이후 생략해도 유지됩니다) |

- `--bucket` 과 `--regions` 는 처음 설치에만 필요합니다. 이후 생략하면 이전 값을 그대로 씁니다. `--regions` 의 뜻은 0단계의 `REGIONS` 와 같습니다.
- 끝나면 `out/index.html`(다이어그램)과 `out/templates/`(소스 계정·조직 관리자에게 넘길 스택 B 템플릿과 `SHA256SUMS`)가 생기고, 소스 계정에서 실행할 스택 B 명령이 화면에 출력됩니다. 4단계는 그 명령으로 진행합니다.
- CloudFront 공유의 링크 서명 비밀키는 `out/viewer-k1.pem` 에 만들어집니다. 이 PC 밖으로 보내지 말고 보관합니다. 키를 잃으면 키를 교체해야 합니다.
- 실패하면 「N단계 <무엇> 실패: <이유>」 한 줄과 [문제 해결](#문제-해결) 표의 행 이름이 출력됩니다.
- 스택 이름은 `nettopo-collector`, `nettopo-viewer` 로 고정입니다. 전체 옵션은 `./deploy.sh --help` 에 있습니다.
- **AWS CloudShell** 에서는 `--profile` 을 생략합니다(콘솔에 로그인한 권한을 그대로 씁니다). 그 밖의 곳에서는 `--profile` 이 필수입니다. CloudShell 안에 생긴 `out/index.html` 은 그 자리에서 브라우저로 열 수 없으니, 메뉴 「작업 → 파일 다운로드」로 PC 에 받거나 S3 콘솔에서 산출물 버킷의 `index.html` 을 바로 다운로드합니다(스크립트가 끝에 이 안내를 출력합니다).

## 직접 설치하기 (단계별)

`deploy.sh` 로 설치했다면 이 절은 건너뛰고 [다이어그램 열기](#다이어그램-열기)로 갑니다. 스크립트가 실행하는 단계와 같습니다. 명령은 [0단계](#0-값-정하기)에서 정한 값을 씁니다.

### 1. 배포 패키지를 비공개 버킷에 올리기

배포 패키지는 이 저장소의 `engine/`(수집 엔진)과 `renderer/`(다이어그램 생성) 두 폴더를 묶은 zip 입니다. 수집기(CodeBuild)가 실행할 때마다 이 zip 을 여러분 버킷에서 내려받아 실행합니다.

```sh
# 배포 패키지 만들기
mkdir -p out
git archive --format=zip -o out/nettopo-source.zip HEAD engine renderer buildspec.yml
```

```sh
# 비공개 버킷을 만들고 올리기
aws s3api create-bucket --bucket "$PKG_BUCKET" \
  --create-bucket-configuration LocationConstraint="$REGION" \
  --profile "$PROFILE" --region "$REGION"
aws s3api put-public-access-block --bucket "$PKG_BUCKET" \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true \
  --profile "$PROFILE" --region "$REGION"
aws s3 cp out/nettopo-source.zip "s3://$PKG_BUCKET/nettopo-source.zip" \
  --sse AES256 --profile "$PROFILE" --region "$REGION"
```

`REGION` 이 `us-east-1` 이면 `--create-bucket-configuration` 옵션을 뺍니다. 새 버전으로 올릴 때처럼 버킷이 이미 있으면 `create-bucket`·`put-public-access-block` 두 명령은 건너뛰고 `aws s3 cp` 만 실행합니다(다시 실행하면 `BucketAlreadyOwnedByYou` 오류가 납니다).

```sh
# 버킷이 비공개인지 확인합니다. 네 값이 모두 true 여야 합니다
aws s3api get-public-access-block --bucket "$PKG_BUCKET" --profile "$PROFILE" --region "$REGION"
```

**잘 됐다면:** `aws s3 cp` 가 `upload: out/nettopo-source.zip to s3://nettopo-pkg-111122223333/nettopo-source.zip`(여러분의 버킷 이름)을 출력하고, 확인 명령에서 `BlockPublicAcls`·`IgnorePublicAcls`·`BlockPublicPolicy`·`RestrictPublicBuckets` 가 모두 `true` 입니다. `create-bucket` 이 이름이 이미 쓰여 실패하면 다른 `PKG_BUCKET` 을 고릅니다([문제 해결](#문제-해결) 표 「1단계 버킷 만들기 실패」 행).

#### 상태 수집 Lambda 패키지 준비

모니터링·사용률 조회를 선택한 경우에만 실행합니다. Lambda 패키지는 `SourceLocation` 에 지정할 소스 ZIP 과 **같은 패키지 버킷**에 올립니다.

```sh
# 출력 마지막의 "S3 키" 값을 STATE_KEY 에 넣습니다
./infra/build-state-lambda.sh
STATE_KEY='<출력된 S3 키>'    # 예: lambda/state-observer-b813997a630e.zip

aws s3 cp "out/${STATE_KEY#lambda/}" "s3://$PKG_BUCKET/$STATE_KEY" \
  --sse AES256 --profile "$PROFILE" --region "$REGION"
```

처음 설치한다면 2단계의 모니터링·사용률 포함 명령으로 진행합니다. 기존 스택 A 에 기능을 추가하는 중이라면 버킷 생성과 소스 ZIP 업로드는 반복하지 않고, 위 Lambda 패키지 준비만 실행한 뒤 [5단계](#5-모니터링사용률-추가-배포-스택-a-갱신)로 돌아갑니다.

### 2. 스택 A (수집기) 올리기

처음 설치할 때는 아래 두 배포 명령 중 **선택한 기능에 맞는 하나만** 실행합니다. 기존 설치에 모니터링·사용률을 추가하려면 [5단계](#5-모니터링사용률-추가-배포-스택-a-갱신)의 갱신 명령을 사용합니다.

#### 기본 다이어그램 또는 CloudFront 공유만 선택한 경우

```sh
aws cloudformation deploy \
  --template-file infra/stack-a-collector.yaml \
  --stack-name nettopo-collector \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
      SourceLocation="$PKG_BUCKET/nettopo-source.zip" \
      TargetRegions="$REGIONS" \
  --profile "$PROFILE" --region "$REGION"
```

#### 모니터링·사용률을 포함해 설치하는 경우

1단계에서 Lambda 패키지를 올린 뒤 실행합니다. `StateLambdaSourceKey` 에 위에서 정한 `STATE_KEY` 를 넣으면 처음 배포에 모니터링·사용률 기능이 포함됩니다.

```sh
aws cloudformation deploy \
  --template-file infra/stack-a-collector.yaml \
  --stack-name nettopo-collector \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
      SourceLocation="$PKG_BUCKET/nettopo-source.zip" \
      TargetRegions="$REGIONS" \
      StateLambdaSourceKey="$STATE_KEY" \
  --profile "$PROFILE" --region "$REGION"
```

어느 명령을 선택했든 배포가 끝나면 산출물 버킷 이름을 확인합니다.

```sh
ARTIFACT_BUCKET=$(aws cloudformation describe-stacks --stack-name nettopo-collector \
  --query "Stacks[0].Outputs[?OutputKey=='ArtifactBucketName'].OutputValue" --output text \
  --profile "$PROFILE" --region "$REGION")
echo "$ARTIFACT_BUCKET"
```

**잘 됐다면:** 배포 명령은 수 분(시험에서는 약 2분) 걸리고 `Successfully created/updated stack - nettopo-collector` 로 끝나며, `echo` 가 산출물 버킷 이름을 출력합니다. 배포가 실패하면 출력된 이유를 읽고 [문제 해결](#문제-해결) 표 「2단계 스택 A 배포 실패」·「스택이 진행 중 또는 ROLLBACK_COMPLETE」 행을 봅니다.

- `TargetAccounts` 를 비워 두면 모니터링 계정의 Transit Gateway 연결에서 소스 계정을 자동으로 찾습니다. TGW 에 연결되지 않은 소스 계정이 있으면 `TargetAccounts=<소스 계정 ID>,…` 를 `--parameter-overrides` 에 추가합니다. 적어도 자동 발견은 계속 돌아가고 여기 적은 계정이 거기에 더해지므로 **TGW 가 모르는 계정만** 적으면 됩니다. 어느 경우든 모니터링 계정은 항상 포함됩니다. 비워 두면 발견에만 의존하므로, 모든 리전에서 발견이 실패하면 수집이 멈춥니다.
- 기본 스케줄은 1시간마다 수집입니다. 수동으로만 돌리려면 `ScheduleState=DISABLED` 를 추가합니다.
- `--tags` 로 태그를 붙였다면 이후 모든 갱신 명령에도 같은 `--tags` 를 붙입니다. 빼면 태그가 지워질 수 있습니다.

> **Note** 템플릿은 영어로 되어 있습니다. CloudFormation 이 템플릿의 한글 설명을 `?` 로 저장하기 때문입니다. 파라미터의 한글 설명은 아래 [파라미터](#파라미터) 표에 있습니다.

### 3. 최초 리소스 수집

스택 A 는 한 시간마다 자동으로 수집하지만, 기다리지 않고 한 번 바로 실행해 결과를 확인합니다.

```sh
# 수집을 한 번 실행합니다
BUILD_ID=$(aws codebuild start-build --project-name nettopo-collector-collector \
  --query build.id --output text --profile "$PROFILE" --region "$REGION")

# 상태가 SUCCEEDED 가 될 때까지 다시 실행해 확인합니다 (보통 2~5분)
aws codebuild batch-get-builds --ids "$BUILD_ID" \
  --query "builds[0].buildStatus" --output text --profile "$PROFILE" --region "$REGION"

# 다이어그램을 내려받습니다
aws s3 cp "s3://$ARTIFACT_BUCKET/index.html" out/index.html --profile "$PROFILE" --region "$REGION"
```

**잘 됐다면:** 상태 확인 명령이 수집 중에는 `IN_PROGRESS`, 끝나면 `SUCCEEDED` 를 출력하고(계정 2개·리전 3개 시험에서 1분 47초), `aws s3 cp` 가 `download: s3://…/index.html to out/index.html` 을 출력합니다. `out/index.html` 을 브라우저로 열고 [다이어그램 열기](#다이어그램-열기)대로 확인합니다.

수집기가 무엇을 했는지는 CodeBuild 로그로 봅니다.

```sh
aws logs tail /aws/codebuild/nettopo-collector-collector --since 1h --profile "$PROFILE" --region "$REGION"
```

계정 발견 줄(예: `계정 자동 발견(ec2:DescribeTransitGatewayAttachments) — 조회한 리전 3개 · 허브 1 + 멤버 1개: …`)과 계정 이름 줄(예: `계정 이름 2/2개 읽음`)을 찾습니다.

모니터링·사용률을 포함해 설치했다면 `SUCCEEDED` 뒤 잠시 기다렸다가 내려받습니다. 이 설치에서는 빌드가 끝난 직후 서명 함수가 `index.html` 을 따로 게시하기 때문에, 바로 받으면 처음 설치는 `404`, 갱신 때는 직전 판을 받을 수 있습니다. `404` 가 나오면 1분쯤 뒤 다시 받습니다(`deploy.sh` 는 게시를 기다린 뒤 받습니다).

빌드가 `FAILED` 로 끝나면 CodeBuild 로그를 확인합니다. 로그에 계정 발견 실패가 있으면 모니터링 계정에 Transit Gateway 가 없거나 그 연결을 읽지 못한 것입니다. 이때는 발견에 기댈 수 없으니 `TargetAccounts=<소스 계정 ID 목록>` 에 보려는 소스 계정을 적어 2단계 명령에 추가하고 다시 올립니다([문제 해결](#문제-해결) 표 「빌드 `FAILED`, 로그에 계정 발견 실패」 행).

## 다이어그램 열기

`out/index.html` 을 브라우저로 엽니다(더블클릭하거나 브라우저 창으로 끌어다 놓습니다). 파일 하나로 디스크에서 바로 열리며 서버가 필요 없습니다. AWS CloudShell 에서는 먼저 파일을 PC 로 받습니다([한 번에 설치하기](#한-번에-설치하기-deploysh)의 CloudShell 항목).

**잘 됐다면:** 계정마다 「계정 이름 + 가린 ID」 제목의 상자(예: `계정 <계정 이름> (<앞 4자리>…<뒤 4자리>)`)가 있고, 계정마다 수집한 리전 수만큼 리전 상자가 있습니다. 권한이 모두 갖춰져 있으면 화면 위에 `⚠` 배너가 없습니다.

단일 계정(A)인 경우, 모니터링 계정의 네트워크 구성이 모두 그려집니다.

같은 종류의 리소스가 많은 자리(전역 줄의 CloudFront · S3 · WAF 웹 ACL, 리전 줄의 Lambda 등)는 「<종류> ×N」 아이콘 하나로 묶여 보입니다. 아이콘을 누르면 오른쪽 정보창에 목록이 나오고, 목록에서 하나를 누르면 그 리소스의 정보를 봅니다. 다른 리소스와 선으로 이어진 리소스는 묶지 않고 따로 그립니다.

추가로 소스 계정 구성이 필요한 경우(B, C), 모니터링 계정에서 보이는 리소스만 그려졌기 때문에, 4단계에서 읽기 전용 역할을 생성하면 다음 수집부터 소스 계정의 리소스가 추가됩니다.

`⚠ 권한 없어 못 읽은 계정 N` 은 소스 계정에 아직 역할이 없다는 뜻이고, `⚠ 못 그린 리소스 N` 은 다이어그램에 배치할 수 없는 리소스(예: 서브넷이 없는 Auto Scaling 그룹)의 수입니다.

보려던 리전이 없으면 [문제 해결](#문제-해결) 표 「한 리전이 화면에 아예 없음」·「한 리전의 리소스가 비어 있고 배너에 그 리전이 나옴」 행을 봅니다.

**다음 단계:** 소스 계정이 있으면 [소스 계정 추가](#소스-계정-추가)(4-B 또는 4-C)를 진행합니다. 기본 다이어그램만 필요하면 여기서 설치가 끝나며, 여기서 멈춰도 됩니다. 모니터링·사용률을 함께 배포했다면 5단계의 [배포 결과 확인](#배포-결과-확인)을 진행하고, CloudFront 공유를 선택했다면 6단계도 진행합니다.

## 소스 계정 추가

모니터링 계정 말고 다른 계정도 다이어그램에 넣을 때만 합니다. [배포 방식 고르기](#배포-방식-고르기)에 따라 4-B 또는 4-C 를 고릅니다. `deploy.sh` 로 설치했다면 끝에 같은 스택 B 명령이 수집 리전 목록이 채워진 채 출력됐습니다. 모니터링 계정 ID 는 가려져 있으니 아래 `get-caller-identity` 명령으로 전체 값을 확인합니다.

### 4-B. 소스 계정에 직접 스택 B 올리기

소스 계정 관리자에게는 `infra/stack-b-member-role.yaml` 파일 하나와 값 두 개(모니터링 계정 ID, 수집 리전 목록)를 보냅니다. 저장소는 필요 없습니다. 받은 쪽은 CloudFormation 콘솔에서 **스택 생성 → 템플릿 파일 업로드**로 파일을 올리고 두 값을 입력하면 됩니다. 마지막 화면에서 「IAM 리소스를 사용자 지정 이름으로 생성할 수 있음을 승인」에 체크합니다(CLI 의 `CAPABILITY_NAMED_IAM` 에 해당). CLI 로 할 때는 아래 명령에서 `--template-file` 경로만 받은 파일로 바꿉니다.

모니터링 계정 ID 를 확인합니다.

```sh
HUB_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text --profile "$PROFILE" --region "$REGION")
```

소스 계정마다 그 계정의 프로필로 한 번씩 실행합니다. 역할은 계정 전체에 하나이므로 리전 하나에만 올립니다.

```sh
aws cloudformation deploy \
  --template-file infra/stack-b-member-role.yaml \
  --stack-name nettopo-member-role \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
      HubAccountId="$HUB_ACCOUNT_ID" \
      AllowedRegions="$REGIONS" \
  --profile <소스 계정 프로필> --region "$REGION"
```

**잘 됐다면:** 약 30초 걸리고 `Successfully created/updated stack - nettopo-member-role` 로 끝납니다(스택 상태 `CREATE_COMPLETE`).

- `AllowedRegions` 는 2단계의 `REGIONS` 와 같아야 합니다. 다르면 그 계정의 일부 리전을 읽지 못하고, 화면 배너에 「… 리전에 대한 읽기 권한이 없어 캔버스에 빠진 리소스가 있습니다」가 뜹니다.
- 스택 A 를 `nettopo-collector` 가 아닌 이름으로 올렸다면 `HubCollectorRoleNamePattern=<스택명>-BuildRole-*` 와 `HubStateRoleNamePattern=<스택명>-StateRole-*` 를 추가합니다.
- 그 계정에 이미 `NetworkReadOnlyRole` 이 있으면 `RoleName=<다른 이름>` 을 주고, 스택 A 도 `MemberRoleName=<같은 이름>` 으로 갱신합니다. 명령은 아래 [역할 이름이 이미 있을 때](#역할-이름이-이미-있을-때)에 있습니다.

역할을 만든 뒤 모니터링 계정에서 두 가지를 합니다. 소스 계정이 모니터링 계정의 Transit Gateway 에 연결되어 있지 않으면 2단계의 `TargetAccounts` 에 그 계정을 적어 스택 A 를 업데이트하고, 3단계를 다시 실행합니다. `deploy.sh` 라면 `--accounts <소스 계정 ID 목록>`(TGW 가 모르는 계정을 모두 적습니다. 이전 목록을 대체합니다)을 붙여 다시 실행하면 끝에 수집까지 합니다. 이미 발견되는 계정이면 `--collect` 만으로 됩니다. 배너의 `⚠ 권한 없어 못 읽은 계정` 이 사라지고 그 계정의 VPC 와 서브넷이 다이어그램에 나타나면 끝입니다. 역할이 맞게 만들어졌는지 따로 시험할 방법은 없으며, 수집 한 번이 곧 시험입니다. 여전히 못 읽은 계정으로 나오면 [문제 해결](#문제-해결) 표 「스택 B 를 올렸는데 그 계정이 여전히 "권한 없어 못 읽은 계정"」 행을 봅니다.

#### 역할 이름이 이미 있을 때

소스 계정에 이미 `NetworkReadOnlyRole` 이라는 역할이 있으면 스택 B 가 이름 충돌로 실패합니다([문제 해결](#문제-해결) 표 「스택 B 가 `CREATE_FAILED` (이름 충돌)」 행). 그 계정에서 역할 이름을 바꾸고, 스택 A 에도 같은 이름을 줍니다. 두 이름이 같아야 합니다. 실패한 시도로 스택이 `ROLLBACK_COMPLETE` 로 남았다면 먼저 그 스택을 지웁니다([삭제](#삭제)의 스택 B 명령).

소스 계정에서는 4-B 명령에 `RoleName` 을 더합니다.

```sh
aws cloudformation deploy \
  --template-file infra/stack-b-member-role.yaml \
  --stack-name nettopo-member-role \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
      HubAccountId="$HUB_ACCOUNT_ID" \
      AllowedRegions="$REGIONS" \
      RoleName=<다른 이름> \
  --profile <소스 계정 프로필> --region "$REGION"
```

모니터링 계정에서는 스택 A 에 같은 이름을 넣습니다.

**`deploy.sh` 로 설치했다면:** `deploy.sh` 에는 이 옵션이 없으므로 저장소 최상위 폴더에서 아래를 한 번 실행합니다. 적지 않은 파라미터는 현재 값이 유지되고, 이후 `deploy.sh` 를 다시 실행해도 이 값은 유지됩니다(`deploy.sh` 는 자기 파라미터만 넘깁니다). `deploy.sh` 에 `--tags` 를 줬다면 여기에도 같은 `--tags` 를 붙입니다. 빼면 스택 태그가 지워집니다.

```sh
aws cloudformation deploy \
  --template-file infra/stack-a-collector.yaml \
  --stack-name nettopo-collector \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides MemberRoleName=<같은 이름> \
  --profile "$PROFILE" --region "$REGION"
```

**직접 설치했다면:** 2단계 명령의 `--parameter-overrides` 에 `MemberRoleName` 을 더해 다시 실행합니다(`StateLambdaSourceKey` 처럼 적지 않은 파라미터는 값이 유지됩니다. `--tags` 를 썼다면 다시 붙입니다).

```sh
aws cloudformation deploy \
  --template-file infra/stack-a-collector.yaml \
  --stack-name nettopo-collector \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
      SourceLocation="$PKG_BUCKET/nettopo-source.zip" \
      TargetRegions="$REGIONS" \
      MemberRoleName=<같은 이름> \
  --profile "$PROFILE" --region "$REGION"
```

그다음 수집을 한 번 실행하고(3단계 또는 `./deploy.sh --profile "$PROFILE" --region "$REGION" --collect`) 그 계정이 나타나는지 확인합니다.

### 4-C. AWS Organizations 에 StackSet 으로 배포하기

조직 관리자(Organizations 관리 계정 또는 StackSets 위임 관리자)가 수행합니다. 네트워크 관리자로부터 두 값을 받습니다: 모니터링 계정 ID, 수집 리전 목록(`REGIONS`). 템플릿은 `infra/stack-b-member-role.yaml` 파일 하나만 있으면 되고 저장소 전체는 필요 없습니다. **위임 관리자 계정**에서 실행한다면 아래 StackSet 명령(`create-stack-set`·`create-stack-instances`·`list-stack-instances`·`update-stack-set`)마다 `--call-as DELEGATED_ADMIN` 을 붙입니다. 붙이지 않으면 관리 계정 권한으로 해석되어 거절됩니다.

StackSets 가 Organizations 를 쓰도록 한 번 켭니다. 관리 계정에서 실행하며, 이미 켜져 있으면 바로 다음으로 갑니다.

```sh
aws cloudformation activate-organizations-access --profile <관리 계정 프로필> --region ap-northeast-2
aws cloudformation describe-organizations-access --profile <관리 계정 프로필> --region ap-northeast-2
```

`"Status": "ENABLED"` 가 나오면 StackSet 을 만들고 OU 에 배포합니다. `AllowedRegions` 값에 쉼표가 있어 `--parameters` 는 JSON 으로 줍니다. 파일을 전달받았다면 `--template-body` 경로를 그 파일로 바꿉니다. 리전은 모니터링 계정의 스택 A 와 같은 값을 직접 적습니다(아래 예시는 `ap-northeast-2`).

```sh
aws cloudformation create-stack-set --stack-set-name nettopo-member-role \
  --template-body file://infra/stack-b-member-role.yaml \
  --permission-model SERVICE_MANAGED \
  --auto-deployment Enabled=true,RetainStacksOnAccountRemoval=false \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameters '[{"ParameterKey":"HubAccountId","ParameterValue":"<모니터링 계정 ID>"},{"ParameterKey":"AllowedRegions","ParameterValue":"<수집 리전 목록>"}]' \
  --profile <관리 계정 프로필> --region ap-northeast-2

aws cloudformation create-stack-instances --stack-set-name nettopo-member-role \
  --deployment-targets OrganizationalUnitIds=<OU ID> \
  --regions ap-northeast-2 \
  --profile <관리 계정 프로필> --region ap-northeast-2

# 계정마다 CURRENT / SUCCEEDED 가 나올 때까지 반복합니다 (보통 2~5분)
aws cloudformation list-stack-instances --stack-set-name nettopo-member-role \
  --query "Summaries[].[Account,Region,Status,StackInstanceStatus.DetailedStatus]" --output table \
  --profile <관리 계정 프로필> --region ap-northeast-2
```

- 리전은 **하나만** 줍니다. 역할은 계정 전체 리소스이므로 두 리전에 올리면 두 번째가 이름 충돌로 실패합니다.
- 자동 배포를 켰으므로 새 계정이 OU 에 들어오면 역할이 자동으로 만들어집니다.
- StackSet 은 관리 계정 자신에게는 배포하지 않습니다. 관리 계정도 다이어그램에 넣으려면 그 계정에서 4-B 명령으로 스택 B 를 직접 올립니다.
- 콘솔로 할 때는 StackSets 에서 **서비스 관리형 권한**을 고르고, 템플릿·파라미터·OU·리전을 위와 같게 넣습니다.

확인은 4-B 와 같습니다. 모니터링 계정에서 3단계를 다시 실행하고(`deploy.sh` 라면 `--collect`) 다이어그램에 소스 계정의 VPC 가 나타나는지 봅니다. 소스 계정이 모니터링 계정의 Transit Gateway 에 연결되어 있지 않으면 그 계정은 발견되지 않으므로, 2단계의 `TargetAccounts` 에 그 계정을 적어 스택 A 를 업데이트한 뒤 수집합니다.

#### 새 버전의 템플릿으로 StackSet 갱신

이미 만든 StackSet 은 `create-stack-set` 을 다시 실행하면 이름이 겹쳐 실패합니다. 새 템플릿을 받으면 아래 명령으로 갱신합니다. 처음 만들 때 준 파라미터는 `UsePreviousValue=true` 로 그대로 둡니다(`RoleName` 등 다른 파라미터를 줬다면 그것도 같은 모양으로 추가합니다). 갱신은 이미 배포된 모든 계정에 적용됩니다.

```sh
aws cloudformation update-stack-set --stack-set-name nettopo-member-role \
  --template-body file://infra/stack-b-member-role.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameters ParameterKey=HubAccountId,UsePreviousValue=true ParameterKey=AllowedRegions,UsePreviousValue=true \
  --profile <관리 계정 프로필> --region ap-northeast-2
```

진행 상황은 위의 `list-stack-instances` 명령으로 봅니다.

## 선택 기능 (필요할 때만)

기본 다이어그램으로 충분하면 이 부분은 건너뜁니다. 두 기능 모두 기존 설치에 나중에 더할 수 있습니다. `deploy.sh` 로는 5단계 대신 `--monitoring`, 6단계 대신 `--viewer --days N` 을 씁니다([한 번에 설치하기](#한-번에-설치하기-deploysh)). 아래 명령은 직접 설치할 때의 방법입니다.

### 5. 모니터링·사용률 추가 배포 (스택 A 갱신)

이 단계는 **기존 스택 A 에 모니터링·사용률을 추가할 때** 실행합니다. 2단계에서 `StateLambdaSourceKey` 를 넣어 함께 배포했다면 아래 갱신 명령을 다시 실행할 필요 없이 [배포 결과 확인](#배포-결과-확인)으로 이동합니다.

0단계의 `PROFILE`, `REGION`, `PKG_BUCKET` 을 현재 설치 값으로 설정합니다. `PKG_BUCKET` 은 기존 스택 A 의 `SourceLocation` 에 지정된 버킷을 사용합니다. 1단계의 [상태 수집 Lambda 패키지 준비](#상태-수집-lambda-패키지-준비)를 실행해 ZIP 을 만들고 올린 뒤 아래 명령으로 스택 A 를 갱신합니다.

**태그를 사용 중이면 같은 `--tags` 를 아래 갱신 명령에도 추가합니다.** 빼면 기존 태그가 지워집니다. 명시하지 않은 기존 파라미터는 유지됩니다.

```sh
aws cloudformation deploy \
  --template-file infra/stack-a-collector.yaml \
  --stack-name nettopo-collector \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides StateLambdaSourceKey="$STATE_KEY" \
  --profile "$PROFILE" --region "$REGION"
```

#### 배포 결과 확인

기존 스택을 갱신했다면 2단계의 산출물 버킷 조회 블록으로 `ARTIFACT_BUCKET` 을 확인한 뒤, 3단계를 한 번 더 실행해 수집 상태가 `SUCCEEDED` 인지 확인하고 새 `index.html` 을 내려받습니다. 처음부터 기능을 포함해 설치했다면 3단계에서 받은 파일을 엽니다. 화면 상단에 **모니터링**과 **사용률** 조작 항목이 표시되는지 확인합니다.

실시간 모니터링은 다이어그램 위에 리소스의 현재 상태를 표시하는 기능입니다. 화면에서 켜 두는 동안 기본 5분마다 상태를 다시 읽어 최신 값으로 바꿉니다.

**모니터링** 버튼을 누르면 두 가지 중 하나를 선택합니다. **구조 조회만**은 리소스 상태(정상/이상)를 표시합니다. **지표까지**는 CloudWatch 지표를 읽어 트래픽 흐름을 선 위에 함께 표시합니다.

선택하면 즉시 첫 조회가 실행되고 이후 5분마다 갱신됩니다. 켠 뒤 최대 4시간이 지나거나 브라우저 창을 닫고 약 20분이 지나면 자동으로 멈춥니다.

**사용률**은 리소스 사용량과 한도 대비 사용 비율을 표시합니다. 화면에서 켜서 값이 갱신되는지 확인합니다.

내려받은 `index.html` 로 열었을 때는 파일을 받은 시점부터 최대 약 12시간 동안만 실시간 모니터링이 동작합니다(서명에 쓴 임시 자격 증명의 남은 수명에 따라 더 짧을 수 있습니다). 멈추면 파일을 다시 내려받습니다. 6단계의 공유 링크로 열면 열 때마다 새 서명을 받으므로 이 제한이 없습니다.

CloudFront 공유도 선택했다면 다음 6단계로 진행합니다.

### 6. CloudFront로 공유하기 (스택 C 배포·링크 발급)

CloudFront 공유를 선택한 경우 실행합니다. **스택 A 와 별도로 스택 C 를 배포하고 서명 링크를 발급**합니다. 모니터링·사용률을 배포하지 않아도 이 단계를 사용할 수 있으며, 스택 A 에 추가 파라미터를 설정할 필요는 없습니다. AWS 계정이 없는 사람도 발급된 링크로 다이어그램을 볼 수 있습니다.

스택 A 배포와 최초 수집이 끝난 뒤 실행합니다. 기존 설치에 추가하는 경우에는 0단계의 `PROFILE`, `REGION` 을 현재 설치 값으로 설정하고, 2단계의 산출물 버킷 조회 블록으로 `ARTIFACT_BUCKET` 을 확인합니다.

```sh
# 서명 키 쌍. 비밀키는 관리자 PC 에만 두고 AWS 에 올리지 않습니다
mkdir -p out && openssl genrsa -out out/viewer-k1.pem 2048
openssl rsa -in out/viewer-k1.pem -pubout -out out/viewer-k1.pub.pem

# 스택 A 와 같은 리전에 올립니다
aws cloudformation deploy \
  --template-file infra/stack-c-viewer.yaml \
  --stack-name nettopo-viewer \
  --parameter-overrides \
      ArtifactBucketName="$ARTIFACT_BUCKET" \
      ViewerPublicKeyPem="$(cat out/viewer-k1.pub.pem)" \
  --profile "$PROFILE" --region "$REGION"

# 보는 링크. 수명(일)은 직접 정합니다. stdout 한 줄이 링크입니다
infra/issue-viewer-link.sh --stack nettopo-viewer --key out/viewer-k1.pem --days 30 \
  --profile "$PROFILE" --region "$REGION"
```

**잘 됐다면:** 스택 C 배포는 수 분(시험에서는 약 3분 30초, 대부분 CloudFront 배포 생성) 걸리고 `Successfully created/updated stack - nettopo-viewer` 로 끝납니다. `issue-viewer-link.sh` 는 stdout 에 링크 한 줄(`https://<도메인>/index.html?Expires=…&Signature=…&Key-Pair-Id=…&Hash-Algorithm=SHA256`)을, stderr 에 만료 안내 한 줄(`만료: <시각> · 이 링크를 가진 사람은 누구나 본다 · 전부 무효화 = 키 교체(docs/install-guide.md 6단계)`)을 출력합니다. 키 교체는 이 단계 끝에 있습니다. 발급이 실패하면 [문제 해결](#문제-해결) 표 「6단계 링크 발급 실패」 행을 봅니다.

**완료 확인:** 발급된 링크를 브라우저에서 열어 최신 다이어그램이 표시되는지 확인합니다. 링크 자체가 403 이면 [문제 해결](#문제-해결) 표 「보는 링크가 403」 행을 봅니다.

- 링크를 가진 사람은 누구나 볼 수 있습니다. 로그인이 없습니다.
- 링크 없이, 만료된 링크로, 또는 `index.html` 이 아닌 경로로 열면 403 입니다.

**키 교체 (링크 무효화).** 링크 하나만 따로 끊을 수는 없습니다. 서명 키를 교체하면 그 키로 서명한 링크가 모두 무효가 됩니다.

- 링크가 유출되면 새 키 쌍을 만들고 `ViewerPublicKeyPem` 과 `KeyLabel`(`k1` ↔ `k2`)을 **함께** 바꿔 스택을 갱신합니다. 갱신이 끝나면 옛 링크가 모두 403 이 됩니다. 공개키만 바꾸면 갱신이 실패하고 롤백됩니다. `deploy.sh` 로는 `--viewer --rotate-key --no-overlap --days 30` 입니다.
- 유출이 아닌 계획된 키 교체는 옛 링크를 살린 채 바꿀 수 있습니다. 새 공개키와 `KeyLabel` 을 바꾸면서 갱신 전의 공개키를 `PreviousPublicKeyPem` 에 그대로 넣으면 옛 링크가 만료될 때까지 열립니다. 옛 링크가 모두 만료된 뒤 `PreviousPublicKeyPem` 을 비워 다시 갱신합니다. `deploy.sh` 로는 `--viewer --rotate-key` → `--viewer --retire-old-key` 입니다([한 번에 설치하기](#한-번에-설치하기-deploysh)).

## 새 버전으로 올리기

`deploy.sh` 를 쓰면 clone 한 저장소에서 `git pull` 로 새 버전을 받은 뒤 처음 설치한 명령을 그대로 다시 실행합니다(`--bucket`·`--regions` 는 생략 가능, 모니터링을 쓰면 `--monitoring`).

```sh
git pull
./deploy.sh --profile "$PROFILE" --region "$REGION"     # --monitoring · --viewer --days N 등은 설치 때처럼 붙입니다
```

**잘 됐다면:** 처음 설치 때처럼 `🔴 … 실패` 줄 없이 끝나고, `out/index.html` 이 새 다이어그램으로 바뀝니다.

직접 실행할 때는 다음 순서입니다.

1. 1단계의 zip 을 새로 만들어 같은 키로 덮어 올립니다(버킷 생성 두 명령은 건너뜁니다).
2. 2단계 `deploy` 명령을 그대로 다시 실행합니다. 적지 않은 파라미터는 이전 값이 유지됩니다. `--tags` 를 썼다면 다시 붙입니다. 템플릿이 바뀌지 않았으면 `No changes to deploy` 로 끝나며 정상입니다.
3. 3단계를 실행합니다.

모니터링·사용률을 쓰면 1단계의 [상태 수집 Lambda 패키지 준비](#상태-수집-lambda-패키지-준비)를 다시 실행하고, 5단계의 갱신 명령으로 `StateLambdaSourceKey` 를 새 키로 바꿉니다. 키 이름에 내용 해시가 들어 있어 같은 이름이면 코드가 갱신되지 않습니다.

**이번 버전은 스택 B 갱신이 필요합니다(WAF 읽기 권한 2개 — `wafv2:ListWebACLs` · `wafv2:ListResourcesForWebACL`).** 갱신하지 않은 계정은 WAF 가 「확인하지 못했습니다」로 보입니다. 4-C 로 배포했다면 [StackSet 갱신](#새-버전의-템플릿으로-stackset-갱신) 명령을 씁니다. 모니터링 계정의 스택 A 는 위 갱신(`deploy.sh` 재실행 또는 2단계)으로 함께 바뀝니다.

**계정 이름도 스택 B 갱신이 필요합니다(`account:GetAccountInformation` 1개 — 그 계정 자신의 이름만 읽습니다).** 다이어그램의 계정 상자 제목이 「계정 이름 (가린 계정 id)」로 바뀝니다. 기존 설치는 **모든 소스 계정**의 스택 B 를 갱신해야 그 계정의 이름이 보입니다(4-C 라면 [StackSet 갱신](#새-버전의-템플릿으로-stackset-갱신) 한 번). 갱신 전까지 그 계정은 지금처럼 계정 id 로만 보이고, 네트워크 그림은 그대로이며 「확인하지 못했습니다」로 바뀌지 않습니다. 모니터링 계정 자신의 이름은 위 스택 A 갱신으로 보입니다.

새 버전이 새 리소스 종류를 읽게 되었다면 소스 계정의 스택 B 도 다시 올립니다. 4-B 는 같은 `deploy` 명령을 새 템플릿으로 다시 실행하고, 4-C 는 [StackSet 갱신](#새-버전의-템플릿으로-stackset-갱신) 명령을 씁니다. 읽기 권한 목록이 스택 B 에 있어서, 올리지 않으면 그 계정에서만 해당 종류를 읽지 못하고 배너가 어느 계정·리전의 무엇이 빠졌는지 알려 줍니다.

## 삭제

`deploy.sh` 로 설치했다면 `./deploy.sh --profile "$PROFILE" --region "$REGION" --destroy` 가 아래 모니터링 계정 쪽 삭제(스택 C → 스택 A → 버킷 3개의 모든 버전)를 한 번에 합니다. 소스 계정의 스택 B 는 아래 2단계대로 따로 지웁니다.

아래 순서로 지웁니다. 스택 A 의 아티팩트 버킷과 스택 C 의 로그 버킷은 스택을 지워도 남도록(`DeletionPolicy: Retain`) 되어 있어 직접 비우고 지웁니다. 스택을 지우면 출력값도 사라지므로 버킷 이름을 먼저 확보합니다. 아티팩트 버킷과 `deploy.sh` 가 만든 패키지 버킷은 버전 관리가 켜져 있어, 이전 객체 버전과 삭제 마커까지 모두 지워야 버킷을 지울 수 있습니다. `aws s3 rb --force` 는 이전 버전을 지우지 않습니다. 아래 함수가 세 버킷 모두에 대해 1,000개씩 비워질 때까지 반복합니다.

```sh
empty_and_delete_bucket() {   # every object version and delete marker, then the bucket
  local batch out
  while :; do
    batch=$(aws s3api list-object-versions --bucket "$1" --max-items 1000 \
      --query '{Objects: [(Versions || `[]`)[].{Key: Key, VersionId: VersionId}, (DeleteMarkers || `[]`)[].{Key: Key, VersionId: VersionId}][], Quiet: `true`}' \
      --output json --profile "$PROFILE" --region "$REGION") || return 1
    printf '%s' "$batch" | grep -q '"Key"' || break
    out=$(aws s3api delete-objects --bucket "$1" --delete "$batch" --profile "$PROFILE" --region "$REGION") || return 1
    if printf '%s' "$out" | grep -q '"Errors"'; then printf '%s\n' "$out"; return 1; fi
  done
  aws s3api delete-bucket --bucket "$1" --profile "$PROFILE" --region "$REGION"
}

# 버킷 이름을 먼저 받아 둔다 — 스택을 지우면 출력값도 사라진다
LOG_BUCKET=$(aws cloudformation describe-stacks --stack-name nettopo-viewer \
  --query "Stacks[0].Outputs[?OutputKey=='AccessLogBucketName'].OutputValue" --output text \
  --profile "$PROFILE" --region "$REGION")
ARTIFACT_BUCKET=$(aws cloudformation describe-stacks --stack-name nettopo-collector \
  --query "Stacks[0].Outputs[?OutputKey=='ArtifactBucketName'].OutputValue" --output text \
  --profile "$PROFILE" --region "$REGION")
PKG_BUCKET=$(aws cloudformation describe-stacks --stack-name nettopo-collector \
  --query "Stacks[0].Parameters[?ParameterKey=='SourceLocation'].ParameterValue" --output text \
  --profile "$PROFILE" --region "$REGION" | cut -d/ -f1)

# 1. 스택 C(배포한 경우) → 그 스택이 남기는 접근 로그 버킷
aws cloudformation delete-stack --stack-name nettopo-viewer --profile "$PROFILE" --region "$REGION"
aws cloudformation wait stack-delete-complete --stack-name nettopo-viewer --profile "$PROFILE" --region "$REGION"
[ -z "$LOG_BUCKET" ] || [ "$LOG_BUCKET" = None ] || empty_and_delete_bucket "$LOG_BUCKET"

# 2. 각 소스 계정의 스택 B(직접 배포한 경우)
aws cloudformation delete-stack --stack-name nettopo-member-role \
  --profile <source-account-profile> --region <stack-b-region>
#    Organizations StackSet 이면: 인스턴스 먼저, 그다음 StackSet(위임 관리자 계정이면 --call-as DELEGATED_ADMIN 추가)
aws cloudformation delete-stack-instances --stack-set-name nettopo-member-role \
  --deployment-targets OrganizationalUnitIds=<OU ID> --regions <stackset-region> --no-retain-stacks \
  --profile <management-account-profile> --region <stackset-region>
aws cloudformation list-stack-set-operations --stack-set-name nettopo-member-role --max-results 1 \
  --query "Summaries[0].Status" --output text \
  --profile <management-account-profile> --region <stackset-region>      # SUCCEEDED 가 될 때까지 반복
aws cloudformation delete-stack-set --stack-set-name nettopo-member-role \
  --profile <management-account-profile> --region <stackset-region>

# 3. 스택 A
aws cloudformation delete-stack --stack-name nettopo-collector --profile "$PROFILE" --region "$REGION"
aws cloudformation wait stack-delete-complete --stack-name nettopo-collector --profile "$PROFILE" --region "$REGION"

# 4. 산출물 버킷(스택 A 가 남긴다 · 버전 관리가 켜져 있어 모든 버전을 지운다)
empty_and_delete_bucket "$ARTIFACT_BUCKET"

# 5. 배포 패키지 버킷(스택이 만든 것이 아니다)
empty_and_delete_bucket "$PKG_BUCKET"
```

스택 B 는 리전을 틀려도 없는 스택을 지우는 명령이 성공으로 끝나 역할이 그대로 남으니, 삭제 뒤 CloudFormation 콘솔에서 스택이 사라졌는지 확인합니다. CloudWatch Logs 그룹은 스택에 포함되어 있어 함께 지워집니다. 내 컴퓨터의 `out/`(다이어그램·템플릿·서명 비밀키 `viewer-k*.pem`)와 링크 발급 기록 `~/.local/state/nettopo/viewer-links.log` 는 지워지지 않습니다.

**잘 됐다면:** `deploy.sh --destroy` 는 `삭제가 끝났습니다.` 와 위 로컬 파일 안내로 끝납니다. 직접 지울 때는 `wait stack-delete-complete` 가 아무것도 출력하지 않고 돌아옵니다. 시험에서 이 순서대로 지워 스택 C, 그 로그 버킷, 스택 B, 스택 A, 산출물 버킷, 패키지 버킷이 모두 지워지고 남은 것이 없었습니다. 지워지지 않는 것이 있으면 [문제 해결](#문제-해결) 표 「삭제 실패」 행을 봅니다.

## 문제 해결

| 증상 | 원인 | 조치 |
|---|---|---|
| 한 리전이 화면에 아예 없음 | 그 리전을 `TargetRegions` 에 적지 않아 수집 자체를 하지 않음 | 2단계 `TargetRegions` 에 추가해 스택 A 를 갱신하고 다시 수집 |
| 한 리전의 리소스가 비어 있고 배너에 그 리전이 나옴 | 그 리전이 소스 계정 스택 B 의 `AllowedRegions` 에 없음 | 두 스택에 같은 리전 목록을 주고 갱신 |
| 스택 B 를 올렸는데 그 계정이 여전히 "권한 없어 못 읽은 계정" | 역할 이름, 모니터링 계정 ID, 스택 A 이름 중 하나가 어긋남 | 4-B 의 세 항목을 대조. 오류 메시지로는 구별되지 않음 |
| 스택 B 가 `CREATE_FAILED` (이름 충돌) | 같은 이름의 역할이 이미 있음 | `RoleName` 과 스택 A `MemberRoleName` 을 함께 바꿈([명령](#역할-이름이-이미-있을-때)) |
| 새 버전 뒤 모니터링 계정에는 나오는 리소스가 다른 계정에서만 배너에 「권한이 없어 읽지 못했습니다」로 뜸 | 그 계정의 스택 B 가 옛 버전 | 4단계 명령으로 스택 B 갱신 |
| 빌드 `FAILED`, 로그에 계정 발견 실패 | 모니터링 계정에서 TGW 연결을 읽지 못함 | 발견에 기댈 수 없으므로 `TargetAccounts` 에 보려는 소스 계정을 적어 스택 A 갱신 |
| `build-state-lambda.sh` 가 "pip 이 없다"로 멈춤 | python3 에 pip 없음 | `python3 -m ensurepip --user` 뒤 재실행, 또는 `PY=<pip 있는 python> ./infra/build-state-lambda.sh` |
| deploy.sh 가 0단계 준비물 확인에서 멈춤 | `aws` 가 없거나 AWS CLI v1 · `--viewer` 인데 `openssl` 이 없음 | AWS CLI v2 를 설치(또는 CloudShell 에서 실행) · openssl 설치 |
| deploy.sh 가 0단계 자격 증명 확인에서 멈춤 | 프로필이 `~/.aws/config` 에 없음 · IAM Identity Center 세션 만료 · CloudFormation 조회 권한 없음 | `aws configure sso --profile <이름>` 또는 `aws sso login --profile <이름>` 뒤 다시 실행. CloudShell 이면 `--profile` 을 빼고, 콘솔 로그인이 만료됐으면 다시 로그인한 뒤 CloudShell 을 새로 엶 |
| 스택이 진행 중 또는 ROLLBACK_COMPLETE | 다른 배포·삭제가 진행 중 · 처음 생성이 실패해 `ROLLBACK_COMPLETE` 로 남음 | 진행 중이면 끝난 뒤 다시 실행. `ROLLBACK_COMPLETE` 는 갱신할 수 없으니 출력된 이유를 고치고 스택을 지운 뒤(`--destroy` 또는 콘솔) 다시 설치 |
| 1단계 버킷 확인 실패 | 그 이름을 다른 계정이 씀 · 버킷이 스택과 다른 리전 · 기존 버킷의 공개 차단이 꺼져 있음 | 다른 `--bucket` · 스택과 같은 리전의 버킷 · 공개 차단 4항목을 켜고 다시 실행 |
| 1단계 버킷 만들기 실패 | 이름이 전 세계에서 이미 쓰임 · 버킷 생성 권한 없음 | 다른 `--bucket` · 권한 확인 |
| 1단계 배포 패키지 실패 | git clone 이 아님 · `--package` 파일이 없거나 zip 이 아님 | git clone 한 저장소 루트에서 실행 · `--package` 를 줬다면 경로 확인 |
| 2단계 스택 A 배포 실패 | 출력된 CloudFormation 이유(권한·파라미터 검증 등) | 이유를 고치고 같은 명령을 다시 실행. 처음 생성이었다면 위 `ROLLBACK_COMPLETE` 행 |
| 3단계 수집 빌드 실패 | 수집 빌드가 `FAILED`·`TIMED_OUT` | 출력된 `aws logs tail …` 명령으로 로그 확인. 계정 발견 실패면 아래 「빌드 `FAILED`, 로그에 계정 발견 실패」 행 |
| 3단계 페이지 게시 대기 시간 초과 | 모니터링을 켠 설치에서 서명 함수가 페이지를 아직 게시하지 않음 | 몇 분 뒤 `--download` 로 다시 받음. 계속되면 출력된 서명 함수 로그 확인 |
| 6단계 서명 키 확인 실패 | 스택 C 가 이미 있는데 비밀키 파일이 없음 · 다른 키를 줌 · 이미 키 교체 중 | 원래 비밀키를 `--viewer-key` 로 줌. 잃었으면 `--viewer --rotate-key --no-overlap --days N` 으로 교체 |
| 6단계 스택 C 배포 실패 | 출력된 CloudFormation 이유 | 이유를 고치고 다시 실행 |
| 6단계 링크 발급 실패 | `infra/issue-viewer-link.sh` 실패(스택 C 출력 없음 · openssl) | 스택 C 상태를 확인하고 다시 실행 |
| 삭제 실패 | 스택 삭제 실패(출력된 이유) · 버킷 객체를 지우지 못함 | 이유를 고치고 `--destroy` 를 다시 실행(이미 지운 것은 건너뜀) |
| WAF 웹 ACL 이 화면에 없거나 배너에 「확인하지 못했습니다」로 나옴 | 그 계정의 스택 B 가 이번 버전보다 옛 버전이라 WAF 읽기 권한(`wafv2:ListWebACLs` · `wafv2:ListResourcesForWebACL`)이 없음 | 최신 템플릿으로 스택 B 갱신(4-B 재실행 또는 [StackSet 갱신](#새-버전의-템플릿으로-stackset-갱신)) 뒤 수집 한 번 |
| 보는 링크가 403 | 만료, 다른 경로, 키 교체 뒤 옛 링크 | 새로 발급 |
| 다이어그램은 뜨는데 실시간 숫자가 멈춤 | 화면이 상태를 읽지 못함 | 링크를 다시 열기. 5단계 갱신 여부 확인 |
| Bedrock 을 쓰는데 화면에 「Bedrock Endpoint」가 없음 | VPC 인터페이스 엔드포인트가 없으면(NAT·인터넷으로 Bedrock 을 부르면) 이 도구는 그 호출을 볼 수 없음 — 엔드포인트가 있어야 표시됨 | 정상. 엔드포인트를 만들면 다음 수집부터 표시됨 |
| 소스 계정 하나가 VPC·라우팅 테이블 정도만 보이고, 나머지는 배너에 「권한이 없어 읽지 못했습니다」로 몰려 나옴 | 그 계정의 스택 B 가 옛 버전이라 읽기 권한이 모자람. 실측에서는 시도한 36종 가운데 8종만 통과했습니다 | 최신 `infra/stack-b-member-role.yaml` 로 스택 B 를 다시 올리고 수집을 한 번 실행 |

## 파라미터

자주 쓰는 파라미터만 적었습니다. 전체 목록은 각 템플릿의 `Parameters` 절에 있습니다.

### 스택 A `infra/stack-a-collector.yaml`

| 파라미터 | 설명 | 기본값 | 필수 |
|---|---|---|---|
| `SourceLocation` | 배포 패키지 zip 의 S3 위치 (`버킷/키`, `s3://` 없이) | 없음 | 예 |
| `TargetRegions` | 수집할 리전 (쉼표 구분). 비우면 스택 리전 하나만 | 빈 값 | 사실상 예 |
| `TargetAccounts` | 추가할 소스 계정 ID (쉼표 구분). 비워도 TGW 연결로 자동 발견하고, 적으면 발견 결과에 더함. TGW 가 모르는 계정만 적으면 됨 | 빈 값 | 아니요 |
| `Schedule` | 수집 주기 (EventBridge 표현식) | `rate(1 hour)` | 아니요 |
| `ScheduleState` | 스케줄 켬/끔 | `ENABLED` | 아니요 |
| `MemberRoleName` | 소스 계정에서 assume 할 역할 이름. 스택 B 의 `RoleName` 과 같아야 함 | `NetworkReadOnlyRole` | 아니요 |
| `MemberExternalId` | 선택. 스택 B `ExternalId` 와 같은 값 — 소스 계정에 들어갈 때 넘긴다([선택 보강](#선택-보강)) | 빈 값 | 아니요 |
| `ViewerOrigin` | 선택. 스택 C 주소 `https://` + 출력값 `ViewerDomain` — 버킷 CORS 를 그 주소 하나로 좁힌다([선택 보강](#선택-보강)) | 빈 값 | 아니요 |
| `ViewerDistributionId` | 선택. 스택 C 출력값 `DistributionId` — 그 배포만 버킷의 `index.html` 을 읽을 수 있다 | 빈 값 | 아니요 |
| `StateLambdaSourceKey` | 모니터링·사용률 Lambda zip 의 S3 키. 비우면 두 기능의 리소스를 만들지 않음 | 빈 값 | 아니요 |
| `FlowLogReading` | VPC 흐름 로그 판독 (`ENABLED`/`DISABLED`). `StateLambdaSourceKey` 를 설정한 설치에서만 동작 | `DISABLED` | 아니요 |
| `FlowLogGroupNamePrefixes` | 읽을 CloudWatch Logs 흐름 로그 그룹 이름 (전체 이름, 쉼표 구분) | 빈 값 | 아니요 |
| `LogRetentionDays` | CodeBuild 로그 보존 일수 | `30` | 아니요 |

### 스택 B `infra/stack-b-member-role.yaml`

| 파라미터 | 설명 | 기본값 | 필수 |
|---|---|---|---|
| `HubAccountId` | 모니터링 계정 ID (12자리) | 없음 | 예 |
| `AllowedRegions` | 모니터링 계정이 이 계정에서 읽을 수 있는 리전. 스택 A 의 `TargetRegions` 와 같게 | `us-east-1,us-west-2,ap-northeast-2` | 사실상 예 |
| `RoleName` | 만들 역할 이름. 스택 A 의 `MemberRoleName` 과 같아야 함 | `NetworkReadOnlyRole` | 아니요 |
| `HubCollectorRoleNamePattern` | 스택 A 수집 역할의 이름 패턴. 스택 A 이름을 바꿨을 때만 | `nettopo-collector-BuildRole-*` | 아니요 |
| `HubStateRoleNamePattern` | 스택 A 실시간 모니터링 역할의 이름 패턴. 스택 A 이름을 바꿨을 때만 | `nettopo-collector-StateRole-*` | 아니요 |
| `HubPermissionSetName` | 모니터링 계정에서 사람이 로컬 CLI 로 수집할 때 쓰는 IAM Identity Center 권한 세트 이름 | 빈 값 | 아니요 |
| `ExternalId` | 선택. 주면 이 외부 ID 를 넘겨야만 역할에 들어올 수 있다([선택 보강](#선택-보강)) | 빈 값 | 아니요 |
| `PrincipalOrgId` | 선택. 주면(`o-...`) 그 AWS Organizations 조직의 주체만 역할에 들어올 수 있다 | 빈 값 | 아니요 |
| `FlowLogReading` 외 3개 | 스택 A 와 같은 이름의 흐름 로그 파라미터 4개 | 꺼짐/빈 값 | 아니요 |

### 스택 C `infra/stack-c-viewer.yaml`

| 파라미터 | 설명 | 기본값 | 필수 |
|---|---|---|---|
| `ArtifactBucketName` | 스택 A 출력 `ArtifactBucketName` | 없음 | 예 |
| `ViewerPublicKeyPem` | 링크 서명용 RSA 2048 공개키 PEM 전체 | 없음 | 예 |
| `KeyLabel` | 키 자리 (`k1` 또는 `k2`). 키 교체 때 공개키와 함께 바꿈 | `k1` | 아니요 |
| `PreviousPublicKeyPem` | 계획된 키 교체 중에만. 갱신 전의 `ViewerPublicKeyPem` 값을 그대로 넣으면 옛 키로 서명한 링크가 만료까지 열림. 옛 링크가 만료되면 비움 | 빈 값 | 아니요 |
| `AccessLogRetentionDays` | CloudFront 액세스 로그 보존 일수 | `90` | 아니요 |
| `LogReaderPrincipalArns` | 액세스 로그를 읽을 이 계정의 IAM 역할·사용자 ARN (쉼표 구분, 공백·와일드카드 불가). 로그에 서명 링크 전체가 남아 읽는 사람은 링크를 다시 쓸 수 있음. IAM Identity Center 는 세션 ARN 이 아니라 경로가 붙은 역할 ARN. 비우면 계정 루트만 읽음 | 빈 값 | 아니요 |

## 보안

- 수집기는 대상 계정에 `Describe*`, `Get*`, `List*`, `Search*` 만 호출하고 아무것도 바꾸지 않습니다. 유일한 예외는 소스 계정으로 들어가기 위한 `sts:AssumeRole` 이며, 전환한 뒤의 세션에도 읽기 전용 제한이 그대로 걸려 있습니다(런타임에서 강제합니다).
- 소스 계정에 부여되는 권한은 `infra/stack-b-member-role.yaml` 의 정책 그대로이며 읽기 액션입니다(흐름 로그를 KMS 고객 키로 암호화한 경우의 S3 경유 복호화만 예외). 모니터링 계정에서 수집기가 갖는 권한은 `infra/stack-a-collector.yaml` 의 역할 정책에 있습니다. 액션마다 왜 필요한지, 리소스 범위, 거부되면 화면에 무엇이 보이는지는 [필요한 IAM 권한](required-permissions.md)에 있습니다.
- 배포 패키지와 결과는 여러분 계정의 비공개 버킷에만 있습니다. 공개 URL 이 없습니다.
- 생성된 HTML 은 외부로 나가는 요청이 없습니다. 실시간 모니터링을 켠 경우에만 같은 계정의 S3 를 서명된 URL 로 읽고 씁니다.
- 실시간 모니터링을 켠 설치의 HTML 안에는 서명 URL 세 개가 들어 있습니다: 상태 읽기, 신호 쓰기, 최신 화면 열기(`index.html` 읽기). 최대 약 12시간 유효하고 각각 S3 키 하나에만 허용됩니다. HTML 을 비공개로 다루고, 유출되면 만료 전까지 그 세 동작만 가능합니다.
- 스택 C 의 보는 링크는 가진 사람이면 누구나 열 수 있습니다. 비밀번호처럼 다루고, 유출 시 키를 교체합니다. CloudFront 액세스 로그에는 링크 전체가 남습니다.
- 공유 경로(내려받기 · S3 서명 URL · 스택 C · Identity Center · 사내망 전용)별로 누가 열 수 있는지, 열람 기록, 회수 방법, 수명, 링크가 남는 곳과 스택 C 비밀키 관리는 [화면 공유 경로 — 보안 검토자용 안내](viewer-sharing.md)에 있습니다.

### 선택 보강

넷 다 기본은 꺼짐이고, 비워 두면 위에 적은 동작 그대로입니다.

- **외부 ID (스택 B `ExternalId` + 스택 A `MemberExternalId`).** 멤버 역할은 원래 모니터링 계정의 수집기 역할 이름만 신뢰합니다. 조직 정책상 외부 ID 까지 요구한다면 두 스택에 같은 값을 넣으세요. 수집기와 실시간 모니터링 함수가 역할에 들어갈 때 그 값을 넘깁니다. 로컬 CLI 로 직접 수집하는 사람은 `--external-id` 로 넘깁니다.
- **조직 (스택 B `PrincipalOrgId`).** 그 AWS Organizations 조직의 주체만 역할에 들어올 수 있습니다. 모니터링 계정도 같은 조직에 있어야 합니다.
- **뷰어 배포 (스택 A `ViewerDistributionId`).** 버킷 정책은 CloudFront 에 `index.html` 읽기만 열지만, 스택 A 를 만들 때는 스택 C 배포 ID 를 모르므로 기본은 모니터링 계정의 아무 배포나 읽을 수 있습니다. 스택 C 를 올린 뒤 넣으면 그 배포만 화면을 내보낼 수 있습니다.
- **버킷 CORS (스택 A `ViewerOrigin`).** 스택 A 를 만들 때는 스택 C 주소를 모르므로, 기본 CORS 는 파일로 연 화면(`null`)과 아무 CloudFront 도메인을 받습니다. CORS 자체는 권한을 주지 않지만(모든 요청은 여전히 서명 URL 이 필요합니다), 스택 C 를 올린 뒤 그 배포 주소 하나로 좁힐 수 있습니다.

`deploy.sh` 로 설치했다면 이 넷을 명령 한 번으로 합니다([한 번에 설치하기](#한-번에-설치하기-deploysh)). 스택 B 에만 외부 ID 를 넣으면 수집이 거부되므로 모니터링 계정을 먼저 갱신하고, 그다음 화면에 출력된 스택 B 명령을 소스 계정·조직 관리자에게 넘깁니다.

```sh
# 모니터링 계정: 외부 ID 를 스택 A 에 넣고, 스택 C 를 올린 뒤 그 배포로 CORS·CloudFront 읽기를 좁힙니다.
# 끝에 출력되는 스택 B 명령(CLI 한 줄 · StackSet)에는 ExternalId 와 PrincipalOrgId 가 들어 있습니다.
./deploy.sh --profile "$PROFILE" --region "$REGION" \
  --external-id <외부 ID> --org-id <o-...> --viewer --days 30 --narrow-viewer
```

직접 실행할 때는 다음과 같습니다.

```sh
# 소스 계정: 외부 ID 와 조직 조건을 넣은 스택 B
aws cloudformation deploy --template-file infra/stack-b-member-role.yaml --stack-name nettopo-member-role \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides HubAccountId=<모니터링 계정 ID> ExternalId=<외부 ID> PrincipalOrgId=<o-...> \
  --profile <소스 계정 프로필> --region "$REGION"

# 모니터링 계정: 같은 외부 ID, 그리고 CORS 를 좁힐 스택 C 주소
VIEWER_DOMAIN=$(aws cloudformation describe-stacks --stack-name nettopo-viewer \
  --query "Stacks[0].Outputs[?OutputKey=='ViewerDomain'].OutputValue" --output text \
  --profile "$PROFILE" --region "$REGION")
VIEWER_ID=$(aws cloudformation describe-stacks --stack-name nettopo-viewer \
  --query "Stacks[0].Outputs[?OutputKey=='DistributionId'].OutputValue" --output text \
  --profile "$PROFILE" --region "$REGION")
aws cloudformation deploy --template-file infra/stack-a-collector.yaml --stack-name nettopo-collector \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides MemberExternalId=<외부 ID> ViewerOrigin="https://$VIEWER_DOMAIN" ViewerDistributionId="$VIEWER_ID" \
  --profile "$PROFILE" --region "$REGION"
```
