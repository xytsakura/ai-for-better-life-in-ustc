param(
    [switch]$Detached
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$envFile = Join-Path $repoRoot ".env"
$exampleFile = Join-Path $repoRoot ".env.example"
$composeFile = Join-Path $PSScriptRoot "compose.yaml"

if (-not (Test-Path $envFile)) {
    Copy-Item $exampleFile $envFile
    Write-Host "Created .env from .env.example"
}

$args = @("compose", "--env-file", $envFile, "-f", $composeFile, "up", "--build")
if ($Detached) {
    $args += "-d"
}

& docker @args
