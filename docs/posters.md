# Movie poster snapshot

The first image batch contains 13 title-and-year matches from Wikimedia
Commons. The search candidates were reviewed rather than accepting the first
search result: results included unrelated books, logos, and an AI-generated
Nosferatu cover, which were excluded. These are source-identified public-domain
posters. Attribution and the Commons license record remain available under
**Poster credits** in the website footer.

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
