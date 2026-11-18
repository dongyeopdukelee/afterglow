# AWS readiness

The 2026-11-18 UTC checks exercised structured parsing with Bedrock in
`us-west-2` and the locally served website and API. Earlier observations
cover `us-east-1` and `us-west-2` on 2026-11-15.

The WSL `afterglow` / `afterglow-source` profiles and new WSL zsh sessions
were configured for Oregon. See [inference configuration](../README.md#inference-configuration)
for application defaults and overrides. This setting does not move existing
resources or resolve the historical runtime throttling below.

## Latest live validation: structured intent passes

### Shared local-development configuration

Normal development now defaults to the same Bedrock adapter and application
configuration intended for deployment. `scripts/dev-api.sh` supplies only
WSL authentication and server startup; model settings remain in the shared
application configuration. Ollama is an explicit offline alternative.

The 2026-11-18 development-parity recheck passed all ten live cases with 363
catalog genre labels. The batch reported 34,127 input tokens and 484 completion
tokens. Per-case evaluator time ranged from 239 to 1,667 ms (median 385.5 ms),
including parsing, scoring and request fingerprinting. Every case reported
the request fingerprint
`22ac008957900599c13afa15375186ff68050e71af5824d6c539ff1192380874`.
These are measured results for this batch, not an accuracy or latency guarantee.

A separate real-HTTP check through the new WSL launcher, Uvicorn, Bedrock and
the read-only imported catalog returned ten correct cards for 1990s comedies,
post-2000 thrillers, and literal `dog` title searches. A detail request loaded
the synopsis. Plot, OR, and greeting requests returned 422 without cards.
The temporary smoke API was stopped afterward.

`uv run --locked --no-env-file movie-db-llm inference-config` reports the
same fingerprint used by the live evaluator. It covers the model, endpoint,
generation settings, prompt examples, schema, and genre labels in the outgoing
request. A fixed placeholder takes the place of the user query. Comparing
movie results also requires the same catalog snapshot.
No public application has been deployed, so hosted behavior still needs
post-deployment verification with the same cases and configuration.

### Repeat the HTTP acceptance cases

Use the same imported CMU snapshot and fingerprint for local and hosted
checks. Set `API_BASE` to the API under test. Send each JSON body verbatim to
`POST /recommendations` with `Content-Type: application/json`. For example,
the first request against the local API is

```sh
API_BASE=http://127.0.0.1:8000
curl -sS -w '\nHTTP %{http_code}\n' "$API_BASE/recommendations" \
  -H 'Content-Type: application/json' -d '{"query":"Comedies from the 1990s"}'
```

| JSON body | HTTP | Expected result on this snapshot |
| --- | --- | --- |
| `{"query":"Comedies from the 1990s"}` | 200 | Every card includes `Comedy` and has a release year from 1990 through 1999 inclusive. |
| `{"query":"Thriller movies after 2000"}` | 200 | Every card includes `Thriller` and has a release year greater than 2000. |
| `{"query":"Movies whose titles contain dog"}` | 200 | Every title contains the literal substring `dog`, ignoring case. |
| `{"query":"90s comedy movies about dogs"}` | 422 | The detail identifies an unsupported constraint and the response has no `items`. |
| `{"query":"Horror or Comedy movies"}` | 422 | The detail identifies an unsupported constraint and the response has no `items`. |
| `{"query":"hello"}` | 422 | The detail asks for a supported filter and the response has no `items`. |

Each 200 response must have ten distinct movie cards in `items`, ordered by
title and ID. Each card contains exactly `id`, `title`, `release_year` and
`genres`. Take the first comedy card's `id` and request `GET /movies/{id}`
at the same `API_BASE`. Expect 200, the same four card fields and values, and
a nonempty `synopsis` string.

### Earlier validation of the selected model

The owner's refreshed default login was valid for the expected account, but
the project's separate WSL login cache had expired. Reusing that matching
session in the private WSL cache restored the existing `afterglow` role;
no Windows files, IAM policies or account settings were changed. Use the
[explicit project login command](website.md#deployment-administrator-profile)
to avoid refreshing a different profile/cache next time.

`mistral.ministral-3-8b-instruct` passed all ten live acceptance cases with
the imported catalog's 363 distinct genre labels. Earlier GPT-OSS and
Ministral 3B runs failed the format or intent checks. With the original
plain-JSON examples, the strict parser rejected text-only replies and quoted
years. See the [hosted prompt protocol](website.md#hosted-intent-parsing-not-yet-deployed).

One passing ten-request batch reported 34,127 input tokens and 489 completion
tokens. Per-case evaluator time ranged from 287 to 1,250 ms (median 390 ms),
including credential resolution, the network call and scoring. This is a small
development sample, not an SLA, a general accuracy guarantee, or total session
spend. Other diagnostic calls are not included in those counts.

Real HTTP requests through Vite, Uvicorn, Bedrock and the imported SQLite
catalog also passed: 1990s comedies, post-2000 thrillers and literal `dog`
title searches returned ten matching cards each. Full synopses loaded from
the detail endpoint. Plot, OR and greeting requests returned 422 without
movie cards. These are API/proxy smoke checks, not a public deployment or a
complete browser/model evaluation.

See the [public release gates](#gates-before-continuing-to-public-release)
for the outstanding work.

## Working inference route: Bedrock Mantle

Earlier connectivity probes sent standalone IAM-signed requests to
`https://bedrock-mantle.us-west-2.api.aws/v1/chat/completions` in Oregon.

- `openai.gpt-oss-20b`: HTTP 200, returned `OK.`; 71 prompt tokens and
  31 completion tokens, 102 total.
- `mistral.ministral-3-3b-instruct`: HTTP 200 with generated text; 18 total
  tokens. It did not follow the tiny `Reply with OK.` prompt exactly.
- `openai.gpt-5.6-luna`: rejected with account-specific access denial despite
  positive applied Mantle quotas. Listing a model or quota is not acceptance.

AWS documents the model-specific endpoint/ID in the
[GPT-OSS model card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-openai-gpt-oss-20b.html)
and the separate APIs and quota systems in its
[endpoint comparison](https://docs.aws.amazon.com/bedrock/latest/userguide/endpoints.html).
The future runtime action is `bedrock-mantle:CreateInference`; the local
administrator role is not the future Lambda execution role.

The subsequent [structured-intent evaluation](#latest-live-validation-structured-intent-passes)
exercised the application parser with the full catalog vocabulary.

## Historical runtime recheck: Converse inference blocked

At 09:31–09:36 UTC on 2026-11-15, the WSL `afterglow` role authenticated
successfully and the configured/default client region was Oregon. Nova Micro
and Titan V2 still reported model authorization, agreement, entitlement and
regional availability. Actual requests did not succeed:

- The Nova Micro parsing evaluation stopped on its first query with
  `status: blocked` and exit code 1.
- A tiny 16-output-token, no-tools Nova request returned
  `ThrottlingException: Too many tokens per day`.
- The short Titan V2 embedding request returned
  `ThrottlingException: Too many requests` and no embedding result.
- The direct Ministral 3B parsing evaluation also stopped on its first query
  with `status: blocked` and exit code 1.

Individual `GetServiceQuota` account-level lookups in `us-west-2` then returned
zero for Nova Micro RPM, TPM and daily tokens; Titan V2 RPM; and Ministral 3B
RPM and TPM. GPT-OSS 20B reported model authorization and availability, but its
individual applied RPM lookup also returned zero, so it was not invoked. A
fresh first-page `ListServiceQuotas` result independently showed Nova Micro
cross-region TPM as zero. These results supersede the earlier nonzero bulk
snapshot below; the cause of the change has not been established.

Use the account-specific applied value for each selected quota and a successful
bounded invocation to establish readiness. For example:

```sh
AWS_CONFIG_FILE=/home/dukelee/.config/afterglow/aws/config \
AWS_SHARED_CREDENTIALS_FILE=/home/dukelee/.config/afterglow/aws/credentials \
AWS_LOGIN_CACHE_DIRECTORY=/home/dukelee/.config/afterglow/aws/login-cache \
  aws --profile afterglow --region us-west-2 service-quotas get-service-quota \
    --service-code bedrock --quota-code L-DC7FF66C \
    --query 'Quota.{name:QuotaName,value:Value,scope:QuotaAppliedAtLevel}'
```

AWS documents
[`GetServiceQuota`](https://docs.aws.amazon.com/servicequotas/2019-06-24/apireference/API_GetServiceQuota.html)
as retrieving the applied quota value. A catalog authorization result or an
earlier bulk listing is not a successful inference test.

No retry loops, quota increases, subscriptions, or hosting resources were
created during this recheck. For the subsequent Mantle results, see
[Working inference route](#working-inference-route-bedrock-mantle); for the
preview's provider and lifecycle, see
[interactive preview](website.md#interactive-preview-during-development).

## Historical existing-capacity assessment

An earlier read-only inventory on 2026-11-15 UTC compared `us-east-1` and
`us-west-2`. The deployment role assumed successfully. No quota increases,
support cases, model subscriptions, or hosting resources were created.

The relevant Nova/Titan limits in Virginia were zero. Oregon reported these
values in its bulk listing. This historical snapshot is **not current usable
capacity**; see the later individual applied-quota lookups above:

| Candidate / quota | Value | Quota code |
| --- | ---: | --- |
| Nova Micro cross-region requests/minute | 4,000 | `L-3F110E0F` |
| Nova Micro cross-region tokens/minute | 8,000,000 | `L-DC7FF66C` |
| Nova Micro model invocation tokens/day | 5,760,000,000 | `L-D2912E70` |
| Titan Text Embeddings V2 requests/minute | 6,000 | `L-26C560CE` |
| Ministral 3B 3.0 direct requests/minute | 10,000 | `L-DCA37E91` |
| Ministral 3B 3.0 direct tokens/minute | 100,000,000 | `L-8A4BEE90` |
| Cross-model tokens/day (account, Oregon) | 150,000,000 | `L-E3F10727` |

These are different quota scopes, not additive allowances. For text embeddings,
plan request rate against the RPM quota rather than the listed TPM value.
Batch job-size/record limits were excluded from interactive capacity decisions.

This inventory initially favored Nova Micro for parsing and Titan V2 for
embeddings. The subsequent [runtime recheck](#historical-runtime-recheck-converse-inference-blocked)
supersedes that assessment. A separate 16-output-token, no-tools Ministral 3B
Converse probe also returned `ThrottlingException: Too many tokens`.

Automatic retries were disabled for these probes. The tiny no-tools requests
distinguish the runtime block from a large prompt or a forced-tool schema issue;
they do not establish its underlying cause. No live model-quality pass, vector
quality measurement or latency benchmark was obtained in this assessment.
Cohere Embed English was also inspected, but its agreement reported
`NOT_AVAILABLE`; it was not invoked or subscribed.

### Historical Converse routing options

Afterglow's low expected traffic does not require a multi-region application.
Bedrock cross-region inference lets AWS route an individual model call among
the destinations in a profile. It does not deploy copies of our API or website.

[Nova Micro](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-amazon-nova-micro.html)
supports direct inference in Virginia, but from Oregon AWS offers the US
profile `us.amazon.nova-micro-v1:0`, routing within the US. Its Oregon source
quota is separate from Virginia's quota. Titan V2 can be called directly in
Oregon. A small direct parsing alternative is
[Ministral 3B](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-mistral-ai-ministral-3b.html),
but its Converse probe also failed. These historical routing options do not
describe the [Mantle route](#working-inference-route-bedrock-mantle).

AWS documents no additional geographic
[cross-region routing fee](https://docs.aws.amazon.com/bedrock/latest/userguide/cross-region-inference.html);
model charges are based on the source region. Oregon's Nova Micro on-demand
Price List entries were checked against catalog version `20261111013037`:
USD 0.035 / 1M input tokens and USD 0.14 / 1M output tokens. The published
[Ministral 3B standard rate](https://aws.amazon.com/bedrock/pricing/) is
USD 0.10 / 1M input tokens and USD 0.10 / 1M output tokens in Oregon.
These are rates, not observed successful-request costs or a complete hosting
budget. No provisioned throughput or other capacity commitment is required
by the proposed approach.

## Earlier Virginia control-plane checks

- The WSL `afterglow` profile successfully assumes the project administrator
  role. It is separate from the future application runtime identity.
- Bedrock lists Nova Micro and Titan Text Embeddings V2. Account-specific
  availability reports `AUTHORIZED`, agreement/entitlement `AVAILABLE`, and
  regional availability `AVAILABLE` for both candidates.
- The `us.amazon.nova-micro-v1:0` geographic inference profile is `ACTIVE`.
- Deterministic HTTP tests exercise the Bedrock provider boundary, real SQLite
  retrieval, AND-combined filters, exact year semantics, and service failures
  without calling AWS. They do not establish natural-language accuracy.

## Earlier Virginia live checks blocked

- A bounded Nova Converse request was denied because AWS reports that the
  account is currently being verified. The service message says this normally
  takes less than two hours and names `aws-verification@amazon.com` if it
  persists beyond that period. No IAM permissions were expanded to bypass it.
- An independent, short Titan embedding request returned `ThrottlingException`.
  No embedding or semantic-quality result was produced. Do not conflate that
  response with proof that the account-verification issue is resolved.
- Both probes disabled automatic retries. No vector index, hosting stack,
  provisioned model capacity, or public paid-inference endpoint was created.
- A read-only Service Quotas check in this session also found zero applied
  inference capacity for the candidate models:

  | Quota | Applied value | Quota code |
  | --- | ---: | --- |
  | Nova Micro cross-region tokens/minute | 0 | `L-DC7FF66C` |
  | Nova Micro cross-region requests/minute | 0 | `L-3F110E0F` |
  | Nova Micro invocation tokens/day | 0 | `L-D2912E70` |
  | Titan Text Embeddings V2 requests/minute | 0 | `L-26C560CE` |
  | Titan Text Embeddings V2 tokens/minute | 0 | `L-DE641971` |

  No quota increases or support requests were submitted. Availability and IAM
  authorization alone do not establish usable inference capacity.
- The checked-in opt-in parsing evaluation was rerun at 06:40 UTC. It stopped
  on its first query, returned structured `status: blocked` output and exit
  code 1, and did not produce a model-quality pass.

## Historical Nova/Titan candidate model prices

AWS Price List API, catalog version `20261111013037`, on-demand inference in
US East (N. Virginia):

| Candidate | Input USD / 1M tokens | Output USD / 1M tokens |
| --- | ---: | ---: |
| Nova Micro | 0.035 | 0.14 |
| Titan Text Embeddings V2 | 0.02 | Not applicable |

See [Bedrock pricing](https://aws.amazon.com/bedrock/pricing/). These are
public rates, not a quote, account-specific discount, or an observed bill.

A **historical Nova/Titan scenario**, not a Mantle estimate or measured usage:
720 monthly searches with 5,000 parsing
input tokens, 256 parsing output tokens, and 250 query-embedding tokens per
search gives USD 0.1518048 parsing + USD 0.0036 query embeddings =
USD 0.1554048/month. Calculated with Python `Decimal`; no free-tier credits
were subtracted. Actual input includes the catalog vocabulary and examples.

This excludes initial catalog embeddings, storage, vector queries, hosting,
logs, quotas, DNS, taxes, and abuse traffic. The complete deployment estimate
and the USD 10/month budget check are **not yet verified**. In particular,
this scenario is not a spending cap; server-enforced quotas remain required.

## Gates before continuing to public release

1. Verify a bounded embedding request using existing account capacity. Check
   the applied quota for the model and source region and investigate any
   runtime rejection.
2. Evaluate semantic synopsis requests against known films, including
   requests that also specify a genre, year or title filter.
3. Validate vector-store filtering. Before provisioning, estimate parsing,
   embedding, initial ingestion and hosting costs against the
   [demo budget](decisions.md#product-scope).
4. Implement and verify fail-closed allowances before exposing paid searches
   to public visitors.
5. Verify the public browser-to-catalog path, then select the custom domain.

Do not publish a recommendation-quality pass, latency measurement, or token
usage measurement until the corresponding live request has succeeded.

## Repeat the bounded live parsing check

Run this paid development check manually outside CI. The required `--live`
flag authorizes up to ten model calls. The script exits nonzero on the first
blocked or incorrect result. It checks decade bounds, all five year comparisons,
literal title punctuation, unsupported plot/OR requests and an unconstrained
greeting against fixed expected behavior.
Each attempted parse records the batch fingerprint, parser latency, and
provider-reported token counts, including usage returned with an incomplete
model response. Missing usage is recorded as `null`.

To repeat the catalog-backed run, set `DATABASE_URL` to the migrated,
imported database. The command reads its distinct genre labels and uses the
[private WSL profile](website.md#deployment-administrator-profile).

```sh
scripts/dev-api.sh --check
```

The evaluator requires the active Bedrock provider and opens SQLite read-only.
It stops before AWS client setup if the file or schema is missing, or if the
catalog lacks any of the three required genres (`Comedy`, `Horror`, and
`Thriller`).
