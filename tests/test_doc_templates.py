import pytest


class TestNegatedAsksDoNotMatch:
    """Live 2026-08-16. Scott answered the version question with "I
    don't want a project plan. I'm looking for a Test plan document."
    and got the Gantt question served straight back at him.

    Substring matching cannot tell a request from a refusal, and a
    refusal is exactly what a person types when we have just guessed
    wrong. A correction that re-triggers the thing being corrected is
    the worst possible moment to be literal about keywords.
    """

    @pytest.mark.parametrize("text", [
        "I don't want a project plan. I'm looking for a Test plan document.",
        "I do not want a project plan, I want a test plan",
        "not a project plan, a test plan please",
        "a test plan instead of a project plan",
    ])
    def test_a_refused_plan_is_not_an_ambiguous_plan_ask(self, text):
        from app.services.doc_templates import ambiguous_plan_ask
        assert ambiguous_plan_ask(text) is False

    @pytest.mark.parametrize("text", [
        "make me a project plan",
        "create a project plan from the meeting",
        # The refusal is of something ELSE; the plan ask is real.
        "a project plan, not a test plan",
        # Mentioned in passing but still genuinely requested later.
        "Before you create a project plan from the discussion, do research",
    ])
    def test_a_genuine_plan_ask_still_matches(self, text):
        from app.services.doc_templates import ambiguous_plan_ask
        assert ambiguous_plan_ask(text) is True

    def test_the_template_registry_respects_refusals_too(self):
        """Same substring problem, same fix: `match_template` scans
        assembled history deliberately, so a refusal in the reply must
        not re-arm the template it refused."""
        from app.services.doc_templates import match_template
        assert match_template("I don't want a gantt chart, just a list") is None
        assert match_template("make me a gantt chart") == "gantt_smartsheet"

    def test_negation_only_reaches_backwards_and_not_far(self):
        """"not" earlier in a long sentence must not silently disarm a
        genuine ask much later in it."""
        from app.services.doc_templates import ambiguous_plan_ask
        far = ("I do not need the transcript cleaned up or the summary "
               "rewritten or anything like that at all, what I want is a "
               "project plan")
        assert ambiguous_plan_ask(far) is True
