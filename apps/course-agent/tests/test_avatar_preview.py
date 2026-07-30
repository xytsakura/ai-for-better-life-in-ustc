from __future__ import annotations

import struct

from fastapi.testclient import TestClient

from course_agent.config import Settings
from course_agent.llm import FakeLLMAdapter
from course_agent.main import create_app


ASSET_NAMES = (
    "agent-idle.png",
    "agent-thinking.png",
    "agent-wave-a.png",
    "agent-wave-b.png",
    "agent-reading.png",
)
FEMALE_ASSET_NAMES = tuple(f"female/{name}" for name in ASSET_NAMES)

STUDY_QUOTES = (
    "《论语》：学而不思则罔，思而不学则殆。",
    "《论语》：温故而知新，可以为师矣。",
    "《论语》：知之者不如好之者，好之者不如乐之者。",
    "《荀子·劝学》：不积跬步，无以至千里；不积小流，无以成江海。",
    "韩愈《进学解》：业精于勤，荒于嬉；行成于思，毁于随。",
    "陆游《冬夜读书示子聿》：纸上得来终觉浅，绝知此事要躬行。",
    "《礼记·中庸》：博学之，审问之，慎思之，明辨之，笃行之。",
    "朱熹《观书有感》：问渠那得清如许？为有源头活水来。",
)


def make_client(tmp_path) -> TestClient:
    settings = Settings(runtime_dir=tmp_path, session_secret="avatar-preview-test")
    return TestClient(create_app(settings, FakeLLMAdapter(settings)))


