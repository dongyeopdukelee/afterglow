# AWS readiness

Checked on 2026-11-15 UTC in `us-east-1` and `us-west-2`. These are observations,
not a claim that public deployment or live model-quality validation is complete.

The default AWS region is now `us-west-2` for the application, the WSL
`afterglow` / `afterglow-source` profiles, and new WSL zsh sessions. Explicit
region overrides still work. This setting does not move existing resources,
enable hosted inference by default, or resolve the runtime throttling below.

## Existing-capacity assessment

An additional read-only inventory on 2026-11-15 UTC compared `us-east-1` and
`us-west-2`. The deployment role still assumes successfully. No quota increases,
support cases, model subscriptions, or hosting resources were created.

The relevant Nova/Titan limits in Virginia remain zero. Oregon reports these
existing applied limits:

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

Quota-based preference remains Nova Micro for parsing and Titan V2 for
embeddings, using Oregon as a candidate source region. Both report model
authorization, entitlement and agreement available there. This is **not a
verified working configuration**:

- The bounded parsing evaluation using Oregon's Nova Micro profile stopped on
  its first query with `status: blocked` and exit code 1.
- A separate 16-output-token Nova Converse probe returned
  `ThrottlingException: Too many tokens per day`.
- One short Titan V2 embedding request was throttled; it produced no valid
  embedding result.
- The same parsing evaluation against direct Ministral 3B also stopped on its
  first query. A separate 16-output-token, no-tools Converse probe returned
  `ThrottlingException: Too many tokens`.

Automatic retries were disabled for these probes. The tiny no-tools requests
distinguish the runtime block from a large prompt or a forced-tool schema issue;
they do not establish its underlying cause. No live model-quality pass, vector
quality measurement or latency benchmark was obtained. The application still
defaults to local Ollama; changing the default AWS region does not switch the
preview to a hosted provider. Diagnose the mismatch
between listed quotas and runtime rejection before accepting a hosted model;
do not interpret nonzero listings as proof that the earlier access problem has
cleared. Cohere Embed English was also inspected, but its agreement reported
`NOT_AVAILABLE`; it was not invoked or subscribed.

### Cross-region routing is not an application requirement

Afterglow's low expected traffic does not require a multi-region application.
Bedrock cross-region inference lets AWS route an individual model call among
the destinations in a profile. It does not deploy copies of our API or website.

[Nova Micro](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-amazon-nova-micro.html)
supports direct inference in Virginia, but from Oregon AWS offers the US
profile `us.amazon.nova-micro-v1:0`, routing within the US. Its Oregon source
quota is separate from Virginia's quota. Titan V2 can be called directly in
Oregon. A small direct parsing alternative is
[Ministral 3B](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-mistral-ai-ministral-3b.html),
but its live probe also failed. No routing approach is accepted as working yet.

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

## Verified candidate model prices

AWS Price List API, catalog version `20261111013037`, on-demand inference in
US East (N. Virginia):

| Candidate | Input USD / 1M tokens | Output USD / 1M tokens |
| --- | ---: | ---: |
| Nova Micro | 0.035 | 0.14 |
| Titan Text Embeddings V2 | 0.02 | Not applicable |

See [Bedrock pricing](https://aws.amazon.com/bedrock/pricing/). These are
public rates, not a quote, account-specific discount, or an observed bill.

A **scenario**, not measured usage: 720 monthly searches with 5,000 parsing
input tokens, 256 parsing output tokens, and 250 query-embedding tokens per
search gives USD 0.1518048 parsing + USD 0.0036 query embeddings =
USD 0.1554048/month. Calculated with Python `Decimal`; no free-tier credits
were subtracted. Actual input includes the catalog vocabulary and examples.

This excludes initial catalog embeddings, storage, vector queries, hosting,
logs, quotas, DNS, taxes, and abuse traffic. The complete deployment estimate
and the USD 10/month budget check are **not yet verified**. In particular,
this scenario is not a spending cap; server-enforced quotas remain required.

## Gates before continuing to public release

1. Select a model/source region with existing capacity and repeat bounded
   parsing and embedding probes. Diagnose any account/runtime rejection even
   when listed quotas are positive. No quota-increase requests are part of
   the current phase; successful invocation and quality checks remain required
   before public deployment.
2. Evaluate representative user requests, including exact year boundaries,
   unsupported constraints, and semantic synopsis examples against known films.
3. Validate vector-store filtering, prices, and initial ingestion cost; finish
   the hosting estimate before provisioning billable resources.
4. Implement and verify fail-closed allowances before enabling paid searches.
5. Verify the public browser-to-catalog path, then select the custom domain.

Do not publish a recommendation-quality pass, latency measurement, or token
usage measurement until the corresponding live request has succeeded.

## Repeat the bounded live parsing check

This explicitly opts into up to four small paid model calls and exits nonzero
on the first blocked or incorrect result. It checks decade bounds, the
after/since distinction, and literal title punctuation against fixed expected
intent. It is not an evaluation of the complete catalog or semantic search.

```sh
AWS_CONFIG_FILE=/home/dukelee/.config/afterglow/aws/config \
AWS_PROFILE=afterglow AWS_REGION=us-west-2 \
  uv run python scripts/check_bedrock.py --live
```
