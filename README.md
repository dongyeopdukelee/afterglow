# Afterglow

A small movie catalog with a React/TypeScript website and a FastAPI API for
local natural-language retrieval, built as a system-design learning project.

See the [product roadmap](docs/roadmap.md) for the planned path from a catalog
API to grounded natural-language movie recommendations.

## Local development

Afterglow retains the `movie_db_llm` Python package and `movie-db-llm` CLI
names for compatibility. Existing commands, local folders, and Docker volumes
do not need to be renamed.

Install the locked dependencies:

```bash
uv sync
```

Prepare the local database:

```bash
uv run alembic upgrade head
uv run movie-db-llm import-cmu
```

Run the API with automatic reload through the WSL launcher:

```bash
scripts/dev-api.sh
```

Bedrock is the normal inference provider for development and the future hosted
application. Both use the same application configuration, Mantle adapter,
prompt, schema, and generation settings. The current model is Ministral 8B in
Oregon; changing the model or settings requires re-running live acceptance.
The launcher uses the existing WSL-only `afterglow` profile, clears inherited
credential overrides, and keeps the default Windows-linked AWS directory out
of this workflow. For first-time setup or an expired login, see
[project authentication](docs/website.md#deployment-administrator-profile).

Inspect the safe configuration fingerprint, or run the bounded real-model
check with the imported catalog:

```bash
scripts/dev-api.sh --print-config
scripts/dev-api.sh --check
```

The fingerprint includes the actual request contract and genre vocabulary,
but no user query or credentials. The check makes up to ten paid Bedrock
requests and reports actual usage/latency and the fingerprint. It is separate
from deterministic regression tests and does not validate semantic search.

### Optional offline inference

To work offline, run [Ollama for Linux](https://docs.ollama.com/linux) inside
WSL with cloud inference disabled, then explicitly select it for the API:

```bash
OLLAMA_NO_CLOUD=1 ollama serve
ollama pull qwen3.5:2b
INTENT_PROVIDER=ollama scripts/dev-api.sh
```

Run the API in another terminal while Ollama serves. An Ollama result does not
establish acceptance for the Bedrock route, and provider failures never trigger
an automatic switch. Catalog browsing and movie details do not need inference.
Project-local Ollama installations can use `.tools/ollama/bin/ollama` and the
ignored `.tools/ollama/models` directory for model storage.

The API is available at `http://127.0.0.1:8000`.

To run the local website, use Node.js 24+ and start Vite in another terminal:

```bash
cd frontend
npm ci --ignore-scripts
npm run fonts
npm run dev
```

Open `http://127.0.0.1:5173`. Vite proxies API calls to the backend above.
For an auto-updating preview that can stay running alongside browser tests,
use the [dedicated preview ports](docs/website.md#interactive-preview-during-development).
See [website development](docs/website.md) for build, browser verification and
deployment prerequisites. This milestone runs locally; AWS deployment and
plot/mood matching remain upcoming.

See [poster sourcing and storage](docs/posters.md) for the bundled artwork
and the separate offline catalog-wide acquisition CLI.

## API

- `GET /health` reports whether the API is running.
- `GET /movies/{movie_id}` returns a movie card and its full synopsis, or 404.
- `GET /movies` returns an ordered page of imported movie cards. It accepts
  `limit` (default 20, maximum 100) and `offset` (default 0) query parameters.
  Without a seed, movies are ordered by title and identifier.
  An optional `seed` (0–2147483647) selects repeatable shuffled discovery,
  with verified poster matches first. Keep the same seed between pages.
- `POST /recommendations` interprets a natural-language request using the
  configured provider and returns up to 10 matching movie cards plus the
  parsed intent.
- `GET /docs` opens FastAPI's interactive API documentation.

The API does not create or modify its schema at startup. Apply migrations before
starting it.

`import-cmu` reads the CMU source files in this repository and requires an
empty movie catalog. It validates all source files before writing the catalog.

Example recommendation request:

```bash
curl http://127.0.0.1:8000/recommendations \
  -H 'Content-Type: application/json' \
  -d '{"query":"Comedies from the 1990s with love in the title"}'
```

The response uses an `items` envelope with lightweight movie cards and an
`intent` object containing `genres`, `year_min`, `year_max`,
`year_min_inclusive`, `year_max_inclusive`, `title_keywords`, and
`unsupported_constraints`. All requested genres and title keywords must match.
Title matching uses literal, case-insensitive substrings. Exact years, decades,
since, and until use inclusive year bounds; before and after use exclusive
bounds. Undated movies only match when no year filter is requested.
Recommendation results are ordered by title and identifier, with no relevance
ranking yet.
No matching movies returns an empty `items` list without relaxing filters.

This step supports genres, release years, and title keywords. Requests for
plot subjects (such as dogs), mood, actors, exclusions, or OR conditions
return HTTP 422 rather than silently dropping those constraints. Empty or
oversized request text also returns 422, as do requests with no movie filters,
such as greetings. Model parsing is probabilistic:
schema validation prevents invalid queries but cannot guarantee that every
request is interpreted correctly. Semantic synopsis search comes next.

HTTP 503 means the configured provider is unavailable, misconfigured, or unable
to complete inference. This includes missing local models, timeouts, hosted
service or credential failures, and an oversized hosted input context.
HTTP 502 means the model returned invalid or incomplete intent.
The API never executes model-generated SQL.

### Inference configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `INTENT_PROVIDER` | `bedrock` | Select `ollama` or `bedrock`; unknown values return 503 without fallback |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Ollama service URL |
| `OLLAMA_MODEL` | `qwen3.5:2b` | Locally downloaded model tag |
| `OLLAMA_TIMEOUT_SECONDS` | `120` | HTTP inference timeout in seconds |
| `AWS_REGION` | `us-west-2` | Bedrock client region; explicitly set to override the Oregon default |
| `BEDROCK_MODEL_ID` | `mistral.ministral-3-8b-instruct` | Mantle model supporting the `/v1` Chat Completions forced-function contract; overrides require live validation |

## Tests and checks

Daily automated observations and controls are documented in
[daily project health](docs/daily-health.md).

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run pyright
```

GitHub Actions runs these checks for pull requests and pushes to `main`. It
also builds the production image, creates a disposable Docker Compose catalog,
and checks the running API over HTTP. A separate job runs the
[website build and browser checks](docs/website.md#verify). The workflow does
not download the local Ollama model. Python recommendation tests use mocked
model responses; the Compose smoke test only checks health and catalog browsing
without starting Ollama.

## Docker Compose

For a new Docker Compose database volume, build the image, apply migrations,
then start the API:

```bash
docker compose build
docker compose run --rm api alembic upgrade head
docker compose run --rm api movie-db-llm import-cmu
docker compose up
```

Open `http://127.0.0.1:8000/docs` or call:

```bash
curl http://127.0.0.1:8000/health
curl 'http://127.0.0.1:8000/movies?limit=20&offset=0'
```

Docker Compose stores the SQLite database in the `movie-db-data` named volume.
The default API uses the same Bedrock application settings as the WSL launch;
`INTENT_PROVIDER`, `AWS_REGION`, and `BEDROCK_MODEL_ID` overrides pass through
when supplied. Container authentication must be configured separately: the
host's credential-process executable and paths do not exist in the image.
Use the WSL launcher for the authenticated local-development workflow above.

For explicit offline Docker inference:

```bash
INTENT_PROVIDER=ollama docker compose --profile offline up -d ollama
INTENT_PROVIDER=ollama docker compose --profile offline exec ollama ollama pull qwen3.5:2b
INTENT_PROVIDER=ollama docker compose --profile offline up
```

Ollama runs on CPU, disables cloud inference, and stores downloaded models in
`ollama-models`. Its port stays on the Compose network.
Stop the service with `Ctrl+C`, then remove the containers with:

```bash
docker compose down
```
