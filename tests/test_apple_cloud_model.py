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
    assert cloud["maxImagesPerRequest"] == 0 and cloud["supportsVision"] is False
