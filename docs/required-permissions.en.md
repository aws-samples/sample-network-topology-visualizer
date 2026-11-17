# Required IAM permissions

An action-by-action description of the IAM permissions NetworkTopology creates in each stack. It is written to be read next to the template policies during a security review. The templates are the source of truth (`infra/stack-a-collector.yaml`, `infra/stack-b-member-role.yaml`, `infra/stack-c-viewer.yaml`), and a test in this repository checks that the action lists in this document match them.

[한국어](required-permissions.md)

## Principles

- **In a source account it only reads.** The collector calls only APIs whose names start with `Describe*`, `Get*`, `List*` or `Search*`; anything else is blocked at runtime by the collection engine (`engine/src/nettopo/awsguard.py`). The one exception is the `sts:AssumeRole` used to enter a source account, and the session it returns carries the same restriction.
- **No wildcards in the actions of Allow statements.** Every action is named. This is narrower than the managed `ReadOnlyAccess` policy. A wildcard action (`s3:*`) appears only in the **Deny** statement that blocks requests not made over TLS.
- **Why the resource is `*`.** The `Describe*` family of APIs in EC2 and elsewhere does not support resource-level permissions. Instead an `aws:RequestedRegion` condition allows only the Regions being collected (Stack A `TargetRegions`, Stack B `AllowedRegions`). APIs that exist in one Region only (CloudFront, Route 53, CloudFront-scope WAF and the account name in us-east-1; Global Accelerator and Cloud WAN in us-west-2) sit in separate statements pinned to that Region, so their cards do not disappear when that Region is absent from the collected list.
- **Writes happen inside the monitoring account, and only to specific keys.** They are all listed in [4. Actions that are not reads, and why](#4-actions-that-are-not-reads-and-why).

The last column of every table below says **what a denial looks like on screen**. An area that could not be read is never drawn as if it were empty.

- **Listing**: everything of that type in that account and Region is reported in a banner at the top of the screen as "could not be read, no permission". Other types are still drawn.
- **Follow-up**: a call that asks for more about one resource found by a listing. If denied, only that resource is marked as partially unreadable; its card and the remaining lines stay.
- **Name**: the account frame's title shows the account ID instead of the account name. That account's network diagram is unaffected and does not turn into "could not confirm".

## 1. Stack B: the read-only role in a source account (`NetworkReadOnlyRole`)

One role per source account. The collector in the monitoring account assumes it to read.

**Trust policy.** Of the principals in the monitoring account (`HubAccountId`), only these may assume the role (an `aws:PrincipalArn` condition).

- Stack A's collection role (`HubCollectorRoleNamePattern`, `nettopo-collector-BuildRole-*` by default)
- Stack A's monitoring role (`HubStateRoleNamePattern`, `nettopo-collector-StateRole-*` by default)
- Optional: the IAM Identity Center permission set a person uses to collect from a local CLI (`HubPermissionSetName`). Left empty, nothing is added.

The account root appears in `Principal` because IAM does not allow a wildcard there. The real boundary is the `aws:PrincipalArn` condition, which is not the same as trusting the root without a condition (that is, every principal in the account). The maximum session duration is one hour.

### 1-1. Structure collection (always)

The reads used to draw the diagram. Collection runs hourly by default. With monitoring on, the types that have a state are re-read every 5 minutes.

| Action | Why it is needed | Resource scope | When denied |
|---|---|---|---|
| `ec2:DescribeVpcs` | VPCs and their CIDRs | `*`, collected Regions | Listing |
| `ec2:DescribeSubnets` | Subnets, AZs, CIDRs | `*`, collected Regions | Listing |
| `ec2:DescribeRouteTables` | Route tables, subnet associations, route lines | `*`, collected Regions | Listing |
| `ec2:DescribeInternetGateways` | Internet gateways and their VPC attachment | `*`, collected Regions | Listing |
| `ec2:DescribeNatGateways` | NAT gateways | `*`, collected Regions | Listing |
| `ec2:DescribeTransitGateways` | Transit Gateways and their owning account | `*`, collected Regions | Listing |
| `ec2:DescribeTransitGatewayAttachments` | TGW attachments and their connection lines; in the monitoring account it also discovers source accounts | `*`, collected Regions | Listing |
| `ec2:DescribeVpcPeeringConnections` | VPC peering | `*`, collected Regions | Listing |
| `ec2:DescribeVpcEndpoints` | VPC endpoints and the subnets they sit in | `*`, collected Regions | Listing |
| `ec2:DescribeVpcEndpointServiceConfigurations` | The GWLB or NLB behind an endpoint service this account provides (the line from a GWLB endpoint to its GWLB). Called only when a GWLB or NLB exists | `*`, collected Regions | Follow-up |
| `ec2:DescribeVpcEndpointServices` | The provider account of an endpoint service owned by another account, and identifying Bedrock endpoints | `*`, collected Regions | Follow-up |
| `ec2:DescribeSecurityGroups` | Security groups and rules that reference other groups | `*`, collected Regions | Listing |
| `ec2:DescribeNetworkAcls` | Network ACLs and their subnet associations | `*`, collected Regions | Listing |
| `ec2:DescribeNetworkInterfaces` | Which subnet an RDS instance sits in, who owns a private-IP load balancer target, and the ENI list used for flow-log verdicts | `*`, collected Regions | Follow-up |
| `elasticloadbalancing:DescribeLoadBalancers` | ALBs, NLBs and GWLBs, and the subnets they sit in | `*`, collected Regions | Listing |
| `elasticloadbalancing:DescribeListeners` | Listeners (protocol, port); once per load balancer | `*`, collected Regions | Follow-up |
| `elasticloadbalancing:DescribeTargetGroups` | Target groups and their target type; once per load balancer | `*`, collected Regions | Follow-up |
| `elasticloadbalancing:DescribeTargetHealth` | Registered targets (the load balancer to target lines) and target health for monitoring. If denied, the target count is not recorded as zero | `*`, collected Regions | Follow-up |
| `elasticloadbalancing:DescribeTags` | Target group tags, which connect a target group to its EKS cluster | `*`, collected Regions | Follow-up |
| `elasticloadbalancing:DescribeRules` | The rule count per ALB listener (the rules column of the console's Listeners and rules view); once per ALB listener. If denied, that column is left blank rather than `0`, and the load balancer is marked as not confirmed | `*`, collected Regions | Follow-up |
| `elasticloadbalancing:DescribeListenerCertificates` | Every certificate on an HTTPS or TLS listener (default plus SNI); once per such listener. If denied, only the default certificate appears and the load balancer is marked as not confirmed | `*`, collected Regions | Follow-up |
| `wafv2:GetWebACLForResource` | The web ACL attached to an ALB; once per ALB. If denied, it is not recorded as "none attached" | `*`, collected Regions | Follow-up |
| `wafv2:GetWebACL` | A web ACL's default action and rule count. `GetWebACLForResource` is also evaluated against this permission for the attached ACL | `*`, collected Regions | Follow-up |
| `wafv2:ListWebACLs` | The regional-scope web ACL card (CloudFront scope is the us-east-1 row below) | `*`, collected Regions | Listing |
| `wafv2:ListResourcesForWebACL` | The line from a regional-scope web ACL to the API Gateway REST API it protects. If denied, it is not recorded as "protects nothing" | `*`, collected Regions | Follow-up |
| `directconnect:DescribeConnections` | Direct Connect connections, and their state for monitoring | `*`, collected Regions | Listing |
| `directconnect:DescribeVirtualInterfaces` | Virtual interfaces, and their BGP state for monitoring | `*`, collected Regions | Listing |
| `directconnect:DescribeDirectConnectGateways` | Direct Connect gateways | `*`, collected Regions | Listing |
| `directconnect:DescribeDirectConnectGatewayAssociations` | A DX gateway's VGW and TGW associations; once per gateway | `*`, collected Regions | Follow-up |
| `ec2:DescribeInstances` | EC2 instances (type, private IP, state) | `*`, collected Regions | Listing |
| `autoscaling:DescribeAutoScalingGroups` | Auto Scaling groups | `*`, collected Regions | Listing |
| `eks:ListClusters` | The list of EKS clusters | `*`, collected Regions | Listing |
| `eks:DescribeCluster` | A cluster's VPC, subnets and endpoint | `*`, collected Regions | Follow-up |
| `network-firewall:ListFirewalls` | The list of Network Firewalls | `*`, collected Regions | Listing |
| `network-firewall:DescribeFirewall` | A firewall's subnets and endpoints | `*`, collected Regions | Follow-up |
| `ec2:DescribeTransitGatewayRouteTables` | TGW route tables (visible only in the account that owns the TGW) | `*`, collected Regions | Listing |
| `ec2:GetTransitGatewayRouteTableAssociations` | The attachments that use a route table | `*`, collected Regions | Follow-up |
| `ec2:GetTransitGatewayRouteTablePropagations` | The attachments that propagate routes into it | `*`, collected Regions | Follow-up |
| `ec2:SearchTransitGatewayRoutes` | TGW routes and blackhole destinations | `*`, collected Regions | Follow-up |
| `ec2:GetTransitGatewayPrefixListReferences` | The Prefix list references tab of a TGW route table (the same table as the console); once per route table. If denied, that table is not recorded as "no references" | `*`, collected Regions | Follow-up |
| `ec2:DescribeTransitGatewayVpcAttachments` | A VPC attachment's subnets, DNS support and appliance mode (the console's attachment detail); once per Region, only when a VPC attachment exists. If denied, it is not recorded as "no subnets" | `*`, collected Regions | Follow-up |
| `ec2:DescribeVpcAttribute` | A VPC's DNS resolution and DNS hostnames settings (the console's VPC details); twice per VPC. If denied, they are not recorded as off | `*`, collected Regions | Follow-up |
| `ec2:DescribeDhcpOptions` | The contents of a VPC's DHCP options set (domain name, name servers); once per Region | `*`, collected Regions | Follow-up |
| `ec2:DescribeInstanceConnectEndpoints` | EC2 Instance Connect endpoints | `*`, collected Regions | Listing |
| `rds:DescribeDBInstances` | RDS DB instances, and their state for monitoring | `*`, collected Regions | Listing |
| `ecs:ListClusters` | The list of ECS clusters | `*`, collected Regions | Listing |
| `ecs:ListServices` | The services in a cluster. If denied, all of ECS is left as unreadable, because drawing part of it would make the rest look absent | `*`, collected Regions | Listing |
| `ecs:DescribeServices` | A service's subnets, security groups and target groups | `*`, collected Regions | Follow-up |
| `sagemaker:ListNotebookInstances` | SageMaker notebook instances | `*`, collected Regions | Listing |
| `sagemaker:DescribeNotebookInstance` | A notebook's subnet, security groups and direct internet access | `*`, collected Regions | Follow-up |
| `sagemaker:ListDomains` | SageMaker Studio domains | `*`, collected Regions | Listing |
| `sagemaker:DescribeDomain` | A domain's VPC and subnets | `*`, collected Regions | Follow-up |
| `sagemaker:ListEndpoints` | SageMaker inference endpoints | `*`, collected Regions | Listing |
| `sagemaker:DescribeEndpoint` | An endpoint's configuration name | `*`, collected Regions | Follow-up |
| `sagemaker:DescribeEndpointConfig` | The model name in that configuration | `*`, collected Regions | Follow-up |
| `sagemaker:DescribeModel` | A model's subnets and security groups | `*`, collected Regions | Follow-up |
| `ec2:DescribeVpnGateways` | Virtual private gateways (VGWs) and their VPC attachment | `*`, collected Regions | Listing |
| `ec2:DescribeCustomerGateways` | Customer gateways (public IP, BGP ASN) | `*`, collected Regions | Listing |
| `lambda:ListFunctions` | Lambda functions attached to a VPC | `*`, collected Regions | Listing |
| `ec2:DescribeVpnConnections` | Site-to-Site VPN connections. The pre-shared keys and device configuration in the response are not stored | `*`, collected Regions | Listing |
| `elasticache:DescribeCacheClusters` | ElastiCache cache clusters, and their state for monitoring | `*`, collected Regions | Listing |
| `elasticache:DescribeCacheSubnetGroups` | A cache's VPC and subnets; once per Region | `*`, collected Regions | Follow-up |
| `elasticache:DescribeReplicationGroups` | Replication group layout | `*`, collected Regions | Follow-up |
| `rds:DescribeDBClusters` | Aurora and Multi-AZ DB clusters, and their state for monitoring | `*`, collected Regions | Listing |
| `rds:DescribeDBSubnetGroups` | A cluster's VPC and subnets; once per Region | `*`, collected Regions | Follow-up |
| `es:ListDomainNames` | The list of OpenSearch Service domains | `*`, collected Regions | Listing |
| `es:DescribeDomains` | A domain's VPC, subnets and endpoint. If denied, it is not recorded as a public domain | `*`, collected Regions | Follow-up |
| `kafka:ListClustersV2` | Amazon MSK clusters, and their state for monitoring | `*`, collected Regions | Listing |
| `kafka:ListNodes` | Broker ENIs, which connect load balancer targets; once per provisioned cluster | `*`, collected Regions | Follow-up |
| `ec2:DescribeManagedPrefixLists` | The name of a prefix list used as a route destination; only when a route uses one | `*`, collected Regions | Follow-up |
| `ec2:GetManagedPrefixListEntries` | The CIDRs in a customer-managed prefix list | `*`, collected Regions | Follow-up |
| `ec2:DescribeAddresses` | Elastic IPs and what they are attached to | `*`, collected Regions | Listing |
| `rds:DescribeDBProxies` | RDS proxies | `*`, collected Regions | Listing |
| `rds:DescribeDBProxyTargets` | A proxy's targets (DB instances, clusters) | `*`, collected Regions | Follow-up |
| `ec2:DescribeTransitGatewayConnects` | The transport attachment a TGW Connect attachment rides on; only when a Connect exists | `*`, collected Regions | Follow-up |
| `ec2:DescribeTransitGatewayConnectPeers` | Connect GRE peers (addresses, BGP ASN) | `*`, collected Regions | Follow-up |
| `vpc-lattice:ListServiceNetworks` | VPC Lattice service networks | `*`, collected Regions | Listing |
| `vpc-lattice:ListServiceNetworkVpcAssociations` | The VPCs associated with a service network | `*`, collected Regions | Follow-up |
| `vpc-lattice:ListServiceNetworkServiceAssociations` | The services associated with a service network | `*`, collected Regions | Follow-up |
| `vpc-lattice:ListServices` | VPC Lattice services | `*`, collected Regions | Listing |
| `vpc-lattice:ListTargetGroups` | Lattice target groups | `*`, collected Regions | Follow-up |
| `vpc-lattice:ListTargets` | The targets registered in a target group (the lines from a service to EC2, an ALB or Lambda) | `*`, collected Regions | Follow-up |
| `apigateway:GET` | API Gateway REST, HTTP and WebSocket APIs, and the VPC link to load balancer lines. The service has a single action, so the resource is narrowed to five paths; API keys, policies and stage variables cannot be read | Five API paths (`/restapis`, `/restapis/*/resources`, `/vpclinks`, `/apis`, `/apis/*/integrations`), collected Regions | Listing |
| `globalaccelerator:ListAccelerators` | Global Accelerator (the API exists only in us-west-2) | `*`, pinned to us-west-2 | Listing |
| `globalaccelerator:ListListeners` | An accelerator's listeners | `*`, pinned to us-west-2 | Follow-up |
| `globalaccelerator:ListEndpointGroups` | Endpoint groups (the lines to an ALB, NLB, EC2 instance or Elastic IP) | `*`, pinned to us-west-2 | Follow-up |
| `networkmanager:ListCoreNetworks` | Cloud WAN core networks (the API exists only in us-west-2) | `*`, pinned to us-west-2 | Listing |
| `networkmanager:ListAttachments` | A core network's VPC attachment lines | `*`, pinned to us-west-2 | Follow-up |
| `cloudfront:ListDistributions` | CloudFront distributions, their origins and attached web ACL | `*`, pinned to us-east-1 | Listing |
| `cloudfront:ListVpcOrigins` | CloudFront VPC origins (lines to a private ALB, NLB or EC2); only when a distribution uses one | `*`, pinned to us-east-1 | Follow-up |
| `cloudfront:ListCachePolicies` | The cache policy names in the Behaviors table; only when a distribution uses one. If denied, only the policy ID appears and that distribution is marked as not confirmed | `*`, pinned to us-east-1 | Follow-up |
| `cloudfront:ListOriginAccessControls` | The origin access control (OAC) names in the Origins table; only when an origin uses one. If denied, only the OAC ID appears and that distribution is marked as not confirmed | `*`, pinned to us-east-1 | Follow-up |
| `route53:ListHostedZones` | Route 53 hosted zones | `*`, pinned to us-east-1 | Listing |
| `route53:ListResourceRecordSets` | Alias records pointing at a load balancer or CloudFront (drawn as lines). Record values are not stored | `*`, pinned to us-east-1 | Follow-up |
| `route53:GetHostedZone` | The VPCs associated with a private zone | `*`, pinned to us-east-1 | Follow-up |
| `wafv2:ListWebACLs` | The CloudFront-scope web ACL card (the global row) | `*`, pinned to us-east-1 | Listing |
| `wafv2:GetWebACL` | A CloudFront-scope web ACL's default action and rule count | `*`, pinned to us-east-1 | Follow-up |
| `account:GetAccountInformation` | The account name in the account frame. Called without `AccountId`, so it reads **this account's own** name only and never asks for another account's; once per account during structure collection, and never by the 5-minute observation, whose role does not have this permission | This account only (`arn:aws:account::<this account>:account`), pinned to us-east-1 | Name |

