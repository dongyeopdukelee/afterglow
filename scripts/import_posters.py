"""Fetch a bounded, reviewed set of Commons posters for the CMU snapshot.

No visitor request searches the web. Exact film/version matches are reviewed
in data/posters/sources.json; Wikimedia supplies provenance and license data.
Run from the repository root. Upload to an existing private S3 bucket with
--bucket; the normal AWS CLI credential chain stays outside the frontend.
"""

import argparse
import hashlib
import html
import json
import re
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

USER_AGENT = (
    "AfterglowPosterImport/0.1 (https://github.com/dongyeopdukelee/afterglow)"
)
ROOT = Path(__file__).resolve().parents[1]
MAX_IMAGE_BYTES = 2_000_000


def plain_text(value: str) -> str:
    """Preserve attribution as text, never third-party HTML."""
    return html.unescape(re.sub(r"<[^>]*>", "", value)).strip()


def fetch(url: str) -> bytes:
    """Fetch only HTTPS Wikimedia media with bounded response bodies."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in {
        "commons.wikimedia.org",
        "upload.wikimedia.org",
        "thumb.wikimedia.org",
    }:
        raise ValueError("Expected an HTTPS Wikimedia URL")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read(MAX_IMAGE_BYTES + 1)
    if len(body) > MAX_IMAGE_BYTES:
        raise ValueError("Poster response exceeds the import size limit")
    return body


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", help="Upload to this existing S3 bucket")
    parser.add_argument("--region", default="us-west-2")
    args = parser.parse_args()
    sources = json.loads((ROOT / "data/posters/sources.json").read_text())
    destination = ROOT / "frontend/public/posters"
    destination.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, object]] = []
    for source in sources:
        params = urllib.parse.urlencode(
            {
                "action": "query",
                "format": "json",
                "titles": "File:" + source["file"],
                "prop": "imageinfo",
                "iiprop": "url|extmetadata|mime",
                "iiurlwidth": 480,
            }
        )
        payload = json.loads(
            fetch("https://commons.wikimedia.org/w/api.php?" + params)
        )
        pages = list(payload.get("query", {}).get("pages", {}).values())
        if len(pages) != 1 or "imageinfo" not in pages[0]:
            raise ValueError(f"Missing Commons file: {source['file']}")
        info = pages[0]["imageinfo"][0]
        metadata = info["extmetadata"]
        license_name = metadata.get("LicenseShortName", {}).get("value")
        if license_name != "Public domain":
            raise ValueError(f"Review license before using {source['file']}")
        mime = info.get("thumbmime", info["mime"])
        extension = {
            "image/jpeg": "jpg",
            "image/png": "png",
            "image/webp": "webp",
        }.get(mime)
        if extension is None:
            raise ValueError("Only JPEG, PNG and WebP posters are supported")
        image_url = info.get("thumburl", info["url"])
        body = fetch(image_url)
        checksum = hashlib.sha256(body).hexdigest()
        filename = f"{checksum[:20]}.{extension}"
        path = destination / filename
        path.write_bytes(body)
        entry: dict[str, object] = {
            "title": source["title"],
            "year": source["year"],
            "filename": filename,
            "source_url": info["descriptionurl"],
            "image_url": image_url,
            "license": license_name,
            "author": plain_text(
                metadata.get("Artist", {}).get(
                    "value", "Not specified on source page"
                )
            ),
            "sha256": checksum,
            "bytes": len(body),
        }
        entries.append(entry)
        print(
            f"Matched {source['title']} ({source['year']}): {len(body)} bytes"
        )
        if args.bucket:
            subprocess.run(
                [
                    "aws",
                    "--region",
                    args.region,
                    "s3api",
                    "put-object",
                    "--bucket",
                    args.bucket,
                    "--key",
                    f"posters/{filename}",
                    "--body",
                    str(path),
                    "--content-type",
                    mime,
                    "--cache-control",
                    "public,max-age=31536000,immutable",
                    "--server-side-encryption",
                    "AES256",
                    "--no-cli-pager",
                ],
                check=True,
                stdout=subprocess.DEVNULL,
            )
    manifest = ROOT / "src/movie_db_llm/posters.json"
    manifest.write_text(json.dumps(entries, indent=2) + "\n")
    print(f"Saved {len(entries)} verified poster records to {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
