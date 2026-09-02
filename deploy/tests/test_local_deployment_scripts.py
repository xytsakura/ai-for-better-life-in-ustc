from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_windows_local_demo_bootstraps_runtime_data_and_model_key() -> None:
    script = (ROOT / "deploy" / "run-demo-local.ps1").read_text(encoding="utf-8")

    expected_in_order = [
        "init_model_profile_key.py",
        "course_agent.cli init-db",
        "course_agent.cli import-manifest",
        "course_agent.cli seed-marketplace",
        "Start-DemoService 8002",
    ]
    positions = [script.index(item) for item in expected_in_order]

    assert positions == sorted(positions)
    assert 'dotenv_values(sys.argv[1])' in script
    assert 'IsNullOrWhiteSpace([string]$property.Value)' in script
    assert '[string]$RuntimeRoot' in script
    assert 'HUB_MODEL_PROFILES_ENABLED = "true"' in script
    assert r'$env:PYTHONPATH = Join-Path $repoRoot "apps\course-agent"' in script


def test_all_supported_demo_entrypoints_seed_the_marketplace() -> None:
    windows = (ROOT / "deploy" / "run-demo-local.ps1").read_text(encoding="utf-8")
    shell = (ROOT / "start-local.sh").read_text(encoding="utf-8")
    compose = (ROOT / "deploy" / "compose.yaml").read_text(encoding="utf-8")

    assert "seed-marketplace" in windows
    assert "seed-marketplace" in shell
    assert "seed-marketplace" in compose


def test_docker_entrypoint_validates_prerequisites_and_waits_for_four_agents() -> None:
    script = (ROOT / "deploy" / "run-demo.ps1").read_text(encoding="utf-8")

    assert "Get-Command docker" in script
    assert "docker compose version" in script
    assert "config --quiet" in script
    assert 'Get-PublishedPort "hub" 8100' in script
    assert 'Get-PublishedPort "course-agent" 8000' in script
    assert 'Get-PublishedPort "demo-agent" 8101' in script
    assert 'Url = "http://127.0.0.1:$hubPort/healthz"' in script
    assert 'Url = "http://127.0.0.1:$coursePort/api/health"' in script
    assert 'Url = "http://127.0.0.1:$demoPort/api/health"' in script
    for agent_id in (
        "hanhai-course-agent",
        "campus-helper-demo",
        "course-review-demo",
        "campus-public-service-demo",
    ):
        assert agent_id in script
    assert "$missingAgentIds.Count -eq 0" in script
