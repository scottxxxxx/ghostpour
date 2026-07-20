"""Reactive image-quality note (Scott + SS, 2026-07-20).

Instead of nagging every user with capture tips up front, we only speak
up when it mattered: a document REPRODUCTION turn (generation armed, an
image attached) whose submitted image looks too soft to read cleanly. In
that case we append the same capture guidance we serve in the tiers
images block to the chat reply, framed as a "here is how to get the rest
next time" nudge rather than a scolding.

Why blur and not resolution: our own sweep (2026-07-20) showed pixel
count is a poor legibility proxy (a 48MP phone photo downscaled to 1280
out-read a 1786px screenshot at 1568). Sharpness predicts a good read far
better, so we score the variance of a Laplacian-filtered grayscale, the
standard blur metric. Scoped to the reproduction lane so casual "what is
in this photo" queries are never touched, which keeps false positives
contained.

Everything here is fail-open: any decode or config hiccup returns "not
low quality" / no note, so the reply is never blocked. Ships dark
(flag absent = off) so it can be flipped and the threshold tuned with no
build. Pillow only, no numpy.
"""

import base64
import logging
from io import BytesIO

logger = logging.getLogger("ghostpour.image_quality")

# Laplacian variance below this (on a long-edge-normalized grayscale) reads
# as blurry. Calibrated 2026-07-20 against the sweep captures: known-good
# images scored 339 (a phone photo of a screen) to 1084 (a crisp
# screenshot), while JPEG block artifacts put a hard floor around 160 that
# even a heavily blurred image cannot fall below. 200 sits above that floor
# (so genuinely soft images are caught) and well below the lowest good
# capture (so real photos are never falsely flagged). Conservative on
# purpose, favoring silence over false alarms. Tune via the served flag.
_DEFAULT_BLUR_THRESHOLD = 200.0
# Normalize the working image so the threshold is resolution-independent.
_WORK_LONG_EDGE = 1024


def image_quality_note_enabled(remote_configs: dict) -> bool:
    """Served flag: client-config.image_quality_note.enabled. Absent = off,
    so the note can be dark-shipped and flipped without a deploy."""
    cfg = (remote_configs or {}).get("client-config") or {}
    block = cfg.get("image_quality_note")
    return bool(isinstance(block, dict) and block.get("enabled"))


def _blur_threshold(remote_configs: dict) -> float:
    cfg = (remote_configs or {}).get("client-config") or {}
    block = cfg.get("image_quality_note")
    if isinstance(block, dict):
        try:
            return float(block["blur_threshold"])
        except (KeyError, TypeError, ValueError):
            pass
    return _DEFAULT_BLUR_THRESHOLD


def blur_variance(image_b64: str) -> float | None:
    """Variance of a Laplacian-filtered grayscale of the image. Higher is
    sharper. Returns None if the image can't be decoded (fail-open)."""
    try:
        from PIL import Image, ImageFilter, ImageStat

        raw = base64.b64decode(image_b64)
        img = Image.open(BytesIO(raw)).convert("L")
        # Normalize scale so one threshold works across image sizes.
        long_edge = max(img.size)
        if long_edge > _WORK_LONG_EDGE:
            ratio = _WORK_LONG_EDGE / long_edge
            img = img.resize((max(1, int(img.width * ratio)),
                              max(1, int(img.height * ratio))))
        edges = img.filter(ImageFilter.Kernel(
            (3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1))
        return float(ImageStat.Stat(edges).var[0])
    except Exception as e:  # noqa: BLE001
        logger.warning("blur_variance failed: %s", e)
        return None


def looks_low_quality(images, remote_configs: dict) -> bool:
    """True only if the first submitted image is confidently soft. Missing
    image, decode failure, or a variance at/above threshold all read as
    fine, so we never nudge on ambiguity."""
    if not images:
        return False
    var = blur_variance(images[0])
    if var is None:
        return False
    return var < _blur_threshold(remote_configs)


def build_capture_note(remote_configs: dict, tier_name: str) -> str | None:
    """Assemble the reply-appended note from the SAME capture_guidance we
    serve in the tiers images block, so the advice has one source. Returns
    None if no guidance is configured for the tier."""
    from app.services.document_generation import tier_feature_block

    images = tier_feature_block(remote_configs, tier_name, "images") or {}
    guide = images.get("capture_guidance")
    if not isinstance(guide, dict):
        return None
    tips = [t for t in (guide.get("tips") or []) if isinstance(t, str) and t]
    if not tips:
        return None
    lead = ("I built this from your photo, but a few areas came through too "
            "blurry to read cleanly, so some cells may be off. A sharper, "
            "straight on capture would let me get everything next time:")
    title = guide.get("title") or "For the sharpest read"
    bullets = "\n".join(f"- {t}" for t in tips)
    return f"{lead}\n\n{title}\n{bullets}"


def maybe_capture_quality_note(images, remote_configs: dict,
                               tier_name: str) -> str | None:
    """One call for the router: returns the note to append, or None. Gated
    on the served flag; fail-open."""
    try:
        if not image_quality_note_enabled(remote_configs):
            return None
        if not looks_low_quality(images, remote_configs):
            return None
        return build_capture_note(remote_configs, tier_name)
    except Exception as e:  # noqa: BLE001
        logger.warning("capture_quality_note failed: %s", e)
        return None
