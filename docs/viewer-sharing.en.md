# Ways to share the diagram: a note for security reviewers

This document describes, from a security reviewer's point of view, the **five ways to share** the network diagram (`index.html`) the collector produces. For each one it records who can open it, whether access is attributable, how to revoke it, how long it lives and where the address ends up, with the template file that backs each statement.

[한국어](viewer-sharing.md)

- **The product provides ways 1 to 3** (the default output of Stack A, S3 presigned URLs, and Stack C). Ways 4 and 5 are things **you build in your own account**; they are not in the product templates, and the configurations described for them have **not been verified** by us (section 7).
- Properties of the AWS services themselves (the maximum lifetime of a presigned URL, for example) follow the AWS documentation and are marked as such.
- The IAM permissions each stack creates are in [Required IAM permissions](required-permissions.en.md).

## 0. First: what one diagram file contains (common to all five ways)

A security review should look at the **file** rather than the link. Whichever way it is opened, the recipient ends up with the same `index.html`.

1. **The network layout itself.** Account IDs, CIDR blocks, public IP addresses and the connection structure are inside the file. Anyone who receives it once holds that content **permanently**. None of the ways to "take it back" below can delete a file that has already been received.
2. **On a stack with live monitoring, four signed S3 addresses.** The signing function refills the address slot in the file and republishes it every 5 minutes, and again right after a successful structure collection (the signer in `infra/stack-a-collector.yaml` and `infra/state-lambda/signer.py`).
   - Read state: `GET state-latest.json`
   - Write the monitoring on/off signal: `PUT control/desired.json`
   - Read the monitoring on/off signal: `GET control/desired.json`, which an open window reads every minute so that it follows whatever another window switched on, changed or switched off
   - Open the latest page: `GET index.html`, a navigation address used only when someone clicks "open the latest page"
   - All four are signed by the **signing function's role** (`SignerRole`). Because S3 evaluates a presigned request with the authority of whoever signed it, what these addresses can do never exceeds that role's permissions over three keys. Reading the signal needs no extra permission, because the role could already read that key.
   - Their lifetime is the shorter of 12 hours and the remaining lifetime of the signing function's credentials.
3. **On a stack without monitoring** the address slot is empty and the page's CSP `connect-src` is `'none'`. The page makes no outbound requests and carries no signed addresses.

**What this means in practice**: on a stack with monitoring on, someone who is sent the file can, for up to about 12 hours after receiving it, (1) see the most recent state figures, (2) switch monitoring on or off, and (3) move to the latest page as of that moment. When several windows have the same stack's page open, the others follow the setting of the window that last switched it on or changed it, and switching it off in any window switches it off everywhere. Because switching it on costs money, there are two ceilings. The page keeps monitoring on only for the time chosen when it was switched on (30 minutes, 1, 2, 4 or 8 hours; 4 by default), and closing the window ends the renewals so it stops within about 20 minutes (`renderer/flow.js`). The server (the state function) stops on its own 8 hours after the last on/off signal was written, regardless of the page (`infra/state-lambda/handler.py`), which is the ceiling that stops a tampered page from keeping it on forever. After 12 hours the diagram remains and only the figures stop.

## 1. At a glance

