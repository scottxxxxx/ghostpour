"""GP answers Help questions from its own copy of the app's articles
(Scott, 2026-11-27): app/services/help_kb.py and the `kb_hash` path of
POST /v1/help/ask. Contract: docs/wire-contracts/help-ask.md.

The model is stubbed in every test (conftest's mock_provider), so nothing
here can spend a token.
"""

from __future__ import annotations

import json
import shutil

import pytest

from app.models.chat import ChatResponse
from app.services import help_ask, help_kb
from tests.test_help_ask import (MODEL_COST, _articles, _body, _dial, _post, _reply,
                                 _rows, _uuid)

# The Help set inside Shoulder Surf 1.20 (2702), SS repo commit c992efd3,
# and the next one (ac4d8cdd). SS Dev computed both hashes from the built
# bundle; GP computed the same values from the repo.
KB_2702 = "64930492bcf3449e380ee9d97c77362ceaf55bafa4865a1fa8384d4d4bdad480"
KB_NEXT = "05a599f84bd411e9e87f6d33cd4fe4208cd85aeaf7af6b3848976772893edcba"
KB_TASKS = "414ac60a35fc90c40b8f930335e6817cae828fdb09b28219505200345164a51c"  # SS 0a41d849
APP = "shouldersurf"


@pytest.fixture
def client(client):
    _dial(client, enabled=True)
    return client


def _pick(ids, *, input_tokens: int = 5000, output_tokens: int = 30) -> ChatResponse:
    raw = {"content": [{"type": "tool_use", "name": "help_pick", "id": "toolu_p",
                        "input": {"article_ids": list(ids)}}], "stop_reason": "tool_use"}
    return ChatResponse(text="", input_tokens=input_tokens, output_tokens=output_tokens,
                        model="claude-haiku-5-5", provider="anthropic",
                        usage={"input_tokens": input_tokens, "output_tokens": output_tokens},
                        raw_response_json=json.dumps(raw))


def _kb_body(**over) -> dict:
    body = _body(kb_hash=KB_2702, app_locale="en",
                 question="how do i see who owes something after a meeting")
    del body["articles"]
    body.update(over)
    return body


def _sent(mock_provider, n: int):
    """The n-th model request of the test (0 is the pick)."""
    return mock_provider.call_args_list[n].args[0]


# --- the store ---------------------------------------------------------------


@pytest.mark.parametrize("kb_hash", [KB_2702, KB_NEXT, KB_TASKS])
def test_every_imported_help_set_still_hashes_to_its_folder_name(kb_hash):
    """The folder name is the promise that these are the build's files. A
    hand edit under config/help-kb breaks it, and this is where that shows."""
    folder = help_kb.KB_ROOT / APP / kb_hash
    assert help_kb.kb_hash_of(help_kb._read_files(folder)) == kb_hash
    assert help_kb.knows(APP, kb_hash)


def test_the_hash_recipe_on_a_fixture_small_enough_to_check_by_hand():
    """path TAB sha256(bytes) NEWLINE per file, sorted by path bytes, then
    sha256 of the lot. The expected value was computed with shasum."""
    import hashlib
    files = {"index.json": b"{}\n", "a.md": b"A\n", "es/a.md": b"\xc3\x81\n",
             "es/index.json": b"{}\n"}
    lines = b"".join(f"{p}\t{hashlib.sha256(files[p]).hexdigest()}\n".encode()
                     for p in ["a.md", "es/a.md", "es/index.json", "index.json"])
    assert help_kb.kb_hash_of(files) == hashlib.sha256(lines).hexdigest()
    # Order of insertion does not matter, content does.
    assert help_kb.kb_hash_of(dict(reversed(list(files.items())))) == help_kb.kb_hash_of(files)
    assert help_kb.kb_hash_of({**files, "a.md": b"B\n"}) != help_kb.kb_hash_of(files)


def test_the_fixture_ss_and_gp_both_pin():
    """SS Dev's Swift tests pin the same four files and the same value
    (2026-11-27). If this changes, the two sides no longer agree on what a
    Help set is called."""
    files = {"index.json": b'{"articles":[]}\n', "a.md": b"Hello\n", "es/a.md": b"Hola\n",
             "whats-new.json": b"{}\n"}
    assert len(files["index.json"]) == 16
    counted = {p: d for p, d in files.items() if help_kb.is_kb_file(p)}
    assert sorted(counted) == ["a.md", "es/a.md", "index.json"]
    assert help_kb.kb_hash_of(counted) == (
        "0c080eb9e6f9ff33157e51f223c3f85cc7895a12b43e9830fbb2b0cb86ca0ad3")


