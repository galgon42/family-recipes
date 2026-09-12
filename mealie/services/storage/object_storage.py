import logging
import mimetypes
from abc import ABC, abstractmethod
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import uuid4

import boto3
from botocore.client import BaseClient
from botocore.config import Config
from botocore.exceptions import ClientError

from mealie.core.config import get_app_dirs, get_app_settings

logger = logging.getLogger(__name__)


class ObjectStorage(ABC):
    """Durable storage for files represented by paths beneath Mealie's data directory."""

    @property
    @abstractmethod
    def enabled(self) -> bool: ...

    @abstractmethod
    def validate(self) -> None: ...

    @abstractmethod
    def upload(self, path: Path) -> None: ...

    @abstractmethod
    def materialize(self, path: Path) -> bool: ...

    @abstractmethod
    def materialize_prefix(self, path: Path) -> None: ...

    @abstractmethod
    def delete(self, path: Path) -> None: ...

    @abstractmethod
    def delete_prefix(self, path: Path) -> None: ...

    @abstractmethod
    def upload_tree(self, path: Path) -> None: ...


class LocalObjectStorage(ObjectStorage):
    """No-op backend preserving Mealie's normal local-filesystem behavior."""

    @property
    def enabled(self) -> bool:
        return False

    def validate(self) -> None:
        return None

    def upload(self, path: Path) -> None:
        return None

    def materialize(self, path: Path) -> bool:
        return path.is_file()

    def materialize_prefix(self, path: Path) -> None:
        return None

    def delete(self, path: Path) -> None:
        return None

    def delete_prefix(self, path: Path) -> None:
        return None

    def upload_tree(self, path: Path) -> None:
        return None


class S3ObjectStorage(ObjectStorage):
    def __init__(self, data_dir: Path, bucket: str, client: BaseClient) -> None:
        self.data_dir = data_dir.resolve()
        self.bucket = bucket
        self.client = client

    @property
    def enabled(self) -> bool:
        return True

    def validate(self) -> None:
        self.client.list_objects_v2(Bucket=self.bucket, MaxKeys=1)

    def _key(self, path: Path) -> str:
        try:
            relative = path.resolve().relative_to(self.data_dir)
        except ValueError as exc:
            raise ValueError(f"Object-storage path must be inside {self.data_dir}") from exc
        return relative.as_posix()

    def _path(self, key: str) -> Path:
        target = (self.data_dir / Path(key)).resolve()
        try:
            target.relative_to(self.data_dir)
        except ValueError as exc:
            raise ValueError("Object-storage key resolves outside the data directory") from exc
        return target

    @staticmethod
    def _is_missing(exc: ClientError) -> bool:
        response = exc.response
        status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        code = str(response.get("Error", {}).get("Code", ""))
        return status == 404 or code in {"404", "NoSuchKey", "NotFound"}

    def upload(self, path: Path) -> None:
        if not path.is_file():
            raise FileNotFoundError(path)

        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.client.upload_file(
            str(path),
            self.bucket,
            self._key(path),
            ExtraArgs={"ContentType": content_type},
        )

    def materialize(self, path: Path) -> bool:
        if path.is_file():
            return True

        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.download-{uuid4().hex}")
        try:
            self.client.download_file(self.bucket, self._key(path), str(temporary))
            temporary.replace(path)
        except ClientError as exc:
            temporary.unlink(missing_ok=True)
            if self._is_missing(exc):
                return False
            raise
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return True

    def _objects(self, prefix_path: Path) -> Iterator[dict[str, Any]]:
        key = self._key(prefix_path)
        prefix = "" if key == "." else key.rstrip("/") + "/"
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            yield from page.get("Contents", [])

    def materialize_prefix(self, path: Path) -> None:
        for item in self._objects(path):
            key = item.get("Key")
            if not key or key.endswith("/"):
                continue
            target = self._path(key)
            self.materialize(target)

    def delete(self, path: Path) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self._key(path))

    def delete_prefix(self, path: Path) -> None:
        # Supabase's S3 gateway supports DeleteObject but rejects the optional
        # AWS multi-object DeleteObjects operation. Resolve the complete key
        # list first so deleting a page cannot invalidate pagination tokens.
        keys = [str(item["Key"]) for item in self._objects(path) if item.get("Key")]
        for key in keys:
            self.client.delete_object(Bucket=self.bucket, Key=key)

    def upload_tree(self, path: Path) -> None:
        if not path.exists():
            return
        for child in path.rglob("*"):
            if child.is_file():
                self.upload(child)


@lru_cache
def get_object_storage() -> ObjectStorage:
    settings = get_app_settings()
    if not settings.SUPABASE_STORAGE_ENABLED:
        return LocalObjectStorage()

    client = boto3.client(
        "s3",
        endpoint_url=settings.SUPABASE_STORAGE_S3_ENDPOINT,
        region_name=settings.SUPABASE_STORAGE_S3_REGION,
        aws_access_key_id=settings.SUPABASE_STORAGE_S3_ACCESS_KEY_ID,
        aws_secret_access_key=settings.SUPABASE_STORAGE_S3_SECRET_ACCESS_KEY,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    return S3ObjectStorage(get_app_dirs().DATA_DIR, settings.SUPABASE_STORAGE_BUCKET or "", client)
