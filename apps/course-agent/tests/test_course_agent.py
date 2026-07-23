from __future__ import annotations

from pathlib import Path

import fitz
from fastapi.testclient import TestClient

from course_agent.config import Settings
from course_agent.llm import FakeLLMAdapter
from course_agent.main import create_app


def make_pdf(path: Path, text: str) -> None:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    document.save(path)
    document.close()


def setup_client(tmp_path: Path) -> TestClient:
    settings = Settings(runtime_dir=tmp_path, session_secret="test-secret")
    app = create_app(settings, FakeLLMAdapter(settings))
    return TestClient(app)


def login(client: TestClient, user_id: str) -> None:
    response = client.post("/api/session", json={"user_id": user_id})
    assert response.status_code == 200


def test_auth_upload_search_and_cross_user_isolation(tmp_path: Path):
    client = setup_client(tmp_path)
    assert client.get("/api/spaces").status_code == 401
    login(client, "demo-a")
    spaces = client.get("/api/spaces").json()["items"]
    personal = next(item for item in spaces if item["space_type"] == "personal")

    source = tmp_path / "source.pdf"
    make_pdf(source, "The definition of uniform continuity requires every epsilon and an appropriate delta. Calculus B1 review notes.")
    with source.open("rb") as handle:
        response = client.post(
            f"/api/spaces/{personal['id']}/documents",
            files={"file": ("source.pdf", handle, "application/pdf")},
            data={"title": "个人一致连续笔记", "material_type": "课程笔记", "license_status": "private"},
        )
    assert response.status_code == 200
    document_id = response.json()["document"]["id"]

    query = client.post(
        "/api/query",
        json={"question": "What is the definition of uniform continuity?", "space_ids": [personal["id"]], "top_k": 5},
    )
    assert query.status_code == 200
    assert query.json()["citations"]
    assert query.json()["degraded"] is False

    duplicate = client.post(
        f"/api/spaces/{personal['id']}/documents",
        files={"file": ("source.pdf", source.read_bytes(), "application/pdf")},
        data={"title": "重复资料", "material_type": "课程笔记", "license_status": "private"},
    )
    assert duplicate.status_code == 409

    reparsed = client.post(f"/api/documents/{document_id}/reparse")
    assert reparsed.status_code == 200
    assert reparsed.json()["document"]["id"] == document_id

    reparsed_again = client.post(f"/api/documents/{document_id}/reparse")
    assert reparsed_again.status_code == 200
    assert reparsed_again.json()["document"]["id"] == document_id

    login(client, "demo-b")
    hidden = client.post(
        "/api/query",
        json={"question": "What is the definition of uniform continuity?", "space_ids": [personal["id"]], "top_k": 5},
    )
    assert hidden.status_code == 404
    personal_b = next(item for item in client.get("/api/spaces").json()["items"] if item["space_type"] == "personal")
    no_hit = client.post(
        "/api/query",
        json={"question": "What is the definition of uniform continuity?", "space_ids": [personal_b["id"]], "top_k": 5},
    )
    assert no_hit.status_code == 200
    assert no_hit.json()["retrieval_count"] == 0

    login(client, "demo-a")
    deleted = client.delete(f"/api/documents/{document_id}")
    assert deleted.status_code == 204
    after_delete = client.post(
        "/api/query",
        json={"question": "一致连续的定义是什么？", "space_ids": [personal["id"]], "top_k": 5},
    )
    assert after_delete.status_code == 200
    assert after_delete.json()["retrieval_count"] == 0


def test_shared_space_is_available_to_both_users(tmp_path: Path):
    client = setup_client(tmp_path)
    login(client, "demo-a")
    spaces_a = client.get("/api/spaces").json()["items"]
    shared = next(item for item in spaces_a if item["space_type"] == "shared")
    source = tmp_path / "shared.pdf"
    make_pdf(source, "Shared study group notes: basic tests for uniform convergence of function sequences.")
    with source.open("rb") as handle:
        response = client.post(
            f"/api/spaces/{shared['id']}/documents",
            files={"file": ("shared.pdf", handle, "application/pdf")},
            data={"title": "共享复习资料", "material_type": "复习提纲", "license_status": "private"},
        )
    assert response.status_code == 200
    login(client, "demo-b")
    shared_b = next(item for item in client.get("/api/spaces").json()["items"] if item["id"] == shared["id"])
    result = client.post(
        "/api/query",
        json={"question": "What is a basic test for uniform convergence?", "space_ids": [shared_b["id"]], "top_k": 5},
    )
    assert result.status_code == 200
    assert result.json()["retrieval_count"] >= 1