### 1-2. Metrics (monitoring in 지표까지 mode, or utilization on)

| Action | Why it is needed | Resource scope | When denied |
|---|---|---|---|
| `cloudwatch:GetMetricData` | Traffic and drop metrics (the flow indicator on a line) and utilization figures | `*`, collected Regions | That resource's figures stay as "?"; the structure is unaffected |
| `cloudwatch:GetMetricData` | CloudFront metrics (`AWS/CloudFront` exists only in us-east-1) and Global Accelerator usage (`AWS/GlobalAccelerator` can only be queried in us-west-2) | `*`, pinned to us-east-1 and us-west-2 | Only that distribution's or accelerator's figures show "?" |

CloudWatch metric permissions cannot be narrowed by namespace. Only reading metrics is allowed, and metric requests are billed (see Estimated costs in the README).

### 1-3. Reading flow logs (only with `FlowLogReading=ENABLED`)

The policy exists only when the stack parameter enables it; while it is off, the role does not carry these actions. If the flow logs are encrypted with a KMS customer key, the role also needs the decrypt permission in [1-6](#1-6-kms-decryption-for-flow-logs-only-when-flowlogkmskeyarns-is-given).

| Action | Why it is needed | Resource scope | When denied |
|---|---|---|---|
| `logs:DescribeLogGroups` | The class of the log group the flow logs go to (an infrequent-access class is reported as unreadable before any read) | The log groups in this account (names and class only, no content), collected Regions | The flow evidence for that segment is reported as unreadable |
| `logs:DescribeLogStreams` | Choosing the recent log streams | **Only the groups named in `FlowLogGroupNamePrefixes`**, and only in this account; other accounts' groups are blocked by a Deny statement | Same |
| `logs:GetLogEvents` | The records inside the verdict window | Same | Same |
| `s3:GetBucketLocation` | The Region of the bucket flow logs are delivered to | This account's buckets plus those named in `FlowLogCentralBucketArns` | Same |
| `s3:ListBucket` | Listing the date and hour prefixes of the verdict window | Only paths prefixed `AWSLogs/<this account>/vpcflowlogs/`, in this account's buckets plus those named | Same |
| `s3:GetObject` | The flow log objects | Only keys of that flow-log shape, and in another account's bucket only keys containing this account's ID (a Deny statement) | Same |

Log group names are given in full. A short prefix would open application logs as well, so the stack rejects `*` and an empty value.

### 1-4. Evidence detection (with monitoring on)

Configuration reads used to judge whether a segment can be confirmed by a metric or a log. No data is read.

| Action | Why it is needed | Resource scope | When denied |
|---|---|---|---|
| `ec2:DescribeFlowLogs` | Whether VPC and TGW flow logs are enabled, and in which format | `*`, collected Regions | The source line for that evidence reads as not confirmed |
| `ec2:DescribeInstances` | Whether EC2 detailed monitoring is on (the metric interval) | `*`, collected Regions | Same |
| `ec2:DescribeVpnConnections` | VPN tunnel log and BGP log settings | `*`, collected Regions | Same |
| `ecs:ListClusters` | The clusters to judge for Container Insights | `*`, collected Regions | Same |
| `ecs:DescribeClusters` | The Container Insights setting | `*`, collected Regions | Same |
| `eks:ListClusters` | The clusters to judge for EKS network policy logs | `*`, collected Regions | Same |
| `eks:DescribeAddon` | The network policy setting of the `vpc-cni` add-on | `*`, collected Regions | Same |
| `logs:DescribeDeliverySources` | Log delivery configuration (flow logs, load balancer access logs) | `*`, collected Regions | Same |
| `route53resolver:ListResolverEndpoints` | The metric configuration of Resolver endpoints | `*`, collected Regions | Same |

### 1-5. Service quotas (with utilization on)

| Action | Why it is needed | Resource scope | When denied |
|---|---|---|---|
| `servicequotas:GetServiceQuota` | The quota applied to this account (VPC, EC2) | `*`, collected Regions | The calculation falls back to the AWS default quota and says so; an account whose quota was raised then reads higher than reality |
| `servicequotas:GetAWSDefaultServiceQuota` | The AWS default quota when no applied value exists | `*`, collected Regions | If that call returns nothing either, the published default is used and the reason is shown |

### 1-6. KMS decryption for flow logs (only when `FlowLogKmsKeyArns` is given)

| Action | Why it is needed | Resource scope | When denied |
|---|---|---|---|
| `kms:Decrypt` | Needed for S3 to serve a flow-log object encrypted with SSE-KMS. The collector never calls KMS itself | **Only the key ARNs given**, and only when `kms:ViaService` is S3 | The flow logs in that bucket are reported as not decryptable with the KMS key |

The key policy has to allow this role as well. SSE-S3 buckets and KMS-encrypted log groups are read without this value.

## 2. Stack A: the collector in the monitoring account

Four roles in the monitoring account. All of them can be assumed only by a service principal (CodeBuild, EventBridge, Lambda).

### 2-1. Collection role (CodeBuild `BuildRole`)

To read the monitoring account itself it carries **the same actions under the same conditions** as [1-1](#1-1-structure-collection-always), plus the following.

| Action | Why it is needed | Resource scope |
|---|---|---|
| `sts:GetCallerIdentity` | Confirming which account this is | `*` (the API has no resource) |
| `sts:AssumeRole` | Switching into a source account's read-only role | **The single role name `MemberRoleName`** (`arn:aws:iam::*:role/<name>`). Which accounts it can actually enter is decided by that role's trust policy |
| `s3:GetObject` | Reading the deployment package (`nettopo-source.zip`) | Objects in the `SourceLocation` bucket |
| `s3:GetObjectVersion` | Reading a version of the same package (a versioned bucket) | Same |
| `kms:Decrypt` | When the package bucket is KMS-encrypted, S3 decrypts on our behalf | Only when `kms:ViaService` is S3 in this Region |
| `kms:DescribeKey` | As above | Same |
| `s3:PutObject` | Storing the diagram and the snapshots | The artifact bucket. On an installation with monitoring on, writing `index.html` is **denied**, because only the signer publishes it |
| `s3:GetObject` | Reading the previous snapshot (to compare changes) | The artifact bucket |
| `s3:ListBucket` | As above | The artifact bucket |
| `logs:CreateLogStream` | Build logs | This stack's build log group |
| `logs:PutLogEvents` | Build logs | Same |

### 2-2. Monitoring role (Lambda `StateRole`, only with monitoring on)

To read the monitoring account itself it carries **the same actions under the same conditions** as [1-1](#1-1-structure-collection-always) through [1-6](#1-6-kms-decryption-for-flow-logs-only-when-flowlogkmskeyarns-is-given), and the `sts:GetCallerIdentity` and `sts:AssumeRole` of 2-1 with the same scope, plus the following.

| Action | Why it is needed | Resource scope |
|---|---|---|
| `s3:GetObject` | Reading the screen's on/off signal and the latest structure | Five keys in the artifact bucket (`control/desired.json`, `structure-latest.json`, `structure-previous.json`, `state-latest.json`, `snapshots/structure/*`) |
| `s3:ListBucket` | Telling a missing key apart from a denied one | The artifact bucket |
| `s3:PutObject` | Recording state | Only `snapshots/state/*` and `state-latest.json` |
| `logs:CreateLogStream` | Function logs | This function's log group |
| `logs:PutLogEvents` | Function logs | Same |
| `sqs:SendMessage` | Keeping a failed cycle | This stack's dead-letter queue |

### 2-3. Signing role (Lambda `SignerRole`, only with monitoring on)

The role that creates the presigned URLs embedded in the page. S3 evaluates a presigned request with the authority of whoever signed it, so what the browser can do never exceeds this role's permissions. It is kept separate from the monitoring role so that the monitoring function cannot switch its own signal on.

| Action | Why it is needed | Resource scope |
|---|---|---|
| `s3:GetObject` | The state the page reads (signed) | `state-latest.json` |
| `s3:PutObject` | The on/off signal the page writes (signed). **The page's only write** | `control/desired.json` |
| `s3:GetObject` | The conditional write of that signal (checking the current value) | `control/desired.json` |
| `s3:ListBucket` | Letting the page receive a 404 while no state exists yet | The artifact bucket |
| `s3:GetObject` | Reading the unsigned diagram | `unsigned/index.html` |
| `s3:GetObject` | Reading the published diagram, and signing "open the latest page" | `index.html` |
| `s3:PutObject` | Publishing the diagram with the presigned URLs in it | `index.html` |
| `logs:CreateLogStream` | Function logs | This function's log group |
| `logs:PutLogEvents` | Function logs | Same |
| `sqs:SendMessage` | Keeping a failed cycle | This stack's dead-letter queue |

### 2-4. Schedule role (EventBridge `ScheduleRole`)

| Action | Why it is needed | Resource scope |
|---|---|---|
| `codebuild:StartBuild` | Starting a collection build on the schedule (hourly by default) | This stack's single collector project |

### 2-5. Bucket and queue policies

- Artifact bucket: all public access blocked, default encryption, and a Deny for requests not made over TLS. The CloudFront read statement (`AllowViewerDistributionReadIndex`) **is present even when Stack C is not deployed**: a CloudFront distribution **in this account** arriving through origin access control (OAC) can read `index.html` and nothing else (`aws:SourceAccount` and `aws:SourceArn` conditions). By default that is any distribution in this account; giving Stack A's `ViewerDistributionId` the ID of your Stack C distribution narrows it to that one ([7](#7-optional-hardening-conditions-off-by-default)).
- Dead-letter queue: a Deny for requests not made over TLS.

## 3. Stack C: the viewer (optional)

**It creates no IAM role.** CloudFront reads only `index.html` from the artifact bucket, through origin access control (the bucket policy in 2-5 above). An unsigned request, an expired link, or any other path gets a 403.

The access log bucket policy:

| Statement | Effect |
|---|---|
| Deny requests not made over TLS | Every S3 request |
| Deny log reads except for the listed readers | A log line contains the whole signed link, so reading and listing the log objects is denied to everyone except the account root and the roles or users named in `LogReaderPrincipalArns` |

## 4. Actions that are not reads, and why

| Action | Where | Rationale |
|---|---|---|
| `codebuild:StartBuild` | Stack A schedule role | Starts this stack's collector project and nothing else |
| `sts:AssumeRole` | Stack A collection and monitoring roles | The only way into a source account. It is narrowed to a single role name, and the session it returns keeps the read-only restriction |
| `kms:Decrypt`, `kms:DescribeKey` | Stack A collection role, and the flow-log policies in Stacks A and B | S3 calls KMS on our behalf when serving an object (`kms:ViaService` is S3). For flow logs, only the keys given |
| `s3:PutObject` | The three Stack A roles | Only to the artifact bucket in the monitoring account, and each role can write different keys (2-1 to 2-3) |
| `logs:CreateLogStream`, `logs:PutLogEvents` | The three Stack A roles | Only to their own log group |
| `sqs:SendMessage` | Stack A monitoring and signing roles | Only to this stack's dead-letter queue |

The role in a source account (Stack B) has no write action. Even the `kms:Decrypt` above is used only through S3, while reading flow logs.

## 5. What it does not ask for

- No permission to create, change or delete resources, to tag them, or to enable flow logs. This tool only observes.
- The managed `ReadOnlyAccess` and `ViewOnlyAccess` policies are not attached. Attaching a broad managed policy to the source account role would defeat the purpose of this document.
- No IAM users and no long-term access keys. People sign in with temporary credentials, such as those from IAM Identity Center.
- No API a browser calls, no Lambda function URL, and no public bucket.

## 6. Permissions the person deploying needs

Sections 1 to 5 are the permissions **the stacks give the collector**. This section is about the credentials of **the person deploying the stacks**. The Prerequisites in the README and the deployment guide say only "credentials that can create IAM roles, S3 buckets, CodeBuild, EventBridge, Lambda, CloudWatch Logs, SQS and CloudFront". What follows expands that one line into the resources the templates create and the APIs that `deploy.sh` and the quick start commands call.

Use temporary credentials such as an IAM Identity Center permission set (no long-term access keys, see [section 5](#5-what-it-does-not-ask-for)). In practice most people deploy with an `AdministratorAccess` permission set; the tables below are for a security reviewer who wants to build a narrower one. These permissions are used **at deployment time only**; collection afterwards is done by the Stack A roles (section 2).

### 6-1. Monitoring account: Stack A (the collector)

The APIs called by quick start steps 1 to 4 and by `deploy.sh`. Stack A creates four IAM roles, one CodeBuild project, five EventBridge rules, two Lambda functions, three CloudWatch Logs log groups, one SQS queue and one S3 bucket. It does not name the roles, so `--capabilities CAPABILITY_IAM` is enough.

| Area | Actions needed | Where they are used |
|---|---|---|
| CloudFormation | `cloudformation:CreateStack`, `UpdateStack`, `DeleteStack`, `CreateChangeSet`, `ExecuteChangeSet`, `DescribeChangeSet`, `DeleteChangeSet`, `DescribeStacks`, `DescribeStackEvents` | `aws cloudformation deploy` deploys through a change set (`deploy.sh` passes `--no-fail-on-empty-changeset`, so it clears an empty change set with `DeleteChangeSet`). Step 0 of `deploy.sh` confirms the credentials with `DescribeStacks` |
| IAM | `iam:CreateRole`, `DeleteRole`, `GetRole`, `UpdateAssumeRolePolicy`, `PutRolePolicy`, `DeleteRolePolicy`, `GetRolePolicy`, **`iam:PassRole`** | The four Stack A roles (2-1 to 2-4) and their inline policies. Roles are attached to the CodeBuild project, the Lambda functions and the EventBridge rules, so creation fails without `PassRole` |
| S3 (package bucket) | `s3:CreateBucket`, `PutPublicAccessBlock`, `GetPublicAccessBlock`, `PutEncryptionConfiguration`, `PutBucketVersioning`, `PutBucketPolicy`, `PutBucketTagging`, `GetBucketLocation`, `ListBucket`, `PutObject`, `GetObject` | The person deploying creates it in step 1 and uploads `nettopo-source.zip` (plus the Lambda zip when monitoring is on). `deploy.sh` also uploads the templates through this bucket (`--s3-bucket`). When CloudFormation reads the Lambda code from this bucket it uses **the deployer's** `GetObject` |
| S3 (artifact bucket) | `s3:CreateBucket`, `PutBucketPolicy`, `PutEncryptionConfiguration`, `PutBucketVersioning`, `PutLifecycleConfiguration`, `PutPublicAccessBlock`, `GetObject` | The stack creates it (the bucket policy in 2-5). `GetObject` is the `index.html` download in step 4 |
| CodeBuild | `codebuild:CreateProject`, `UpdateProject`, `DeleteProject`, `BatchGetProjects`, **`StartBuild`**, **`BatchGetBuilds`** | Step 3 starts the first collection directly instead of waiting for the schedule, and waits for it to finish |
| EventBridge | `events:PutRule`, `DeleteRule`, `DescribeRule`, `PutTargets`, `RemoveTargets` | The five rules (hourly collection, 5-minute monitoring and so on) |
| Lambda | `lambda:CreateFunction`, `DeleteFunction`, `GetFunction`, `UpdateFunctionCode`, `UpdateFunctionConfiguration`, `AddPermission`, `RemovePermission`, `PutFunctionEventInvokeConfig`, `DeleteFunctionEventInvokeConfig`, `PutFunctionConcurrency`, `DeleteFunctionConcurrency` | The monitoring and signing functions (created only when `StateLambdaSourceKey` is given) |
| CloudWatch Logs | `logs:CreateLogGroup`, `DeleteLogGroup`, `DescribeLogGroups`, `PutRetentionPolicy` | The three log groups. (`deploy.sh` does not read the logs itself: if a collection fails it only prints an `aws logs tail …` command. Running that command needs `logs:StartLiveTail` or `FilterLogEvents` in addition) |
| SQS | `sqs:CreateQueue`, `DeleteQueue`, `GetQueueAttributes`, `SetQueueAttributes` | The dead-letter queue and its policy |
| STS | `sts:GetCallerIdentity` | Step 0 of `deploy.sh` confirms the account ID |
| Tags (only with `deploy.sh --tags`) | `iam:TagRole`, `UntagRole`, `lambda:TagResource`, `UntagResource`, `logs:TagResource`, `UntagResource`, `sqs:TagQueue`, `UntagQueue`, `events:TagResource`, `UntagResource`, `s3:PutBucketTagging` | CloudFormation propagates stack tags to each resource, so each resource's tag actions are needed. The templates themselves contain no `Tags` |

### 6-2. Source account: Stack B (the read-only role)

The role is named (the `RoleName` parameter, `NetworkReadOnlyRole` by default), so **`--capabilities CAPABILITY_NAMED_IAM`** is required. In the console, tick "I acknowledge that AWS CloudFormation might create IAM resources with custom names" on the last screen.

| Method | Permissions needed |
|---|---|
| One account at a time (4-B) | The CloudFormation actions from 6-1 plus `iam:CreateRole`, `DeleteRole`, `GetRole`, `UpdateAssumeRolePolicy`, `PutRolePolicy`, `DeleteRolePolicy`, `GetRolePolicy`. |
| Organizations StackSet (4-C) | With credentials in the Organizations management account or a StackSets **delegated administrator**: `cloudformation:CreateStackSet`, `UpdateStackSet`, `CreateStackInstances`, `ListStackInstances`, `DescribeStackSet*`. A delegated administrator adds `--call-as DELEGATED_ADMIN` to every command. Creating the role inside each target account is done by the service-managed permissions, so no credentials in the target accounts are needed |

### 6-3. Monitoring account: Stack C (the viewer, optional)

It creates no IAM role (section 3). It creates a CloudFront distribution, a key group, a public key, an origin access control and a response headers policy, plus the access log bucket. It does not touch the artifact bucket: the bucket policy that lets CloudFront read it is already part of Stack A (2-5).

| Area | Actions needed |
|---|---|
| CloudFront | `cloudfront:CreateDistribution`, `UpdateDistribution`, `DeleteDistribution`, `GetDistribution`, `TagResource`, `CreatePublicKey`, `UpdatePublicKey`, `DeletePublicKey`, `GetPublicKey`, `CreateKeyGroup`, `UpdateKeyGroup`, `DeleteKeyGroup`, `GetKeyGroup`, `CreateOriginAccessControl`, `UpdateOriginAccessControl`, `DeleteOriginAccessControl`, `GetOriginAccessControl`, `CreateResponseHeadersPolicy`, `UpdateResponseHeadersPolicy`, `DeleteResponseHeadersPolicy`, `GetResponseHeadersPolicy` |
| S3 | The same artifact bucket actions as 6-1 (creating the access log bucket and its policy) |

### 6-4. When deleting

Following the [Cleanup section of the deployment guide](install-guide.en.md#cleanup), besides `cloudformation:DeleteStack` you need `s3:ListBucketVersions`, `DeleteObject`, `DeleteObjectVersion` and `DeleteBucket` to empty and delete the artifact, package and access log buckets. The buckets survive stack deletion, so that the output is kept.

## 7. Optional hardening conditions (off by default)

Left empty, everything in sections 1 to 3 behaves as described. No action is added or removed; only conditions are attached.

- **Stack B `ExternalId`** adds an `sts:ExternalId` condition to the read-only role's trust policy. Giving the same value to **Stack A `MemberExternalId`** makes the collector and the monitoring function pass it on every assume. Given to Stack B only, the assume is denied.
- **Stack B `PrincipalOrgId`** adds an `aws:PrincipalOrgID` condition to the trust policy. The monitoring account has to be in the same organization.
- **Stack A `ViewerOrigin`** narrows the artifact bucket's CORS allowed origin from any CloudFront domain (`https://*.cloudfront.net`) to `https://` plus that one domain; the `null` origin, a page opened from a file, is unaffected. Give the custom domain here when you use one.
- **Stack A `ViewerDistributionId`** narrows the CloudFront read statement in 2-5 to that one distribution.

Conditions that are always present, whatever those values are:

- **`aws:SourceAccount` in the service roles' trust policies.** The four Stack A roles (`BuildRole`, `ScheduleRole`, `StateRole`, `SignerRole`) can be assumed by a service principal only when it is acting for a resource **in this account**, which prevents a confused deputy.
- **`s3:ResourceAccount`.** The statements that read and write the artifact bucket, and the one that reads the deployment package (`nettopo-source.zip`), are allowed only when the bucket is **owned by this account**. A request to someone else's bucket of the same name is denied.
