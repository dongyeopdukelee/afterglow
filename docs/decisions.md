# Project decisions

This is the living record of high-level decisions for Afterglow. Update it
when a decision is made, changed, or removed.

## Product scope

- The public project name is Afterglow; the GitHub repository is
  `dongyeopdukelee/afterglow`. The internal Python package, distribution, CLI,
  local folder, and Docker storage names remain unchanged.

- The initial product is an API-first movie catalog for browsing and title search.
- The first catalog will be imported from the CMU Movie Summary Corpus.
- Movies and genres will use a normalized `movies` / `movie_genres`
  association-table design; a separate genre lookup table is not needed yet.
- Imported genre values are stored as trimmed raw source labels without
  canonicalization.
- Catalog lists keep movie cards lightweight; full synopses are fetched
  separately. See the [API contracts](../README.md#api).
- Catalog movie release years are optional.
- Catalog ingestion will use downloadable public dataset snapshots; live
  provider APIs and freshness automation are deferred.
- The initial importer will parse the CMU Movie Summary Corpus source files
  directly; generic multi-source ingestion is deferred.
- Initial catalog imports will validate the complete dataset before writing;
  malformed data rejects the entire import.
- The initial CMU import requires an empty catalog.
- Natural-language recommendation will be implemented in two steps before
  release: structured catalog retrieval first, then semantic vector search.
- The structured-retrieval step will use an LLM to parse a user request and
  the application to query the catalog database.
- Normal local development and hosted deployment will use the same on-demand
  Bedrock model and adapter for intent parsing. Keep the endpoint family,
  region, prompts, intent schema, and inference settings with the model in
  shared, version-controlled application configuration so local model-behavior
  checks exercise the request path intended for deployment.
- Ollama with Qwen is an explicit offline option. Model acceptance for the
  hosted route requires a live check against Bedrock.
- Structured request intent will support catalog genre labels, an optional
  release-year range, and title keywords.
- Each recommendation request will return at most 10 movie cards.
- Structured filters are combined with AND. Title keywords match literal,
  case-insensitive substrings. Intent records year bounds and whether each is
  inclusive; database comparisons apply exclusivity without model arithmetic.
  Movies without a release year cannot match a year filter.
- Model output pairs literal years with comparisons (`after`, `before`,
  `since`, `until`, `in`); the adapter maps them to the public year bounds and
  inclusive flags without arithmetic. `in` sets both inclusive bounds to the
  same year; `until` includes its year.
- Structured-retrieval results are ordered by title and identifier, without
  relevance ranking. Unsupported constraints or requests without any supported
  filters are rejected instead of returning arbitrary or partial matches.
- The LLM will not write SQL directly; the application will construct database
  queries from constrained parsed fields.
- The vector-search step will combine semantic synopsis retrieval with the
  structured metadata filters.
- When semantic retrieval is added, use the same embedding model and
  compatible vector settings for queries and synopses in both development
  and deployment. Record the embedding configuration and catalog version with
  the index so retrieval evaluations use a known, compatible index. The parser
  can use a different model.
- User accounts, visitor-time external movie-data provider APIs, and
  LLM-generated recommendation explanations are deferred.
- Afterglow will have a public React/TypeScript website, built with Vite.
- The website uses a light neumorphic visual direction inspired by Stripe:
  Cornsilk (`#fefae0`) as the base, Dry Sage (`#ccd5ae`) for supporting
  surfaces, and Light Bronze (`#d4a373`) for accents. Surfaces are
  raised and search controls inset. Satoshi is used for
  display text and DM Sans for body text and controls; fonts are self-hosted.
  Satoshi's restricted binary stays out of Git and is obtained from Fontshare
  during font setup. Darker shades and neutral text preserve readable contrast.
  The wordmark is plain text, without a separate logo symbol. Interactive
  controls retain visible boundaries and keyboard focus indicators.
- New visits shuffle the catalog with a per-visit seed; pagination keeps that
  seed so films are not repeated or skipped. Reviewed starter films appear
  first, followed by the rest of the catalog. The unseeded API remains
  alphabetical and recommendation results keep their existing ordering.
- Catalog cards and details fetch the acquired posters from private S3 through
  `GET /movies/{id}/poster`. A full page eagerly loads its 12 poster slots;
  each matched image normally makes one S3 GET, with no HEAD request or image
  cache. Each detail opening makes a separate request. The backend makes
  at most three total attempts, with bounded timeouts
  and backoff, including failed/truncated body reads. Transient failures retry;
  missing matches and permanent errors need no repeated request. Exhaustion
  uses the existing abstract fallback without blocking catalog/search results.
- The metadata-only poster index is packaged with the backend. Match exact
  title, release year and the source synopsis fingerprint, independent of local
  movie IDs; validate image size and SHA-256 before serving bytes. No downloaded
  bulk image binaries are added to Git. The bucket is backend configuration
  and the poster client uses its Oregon region (`us-west-2`). AWS credentials
  stay in the backend. Keep S3 private and encrypted.
- The two static hero posters keep their bundled images and make no S3 calls.
  Existing starter-film discovery priority and seeded pagination remain; bulk
  poster availability applies to all catalog/search results. Hero film cards
  are not interactive. TMDB's approved logo and notice appear in Image credits.
- Poster acquisition remains an offline, resumable job using Wikimedia film
  identifiers and TMDB; user requests never query those providers. See
  [poster sourcing and storage](posters.md) for acquisition provenance, index
  generation, S3 upload verification and delivery behavior.
- New AWS resources and configuration default to `us-west-2` (Oregon).
  An explicit region selection or a service-specific endpoint takes precedence.
  Existing resources stay in their original regions.
- The owner is willing to spend up to $10/month on the project, including
  development-time Bedrock calls, production inference, storage, and hosting.
  Count cumulative usage across development and production toward this budget.
  The public demo targets about 720 recommendation requests/month. The existing
  separate allowance of up to $20/year upfront for a custom domain remains;
  the exact domain and registrar quote remain unselected.
- Routine costs below $1 do not need separate cost discussion or confirmation
  while projected total project spending remains within the $10/month budget.
  Repeated small charges still count toward the monthly total; flag expected
  budget overruns.
- Model selection for public deployment requires accuracy and token-cost checks.
- Select hosted inference from existing account quotas; quota-increase requests
  are out of scope for the current deployment phase. A positive quota listing
  does not replace successful live invocation and quality checks.
- Bedrock intent extraction uses Mantle Chat Completions. For defaults, see
  [inference configuration](../README.md#inference-configuration).
  Live results and deployment status are recorded in [AWS readiness](aws-readiness.md).
- Bedrock intent extraction requires validated tool output, shared
  application-owned year semantics, bounded input/output, and no silent
  fallback to another provider. Keep deterministic regression tests separate
  from bounded live Bedrock evaluations; use the production inference
  configuration for those live evaluations and exercise the full application
  request path. Record model/configuration identity, actual token usage, and
  latency. Matching configuration reduces drift but is not a guarantee of
  identical model responses.
- The website will remain online when a conservative usage quota pauses
  recommendation searches. Budget alerts alone are not a spending cap.
- Deployment will use the owner's existing AWS account without assuming signup
  credits. Lambda/API Gateway, S3/CloudFront and a read-only catalog snapshot
  are the target hosting approach; vector-storage compatibility and costs must
  be validated before provisioning.
- The owner selected a long-lived IAM user access key with AWS's managed
  `AdministratorAccess` policy for WSL development and administration across
  AWS services. The owner accepts a credential without automatic expiry to
  avoid recurring browser sign-in. Keep the login-backed profiles available
  for separate use. The hosted API will use its own execution role. See
  [WSL authentication setup](website.md#deployment-administrator-profile) for
  the profile, credential handling, and provisioning status.

## Technology

- The backend language is Python.
- The web API framework is FastAPI.
- Uvicorn will serve the FastAPI application locally and in containers.
- The initial database is SQLite.
- SQLAlchemy ORM 2 will access the database.
- Alembic will manage database schema migrations.
- Dependencies and the project-local virtual environment will be managed with uv.
- Pyright in strict mode will enforce static type checking.
- Ruff will lint and format Python code.
- Docker will containerize the application.
- Docker Compose will define and run the local application stack.
- Docker Compose will provide `DATABASE_URL` and persist the SQLite database in
  a named Docker volume.
- GitHub Actions will provide continuous integration; see
  [tests and checks](../README.md#tests-and-checks) for triggers and coverage.
  Continuous deployment is deferred until the project has a live deployment
  target.
- GitHub Actions will record daily automated health observations under
  `reports/health`, using existing Python checks and package build tooling.
  Report-only commits use the owner's verified account-associated author email
  and the workflow's GitHub token, without weakening branch protections.
- The optional Docker Compose service runs Ollama on CPU with persistent
  model storage. The API reaches it over HTTP. GPU setup is deferred.
