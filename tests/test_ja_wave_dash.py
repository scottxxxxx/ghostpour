"""The Japanese prompts ban the wave dash too (2026-11-01).

SS Dev's Glance demo on the Pro demo account (Sonnet 5, cards on) came back
with a card headline "ゾーンC〜F": a range written with the wave dash. The
served ja prompts (version 11) banned only emダッシュ and enダッシュ, "the
long horizontal bar", so 〜 was never covered in Japanese, and the ja
analysisSchema itself wrote "5〜10語程度" and "1〜4個", modelling the mark
the prompt bans. Scott's rule is no dash punctuation anywhere he publishes.
So every ja dash sentence now names 波ダッシュ（〜）and 全角ダッシュ（―）
and says ranges are written AからB, and no served ja text carries either
mark outside that sentence.
"""
import json
import re
from pathlib import Path

JA = Path(__file__).resolve().parent.parent / "config" / "remote" / "protected-prompts.ja.json"
BAN = "波ダッシュ（〜）や全角ダッシュ（―）も同じく使わず、範囲は「AからB」のように書いてください。"
OLD_BAN = "出力のどこにも、emダッシュやenダッシュ"


def _texts(node, path="", out=None):
    out = [] if out is None else out
    if isinstance(node, str):
        out.append((path, node))
    elif isinstance(node, dict):
        for k, v in node.items():
            _texts(v, f"{path}.{k}" if path else k, out)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _texts(v, f"{path}[{i}]", out)
    return out


def _doc():
    return json.loads(JA.read_text(encoding="utf-8"))


def test_every_ja_dash_sentence_also_bans_the_wave_dash():
    texts = _texts(_doc())
    with_ban = [p for p, t in texts if OLD_BAN in t]
    assert len(with_ban) >= 8, with_ban
    missing = [p for p, t in texts if OLD_BAN in t and BAN not in t]
    assert not missing, f"ja prompts that ban only the long bar: {missing}"


def test_no_served_ja_text_carries_a_wave_or_fullwidth_dash():
    offenders = []
    for p, t in _texts(_doc()):
        stripped = t.replace(BAN, "")
        if re.search("[〜―—–]", stripped):
            offenders.append((p, re.findall("[^。\n]*[〜―—–][^。\n]*", stripped)[:2]))
    assert not offenders, f"the prompt models the mark it bans: {offenders}"


def test_the_ja_version_moved_with_the_contract():
    assert _doc()["version"] >= 12
