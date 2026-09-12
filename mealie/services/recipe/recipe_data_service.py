import asyncio
import shutil
from logging import Logger
from pathlib import Path

from pydantic import UUID4

from mealie.pkgs import img, safehttp
from mealie.schema.recipe.recipe import Recipe
from mealie.schema.recipe.recipe_image_types import RecipeImageTypes
from mealie.services._base_service import BaseService
from mealie.services.storage import get_object_storage


async def gather_with_concurrency(n, *coros, ignore_exceptions=False):
    semaphore = asyncio.Semaphore(n)

    async def sem_coro(coro):
        async with semaphore:
            return await coro

    results = await asyncio.gather(*(sem_coro(c) for c in coros), return_exceptions=ignore_exceptions)
    if ignore_exceptions:
        results = [r for r in results if not isinstance(r, Exception)]
    return results


async def largest_content_len(urls: list[str]) -> tuple[str, int]:
    largest_url = ""
    largest_len = 0

    max_concurrency = 10

    tasks = [safehttp.resilient_fetch(url, method="HEAD") for url in urls]
    responses: list[safehttp.FetchResult | None] = await gather_with_concurrency(
        max_concurrency, *tasks, ignore_exceptions=True
    )
    for response in responses:
        if response is None:
            continue

        len_int = int(response.headers.get("Content-Length", 0))
        if len_int > largest_len:
            largest_url = response.url
            largest_len = len_int

    return largest_url, largest_len


class NotAnImageError(Exception):
    pass


class InvalidDomainError(Exception):
    pass


class RecipeDataService(BaseService):
    minifier: img.ABCMinifier

    def __init__(self, recipe_id: UUID4, logger: Logger | None = None) -> None:
        """
        RecipeDataService is a service that consolidates the reading/writing actions related
        to assets, and images for a recipe.
        """
        super().__init__()

        self.recipe_id = recipe_id
        self.logger = logger or self.logger
        self.minifier = img.PillowMinifier(purge=True, logger=self.logger)

        self.dir_data = Recipe.directory_from_id(self.recipe_id)
        self.dir_image = self.dir_data.joinpath("images")
        self.dir_image_timeline = self.dir_image.joinpath("timeline")
        self.dir_assets = self.dir_data.joinpath("assets")
        self.dir_source = self.dir_data.joinpath("source")
        self.storage = get_object_storage()

        for dir in [self.dir_image, self.dir_image_timeline, self.dir_assets, self.dir_source]:
            dir.mkdir(parents=True, exist_ok=True)

    def delete_all_data(self) -> None:
        self.storage.delete_prefix(self.dir_data)
        try:
            shutil.rmtree(self.dir_data)
        except Exception as e:
            self.logger.exception(f"Failed to delete recipe data: {e}")

    def write_image(self, file_data: bytes | Path, extension: str, image_dir: Path | None = None) -> Path:
        if not image_dir:
            image_dir = self.dir_image

        extension = extension.replace(".", "").lower()
        if f".{extension}" not in img.IMAGE_EXTENSIONS:
            raise ValueError(f"Unsupported image extension: {extension}")

        source_dir = self.dir_source if image_dir == self.dir_image else image_dir.joinpath("source")
        source_dir.mkdir(parents=True, exist_ok=True)
        source_path = source_dir.joinpath("original-upload")
        source_extension_path = source_dir.joinpath("original-extension.txt")
        source_path.unlink(missing_ok=True)

        if isinstance(file_data, Path):
            shutil.copy2(file_data, source_path)
        elif isinstance(file_data, bytes):
            source_path.write_bytes(file_data)
        else:
            with source_path.open("wb") as f:
                shutil.copyfileobj(file_data, f)

        image_path = image_dir.joinpath(f"original.{extension}")
        image_path.unlink(missing_ok=True)
        shutil.copy2(source_path, image_path)

        try:
            self.minifier.minify(image_path)
        except Exception:
            # Remove the partially-written file so corrupt images don't persist on disk.
            image_path.unlink(missing_ok=True)
            raise

        self.storage.upload(source_path)
        source_extension_path.write_text(extension, encoding="utf-8")
        self.storage.upload(source_extension_path)
        for image_type in RecipeImageTypes:
            generated_path = image_dir.joinpath(image_type.value)
            if generated_path.is_file():
                self.storage.upload(generated_path)

        return image_path

    def delete_image(self, image_dir: Path | None = None):
        if not image_dir:
            image_dir = self.dir_image

        for img_type in RecipeImageTypes:
            image_path = image_dir.joinpath(img_type.value)
            image_path.unlink(missing_ok=True)
            self.storage.delete(image_path)

        if image_dir == self.dir_image:
            source_path = self.dir_source.joinpath("original-upload")
            source_extension_path = self.dir_source.joinpath("original-extension.txt")
            source_path.unlink(missing_ok=True)
            source_extension_path.unlink(missing_ok=True)
            self.storage.delete(source_path)
            self.storage.delete(source_extension_path)

    async def scrape_image(self, image_url: str | dict[str, str] | list[str]) -> None:
        self.logger.info(f"Image URL: {image_url}")

        image_url_str = ""

        if isinstance(image_url, str):  # Handles String Types
            image_url_str = image_url

        elif isinstance(image_url, list):  # Handles List Types
            # Multiple images have been defined in the schema - usually different resolutions
            # Typically would be in smallest->biggest order, but can't be certain so test each.
            # 'Google will pick the best image to display in Search results based on the aspect ratio and resolution.'
            image_url_str, _ = await largest_content_len(image_url)

        elif isinstance(image_url, dict):  # Handles Dictionary Types
            for key in image_url:
                if key == "url":
                    image_url_str = image_url.get("url", "")

        if not image_url_str:
            raise ValueError(f"image url could not be parsed from input: {image_url}")

        ext = image_url_str.split(".")[-1]

        if ext not in img.IMAGE_EXTENSIONS:
            ext = "jpg"  # Guess the extension

        file_name = f"{self.recipe_id!s}.{ext}"
        file_path = Recipe.directory_from_id(self.recipe_id).joinpath("images", file_name)

        try:
            # FlareSolverr returns HTML, not image bytes, so it can't serve an image download.
            r = await safehttp.resilient_fetch(image_url_str, allow_flaresolverr=False)
        except Exception:
            self.logger.exception("Fatal Image Request Exception")
            return None

        if r is None:
            # Every impersonation was rejected, or the server returned an error status.
            return None

        content_type = r.headers.get("content-type", "")

        if "image" not in content_type:
            self.logger.error(f"Content-Type: {content_type} is not an image")
            raise NotAnImageError(f"Content-Type {content_type} is not an image")

        self.logger.debug(f"File Name Suffix {file_path.suffix}")
        self.write_image(r.content, file_path.suffix)
        file_path.unlink(missing_ok=True)
