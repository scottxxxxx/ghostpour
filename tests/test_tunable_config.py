"""Pin the JSON-as-source-of-truth contract for tunable per-tier
parameters. Today's coverage is `max_input_tokens`; new fields slot in
the same way.

Two invariants to lock:
1. tiers.json wins over tiers.yml for tunable values.
2. Yaml is the safety-net default — used only when the JSON path is
   missing entirely (degraded environment, fresh container before seed,
   etc.).
"""

from app.services.tunable_config import (
    project_chat_max_input_tokens,
    _read_json_field,
)


def _configs(free=None, plus=None, pro=None):
    """Build a minimal remote_configs dict mirroring tiers.json shape.
    Pass None to omit a tier's max_input_tokens; pass a number to set it."""
    tiers = {}
    for name, value in (("free", free), ("plus", plus), ("pro", pro)):
        if value is None:
            tiers[name] = {}
        else:
            tiers[name] = {
                "feature_definitions": {
                    "project_chat": {"max_input_tokens": value},
                },
            }
    return {"tiers": {"version": 15, "tiers": tiers}}


class TestProjectChatMaxInputTokens:
    def test_json_wins_over_yaml(self):
        configs = _configs(free=99999)
        # Yaml says 50_000, JSON says 99_999 → JSON wins.
        assert project_chat_max_input_tokens(configs, "free", yaml_default=50_000) == 99_999

    def test_yaml_used_when_json_field_missing(self):
        """Tier exists in JSON but the max_input_tokens field is absent
        — fall back to yaml. This is the "newly-added field, hasn't
        been stamped into JSON yet" case."""
        configs = _configs(free=None)  # tiers.free has no feature_definitions
        assert project_chat_max_input_tokens(configs, "free", yaml_default=50_000) == 50_000

    def test_yaml_used_when_no_remote_configs(self):
        """Degraded environment with no configs at all (tests, fresh
        container before seed). Caller still gets a sane value."""
        assert project_chat_max_input_tokens(None, "free", yaml_default=50_000) == 50_000

    def test_yaml_used_when_tiers_config_missing(self):
        """remote_configs has no 'tiers' slug at all."""
        assert project_chat_max_input_tokens({}, "free", yaml_default=50_000) == 50_000

    def test_unknown_tier_falls_back_to_yaml(self):
        """Caller passes a tier name not in the JSON. Yaml default still
        wins so we never crash on a typo."""
        configs = _configs(free=99999)
        assert project_chat_max_input_tokens(configs, "nonexistent", yaml_default=-1) == -1

    def test_negative_one_means_uncapped(self):
        """-1 round-trips through the JSON path — admin shouldn't be
        special-cased to fall back to yaml just because their cap is -1."""
        configs = _configs(free=-1)
        assert project_chat_max_input_tokens(configs, "free", yaml_default=50_000) == -1

    def test_invalid_json_value_falls_back(self):
        """Garbage data in the JSON (string instead of int) shouldn't
        explode the request; fall through to yaml."""
        configs = {
            "tiers": {
                "tiers": {"free": {"feature_definitions": {"project_chat": {"max_input_tokens": "fifty thousand"}}}},
            },
        }
        assert project_chat_max_input_tokens(configs, "free", yaml_default=50_000) == 50_000


class TestReadJsonField:
    def test_reads_nested_path(self):
        configs = {
            "tiers": {
                "tiers": {
                    "free": {
                        "feature_definitions": {
                            "project_chat": {"max_input_tokens": 50_000, "other": "x"},
                        },
                    },
                },
            },
        }
        assert _read_json_field(configs, "free", "project_chat", "max_input_tokens") == 50_000
        assert _read_json_field(configs, "free", "project_chat", "other") == "x"

    def test_returns_none_for_missing_node(self):
        """Missing at any level → None, never raises."""
        assert _read_json_field(None, "free", "project_chat", "x") is None
        assert _read_json_field({}, "free", "project_chat", "x") is None
        assert _read_json_field({"tiers": {}}, "free", "project_chat", "x") is None
        assert _read_json_field({"tiers": {"tiers": {}}}, "free", "project_chat", "x") is None
