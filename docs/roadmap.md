# Product roadmap

The target experience is a user request such as "I want to watch a sad movie
with dogs in it" becoming grounded movie recommendations.

## 1. Database lifecycle — Complete

- [x] Use Alembic migrations to track schema changes. The initial
  `create_movie_catalog` revision creates the catalog schema.
- [x] Set up the database explicitly before starting the API with
  `uv run alembic upgrade head`.

## 2. Catalog API

- [x] Add a movie-detail endpoint with the full synopsis.
- Add title search and genre filtering.
- [x] Add `limit`/`offset` pagination to catalog browsing.

## 3. Catalog ingestion — Complete

- [x] Keep the CMU Movie Summary Corpus source snapshot and attribution in the
  repository.
- [x] Parse and validate the metadata and plot-summary source files before
  writing any catalog rows.
- [x] Import the validated CMU catalog into an empty database with
  `uv run movie-db-llm import-cmu`.

## 4. Structured retrieval — Complete

- [x] Parse natural-language movie requests locally with Qwen3.5 2B via Ollama.
- [x] Validate constrained genre, release-year, and title-keyword intent.
- [x] Build catalog queries in the application and return up to 10 movie cards
  from `POST /recommendations`.
- [x] Reject unsupported constraints and handle local model failures.

## 5. Semantic retrieval

- Generate embeddings for movie synopses.
- Add vector search over the catalog.
- Combine semantic retrieval with structured metadata filters.

## 6. LLM recommendations

Build on the completed structured-retrieval step above.

- Extend interpretation and retrieval to semantic synopsis requests.
- Ground semantic responses in retrieved catalog results.
- Recommendation explanations remain deferred.

## 7. Evaluation and operations

- Create a representative set of recommendation queries.
- Measure retrieval quality, cost, and latency.
- Handle ambiguous and unsupported requests safely.

## 8. Public website and AWS deployment

- [x] Add the first local React/TypeScript website with browsing, structured
  recommendation input and full movie synopses, plus mobile/desktop tests.
- [x] Verify the `afterglow` deployment role and configure a WSL-only profile.
- [x] Add an optional IAM-signed Bedrock Mantle intent adapter with
  deterministic API/catalog tests; Ollama remains the default. GPT-OSS 20B
  basic inference works in Oregon; full live intent validation is pending.
- Evaluate on-demand Bedrock parsing and embedding models with measured cost.
- Account-specific readiness and the current live-inference blocker are
  recorded in [AWS readiness](aws-readiness.md).
- Implement serverless hosting, semantic retrieval and fail-closed quotas.
- Deploy and verify the complete public request path before announcing it.
- Select a custom domain following the
  [release gates](aws-readiness.md#gates-before-continuing-to-public-release).
