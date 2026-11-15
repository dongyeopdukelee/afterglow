# Afterglow website

This first website slice runs locally against the existing FastAPI catalog.
It is not a public AWS deployment. Production Bedrock integration, semantic
indexing/search, usage quotas and AWS infrastructure are subsequent work;
the UI explicitly labels plot/mood matching as unavailable until implemented.

## Run locally

For dependencies, catalog setup and API/website startup commands, see
[local development](../README.md#local-development).

The build is static (`npm run build`, output `frontend/dist`). Calls are
same-origin. A future CloudFront deployment must route `/movies`, `/movies/*`
and `/recommendations` to the API, with remaining paths served from the
frontend S3 bucket.

## Verify

Run the [Python checks](../README.md#tests-and-checks) separately. For the
website, starting at the repository root:

```sh
cd frontend
npm run build
npx playwright install chromium
npm test
```

On this WSL host, set these variables before the Playwright commands above:

```sh
export PLAYWRIGHT_BROWSERS_PATH=/tmp/afterglow-browsers
export LD_LIBRARY_PATH=/tmp/afterglow-browser-libs/extracted/usr/lib/x86_64-linux-gnu
```

These browser paths and extracted official Ubuntu libraries are temporary host
setup, not repository dependencies.

Browser tests run real Vite and FastAPI processes with an isolated, migrated
SQLite fixture; ports 8000 and 5173 must be free. Recommendation response
fixtures exercise the browser API contract, not model accuracy. Both desktop
and mobile layouts cover browsing, pagination, full synopses, interpreted
filters, empty/malformed responses and 422/429/503 recovery. Browser downloads
and test fixtures are development-only, not shipped to visitors.

## Public deployment prerequisites

- AWS CLI and an authenticated AWS profile in the intended account/region.
  Account access has not been validated in this local milestone.
  Do not paste AWS secrets into chat or commit them to this repository.
- Exact domain availability and an approved registration/renewal quote within
  the [selected budget](decisions.md#product-scope).
- Measured Bedrock model quality/token costs and vector-store filter
  compatibility, as required by the [deployment decisions](decisions.md#product-scope).
- Server-enforced usage limits before exposing paid anonymous inference;
  a 429 display is not itself a quota implementation. Follow the availability
  and spending constraints in the [product decisions](decisions.md#product-scope).

The catalog attribution remains visible in the website. Covers are decorative
CSS artwork, not downloaded movie posters or additional licensed media.