| Way | Who can open it | Is access attributable | Taking it back | Lifetime | Where the address or file ends up | Provided by the product |
|---|---|---|---|---|---|---|
| ① Download from S3 | Anyone with IAM permission in the monitoring account to read that bucket | Only if CloudTrail **S3 data events** are enabled, and then under the name of the person who downloaded it | Revoking the IAM permission stops future downloads; a file already received cannot be taken back | The file is permanent; the signed addresses inside it last up to about 12 hours | The recipient's machine, email, chat attachments | Provided; step 3 of the deployment guide |
| ② S3 presigned URL | Anyone holding the address | Recorded, but under the name of the **signing principal**, plus the request IP | Cutting off the credentials that signed invalidates every address they signed | Up to 7 days from the CLI; with temporary credentials, until that session ends | Chat, email, browser history, and S3 server access logs if enabled | The addresses inside the page are provided; an admin-issued link is unverified |
| ③ Stack C signed link | Anyone holding the link (the single path `/index.html`) | Only the IP, time and browser; the person cannot be identified | Rotating the key invalidates **every** link signed with it; a single link cannot be cut | Chosen by the administrator, with no maximum | **The CloudFront access log, including the full signature**, chat, browser history | Provided and verified |
| ④ Identity Center permission set | Internal users assigned that permission set | With data events enabled, **under the user's own name** | Removing the assignment takes effect at once; a file already received cannot be taken back | The sign-in session (1 to 12 hours); the file received is permanent | The recipient's machine | Your configuration, unverified |
| ⑤ Internal network only | From the internal network (VPN or Direct Connect), plus IAM permission or a signed address | As for ① and ② | As for ① and ②, plus requests from outside the network are denied | As for ① and ② | A received file can still leave the network | Your configuration, unverified, cannot be combined with Stack C |

## 2. Way by way

### ① Download from S3: the default output of Stack A

- **What it is**: an administrator downloads `index.html` with `aws s3 cp` and opens it. Stack A prints that command as an output. The bucket has all four public access blocks, encryption at rest and a denial of requests not made over TLS, and it has no public address (`ArtifactBucket` in `infra/stack-a-collector.yaml`).
- **Who can open it**: the bucket policy does not narrow principals; its only statements are the TLS denial and the CloudFront allowance. So **anyone in the monitoring account with IAM permission to read that bucket** can download it. That includes anyone holding broad read permissions, such as the AWS managed `ReadOnlyAccess`, which covers reading S3.
- **Who opened it**: none of Stacks A, B or C creates a CloudTrail trail or S3 server access logging. A download (`GetObject`) is a CloudTrail **data event** and is not recorded by default (per the AWS documentation). Enable data events on that bucket and **the name of the person who downloaded it** is recorded. Who they passed the file to afterwards is recorded nowhere.
- **Taking it back**: removing the IAM permission stops **future** downloads. A file already received cannot be recovered. The signed addresses inside it expire by themselves after up to about 12 hours (section 0).
- **Lifetime**: the file contents are permanent; the live figures last up to about 12 hours from the moment it was received.
- **Where the address ends up**: there is no address. Instead the file itself ends up on laptops, in email and in chat attachments.
- **When it fits**: the audience is a handful of administrators who can already sign in to the monitoring account.

### ② S3 presigned URL: the default path, without Stack C

There are two kinds.

- **(a) The addresses already inside the page**, the ones in section 0. Nobody issues them by hand. The page only offers the "open the latest page" button when that address is `/index.html` **on the same origin** as the state address, which means that forwarding a file received through ① forwards these addresses with it.
- **(b) A link an administrator issues separately**, by creating a presigned URL for `index.html` with their own credentials and sending it. The deployment guide does not describe this and we have **not verified it**. In the recipient's browser the page opens on the bucket origin (`https://<bucket>.s3.<Region>.amazonaws.com`), and because the page's `connect-src` is that same origin, **we infer from the code** that the live figures would work as same-origin requests. If the host in the address is not the regional, virtual-hosted form (a global endpoint, say) the origin differs and CORS may block it, which is the same reason the signing function pins the regional endpoint.
- **Who can open it**: anyone holding the address. There is no sign-in. A presigned URL is a bearer token (per the AWS documentation).
- **Who opened it**: the request is processed with **the authority of whoever signed it**. Even with data events enabled, the name recorded is the signing function's role for (a) and the issuing administrator for (b); the person who actually opened it appears only as a **request IP**.
- **Taking it back**: there is no way to cut off a single address. If the credentials used to sign are cut off (session revoked, deleted or deactivated), **every** address signed with them becomes invalid (per the AWS documentation). Removing the signing principal's permissions has the same effect. Doing this to (a) stops everyone's live figures at the same time.
- **Lifetime** (per the AWS documentation): up to 7 days from the CLI or an SDK, 1 minute to 12 hours from the console. Signed with **temporary credentials** (a role, or an Identity Center sign-in) it becomes invalid **when that session ends**, whatever lifetime was requested. A 7-day link requires a long-term access key, and the deployment guide tells you not to create one.
- **Where the address ends up**: chat, email, browser history, and S3 server access logs if you enabled them, where the whole request address is recorded (per the AWS documentation). Stack A does not enable server access logging. A link-preview bot in a chat tool may open the address on your behalf (a general caution).
- **When it fits**: showing the diagram to someone without an AWS account **for a few hours**.

