# Afterglow website

This first website slice runs locally against the existing FastAPI catalog.
It is not a public AWS deployment. An optional Bedrock parsing adapter is
implemented using Mantle; full live validation is tracked in
[AWS readiness](aws-readiness.md).
Semantic indexing/search, usage quotas and public hosting infrastructure remain
subsequent work; the UI describes only the currently supported genre, year,
and title filters.

See the [product decisions](decisions.md#product-scope) for the visual direction.
The first keyboard stop lets visitors skip to the films. The header keeps
only the brand, and search instructions follow the supported
[API filters](../README.md#api). Reduced-motion settings disable smooth
scrolling and transitions.

## Run locally

For dependencies, catalog setup and API/website startup commands, see
[local development](../README.md#local-development).

Typography is self-hosted: Satoshi for headings/wordmarks and DM Sans for
body text and controls. Run `npm run fonts` in `frontend` before the first
development session; `npm run build` also performs this setup. It downloads
the unmodified Satoshi webfont directly from Fontshare and verifies its pinned
checksum. That font binary is ignored by Git; its ITF license permits the
project's self-hosted use and limits redistribution. DM Sans's Latin and
extended Latin webfonts are included under the SIL Open Font License. Font
sources and notices are in `frontend/public/fonts/README.md`.

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
remain same-origin. Restart Vite if changing this setting. Recommendation
searches use the [configured inference provider](../README.md#inference-configuration);
browsing and synopses do not require inference.

For an independent UI redesign preview from another checkout, run that
checkout's API on 8002 and Vite on 5175. The original preview on 5174 keeps
its API on 8001. On this host, the redesign API reads the same CMU snapshot
without modifying it:

```sh
DATABASE_URL='sqlite:///file:/tmp/afterglow-website/.tools/preview/catalog.sqlite3?mode=ro&uri=true' \
  uv run uvicorn movie_db_llm.main:app --host 127.0.0.1 --port 8002 --reload
```

```sh
cd frontend
AFTERGLOW_API_PROXY=http://127.0.0.1:8002 \
  npm run dev -- --port 5175 --strictPort
```

This keeps the original website on 5174 available for comparison. Vite updates
the redesign browser tab as frontend source changes; keep its process running.

Keep preview processes in a dedicated `afterglow-dev` tmux window. Start
Ollama separately when using the default local provider. If explicitly using
Bedrock, recommendation searches make bounded paid calls and require a current
AWS login; catalog browsing and synopses do not require inference.
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
`/home/dukelee/.config/afterglow/aws/login-cache`:

```sh
AWS_CONFIG_FILE=/home/dukelee/.config/afterglow/aws/config \
AWS_SHARED_CREDENTIALS_FILE=/home/dukelee/.config/afterglow/aws/credentials \
AWS_LOGIN_CACHE_DIRECTORY=/home/dukelee/.config/afterglow/aws/login-cache \
  aws login --profile afterglow-source
```

An unqualified `aws login` refreshes the default profile/cache, not necessarily
this project session. On this host the default `.aws` path points to Windows;
the explicit paths above keep project authentication inside WSL. Never copy
tokens into this repository, logs or chat.

The role's administrator
permissions are for deployment tooling only; do not assign it to the website
or API runtime. No hosting resources have been provisioned by this role setup.

### Hosted intent parsing (not yet deployed)

To select Bedrock explicitly on the WSL host:

```sh
AWS_CONFIG_FILE=/home/dukelee/.config/afterglow/aws/config \
AWS_PROFILE=afterglow AWS_REGION=us-west-2 INTENT_PROVIDER=bedrock \
  uv run uvicorn movie_db_llm.main:app --reload
```

See [inference configuration](../README.md#inference-configuration) for provider
settings and defaults, and the [API contract](../README.md#api) for failure
responses. Hosted parsing uses one IAM-signed Mantle Chat Completions call
through the regional `/v1` route, at most 1,024 completion tokens (including
reasoning), a bounded input context, and no automatic paid retries. It requires
exactly one `movie_intent` function call and rejects truncated output, refusals,
wrong function names and invalid arguments.
Hosted examples use assistant function calls with corresponding tool replies,
not Ollama's plain-JSON assistant replies. The underlying extraction rules and
application-owned year semantics remain shared.
Credentials refresh through the normal AWS credential chain; Lambda will use
its execution role, not the local administrator profile. No API key is needed.
Catalog browsing, movie details and health remain available when inference fails.

This is adapter support, not proof of model quality or a public deployment.
See [AWS readiness](aws-readiness.md) for live-check results and pending gates.
Deterministic regression checks run without AWS credentials or paid calls:

```sh
uv run pytest tests/test_bedrock.py tests/test_recommendations.py
uv run pyright scripts/check_bedrock.py
```

For opt-in paid evaluation, see the
[bounded live check](aws-readiness.md#repeat-the-bounded-live-parsing-check).
CI never runs that evaluation or needs AWS credentials. Models listed by AWS
are not necessarily callable by the account; test the exact endpoint and model.

The opening selection shuffles on a new visit; its seed stays fixed while
paging. A `?seed=332` URL makes a visit reproducible. Verified poster matches
appear first, then the complete remaining catalog. See the
[API contract](../README.md#api) for ordering outside website browsing.

The catalog attribution remains visible. [Poster sourcing and S3 storage](posters.md)
describes the reviewed Commons snapshot, local copies, provenance records, and
artwork fallback. The two hero films are non-clickable illustrations of the
collection.
