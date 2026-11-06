# Deployment Guide

This document is the procedure for deploying NetworkTopology into your AWS accounts. What the tool does, the architecture, and costs are in the [README](../README.md). The diagram's on-screen text is in Korean; where this guide quotes a banner or button, it gives the Korean text with its meaning.

Terms: the **monitoring account** is where the collector (Stack A) is deployed; **source accounts** are the accounts whose resources appear in the diagram (Stack B). The collector assumes an IAM role in each source account, calls read-only APIs only, and stores results only in the monitoring account's S3 bucket.

- **Stack A** (`nettopo-collector`): the collector in the monitoring account. Always required.
- **Stack B** (`nettopo-member-role`): one read-only IAM role in each source account. Only if you have source accounts.
- **Stack C** (`nettopo-viewer`): optional CloudFront viewer that gives people without an AWS account a signed viewing link.

## Table of contents

- [What you get](#what-you-get)
- [Deployment options](#deployment-options)
- [Prerequisites](#prerequisites)
- [Get the code](#get-the-code)
- [Choose a deployment option](#choose-a-deployment-option)
- [0. Set your values](#0-set-your-values)
- [One-command install (deploy.sh)](#one-command-install-deploysh) (recommended; you can stop after this and [Open the diagram](#open-the-diagram))
- [Manual install (step by step)](#manual-install-step-by-step)
- [Open the diagram](#open-the-diagram)
- [Add source accounts](#add-source-accounts)
- [Optional features (only if you need them)](#optional-features-only-if-you-need-them)
  - [Add monitoring and utilization (update Stack A)](#5-add-monitoring-and-utilization-update-stack-a)
  - [Share through CloudFront (deploy Stack C and issue a link)](#6-share-through-cloudfront-deploy-stack-c-and-issue-a-link)
- [Updating](#updating)
- [Cleanup](#cleanup)
- [Troubleshooting](#troubleshooting)
- [Parameters](#parameters)
- [Security](#security)

## What you get

| | |
|---|---|
| **What gets created** | In the monitoring account, Stack A: a CodeBuild project (the collector), an EventBridge schedule that runs it every hour, a private S3 artifact bucket that holds the diagram, and their IAM roles and CloudWatch Logs group. You also create one private S3 bucket for the deployment package. Optional: Stack B, one read-only IAM role (`NetworkReadOnlyRole`) in each source account; Stack C, a CloudFront distribution and its access-log bucket in the monitoring account; monitoring and utilization add two Lambda functions and an SQS dead-letter queue to Stack A |
| **What you end up with** | `out/index.html` on your computer: a single HTML file with the diagram. The collector refreshes it in S3 every hour |
| **How long it takes** | Stack A: a few minutes (about 2 minutes in our test). First collection: usually 2 to 5 minutes (1 min 47 s in our test with 2 accounts and 3 Regions). Stack B: about 30 seconds per account. Stack C: a few minutes (about 3.5 minutes in our test, mostly CloudFront) |
| **Cost** | About $7 to $18 a month at the template defaults, almost all of it CodeBuild run time (once an hour, usually 2 to 5 minutes per build). Live monitoring, CloudFront, S3 history, and logs add usage-based costs. Details: [Estimated Costs](../README.md#estimated-costs) in the README |
| **Permissions you need** | Monitoring account: create IAM roles, S3 buckets, CodeBuild, EventBridge, Lambda, CloudWatch Logs, SQS, and CloudFront. Each source account: create one IAM role. Action-level list: [Required IAM permissions §6](required-permissions.md#6-배포하는-사람에게-필요한-권한) (Korean) |

## Deployment options

Monitoring and utilization queries, and CloudFront sharing, are optional features. Deploy monitoring and utilization queries by configuring `StateLambdaSourceKey` in Stack A. For CloudFront sharing, deploy Stack C in addition.

| Features | Required deployment | First installation | Add to an existing installation |
|---|---|---|---|
| Basic diagram | Stack A, plus Stack B if you have source accounts | Steps 1–3, then step 4 if you have source accounts | Already available |
| Monitoring and utilization queries | Configure `StateLambdaSourceKey` in Stack A | Upload the Lambda package in step 1 → include it in step 2 → collect, then verify in step 5 | Update Stack A in [step 5](#5-add-monitoring-and-utilization-update-stack-a) |
| CloudFront sharing | Stack C and a signed link | Collect the basic diagram, then run [step 6](#6-share-through-cloudfront-deploy-stack-c-and-issue-a-link) | Deploy Stack C and issue a link in step 6 |
| Monitoring and utilization queries + CloudFront sharing | Both deployments above | Install with monitoring and utilization, then run step 6 | Step 5 → step 6 |

With [deploy.sh](#one-command-install-deploysh), the same choices are the options `--monitoring` and `--viewer --days N`. The step numbers in the table refer to the [manual install](#manual-install-step-by-step) and to steps 4 to 6.

## Prerequisites

Check each item before you start:

- [ ] **AWS CLI v2** (`aws --version` prints `aws-cli/2.…`; `deploy.sh` stops on v1), **bash** (default on macOS, Linux, and AWS CloudShell), and **git**
- [ ] **A named profile for the monitoring account** (`--profile`). An IAM Identity Center profile is recommended; see below
- [ ] **Permissions**: credentials in the monitoring account that can create IAM roles, S3 buckets, CodeBuild, EventBridge, Lambda, CloudWatch Logs, SQS, and CloudFront. Credentials in each source account that can create an IAM role. The permissions each stack creates are listed in [Required IAM permissions](required-permissions.md) (Korean); the action-level list for the person deploying is in [§6 of the same document](required-permissions.md#6-배포하는-사람에게-필요한-권한)
- [ ] Only if you choose monitoring and utilization queries: **python3 with pip**. If pip is missing, run `python3 -m ensurepip --user`
- [ ] Only if you choose CloudFront sharing: **openssl**

Run all commands from the repository root. Credentials are passed as a named profile (`--profile`). An IAM Identity Center profile is recommended. Do not create long-term access keys.

```sh
aws configure sso --profile my-hub
```

If you switch roles through another profile, use a profile in `~/.aws/config` with `role_arn` and `source_profile`.

**If it worked:** `aws sts get-caller-identity --profile my-hub --region <Region>` prints the monitoring account ID. If the IAM Identity Center session has expired, run `aws sso login --profile my-hub` (see the [Troubleshooting](#troubleshooting) row "`deploy.sh 가 0단계 자격 증명 확인에서 멈춤`").

## Get the code

```sh
git clone https://github.com/aws-samples/sample-network-topology-visualizer.git
cd sample-network-topology-visualizer
```

**If it worked:** the folder contains `deploy.sh`, `infra/`, `engine/`, and `renderer/`. Run every later command from this folder. The deployment package is built from the git clone, so a repository downloaded as a zip does not work (see the [Troubleshooting](#troubleshooting) row "`1단계 배포 패키지 실패`").

## Choose a deployment option

| Situation | Steps | Step 4 |
|---|---|---|
| A. A single account | 1, 2, 3 | Skip |
| B. A few source accounts that you manage yourself | 1, 2, 3, 4-B | Deploy Stack B in each source account |
| C. AWS Organizations | 1, 2, 3, 4-C | An organization admin deploys a StackSet to the OU |

These are the basic steps for your account setup. Add monitoring and utilization queries and CloudFront sharing according to the [deployment options table](#deployment-options) above.

If the monitoring account has a Transit Gateway, source accounts are discovered automatically from the owner accounts of its attachments. If there is no Transit Gateway, or some source accounts are not attached to it, list those accounts in `TargetAccounts` in step 2. Auto-discovery still runs and the accounts you list are added to the discovered ones, so you do not need to repeat accounts the Transit Gateway already knows.

If you use [deploy.sh](#one-command-install-deploysh), it runs steps 1 to 3 for you; step 4 is the same either way.

## 0. Set your values

Set these once in your terminal. Every command in this guide, including `deploy.sh`, uses them, so you can copy and paste the commands as they are. If you open a new terminal, set them again.

```sh
PROFILE=my-hub                              # profile for the monitoring account
REGION=ap-northeast-2                       # Region for the stacks (any Region works)
PKG_BUCKET=nettopo-pkg-111122223333         # new private bucket for the deployment package; the name must be globally unique
REGIONS=ap-northeast-2,ap-northeast-1       # Regions to collect, comma-separated
```

Regions you do not list in `REGIONS` are not collected at all. They do not appear on the screen and nothing says they are missing, so list every Region you want to see. Give the same list to the source account role in step 4.

Keep the stack names `nettopo-collector`, `nettopo-member-role`, and `nettopo-viewer`. The source account role's trust policy defaults point at a stack named `nettopo-collector`.

## One-command install (deploy.sh)

`deploy.sh` in the top folder runs steps 1-3 of the [manual install](#manual-install-step-by-step), steps [5](#5-add-monitoring-and-utilization-update-stack-a) and [6](#6-share-through-cloudfront-deploy-stack-c-and-issue-a-link), and the [Cleanup](#cleanup) for you. This is the recommended way for a first installation. Use it in the monitoring account only. The step-by-step commands below are for reviewing what the script does or running it yourself.

```sh
# First installation. Running the same command again updates the installation
./deploy.sh --profile "$PROFILE" --region "$REGION" \
  --bucket "$PKG_BUCKET" --regions "$REGIONS"
```

**If it worked:** the script prints its stages in Korean (`== 0단계 …` to `== 3단계 …`), creates the package bucket if it does not exist, deploys Stack A, waits for one collection, and ends without a `🔴 … 실패` (failed) line. The file `out/index.html` (the diagram) now exists, and the last line of the output gives its path. Continue with [Open the diagram](#open-the-diagram); for the basic diagram of a single account, you can stop there.

| To | Add |
|---|---|
| Monitoring and utilization queries | `--monitoring` (installations that use monitoring pass it on every update too) |
| CloudFront sharing | `--viewer --days 30` (link lifetime in days; there is no default, so it is required) |
| Add source accounts that are not attached to the Transit Gateway | `--accounts 111122223333,444455556666` |
| Stack tags | `--tags Owner=net,CostCenter=1234` (once; later runs without it re-apply the existing tags) |
| Preview the resources that would change | `--dry-run` |
| Collect once more | `--collect` |
| Download only the latest diagram (no collection, no deployment) | `--download` (installing and `--collect` already download it at the end; use this to fetch the diagram the collector refreshes every hour) |
| Rotate the signing key | Planned rotation `--viewer --rotate-key --days 30` (old links keep working until they expire) → after the old links expire, `--viewer --retire-old-key` · Leaked link `--viewer --rotate-key --no-overlap --days 30` (every link issued so far returns 403) |
| Delete everything in the monitoring account | `--destroy` (asks for confirmation) |
| [Optional hardening](#optional-hardening): external ID | `--external-id <external ID>` (sets Stack A `MemberExternalId` and adds `ExternalId` to both printed Stack B commands; later runs without it keep the previous value) |
| [Optional hardening](#optional-hardening): organization condition | `--org-id o-<organization ID>` (adds `PrincipalOrgId` to both printed Stack B commands; it is not stored, so pass it again whenever you need the commands) |
| [Optional hardening](#optional-hardening): narrow bucket CORS and CloudFront read access to your Stack C distribution | `--viewer --days 30 --narrow-viewer` (after deploying Stack C, updates Stack A once more with its address and distribution ID; later runs without it keep the values) |

- `--bucket` and `--regions` are needed only for the first installation; later runs reuse the previous values. `--regions` means the same as `REGIONS` in step 0.
- When it finishes you get `out/index.html` (the diagram) and `out/templates/` (the Stack B template and `SHA256SUMS` to hand to source account or organization administrators), and the Stack B command to run in the source accounts is printed. Use it for step 4.
- For CloudFront sharing, the link-signing private key is created at `out/viewer-k1.pem`. Keep it and do not send it off this PC. If you lose it, you must rotate the key.
- On failure the script prints one line, "<step> <what> 실패: <reason>", and the name of the [Troubleshooting](#troubleshooting) row to read. The script's messages are in Korean; the row names are quoted in the table.
- The stack names are fixed: `nettopo-collector` and `nettopo-viewer`. `./deploy.sh --help` lists every option.
- In **AWS CloudShell**, omit `--profile` (the script uses the permissions you signed in to the console with). Everywhere else `--profile` is required. You cannot open the `out/index.html` inside CloudShell in a browser there: download it to your PC with the CloudShell menu **Actions → Download file**, or download `index.html` from the artifact bucket in the S3 console (the script prints this at the end).

## Manual install (step by step)

Skip this section if you installed with `deploy.sh` and go to [Open the diagram](#open-the-diagram). These are the steps the script runs. The commands use the values from [step 0](#0-set-your-values).

### 1. Upload the deployment package to a private bucket

The deployment package is a zip of two folders in this repository: `engine/` (the collector) and `renderer/` (diagram generation). Each time the collector (CodeBuild) runs, it downloads this zip from your bucket and executes it.

```sh
# build the deployment package
mkdir -p out
git archive --format=zip -o out/nettopo-source.zip HEAD engine renderer buildspec.yml
```

```sh
# create the private bucket and upload
aws s3api create-bucket --bucket "$PKG_BUCKET" \
  --create-bucket-configuration LocationConstraint="$REGION" \
  --profile "$PROFILE" --region "$REGION"
aws s3api put-public-access-block --bucket "$PKG_BUCKET" \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true \
  --profile "$PROFILE" --region "$REGION"
aws s3 cp out/nettopo-source.zip "s3://$PKG_BUCKET/nettopo-source.zip" \
  --sse AES256 --profile "$PROFILE" --region "$REGION"
```

If `REGION` is `us-east-1`, omit the `--create-bucket-configuration` option. If the bucket already exists, as when you update to a new version, skip the `create-bucket` and `put-public-access-block` commands and run only `aws s3 cp` (running them again fails with `BucketAlreadyOwnedByYou`).

```sh
# check that the bucket is private: all four values must be true
aws s3api get-public-access-block --bucket "$PKG_BUCKET" --profile "$PROFILE" --region "$REGION"
```

**If it worked:** `aws s3 cp` prints `upload: out/nettopo-source.zip to s3://nettopo-pkg-111122223333/nettopo-source.zip` (with your bucket name), and the check shows `BlockPublicAcls`, `IgnorePublicAcls`, `BlockPublicPolicy`, and `RestrictPublicBuckets` all `true`. If `create-bucket` fails because the name is taken, choose another `PKG_BUCKET` (see the [Troubleshooting](#troubleshooting) row "`1단계 버킷 만들기 실패`").

#### Prepare the state collection Lambda package

Run this only if you choose monitoring and utilization queries. Upload the Lambda package to **the same package bucket** as the source ZIP specified in `SourceLocation`.

```sh
# put the "S3 키" (S3 key) value from the end of the output into STATE_KEY
./infra/build-state-lambda.sh
STATE_KEY='<S3 key from the script output>'    # for example lambda/state-observer-b813997a630e.zip

aws s3 cp "out/${STATE_KEY#lambda/}" "s3://$PKG_BUCKET/$STATE_KEY" \
  --sse AES256 --profile "$PROFILE" --region "$REGION"
```

For a first installation, continue with the monitoring and utilization deployment command in step 2. To add the features to an existing Stack A, run only the Lambda package preparation above, without recreating the bucket or uploading the source ZIP, then return to [step 5](#5-add-monitoring-and-utilization-update-stack-a).

### 2. Deploy Stack A (collector)

For a first installation, run **only one** of the two deployment commands below, according to your chosen features. To add monitoring and utilization to an existing installation, use the update command in [step 5](#5-add-monitoring-and-utilization-update-stack-a).

#### Basic diagram or CloudFront sharing alone

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

#### Install with monitoring and utilization

Upload the Lambda package in step 1 before running this command. Passing the `STATE_KEY` you set above as `StateLambdaSourceKey` includes monitoring and utilization in the initial deployment.

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

After either deployment command completes, get the artifact bucket name.

```sh
ARTIFACT_BUCKET=$(aws cloudformation describe-stacks --stack-name nettopo-collector \
  --query "Stacks[0].Outputs[?OutputKey=='ArtifactBucketName'].OutputValue" --output text \
  --profile "$PROFILE" --region "$REGION")
echo "$ARTIFACT_BUCKET"
```

**If it worked:** each deployment command takes a few minutes (about 2 minutes in our test) and ends with `Successfully created/updated stack - nettopo-collector`, and `echo` prints the artifact bucket name. If the deployment fails, read the reason it prints, then see the [Troubleshooting](#troubleshooting) rows "`2단계 스택 A 배포 실패`" and "`스택이 진행 중 또는 ROLLBACK_COMPLETE`".

- Leave `TargetAccounts` empty to discover source accounts from the monitoring account's Transit Gateway attachments. If any source account is not attached to the Transit Gateway, add `TargetAccounts=<source account ID>,…` to `--parameter-overrides`. Auto-discovery keeps running and the accounts you list are added to it, so list **only the accounts the Transit Gateway does not know**. The monitoring account is always included either way. If you leave it empty you rely on discovery alone, so collection stops when discovery fails in every Region.
- The default schedule collects every hour. To run only on demand, add `ScheduleState=DISABLED`.
- If you add `--tags`, add the same `--tags` to every later update command. Omitting them may remove the tags.

> **Note** The templates are in English because CloudFormation stores Korean description text as `?`. Korean descriptions of the parameters are in the [Parameters](#parameters) section below.

### 3. Initial resource collection

Stack A collects automatically every hour, but run it once now to check the result.

```sh
# run one collection
BUILD_ID=$(aws codebuild start-build --project-name nettopo-collector-collector \
  --query build.id --output text --profile "$PROFILE" --region "$REGION")

# run again until the status is SUCCEEDED (usually 2 to 5 minutes)
aws codebuild batch-get-builds --ids "$BUILD_ID" \
  --query "builds[0].buildStatus" --output text --profile "$PROFILE" --region "$REGION"

# download the diagram
aws s3 cp "s3://$ARTIFACT_BUCKET/index.html" out/index.html --profile "$PROFILE" --region "$REGION"
```

**If it worked:** the status command prints `IN_PROGRESS` while the collection runs and `SUCCEEDED` when it is done (1 min 47 s in our test with 2 accounts and 3 Regions), and `aws s3 cp` prints `download: s3://…/index.html to out/index.html`. Open `out/index.html` in a browser and check it as described in [Open the diagram](#open-the-diagram).

To see what the collector did, read its CodeBuild log:

```sh
aws logs tail /aws/codebuild/nettopo-collector-collector --since 1h --profile "$PROFILE" --region "$REGION"
```

The log is in Korean. Look for the account discovery line, for example `계정 자동 발견(ec2:DescribeTransitGatewayAttachments) — 조회한 리전 3개 · 허브 1 + 멤버 1개: …` (account discovery: 3 Regions queried, the monitoring account plus 1 source account), and the account name line, for example `계정 이름 2/2개 읽음` (names read for 2 of 2 accounts).

If you installed with monitoring and utilization, wait a moment after `SUCCEEDED` before downloading. In that installation a signer function publishes `index.html` right after the build, so downloading immediately can return `404` on the first installation or the previous version on an update. On `404`, download again about a minute later (`deploy.sh` waits for the publication before downloading).

If the build ends with `FAILED`, check the CodeBuild log. If it reports account discovery failure, the monitoring account has no Transit Gateway or could not read its attachments. Discovery cannot be relied on here, so add `TargetAccounts=<source account IDs>` with the source accounts you want to the step 2 command and redeploy (see the [Troubleshooting](#troubleshooting) row "Build `FAILED` with account discovery failure in the log").

## Open the diagram

Open `out/index.html` in a browser (double-click it or drag it into a browser window). It is a single file that opens from your disk; no server is needed. In AWS CloudShell, download the file to your PC first (see the CloudShell bullet in [One-command install](#one-command-install-deploysh)).

**If it worked:** each account is a frame titled with the account name and a masked ID, such as `계정 <account name> (<first 4 digits>…<last 4 digits>)`, and each account has one Region frame for every Region that was collected. When every permission is in place, there is no `⚠` banner at the top of the screen.

With a single account (A), the whole network configuration of the monitoring account is drawn.

Where there are many resources of one type (CloudFront, S3, and WAF web ACLs in the global row; Lambda in a Region row, and so on), they appear as a single "<type> ×N" icon. Click the icon to see the list in the info panel on the right, then click an entry to see that resource. Resources connected to others by a line are not folded; they are drawn on their own.

If you also need source accounts (B, C), only the resources visible from the monitoring account are drawn so far. Create the read-only role in step 4, and the source accounts' resources are added from the next collection.

`⚠ 권한 없어 못 읽은 계정 N` (accounts not readable) means the source accounts do not have the role yet. `⚠ 못 그린 리소스 N` (resources not drawn) is the number of resources that cannot be placed on the diagram, for example an Auto Scaling group with no subnets.

If a Region you expected is missing, see the [Troubleshooting](#troubleshooting) rows "A Region is missing from the screen entirely" and "A Region is empty and the banner names it".

**Next:** If you have source accounts, continue with [Add source accounts](#add-source-accounts) (step 4-B or 4-C). For the basic diagram alone, installation is complete and you can stop here. If you included monitoring and utilization, [verify the deployment](#verify-the-deployment) in step 5. If you chose CloudFront sharing, also run step 6.

## Add source accounts

Only if the diagram should include accounts other than the monitoring account. Choose 4-B or 4-C as in [Choose a deployment option](#choose-a-deployment-option). If you installed with `deploy.sh`, it printed the same Stack B commands at the end with your Region list filled in; the monitoring account ID is shown masked, so get the full ID with the `get-caller-identity` command below.

### 4-B. Deploy Stack B in each source account

Send the source account administrator one file, `infra/stack-b-member-role.yaml`, and two values: the monitoring account ID and the collection Region list. The repository is not needed. In the CloudFormation console, they choose **Create stack → Upload a template file**, upload the file, and enter the two values. On the last screen, check the acknowledgement that CloudFormation may create IAM resources with custom names (the console equivalent of `CAPABILITY_NAMED_IAM`). With the CLI, they replace the `--template-file` path in the command below with the received file.

Get the monitoring account ID:

```sh
HUB_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text --profile "$PROFILE" --region "$REGION")
```

Run the following once per source account with that account's profile. The role is account-wide, so deploy in one Region only.

```sh
aws cloudformation deploy \
  --template-file infra/stack-b-member-role.yaml \
  --stack-name nettopo-member-role \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
      HubAccountId="$HUB_ACCOUNT_ID" \
      AllowedRegions="$REGIONS" \
  --profile <source account profile> --region "$REGION"
```

**If it worked:** the deployment takes about 30 seconds and ends with `Successfully created/updated stack - nettopo-member-role` (stack status `CREATE_COMPLETE`).

- `AllowedRegions` must equal `REGIONS` from step 2. Otherwise some Regions of that account cannot be read and a banner reports that the tool has no read permission for those Regions, so resources are missing from the canvas.
- If Stack A has a name other than `nettopo-collector`, add `HubCollectorRoleNamePattern=<stack name>-BuildRole-*` and `HubStateRoleNamePattern=<stack name>-StateRole-*`.
- If the account already has a role named `NetworkReadOnlyRole`, pass `RoleName=<other name>` and update Stack A with `MemberRoleName=<same name>`. The commands are in [If the role name is already taken](#if-the-role-name-is-already-taken) below.

After the role exists, do two things in the monitoring account. If the source account is not attached to the monitoring account's Transit Gateway, list that account in `TargetAccounts` from step 2 and update Stack A; then run step 3 again. With `deploy.sh`, run it again with `--accounts <source account IDs>` (list every account the Transit Gateway does not know; the list replaces the previous one), which also collects again at the end; if the account is already discovered, `--collect` is enough. When the banner line `⚠ 권한 없어 못 읽은 계정` disappears and the account's VPCs and subnets appear, you are done. There is no separate way to test the role; one collection is the test. If the account still shows as unreadable, see the [Troubleshooting](#troubleshooting) row "Stack B deployed but the account still shows as unreadable".

#### If the role name is already taken

If the source account already has a role named `NetworkReadOnlyRole`, Stack B fails with a name conflict (see the [Troubleshooting](#troubleshooting) row "Stack B `CREATE_FAILED` (name conflict)"). Give the role another name in that account and give Stack A the same name; the two names must match. If the failed attempt left the stack in `ROLLBACK_COMPLETE`, delete that stack first (the Stack B command in [Cleanup](#cleanup)).

In the source account, add `RoleName` to the 4-B command:

```sh
aws cloudformation deploy \
  --template-file infra/stack-b-member-role.yaml \
  --stack-name nettopo-member-role \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
      HubAccountId="$HUB_ACCOUNT_ID" \
      AllowedRegions="$REGIONS" \
      RoleName=<other name> \
  --profile <source account profile> --region "$REGION"
```

In the monitoring account, set the same name in Stack A.

**If you installed with `deploy.sh`:** `deploy.sh` has no option for this, so run the following once from the repository root. Parameters you do not list keep their current values, and later `deploy.sh` runs keep this value too, because `deploy.sh` passes only its own parameters. If you gave `deploy.sh` `--tags`, add the same `--tags` here; otherwise the stack tags are removed.

```sh
aws cloudformation deploy \
  --template-file infra/stack-a-collector.yaml \
  --stack-name nettopo-collector \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides MemberRoleName=<same name> \
  --profile "$PROFILE" --region "$REGION"
```

**If you installed manually:** rerun the step 2 command with `MemberRoleName` added to `--parameter-overrides` (parameters you do not list, such as `StateLambdaSourceKey`, keep their values; re-add `--tags` if you used them):

```sh
aws cloudformation deploy \
  --template-file infra/stack-a-collector.yaml \
  --stack-name nettopo-collector \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
      SourceLocation="$PKG_BUCKET/nettopo-source.zip" \
      TargetRegions="$REGIONS" \
      MemberRoleName=<same name> \
  --profile "$PROFILE" --region "$REGION"
```

Then collect once (step 3, or `./deploy.sh --profile "$PROFILE" --region "$REGION" --collect`) and check that the account appears.

### 4-C. Deploy with StackSets in AWS Organizations

Performed by an organization administrator (management account or StackSets delegated administrator). You need two values from the network administrator: the monitoring account ID and the collection Region list (`REGIONS`). The only file needed is `infra/stack-b-member-role.yaml`; the full repository is not required. If you run from a **delegated administrator** account, add `--call-as DELEGATED_ADMIN` to every StackSet command below (`create-stack-set`, `create-stack-instances`, `list-stack-instances`, `update-stack-set`). Without it the call is treated as a management account call and is rejected.

Enable Organizations access for StackSets once, from the management account. If it is already enabled, continue.

```sh
aws cloudformation activate-organizations-access --profile <management account profile> --region ap-northeast-2
aws cloudformation describe-organizations-access --profile <management account profile> --region ap-northeast-2
```

When it reports `"Status": "ENABLED"`, create the StackSet and deploy it to the OU. `AllowedRegions` contains commas, so pass `--parameters` as JSON. If the template was handed to you as a file, point `--template-body` at that file. Type the Region literally, the same one as Stack A in the monitoring account (the examples use `ap-northeast-2`).

```sh
aws cloudformation create-stack-set --stack-set-name nettopo-member-role \
  --template-body file://infra/stack-b-member-role.yaml \
  --permission-model SERVICE_MANAGED \
  --auto-deployment Enabled=true,RetainStacksOnAccountRemoval=false \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameters '[{"ParameterKey":"HubAccountId","ParameterValue":"<monitoring account ID>"},{"ParameterKey":"AllowedRegions","ParameterValue":"<collection Regions>"}]' \
  --profile <management account profile> --region ap-northeast-2

aws cloudformation create-stack-instances --stack-set-name nettopo-member-role \
  --deployment-targets OrganizationalUnitIds=<OU ID> \
  --regions ap-northeast-2 \
  --profile <management account profile> --region ap-northeast-2

# repeat until every account shows CURRENT / SUCCEEDED (usually 2 to 5 minutes)
aws cloudformation list-stack-instances --stack-set-name nettopo-member-role \
  --query "Summaries[].[Account,Region,Status,StackInstanceStatus.DetailedStatus]" --output table \
  --profile <management account profile> --region ap-northeast-2
```

- Use **one** Region only. The role is an account-wide resource; a second Region fails with a name conflict.
- Automatic deployment is enabled, so new accounts that join the OU get the role automatically.
- StackSets do not deploy to the management account itself. To include it in the diagram, deploy Stack B there with the 4-B command.
- In the console, choose **Service-managed permissions** under StackSets and enter the same template, parameters, OU, and Region.

Verification is the same as 4-B: run step 3 again in the monitoring account (with `deploy.sh`: `--collect`) and check that the source accounts' VPCs appear in the diagram. If a source account is not attached to the monitoring account's Transit Gateway, it is not discovered: list that account in `TargetAccounts` in step 2, update Stack A, then collect.

#### Update the StackSet with a new template

Running `create-stack-set` again for an existing StackSet fails because the name is taken. When you receive a new template, update it with the command below. Keep the parameters you gave at creation with `UsePreviousValue=true` (add any other parameters you set, such as `RoleName`, the same way). The update applies to every account already deployed.

```sh
aws cloudformation update-stack-set --stack-set-name nettopo-member-role \
  --template-body file://infra/stack-b-member-role.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameters ParameterKey=HubAccountId,UsePreviousValue=true ParameterKey=AllowedRegions,UsePreviousValue=true \
  --profile <management account profile> --region ap-northeast-2
```

Follow progress with the `list-stack-instances` command above.

## Optional features (only if you need them)

Skip this part if the basic diagram is enough. Each feature can also be added later to an existing installation. With `deploy.sh`, use `--monitoring` instead of step 5 and `--viewer --days N` instead of step 6 ([One-command install](#one-command-install-deploysh)); the commands below are for the manual path.

### 5. Add monitoring and utilization (update Stack A)

Run this step **to add monitoring and utilization to an existing Stack A**. If you included `StateLambdaSourceKey` in step 2, skip the update command below and go directly to [Verify the deployment](#verify-the-deployment).

Set `PROFILE`, `REGION`, and `PKG_BUCKET` from step 0 to your existing installation's values. Use the bucket specified in the existing Stack A's `SourceLocation` as `PKG_BUCKET`. Run [Prepare the state collection Lambda package](#prepare-the-state-collection-lambda-package) in step 1 to build and upload the ZIP, then update Stack A with the command below.

**If the stack has tags, add the same `--tags` to this update command.** Omitting them removes the existing tags. Existing parameters you do not pass keep their values.

```sh
aws cloudformation deploy \
  --template-file infra/stack-a-collector.yaml \
  --stack-name nettopo-collector \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides StateLambdaSourceKey="$STATE_KEY" \
  --profile "$PROFILE" --region "$REGION"
```

#### Verify the deployment

If you updated an existing stack, get `ARTIFACT_BUCKET` from the artifact bucket lookup block in step 2, then run step 3 again, check that the collection status is `SUCCEEDED`, and download the new `index.html`. If you included the features in the initial deployment, open the file from step 3. Check that the **모니터링** (Monitoring) and **사용률** (Utilization) controls appear at the top of the screen.

Live monitoring shows the current state of resources on top of the diagram. While it is turned on in the screen, it re-reads the state every 5 minutes by default and replaces the values.

Clicking **모니터링** offers two choices. **구조 조회만** (structure only) shows resource state (healthy / impaired). **지표까지** (with metrics) also reads CloudWatch metrics and shows traffic flow on the lines.

The first check runs immediately and then every 5 minutes. Monitoring stops automatically 4 hours after it was turned on, or about 20 minutes after the browser window is closed.

**사용률** (Utilization) shows resource usage and usage relative to a limit. Turn it on in the screen and check that values update.

If you open a downloaded `index.html`, live monitoring works for at most about 12 hours from the time of download (less if the temporary credentials used for signing expire sooner). When it stops, download the file again. Opening through the step 6 sharing link gets fresh signatures each time, so this limit does not apply.

If you also chose CloudFront sharing, continue with step 6.

### 6. Share through CloudFront (deploy Stack C and issue a link)

Run this if you choose CloudFront sharing. **Deploy Stack C separately from Stack A and issue a signed link.** Monitoring and utilization do not need to be deployed for this step, and no additional Stack A parameters are needed. People without an AWS account can view the diagram through the issued link.

Run this after deploying Stack A and completing the first collection. To add sharing to an existing installation, set `PROFILE` and `REGION` from step 0 to its values and use the artifact bucket lookup block in step 2 to get `ARTIFACT_BUCKET`.

```sh
# signing key pair; keep the private key on the administrator's machine, never upload it
mkdir -p out && openssl genrsa -out out/viewer-k1.pem 2048
openssl rsa -in out/viewer-k1.pem -pubout -out out/viewer-k1.pub.pem

# deploy in the same Region as Stack A
aws cloudformation deploy \
  --template-file infra/stack-c-viewer.yaml \
  --stack-name nettopo-viewer \
  --parameter-overrides \
      ArtifactBucketName="$ARTIFACT_BUCKET" \
      ViewerPublicKeyPem="$(cat out/viewer-k1.pub.pem)" \
  --profile "$PROFILE" --region "$REGION"

# issue a link; choose the lifetime in days. The single stdout line is the link
infra/issue-viewer-link.sh --stack nettopo-viewer --key out/viewer-k1.pem --days 30 \
  --profile "$PROFILE" --region "$REGION"
```

**If it worked:** the Stack C deployment takes a few minutes (about 3.5 minutes in our test, mostly creating the CloudFront distribution) and ends with `Successfully created/updated stack - nettopo-viewer`. `issue-viewer-link.sh` prints the link as one line on stdout (`https://<domain>/index.html?Expires=…&Signature=…&Key-Pair-Id=…&Hash-Algorithm=SHA256`) and one expiry line on stderr. The script's messages are in Korean: the expiry line `만료: <time> · 이 링크를 가진 사람은 누구나 본다 · 전부 무효화 = 키 교체(docs/install-guide.md 6단계)` means "Expires: <time> · anyone with this link can view it · to invalidate every link, rotate the key (step 6 of the guide)". Key rotation is described at the end of this step. If issuing fails, see the [Troubleshooting](#troubleshooting) row "`6단계 링크 발급 실패`".

**Completion check:** Open the issued link in a browser and check that the latest diagram appears. If the link itself returns 403, see the [Troubleshooting](#troubleshooting) row "Viewing link returns 403".

- Anyone with the link can view the diagram. There is no login.
- Opening without a link, with an expired link, or any path other than `index.html` returns 403.

**Rotate the key (invalidate links).** A single link cannot be revoked on its own; rotating the signing key invalidates every link signed with that key.

- If a link leaks, create a new key pair and update the stack with the new `ViewerPublicKeyPem` **and** a different `KeyLabel` (`k1` <-> `k2`) in the same update. When the update completes, every old link returns 403. Changing only the public key fails and rolls back. With `deploy.sh` this is `--viewer --rotate-key --no-overlap --days 30`.
- A planned rotation, not caused by a leak, can keep old links working. Change the public key and `KeyLabel`, and put the public key the stack had before the update into `PreviousPublicKeyPem` unchanged; old links then keep working until they expire. After every old link has expired, clear `PreviousPublicKeyPem` and update again. With `deploy.sh` this is `--viewer --rotate-key` followed by `--viewer --retire-old-key` ([One-command install](#one-command-install-deploysh)).

## Updating

With `deploy.sh`, get the new version with `git pull` in your clone, then run the command you used for the first installation again (`--bucket` and `--regions` can be omitted; add `--monitoring` if you use monitoring).

```sh
git pull
./deploy.sh --profile "$PROFILE" --region "$REGION"     # add --monitoring, --viewer --days N, and so on, as at installation
```

**If it worked:** the output ends as in the first installation, without a `🔴 … 실패` (failed) line, and `out/index.html` is the new diagram.

To run the steps yourself:

1. Rebuild the zip from step 1 and upload it to the same key (skip the two bucket creation commands).
2. Run the step 2 `deploy` command again. Parameters you do not pass keep their previous values. Re-add `--tags` if you used them. If the template did not change, it ends with `No changes to deploy`; that is expected.
3. Run step 3.

If monitoring or utilization is enabled, rerun [Prepare the state collection Lambda package](#prepare-the-state-collection-lambda-package) in step 1, then use the step 5 update command to set `StateLambdaSourceKey` to the new key. The key contains a content hash; the same key does not update the code.

**This version requires a Stack B update (two WAF read permissions: `wafv2:ListWebACLs` and `wafv2:ListResourcesForWebACL`).** In accounts that are not updated, WAF shows as "확인하지 못했습니다" (could not be checked). If you deployed with 4-C, use the [StackSet update](#update-the-stackset-with-a-new-template) command. Stack A in the monitoring account changes with the update above (rerun `deploy.sh`, or step 2).

**Account names also require a Stack B update (one permission, `account:GetAccountInformation`, which reads only that account's own name).** Account boxes in the diagram are then titled "account name (masked account ID)". Existing installations must update Stack B in **every source account** to see that account's name (with 4-C, one [StackSet update](#update-the-stackset-with-a-new-template)). Until then the account shows only its ID, as before; its network diagram is unchanged and is not marked "확인하지 못했습니다" (could not be checked). The monitoring account's own name appears after the Stack A update above.

If the new version reads new resource types, redeploy Stack B in the source accounts: for 4-B run the same `deploy` command with the new template; for 4-C use the [StackSet update](#update-the-stackset-with-a-new-template) command. The read permission list lives in Stack B; without the update, those types cannot be read in that account only, and a banner names the account, Region, and types that were missed.

## Cleanup

If you installed with `deploy.sh`, `./deploy.sh --profile "$PROFILE" --region "$REGION" --destroy` performs the monitoring account part below in one go (Stack C, then Stack A, then every version in the three buckets). Delete Stack B in the source accounts separately, as in step 2 below.

Delete in this order. Stack A's artifact bucket and Stack C's log bucket are kept when their stacks are deleted (`DeletionPolicy: Retain`), so empty and delete them yourself. Capture the bucket names first, because the stack outputs disappear when the stacks are deleted. The artifact bucket has versioning enabled, and a package bucket created by `deploy.sh` does too, so every previous object version and delete marker must be removed before the bucket can be deleted; `aws s3 rb --force` does not remove previous versions. The function below does this for any of the three buckets, 1,000 keys at a time, until the bucket is empty.

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

# Capture the bucket names first; the stack outputs disappear when the stacks are deleted
LOG_BUCKET=$(aws cloudformation describe-stacks --stack-name nettopo-viewer \
  --query "Stacks[0].Outputs[?OutputKey=='AccessLogBucketName'].OutputValue" --output text \
  --profile "$PROFILE" --region "$REGION")
ARTIFACT_BUCKET=$(aws cloudformation describe-stacks --stack-name nettopo-collector \
  --query "Stacks[0].Outputs[?OutputKey=='ArtifactBucketName'].OutputValue" --output text \
  --profile "$PROFILE" --region "$REGION")
PKG_BUCKET=$(aws cloudformation describe-stacks --stack-name nettopo-collector \
  --query "Stacks[0].Parameters[?ParameterKey=='SourceLocation'].ParameterValue" --output text \
  --profile "$PROFILE" --region "$REGION" | cut -d/ -f1)

# 1. Stack C (if deployed), then its access-log bucket (kept by the stack)
aws cloudformation delete-stack --stack-name nettopo-viewer --profile "$PROFILE" --region "$REGION"
aws cloudformation wait stack-delete-complete --stack-name nettopo-viewer --profile "$PROFILE" --region "$REGION"
[ -z "$LOG_BUCKET" ] || [ "$LOG_BUCKET" = None ] || empty_and_delete_bucket "$LOG_BUCKET"

# 2. Stack B in each source account (direct deployment)
aws cloudformation delete-stack --stack-name nettopo-member-role \
  --profile <source-account-profile> --region <stack-b-region>
#    or, for an Organizations StackSet: instances first, then the StackSet (add --call-as DELEGATED_ADMIN from a delegated administrator)
aws cloudformation delete-stack-instances --stack-set-name nettopo-member-role \
  --deployment-targets OrganizationalUnitIds=<OU ID> --regions <stackset-region> --no-retain-stacks \
  --profile <management-account-profile> --region <stackset-region>
aws cloudformation list-stack-set-operations --stack-set-name nettopo-member-role --max-results 1 \
  --query "Summaries[0].Status" --output text \
  --profile <management-account-profile> --region <stackset-region>      # repeat until SUCCEEDED
aws cloudformation delete-stack-set --stack-set-name nettopo-member-role \
  --profile <management-account-profile> --region <stackset-region>

# 3. Stack A
aws cloudformation delete-stack --stack-name nettopo-collector --profile "$PROFILE" --region "$REGION"
aws cloudformation wait stack-delete-complete --stack-name nettopo-collector --profile "$PROFILE" --region "$REGION"

# 4. Artifact bucket (kept by Stack A; versioned, so every version is deleted)
empty_and_delete_bucket "$ARTIFACT_BUCKET"

# 5. Deployment package bucket (not created by a stack)
empty_and_delete_bucket "$PKG_BUCKET"
```

With the wrong Region, `delete-stack` for Stack B still succeeds for a stack that does not exist and the role stays, so check in the CloudFormation console that the stack is gone. CloudWatch Logs groups are part of the stacks and are deleted with them. Files on your computer are not deleted: `out/` (the diagram, templates, and the signing keys `viewer-k*.pem`) and the link log `~/.local/state/nettopo/viewer-links.log`.

**If it worked:** `deploy.sh --destroy` ends with `삭제가 끝났습니다.` (deletion finished), followed by a reminder of the local files above. With the manual commands, each `wait stack-delete-complete` returns without output. Following this order in our test removed everything: Stack C, its log bucket, Stack B, Stack A, the artifact bucket, and the package bucket. If something cannot be deleted, see the [Troubleshooting](#troubleshooting) row "`삭제 실패`".

## Troubleshooting

| Symptom | Cause | Action |
|---|---|---|
| A Region is missing from the screen entirely | The Region is not in `TargetRegions`, so it was never collected | Add it to `TargetRegions` in step 2, update Stack A, and collect again |
| A Region is empty and the banner names it | The Region is not in the source account's `AllowedRegions` in Stack B | Give both stacks the same Region list and update |
| Stack B deployed but the account still shows as unreadable | Role name, monitoring account ID, or Stack A name mismatch | Check the three items in 4-B; the error message does not distinguish them |
| Stack B `CREATE_FAILED` (name conflict) | A role with that name already exists | Change `RoleName` and Stack A `MemberRoleName` together ([commands](#if-the-role-name-is-already-taken)) |
| After an update, resources visible in the monitoring account are reported by the banner as unreadable in other accounts only | Stack B in that account is outdated | Redeploy Stack B with the step 4 command |
| Build `FAILED` with account discovery failure in the log | The monitoring account could not read Transit Gateway attachments | Discovery cannot be relied on, so list the source accounts you want in `TargetAccounts` and update Stack A |
| `build-state-lambda.sh 가 "pip 이 없다"로 멈춤` (step 5 script stops with "pip is missing") | python3 has no pip | Run `python3 -m ensurepip --user` and retry, or `PY=<python with pip> ./infra/build-state-lambda.sh` |
| `deploy.sh 가 0단계 준비물 확인에서 멈춤` (stopped at step 0, prerequisites) | `aws` is missing or is AWS CLI v1 · `--viewer` without `openssl` | Install AWS CLI v2 (or run in CloudShell) · install openssl |
| `deploy.sh 가 0단계 자격 증명 확인에서 멈춤` (stopped at step 0, credentials) | The profile is not in `~/.aws/config` · IAM Identity Center session expired · no permission to read CloudFormation | `aws configure sso --profile <name>` or `aws sso login --profile <name>`, then retry. In CloudShell, drop `--profile`; if the console sign-in expired, sign in again and open a new CloudShell |
| `스택이 진행 중 또는 ROLLBACK_COMPLETE` (stack in progress or ROLLBACK_COMPLETE) | Another deployment or deletion is running · the first creation failed and the stack is left in `ROLLBACK_COMPLETE` | If in progress, retry after it ends. `ROLLBACK_COMPLETE` cannot be updated: fix the printed reason, delete the stack (`--destroy` or the console), and install again |
| `1단계 버킷 확인 실패` (step 1, bucket check failed) | Another account owns the name · the bucket is in a different Region from the stack · Block Public Access is off on the existing bucket | Another `--bucket` · a bucket in the stack's Region · turn on all four Block Public Access settings and retry |
| `1단계 버킷 만들기 실패` (step 1, bucket creation failed) | The name is already taken globally · no permission to create buckets | Another `--bucket` · check permissions |
| `1단계 배포 패키지 실패` (step 1, deployment package failed) | Not a git clone · the `--package` file is missing or not a zip | Run from the root of your git clone · if you passed `--package`, check the path |
| `2단계 스택 A 배포 실패` (step 2, Stack A deployment failed) | The printed CloudFormation reason (permissions, parameter validation, and so on) | Fix the reason and rerun the same command. If it was the first creation, see the `ROLLBACK_COMPLETE` row above |
| `3단계 수집 빌드 실패` (step 3, collection build failed) | The collection build ended `FAILED` or `TIMED_OUT` | Read the log with the printed `aws logs tail …` command. For account discovery failures, see the "Build `FAILED` with account discovery failure" row |
| `3단계 페이지 게시 대기 시간 초과` (step 3, timed out waiting for the page) | With monitoring enabled, the signer function has not published the page yet | Download again with `--download` a few minutes later. If it persists, read the printed signer function log |
| `6단계 서명 키 확인 실패` (step 6, signing key check failed) | Stack C exists but the private key file is missing · a different key was given · a rotation is already in progress | Pass the original private key with `--viewer-key`. If it is lost, rotate with `--viewer --rotate-key --no-overlap --days N` |
| `6단계 스택 C 배포 실패` (step 6, Stack C deployment failed) | The printed CloudFormation reason | Fix the reason and retry |
| `6단계 링크 발급 실패` (step 6, link issuance failed) | `infra/issue-viewer-link.sh` failed (no Stack C output · openssl) | Check Stack C and retry |
| `삭제 실패` (cleanup failed) | A stack deletion failed (printed reason) · bucket objects could not be deleted | Fix the reason and rerun `--destroy` (what is already deleted is skipped) |
| WAF web ACLs are missing, or the banner says "확인하지 못했습니다" for them | Stack B in that account predates this version and lacks the WAF read permissions (`wafv2:ListWebACLs`, `wafv2:ListResourcesForWebACL`) | Update Stack B with the latest template (rerun 4-B or the [StackSet update](#update-the-stackset-with-a-new-template)), then collect once |
| Viewing link returns 403 | Expired, wrong path, or an old link after key rotation | Issue a new link |
| Diagram loads but live numbers stop | The screen could not read the state | Reopen the link; check that step 5 was applied |
| You use Bedrock but no "Bedrock Endpoint" appears | Without a VPC interface endpoint (calls go over NAT or the internet) this tool cannot see Bedrock calls | Expected. Create an endpoint; it appears from the next collection |
| One source account shows little more than VPCs and route tables, and the banner reports the rest as unreadable | Stack B in that account is outdated and lacks read permissions. In one measurement only 8 of the 36 resource types attempted succeeded | Redeploy Stack B from the latest `infra/stack-b-member-role.yaml` and run one collection |

## Parameters

Only the commonly used parameters are listed. See the `Parameters` section of each template for the full list.

### Stack A `infra/stack-a-collector.yaml`

| Parameter | Description | Default | Required |
|---|---|---|---|
| `SourceLocation` | S3 location of the deployment package zip (`bucket/key`, without `s3://`) | None | Yes |
| `TargetRegions` | Regions to collect, comma-separated. Empty collects only the stack's Region | Empty | Effectively yes |
| `TargetAccounts` | Source account IDs to add, comma-separated. Discovery from Transit Gateway attachments runs either way, and listed accounts are added to the result, so list only the accounts the Transit Gateway does not know | Empty | No |
| `Schedule` | Collection interval (EventBridge schedule expression) | `rate(1 hour)` | No |
| `ScheduleState` | Whether the schedule is enabled | `ENABLED` | No |
| `MemberRoleName` | Role assumed in source accounts; must match Stack B `RoleName` | `NetworkReadOnlyRole` | No |
| `MemberExternalId` | Optional. Same value as Stack B `ExternalId`; passed when entering source accounts ([Optional hardening](#optional-hardening)) | Empty | No |
| `ViewerOrigin` | Optional. Stack C address `https://` + its `ViewerDomain` output; narrows the bucket CORS rule to it ([Optional hardening](#optional-hardening)) | Empty | No |
| `ViewerDistributionId` | Optional. Stack C output `DistributionId`; only that distribution may read `index.html` from the bucket | Empty | No |
| `StateLambdaSourceKey` | S3 key of the monitoring and utilization Lambda zip. Empty creates no resources for either feature | Empty | No |
| `FlowLogReading` | VPC flow log reading (`ENABLED` / `DISABLED`). Works only in installations that set `StateLambdaSourceKey` | `DISABLED` | No |
| `FlowLogGroupNamePrefixes` | CloudWatch Logs flow log group names to read (full names, comma-separated) | Empty | No |
| `LogRetentionDays` | CodeBuild log retention in days | `30` | No |

### Stack B `infra/stack-b-member-role.yaml`

| Parameter | Description | Default | Required |
|---|---|---|---|
| `HubAccountId` | Monitoring account ID (12 digits) | None | Yes |
| `AllowedRegions` | Regions the monitoring account may read in this account; same as Stack A `TargetRegions` | `us-east-1,us-west-2,ap-northeast-2` | Effectively yes |
| `RoleName` | Name of the role to create; must match Stack A `MemberRoleName` | `NetworkReadOnlyRole` | No |
| `HubCollectorRoleNamePattern` | Name pattern of Stack A's collector role; only if you renamed Stack A | `nettopo-collector-BuildRole-*` | No |
| `HubStateRoleNamePattern` | Name pattern of Stack A's live monitoring role; only if you renamed Stack A | `nettopo-collector-StateRole-*` | No |
| `HubPermissionSetName` | IAM Identity Center permission set a person uses to collect from the monitoring account with the local CLI | Empty | No |
| `ExternalId` | Optional. If set, the role can be assumed only with this external ID ([Optional hardening](#optional-hardening)) | Empty | No |
| `PrincipalOrgId` | Optional. If set (`o-...`), only principals in that AWS Organizations organization can assume the role | Empty | No |
| `FlowLogReading` and 3 more | The same four flow log parameters as Stack A | Disabled / empty | No |

### Stack C `infra/stack-c-viewer.yaml`

| Parameter | Description | Default | Required |
|---|---|---|---|
| `ArtifactBucketName` | Stack A output `ArtifactBucketName` | None | Yes |
| `ViewerPublicKeyPem` | Full PEM of the RSA 2048 public key used to sign links | None | Yes |
| `KeyLabel` | Key slot (`k1` or `k2`); switch together with the public key when rotating | `k1` | No |
| `PreviousPublicKeyPem` | Only during a planned key rotation. Put the stack's previous `ViewerPublicKeyPem` value here unchanged and links signed with the old key keep working until they expire. Clear it once the old links have expired | Empty | No |
| `AccessLogRetentionDays` | CloudFront access log retention in days | `90` | No |
| `LogReaderPrincipalArns` | IAM role or user ARNs in this account that may read the access logs (comma-separated, no spaces or wildcards). Logs contain full signed links, so readers can reuse a link. For IAM Identity Center, use the role ARN with its path, not the session ARN. Empty means only the account root user can read the logs | Empty | No |

## Security

- In target accounts the collector calls only `Describe*`, `Get*`, `List*`, and `Search*` APIs and changes nothing. The single exception is `sts:AssumeRole`, used to enter a source account; the assumed session keeps the read-only restriction, which is enforced at runtime.
- The permissions granted in a source account are exactly the policy in `infra/stack-b-member-role.yaml`, all read actions (the only exception is decryption through S3 when flow logs are encrypted with a KMS customer key). The collector's permissions in the monitoring account are in the role policies of `infra/stack-a-collector.yaml`. For each action, why it is needed, its resource scope, and what the screen shows when it is denied, see [Required IAM permissions](required-permissions.md) (Korean).
- The deployment package and results live only in private buckets in your account. There are no public URLs.
- The generated HTML makes no outbound requests. With live monitoring enabled, it reads and writes S3 in the same account through signed URLs only.
- With live monitoring enabled, the HTML embeds three signed URLs: one to read state, one to write the monitoring signal, and one to open the latest page (read `index.html`). They are valid for at most about 12 hours and each is limited to a single S3 key. Treat the HTML as private; if it leaks, only those three operations are possible until the signatures expire.
- Anyone holding a Stack C viewing link can open it. Treat links like passwords and rotate the key if one leaks. CloudFront access logs contain the full link.
- For each sharing path (download, S3 signed URL, Stack C, Identity Center, internal network only) — who can open it, whether access is attributable, how to revoke, lifetime, where links leak — and Stack C private-key handling, see [viewer-sharing.md](viewer-sharing.md) (Korean).

### Optional hardening

All four are off by default; leaving them empty keeps the behavior described above.

- **External ID (Stack B `ExternalId` + Stack A `MemberExternalId`).** The member role trusts only the monitoring account's collector roles by name. If your policy also requires an external ID, set the same value in both stacks; the collector and the live monitoring function then pass it when they assume the role. A person who collects with the local CLI passes it with `--external-id`.
- **Organization (Stack B `PrincipalOrgId`).** Only principals in that AWS Organizations organization can assume the role. The monitoring account must be in the same organization.
- **Viewer distribution (Stack A `ViewerDistributionId`).** The bucket policy lets CloudFront read only `index.html`, but by default from any distribution in the monitoring account, because the Stack C distribution ID is not known when Stack A is created. Set it after deploying Stack C so that only your distribution can serve the page.
- **Bucket CORS (Stack A `ViewerOrigin`).** By default the bucket's CORS rule accepts pages opened from a file (`null`) and from any CloudFront domain, because the Stack C address is not known when Stack A is created. CORS grants no access by itself (every request still needs a signed URL), but after deploying Stack C you can narrow it to your distribution.

If you installed with `deploy.sh`, one command does all four ([One-command install](#one-command-install-deploysh)). Setting the external ID only in Stack B makes collection fail, so update the monitoring account first, then hand the printed Stack B commands to the source account or organization administrators.

```sh
# Monitoring account: put the external ID in Stack A, deploy Stack C, then narrow CORS and CloudFront read access to it.
# The Stack B commands printed at the end (one-line CLI and StackSet) include ExternalId and PrincipalOrgId.
./deploy.sh --profile "$PROFILE" --region "$REGION" \
  --external-id <external ID> --org-id <o-...> --viewer --days 30 --narrow-viewer
```

To run it yourself:

```sh
# Source account: Stack B with an external ID and an organization condition
aws cloudformation deploy --template-file infra/stack-b-member-role.yaml --stack-name nettopo-member-role \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides HubAccountId=<monitoring account ID> ExternalId=<external ID> PrincipalOrgId=<o-...> \
  --profile <source account profile> --region "$REGION"

# Monitoring account: the same external ID, and the Stack C address for CORS
VIEWER_DOMAIN=$(aws cloudformation describe-stacks --stack-name nettopo-viewer \
  --query "Stacks[0].Outputs[?OutputKey=='ViewerDomain'].OutputValue" --output text \
  --profile "$PROFILE" --region "$REGION")
VIEWER_ID=$(aws cloudformation describe-stacks --stack-name nettopo-viewer \
  --query "Stacks[0].Outputs[?OutputKey=='DistributionId'].OutputValue" --output text \
  --profile "$PROFILE" --region "$REGION")
aws cloudformation deploy --template-file infra/stack-a-collector.yaml --stack-name nettopo-collector \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides MemberExternalId=<external ID> ViewerOrigin="https://$VIEWER_DOMAIN" ViewerDistributionId="$VIEWER_ID" \
  --profile "$PROFILE" --region "$REGION"
```
