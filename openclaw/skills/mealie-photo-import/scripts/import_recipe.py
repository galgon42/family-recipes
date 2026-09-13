#!/usr/bin/env python3
"""Upload recipe photos to Mealie's AI recipe import endpoint."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Validate inputs without creating a recipe")
    mode.add_argument("--confirm", action="store_true", help="Create the recipe after user authorization")
    parser.add_argument("--image", action="append", required=True, help="Recipe image path; repeat for multiple pages")
    parser.add_argument("--notes", default="", help="Optional notes or corrections to merge with the photos")
    parser.add_argument("--language", default="", help="Optional language for the imported recipe")
    parser.add_argument(
        "--create-new-organizers",
        action="store_true",
        help="Allow Mealie to create new tags, categories, and tools",
    )
    return parser.parse_args()


def validate_environment() -> tuple[str, str, str]:
    base_url = os.environ.get("MEALIE_URL", "").strip().rstrip("/")
    token = os.environ.get("MEALIE_API_TOKEN", "").strip()
    group_slug = os.environ.get("MEALIE_GROUP_SLUG", "home").strip() or "home"

    if not base_url:
        raise ValueError("MEALIE_URL is not configured")
    if not base_url.startswith(("https://", "http://localhost", "http://127.0.0.1")):
        raise ValueError("MEALIE_URL must use HTTPS unless it points to localhost")
    if not token:
        raise ValueError("MEALIE_API_TOKEN is not configured")
    return base_url, token, group_slug


def validate_images(raw_paths: list[str]) -> list[tuple[Path, str]]:
    images: list[tuple[Path, str]] = []
    for raw_path in raw_paths:
        path = Path(raw_path).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"Image does not exist: {path}")
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if not media_type.startswith("image/"):
            raise ValueError(f"File is not a recognized image: {path}")
        images.append((path, media_type))
    return images


def multipart_body(
    images: list[tuple[Path, str]], notes: str, language: str, create_new_organizers: bool
) -> tuple[bytes, str]:
    boundary = f"----openclaw-mealie-{uuid.uuid4().hex}"
    chunks: list[bytes] = []

    def add_text(name: str, value: str) -> None:
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
                value.encode("utf-8"),
                b"\r\n",
            ]
        )

    if notes:
        add_text("content", notes)
    if language:
        add_text("translateLanguage", language)
    add_text("createNewOrganizers", "true" if create_new_organizers else "false")

    for path, media_type in images:
        safe_name = path.name.replace('"', "_")
        chunks.extend(
            [
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="images"; filename="{safe_name}"\r\n'.encode(),
                f"Content-Type: {media_type}\r\n\r\n".encode(),
                path.read_bytes(),
                b"\r\n",
            ]
        )

    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), boundary


def create_recipe(
    base_url: str,
    token: str,
    group_slug: str,
    images: list[tuple[Path, str]],
    notes: str,
    language: str,
    create_new_organizers: bool,
) -> dict[str, str]:
    body, boundary = multipart_body(images, notes, language, create_new_organizers)
    request = urllib.request.Request(
        f"{base_url}/api/recipes/create/ai",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=360) as response:
            response_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Mealie returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach Mealie: {exc.reason}") from exc

    try:
        slug = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Mealie returned an unexpected response: {response_body[:500]}") from exc
    if not isinstance(slug, str) or not slug:
        raise RuntimeError(f"Mealie did not return a recipe slug: {response_body[:500]}")

    return {"slug": slug, "url": f"{base_url}/g/{group_slug}/r/{slug}"}


def main() -> int:
    args = parse_args()
    try:
        base_url, token, group_slug = validate_environment()
        images = validate_images(args.image)
        if args.check:
            print(
                json.dumps(
                    {
                        "ok": True,
                        "mode": "check",
                        "imageCount": len(images),
                        "images": [str(path) for path, _ in images],
                        "hasNotes": bool(args.notes),
                        "language": args.language or None,
                    }
                )
            )
            return 0

        result = create_recipe(
            base_url,
            token,
            group_slug,
            images,
            args.notes,
            args.language,
            args.create_new_organizers,
        )
        print(json.dumps({"ok": True, **result}))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
