---
name: mealie-photo-import
description: Add a recipe to Mealie from one or more attached photos, with optional notes or corrections.
metadata:
  openclaw:
    emoji: "📸"
    requires:
      bins:
        - python3
      env:
        - MEALIE_API_TOKEN
        - MEALIE_URL
    primaryEnv: MEALIE_API_TOKEN
    envVars:
      - name: MEALIE_API_TOKEN
        required: true
        description: Long-lived API token created in the Mealie user profile.
      - name: MEALIE_URL
        required: true
        description: Base URL of the Mealie site, without /api.
      - name: MEALIE_GROUP_SLUG
        required: false
        description: Group slug used to build the resulting recipe link; defaults to home.
---

# Mealie photo import

Use this skill when the user wants to add a recipe from attached photos. Mealie performs the image reading and recipe creation; do not transcribe or invent missing recipe details yourself.

## Workflow

1. Gather every local image path from the current message. Preserve page order. If the order is unclear and it could change the recipe, ask the user which photo comes first.
2. Treat any user notes as corrections or extra source material. Pass them with `--notes`; do not silently rewrite them.
3. Validate the request without changing Mealie:

   ```bash
   python3 "{baseDir}/scripts/import_recipe.py" --check --image "/path/page-1.jpg" --image "/path/page-2.jpg" --notes "Use half the stated salt"
   ```

4. Creation is an external write and may use the configured AI provider. If the current user message explicitly says to add, save, create, or import the attached recipe, that message authorizes the write. Otherwise, summarize the number of photos and notes, then ask whether to add it.
5. After authorization, run the same command with `--confirm` instead of `--check`:

   ```bash
   python3 "{baseDir}/scripts/import_recipe.py" --confirm --image "/path/page-1.jpg" --image "/path/page-2.jpg" --notes "Use half the stated salt"
   ```

6. Return the recipe link printed by the script and remind the user to review the ingredients and directions. Never claim the import succeeded unless the script returns a recipe slug.

## Constraints

- Never print, echo, or place `MEALIE_API_TOKEN` in a command argument. The helper reads it from the environment.
- Send only images the user supplied for this recipe.
- Do not retry a failed creation automatically; a timeout can occur after Mealie has already created the recipe. Report the failure and check Mealie before another write.
- If Mealie reports that no image provider is configured, tell the user to add an image-capable AI provider in Mealie's group settings.
- If no local attachment path is available, ask the user to attach the photo again.