def test_whats_new_is_not_part_of_the_set():
    assert help_kb.is_kb_file("index.json") and help_kb.is_kb_file("ja/am-import.md")
    assert not help_kb.is_kb_file("whats-new.json")
    assert not help_kb.is_kb_file("es/whats-new.json")


def test_an_edited_folder_is_refused(tmp_path, monkeypatch):
    """Sabotage in a test: one byte changed in one article and the set is
    no longer the build's. It must stop being served, not be served wrong."""
    dest = tmp_path / APP / KB_2702
    shutil.copytree(help_kb.KB_ROOT / APP / KB_2702, dest)
    monkeypatch.setattr(help_kb, "KB_ROOT", tmp_path)
    help_kb._verified_folder.cache_clear()
    help_kb._load.cache_clear()
    try:
        assert help_kb.knows(APP, KB_2702)
        help_kb._verified_folder.cache_clear()
        with open(dest / "am-import.md", "ab") as fh:
            fh.write(b" ")
        assert not help_kb.knows(APP, KB_2702)
        assert help_kb.articles(APP, KB_2702, "en") is None
    finally:
        help_kb._verified_folder.cache_clear()
        help_kb._load.cache_clear()


def test_every_language_has_the_same_ids_and_its_own_text():
    en, lang = help_kb.articles(APP, KB_2702, "en")
    assert lang == "en" and len(en) == 75
    ids = [a["id"] for a in en]
    for locale, want in [("es", "es"), ("es-MX", "es"), ("fr_CA", "fr"), ("ja", "ja"),
                         ("pt-BR", "pt-BR"), ("pt", "pt-BR")]:
        arts, lang = help_kb.articles(APP, KB_2702, locale)
        assert lang == want, locale
        assert [a["id"] for a in arts] == ids, locale
        same = sum(a["body"] == e["body"] for a, e in zip(arts, en))
        assert same == 0, f"{locale}: {same} articles were served in English"
    # A language the app has no Help for is answered from the English set.
    assert help_kb.articles(APP, KB_2702, "de")[1] == "en"
    assert help_kb.articles(APP, KB_2702, None)[1] == "en"


def test_a_set_is_only_known_for_the_app_it_was_imported_for():
    assert not help_kb.knows("n400", KB_2702)
    assert not help_kb.knows(APP, "0" * 64)
    assert not help_kb.knows("../shouldersurf", KB_2702)
    assert not help_kb.knows(APP, "../" + KB_2702[3:])


# --- the route ---------------------------------------------------------------


def test_gp_finds_the_article_the_keyword_ranker_missed(client, mock_provider, tmp_db_path):
    """The real first question on 1.20. The pick names the action items
    article, and the answer call is given ITS text, which the app's top
    three never included."""
    mock_provider.side_effect = [
        _pick(["am-action-items-and-reminders", "am-reports"]),
        _reply("Open the meeting's report and look under Action Required.", True,
               ["am-action-items-and-reminders"], related=["am-reports", "am-meeting-page"]),
    ]
    body = _kb_body()
    r = _post(client, body)
    assert r.status_code == 200, r.text
    assert r.json() == {
        "answer": "Open the meeting's report and look under Action Required.",
        "covered": True,
        "used_article_ids": ["am-action-items-and-reminders"],
        "related_article_ids": ["am-reports", "am-meeting-page"],
        "source": "kb",
        "output_language": None,
        "kb_hash": KB_2702,
    }
    assert mock_provider.call_count == 2
    pick, answer = _sent(mock_provider, 0), _sent(mock_provider, 1)
    arts = {a["id"]: a for a in help_kb.articles(APP, KB_2702, "en")[0]}
    owed = arts["am-action-items-and-reminders"]["body"]
    # The pick sees every title and summary and no article text.
    assert pick.system_prompt == help_ask.PICK_SYSTEM_PROMPT
    assert pick.user_content.count("<entry id=") == 75
    assert owed[:200] not in pick.user_content
    assert pick.tool_choice == {"type": "tool", "name": "help_pick"}
    # The answer call reads the two picked articles in full, and only those.
    assert answer.system_prompt == help_ask.SYSTEM_PROMPT
    assert answer.user_content.count("<article id=") == 2
    assert owed in answer.user_content
    assert arts["am-reports"]["body"] in answer.user_content
    assert arts["am-import"]["body"] not in answer.user_content
    assert answer.user_content.count("<entry id=") == 75
    # One row for the question: what GP picked, both calls' tokens and cost.
    (row,) = _rows(tmp_db_path, body["device_id"])
    assert json.loads(row["offered_article_ids"]) == ["am-action-items-and-reminders",
                                                      "am-reports"]
    assert row["outcome"] == "answered" and row["cost_final"] == 1
    assert row["input_tokens"] == 5000 + 1500 and row["output_tokens"] == 30 + 90
    assert row["cost_usd"] == pytest.approx(2 * MODEL_COST)


