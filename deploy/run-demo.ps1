param(
    [switch]$Detached,
    [int]$ReadyTimeoutSeconds = 600
)

$ErrorActionPreference = "Stop"
$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$envFile = Join-Path $repoRoot ".env"
$exampleFile = Join-Path $repoRoot ".env.example"
$composeFile = Join-Path $PSScriptRoot "compose.yaml"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker is required. Install Docker Desktop and ensure 'docker' is available in PATH."
}
& docker compose version *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose v2 is required. Verify it with: docker compose version"
}

if (-not (Test-Path $envFile)) {
    Copy-Item $exampleFile $envFile
    Write-Host "Created .env from .env.example"
}

& docker compose --env-file $envFile -f $composeFile config --quiet
if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose configuration validation failed. Check .env and deploy/compose.yaml."
}

$args = @("compose", "--env-file", $envFile, "-f", $composeFile, "up", "--build")
if ($Detached) {
    $args += "-d"
}

& docker @args
if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose failed to start the demo."
}

if ($Detached) {
    function Get-PublishedPort([string]$Service, [int]$ContainerPort) {
        $binding = (& docker compose --env-file $envFile -f $composeFile port $Service $ContainerPort | Select-Object -First 1).Trim()
        if ($LASTEXITCODE -ne 0 -or $binding -notmatch ':(\d+)$') {
            throw "Unable to resolve the published port for $Service/$ContainerPort."
        }
        return [int]$Matches[1]
    }

    $hubPort = Get-PublishedPort "hub" 8100
    $coursePort = Get-PublishedPort "course-agent" 8000
    $demoPort = Get-PublishedPort "demo-agent" 8101
    $deadline = (Get-Date).AddSeconds($ReadyTimeoutSeconds)
    $services = @(
        @{ Name = "Hub"; Url = "http://127.0.0.1:$hubPort/healthz" },
        @{ Name = "Hanhai Course Agent"; Url = "http://127.0.0.1:$coursePort/api/health" },
        @{ Name = "Campus Demo Agents"; Url = "http://127.0.0.1:$demoPort/api/health" }
    )
    foreach ($service in $services) {
        $ready = $false
        while ((Get-Date) -lt $deadline) {
            try {
                Invoke-RestMethod -Uri $service.Url -TimeoutSec 5 | Out-Null
                $ready = $true
                break
            } catch {
                Start-Sleep -Seconds 2
            }
        }
        if (-not $ready) {
            & docker compose --env-file $envFile -f $composeFile ps
            throw "$($service.Name) did not become healthy within $ReadyTimeoutSeconds seconds."
        }
    }

    $requiredAgentIds = @(
        "hanhai-course-agent",
        "campus-helper-demo",
        "course-review-demo",
        "campus-public-service-demo"
    )
    $agentsReady = $false
    while ((Get-Date) -lt $deadline) {
        try {
            $payload = Invoke-RestMethod -Uri "http://127.0.0.1:$hubPort/api/agents" -Headers @{ "X-Hub-User" = "demo-a" } -TimeoutSec 5
            $activeAgentIds = @($payload.agents | Where-Object { $_.status -eq "active" } | ForEach-Object { $_.agent_id })
            $missingAgentIds = @($requiredAgentIds | Where-Object { $_ -notin $activeAgentIds })
            if ($missingAgentIds.Count -eq 0) {
                $agentsReady = $true
                break
            }
        } catch {
            # Bootstrap and conformance may still be running.
        }
        Start-Sleep -Seconds 2
    }
    if (-not $agentsReady) {
        & docker compose --env-file $envFile -f $composeFile ps
        throw "The four required demo Agents were not registered and activated within $ReadyTimeoutSeconds seconds."
    }

    Write-Host "Demo is ready: http://127.0.0.1:$hubPort/ (4 active Agents)"
}
