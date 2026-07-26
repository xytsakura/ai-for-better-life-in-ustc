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


def make_client(tmp_path: Path, adapter: FakeLLMAdapter | None = None) -> tuple[TestClient, FakeLLMAdapter]:
    settings = Settings(runtime_dir=tmp_path, session_secret="test-secret")
    adapter = adapter or FakeLLMAdapter(settings)
    app = create_app(settings, adapter)
    return TestClient(app), adapter


def login(client: TestClient, user_id: str) -> None:
    response = client.post("/api/session", json={"user_id": user_id})
    assert response.status_code == 200


def upload_pdf(client: TestClient, tmp_path: Path, space_id: str, name: str, text: str) -> str:
    source = tmp_path / name
    make_pdf(source, text)
    with source.open("rb") as handle:
        response = client.post(
            f"/api/spaces/{space_id}/documents",
            files={"file": (name, handle, "application/pdf")},
            data={"title": name, "material_type": "课程笔记", "license_status": "private"},
        )
    assert response.status_code == 200
    return response.json()["document"]["id"]


def personal_space(client: TestClient) -> dict:
    spaces = client.get("/api/spaces").json()["items"]
    return next(item for item in spaces if item["space_type"] == "personal")


def shared_space(client: TestClient) -> dict:
    spaces = client.get("/api/spaces").json()["items"]
    return next(item for item in spaces if item["space_type"] == "shared")


def test_direct_mode_is_default_and_skips_search(monkeypatch, tmp_path: Path):
    client, adapter = make_client(tmp_path)
    login(client, "demo-a")

    def fail_search(*_: object, **__: object) -> None:
        raise AssertionError("direct mode must not call search")

    monkeypatch.setattr("course_agent.main.search", fail_search)
    response = client.post(
        "/api/query",
        json={"question": "Explain uniform convergence."},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "direct"
    assert body["scope"] == "general"
    assert body["retrieval_count"] == 0
    assert body["citations"] == []
    assert body["degraded"] is False
    assert adapter.direct_calls == 1
    assert adapter.retrieval_calls == 0


def test_retrieval_requires_non_empty_document_ids(tmp_path: Path):
    client, _ = make_client(tmp_path)
    login(client, "demo-a")
    personal = personal_space(client)

    missing = client.post(
        "/api/query",
        json={"mode": "retrieval", "scope": "knowledge_base", "space_id": personal["id"], "question": "uniform continuity"},
    )
    assert missing.status_code == 422

    blank = client.post(
        "/api/query",
        json={
            "mode": "retrieval",
            "scope": "knowledge_base",
            "space_id": personal["id"],
            "question": "uniform continuity",
            "document_ids": [""],
        },
    )
    assert blank.status_code == 422

    legacy_scope = client.post(
        "/api/query",
        json={"question": "uniform continuity", "space_ids": ["math-b1-shared"]},
    )
    assert legacy_scope.status_code == 422

    wrong_scope = client.post(
        "/api/query",
        json={
            "mode": "direct",
            "scope": "knowledge_base",
            "question": "should fail",
        },
    )
    assert wrong_scope.status_code == 422

    missing_space = client.post(
        "/api/query",
        json={
            "mode": "retrieval",
            "scope": "knowledge_base",
            "question": "should fail",
            "document_ids": ["x"],
        },
    )
    assert missing_space.status_code == 422


def test_auth_upload_retrieval_and_cross_user_isolation(tmp_path: Path):
    client, _ = make_client(tmp_path)
    assert client.get("/api/spaces").status_code == 401
    login(client, "demo-a")
    personal = personal_space(client)

    source = tmp_path / "source.pdf"
    make_pdf(
        source,
        "The definition of uniform continuity requires every epsilon and an appropriate delta. Calculus B1 review notes.",
    )
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
        json={
            "mode": "retrieval",
            "scope": "knowledge_base",
            "space_id": personal["id"],
            "question": "What is the definition of uniform continuity?",
            "document_ids": [document_id],
            "top_k": 5,
        },
    )
    assert query.status_code == 200
    assert query.json()["mode"] == "retrieval"
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

    login(client, "demo-b")
    hidden = client.post(
        "/api/query",
        json={
            "mode": "retrieval",
            "scope": "knowledge_base",
            "space_id": personal["id"],
            "question": "What is the definition of uniform continuity?",
            "document_ids": [document_id],
            "top_k": 5,
        },
    )
    assert hidden.status_code == 404

    login(client, "demo-a")
    deleted = client.delete(f"/api/documents/{document_id}")
    assert deleted.status_code == 204
    after_delete = client.post(
        "/api/query",
        json={
            "mode": "retrieval",
            "scope": "knowledge_base",
            "space_id": personal["id"],
            "question": "一致连续的定义是什么？",
            "document_ids": [document_id],
            "top_k": 5,
        },
    )
    assert after_delete.status_code == 404


def test_retrieval_only_uses_selected_documents(tmp_path: Path):
    client, _ = make_client(tmp_path)
    login(client, "demo-a")
    personal = personal_space(client)
    alpha_id = upload_pdf(
        client,
        tmp_path,
        personal["id"],
        "alpha.pdf",
        "Alphaonly marker explains compactness and continuous functions.",
    )
    beta_id = upload_pdf(
        client,
        tmp_path,
        personal["id"],
        "beta.pdf",
        "Betaonly marker explains uniform convergence tests for function sequences.",
    )

    wrong_document = client.post(
        "/api/query",
        json={
            "mode": "retrieval",
            "scope": "knowledge_base",
            "space_id": personal["id"],
            "question": "Betaonly",
            "document_ids": [alpha_id],
            "top_k": 5,
        },
    )
    assert wrong_document.status_code == 200
    assert wrong_document.json()["mode"] == "retrieval"
    assert wrong_document.json()["retrieval_count"] == 0
    assert wrong_document.json()["citations"] == []

    right_document = client.post(
        "/api/query",
        json={
            "mode": "retrieval",
            "scope": "knowledge_base",
            "space_id": personal["id"],
            "question": "Betaonly",
            "document_ids": [beta_id],
            "top_k": 5,
        },
    )
    assert right_document.status_code == 200
    assert right_document.json()["retrieval_count"] >= 1
    assert {item["document_id"] for item in right_document.json()["citations"]} == {beta_id}


def test_shared_space_retrieval_is_available_to_both_users(tmp_path: Path):
    client, _ = make_client(tmp_path)
    login(client, "demo-a")
    shared = shared_space(client)
    document_id = upload_pdf(
        client,
        tmp_path,
        shared["id"],
        "shared.pdf",
        "Shared study group notes: basic tests for uniform convergence of function sequences.",
    )

    login(client, "demo-b")
    result = client.post(
        "/api/query",
        json={
            "mode": "retrieval",
            "scope": "knowledge_base",
            "space_id": shared["id"],
            "question": "What is a basic test for uniform convergence?",
            "document_ids": [document_id],
            "top_k": 5,
        },
    )
    assert result.status_code == 200
    assert result.json()["mode"] == "retrieval"
    assert result.json()["retrieval_count"] >= 1
