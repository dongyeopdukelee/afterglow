# Movie poster snapshot

## Website delivery

The website now uses the catalog-wide collection for movie cards and detail
views. The acquired batch contains 40,315 unique images covering 39,290 of the
42,207 catalog movies. All batch images are stored under `posters/` in the
private `afterglow-posters-144160833822-us-west-2` bucket. The two decorative
hero films retain their bundled starter images.

Each card loads `GET /movies/{movie_id}/poster`. The backend matches exact
title/year and a SHA-256 fingerprint of the source synopsis using the packaged
`src/movie_db_llm/poster_index.json.gz` index. Local IDs from an acquisition
checkout are never used as cross-catalog identities. Catalog lists and
recommendations do not wait for image downloads or call S3 themselves.

A 12-card browse page eagerly requests all 12 poster slots. Each matched
poster makes one normal S3 GET, without a preceding HEAD. Responses use
`Cache-Control: no-store` so fresh page loads fetch again. Unmatched films
return 404 without contacting S3. Poster failures affect only that image.

The backend owns a three-attempt maximum, counting the first request. SDK
automatic retries are disabled for this client to prevent multiplying the cap.
The retry covers the entire GET and body read, with small exponential jitter,
two-second connection and three-second socket-read timeouts. Transient network,
429/5xx/throttling, truncation and checksum failures retry; permanent errors
stop early. Each attempt closes its response body. Bytes are bounded to the
verified expected size (at most 2 MB), checked against SHA-256, then returned
with a raster content type. Failed posters return a generic 503, and the
browser uses its abstract placeholder without adding another retry loop.

Set `POSTER_BUCKET` and optionally `POSTER_REGION` (default `us-west-2`) for
the backend. Without a bucket, images fall back without AWS calls. The WSL
launcher selects the existing project bucket automatically. Hosted deployment
must configure its own execution role for `s3:GetObject` on this bucket's
`posters/*` objects; it must not use the owner's local administrator key.
Compose passes the settings through but does not mount host credentials.
All AWS authentication stays in the backend; no public bucket or browser
credentials are required. Public hosting remains a separate milestone.

Generate the compact metadata index from a verified acquisition manifest:

```sh
uv run python scripts/build_poster_index.py /path/to/poster-catalog/manifest.json \
  src/movie_db_llm/poster_index.json.gz
```

The index includes the source manifest's checksum and is reproducibly gzipped.
Keep the acquisition manifests, per-file provenance and verification reports
with the private S3 batch. The repo contains metadata, not the bulk images.

The Image credits section includes TMDB's required notice and an unmodified
approved logo downloaded from its official attribution page:
`https://www.themoviedb.org/assets/v4/logos/v2/blue_short-8e7b30f73a4020692ccca9c88bafe5dcb6f8a62a4c6bc55cd9ba82bb2cd95f6c.svg`.
The logo is self-hosted at `frontend/public/credits/tmdb.svg`. See
[TMDB's attribution requirements](https://developer.themoviedb.org/docs/faq).

## Catalog-wide acquisition

The bundled website snapshot below is separate from the catalog-wide storage
job. The current catalog contains 42,207 films. A Wikimedia/Wikidata inventory
uses the CMU snapshot's original Freebase identifiers and Wikidata's film
poster property (P3383), rather than general image search. Cached source and
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
never to the image CDN. Downloads accept only fixed-host JPEG, PNG, or WebP
paths, validate format signatures, and are bounded to 2 MB per image.

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

These operational files and downloaded bulk assets are ignored by Git; the
backend's packaged metadata index connects the verified batch to the website.
TMDB's approved logo and required notice appear in the Image credits section.
TMDB images are not public-domain assets. See
[TMDB attribution and usage](https://developer.themoviedb.org/docs/faq) and
[API rate limits](https://developer.themoviedb.org/docs/rate-limiting).

## Bundled website snapshot

The decorative hero's starter image batch contains 13 title-and-year matches from Wikimedia
Commons. The search candidates were reviewed rather than accepting the first
search result: results included unrelated books, logos, and an AI-generated
Nosferatu cover, which were excluded. These are source-identified public-domain
posters. Attribution and the Commons license records remain in the asset
manifest and source files; the page does not include a separate credits panel.

`data/posters/sources.json` records the reviewed film/version/file choices.
`src/movie_db_llm/posters.json` records the source page, author, image URL,
license, SHA-256 checksum, and size. Both the API and website use this manifest.
The API retains those title/year pairs for its existing discovery priority.
Bulk delivery uses the stronger title/year/synopsis identity described above.

`frontend/public/posters` contains the downloaded, content-addressed images.
Bundled copies keep the decorative hero independent of image-service availability.
Catalog cards and details use the S3 route above. Unmatched movies and failed
image requests show the abstract CSS fallback.
To add another poster, review its source/version/license first, add it to the
sources file, then run from the repository root:

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
batch. A public bucket is not needed for the authenticated backend proxy.

## Stored batch and costs

The initial batch was uploaded on 2026-11-15 to the private Afterglow poster
bucket in `us-west-2` (Oregon), with all four Block Public Access flags enabled,
bucket-owner-enforced ownership, and SSE-S3 encryption. That starter upload
contained the bundled poster files under `posters/` and their manifest;
it does not describe the bucket's later catalog-wide acquisitions. Stored
batch reports, including those under `batches/wikimedia/` and `batches/tmdb/`,
record coverage and provenance for the corresponding acquisition passes.
The local operator record `.tools/poster-storage.json` records the exact
bucket for the acquisition host; it contains no credentials. Local previews
now fetch catalog posters through the private S3 backend; hero assets remain bundled.

The bundled snapshot's poster files total **1,526,686 bytes**. The retrieved
us-west-2 AWS price catalog version `20261109154310` lists S3 Standard at
USD 0.023/GB-month for the first storage tier, PUT/LIST at
USD 0.005/1,000 requests, and GET at
USD 0.004/10,000 requests. The image bytes therefore cost about USD
0.000033/month to store; the initial 13 image PUTs cost USD 0.000065.
The small manifest, verification requests, and any later delivery/transfer
charges are additional. This is an asset-storage estimate, not a full hosting
estimate. See [S3 pricing](https://aws.amazon.com/s3/pricing/).