### ③ Stack C CloudFront signed link: optional

- **What it is**: CloudFront serves **one key only**, `index.html`, read-only, out of the private bucket. The bucket policy allows a CloudFront distribution in the same account to read `index.html` and nothing else, and the distribution accepts only GET and HEAD, does not cache, answers an unsigned request with 403 and accepts HTTPS only (`infra/stack-c-viewer.yaml`). Links are created with `openssl` on the administrator's machine (`infra/issue-viewer-link.sh`).
- **Who can open it**: anyone holding the link. No sign-in. The only path a link opens is `/index.html`. Verified: an unsigned request, an expired link, another path, a tampered link and a write request all return 403, and so does going to S3 directly.
- **Who opened it**: **the person cannot be identified.** That is a trade-off the design accepts. The access log records the request IP, the time and browser information, and cookies are not logged.
- **Taking it back**: rotating the key invalidates every link signed with it. There is no way to cut off a single link. The rotation procedures are in section 4.
- **Lifetime**: chosen by the administrator. The script has no default: it requires a number of days, and sets no maximum.
- **Where the address ends up**:
  - **The CloudFront access log records the whole link, signature included.** Kept for 90 days by default (`AccessLogRetentionDays`). The log bucket policy denies reads to everyone except the account root and the roles or users named in `LogReaderPrincipalArns`. If an auditor needs to read the logs, name their role there.
  - Chat, email, browser history, and link-preview bots (a general caution).
  - It does not leak through the referrer: the response carries `Referrer-Policy: no-referrer`, and `X-Robots-Tag` keeps it out of search engines.
- **When it fits**: showing the diagram to people without an AWS account **for a long time**, in a team that does not need to know who looked.

### ④ Reading S3 directly with an Identity Center permission set: your configuration, outside the product

- **What it is**: you create a permission set in the monitoring account that grants only "read `index.html` in this bucket" and assign it to the people who need to look, for example `s3:GetObject` on `arn:aws:s3:::<bucket>/index.html`. They then either `aws s3 cp` as in ① with their own sign-in, or open it in the S3 console, which creates a presigned URL under their session (AWS behaviour; not verified by us).
- **Who can open it**: the internal users assigned the permission set. The permission set is not the **only** way in, though, because the bucket policy does not narrow principals: anyone who already holds broad S3 read permissions can still download the file.
- **Who opened it**: this is the **only one of the five ways that records a person's name**, and only if you enable CloudTrail S3 data events on this bucket (per the AWS documentation; data events are billed separately). The live requests made after the page opens are still recorded under the signing function's role (section 0).
- **Taking it back**: removing the assignment blocks the next request. A file already received cannot be recovered.
- **Lifetime**: the sign-in session (1 to 12 hours, as configured on the permission set). A file received is permanent.
- **When it fits**: an organisation where everyone who needs to look uses internal AWS sign-in (Identity Center). Stack C is not needed.

### ⑤ Internal network only, with an S3 interface endpoint and a bucket policy: your configuration, outside the product

