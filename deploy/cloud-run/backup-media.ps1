[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[a-z][a-z0-9-]{4,28}[a-z0-9]$")]
    [string] $ProjectId,

    [Parameter(Mandatory = $true)]
    [string] $OutputDirectory
)

$ErrorActionPreference = "Stop"
$RepositoryRoot = Resolve-Path (Join-Path $PSScriptRoot "../..")
$AccessKeySecret = "supabase-s3-access-key-id"
$SecretKeySecret = "supabase-s3-secret-access-key"

$GCloudCommand = Get-Command gcloud -ErrorAction Stop
$UvCommand = Get-Command uv -ErrorAction Stop
$PreviousAccessKey = $env:SUPABASE_STORAGE_S3_ACCESS_KEY_ID
$PreviousSecretKey = $env:SUPABASE_STORAGE_S3_SECRET_ACCESS_KEY

try {
    $env:SUPABASE_STORAGE_S3_ACCESS_KEY_ID = (& $GCloudCommand.Source secrets versions access latest --secret $AccessKeySecret --project $ProjectId).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to read $AccessKeySecret from Secret Manager."
    }

    $env:SUPABASE_STORAGE_S3_SECRET_ACCESS_KEY = (& $GCloudCommand.Source secrets versions access latest --secret $SecretKeySecret --project $ProjectId).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to read $SecretKeySecret from Secret Manager."
    }

    Push-Location $RepositoryRoot
    try {
        & $UvCommand.Source run --no-project --with boto3==1.43.89 python deploy/cloud-run/export_media.py --output-directory $OutputDirectory
        if ($LASTEXITCODE -ne 0) {
            throw "Supabase media export failed."
        }
    }
    finally {
        Pop-Location
    }
}
finally {
    $env:SUPABASE_STORAGE_S3_ACCESS_KEY_ID = $PreviousAccessKey
    $env:SUPABASE_STORAGE_S3_SECRET_ACCESS_KEY = $PreviousSecretKey
}
