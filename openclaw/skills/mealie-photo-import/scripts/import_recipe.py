#!/usr/bin/env python3
"""Create a Mealie recipe from schema.org JSON without using Mealie AI."""

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
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Validate inputs without creating a recipe")
    mode.add_argument("--confirm", action="store_true", help="Create the recipe after user authorization")
    parser.add_argument("--recipe-json", required=True, help="Path to a schema.org Recipe JSON object")
    parser.add_argument("--image", action="append", default=[], help="Source image path; first image becomes the cover")
    parser.add_argument("--source-url", default="", help="Optional source URL saved with the recipe")
    parser.add_argument("--include-tags", action="store_true", help="Import recognized keywords as tags")
    parser.add_argument("--include-categories", action="store_true", help="Import recognized categories")
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


def load_recipe(raw_path: str) -> tuple[Path, dict[str, Any]]:
    path = Path(raw_path).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"Recipe JSON does not exist: {path}")
    try:
        recipe = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Recipe file is not valid JSON: {exc}") from exc
    if not isinstance(recipe, dict):
        raise ValueError("Recipe JSON must contain one object")
    if not isinstance(recipe.get("name"), str) or not recipe["name"].strip():
        raise ValueError("Recipe JSON must contain a non-empty name")
    if recipe.get("@type") not in (None, "Recipe"):
        raise ValueError('Recipe JSON "@type" must be "Recipe"')
    recipe.setdefault("@context", "https://schema.org")
    recipe.setdefault("@type", "Recipe")
    return path, recipe


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


def send_request(request: urllib.request.Request, timeout: int) -> str:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Mealie returned HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach Mealie: {exc.reason}") from exc


def json_request(url: str, token: str, payload: dict[str, Any], timeout: int = 60) -> str:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    return send_request(request, timeout)


def multipart_body(image: tuple[Path, str]) -> tuple[bytes, str]:
    path, media_type = image
    boundary = f"----openclaw-mealie-{uuid.uuid4().hex}"
    safe_name = path.name.replace('"', "_")
    extension = path.suffix.lstrip(".").lower() or "bin"
    chunks = [
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="extension"\r\n\r\n',
        extension.encode(),
        b"\r\n",
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="image"; filename="{safe_name}"\r\n'.encode(),
        f"Content-Type: {media_type}\r\n\r\n".encode(),
        path.read_bytes(),
        b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ]
    return b"".join(chunks), boundary


def upload_cover(base_url: str, token: str, slug: str, image: tuple[Path, str]) -> None:
    body, boundary = multipart_body(image)
    request = urllib.request.Request(
        f"{base_url}/api/recipes/{slug}/image",
        data=body,
        method="PUT",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
        },
    )
    send_request(request, timeout=120)


def create_recipe(
    base_url: str,
    token: str,
    group_slug: str,
    recipe: dict[str, Any],
    images: list[tuple[Path, str]],
    source_url: str,
    include_tags: bool,
    include_categories: bool,
) -> dict[str, Any]:
    response_body = json_request(
        f"{base_url}/api/recipes/create/html-or-json",
        token,
        {
            "data": json.dumps(recipe, ensure_ascii=False),
            "url": source_url or None,
            "includeTags": include_tags,
            "includeCategories": include_categories,
        },
        timeout=120,
    )
    try:
        slug = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Mealie returned an unexpected response: {response_body[:500]}") from exc
    if not isinstance(slug, str) or not slug:
        raise RuntimeError(f"Mealie did not return a recipe slug: {response_body[:500]}")

    result: dict[str, Any] = {
        "slug": slug,
        "url": f"{base_url}/g/{group_slug}/r/{slug}",
        "coverUploaded": False,
    }
    if images:
        try:
            upload_cover(base_url, token, slug, images[0])
            result["coverUploaded"] = True
        except RuntimeError as exc:
            result["coverError"] = str(exc)
    return result


def count_items(value: Any) -> int:
    return len(value) if isinstance(value, list) else int(bool(value))


def main() -> int:
    args = parse_args()
    try:
        base_url, token, group_slug = validate_environment()
        recipe_path, recipe = load_recipe(args.recipe_json)
        images = validate_images(args.image)
        if args.check:
            print(
                json.dumps(
                    {
                        "ok": True,
                        "mode": "check",
                        "recipeFile": str(recipe_path),
                        "name": recipe["name"],
                        "ingredientCount": count_items(recipe.get("recipeIngredient")),
                        "instructionCount": count_items(recipe.get("recipeInstructions")),
                        "imageCount": len(images),
                        "coverImage": str(images[0][0]) if images else None,
                    },
                    ensure_ascii=False,
                )
            )
            return 0

        result = create_recipe(
            base_url,
            token,
            group_slug,
            recipe,
            images,
            args.source_url,
            args.include_tags,
            args.include_categories,
        )
        print(json.dumps({"ok": True, **result}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