- **What it is**: you add an S3 interface VPC endpoint and a bucket policy statement denying any request that did not come through that endpoint (`aws:SourceVpce`), so that the file can only be downloaded or opened from inside the network (VPN or Direct Connect). **We have neither built nor verified this.** What follows are cautions derived from the templates.
- **Blocking the whole bucket stops collection.** Everything that writes to this bucket arrives from outside your VPC, through the public S3 endpoint, because the CodeBuild project and the Lambda functions in Stack A have no VPC configuration. The principals you must not block: the collection role (`BuildRole`), the monitoring role (`StateRole`), the signing role (`SignerRole`) and, if you use Stack C, CloudFront.
- **Exempting the signing role by name defeats the network restriction.** The signed addresses inside the page are processed under the signing role's name (section 0), so exempting that role exempts **anyone outside the network** who holds the file. The exception has to be drawn by "key plus operation" rather than by principal: what the browser does is read `state-latest.json`, write `control/desired.json` and read `index.html`; what the signing function itself does is read `unsigned/index.html` and write `index.html`. They do not overlap, so attaching the endpoint condition to only the first three **looks possible, but we have not verified it**.
- **It cannot be combined with Stack C.** CloudFront reads the bucket through the public endpoint. Exempting CloudFront would make the links work from the internet.
- For an internal browser to send `<bucket>.s3.<Region>.amazonaws.com` to the endpoint, the endpoint's private DNS and internal DNS forwarding are both needed (per the AWS documentation). Changing the host in the address breaks the signature.
- **The network protects downloading and the live figures, not the contents of the file.** A file downloaded inside the network can leave on a laptop (section 0-1).
- **When it fits**: an organisation that has to forbid any access from outside. The design and the verification are yours.

## 3. The live figures do not go through CloudFront, so a custom domain belongs in Stack A `ViewerOrigin`

- The page calls state reads and signal writes **directly, through the S3 signed addresses inside it**. Stack C serves `index.html` and nothing else. Verified: every live request goes straight to the S3 regional origin, and CloudFront is used once, for `index.html`.
- A page opened from another origin therefore needs S3's CORS allowance. The allow list is `"null"` (a page downloaded and opened over `file://`) plus one origin. That origin is `https://*.cloudfront.net` (Stack C's default domain) by default, and giving Stack A's `ViewerOrigin` replaces it with that single value (the `ArtifactBucket` CORS rule in `infra/stack-a-collector.yaml`).
- Attaching a custom domain to Stack C (`CustomDomainName`) makes that domain the page's origin. Set Stack A `ViewerOrigin` to `https://<custom domain>` and the live figures keep arriving. Leave it empty and, because the domain is not in the CORS list, **the diagram appears but the live figures are blocked**. `ViewerOrigin` takes a single origin, so naming the custom domain blocks the figures for a page opened on the default `*.cloudfront.net` domain instead. The template's parameter description says the same.
- This is judged from **the code**: the CORS list plus the page's direct requests. We have not attached a custom domain and measured it.
- CORS grants no access: an unsigned request is a 403 whatever the origin.

## 4. Handling the Stack C private key

A private key amounts to **permission to issue any number of viewing links, with no lifetime limit** (see the lifetime row of ③ in section 2). Treat it accordingly.

**Where to keep it**

- It is created on the administrator's machine and **never uploaded to AWS**, which is a design decision; only the public key goes into the stack. `deploy.sh` writes the private key to `out/viewer-k1.pem` with mode 600.
- Keep the number of people holding it to one or two.
- **If you created it in CloudShell**, remember that the CloudShell home directory persists in AWS-managed storage after the session ends (per the AWS documentation). Delete it after signing, or create it on the administrator's machine in the first place.
- **A backup is not required.** If you lose the key, rotate to a new one (below); the only cost is that the old links stop working and have to be reissued. If your organisation's rules require keeping keys, use an internal secret store.

**Two kinds of rotation.** CloudFront cannot change the value of a public key once uploaded, so two key slots (`k1` and `k2`) are used alternately. After a rotation the `Key-Pair-Id` in a link changes.

