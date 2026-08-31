"""The share page renders the summary's markdown (Scott 2026-08-24: the
model writes headings/bold/bullets and the page showed the raw chars),
and does it SAFELY (escape first, only our formatting is live)."""
from app.services.share_bundle import render_markdown


def test_headings_bold_italic_bullets_and_code():
    md = "# Resumen de la Reunion\n**Estado y Alcance**\n- El alcance original\n- Se identificaron\nTexto con *enfasis* y `codigo`."
    h = render_markdown(md)
    assert "<h3>Resumen de la Reunion</h3>" in h
    assert "<strong>Estado y Alcance</strong>" in h
    assert h.count("<li>") == 2 and "<ul>" in h and "</ul>" in h
    assert "<em>enfasis</em>" in h and "<code>codigo</code>" in h
    assert "**" not in h and "# " not in h


def test_numbered_lists_and_bullet_glyph():
    h = render_markdown("1. first\n2. second\n• third")
    assert h.count("<li>") == 3


def test_html_is_escaped_before_formatting_no_injection():
    h = render_markdown("**bold** <script>alert(1)</script> and <img src=x onerror=y>")
    assert "<script>" not in h and "onerror=y>" not in h
    assert "&lt;script&gt;" in h and "<strong>bold</strong>" in h


def test_only_http_links_become_anchors():
    assert '<a href="https://a.com" target="_blank" rel="noopener nofollow">site</a>' in render_markdown("[site](https://a.com)")
    # javascript: and other schemes are left as inert text
    h = render_markdown("[x](javascript:alert(1))")
    assert "<a " not in h and "javascript:alert(1)" in h


def test_empty_is_empty():
    assert render_markdown("") == "" and render_markdown("   ") == "" and render_markdown(None) == ""


def test_the_page_shows_formatted_summary_not_raw_markdown(client, pro_user):
    import io, json, zipfile
    ORIGIN = "0E0E0E0E-1111-4222-8333-444444444444"
    rec = {"title": "Kickoff", "durationSeconds": 90.0,
           "rollingSummary": "## Estado y Alcance\n- **El alcance** original se entrego\n- Se identificaron features",
           "transcript": "hi"}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps({"formatVersion": 1}))
        z.writestr(f"meetings/{ORIGIN}.json", json.dumps(rec, ensure_ascii=False))
    r = client.post("/v1/shares", content=buf.getvalue(), headers={**pro_user["headers"],
        "Content-Type": "application/vnd.shouldersurf.archive", "X-Share-Title": "Kickoff",
        "X-Share-Date": "2026-08-24", "X-Share-Duration-Seconds": "90",
        "X-Share-Summary-Line": "s", "X-Share-Transcript-Included": "true"})
    assert r.status_code == 200, r.text
    token = r.json()["url"].rsplit("/", 1)[1]
    page = client.get(f"/s/{token}").text
    assert "<h4>Estado y Alcance</h4>" in page          # ## -> h4
    assert "<strong>El alcance</strong>" in page
    assert "<li>" in page
    assert "## Estado" not in page and "**El alcance**" not in page   # no raw markdown left