def test_the_articles_are_read_in_the_apps_language(client, mock_provider):
    mock_provider.side_effect = [_pick(["am-import"]), _reply("Sim.", True, ["am-import"])]
    r = _post(client, _kb_body(app_locale="pt-BR", question="como importo uma gravação?"))
    assert r.status_code == 200, r.text
    pt = {a["id"]: a for a in help_kb.articles(APP, KB_2702, "pt-BR")[0]}["am-import"]
    en = {a["id"]: a for a in help_kb.articles(APP, KB_2702, "en")[0]}["am-import"]
    answer = _sent(mock_provider, 1)
    assert pt["body"] in answer.user_content and en["body"] not in answer.user_content
    assert pt["title"] in _sent(mock_provider, 0).user_content


def test_a_set_gp_does_not_have_is_a_409_before_anything_is_spent(client, mock_provider,
                                                                 tmp_db_path):
    body = _kb_body(kb_hash="ab" * 32)
    r = _post(client, body)
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "kb_unknown"
    assert r.json()["detail"]["details"] == {"kb_hash": "ab" * 32}
    assert mock_provider.call_count == 0
    assert _rows(tmp_db_path, body["device_id"]) == []
    # No rate-limit slot was taken: the device's whole hourly allowance is
    # still there for the re-send.
    _dial(client, per_device_per_hour=1)
    mock_provider.return_value = _reply()
    assert _post(client, _body(device_id=body["device_id"])).status_code == 200


def test_an_unknown_set_with_articles_is_answered_the_old_way(client, mock_provider):
    mock_provider.return_value = _reply()
    r = _post(client, _body(kb_hash="ab" * 32))
    assert r.status_code == 200, r.text
    assert r.json()["source"] == "articles" and "kb_hash" not in r.json()
    assert mock_provider.call_count == 1
    assert _sent(mock_provider, 0).system_prompt == help_ask.SYSTEM_PROMPT


def test_a_known_set_wins_over_articles_the_client_also_sent(client, mock_provider):
    mock_provider.side_effect = [_pick(["am-import"]), _reply("x", True, ["am-import"])]
    r = _post(client, _body(kb_hash=KB_2702, app_locale="en"))
    assert r.status_code == 200 and r.json()["source"] == "kb"
    answer = _sent(mock_provider, 1)
    assert _articles()[0]["body"] not in answer.user_content


def test_neither_articles_nor_a_kb_hash_is_a_400(client, mock_provider):
    body = _kb_body()
    del body["kb_hash"]
    r = _post(client, body)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "invalid_request"
    assert mock_provider.call_count == 0


@pytest.mark.parametrize("bad", ["abc", "AB" * 32, "g" * 64, 12, ["a"]])
def test_a_malformed_kb_hash_is_a_400_that_names_the_field(client, mock_provider, bad):
    r = _post(client, _kb_body(kb_hash=bad))
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["details"]["field"] == "kb_hash"
    assert mock_provider.call_count == 0


def test_ids_the_pick_invented_are_dropped_and_five_is_the_most(client, mock_provider,
                                                               tmp_db_path):
    real = ["am-import", "am-reports", "am-meeting-page", "am-meeting-chat",
            "am-review-tab", "am-fix-a-word"]
    mock_provider.side_effect = [_pick(["not-an-article", real[0], real[0], *real[1:]]),
                                 _reply("x", True, [real[0]])]
    body = _kb_body()
    assert _post(client, body).status_code == 200
    assert _sent(mock_provider, 1).user_content.count("<article id=") == 5
    (row,) = _rows(tmp_db_path, body["device_id"])
    assert json.loads(row["offered_article_ids"]) == real[:5]


@pytest.mark.parametrize("pick", [
    _pick([]),
    ChatResponse(text="no idea", input_tokens=10, output_tokens=5, model="m",
                 provider="anthropic", usage={"input_tokens": 10, "output_tokens": 5},
                 raw_response_json=json.dumps({"content": [{"type": "text", "text": "x"}]})),
])
def test_no_pick_still_gets_an_answer_from_the_catalog_alone(client, mock_provider, pick):
    """An empty or unreadable pick is not a failure: the answer call still
    sees the catalog and can say Help has no answer, or point at an article."""
    mock_provider.side_effect = [pick, _reply("Help does not have an answer.", False, [],
                                              related=["am-import"])]
    r = _post(client, _kb_body(question="what is the capital of France"))
    assert r.status_code == 200, r.text
    assert r.json()["covered"] is False and r.json()["related_article_ids"] == ["am-import"]
    answer = _sent(mock_provider, 1)
    assert answer.user_content.count("<article id=") == 0
    assert answer.user_content.count("<entry id=") == 75


