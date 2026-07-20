"""Reactive capture-quality note (2026-07-20): blur detection on a
submitted image drives a reply-appended nudge, scoped to the reproduction
lane and gated by a served flag. See app/services/image_quality.py."""

import base64
from io import BytesIO

from PIL import Image, ImageDraw, ImageFilter

from app.services import image_quality as iq


def _b64(img) -> str:
    buf = BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=80)
    return base64.b64encode(buf.getvalue()).decode()


def _sharp_img():
    # Fine checkerboard: lots of high-frequency edges -> high Laplacian var.
    img = Image.new("L", (400, 400), 255)
    d = ImageDraw.Draw(img)
    for x in range(0, 400, 4):
        for y in range(0, 400, 4):
            if (x // 4 + y // 4) % 2 == 0:
                d.rectangle([x, y, x + 3, y + 3], fill=0)
    return img


def _blurry_img():
    return _sharp_img().filter(ImageFilter.GaussianBlur(8))


GUIDANCE = {"title": "For the sharpest read",
            "tips": ["Fill the frame", "Hold steady", "Even light"]}
CFG_ON = {
    "client-config": {"image_quality_note": {"enabled": True, "blur_threshold": 200}},
    "tiers": {"tiers": {"pro": {"feature_definitions": {
        "images": {"capture_guidance": GUIDANCE}}}}},
}
CFG_OFF = {**CFG_ON, "client-config": {"image_quality_note": {"enabled": False}}}


def test_blur_variance_orders_sharp_above_blurry():
    sharp = iq.blur_variance(_b64(_sharp_img()))
    blurry = iq.blur_variance(_b64(_blurry_img()))
    assert sharp is not None and blurry is not None
    assert sharp > blurry
    # sanity against the default threshold
    assert sharp > iq._DEFAULT_BLUR_THRESHOLD > blurry


def test_blur_variance_failopen_on_garbage():
    assert iq.blur_variance("not-base64-image") is None


def test_looks_low_quality_gates_on_content():
    assert iq.looks_low_quality([_b64(_blurry_img())], CFG_ON) is True
    assert iq.looks_low_quality([_b64(_sharp_img())], CFG_ON) is False
    assert iq.looks_low_quality([], CFG_ON) is False


def test_note_only_when_enabled_and_low():
    # off flag -> never
    assert iq.maybe_capture_quality_note([_b64(_blurry_img())], CFG_OFF, "pro") is None
    # on + sharp -> no note
    assert iq.maybe_capture_quality_note([_b64(_sharp_img())], CFG_ON, "pro") is None
    # on + blurry -> note, reusing the served tips, dash-free
    note = iq.maybe_capture_quality_note([_b64(_blurry_img())], CFG_ON, "pro")
    assert note and "Fill the frame" in note and "For the sharpest read" in note
    assert "—" not in note and "–" not in note


def test_build_note_none_without_guidance():
    cfg = {"tiers": {"tiers": {"pro": {"feature_definitions": {"images": {}}}}}}
    assert iq.build_capture_note(cfg, "pro") is None
