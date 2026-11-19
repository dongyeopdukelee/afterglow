"""Bounded reads of private S3 posters; failures never block catalog results."""

import logging
import random
import time
from functools import lru_cache
from hashlib import sha256
from threading import Lock
from typing import Protocol, TypedDict, cast

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from botocore.response import StreamingBody

from movie_db_llm.config import POSTER_BUCKET, POSTER_REGION
from movie_db_llm.posters import PosterAsset

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 3
_store_lock = Lock()


class S3Object(TypedDict):
    Body: StreamingBody
    ContentLength: int


class PosterS3Client(Protocol):
    def get_object(self, *, Bucket: str, Key: str) -> S3Object: ...


class PosterUnavailableError(Exception):
    """A noncritical poster could not be fetched and verified."""


class PosterStore:
    def __init__(self, client: PosterS3Client, bucket: str) -> None:
        self.client = client
        self.bucket = bucket

    def fetch(self, asset: PosterAsset) -> bytes:
        """Retry the entire GET/read at most three times, then omit the image."""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = self.client.get_object(
                    Bucket=self.bucket, Key=f"posters/{asset.filename}"
                )
                # Read before sending HTTP headers, so truncated streams can
                # retry. Always close bodies, including invalid/oversized ones.
                with response["Body"] as body:
                    if response["ContentLength"] != asset.size:
                        raise PosterUnavailableError("Poster size mismatch")
                    data = body.read(asset.size + 1)
                if (
                    len(data) != asset.size
                    or sha256(data).hexdigest() != asset.sha256
                ):
                    raise PosterUnavailableError("Poster checksum mismatch")
                return data
            except ClientError as error:
                status = error.response.get("ResponseMetadata", {}).get(
                    "HTTPStatusCode", 0
                )
                code = error.response.get("Error", {}).get("Code", "")
                if status not in (429, 500, 502, 503, 504) and code not in (
                    "SlowDown",
                    "RequestTimeout",
                    "Throttling",
                    "InternalError",
                ):
                    raise PosterUnavailableError(
                        "Poster unavailable"
                    ) from error
            except BotoCoreError, OSError, PosterUnavailableError:
                pass
            logger.warning(
                "Poster fetch failed", extra={"poster_attempt": attempt}
            )
            if attempt < MAX_ATTEMPTS:
                time.sleep(random.uniform(0, 0.2 * 2 ** (attempt - 1)))
        raise PosterUnavailableError("Poster unavailable after three attempts")


def poster_store() -> PosterStore:
    with _store_lock:
        return cached_poster_store()


@lru_cache(maxsize=1)
def cached_poster_store() -> PosterStore:
    """Reuse the SDK connection pool and let it refresh runtime credentials."""
    client = cast(
        PosterS3Client,
        boto3.Session().client(
            "s3",
            region_name=POSTER_REGION,
            config=Config(
                # One loop owns the complete GET + body read, preventing retries
                # in the SDK from multiplying the three application attempts.
                retries={"total_max_attempts": 1, "mode": "standard"},
                connect_timeout=2,
                read_timeout=3,
                max_pool_connections=16,
            ),
        ),
    )
    return PosterStore(client, POSTER_BUCKET)
