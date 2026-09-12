from enum import StrEnum

from fastapi import APIRouter, HTTPException, status
from pydantic import UUID4
from starlette.responses import FileResponse

from mealie.schema.recipe import Recipe
from mealie.schema.recipe.recipe_timeline_events import RecipeTimelineEventOut
from mealie.services.storage import get_object_storage

router = APIRouter(prefix="/recipes")


class ImageType(StrEnum):
    original = "original.webp"
    small = "min-original.webp"
    tiny = "tiny-original.webp"


@router.get("/{recipe_id}/images/{file_name}")
async def get_recipe_img(recipe_id: UUID4, file_name: ImageType = ImageType.original):
    """
    Takes in a recipe id, returns the static image. This route is proxied in the docker image
    and should not hit the API in production
    """
    recipe_image = Recipe.directory_from_id(recipe_id).joinpath("images", file_name.value)

    if get_object_storage().materialize(recipe_image):
        return FileResponse(recipe_image, media_type="image/webp")
    else:
        raise HTTPException(status.HTTP_404_NOT_FOUND)


@router.get("/{recipe_id}/images/timeline/{timeline_event_id}/{file_name}")
async def get_recipe_timeline_event_img(
    recipe_id: UUID4, timeline_event_id: UUID4, file_name: ImageType = ImageType.original
):
    """
    Takes in a recipe id and event timeline id, returns the static image. This route is proxied in the docker image
    and should not hit the API in production
    """
    timeline_event_image = RecipeTimelineEventOut.image_dir_from_id(recipe_id, timeline_event_id).joinpath(
        file_name.value
    )

    if get_object_storage().materialize(timeline_event_image):
        return FileResponse(timeline_event_image, media_type="image/webp")
    else:
        raise HTTPException(status.HTTP_404_NOT_FOUND)


@router.get("/{recipe_id}/assets/{file_name}")
async def get_recipe_asset(recipe_id: UUID4, file_name: str):
    """Returns a recipe asset"""
    asset_dir = Recipe.directory_from_id(recipe_id).joinpath("assets")
    file = asset_dir.joinpath(file_name).resolve()

    if not file.is_relative_to(asset_dir.resolve()):
        raise HTTPException(status.HTTP_400_BAD_REQUEST)

    if get_object_storage().materialize(file):
        # Force download and disable MIME sniffing so uploaded assets cannot be
        # served as active content in Mealie's origin.
        return FileResponse(
            file,
            filename=file.name,
            content_disposition_type="attachment",
            headers={"X-Content-Type-Options": "nosniff"},
        )
    else:
        raise HTTPException(status.HTTP_404_NOT_FOUND)


@router.get("/{recipe_id}/source")
async def get_recipe_source(recipe_id: UUID4):
    """Download the exact source bytes used for the recipe's current cover image."""
    source_dir = Recipe.directory_from_id(recipe_id).joinpath("source")
    source = source_dir.joinpath("original-upload")
    extension_file = source_dir.joinpath("original-extension.txt")
    storage = get_object_storage()

    if not storage.materialize(source):
        raise HTTPException(status.HTTP_404_NOT_FOUND)

    extension = "bin"
    if storage.materialize(extension_file):
        stored_extension = extension_file.read_text(encoding="utf-8").strip().lower()
        if stored_extension.isalnum() and len(stored_extension) <= 10:
            extension = stored_extension

    return FileResponse(
        source,
        filename=f"recipe-source.{extension}",
        content_disposition_type="attachment",
        headers={"X-Content-Type-Options": "nosniff"},
    )