- **Planned rotation, with overlap**: `./deploy.sh --viewer --rotate-key --days N`. The new key goes into the empty slot and the old key stays in the key group (Stack C `PreviousPublicKeyPem`). Old links keep working until they expire, and new links are signed with the new key. Once the old links have all expired, `./deploy.sh --viewer --retire-old-key` removes the old key. From the record of links issued on this machine (`~/.local/state/nettopo/viewer-links.log`, which stores neither links nor signatures), the script tells you the latest expiry among the links signed with the old key.
- **Leak response, immediate**: `./deploy.sh --viewer --rotate-key --no-overlap --days N`. The old key is removed in the same update, so **every** link issued so far returns 403. Verified: right after the rotation, old 7-day and 10-year links returned 403, a new link returned 200, and the stack update took about 30 seconds.

**What a rotation cannot invalidate**

- Pages already open, and files already saved (section 0-1).
- The S3 signed addresses inside such a page (up to about 12 hours), which are signed by the signing function's role and have nothing to do with the CloudFront key (section 0-2).
- The S3 presigned URLs of ②, and files received through ① or ④.

**When to rotate (recommended).** Immediately, without overlap, whenever you suspect a leak; and whenever the group of people holding links changes (someone leaves, an engagement with an outside partner ends). Since a single link cannot be cut off, "rotate when the group changes and reissue to those who remain" is the only way to take links back. Keep link lifetimes short, 30 days for example, and decide them together with the access log retention period. That is an operational recommendation, not a product default.

**How links are signed.** The script creates CloudFront signed URLs with an RSA 2048-bit key and **SHA-256**, and adds `Hash-Algorithm=SHA256` to the link (`infra/issue-viewer-link.sh`). Without that parameter CloudFront verifies with SHA-1 and returns 403.

## 5. Outside the scope of this product

- **A sign-in layer in front of CloudFront** (Cognito with Lambda@Edge, say), ALB with OIDC, Amplify access control, WAF IP restrictions, and pushing the diagram into chat or email after a collection: this product neither builds nor tests any of them. You may put them on top of Stack C at your own discretion, but the product does not recommend them and does not guarantee that they work.
- Such a layer usually comes with a custom domain, in which case you have to name that domain in Stack A `ViewerOrigin` as described in section 3 for the live figures to arrive.

## 6. Choosing: decide by who is going to look

| Audience | Recommended | Why |
|---|---|---|
| A few administrators who sign in to the monitoring account | ① Download | No extra configuration |
| Everyone uses internal AWS sign-in (Identity Center) | ④ Permission set with data events | **The only way that records who received it, by name.** Stack C not needed |
| People without an AWS account, for a few hours | ② Presigned URL | No extra configuration, and it expires on its own |
| People without an AWS account, for a long time, no need to identify them | ③ Stack C | Every time the link is opened it serves the latest diagram. Narrow who may read the logs and handle the key as in section 4 |
| Any access from outside is forbidden | ⑤ Internal network only | Your design. Read the cautions in ⑤ of section 2 first. Cannot be combined with Stack C |

Common to every way: a file that has been received cannot be taken back, and on a stack with monitoring on it contains four signed addresses lasting up to about 12 hours (section 0).

## 7. What we have not verified

1. The effective lifetime of the signed addresses inside the page (by design up to 12 hours, possibly shorter depending on the signing function's credentials).
2. Live behaviour on a page opened through an administrator-issued presigned URL, ② (b).
3. Whether the signing function signs again with new credentials after its role's session is cut off.
4. The ④ permission set configuration, opening the file from the S3 console, and the name recorded in data events.
5. The complete ⑤ internal-network bucket policy, including a condition design that does not stop collection.
6. That `ViewerOrigin` really makes the live figures arrive on a custom domain, and that leaving it empty blocks them (section 3 argues from the code).
7. How signature strings are recorded in S3 server access logs and CloudTrail, and what a chat preview bot reaches.
