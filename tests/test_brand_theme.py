from fastapi.testclient import TestClient

from ninerouter_orchestrator.web import app


def test_ui_uses_9router_brand_assets():
    client = TestClient(app)
    page = client.get("/").text
    css = client.get("/assets/styles.css").text

    assert 'name="theme-color" content="#fdfaf6"' in page
    assert 'rel="icon" href="/assets/favicon.svg" type="image/svg+xml"' in page
    assert "family=Inter:wght@400;500;600;700;800" in page
    assert 'class="brand-mark" aria-hidden="true"' in page
    assert '<img src="/assets/favicon.svg" alt="" width="34" height="34">' in page
    assert '.brand-mark img { display: block; width: 100%; height: 100%; }' in css
    assert "--paper: #fdfaf6;" in css
    assert "--signal: #e56a4a;" in css
    assert '--font-sans: "Inter"' in css
    assert "--radius: 10px;" in css
    assert "--radius-lg: 14px;" in css
    for retired_font in ("Syne", "Manrope", "DM Mono"):
        assert retired_font not in page
        assert retired_font not in css


def test_favicon_is_served_as_svg():
    response = TestClient(app).get("/assets/favicon.svg")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert 'viewBox="0 0 64 64"' in response.text


def test_brand_theme_keeps_motion_and_visibility_guards():
    css = TestClient(app).get("/assets/styles.css").text
    assert "[hidden] { display: none !important; }" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "animation: none !important; transition: none !important;" in css
    assert ":focus-visible { outline: 3px solid var(--signal-dark);" in css


def test_ui_replaces_native_control_chrome_and_confirmations():
    client = TestClient(app)
    page = client.get("/").text
    css = client.get("/assets/styles.css").text
    controls = client.get("/assets/theme-controls.js").text
    scripts = "\n".join(
        client.get(f"/assets/{name}").text for name in ("app.js", "workspaces.js")
    )

    assert 'id="theme-confirm"' in page
    assert 'src="/assets/theme-controls.js"' in page
    assert ".theme-select-button" in css
    assert 'input[type="checkbox"]' in css
    assert "appearance: none;" in css
    assert "window.ThemeControls" in controls
    assert "refreshSelect" in controls
    assert "window.confirm(" not in scripts
    assert ".confirm(" not in scripts.replace("ThemeControls.confirm(", "")
