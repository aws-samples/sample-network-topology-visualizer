# Estimated Costs

[한국어](costs.md)

These are estimates calculated from the template defaults, not measurements. Prices vary by Region and change over time; use the [AWS Pricing Calculator](https://calculator.aws/) with your Region and settings.

Charges are billed to the AWS account that owns the resource or, for API requests, to the account that owns the IAM principal making the request. Stack A and Stack C create all of their resources (CodeBuild, Lambda, S3, CloudWatch Logs, SQS, and CloudFront) in the monitoring account, so that account pays for them. Source accounts are charged only for CloudWatch `GetMetricData` requests, which the collector makes with the Stack B role it assumes in each source account. Stack B creates only an IAM role, which is free. If traffic monitoring and utilization are not enabled, source accounts incur no charges.

| Item | What the cost depends on | Approximate monthly cost | Billed to |
|---|---|---|---|
| CodeBuild collection (`BUILD_GENERAL1_SMALL`, once an hour by default) | price per build minute × minutes per build × 720 builds a month. At $0.005 per minute (us-east-1 and ap-northeast-2, AWS price list of 2026-09-11) and the usual 2 to 5 minutes per build: $0.005 × 2 × 720 = $7.20 to $0.005 × 5 × 720 = $18 | About $7 to $18 | Monitoring account |
| Lambda (live monitoring, if enabled): two functions, state observation (1,024 MB, arm64) and page signing (256 MB, arm64), each every 5 minutes and also on events | invocations × run time × memory | Depends on run time | Monitoring account |
| S3 storage and requests | Grows over time: every build adds a snapshot, and the artifact bucket keeps previous versions (only previous versions of `index.html` and `unsigned/` expire, after 1 day). A package bucket created by `deploy.sh` also keeps previous versions | Grows with history | Monitoring account |
| CloudWatch Logs | Three log groups (collector, state, signer), kept 30 days by default (`LogRetentionDays`) | Per GB ingested and stored | Monitoring account |
| SQS dead-letter queue (live monitoring) | Holds only failed state runs; normally empty | About $0 | Monitoring account |
| CloudFront (Stack C) | Data transfer and requests, plus the access logs stored in the log bucket (kept 90 days by default) | Depends on viewers | Monitoring account |
| CloudWatch metric queries (traffic monitoring or utilization) | Metrics requested per collection cycle. `GetMetricData` costs $0.01 per 1,000 metrics and is not included in the CloudWatch Free Tier. The time range and the number of data points returned do not affect the price | Depends on resources | Account that owns the metrics |
| Structure collection API calls (`Describe*` and similar) | Not billed | None | No charge |

Almost all of it is CodeBuild run time. Each extra minute per build (more accounts, Regions, or resources) adds about $3.60 a month ($0.005 × 720); a longer `Schedule` reduces it proportionally.
