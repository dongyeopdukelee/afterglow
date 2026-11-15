# AWS readiness

Checked on 2026-11-15 UTC in `us-east-1`. These are observations, not a claim
that public deployment or live model-quality validation is complete.

## Verified

- The WSL `afterglow` profile successfully assumes the project administrator
  role. It is separate from the future application runtime identity.
- Bedrock lists Nova Micro and Titan Text Embeddings V2. Account-specific
  availability reports `AUTHORIZED`, agreement/entitlement `AVAILABLE`, and
  regional availability `AVAILABLE` for both candidates.
- The `us.amazon.nova-micro-v1:0` geographic inference profile is `ACTIVE`.
- Deterministic HTTP tests exercise the Bedrock provider boundary, real SQLite
  retrieval, AND-combined filters, exact year semantics, and service failures
  without calling AWS. They do not establish natural-language accuracy.

## Live checks blocked

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

1. Repeat bounded parsing and embedding probes after AWS verification clears
   and inference quotas are usable. If quotas remain zero, resolve the
   account/Service Quotas restriction with AWS before public deployment.
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
AWS_PROFILE=afterglow AWS_REGION=us-east-1 \
  uv run python scripts/check_bedrock.py --live
```