def test_a_pick_that_fails_is_a_503_and_keeps_the_reservation(client, mock_provider,
                                                              tmp_db_path):
    mock_provider.side_effect = [RuntimeError("connection dropped")]
    body = _kb_body()
    r = _post(client, body)
    assert r.status_code == 503 and r.json()["detail"]["details"]["reason"] == "model_error"
    (row,) = _rows(tmp_db_path, body["device_id"])
    assert row["outcome"] == "model_error" and row["cost_final"] == 0 and row["cost_usd"] > 0


def test_an_answer_that_fails_after_a_paid_pick_keeps_the_reservation(client, mock_provider,
                                                                     tmp_db_path):
    """The pick was billed, so the question did not cost zero even though
    the provider answered the second call with an error."""
    from fastapi import HTTPException
    mock_provider.side_effect = [_pick(["am-import"]),
                                 HTTPException(status_code=502, detail="upstream")]
    body = _kb_body()
    assert _post(client, body).status_code == 503
    (row,) = _rows(tmp_db_path, body["device_id"])
    assert row["cost_final"] == 0 and row["cost_usd"] > 0


def test_the_reservation_covers_both_calls_with_the_longest_articles(client, mock_provider,
                                                                     tmp_db_path):
    """Reserved before the pick, so it has to assume the five longest
    articles. Seen by making the model fail and reading what was held."""
    mock_provider.side_effect = [RuntimeError("x")]
    body = _kb_body(app_locale="fr")
    _post(client, body)
    (row,) = _rows(tmp_db_path, body["device_id"])
    arts = help_kb.articles(APP, KB_2702, "fr")[0]
    five = sorted((len(a["body"].encode()) for a in arts), reverse=True)[:5]
    prices = help_ask.token_prices(client.app.state.pricing, client.app.state.remote_configs,
                                   "anthropic", "claude-haiku-5-5")
    assert row["cost_usd"] > sum(five) * prices[0]


def test_the_old_request_is_untouched_by_all_of_this(client, mock_provider):
    mock_provider.return_value = _reply()
    r = _post(client, _body())
    assert r.status_code == 200 and r.json()["source"] == "articles"
    assert "kb_hash" not in r.json() and mock_provider.call_count == 1


# --- a build from before kb_hash ---------------------------------------------


def test_build_2702_is_answered_from_gps_copy_with_the_response_it_always_had(
        client, mock_provider, tmp_db_path):
    """1.20 (2702) sends articles and a catalog and no kb_hash. GP knows
    which Help set is inside it, so it finds the articles itself, and the
    response has no field that build has not seen before."""
    mock_provider.side_effect = [_pick(["am-action-items-and-reminders"]),
                                 _reply("Look under Action Required.", True,
                                        ["am-action-items-and-reminders"])]
    body = _body(app_build="2702", app_locale="en",
                 question="how do i see who owes something after a meeting")
    r = _post(client, body)
    assert r.status_code == 200, r.text
    assert r.json() == {"answer": "Look under Action Required.", "covered": True,
                        "used_article_ids": ["am-action-items-and-reminders"],
                        "related_article_ids": [], "source": "articles",
                        "output_language": None}
    assert mock_provider.call_count == 2
    arts = {a["id"]: a for a in help_kb.articles(APP, KB_2702, "en")[0]}
    answer = _sent(mock_provider, 1)
    assert arts["am-action-items-and-reminders"]["body"] in answer.user_content
    assert _articles()[0]["body"] not in answer.user_content


def test_a_build_gp_has_not_checked_is_answered_from_what_it_sent(client, mock_provider):
    mock_provider.return_value = _reply()
    r = _post(client, _body(app_build="2701"))
    assert r.status_code == 200 and mock_provider.call_count == 1
    assert _articles()[1]["body"] in _sent(mock_provider, 0).user_content


def test_every_listed_build_names_a_help_set_gp_holds():
    builds = help_kb._builds(APP)
    assert builds.get("2702") == KB_2702
    for build, kb_hash in builds.items():
        assert help_kb.knows(APP, kb_hash), build
