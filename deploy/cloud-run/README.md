# Cloud Run + Supabase deployment

This directory deploys the pinned Mealie fork to Google Cloud Run and connects it to Supabase PostgreSQL. It is a **smoke-test baseline**, not yet a durable production deployment: upstream Mealie stores media and other runtime files on a local filesystem, while Cloud Run's writable filesystem is ephemeral.

## Architecture

- Cloud Run: Mealie web/API container
- Artifact Registry: versioned container images
- Google Secret Manager: Supabase database connection and, later, storage/OIDC credentials
- Supabase PostgreSQL: Mealie application tables in a non-public `mealie` schema
- Private Supabase Storage bucket: originals, generated recipe images, timeline photos, recipe assets, avatars, and durable exports after the storage adapter is complete

## Required setup

1. Use Google Cloud project `family-recipes-508022` with billing enabled and authenticate the Google Cloud CLI.
2. Create a dedicated Supabase development project in a region close to the Cloud Run region. The current project is in the western US, so this workflow defaults Cloud Run to `us-west1`.
3. In Supabase, create a restricted login role and a `mealie` schema. Grant that role only `USAGE` and `CREATE` on the schema and set its default `search_path` to `mealie`; the schema may remain owned by the Supabase dashboard administrator because the SQL Editor cannot assume arbitrary custom roles. Do not add `mealie` to the Supabase Data API's exposed schemas.
4. Copy the **session pooler** connection string from Supabase's Connect panel. It uses port 5432 and works over IPv4. Change the scheme to `postgresql://` if the panel returns `postgres://`, because Mealie validates that exact scheme.
5. Store the complete URL in Google Secret Manager under `supabase-postgres-url`. Never commit it.
6. Enable Supabase Storage's S3 protocol and store its server-only credentials in Secret Manager as `supabase-s3-access-key-id` and `supabase-s3-secret-access-key`.
7. Run `deploy.ps1 -ProjectId family-recipes-508022` from PowerShell.

The script creates the Artifact Registry repository and a dedicated runtime service account if needed, builds the exact source commit using Cloud Build, grants that service account access only to the named database secret, and deploys one always-allocated Cloud Run instance.

## Why one instance with always-allocated CPU?

Mealie runs scheduled maintenance inside the web process. A Cloud Run instance that scales to zero or receives CPU only during requests cannot execute that work reliably. One instance is also mandatory until media operations are made safe for concurrent instances. This setting has a continuous cost.

## Production gate

Do not import the family collection until all of these pass:

- Every recipe original, thumbnail, timeline image, asset, and avatar is written to the private Supabase Storage bucket before the request succeeds.
- Media reads are authorized by Mealie and never use a public bucket URL.
- Delete/copy/export operations update object storage consistently.
- Temporary processing files stay in `/tmp` and can disappear without data loss.
- A forced Cloud Run revision replacement preserves recipes and media.
- Database and media backups restore into a fresh project.
- Logged-out requests cannot retrieve media or exports.

Supabase S3 access keys are server-only and bypass Storage RLS across the project. If used by the adapter, keep them in Secret Manager and use a dedicated Supabase project. Supabase Storage does not provide S3 object versioning, so a separate media backup remains required.
