"""Apple Cloud (Private Cloud Compute) in the SS model list, Scott 2026-10-05.
Builds before 2126 have no cloud code and would run the on-device model under
the cloud name, so every locale's entry must carry minAppBuild 2126 and must
not become the default."""
import json
from pathlib import Path

import pytest

REMOTE = Path(__file__).resolve().parent.parent / "config" / "remote"


@pytest.mark.parametrize("name", ["llm-providers.json", "llm-providers.es.json",
                                  "llm-providers.fr.json", "llm-providers.ja.json"])
def test_apple_cloud_is_gated_to_build_2126_and_not_default(name):
    doc = json.loads((REMOTE / name).read_text(encoding="utf-8"))
    on_device = next(p for p in doc["providers"] if p["id"] == "onDevice")
    ids = [m["id"] for m in on_device["models"]]
    assert ids[:2] == ["foundation-models", "private-cloud-compute"]
    cloud = on_device["models"][1]
    assert cloud["minAppBuild"] == 2126
    assert cloud["isDefault"] is False and on_device["models"][0]["isDefault"] is True
    assert cloud["contextWindow"] == 32768 and cloud["streamingSupported"] is False
    # Vision (SS, 2026-10-13, read from code): since build 2151 the client asks
    # Apple Cloud for its own .vision capability on iOS 27 and caps apple
    # models at 5 images (SessionManager.swift:5346, MeetingChatSection and
    # ProjectChatSection effectiveMaxImages). Builds 2126 to 2150 were text
    # only and merely hide or show the photo strip on this flag, and were
    # never distributed. On-device stays text only: 2151 turned images off.
    # 1.19 (build 2196, SS 2026-10-13, read from LLMService.applePhotoLimit):
    # free Apple Cloud takes 1 photo per question, an owner of
    # unlock_apple_cloud 5. The served value describes what most users get.
    assert cloud["maxImagesPerRequest"] == 1 and cloud["supportsVision"] is True
    on_dev = on_device["models"][0]
    assert on_dev["supportsVision"] is False and on_dev["maxImagesPerRequest"] == 0
