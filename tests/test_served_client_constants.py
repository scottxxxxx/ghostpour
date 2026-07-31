"""Constants SS handed us, so they stop being compiled into the app.

Standing direction (Scott, 2026-07-31): anything tunable in a client app
should be served by us and changeable without a release. SS offered their
list after their 300 second report threshold cost a demo meeting that
captured 298 seconds.

Every value here MATCHES the client constant it replaces, so wiring it up is
a no-op flip. These tests exist to keep that true: if someone changes a value
here they should be doing it deliberately, with SS told.
"""

from __future__ import annotations

import json

CFG = json.load(open("config/remote/client-config.json"))


def test_post_session_carries_both_report_and_analysis_gates():
    ps = CFG["post_session"]
    assert ps["report_min_seconds"] == 300
    assert ps["allow_request_below_minimum"] is True
    assert ps["request_min_seconds"] == 30
    # the analysis twins, taken 2026-07-31
    assert ps["analysis_min_seconds"] == 120
    assert ps["analysis_min_words"] == 300


def test_enrichment_retry_policy_is_served():
    e = CFG["enrichment"]
    assert e["max_auto_attempts"] == 5
    assert e["retry_interval_seconds"] == 86400      # 24h
    assert e["in_flight_stale_seconds"] == 300       # 5min
    assert e["foreground_sweep_recovery_cap"] == 5


def test_session_windows_are_served():
    s = CFG["session"]
    assert s["resumable_window_seconds"] == 3600
    assert s["min_post_processing_seconds"] == 120


def test_image_values_stay_where_they_already_live():
    """SS listed 1024/0.7 as constants to move, but we already serve these
    per tier. Adding a second home for them would create exactly the split
    that made their word floor claim 'GP spec' for months."""
    tiers = json.load(open("config/remote/tiers.json"))["tiers"]
    for name, t in tiers.items():
        imgs = (t.get("feature_definitions") or {}).get("images")
        if imgs:
            assert imgs["max_long_edge"] == 1568
            assert imgs["jpeg_quality"] == 0.8
    assert "images" not in CFG, "image config has one home, in tiers"


def test_the_audio_pipeline_is_not_served():
    """SS held back EnrollmentVAD and SpeakerEngine and they are right: a bad
    served value would wreck diarization with no way to correlate the damage
    back to a config change."""
    blob = json.dumps(CFG).lower()
    for forbidden in ("vad", "speaker_engine", "enrollment", "diariz"):
        assert forbidden not in blob