def png_header(data: bytes) -> tuple[int, int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height, _bit_depth, color_type = struct.unpack(">IIBB", data[16:26])
    return width, height, color_type


def test_avatar_preview_page_and_interactions_are_packaged(tmp_path):
    client = make_client(tmp_path)

    html_response = client.get("/assets/avatar-preview.html")
    assert html_response.status_code == 200
    assert html_response.headers["content-type"].startswith("text/html")
    html = html_response.text
    assert 'id="avatar-button"' in html
    assert 'id="speech-bubble"' in html
    assert 'aria-live="polite"' in html
    assert 'id="simulate-reply"' in html
    assert "/assets/avatar-preview/agent-idle.png" in html

    css_response = client.get("/assets/avatar-preview.css")
    assert css_response.status_code == 200
    css = css_response.text
    assert ":root[data-theme=\"light\"]" in css
    assert "border: 2px solid #ffffff" in css
    assert "aspect-ratio: 378 / 887" in css
    assert "max-width: min(100%, 440px)" in css
    assert "box-shadow: inset 0 0 0 2px var(--accent)" in css
    assert "@media (max-width: 640px)" in css
    assert "@media (prefers-reduced-motion: reduce)" in css

    script_response = client.get("/assets/avatar-preview.js")
    assert script_response.status_code == 200
    script = script_response.text
    assert "const SECOND_CLICK_WINDOW_MS = 5000" in script
    assert "const THINKING_DURATION_MS = 2400" in script
    assert "const WAVE_DURATION_MS = 1600" in script
    assert "const READING_DURATION_MS = 3600" in script
    assert "你好呀，${state.name}" in script
    assert "const STUDY_QUOTES = Object.freeze([" in script
    assert "function nextStudyQuote()" in script
    assert "state.quoteIndex = (state.quoteIndex + 1) % STUDY_QUOTES.length" in script
    assert "showSpeech(nextStudyQuote(), QUOTE_DURATION_MS)" in script
    for quote in STUDY_QUOTES:
        assert quote in script
    assert "course-agent:profile-v1:" in script
    assert "agent-wave-a.png" in script
    assert "agent-wave-b.png" in script
    for forbidden in (
        "fetch(",
        "XMLHttpRequest",
        "WebSocket",
        "localStorage.setItem",
        "document.cookie",
    ):
        assert forbidden not in script


def test_product_logo_replaces_visual_107_marks(tmp_path):
    client = make_client(tmp_path)

    main_html = client.get("/").text
    assert main_html.count('src="/assets/product-logo.png?v=product-logo-v1"') == 2
    assert '<link rel="icon" type="image/png" href="/assets/product-logo.png?v=product-logo-v1">' in main_html
    assert '<div class="brand-mark">107</div>' not in main_html
    assert '<div class="home-logo-mark">107</div>' not in main_html
    assert '<title>瀚海行agent · 心游文瀚海，志上理云天</title>' in main_html
    assert '<div class="brand" role="img" aria-label="瀚海行agent，AI for better life in ustc">' in main_html
    assert '<div class="brand-title">瀚海行agent</div>' in main_html
    assert '<div class="brand-sub">AI for better life in ustc</div>' in main_html
    assert '<div class="home-logo-badge">心游文瀚海，志上理云天</div>' in main_html
    assert 'placeholder="有问题尽管问瀚海行agent…"' in main_html
    assert 'aria-label="瀚海行agent 虚拟形象"' in main_html
    assert "课程复习 Agent" not in main_html
    assert "USTC Course Agent" not in main_html
    assert "AI for better life In ustc" not in main_html
    assert client.get("/openapi.json").json()["info"]["title"] == "瀚海行agent"

    preview_html = client.get("/assets/avatar-preview.html").text
    assert preview_html.count('src="/assets/product-logo.png?v=product-logo-v1"') == 1
    assert '/assets/avatar-preview.css?v=product-logo-v1' in preview_html
    assert '<span class="preview-brand-mark" aria-hidden="true">107</span>' not in preview_html
    assert '<title>虚拟形象预览 · 瀚海行agent</title>' in preview_html
    assert '<div class="preview-brand" role="img" aria-label="瀚海行agent，虚拟形象预览">' in preview_html
    assert '<strong>瀚海行agent</strong>' in preview_html
    assert "课程复习 Agent" not in preview_html

    logo_response = client.get("/assets/product-logo.png")
    assert logo_response.status_code == 200
    assert logo_response.headers["content-type"] == "image/png"
    assert png_header(logo_response.content) == (256, 256, 2)

    main_styles = client.get("/assets/styles.css").text
    preview_styles = client.get("/assets/avatar-preview.css").text
    assert "object-fit: cover;" in main_styles[
        main_styles.index(".brand-mark {") : main_styles.index(".brand-title")
    ]
    assert "object-fit: cover;" in main_styles[
        main_styles.index(".home-logo-mark {") : main_styles.index(".home-logo-badge")
    ]
    assert "object-fit: cover;" in preview_styles[
        preview_styles.index(".preview-brand-mark {") : preview_styles.index(".preview-brand-copy")
    ]


def test_avatar_preview_pose_assets_are_consistent_transparent_pngs(tmp_path):
    client = make_client(tmp_path)
    headers = []

    for name in (*ASSET_NAMES, *FEMALE_ASSET_NAMES):
        response = client.get(f"/assets/avatar-preview/{name}")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        headers.append(png_header(response.content))

    assert headers == [(378, 887, 6)] * (len(ASSET_NAMES) + len(FEMALE_ASSET_NAMES))


def test_virtual_avatar_is_integrated_with_real_agent_lifecycle(tmp_path):
    client = make_client(tmp_path)

    html = client.get("/").text
    assert 'class="home-workspace"' in html
    assert 'id="home-agent-avatar-dock"' in html
    assert 'class="home-agent-avatar-dock feature-preferences-pending"' in html
    assert 'id="home-agent-avatar-button"' in html
    assert 'id="home-agent-avatar-image"' in html
    assert 'id="home-agent-avatar-speech"' in html
    assert 'id="home-agent-avatar-actions"' in html
    assert 'data-avatar-action="schedule"' in html
    assert 'data-avatar-action="weather"' in html
    assert 'data-avatar-action="literature"' in html
    assert 'data-avatar-action="exams"' in html
    assert 'id="home-agent-avatar-scale"' in html
    assert 'id="home-agent-avatar-speech-dismiss"' in html
    assert html.index('id="home-agent-avatar-button"') < html.index('id="home-agent-avatar-actions"')
    assert html.index('id="home-agent-avatar-actions"') < html.index('id="home-agent-avatar-scale-control"')
    assert 'id="home-agent-avatar-announcer"' in html
    assert 'id="home-agent-avatar-state"' in html
    speech_markup = html.split('id="home-agent-avatar-speech"', 1)[1].split(">", 1)[0]
    assert 'data-visible="false"' in speech_markup
    assert 'aria-hidden="true"' in speech_markup
    assert 'aria-busy="false"' in html
    assert 'aria-live="polite"' in html
    assert '/assets/avatar-preview/agent-idle.png' in html
    assert '/assets/styles.css?v=avatar-actions-v2' in html
    assert '/assets/app.js?v=avatar-actions-v2' in html

    styles = client.get("/assets/styles.css").text
    assert ".home-agent-avatar-dock" in styles
    assert ".home-agent-avatar-dock.feature-preferences-pending" in styles
    assert ".home-agent-avatar-speech" in styles
    assert "border: 1px solid var(--agent-bubble-border)" in styles
    assert ".home-agent-avatar-button[data-state=\"thinking\"]" in styles
    assert "aspect-ratio: 378 / 887" in styles
    assert "grid-template-rows: 74px minmax(0, 1fr)" in styles
    assert "position: absolute;" in styles[
        styles.index(".home-agent-avatar-speech-zone {") : styles.index(".home-agent-avatar-speech {")
    ]
    assert "@media (prefers-reduced-motion: reduce)" in styles

    script = client.get("/assets/app.js").text
    for asset_name in ASSET_NAMES:
        assert f"/assets/avatar-preview/{asset_name}" in script
        assert f"/assets/avatar-preview/female/{asset_name}" in script
    for quote in STUDY_QUOTES:
        assert quote in script
    assert "const HOME_AGENT_AVATAR_QUOTES = Object.freeze([" in script
    assert "const HOME_AGENT_AVATAR_POSE_SETS = {" in script
    assert "function normalizeHomeAgentAvatarCharacter(value)" in script
    assert "function activeHomeAgentAvatarPoses()" in script
    assert "function syncHomeAgentAvatarSource()" in script
    assert "function startHomeAgentAvatarThinking()" in script
    assert "function stopHomeAgentAvatarThinking()" in script
    assert "function startHomeAgentAvatarWave()" in script
    assert "function startHomeAgentAvatarReading()" in script
    assert "function handleHomeAgentAvatarInteraction(event)" in script
    interaction_start = script.index("function handleHomeAgentAvatarInteraction(event)")
    interaction_end = script.index("function initHomeAgentAvatar()", interaction_start)
    interaction_script = script[interaction_start:interaction_end]
    assert "if (state.isQuerying || state.homeAgentAvatar.mode === 'thinking') return;" in interaction_script
    assert "if (isLoading && state.features.avatar !== false) startHomeAgentAvatarThinking();" in script
    assert "else stopHomeAgentAvatarThinking();" in script
    assert "const name = state.user ? effectiveDisplayName() : '同学';" in script
    assert "resetHomeAgentAvatar({ resetQuotes: true });" in script

    preferences_start = script.index("function loadUserPreferences()")
    preferences_end = script.index("function captureAuthContext()", preferences_start)
    preferences_script = script[preferences_start:preferences_end]
    assert preferences_script.index("state.features = readFeaturePreferences(state.user);") < preferences_script.index(
        "resetHomeAgentAvatar({ resetQuotes: true });"
    )

    character_start = script.index("function updateAvatarCharacter(")
    character_end = script.index("async function loadSettings()", character_start)
    character_script = script[character_start:character_end]
    assert "syncHomeAgentAvatarSource();" in character_script
    assert "resetHomeAgentAvatar" not in character_script
    assert "homeAgentAvatar.mode =" not in character_script

    speech_start = script.index("function showHomeAgentAvatarSpeech(")
    speech_end = script.index("function resetHomeAgentAvatar(", speech_start)
    speech_script = script[speech_start:speech_end]
    assert "const announcer = $('#home-agent-avatar-announcer');" in speech_script
    assert "const announcementId = ++avatar.announcementId;" in speech_script
    assert "positionHomeAgentAvatarSpeech();" in speech_script
    assert "announcer.textContent = message;" in speech_script

    query_start = script.index("async function query(")
    query_end = script.index("// ---------- Home ----------", query_start)
    query_script = script[query_start:query_end]
    assert query_script.index("if (!isHome) clearAnswer(prefix);") < query_script.index("setLoading(true);")
    assert query_script.index("setLoading(true);") < query_script.index("const requestId = ++state.queryRequestId;")

    clear_start = script.index("function clearAnswer(")
    clear_end = script.index("function scrollHomeToBottom", clear_start)
    assert "if (state.isQuerying) setLoading(false);" in script[clear_start:clear_end]
    assert "const HISTORY_KEY_PREFIX = 'course-agent:history-v2:'" in script
    assert "function historyStorageKey(user = state.user)" in script
    assert "course-agent-history-v1" not in script


def test_virtual_avatar_bubble_theme_and_vertical_responsive_layout_are_packaged(tmp_path):
    client = make_client(tmp_path)
    html = client.get("/").text
    styles = client.get("/assets/styles.css").text

    dark_theme = styles[
        styles.index(":root {") : styles.index(':root[data-theme="light"]')
    ]
    light_theme = styles[
        styles.index(':root[data-theme="light"]') : styles.index("* { box-sizing:")
    ]
    assert "--agent-bubble-bg: #171717;" in dark_theme
    assert "--agent-bubble-text: #ffffff;" in dark_theme
    assert "--agent-bubble-border: #ffffff;" in dark_theme
    assert "--agent-bubble-bg: #ffffff;" in light_theme
    assert "--agent-bubble-text: #111111;" in light_theme
    assert "--agent-bubble-border: #111111;" in light_theme
    assert "color: var(--agent-bubble-text);" in styles
    assert "background: var(--agent-bubble-bg);" in styles
    assert styles.count("var(--agent-bubble-border)") >= 3

    mobile_1100 = styles[
        styles.index("@media (max-width: 1100px)") : styles.index("@media (max-width: 900px)")
    ]
    assert "grid-template-rows: 280px minmax(0, 1fr);" in mobile_1100
    assert "grid-template-columns: minmax(0, 1fr);" in mobile_1100
    assert "grid-template-rows: 74px minmax(0, 1fr);" in mobile_1100
    assert "height: 144px; max-height: 144px;" in mobile_1100
    assert ".home-agent-avatar-stage" in mobile_1100
    assert "grid-row: 2;" in mobile_1100

    mobile_680 = styles[
        styles.index("@media (max-width: 680px)") : styles.index("@media (max-width: 420px)")
    ]
    assert "grid-template-rows: 246px minmax(0, 1fr);" in mobile_680
    assert "grid-template-columns: minmax(0, 1fr);" in mobile_680
    assert "grid-template-rows: 72px minmax(0, 1fr);" in mobile_680
    assert "height: 130px; max-height: 130px;" in mobile_680

    mobile_420 = styles[
        styles.index("@media (max-width: 420px)") : styles.index("@media (prefers-reduced-motion: reduce)")
    ]
    assert "grid-template-rows: 230px minmax(0, 1fr);" in mobile_420
    assert "grid-template-columns: minmax(0, 1fr);" in mobile_420
    assert "height: 118px; max-height: 118px;" in mobile_420
    assert "grid-template-columns: minmax(0, 1fr) 72px" not in mobile_420

    settings_markup = html.split('id="view-settings"', 1)[1].split("<!-- Modals -->", 1)[0]
    assert "home-agent-avatar-dock" not in settings_markup
    assert "home-agent-avatar-drag" not in settings_markup


def test_virtual_avatar_dock_dragging_is_packaged(tmp_path):
    client = make_client(tmp_path)
    styles = client.get("/assets/styles.css").text
    script = client.get("/assets/app.js").text

    assert "const HOME_AGENT_AVATAR_DRAG_THRESHOLD_PX = 7;" in script
    assert "const HOME_AGENT_AVATAR_BOUNDARY_PADDING_PX = 8;" in script
    assert "const HOME_AGENT_AVATAR_SPEECH_GAP_PX = 14;" in script
    assert "const HOME_AGENT_AVATAR_SPEECH_MIN_WIDTH_PX = 64;" in script
    assert "const HOME_AGENT_AVATAR_SPEECH_MAX_WIDTH_PX = 200;" in script
    assert "const HOME_AGENT_AVATAR_SCALE_MIN = 0.67;" in script
    assert "const HOME_AGENT_AVATAR_SCALE_MAX = 1.33;" in script
    assert "const HOME_AGENT_AVATAR_SPEECH_SIDE_HYSTERESIS_PX = 24;" in script
    assert "const HOME_AGENT_AVATAR_KEYBOARD_STEP_PX = 8;" in script
    assert "const HOME_AGENT_AVATAR_KEYBOARD_FAST_STEP_PX = 32;" in script
    assert "drag: {" in script
    for field in (
        "offsetX: 0",
        "offsetY: 0",
        "pointerId: null",
        "hasMoved: false",
        "suppressPointerClick: false",
        "resizeObserver: null",
    ):
        assert field in script

    dock_styles = styles[
        styles.index(".home-agent-avatar-dock {") : styles.index(".home-agent-avatar-speech-zone {")
    ]
    assert "--home-avatar-drag-x: 0px;" in dock_styles
    assert "--home-avatar-drag-y: 0px;" in dock_styles
    assert "--home-avatar-speech-left: 0px;" in dock_styles
    assert "--home-avatar-speech-top: 0px;" in dock_styles
    assert "--home-avatar-scale: 1;" in dock_styles
    assert "--home-avatar-speech-min-width: 64px;" in dock_styles
    assert "--home-avatar-speech-max-width: 200px;" in dock_styles
    assert "--home-avatar-speech-tail-top: 50%;" in dock_styles
    assert "transform: translate3d(var(--home-avatar-drag-x), var(--home-avatar-drag-y), 0);" in dock_styles
    assert ".home-agent-avatar-dock[data-dragging=\"true\"]" in dock_styles
    button_styles = styles[
        styles.index(".home-agent-avatar-button {") : styles.index(".home-agent-avatar-button:focus-visible")
    ]
    assert "cursor: grab;" in button_styles
    assert "touch-action: none;" in button_styles
    assert "pointer-events: auto;" in button_styles
    assert "cursor: grabbing;" in button_styles

    def section(start_marker: str, end_marker: str) -> str:
        start = script.index(start_marker)
        end = script.index(end_marker, start)
        return script[start:end]

    bounds = section("function homeAgentAvatarDragBounds()", "function setHomeAgentAvatarOffset(")
    assert "const surface = $('.app-main');" in bounds
    assert "const dock = $('#home-agent-avatar-dock');" in bounds
    assert "surface.getBoundingClientRect()" in bounds
    assert "dock.getBoundingClientRect()" in bounds
    assert "const viewportWidth = document.documentElement.clientWidth;" in bounds
    assert "const viewportHeight = document.documentElement.clientHeight;" in bounds
    assert "const paintedRects = [" in bounds
    assert "dock.querySelectorAll('[data-avatar-control-boundary]')" in bounds
    assert "const baseLeft = paintedRect.left - drag.offsetX;" in bounds
    assert "const baseTop = paintedRect.top - drag.offsetY;" in bounds
    assert bounds.count("HOME_AGENT_AVATAR_BOUNDARY_PADDING_PX") == 4
    assert "if (min <= max) return { min, max };" in bounds
    assert "const centered = (min + max) / 2;" in bounds

    set_offset = section("function setHomeAgentAvatarOffset(", "function clampHomeAgentAvatarPosition()")
    assert "drag.offsetX = clamp(Number(offsetX) || 0, bounds.x.min, bounds.x.max);" in set_offset
    assert "drag.offsetY = clamp(Number(offsetY) || 0, bounds.y.min, bounds.y.max);" in set_offset
    assert "dock.style.setProperty('--home-avatar-drag-x', `${drag.offsetX}px`);" in set_offset
    assert "dock.style.setProperty('--home-avatar-drag-y', `${drag.offsetY}px`);" in set_offset

    reset_position = section(
        "function resetHomeAgentAvatarPosition()",
        "function armHomeAgentAvatarPointerClickSuppression()",
    )
    assert "if (!setHomeAgentAvatarOffset(0, 0))" in reset_position
    assert "drag.offsetX = 0;" in reset_position
    assert "drag.offsetY = 0;" in reset_position

    pointer_start = section("function startHomeAgentAvatarDrag(", "function moveHomeAgentAvatarDrag(")
    assert "drag.pointerId !== null || event.button !== 0 || event.isPrimary === false" in pointer_start
    assert "drag.pointerId = event.pointerId;" in pointer_start
    assert "event.currentTarget.setPointerCapture(event.pointerId);" in pointer_start

    pointer_move = section("function moveHomeAgentAvatarDrag(", "function endHomeAgentAvatarDrag(")
    pointer_guard = pointer_move.index("if (drag.pointerId !== event.pointerId) return;")
    threshold = pointer_move.index("Math.hypot(deltaX, deltaY) < HOME_AGENT_AVATAR_DRAG_THRESHOLD_PX")
    prevent_default = pointer_move.index("event.preventDefault();")
    assert pointer_guard < threshold < prevent_default
    assert "dock.dataset.dragging = 'true';" in pointer_move
    assert "setHomeAgentAvatarOffset(drag.startOffsetX + deltaX, drag.startOffsetY + deltaY);" in pointer_move

    pointer_end = section("function endHomeAgentAvatarDrag(", "function handleHomeAgentAvatarKeydown(")
    assert "if (button.hasPointerCapture?.(pointerId)) button.releasePointerCapture(pointerId);" in pointer_end
    assert "dock.dataset.dragging = 'false';" in pointer_end
    assert "if (didDrag) armHomeAgentAvatarPointerClickSuppression();" in pointer_end

    keyboard = section("function handleHomeAgentAvatarKeydown(", "function handleHomeAgentAvatarInteraction(")
    assert "if (event.ctrlKey || event.altKey || event.metaKey) return;" in keyboard
    assert "if (event.key === 'Home')" in keyboard
    assert "resetHomeAgentAvatarPosition();" in keyboard
    for key in ("ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"):
        assert key in keyboard
    assert "event.shiftKey" in keyboard
    assert "HOME_AGENT_AVATAR_KEYBOARD_FAST_STEP_PX" in keyboard
    assert "HOME_AGENT_AVATAR_KEYBOARD_STEP_PX" in keyboard
    assert "setHomeAgentAvatarOffset(" in keyboard

    interaction = section("function handleHomeAgentAvatarInteraction(", "function initHomeAgentAvatar()")
    assert "if (drag.suppressPointerClick && event.detail !== 0)" in interaction
    assert interaction.index("event.preventDefault();") < interaction.index("startHomeAgentAvatarWave();")
    assert "drag.suppressPointerClick = false;" in interaction

    init_avatar = section("function initHomeAgentAvatar()", "function setLoading(")
    for pointer_event, handler in (
        ("pointerdown", "startHomeAgentAvatarDrag"),
        ("pointermove", "moveHomeAgentAvatarDrag"),
        ("pointerup", "endHomeAgentAvatarDrag"),
        ("pointercancel", "endHomeAgentAvatarDrag"),
        ("lostpointercapture", "endHomeAgentAvatarDrag"),
    ):
        assert f"button.addEventListener('{pointer_event}', {handler});" in init_avatar
    assert "button.addEventListener('keydown', handleHomeAgentAvatarKeydown);" in init_avatar
    assert "button.setAttribute('aria-keyshortcuts', 'ArrowLeft ArrowRight ArrowUp ArrowDown Home');" in init_avatar
    assert "drag.resizeObserver = new ResizeObserver(clampHomeAgentAvatarPosition);" in init_avatar
    assert "drag.resizeObserver.observe($('.app-main'));" in init_avatar
    assert "drag.resizeObserver.observe($('.home-workspace'));" in init_avatar
    assert "drag.resizeObserver.observe(dock);" in init_avatar
    assert "window.addEventListener('resize', clampHomeAgentAvatarPosition);" in init_avatar
    assert "window.requestAnimationFrame(clampHomeAgentAvatarPosition);" in init_avatar


def test_virtual_avatar_speech_uses_the_roomier_side_without_covering_the_character(tmp_path):
    client = make_client(tmp_path)
    styles = client.get("/assets/styles.css").text
    script = client.get("/assets/app.js").text

    start = script.index("function positionHomeAgentAvatarSpeech()")
    end = script.index("function showHomeAgentAvatarSpeech(", start)
    positioning = script[start:end]
    assert "const leftSpace = buttonRect.left - HOME_AGENT_AVATAR_BOUNDARY_PADDING_PX;" in positioning
    assert "const rightSpace = viewportWidth - HOME_AGENT_AVATAR_BOUNDARY_PADDING_PX - buttonRect.right;" in positioning
    assert "if (side !== 'left' && side !== 'right') side = rightSpace >= leftSpace ? 'right' : 'left';" in positioning
    assert "rightSpace > leftSpace + HOME_AGENT_AVATAR_SPEECH_SIDE_HYSTERESIS_PX" in positioning
    assert "leftSpace > rightSpace + HOME_AGENT_AVATAR_SPEECH_SIDE_HYSTERESIS_PX" in positioning
    assert "Math.min(HOME_AGENT_AVATAR_SPEECH_MAX_WIDTH_PX, availableWidth)" in positioning
    assert "dock.dataset.speechSide = side;" in positioning
    assert "--home-avatar-speech-left" in positioning
    assert "--home-avatar-speech-top" in positioning
    assert "--home-avatar-speech-min-width" in positioning
    assert "--home-avatar-speech-max-width" in positioning
    assert "const measuredWidth = speechZone.getBoundingClientRect().width;" in positioning
    assert "--home-avatar-speech-tail-top" in positioning
    assert "surfaceBottom - HOME_AGENT_AVATAR_BOUNDARY_PADDING_PX - bubbleHeight" in positioning
    assert "headAnchorY - speechTopViewport" in positioning

    set_offset_start = script.index("function setHomeAgentAvatarOffset(")
    set_offset_end = script.index("function clampHomeAgentAvatarPosition()", set_offset_start)
    assert "positionHomeAgentAvatarSpeech();" in script[set_offset_start:set_offset_end]

    speech_zone = styles[
        styles.index(".home-agent-avatar-speech-zone {") : styles.index(".home-agent-avatar-speech {", styles.index(".home-agent-avatar-speech-zone {"))
    ]
    assert "position: absolute;" in speech_zone
    assert "left: var(--home-avatar-speech-left);" in speech_zone
    assert "top: var(--home-avatar-speech-top);" in speech_zone
    assert "width: max-content;" in speech_zone
    assert "max-width: var(--home-avatar-speech-max-width);" in speech_zone
    assert "pointer-events: none;" in speech_zone
    assert '.home-agent-avatar-dock[data-speech-side="right"] .home-agent-avatar-speech::after' in styles


def test_virtual_avatar_quick_actions_and_preferences_are_packaged(tmp_path):
    client = make_client(tmp_path)
    html = client.get("/").text
    styles = client.get("/assets/styles.css").text
    script = client.get("/assets/app.js").text

    for action in ("schedule", "weather", "literature", "exams"):
        assert f'data-avatar-action="{action}"' in html
        assert f'data-avatar-action-toggle="{action}"' in html
    assert 'id="feature-avatar-literature-direction"' in html
    assert 'value="computer-science"' in html
    assert 'value="life-science"' in html

    assert "function normalizeFeaturePreferences(value = {})" in script
    assert "avatarScale: normalizeHomeAgentAvatarScale(features.avatarScale)" in script
    assert "avatarActions: normalizeAvatarActions(features.avatarActions)" in script
    assert "literatureDirection: normalizeLiteratureDirection(features.literatureDirection)" in script
    assert "function formatTodayScheduleSpeech()" in script
    assert "if (!items.length) return '今天还没有安排计划~';" in script
    assert "function formatExamSpeech()" in script
    assert "item.category === 'exam' || item.source === 'ustc'" in script
    assert "function formatLiteratureSpeech()" in script
    assert "当前为静态精选，后续接入实时文献源。" in script
    assert "await api('/api/weather/today', {}, 12000)" in script
    assert "const HOME_AGENT_AVATAR_ACTION_DURATION_MS = 0;" in script
    assert "function updateAvatarActionPreference(action, enabled)" in script
    assert "function updateLiteratureDirection(value)" in script
    cancel_start = script.index("function cancelHomeAgentAvatarAction()")
    cancel_end = script.index("function hideHomeAgentAvatarSpeech", cancel_start)
    cancel_script = script[cancel_start:cancel_end]
    assert "if (avatar.mode === 'thinking' && !state.isQuerying) setHomeAgentAvatarMode('idle');" in cancel_script
    assert "const enabled = Boolean(state.user)" in script
    assert "&& !state.isQuerying" in script
    assert "syncHomeAgentAvatarActionControls();" in script[
        script.index("function setLoading(") : script.index("// ---------- Views ----------")
    ]
    assert "pointerType === 'touch' && !avatar.controlsOpen" in script
    assert "scheduleHomeAgentAvatarControlsClose();" in script

    assert ".home-agent-avatar-scale-control" in styles
    assert ".home-agent-avatar-action-group-left" in styles
    assert ".home-agent-avatar-action-group-right" in styles
    avatar_button_styles = styles[
        styles.index(".home-agent-avatar-button {") : styles.index(
            ".home-agent-avatar-button[aria-disabled=", styles.index(".home-agent-avatar-button {")
        )
    ]
    assert "transform: translateX(-50%) scale(var(--home-avatar-scale));" in avatar_button_styles
    assert "transform-origin: center bottom;" in avatar_button_styles
    assert "height: min(100%, calc(75% + 8px));" in avatar_button_styles
    profile_avatar_image_styles = styles[
        styles.index(".user-avatar img,") : styles.index(".user-info {", styles.index(".user-avatar img,"))
    ]
    assert "height: 100%;" in profile_avatar_image_styles
    assert "calc(75% + 8px)" not in profile_avatar_image_styles
    assert ':focus-within .home-agent-avatar-action-group' in styles
    assert "white-space: pre-line;" in styles
    assert "font-size: 0.7rem;" in styles
    assert "max-height: min(56vh, 336px);" in styles
    assert ".home-agent-avatar-speech-dismiss" in styles
    assert "width: 44px;" in styles[
        styles.index(".home-agent-avatar-speech-dismiss {") : styles.index(
            ".home-agent-avatar-speech[data-dismissible=", styles.index(".home-agent-avatar-speech-dismiss {")
        )
    ]
    speech_zone = styles[
        styles.index(".home-agent-avatar-speech-zone {") : styles.index(
            ".home-agent-avatar-speech {", styles.index(".home-agent-avatar-speech-zone {")
        )
    ]
    assert "min-width: var(--home-avatar-speech-min-width);" in speech_zone
    assert "min-width: 0;" not in speech_zone
