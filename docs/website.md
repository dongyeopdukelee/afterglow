# Afterglow website

This first website slice runs locally against the existing FastAPI catalog.
It is not a public AWS deployment. Production Bedrock integration, semantic
indexing/search, usage quotas and AWS infrastructure are subsequent work;
the UI explicitly labels plot/mood matching as unavailable until implemented.

## Run locally

Use Node.js 24+ and the project's existing Python/uv setup. Prepare the catalog
as documented in the root README, then start the backend:

```sh
uv run uvicorn movie_db_llm.main:app --reload
```

In another terminal:

```sh
cd frontend
npm ci --ignore-scripts
npm run dev
```

Open http://127.0.0.1:5173. Vite proxies catalog and recommendation calls to
127.0.0.1:8000; local Ollama is still required for recommendation inference.
Browsing and movie details do not need a model. No live model is used in CI.

The build is static (`npm run build`, output `frontend/dist`). Calls are
same-origin. A future CloudFront deployment must route `/movies`
and `/recommendations` to the API, with remaining paths served from the
frontend S3 bucket.

## Verify

```sh
uv run pytest
cd frontend
npm run build
npx playwright install chromium
npm test
```

Browser tests run real Vite and FastAPI processes with an isolated, migrated
SQLite fixture; ports 8000 and 5173 must be free. Recommendation response
fixtures exercise the browser API contract, not model accuracy. Both desktop
and mobile layouts cover browsing, pagination, full synopses, interpreted
filters, empty/malformed responses and 422/429/503 recovery. Browser downloads
and test fixtures are development-only, not shipped to visitors.

## Public deployment prerequisites

- An authenticated AWS profile in the intended account/region. In this setup
  `~/.aws` points to Windows but contains no config or credentials files.
  Do not paste AWS secrets into chat or commit them to this repository.
- Exact domain availability and an approved registration/renewal quote within
  the selected $20/year upfront allowance, separate from $10/month recurring.
- Measured Bedrock model quality/token costs and vector-store filter
  compatibility. The reviewed ~$6.50/month figure is a planning scenario,
  not a measured quote or a guarantee. No signup credits are assumed.
- Server-enforced usage limits before exposing paid anonymous inference;
  a 429 display is not itself a quota implementation. Keep the website up
  while paid search is disabled. Budget alerts are not a hard spending cap.

The catalog attribution remains visible in the website. Covers are decorative
CSS artwork, not downloaded movie posters or additional licensed media.
