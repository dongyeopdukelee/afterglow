# Movie poster snapshot

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

The catalog is read-only. `jobs.sqlite3` checkpoints each result;
`coverage.json` records every film, including pending, missing, ambiguous, and
failed matches; `manifest.json` records only downloaded images and includes
the TMDB identifier, matching method, source URL, byte count, and SHA-256.
Images use content-addressed names under `images/`. Repeating the command
continues pending work. `--retry-errors` retries failed records, and
`--retry-review` revisits uncertain matches after improving the ID map. `--limit 10`
is useful for a small real-provider smoke check. The default is eight workers
and eight API requests per second, with shared backoff for rate limits.

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
review, even if an external ID is now available. Safe external-ID results are
preserved. Use a subsequent `--retry-review` pass to acquire newly resolved IDs.
After correcting source identities or mappings, `--retry-review` can retry
records whose conflicts have been resolved.

Upload completed files to the existing private bucket using the WSL-only
deployment profile described below. Sync is additive; do not use `--delete`:

```sh
aws s3 sync .tools/tmdb-posters/images/ "s3://$POSTER_BUCKET/posters/" \
  --region us-west-2 --sse AES256 --only-show-errors
aws s3 cp .tools/tmdb-posters/manifest.json \
  "s3://$POSTER_BUCKET/batches/tmdb/manifest.json" \
  --region us-west-2 --sse AES256
aws s3 cp .tools/tmdb-posters/coverage.json \
  "s3://$POSTER_BUCKET/batches/tmdb/coverage.json" \
  --region us-west-2 --sse AES256
```

These operational files and downloaded bulk assets are ignored by Git; the
website continues using its bundled starter snapshot until display integration.
Before displaying TMDB images, include its approved logo and required notice
in an About/Credits section. TMDB images are not public-domain assets. See
[TMDB attribution and usage](https://developer.themoviedb.org/docs/faq) and
[API rate limits](https://developer.themoviedb.org/docs/rate-limiting).

## Bundled website snapshot

The first image batch contains 13 title-and-year matches from Wikimedia
Commons. The search candidates were reviewed rather than accepting the first
search result: results included unrelated books, logos, and an AI-generated
Nosferatu cover, which were excluded. These are source-identified public-domain
posters. Attribution and the Commons license records remain in the asset
manifest and source files; the page does not include a separate credits panel.

`data/posters/sources.json` records the reviewed film/version/file choices.
`src/movie_db_llm/posters.json` records the source page, author, image URL,
license, SHA-256 checksum, and size. Both the API and website use this manifest.
The API matches exact title/year pairs rather than assuming local database
identifiers are permanent or attaching an original's poster to a remake.

`frontend/public/posters` contains the downloaded, content-addressed images.
Bundled copies make the local website reproducible and avoid visitor-time
matching, Wikimedia availability, AWS credentials, or expiring signed URLs.
Unmatched movies and failed image requests show the abstract CSS fallback.
To add another poster, review its source/version/license first, add it to the
sources file, then run from the repository root:

```sh
python3 scripts/import_posters.py
```

The importer is bounded to the reviewed list and 2 MB per response; it refuses
unreviewed licenses and non-image formats. It never creates a bucket or makes
an object public. To upload the images to an existing private project bucket,
use the WSL-only [deployment profile](website.md#deployment-administrator-profile):

```sh
AWS_CONFIG_FILE=/home/dukelee/.config/afterglow/aws/config \
AWS_PROFILE=afterglow \
  python3 scripts/import_posters.py --bucket "$POSTER_BUCKET" --region us-west-2
```

Restart the API after changing the manifest. Vite reloads its imported manifest
and image references automatically. The importer uploads only images; upload
the updated manifest separately to the same S3 prefix when updating the stored
batch. A future public deployment can serve these objects through its frontend
distribution; a public bucket is not needed.

## Stored batch and costs

The initial batch was uploaded on 2026-11-15 to the private Afterglow poster
bucket in `us-west-2` (Oregon), with all four Block Public Access flags enabled,
bucket-owner-enforced ownership, and SSE-S3 encryption. The stored objects
are the 13 poster files under `posters/` and their manifest. The local operator
record `.tools/poster-storage.json` records the exact bucket for this host;
it contains no credentials. Local previews use the checked-in copies.

The poster files total **1,526,686 bytes**. The retrieved us-west-2 AWS price
catalog version `20261109154310` lists S3 Standard at USD 0.023/GB-month for
the first storage tier, PUT/LIST at USD 0.005/1,000 requests, and GET at
USD 0.004/10,000 requests. The image bytes therefore cost about USD
0.000033/month to store; the initial 13 image PUTs cost USD 0.000065.
The small manifest, verification requests, and any later delivery/transfer
charges are additional. This is an asset-storage estimate, not a full hosting
estimate. See [S3 pricing](https://aws.amazon.com/s3/pricing/).
