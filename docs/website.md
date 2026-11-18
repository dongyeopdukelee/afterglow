# Afterglow website

The website runs locally against the FastAPI catalog and supports genre,
year and title filters. For live Bedrock parsing results and the remaining
semantic search, spending and public hosting checks, see
[AWS readiness](aws-readiness.md).

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
Both servers listen only on loopback. Vite updates the open page when frontend
files change, and Uvicorn restarts the API when Python files change.

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

Start the servers in two terminals from the root of the persistent worktree.
For the WSL Bedrock preview, use the
[hosted API command](#hosted-intent-parsing-not-yet-deployed).
For local inference, start Ollama and run the API with this command.

```sh
INTENT_PROVIDER=ollama DATABASE_URL="sqlite:///$PWD/.tools/preview/catalog.sqlite3" \
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

For an independent UI redesign preview, run the other checkout's API on 8002
and Vite on 5175. From the persistent worktree, run
`export PREVIEW_CATALOG="$PWD/.tools/preview/catalog.sqlite3"` before switching
the shell to the redesign checkout. Its API opens the same catalog read-only.
For Bedrock, substitute the read-only URL below and port 8002 in the
[hosted API command](#hosted-intent-parsing-not-yet-deployed).

```sh
INTENT_PROVIDER=ollama DATABASE_URL="sqlite:///file:$PREVIEW_CATALOG?mode=ro&uri=true" \
  uv run uvicorn movie_db_llm.main:app --host 127.0.0.1 --port 8002 --reload
```

```sh
cd frontend
AFTERGLOW_API_PROXY=http://127.0.0.1:8002 \
  npm run dev -- --port 5175 --strictPort
```

This keeps the original website on 5174 available for comparison. Vite updates
the redesign browser tab as frontend source changes; keep its process running.

The preview runs in the `afterglow-dev` tmux window. Its servers keep running
when the browser closes or tmux detaches. Stopping WSL or rebooting stops
them. View logs with `tmux select-window -t main:afterglow-dev`. To stop the
preview, press Ctrl+C in each server pane. Do not use broad process-kill
commands that would also stop tests or other projects.

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
- Server-enforced usage limits before exposing paid anonymous inference;
  a 429 display is not itself a quota implementation. Follow the availability
  and spending constraints in the [product decisions](decisions.md#product-scope).

Follow the [release gates](aws-readiness.md#gates-before-continuing-to-public-release)
for model evaluation, costs, vector-store compatibility and domain timing.
Before buying the domain, verify availability and obtain an approved
registration/renewal quote within the [selected budget](decisions.md#product-scope).

### Deployment administrator profile

The owner-authorized `afterglow` IAM role uses temporary sessions,
not permanent access keys. On the current WSL host, its private CLI config is
`/home/dukelee/.config/afterglow/aws/config`; the profile is `afterglow`.
Exported access keys can take precedence over a profile selected through
`AWS_PROFILE`. The CLI examples select the profile with `--profile`. The
application and importer commands use `env -u` to clear inherited profile,
key, web identity, and container credential overrides for the launched process.
[AWS credential precedence](https://docs.aws.amazon.com/cli/latest/topic/config-vars.html#credentials)

Verify the assumed identity with the private config.

```sh
AWS_CONFIG_FILE=/home/dukelee/.config/afterglow/aws/config \
AWS_SHARED_CREDENTIALS_FILE=/home/dukelee/.config/afterglow/aws/credentials \
AWS_LOGIN_CACHE_DIRECTORY=/home/dukelee/.config/afterglow/aws/login-cache \
  aws --profile afterglow sts get-caller-identity
```

The `afterglow` profile uses a `credential_process` that reads the project's
private WSL `afterglow-source` login session, calls STS AssumeRole, and returns
one-hour role credentials to its caller. It does not use `role_arn` /
`source_profile` or the AWS CLI's standard on-disk assume-role cache.
The separate `afterglow-source` profile uses `login_session`.
When the browser-login session expires, refresh `afterglow-source` with
the same config and cache.

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

The local preview makes paid Bedrock calls for recommendations. Run this
command from the persistent worktree to start Uvicorn on port 8001 with the
prepared preview catalog. Refresh an expired session with the
[project login](#deployment-administrator-profile).

```sh
env -u AWS_DEFAULT_PROFILE -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY \
  -u AWS_SESSION_TOKEN -u AWS_SECURITY_TOKEN -u AWS_WEB_IDENTITY_TOKEN_FILE \
  -u AWS_ROLE_ARN -u AWS_ROLE_SESSION_NAME \
  -u AWS_CONTAINER_CREDENTIALS_RELATIVE_URI -u AWS_CONTAINER_CREDENTIALS_FULL_URI \
  -u AWS_CONTAINER_AUTHORIZATION_TOKEN -u AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE \
  AWS_CONFIG_FILE=/home/dukelee/.config/afterglow/aws/config \
  AWS_SHARED_CREDENTIALS_FILE=/home/dukelee/.config/afterglow/aws/credentials \
  AWS_LOGIN_CACHE_DIRECTORY=/home/dukelee/.config/afterglow/aws/login-cache \
  AWS_PROFILE=afterglow AWS_REGION=us-west-2 INTENT_PROVIDER=bedrock \
  DATABASE_URL="sqlite:///$PWD/.tools/preview/catalog.sqlite3" \
  uv run uvicorn movie_db_llm.main:app --host 127.0.0.1 --port 8001 --reload
```

See [inference configuration](../README.md#inference-configuration) for provider
settings and defaults, and the [API contract](../README.md#api) for failure
responses. Hosted parsing uses one IAM-signed Mantle Chat Completions call
through the regional `/v1` route, at most 1,024 completion tokens (including
reasoning), a bounded input context, and no automatic paid retries. It requires
exactly one `movie_intent` function call and rejects truncated output, refusals,
wrong function names and invalid arguments.
Hosted examples pair each assistant function call with its tool reply. The
prompt calls for integer years and empty lists for an unconstrained request.
Ollama uses plain-JSON examples. Both providers share the extraction rules
and the application's interpretation of year comparisons.
Credentials refresh through the normal AWS credential chain; Lambda will use
its execution role, not the local administrator profile. No API key is needed.
Catalog browsing, movie details and health remain available when inference fails.

Deterministic regression checks run without AWS credentials or paid calls:

```sh
uv run pytest tests/test_bedrock.py tests/test_recommendations.py
uv run pyright scripts/check_bedrock.py
```

For the opt-in paid evaluation outside CI, see the
[bounded live check](aws-readiness.md#repeat-the-bounded-live-parsing-check).

The opening selection shuffles on a new visit; its seed stays fixed while
paging. A `?seed=332` URL makes a visit reproducible. Verified poster matches
appear first, then the complete remaining catalog. See the
[API contract](../README.md#api) for ordering outside website browsing.

The catalog attribution remains visible. [Poster sourcing and S3 storage](posters.md)
describes the reviewed Commons snapshot, local copies, provenance records, and
artwork fallback. The two hero films are non-clickable illustrations of the
collection.
