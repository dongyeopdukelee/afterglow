# Afterglow

A small movie catalog with a React/TypeScript website and a FastAPI backend
for natural-language searches by genre, year, and title, built as a
system-design learning project.

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

The API listens at `http://127.0.0.1:8000`. Recommendation searches use
paid Amazon Bedrock inference by default. The API sends the query and catalog
genre labels using the same settings intended for deployment. For Bedrock
searches and catalog posters, follow the
[WSL authentication setup](docs/website.md#deployment-administrator-profile).

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

Browse and synopsis data come from the catalog. The website loads card and
detail images separately through the API. See
[poster delivery and configuration](docs/posters.md#website-delivery).

### Optional offline inference

For a project-local [Ollama for Linux](https://docs.ollama.com/linux)
installation in WSL, start the server from the repository root in its own
terminal. Use the project model cache on every start.

```bash
OLLAMA_MODELS="$PWD/.tools/ollama/models" OLLAMA_NO_CLOUD=1 \
  .tools/ollama/bin/ollama serve
```

If Ollama is on your `PATH`, use `ollama` in place of the project executable.
Omit `OLLAMA_MODELS` if you keep the weights in Ollama's default cache.

With the server running, download the selected tag once while online in a
second terminal. Skip this step if the tag is already cached.

```bash
.tools/ollama/bin/ollama pull "${OLLAMA_MODEL:-qwen3.5:2b}"
```

With the weights cached, start the standalone API in the second terminal with
posters disabled for offline use.

```bash
INTENT_PROVIDER=ollama POSTER_BUCKET= \
  uv run --locked --offline uvicorn movie_db_llm.main:app --host 127.0.0.1
```

To use Ollama while keeping S3 posters, run
`INTENT_PROVIDER=ollama scripts/dev-api.sh`. The WSL launcher always selects
the project S3 bucket.

An Ollama result does not establish acceptance for the Bedrock route, and
provider failures never trigger an automatic switch.

## API

- `GET /health` reports whether the API is running.
- `GET /movies/{movie_id}` returns a movie card and its full synopsis, or 404.
- `GET /movies/{movie_id}/poster` fetches a matched poster from private S3.
  It returns image bytes, 404 for an unmatched film or unconfigured bucket,
  or 503 if the poster cannot be fetched or verified. See
  [poster delivery](docs/posters.md#website-delivery).
- `GET /movies` returns an ordered page of imported movie cards. It accepts
  `limit` (default 20, maximum 100) and `offset` (default 0) query parameters.
  Without a seed, movies are ordered by title and identifier.
  An optional `seed` (0–2147483647) selects repeatable shuffled discovery,
  with the 13 starter films first. Keep the same seed between pages.
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
| `INTENT_PROVIDER` | `bedrock` | Select `bedrock` or `ollama` for recommendations. An unknown value makes this route return HTTP 503. |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Ollama service URL |
| `OLLAMA_MODEL` | `qwen3.5:2b` | Locally downloaded model tag |
| `OLLAMA_TIMEOUT_SECONDS` | `120` | HTTP inference timeout in seconds |
| `AWS_REGION` | `us-west-2` | Bedrock client region; explicitly set to override the Oregon default |
| `BEDROCK_MODEL_ID` | `mistral.ministral-3-8b-instruct` | Mantle model supporting the `/v1` Chat Completions forced-function contract; overrides require live validation |

Run the configuration report from the repository root with the API's
`DATABASE_URL` and provider settings. It works without AWS credentials or a
model call.

```bash
uv run --locked --no-env-file movie-db-llm inference-config
```

For Bedrock, the report reads the SQLite catalog in read-only mode and prints
a SHA-256 fingerprint of the regional endpoint and request body. The digest
covers the model, prompt examples, tool schema, generation settings, and genre
labels. A fixed placeholder takes the place of the user's query. For Ollama,
the report identifies the selected provider and model. The command exits
nonzero for an unknown provider. The Bedrock report also fails if the region
is malformed or the SQLite catalog cannot be read.

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
[website build and browser checks](docs/website.md#verify). Python recommendation
tests use mocked model responses, and the Compose smoke test checks health
and catalog browsing. CI requires neither AWS inference nor Ollama weights.

After changing the Bedrock model or its request settings, use the imported
catalog for the
[paid parser evaluation](docs/aws-readiness.md#repeat-the-bounded-live-parsing-check)
and the [HTTP acceptance cases](docs/aws-readiness.md#repeat-the-http-acceptance-cases).

## Docker Compose

For a new Docker Compose database volume, build the image, apply migrations,
then start the API:

```bash
docker compose build
docker compose run --rm api alembic upgrade head
docker compose run --rm api movie-db-llm import-cmu
docker compose up
```

Compose publishes the API only on the host's loopback interface. Open
`http://127.0.0.1:8000/docs` or query the catalog from the host.

```bash
curl http://127.0.0.1:8000/health
curl 'http://127.0.0.1:8000/movies?limit=20&offset=0'
```

Docker Compose stores the SQLite database in the `movie-db-data` named volume.
The default API uses the same Bedrock application settings as the WSL launch.
`INTENT_PROVIDER`, `AWS_REGION`, `BEDROCK_MODEL_ID`, and `OLLAMA_MODEL`
overrides pass through when supplied. The Compose stack leaves S3 poster
delivery disabled. Configure container authentication separately for Bedrock.
Use the [WSL launcher](#local-development) for authenticated local development
with S3 posters.

For offline Docker inference, download the selected model once while online.
Skip this step if the tag is already in the `ollama-models` volume.

```bash
docker compose --profile offline up -d ollama
docker compose --profile offline exec ollama ollama pull "${OLLAMA_MODEL:-qwen3.5:2b}"
```

With both images and the model cached, select Ollama for the API and enable
the offline profile to start its model service.

```bash
INTENT_PROVIDER=ollama docker compose --profile offline up --pull never
```

Ollama runs on CPU, disables cloud inference, and stores downloaded models in
`ollama-models`. Its port stays on the Compose network.
Stop the service with `Ctrl+C`, then remove the containers with:

```bash
docker compose --profile offline down
```
