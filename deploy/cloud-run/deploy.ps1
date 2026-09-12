[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[a-z][a-z0-9-]{4,28}[a-z0-9]$")]
    [string] $ProjectId,

    [ValidatePattern("^[a-z][a-z0-9-]+[a-z0-9]$")]
    [string] $Region = "us-west1",

    [ValidatePattern("^[a-z][a-z0-9-]{0,47}[a-z0-9]$")]
    [string] $ServiceName = "family-recipes",

    [ValidatePattern("^[a-z][a-z0-9-]{0,61}[a-z0-9]$")]
    [string] $RepositoryName = "family-recipes",

    [string] $ImageTag = "v3.25.1-family.0",

    [string] $BaseUrl = ""
)

$ErrorActionPreference = "Stop"
$DatabaseSecret = "supabase-postgres-url"
$StorageAccessKeySecret = "supabase-s3-access-key-id"
$StorageSecretKeySecret = "supabase-s3-secret-access-key"
$AuthSecret = "mealie-auth-secret"
$SessionSecret = "mealie-session-secret"
$RequiredSecrets = @($DatabaseSecret, $StorageAccessKeySecret, $StorageSecretKeySecret, $AuthSecret, $SessionSecret)
$RuntimeAccountName = "family-recipes-runtime"
$RuntimeAccount = "$RuntimeAccountName@$ProjectId.iam.gserviceaccount.com"
$ImageUri = "$Region-docker.pkg.dev/$ProjectId/$RepositoryName/mealie`:$ImageTag"
$RepositoryRoot = Resolve-Path (Join-Path $PSScriptRoot "../..")

$GCloudCommand = Get-Command gcloud -ErrorAction SilentlyContinue
if ($GCloudCommand) {
    $GCloudPath = $GCloudCommand.Source
}
else {
    $BundledGCloud = Join-Path $env:LOCALAPPDATA "Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
    if (Test-Path -LiteralPath $BundledGCloud) {
        $GCloudPath = $BundledGCloud
    }
    else {
        throw "Google Cloud CLI is not installed or is not available on PATH."
    }
}

function Invoke-GCloud {
    & $GCloudPath @args
    if ($LASTEXITCODE -ne 0) {
        throw "gcloud command failed: gcloud $($args -join ' ')"
    }
}

Invoke-GCloud config set project $ProjectId
Invoke-GCloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com secretmanager.googleapis.com

& $GCloudPath artifacts repositories describe $RepositoryName --location $Region --project $ProjectId *> $null
if ($LASTEXITCODE -ne 0) {
    Invoke-GCloud artifacts repositories create $RepositoryName --location $Region --repository-format docker --description "Family Recipes container images" --project $ProjectId
}

& $GCloudPath iam service-accounts describe $RuntimeAccount --project $ProjectId *> $null
if ($LASTEXITCODE -ne 0) {
    Invoke-GCloud iam service-accounts create $RuntimeAccountName --display-name "Family Recipes Cloud Run" --project $ProjectId
}

foreach ($SecretName in $RequiredSecrets) {
    & $GCloudPath secrets describe $SecretName --project $ProjectId *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Missing Secret Manager secret '$SecretName'. Add it before deploying."
    }

    Invoke-GCloud secrets add-iam-policy-binding $SecretName --member "serviceAccount:$RuntimeAccount" --role roles/secretmanager.secretAccessor --project $ProjectId
}

$Commit = (& git -C $RepositoryRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) {
    throw "Unable to resolve the source commit."
}

Push-Location $RepositoryRoot
try {
    Invoke-GCloud builds submit --config deploy/cloud-run/cloudbuild.yaml --substitutions "_REGION=$Region,_REPOSITORY=$RepositoryName,_IMAGE_TAG=$ImageTag,_COMMIT=$Commit" --project $ProjectId .
}
finally {
    Pop-Location
}

$Environment = @(
    "API_PORT=8080",
    "DB_ENGINE=postgres",
    "ALLOW_SIGNUP=false",
    "OIDC_AUTH_ENABLED=false",
    "OIDC_SIGNUP_ENABLED=false",
    "OIDC_REQUIRES_EMAIL_VERIFICATION=true",
    "TZ=America/New_York",
    "UVICORN_WORKERS=1",
    "DATA_DIR=/tmp/mealie-data",
    "SUPABASE_STORAGE_S3_ENDPOINT=https://jxtozsdctdwzjfdfcthr.storage.supabase.co/storage/v1/s3",
    "SUPABASE_STORAGE_S3_REGION=us-west-2",
    "SUPABASE_STORAGE_BUCKET=mealie-media"
)

if ($BaseUrl) {
    $Environment += "BASE_URL=$BaseUrl"
}

Invoke-GCloud run deploy $ServiceName `
    --image $ImageUri `
    --region $Region `
    --service-account $RuntimeAccount `
    --allow-unauthenticated `
    --port 8080 `
    --cpu 1 `
    --memory 1Gi `
    --min 1 `
    --max 1 `
    --concurrency 20 `
    --timeout 300 `
    --no-cpu-throttling `
    --set-env-vars ($Environment -join ",") `
    --set-secrets "POSTGRES_URL_OVERRIDE=$DatabaseSecret`:latest,SUPABASE_STORAGE_S3_ACCESS_KEY_ID=$StorageAccessKeySecret`:latest,SUPABASE_STORAGE_S3_SECRET_ACCESS_KEY=$StorageSecretKeySecret`:latest,SECRET=$AuthSecret`:latest,SESSION_SECRET=$SessionSecret`:latest" `
    --project $ProjectId

$ServiceUrl = (& $GCloudPath run services describe $ServiceName --region $Region --project $ProjectId --format "value(status.url)").Trim()
if ($LASTEXITCODE -ne 0 -or -not $ServiceUrl) {
    throw "Deployment completed, but the service URL could not be read."
}

if (-not $BaseUrl) {
    Invoke-GCloud run services update $ServiceName --region $Region --project $ProjectId --update-env-vars "BASE_URL=$ServiceUrl"
}

Write-Warning "Complete the media upload and forced-revision recovery checks before importing irreplaceable family photos."
Write-Host "Cloud Run service: $ServiceUrl"
