from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError

from mealie.services.storage.object_storage import LocalObjectStorage, S3ObjectStorage


class FakePaginator:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects

    def paginate(self, Bucket: str, Prefix: str):
        del Bucket
        yield {"Contents": [{"Key": key} for key in self.objects if key.startswith(Prefix)]}


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.deleted_batches: list[list[dict[str, str]]] = []

    def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]:
        return {"Contents": [], "Request": kwargs}

    def upload_file(self, filename: str, bucket: str, key: str, ExtraArgs: dict[str, str]) -> None:
        del bucket, ExtraArgs
        self.objects[key] = Path(filename).read_bytes()

    def download_file(self, bucket: str, key: str, filename: str) -> None:
        del bucket
        if key not in self.objects:
            raise ClientError(
                {"Error": {"Code": "NoSuchKey"}, "ResponseMetadata": {"HTTPStatusCode": 404}},
                "GetObject",
            )
        Path(filename).write_bytes(self.objects[key])

    def get_paginator(self, operation: str) -> FakePaginator:
        assert operation == "list_objects_v2"
        return FakePaginator(self.objects)

    def delete_object(self, Bucket: str, Key: str) -> None:
        del Bucket
        self.objects.pop(Key, None)

    def delete_objects(self, Bucket: str, Delete: dict[str, Any]) -> None:
        del Bucket
        batch = Delete["Objects"]
        self.deleted_batches.append(batch)
        for item in batch:
            self.objects.pop(item["Key"], None)


def test_local_storage_preserves_filesystem_behavior(tmp_path: Path) -> None:
    path = tmp_path / "image.webp"
    storage = LocalObjectStorage()

    assert not storage.enabled
    assert not storage.materialize(path)
    path.write_bytes(b"image")
    assert storage.materialize(path)


def test_s3_storage_uploads_and_materializes_file(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    image = data_dir / "recipes" / "abc" / "images" / "original.webp"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"original image")
    client = FakeS3Client()
    storage = S3ObjectStorage(data_dir, "mealie-media", client)  # type: ignore[arg-type]

    storage.validate()
    storage.upload(image)
    assert client.objects["recipes/abc/images/original.webp"] == b"original image"

    image.unlink()
    assert storage.materialize(image)
    assert image.read_bytes() == b"original image"


def test_s3_storage_returns_false_for_missing_object(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    storage = S3ObjectStorage(data_dir, "mealie-media", FakeS3Client())  # type: ignore[arg-type]

    assert not storage.materialize(data_dir / "recipes" / "missing.webp")


def test_s3_storage_rejects_paths_outside_data_directory(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    outside = tmp_path / "outside.webp"
    outside.write_bytes(b"outside")
    storage = S3ObjectStorage(data_dir, "mealie-media", FakeS3Client())  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="inside"):
        storage.upload(outside)


def test_s3_storage_materializes_and_deletes_prefix(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    client = FakeS3Client()
    client.objects = {
        "recipes/abc/images/original.webp": b"original",
        "recipes/abc/assets/source.pdf": b"pdf",
        "recipes/other/images/original.webp": b"other",
    }
    storage = S3ObjectStorage(data_dir, "mealie-media", client)  # type: ignore[arg-type]
    recipe_dir = data_dir / "recipes" / "abc"

    storage.materialize_prefix(recipe_dir)
    assert (recipe_dir / "images" / "original.webp").read_bytes() == b"original"
    assert (recipe_dir / "assets" / "source.pdf").read_bytes() == b"pdf"

    storage.delete_prefix(recipe_dir)
    assert "recipes/abc/images/original.webp" not in client.objects
    assert "recipes/abc/assets/source.pdf" not in client.objects
    assert "recipes/other/images/original.webp" in client.objects


def test_materialize_and_delete_data_root(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    client = FakeS3Client()
    client.objects = {
        "recipes/one/images/original.webp": b"one",
        "users/two/profile.webp": b"two",
    }
    storage = S3ObjectStorage(data_dir, "media", client)

    storage.materialize_prefix(data_dir)

    assert (data_dir / "recipes/one/images/original.webp").read_bytes() == b"one"
    assert (data_dir / "users/two/profile.webp").read_bytes() == b"two"

    storage.delete_prefix(data_dir)
    assert client.objects == {}
