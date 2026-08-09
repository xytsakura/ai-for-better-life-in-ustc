from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from course_agent.config import Settings
from course_agent.llm import FakeLLMAdapter
from course_agent.main import create_app


def make_client(tmp_path: Path) -> TestClient:
    settings = Settings(runtime_dir=tmp_path, session_secret="test-secret")
    app = create_app(settings, FakeLLMAdapter(settings))
    return TestClient(app)


def test_marketplace_frontend_assets_are_packaged(tmp_path: Path):
    client = make_client(tmp_path)

    html = client.get("/").text
    assert 'href="#/marketplace"' in html
    assert 'data-view="marketplace"' in html
    assert 'id="view-marketplace"' in html
    assert 'id="marketplace-library-list"' in html
    assert 'id="marketplace-library-detail"' in html
    assert 'id="marketplace-tab-mine"' in html
    assert 'id="marketplace-review-tab"' in html
    assert 'id="marketplace-tab-review"' in html
    assert 'id="library-publish-btn"' in html
    assert 'id="publication-modal"' in html
    assert 'id="publication-form"' in html
    assert 'id="publication-document-list"' in html

    styles = client.get("/assets/styles.css").text
    assert ".view-marketplace" in styles
    assert ".marketplace-layout" in styles
    assert ".marketplace-library-item" in styles
    assert ".marketplace-detail-panel" in styles
    assert ".publication-policy-grid" in styles
    assert "@media (max-width: 900px)" in styles
    assert ".marketplace-layout { grid-template-columns: 1fr; }" in styles


def test_marketplace_frontend_uses_spec_api_contracts(tmp_path: Path):
    client = make_client(tmp_path)
    script = client.get("/assets/app.js").text.replace("\r\n", "\n")

    assert "const valid = ['home', 'library', 'marketplace', 'schedule', 'settings'];" in script
    assert "marketplace: {" in script
    assert "function resetMarketplaceState()" in script
    assert "function loadMarketplace()" in script
    assert "function loadMarketplaceLibraryDetail(" in script
    assert "function submitPublication(" in script
    assert "function submitAdminReview(" in script
    assert "function enterMarketplaceLibrary(" in script

    for endpoint in (
        "/api/marketplace/libraries?",
        "/api/marketplace/libraries/${encodeURIComponent(libraryId)}",
        "/api/marketplace/libraries/${encodeURIComponent(library.id)}/subscribe",
        "/api/marketplace/libraries/${encodeURIComponent(library.id)}/subscription",
        "/api/publications/mine?page_size=",
        "/api/publications/${encodeURIComponent(state.marketplace.publishLibraryId)}/versions",
        "/api/publication-versions/${encodeURIComponent(versionId)}/withdraw",
        "/api/publications/${encodeURIComponent(libraryId)}/withdraw",
        "/api/admin/publication-versions?status=pending",
        "/api/admin/publication-versions/${encodeURIComponent(versionId)}",
        "/api/admin/publications/${encodeURIComponent(libraryId)}/${action}",
        "/api/admin/publications/${encodeURIComponent(libraryId)}/rollback",
    ):
        assert endpoint in script

    assert "document_reviews: documentReviews" in script
    assert "document_id: documentId" in script
    assert "use_in_rag: document.use_in_rag !== false" in script
    assert "can_preview: document.can_preview !== false" in script
    assert "can_download: Boolean(document.can_download)" in script
    assert "state.spaces.find(space => String(space.library_id) === String(library.id))" in script
    assert "...(detail.library || {})" in script
    assert "review_note: draft.review_note || ''," in script
    assert "review_note: state.marketplace.reviewNote || ''," in script
    assert "reviewNote.trim() || null" not in script
    assert "document.use_in_rag !== false" in script
    assert "data-marketplace-rollback-version" in script
    assert "data-marketplace-preview-document" in script


def test_document_selection_stays_in_sync_across_all_surfaces(tmp_path: Path):
    client = make_client(tmp_path)
    script = client.get("/assets/app.js").text.replace("\r\n", "\n")

    sync_section = script.split("function syncSourceSelectors()", 1)[1].split("\n}", 1)[0]
    assert "renderSourceList('source-list'" in sync_section
    assert "renderSourceList('home-source-list'" in sync_section
    assert "renderDocuments();" in sync_section

    library_callback = script.split("function renderSourceSelector()", 1)[1].split(
        "function renderHomeSourceSelector()", 1
    )[0]
    assert "renderDocuments();" in library_callback

    home_callback = script.split("function renderHomeSourceSelector()", 1)[1].split(
        "function selectDocumentsByAction(", 1
    )[0]
    assert "renderDocuments();" in home_callback

    bulk_section = script.split("function selectDocumentsByAction(", 1)[1].split(
        "function updateQueryStatus()", 1
    )[0]
    assert "renderSourceSelector();" in bulk_section
    assert "renderHomeSourceSelector();" in bulk_section
    assert "renderDocuments();" in bulk_section


def test_marketplace_frontend_clears_identity_scoped_state(tmp_path: Path):
    client = make_client(tmp_path)
    script = client.get("/assets/app.js").text.replace("\r\n", "\n")

    def section(start_marker: str, end_marker: str) -> str:
        start = script.index(start_marker)
        end = script.index(end_marker, start)
        return script[start:end]

    login_script = section("async function login(", "async function logout(")
    assert login_script.index("state.authGeneration += 1;") < login_script.index("resetMarketplaceState();")
    assert login_script.index("resetMarketplaceState();") < login_script.index("renderMarketplace();")
    assert "if (state.currentView === 'marketplace') await loadMarketplace();" in login_script

    logout_script = section("async function logout(", "// ---------- Spaces ----------")
    assert logout_script.index("state.authGeneration += 1;") < logout_script.index("resetMarketplaceState();")
    assert "closePublicationModal({ restoreFocus: false });" in logout_script
    assert "renderMarketplace();" in logout_script

    assert "const authContext = captureAuthContext();" in section("async function loadMarketplace()", "async function loadMarketplaceLibraryDetail(")
    detail_loader = section("async function loadMarketplaceLibraryDetail(", "async function reloadMarketplaceAfterMutation(")
    assert "if (!authContextMatches(authContext) || String(state.marketplace.selectedLibraryId) !== String(libraryId)) return;" in detail_loader
    assert "Legacy modal focus list: ['#avatar-crop-modal', '#plan-modal', '#exam-import-modal', '#login-modal']" in script
    assert "'#publication-modal'" in section("function activeModal()", "function trapModalFocus(")
