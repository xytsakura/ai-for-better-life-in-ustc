param(
  [Parameter(Mandatory = $true)]
  [string]$Manifest,

  [string]$BaseUrl,

  [string]$Token,

  [string]$Output
)

$ErrorActionPreference = 'Stop'
$contractRoot = Split-Path -Parent $PSScriptRoot

Push-Location $contractRoot
try {
  if (-not (Test-Path -LiteralPath 'node_modules')) {
    npm ci
  }

  $arguments = @('conformance/runner.mjs', '--manifest', (Resolve-Path -LiteralPath $Manifest).Path)
  if ($BaseUrl) { $arguments += @('--base-url', $BaseUrl) }
  if ($Token) { $arguments += @('--token', $Token) }
  if ($Output) { $arguments += @('--output', $Output) }

  & node @arguments
  exit $LASTEXITCODE
}
finally {
  Pop-Location
}
