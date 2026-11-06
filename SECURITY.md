# Security

## Reporting a security issue

If you discover a potential security issue in this project, please notify AWS/Amazon Security via the
[vulnerability reporting page](https://aws.amazon.com/security/vulnerability-reporting/).
Please do **not** create a public GitHub issue.

## Security design summary

This section describes only the controls that the code in this repository implements. Anything not listed
here (for example an audit trail or customer-managed encryption keys) is not provided by this tool.

### Read-only collection

- Every AWS API call made by the collector engine passes through a botocore hook (on both
  `before-parameter-build` and `before-call`) that allows only operations whose names start with
  `Describe`, `Get`, `List`, or `Search`. Any other operation raises `ReadOnlyViolation` and the call is not
  sent (fail-closed). Because the hook also runs at parameter build time, presigned URLs for write
  operations are blocked as well.
- The only exception is `sts:AssumeRole`, which is available only from a dedicated client used to enter
  member accounts. Sessions built from the assumed credentials get the same read-only hook.
- A static test checks that the engine creates AWS clients only through this guard.
- The member-account role (stack B) allows only read actions — operations named `Describe…`, `Get…`,
  `List…`, or `Search…`, plus `apigateway:GET` on named paths — each listed by name, with no wildcard
  actions. Optional flow-log reading (off by default) adds `logs:DescribeLogGroups`,
  `logs:DescribeLogStreams`, `logs:GetLogEvents`, `s3:GetBucketLocation`, `s3:GetObject`, `s3:ListBucket`,
  and `kms:Decrypt` limited to use through S3; when it is enabled, the hub's monitoring role gets the
  equivalent flow-log reads. The role trusts only the hub account's collector and monitoring role name
  patterns; no IAM Identity Center users are trusted unless you name a permission set.
- To label accounts by name, the structure collector reads each account's own name with
  `account:GetAccountInformation` (no account ID parameter; the permission is limited to that account's own
  resource and to us-east-1). The 5-minute monitoring role does not have this permission. If it is denied,
  the diagram shows the masked account ID instead.
- Optional, off by default: Stack B `ExternalId` adds an `sts:ExternalId` condition (Stack A `MemberExternalId`
  passes the same value on every assume), and Stack B `PrincipalOrgId` adds an `aws:PrincipalOrgID` condition.
- Scope: the read-only guard applies to calls into the accounts being collected. Resolving the operator's
  own credentials (SSO token refresh, `sso:GetRoleCredentials`, and `sts:AssumeRole` for role-chaining
  profiles when using the local CLI) is deliberately outside the guard; no service clients are built from
  that credential session. In the hub account, the collector writes its output to its own bucket, and the
  optional monitoring and signing functions write state and signed files to that same bucket.

### No inbound endpoints

- The CloudFormation templates create no API Gateway, Lambda function URL, load balancer, or Amazon Cognito
  resources. The optional AWS Lambda functions are invoked by Amazon EventBridge rules (a schedule,
  object-created events for specific keys in the tool's bucket, and successful collector builds), or
  manually by an operator with `lambda:InvokeFunction` in the hub account.
- The optional viewer stack (stack C) creates an Amazon CloudFront distribution that serves only `GET` and
  `HEAD` over HTTPS and requires a signed URL.

### Storage

- The artifact bucket enables all four S3 Block Public Access settings and bucket-owner-enforced object
  ownership (ACLs disabled), uses default encryption with SSE-S3 (AES-256), enables versioning, and has a
  bucket policy that denies any request not made over TLS (`aws:SecureTransport` is `false`).
- The bucket's CORS rule allows only `GET` and `PUT`, and only from the `null` origin (pages opened from a
  file, and sandboxed or other opaque-origin pages) or from CloudFront. CORS grants no access by itself;
  every request still needs a presigned URL. Optional Stack A
  `ViewerOrigin` narrows the CloudFront origin to your Stack C distribution.
- The bucket policy lets CloudFront read only `index.html`, through origin access control. By default any
  distribution in the monitoring account qualifies; optional Stack A `ViewerDistributionId` limits it to your
  Stack C distribution.
- The viewer access-log bucket (stack C) also blocks public access, uses SSE-S3, denies non-TLS requests,
  limits who can read the logs, and expires logs after a configurable period (default 90 days).
- Not provided: an AWS CloudTrail trail (use your account or organization trail), S3 server access logging
  on the artifact bucket, and customer-managed AWS KMS keys.

### Handle the diagram as sensitive data

- The generated diagram HTML contains details of your environment, including AWS account IDs and account
  names (which may be email addresses), VPC and subnet CIDR blocks, public IP addresses, resource IDs, and
  resource tags. Do not publish it or store it
  in a public location. Share it only through the signed links described below.
- When monitoring is enabled, the HTML also contains presigned S3 URLs (read the latest state, read and
  write the monitoring control key, read the diagram). Anyone who has a copy of the HTML can use them until
  they expire. Every open copy reads the same control key, so all windows follow the same live setting; the
  control key holds only on/off, the chosen layers, and the expiry times.
- The HTML sets a Content Security Policy with `default-src 'none'`, allows scripts only by SHA-256 hash,
  and limits `connect-src` to the bucket's regional S3 endpoint (`'none'` when monitoring is off). It loads
  no external resources.

### Signed link lifetime and key rotation

- Presigned S3 URLs request a 12-hour lifetime, capped by the remaining lifetime of the signing
  credentials. They are re-signed every 5 minutes by default (the `StateSchedule` parameter) and after each
  collector build, and are not issued when less than 5 minutes would remain.
- CloudFront viewer links (stack C) are signed locally with `openssl` using an RSA 2048-bit key and SHA-256
  (the link carries `Hash-Algorithm=SHA256`); the private key is not uploaded to AWS. You must choose the
  link lifetime explicitly (`--days`); there is no default and the tool does not enforce a maximum. Each link grants access to `/index.html` only.
- Key rotation uses two key slots. `deploy.sh --rotate-key` keeps the previous public key trusted so that
  existing links keep working; adding `--no-overlap` removes the previous key immediately (for example after
  a key leak). `deploy.sh --retire-old-key` removes it later. The record of issued links that this step uses to
  find the longest remaining lifetime is kept only on the machine that issued them; links issued elsewhere
  keep working until the previous key is removed.
