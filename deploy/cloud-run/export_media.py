"""Export every object in the private Supabase media bucket with a checksum manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

import boto3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument(
        "--endpoint",
        default="https://jxtozsdctdwzjfdfcthr.storage.supabase.co/storage/v1/s3",
        help="Supabase Storage S3 endpoint",
    )
    parser.add_argument("--region", default="us-west-2")
    parser.add_argument("--bucket", default="mealie-media")
    return parser.parse_args()


def required_environment(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def safe_destination(root: Path, key: str) -> Path:
    object_path = PurePosixPath(key)
    if object_path.is_absolute() or ".." in object_path.parts:
        raise ValueError(f"Unsafe object key: {key!r}")

    destination = root.joinpath(*object_path.parts).resolve()
    if not destination.is_relative_to(root.resolve()):
        raise ValueError(f"Object key escapes the backup directory: {key!r}")
    return destination


def export_bucket(args: argparse.Namespace) -> dict[str, Any]:
    output_directory: Path = args.output_directory.resolve()
    if output_directory.exists() and any(output_directory.iterdir()):
        raise FileExistsError(f"Output directory is not empty: {output_directory}")
    output_directory.mkdir(parents=True, exist_ok=True)

    client = boto3.client(
        "s3",
        endpoint_url=args.endpoint,
        region_name=args.region,
        aws_access_key_id=required_environment("SUPABASE_STORAGE_S3_ACCESS_KEY_ID"),
        aws_secret_access_key=required_environment("SUPABASE_STORAGE_S3_SECRET_ACCESS_KEY"),
    )

    objects: list[dict[str, Any]] = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=args.bucket):
        for item in page.get("Contents", []):
            key = item["Key"]
            destination = safe_destination(output_directory, key)
            destination.parent.mkdir(parents=True, exist_ok=True)

            digest = hashlib.sha256()
            response = client.get_object(Bucket=args.bucket, Key=key)
            with destination.open("wb") as output_file:
                for chunk in response["Body"].iter_chunks(chunk_size=1024 * 1024):
                    if chunk:
                        output_file.write(chunk)
                        digest.update(chunk)

            objects.append(
                {
                    "key": key,
                    "size": destination.stat().st_size,
                    "sha256": digest.hexdigest(),
                    "etag": str(item.get("ETag", "")).strip('"'),
                    "last_modified": item["LastModified"].isoformat(),
                }
            )

    manifest = {
        "format": "family-recipes-supabase-media-backup-v1",
        "created_at": datetime.now().astimezone().isoformat(),
        "bucket": args.bucket,
        "endpoint": args.endpoint,
        "region": args.region,
        "object_count": len(objects),
        "total_bytes": sum(item["size"] for item in objects),
        "objects": objects,
    }
    manifest_path = output_directory / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    args = parse_args()
    manifest = export_bucket(args)
    sys.stdout.write(f"Exported {manifest['object_count']} objects ({manifest['total_bytes']} bytes)\n")


if __name__ == "__main__":
    main()
