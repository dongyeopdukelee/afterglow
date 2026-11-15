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
- The local-development request-parsing LLM will be Qwen3.5 2B through Ollama
  using the `qwen3.5:2b` model tag.
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
- User accounts, live external movie-data provider APIs, and
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
  seed so films are not repeated or skipped. Verified poster matches appear
  first, followed by the rest of the catalog. The unseeded API remains
  alphabetical and recommendation results keep their existing ordering.
- Movie posters use a small, reviewed Wikimedia Commons snapshot instead of
  visitor-time web lookups. See [poster sourcing and storage](posters.md) for
  matching, provenance, bundled assets, private S3 storage, and fallback rules.
  Hero film cards are not interactive.
- Poster acquisition is expanding across the complete CMU catalog using
  Wikimedia film identifiers and TMDB. Bulk downloads are an offline,
  resumable storage job, separate from the website's bundled starter snapshot.
  External identifiers take precedence; title/year fallback requires a unique
  exact match. Conflicting external identifiers and title/year joins shared by
  distinct CMU source films block both matching paths and invalidate affected
  checkpoint results when resumed with the metadata and ID map.
  Normalized title/year collisions in the catalog block fallback even without
  optional crosswalk inputs; independently resolved external IDs remain usable.
  Older fallback results for those collisions are invalidated on resume.
  Missing, ambiguous, and failed images remain explicit in the
  coverage report. TMDB credentials stay in ignored local files and never
  reach the frontend. TMDB attribution must be added before its images are
  displayed in the website.
- New AWS resources and configuration defaults use `us-west-2` (Oregon).
  Previously recorded `us-east-1` readiness observations remain historical;
  this change does not migrate existing resources.
- The public demo targets about 720 recommendation requests/month and a
  $10/month recurring budget, excluding up to $20/year upfront for a custom
  domain. The exact domain and registrar quote remain unselected.
- Production inference will use on-demand Amazon Bedrock; local development
  retains Ollama. Models will be selected after accuracy and token-cost checks.
- Hosted intent extraction uses the Bedrock Converse API with validated tool
  output, shared application-owned year semantics, bounded input/output, and
  no silent fallback to another provider. Nova Micro is the current candidate;
  live model-quality validation remains required before public deployment.
- The website will remain online when a conservative usage quota pauses
  recommendation searches. Budget alerts alone are not a spending cap.
- Deployment will use the owner's existing AWS account without assuming signup
  credits. Lambda/API Gateway, S3/CloudFront and a read-only catalog snapshot
  are the target hosting approach; vector-storage compatibility and costs must
  be validated before provisioning.
- Owner-authorized deployment administration uses the `afterglow` IAM role
  with AWS's managed `AdministratorAccess` policy. Only the owner's existing
  `AccountFullAccessRole` can assume it, using one-hour temporary sessions.
  This is a deployment-tooling role, not the application's runtime identity.

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
- Ollama will serve local model inference over HTTP. Docker Compose will run
  it as a CPU service with persistent model storage; GPU setup is deferred.
