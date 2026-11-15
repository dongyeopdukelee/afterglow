# Afterglow website

This first website slice runs locally against the existing FastAPI catalog.
It is not a public AWS deployment. An optional Bedrock parsing adapter is
implemented, with live validation still pending; see
[AWS readiness](aws-readiness.md).
Semantic indexing/search, usage quotas and AWS infrastructure remain subsequent
work; the UI labels plot/mood matching as unavailable until implemented.

The interface uses a light neumorphic theme: soft raised film cards, an inset
search field, violet controls and original abstract CSS artwork. Navigation
links jump directly to the catalog or search, and the first keyboard stop
lets visitors skip to the films. Search instructions describe the supported
genre, year and title filters. Shadows supplement visible control boundaries
and focus outlines; reduced-motion settings disable smooth scrolling and
transitions.

## Run locally

For dependencies, catalog setup and API/website startup commands, see
[local development](../README.md#local-development).

### Interactive preview during development

Keep the interactive website on <http://localhost:5174> and its API on
<http://localhost:8001>, leaving ports 5173 and 8000 free for browser tests.
The Vite development server updates the open page when frontend files change;
Uvicorn's reload mode restarts the API when Python files change. This is a
development preview, not a production server or a mock of hosted inference.

From the repository root, prepare a separate local preview catalog once:

```sh
mkdir -p .tools/preview
export DATABASE_URL="sqlite:///$(pwd)/.tools/preview/catalog.sqlite3"
uv run alembic upgrade head
uv run movie-db-llm import-cmu
```

The import requires an empty catalog; do not repeat it on a populated database.
The ignored `.tools/preview` directory keeps the real CMU snapshot persistent
and separate from disposable test databases. For later schema changes, apply
the migrations to this database before restarting its API.

Start these in two terminals from the repository root:

```sh
DATABASE_URL="sqlite:///$(pwd)/.tools/preview/catalog.sqlite3" \
  uv run uvicorn movie_db_llm.main:app --host 127.0.0.1 --port 8001 --reload
```

```sh
cd frontend
AFTERGLOW_API_PROXY=http://127.0.0.1:8001 \
  npm run dev -- --port 5174 --strictPort
```

`AFTERGLOW_API_PROXY` configures only Vite's development proxy; browser requests
remain same-origin. Restart Vite if changing this setting. Local searches
still require Ollama with `qwen3.5:2b`; browsing and synopses do not.

For an independent UI redesign preview from another checkout, keep that
checkout's Vite server on port 5175 with the existing API on 8001:

```sh
cd frontend
AFTERGLOW_API_PROXY=http://127.0.0.1:8001 \
  npm run dev -- --port 5175 --strictPort
```

This keeps the original website on 5174 available for comparison. Vite updates
the redesign browser tab as frontend source changes; keep its process running.

On the current WSL host, these services run in the `afterglow-dev` tmux window
from `/tmp/afterglow-website`. A third pane runs the existing local Ollama
installation and model cache, without paid API calls or model downloads.
Closing the browser or detaching tmux does not
stop them; stopping WSL or rebooting does. To view logs, select that window
with `tmux select-window -t main:afterglow-dev`. To stop the preview, press
Ctrl+C in each server pane. Do not use broad process-kill commands that would
also stop tests or other projects.

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
Keyboard coverage also rapidly closes and reopens the same synopsis, using
both the close button and Escape while preserving focus on the invoking card.

## Public deployment prerequisites

- AWS CLI and an authenticated AWS profile in the intended account/region.
  Do not paste AWS secrets into chat or commit them to this repository.
- Measured Bedrock model quality/token costs and vector-store filter
  compatibility, as required by the [deployment decisions](decisions.md#product-scope).
- Server-enforced usage limits before exposing paid anonymous inference;
  a 429 display is not itself a quota implementation. Follow the availability
  and spending constraints in the [product decisions](decisions.md#product-scope).

Follow the [release gates](aws-readiness.md#gates-before-continuing-to-public-release)
for domain timing. Before buying it, verify availability and obtain an approved
registration/renewal quote within the [selected budget](decisions.md#product-scope).

### Deployment administrator profile

The owner-authorized `afterglow` IAM role uses temporary sessions,
not permanent access keys. On the current WSL host, its private CLI config is
`/home/dukelee/.config/afterglow/aws/config`; the profile is `afterglow`.
This explicit config avoids writing to the Windows-linked default `.aws`
directory. Verify the assumed identity with:

```sh
AWS_CONFIG_FILE=/home/dukelee/.config/afterglow/aws/config \
  aws --profile afterglow sts get-caller-identity
```

The profile obtains one-hour role credentials from the owner's browser-login
session. When that source session expires, authenticate `afterglow-source`
again using the same config and the WSL login cache directory
`/home/dukelee/.config/afterglow/aws/login-cache`. The role's administrator
permissions are for deployment tooling only; do not assign it to the website
or API runtime. No hosting resources have been provisioned by this role setup.

### Hosted intent parsing (not yet deployed)

To select Bedrock explicitly on the WSL host:

```sh
AWS_CONFIG_FILE=/home/dukelee/.config/afterglow/aws/config \
AWS_PROFILE=afterglow AWS_REGION=us-east-1 INTENT_PROVIDER=bedrock \
  uv run uvicorn movie_db_llm.main:app --reload
```

See [inference configuration](../README.md#inference-configuration) for provider
settings and defaults, and the [API contract](../README.md#api) for failure
responses. Hosted parsing uses one bounded Converse call, at most 512 output
tokens, a bounded input context, and no automatic paid retries. Catalog browsing,
movie details and health remain available when inference fails.

This is adapter support, not proof of model quality or a public deployment.
See [AWS readiness](aws-readiness.md) for live-check results and pending gates.
Deterministic regression checks run without AWS credentials or paid calls:

```sh
uv run pytest tests/test_bedrock.py tests/test_recommendations.py
uv run pyright scripts/check_bedrock.py
```

The catalog attribution remains visible in the website. Covers are decorative
CSS artwork, not downloaded movie posters or additional licensed media.
