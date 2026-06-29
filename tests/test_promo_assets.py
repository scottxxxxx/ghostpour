"""Hot-reloadable promo creatives: live store wins over bundled, admin upload
updates a creative with no code deploy."""

ADMIN = {"X-Admin-Key": "test-admin-key"}
NAME = "ss-launch-techrehearsal.html"

NEW_HTML = (
    "<!doctype html><html><body>"
    "<a href='https://x?promo_cta_id=zz' data-cta-id='zz'>NEW CREATIVE</a>"
    "</body></html>"
)


def test_serve_falls_back_to_bundled(client):
    # No store copy yet -> the bundled creative serves (the shipped one).
    r = client.get(f"/v1/promo/assets/{NAME}")
    assert r.status_code == 200
    assert "Try Tech Rehearsal" in r.text


def test_upload_hot_reloads_without_deploy(client):
    # Upload a new creative live; the store copy must win immediately.
    r = client.put(f"/webhooks/admin/promo-asset/{NAME}", content=NEW_HTML, headers=ADMIN)
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "store"
    served = client.get(f"/v1/promo/assets/{NAME}").text
    assert "NEW CREATIVE" in served and "promo_cta_id=zz" in served
    # listing reflects the store copy
    assets = {a["name"]: a for a in client.get("/webhooks/admin/promo-assets", headers=ADMIN).json()["assets"]}
    assert assets[NAME]["source"] == "store"
    # delete reverts to the bundled default
    assert client.delete(f"/webhooks/admin/promo-asset/{NAME}", headers=ADMIN).status_code == 200
    assert "Try Tech Rehearsal" in client.get(f"/v1/promo/assets/{NAME}").text


def test_upload_validation_and_auth(client):
    # non-html rejected
    assert client.put("/webhooks/admin/promo-asset/evil.js", content="x", headers=ADMIN).status_code == 400
    # empty body rejected
    assert client.put("/webhooks/admin/promo-asset/empty.html", content="", headers=ADMIN).status_code == 400
    # admin required
    assert client.put("/webhooks/admin/promo-asset/x.html", content="x",
                      headers={"X-Admin-Key": "wrong"}).status_code == 403
    # serve guards traversal + unknown
    assert client.get("/v1/promo/assets/..%2f..%2fconfig%2ftiers.yml").status_code == 404
    assert client.get("/v1/promo/assets/nope.html").status_code == 404


def test_delete_missing_store_copy_is_404(client):
    # nothing live -> deleting reports 404, bundled default is untouched
    assert client.delete(f"/webhooks/admin/promo-asset/{NAME}", headers=ADMIN).status_code == 404
    assert client.get(f"/v1/promo/assets/{NAME}").status_code == 200


# 1x1 transparent PNG
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6360000002000154a24f6f0000000049454e44ae426082"
)


def test_image_creative_serves_with_image_content_type(client):
    # An image creative can be hosted (native card media.url) and serves as image/png.
    r = client.put("/webhooks/admin/promo-asset/tr-hero.png", content=PNG, headers=ADMIN)
    assert r.status_code == 200, r.text
    served = client.get("/v1/promo/assets/tr-hero.png")
    assert served.status_code == 200
    assert served.headers["content-type"].startswith("image/png")
    assert served.content == PNG
    # listing includes the image creative
    assets = {a["name"]: a for a in client.get("/webhooks/admin/promo-assets", headers=ADMIN).json()["assets"]}
    assert assets["tr-hero.png"]["source"] == "store"
    assert client.delete("/webhooks/admin/promo-asset/tr-hero.png", headers=ADMIN).status_code == 200
