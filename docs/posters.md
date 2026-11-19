# Movie poster snapshot

## Website delivery

The website uses the catalog-wide collection for browse and recommendation
cards and detail views. The two non-interactive hero films use bundled artwork
and make zero S3 requests. Visitor requests never call TMDB or Wikimedia.

Each card loads `GET /movies/{movie_id}/poster`. The backend matches exact
title/year and a SHA-256 fingerprint of the source synopsis using the packaged
`src/movie_db_llm/poster_index.json.gz` index. Local IDs from an acquisition
checkout are never used as cross-catalog identities. Catalog lists and
recommendations do not wait for image downloads or call S3 themselves.

A full browse page eagerly schedules all 12 poster slots. Three card download
slots and one reserved detail slot limit concurrent poster downloads.
The [Web Locks API](https://w3c.github.io/web-locks/) shares
these slots across same-origin tabs where available, such as on localhost or
HTTPS. When Web Locks is unavailable or acquisition fails, the page uses the
same limits in its own queue. The browser cancels queued and in-flight card
downloads when leaving a results page. Recommendation pages follow the
[API result limit](../README.md#api). Each matched poster
normally makes one S3 GET with no preceding HEAD. Responses include
`Cache-Control: no-store` so fresh page loads fetch again. An unmatched film
uses the [API's 404 response](../README.md#api) without contacting S3. Each
detail opening requests its own poster, even if the card image is loading.
The backend releases its catalog database connection before the S3 fetch
and runs that fetch outside the shared worker pool used by catalog routes.

The backend owns a three-attempt maximum, counting the first request. SDK
automatic retries and S3 region redirects are disabled for poster GETs.
The retry covers the entire GET and body read, with small exponential jitter,
two-second connection and three-second socket-read timeouts. Transient network,
retryable service, truncation and checksum failures retry. Permanent errors
stop early. For object streams returned by `GetObject`, the backend reads at
most the expected size plus one byte and closes the stream after each attempt.
It checks the size and SHA-256 even when the response omits `Content-Length`,
then serves the verified bytes as JPEG, PNG, or WebP. The expected size is
capped at 2,000,000 bytes. When an image is unavailable, the browser shows
its abstract placeholder without adding a retry loop.

Set `POSTER_BUCKET` for the backend. The poster client uses `us-west-2`
(Oregon) and ignores endpoint URLs inherited from the environment or shared
AWS configuration. Without a bucket, images fall back without AWS calls.
The WSL launcher always uses the [existing project bucket](#stored-batch-and-costs),
including when Ollama handles inference. For fully offline use, run the
[standalone API with posters disabled](../README.md#optional-offline-inference).
Hosted deployment must configure its own execution role for `s3:GetObject`
on the bucket's `posters/*` objects. The owner's administrator key is for
local development.
All AWS authentication stays in the backend; no public bucket or browser
credentials are required. Public hosting remains a separate milestone.

The exact enriched manifest for the shipped index is kept in the owner's
ignored WSL archive at `.tools/poster-catalog/manifest-<sha256>.json`, where
`<sha256>` is `source_manifest_sha256` from the shipped index. From the project
root, verify the archived input checksum before rebuilding.

```sh
manifest_sha=$(python3 - <<'PY'
import gzip
import json

with gzip.open("src/movie_db_llm/poster_index.json.gz", "rt") as index:
    print(json.load(index)["source_manifest_sha256"])
PY
) &&
manifest=".tools/poster-catalog/manifest-$manifest_sha.json" &&
printf '%s  %s\n' "$manifest_sha" "$manifest" \
  | sha256sum --check &&
  uv run python scripts/build_poster_index.py "$manifest" src/movie_db_llm/poster_index.json.gz
```

Restart the API after a successful rebuild so it loads the new index. The
running process caches the mappings, and the WSL launcher's Python reload
watcher does not watch the gzip file.

To regenerate the enriched input from older acquisition reports, use each
pass's original catalog snapshot. For each verified stored row, look up its
`movie_id` in that source catalog, require the row's exact title and year to
match, and add `source_synopsis_sha256` as the SHA-256 of the full, unmodified
synopsis encoded as UTF-8. Preserve the verified filename, byte count, and
image SHA-256. Join to the delivery catalog on exact title, year, and synopsis
hash. Keep one reviewed image per joined identity when combining passes.
Retain the final enriched JSON bytes to reproduce the same recorded checksum.

The builder requires the manifest to be a JSON array of verified records.
The index includes the source manifest's checksum and is reproducibly gzipped.
The build atomically replaces the destination after writing successfully;
a failed rebuild leaves the previous index intact. A truncated package makes
posters unavailable while catalog browsing remains usable.
The [per-pass S3 reports](#stored-batch-and-costs) omit
`source_synopsis_sha256`, which the builder requires.

The Image credits section acknowledges Wikimedia Commons and includes TMDB's
required notice and an unmodified approved logo downloaded from its official
attribution page:
`https://www.themoviedb.org/assets/v4/logos/v2/blue_short-8e7b30f73a4020692ccca9c88bafe5dcb6f8a62a4c6bc55cd9ba82bb2cd95f6c.svg`.
The logo is self-hosted at `frontend/public/credits/tmdb.svg`. See
[TMDB's attribution requirements](https://developer.themoviedb.org/docs/faq).

## Catalog-wide acquisition

A Wikimedia/Wikidata inventory uses the CMU snapshot's original Freebase
identifiers and Wikidata's film poster property (P3383). Cached source and
license metadata accompany the downloaded public-domain images.

TMDB supplies the broader acquisition pass. Set `TMDB_READ_ACCESS_TOKEN` in
the environment or in the ignored `.tools/tmdb.env` file (mode 0600); do not
put credentials in source or command-line arguments. Run with the project's
Python environment:

```sh
python -m movie_db_llm.poster_import \
  --database /path/to/catalog.sqlite3 \
  --state-dir .tools/tmdb-posters
```

The catalog is read-only. `jobs.sqlite3` checkpoints each result and rejects
reuse with a changed catalog; use a separate state directory in that case.
`coverage.json` records every film, including pending, missing, ambiguous, and
failed matches; `manifest.json` records only downloaded images and includes
the TMDB identifier, matching method, source URL, byte count, and SHA-256.
`progress.json` contains counts by status. All three reports use atomic
replacement.
Images use content-addressed names under `images/`. Repeating the command
continues pending work. `--retry-errors` retries failed records, and
`--retry-review` revisits uncertain matches after improving the ID map. `--limit 10`
is useful for a small real-provider smoke check. `--workers` accepts 1–16
workers (default eight), and `--requests-per-second` accepts a rate greater
than zero and at most 20 (default eight). API requests share rate-limit
backoff, including workers already waiting. Rejected authentication stops
the campaign instead of classifying remaining films as missing.

The token is sent only in Authorization headers to the fixed TMDB API host,
never to the image CDN. Downloads accept only fixed-host paths, validate
format signatures, and follow the
[delivery format and size limits](#website-delivery).

An optional `--id-map` JSON file contains `[freebase_id, tmdb_id]` pairs
obtained from Wikidata's P646/P4947 properties. Supply `--cmu-metadata` with
the original `movie.metadata.tsv` to join those IDs to the catalog. Conflicting
identifiers and title/year keys shared by distinct CMU source films are marked
`needs_review` with reason `identity_conflict`; neither external-ID matching
nor title/year fallback can acquire those films. Source collisions are checked
even when only `--cmu-metadata` is supplied. Metadata uses literal tab fields,
preserving quoted titles and applying the catalog's title/year conversion.
The complete catalog is also checked for collisions under the matcher's title
normalization and release year, even without either optional file. These
collisions block title/year fallback while independently resolved external IDs
remain usable. Without an ID, matching requires a unique exact normalized
title and release year; a missing year requires an external ID.
An unavailable or uncertain match is reported, never replaced with another
film's image. No source can guarantee a poster for every film.

To reconcile an older checkpoint, rerun the importer with the same state
directory, supplying the CMU metadata and ID map for external-ID resolution.
Catalog collisions are detected even when those files are omitted. Every
identity conflict is reclassified before pending work is selected, including
previously stored, unavailable, and failed results, regardless of retry flags
or `--limit`.
The refreshed manifest excludes those records; existing image files remain
on disk. Replace the stored batch reports after the operator reviews the
reconciliation. This correction changes acquisition semantics, so an older
running process must finish or be paused by its operator before reconciliation.
Stored title/year fallback results for colliding catalog keys also move to
review, even if an external ID is now available. Completed external-ID results
retain their provenance when optional crosswalk inputs are omitted and only
fallback ambiguity remains. Proven source-identity or mapping conflicts still
invalidate them. Use a subsequent `--retry-review` pass to acquire newly
resolved IDs.
After correcting source identities or mappings, `--retry-review` can retry
records whose conflicts have been resolved.

After verifying the [WSL profile](website.md#deployment-administrator-profile),
use `with_afterglow_dev` from that setup to upload completed files to the
existing private bucket. Keep `--delete` out of the sync command to preserve
previously uploaded images.

```sh
with_afterglow_dev aws --region us-west-2 s3 sync .tools/tmdb-posters/images/ \
  "s3://$POSTER_BUCKET/posters/" --sse AES256 --only-show-errors
with_afterglow_dev aws --region us-west-2 s3 cp .tools/tmdb-posters/manifest.json \
  "s3://$POSTER_BUCKET/batches/tmdb/manifest.json" --sse AES256
with_afterglow_dev aws --region us-west-2 s3 cp .tools/tmdb-posters/coverage.json \
  "s3://$POSTER_BUCKET/batches/tmdb/coverage.json" --sse AES256
```

These operational files and downloaded bulk assets are ignored by Git. After
verifying the uploaded objects, follow the [index build steps](#website-delivery).
TMDB images are not public-domain assets. See
[API rate limits](https://developer.themoviedb.org/docs/rate-limiting).

## Bundled website snapshot

The starter snapshot contains 13 title-and-year matches from Wikimedia
Commons. The search candidates were reviewed rather than accepting the first
search result: results included unrelated books, logos, and an AI-generated
Nosferatu cover, which were excluded. These are source-identified public-domain
posters. The asset manifest and source files retain attribution and the
Commons license records.

`data/posters/sources.json` records the reviewed film/version/file choices.
`src/movie_db_llm/posters.json` records the source page, author, image URL,
license, SHA-256 checksum, and size. The website uses this manifest for the
hero artwork. The API uses it for [seeded discovery](../README.md#api).

`frontend/public/posters` contains the downloaded, content-addressed images.
To add another poster to the bundled snapshot, review its source, version,
and license first and add it to the sources file. Run the importer from the
repository root:

```sh
python3 scripts/import_posters.py
```

The importer is bounded to the reviewed list and 2 MB per response; it refuses
unreviewed licenses and non-image formats. It never creates a bucket or makes
an object public. To upload the images to an existing private project bucket,
use `with_afterglow_dev` after verifying the
[WSL profile](website.md#deployment-administrator-profile).

```sh
with_afterglow_dev \
  python3 scripts/import_posters.py --bucket "$POSTER_BUCKET" --region us-west-2
```

Restart the API after changing the manifest. Vite reloads its imported manifest
and image references automatically. The importer uploads only images; upload
the updated manifest separately to the same S3 prefix when updating the stored
batch.

## Stored batch and costs

The catalog-wide batch in `posters/` contains 40,315 unique images totaling
3,679,155,351 bytes and covering 39,290 of the 42,207 catalog movies. The
objects in the private `afterglow-posters-144160833822-us-west-2` bucket were
verified against the per-file size and MD5 manifest. The bucket uses SSE-S3
encryption (`AES256`) and bucket-owner-enforced ownership, with all four Block
Public Access flags enabled. The starter upload used the same `posters/`
prefix. Private batch reports under `batches/wikimedia/` and `batches/tmdb/`
record coverage and provenance for the acquisition passes. The local operator
record `.tools/poster-storage.json` identifies the acquisition bucket and
contains no credentials.

The retrieved us-west-2 AWS price catalog version `20261109154310` lists S3
Standard at USD 0.023/GB-month for the first storage tier, PUT/LIST at
USD 0.005/1,000 requests, and GET at USD 0.004/10,000 requests.
Account for the full image batch, batch reports,
upload and verification requests, and delivery GETs and data transfer when
estimating poster costs. See [S3 pricing](https://aws.amazon.com/s3/pricing/).
