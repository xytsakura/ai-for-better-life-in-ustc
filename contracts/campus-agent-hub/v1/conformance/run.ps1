param(
  [Parameter(Mandatory = $true)]
  [string]$Manifest,

  [string]$BaseUrl,

  [string]$Token,

  [string]$Output
)

$ErrorActionPreference = 'Stop'
$contractRoot = Split-Path -Parent $PSScriptRoot
$manifestPath = (Resolve-Path -LiteralPath $Manifest).Path
$outputPath = if ($Output) {
  if ([System.IO.Path]::IsPathRooted($Output)) {
    [System.IO.Path]::GetFullPath($Output)
  } else {
    [System.IO.Path]::GetFullPath((Join-Path (Get-Location).Path $Output))
  }
} else {
  $null
}

Push-Location $contractRoot
try {
  if (-not (Test-Path -LiteralPath 'node_modules')) {
    npm ci
  }

  $arguments = @('conformance/runner.mjs', '--manifest', $manifestPath)
  if ($BaseUrl) { $arguments += @('--base-url', $BaseUrl) }
  if ($Token) { $arguments += @('--token', $Token) }
  if ($outputPath) { $arguments += @('--output', $outputPath) }

  & node @arguments
  exit $LASTEXITCODE
}
finally {
  Pop-Location
}
