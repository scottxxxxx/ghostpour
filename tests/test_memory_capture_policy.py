"""Unit tests for the memory-capture verdict resolver.

Verdict matrix:
  feature_state | has_quota | verdict           | cta_kind
  enabled       | (any)     | capture           | None
  teaser        | (any)     | recall_only       | None
  disabled      | True      | capture_with_cta  | free_within_quota_footer
  disabled      | False     | skip_with_cta     | free_no_quota_only
"""

import pytest

from app.services.memory_capture_policy import resolve_memory_capture_verdict


@pytest.mark.parametrize("has_quota", [True, False])
def test_pro_always_captures_no_cta(has_quota):
    v = resolve_memory_capture_verdict(feature_state="enabled", has_quota=has_quota)
    assert v.verdict == "capture"
    assert v.cta_kind is None


@pytest.mark.parametrize("has_quota", [True, False])
def test_plus_recall_only_no_cta(has_quota):
    v = resolve_memory_capture_verdict(feature_state="teaser", has_quota=has_quota)
    assert v.verdict == "recall_only"
    assert v.cta_kind is None


def test_free_within_quota_captures_with_cta():
    v = resolve_memory_capture_verdict(feature_state="disabled", has_quota=True)
    assert v.verdict == "capture_with_cta"
    assert v.cta_kind == "free_within_quota_footer"


def test_free_over_quota_skips_with_cta():
    v = resolve_memory_capture_verdict(feature_state="disabled", has_quota=False)
    assert v.verdict == "skip_with_cta"
    assert v.cta_kind == "free_no_quota_only"
