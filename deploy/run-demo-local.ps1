param(
    [switch]$SkipBootstrap,
    [switch]$SkipVerification,
    [string]$RuntimeRoot = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$hubPython = Join-Path $repoRoot "apps\hub\.venv\Scripts\python.exe"
$coursePython = Join-Path $repoRoot "apps\course-agent\.venv\Scripts\python.exe"
$demoPython = Join-Path $repoRoot "apps\demo-agent\.venv\Scripts\python.exe"
$envFile = Join-Path $repoRoot ".env"
$envExample = Join-Path $repoRoot ".env.example"

foreach ($python in @($hubPython, $coursePython, $demoPython)) {
    if (-not (Test-Path $python)) {
        throw "Required virtual environment is missing: $python"
    }
}

if (-not (Test-Path $envFile)) {
    Copy-Item $envExample $envFile
    Write-Host "Created .env from .env.example. Add model credentials there when needed."
}

# Use python-dotenv from the course Agent environment so quoted values and
# special characters are handled without printing secrets to the terminal.
$dotenvJson = & $coursePython -c @'
import json
import sys
from dotenv import dotenv_values

print(json.dumps({key: value for key, value in dotenv_values(sys.argv[1]).items() if value is not None}))
'@ $envFile
if ($LASTEXITCODE -ne 0) { throw "Failed to load .env." }
$dotenv = $dotenvJson | ConvertFrom-Json
foreach ($property in $dotenv.PSObject.Properties) {
    if ([string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable($property.Name, "Process"))) {
        [Environment]::SetEnvironmentVariable($property.Name, [string]$property.Value, "Process")
    }
}

if ([string]::IsNullOrWhiteSpace($RuntimeRoot)) {
    $runtimePath = Join-Path $repoRoot "var"
} elseif ([System.IO.Path]::IsPathRooted($RuntimeRoot)) {
    $runtimePath = [System.IO.Path]::GetFullPath($RuntimeRoot)
} else {
    $runtimePath = [System.IO.Path]::GetFullPath((Join-Path $repoRoot $RuntimeRoot))
}
$hubRuntime = Join-Path $runtimePath "hub"
$courseRuntime = Join-Path $runtimePath "course-agent"
$secretsRuntime = Join-Path $runtimePath "hub-secrets"
New-Item -ItemType Directory -Force $hubRuntime, $courseRuntime, $secretsRuntime | Out-Null

function Test-LocalPort([int]$Port) {
    return $null -ne (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
}

function Start-DemoService(
    [int]$Port,
    [string]$Python,
    [string]$Module,
    [string]$AppDirectory,
    [string]$Name
) {
    if (Test-LocalPort $Port) {
        Write-Host "$Name is already listening on port $Port."
        return
    }
    $arguments = @(
        "-m", "uvicorn", $Module,
        "--app-dir", $AppDirectory,
        "--host", "127.0.0.1",
        "--port", "$Port"
    )
    Start-Process -FilePath $Python -ArgumentList $arguments -WorkingDirectory $repoRoot -WindowStyle Hidden
    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $deadline) {
        if (Test-LocalPort $Port) {
            Write-Host "$Name started on port $Port."
            return
        }
        Start-Sleep -Milliseconds 500
    }
    throw "$Name did not start on port $Port."
}

$env:HUB_DEMO_MODE = "true"
$env:HUB_DATABASE_PATH = Join-Path $hubRuntime "hub.sqlite3"
$env:HUB_PUBLIC_BASE_URL = "http://127.0.0.1:8100"
$env:HUB_MODEL_PROFILES_ENABLED = "true"
$env:HUB_MODEL_PROFILE_MASTER_KEY_FILE = Join-Path $secretsRuntime "model-profile-master.key"
$env:HUB_ALLOW_LOCAL_MODEL_PROVIDERS = "true"
if ([string]::IsNullOrWhiteSpace($env:HUB_MODEL_PROVIDER_ORIGIN_ALLOWLIST)) {
    $env:HUB_MODEL_PROVIDER_ORIGIN_ALLOWLIST = "https://ie-crs.haoxiang.ai"
}
$env:HUB_INTERNAL_URL_ALLOWLIST = "http://127.0.0.1:8002,http://127.0.0.1:8101"
$env:HUB_AUTOMATIC_CHECKS_ENABLED = "true"
$env:HUB_REQUIRE_PASSING_CHECKS = "true"
$env:HUB_HEALTH_POLL_INTERVAL_SECONDS = "30"
& $hubPython (Join-Path $repoRoot "deploy\init_model_profile_key.py")
if ($LASTEXITCODE -ne 0) { throw "Hub model profile key initialization failed." }
Start-DemoService 8100 $hubPython "hub.main:app" (Join-Path $repoRoot "apps\hub") "Campus Agent Hub"

$env:HUB_URL = "http://127.0.0.1:8100"
$env:CONTRACT_ROOT = Join-Path $repoRoot "contracts\campus-agent-hub\v1"
$env:COURSE_AGENT_SECRET_PATH = Join-Path $secretsRuntime "course-agent.secret"
$env:COURSE_AGENT_PUBLIC_URL = "http://127.0.0.1:8002"
$env:COURSE_AGENT_INTERNAL_URL = "http://127.0.0.1:8002"
$env:DEMO_AGENT_PUBLIC_URL = "http://127.0.0.1:8101"
$env:DEMO_AGENT_INTERNAL_URL = "http://127.0.0.1:8101"

if (-not $SkipBootstrap) {
    $env:HUB_BOOTSTRAP_REGISTER_ONLY = "1"
    & $hubPython (Join-Path $repoRoot "deploy\bootstrap_demo.py")
    if ($LASTEXITCODE -ne 0) { throw "Register-only bootstrap failed." }
    Remove-Item Env:HUB_BOOTSTRAP_REGISTER_ONLY -ErrorAction SilentlyContinue
}

$env:COURSE_AGENT_DEMO_MODE = "true"
$env:COURSE_AGENT_RUNTIME_DIR = $courseRuntime
$env:COURSE_AGENT_ADMIN_USER_IDS = "demo-a"
$env:COURSE_AGENT_HUB_AUTH_REQUIRED = "true"
$env:COURSE_AGENT_SESSION_HTTPS_ONLY = "false"
$env:COURSE_AGENT_HUB_JWKS_URL = "http://127.0.0.1:8100/.well-known/jwks.json"
$env:COURSE_AGENT_HUB_TOKEN_ENDPOINT = "http://127.0.0.1:8100/oauth/token"
$env:COURSE_AGENT_HUB_CLIENT_ID = "hanhai-course-agent"
$env:COURSE_AGENT_HUB_CLIENT_SECRET_FILE = Join-Path $secretsRuntime "course-agent.secret"
$env:COURSE_AGENT_HUB_WORKSPACE_REDIRECT_URI = "http://127.0.0.1:8002/api/hub/callback"
$env:COURSE_AGENT_HUB_RETURN_URL = "http://127.0.0.1:8100/"
$env:COURSE_AGENT_HUB_USER_MAP = "demo-a:demo-a,demo-b:demo-b,demo-c:demo-c"
$env:COURSE_AGENT_HUB_MODEL_GATEWAY_ENABLED = "true"
$env:COURSE_AGENT_HUB_MODEL_GRANT_ENDPOINT = "http://127.0.0.1:8100/api/model-gateway/grants/exchange"
$env:COURSE_AGENT_HUB_MODEL_GATEWAY_URL = "http://127.0.0.1:8100/api/model-gateway/v1/generate"
$env:COURSE_AGENT_HUB_MODEL_DELEGATION_TTL_SECONDS = "3600"
$env:COURSE_AGENT_HUB_MODEL_GATEWAY_TIMEOUT_SECONDS = "60"
$env:PYTHONPATH = Join-Path $repoRoot "apps\course-agent"

& $coursePython -m course_agent.cli init-db
if ($LASTEXITCODE -ne 0) { throw "Course Agent database initialization failed." }
& $coursePython -m course_agent.cli import-manifest (Join-Path $repoRoot "data\manifests\math-analysis-b1.yaml")
if ($LASTEXITCODE -ne 0) { throw "Course material import failed." }
& $coursePython -m course_agent.cli seed-marketplace (Join-Path $repoRoot "data\manifests\marketplace-demo.yaml")
if ($LASTEXITCODE -ne 0) { throw "Course marketplace seed failed." }

Start-DemoService 8002 $coursePython "course_agent.main:app" (Join-Path $repoRoot "apps\course-agent") "Hanhai Course Agent"

$env:DEMO_AGENT_REQUIRE_HUB_TOKEN = "1"
$env:DEMO_AGENT_HUB_JWKS_URL = "http://127.0.0.1:8100/.well-known/jwks.json"
$env:DEMO_AGENT_HUB_AUDIENCE = "campus-helper-demo"
$env:DEMO_AGENT_HUB_AUDIENCES = "campus-helper-demo,course-review-demo,campus-public-service-demo"
$env:DEMO_AGENT_HUB_ISSUER = "campus-agent-hub"
Start-DemoService 8101 $demoPython "demo_agent.main:app" (Join-Path $repoRoot "apps\demo-agent") "Campus Demo Agents"

if (-not $SkipBootstrap) {
    & $hubPython (Join-Path $repoRoot "deploy\bootstrap_demo.py")
    if ($LASTEXITCODE -ne 0) { throw "Full bootstrap failed." }
}

if (-not $SkipVerification) {
    & $hubPython (Join-Path $repoRoot "deploy\verify_demo.py") --iterations 1 --minimum-success 1 --output (Join-Path $runtimePath "demo-acceptance.json")
    if ($LASTEXITCODE -ne 0) { throw "Demo verification failed." }
}

Write-Host "Full local Demo is ready at http://127.0.0.1:8100/"
