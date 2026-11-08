from types import SimpleNamespace

import pytest

from rti_sahayak import Applicant, Draft, check, draft_offline, draft_with_ai, render_letter
from rti_sahayak.drafter import DraftError, content_issues, render_first_appeal

APPLICANT = Applicant(
    name="Asha Verma", address="House No 5, Sarkanda, Bilaspur 495001", email="asha@example.com", place="Bilaspur"
)


def make_draft(points, **kw):
    base = dict(
        public_authority="Municipal Corporation, Bilaspur",
        authority_is_guess=False,
        subject="road repair",
        points=points,
        period="2024 to date",
        life_or_liberty=False,
        language="en",
        notes_for_user=[],
    )
    base.update(kw)
    return Draft(**base)


def test_offline_letter_passes_checker():
    d, _ = draft_offline(
        "Why has the road in ward 12 not been repaired since 2023? How much money was sanctioned?",
        authority_hint="Municipal Corporation, Bilaspur",
    )
    r = check(render_letter(d, APPLICANT))
    assert r.risk == "Low", r.codes
    assert d.period == "2023 to date"


def test_offline_needs_input():
    with pytest.raises(DraftError):
        draft_offline("  ")


def test_offline_flags_private_company():
    d, _ = draft_offline("HDFC Bank charged me a penalty. What is the rule?")
    assert any("regulator" in n for n in d.notes_for_user)


def test_offline_hindi():
    d, _ = draft_offline("सड़क का काम कब पूरा होगा?", authority_hint="ग्राम पंचायत, तखतपुर")
    assert d.language == "hi"
    letter = render_letter(d, Applicant(name="राम", address="ग्राम तखतपुर, पता: बिलासपुर 495001", fee_mode="BPL"))
    assert "लोक सूचना अधिकारी" in letter and "BPL" in letter
    assert check(letter).risk == "Low", check(letter).codes


def test_letter_without_details_is_flagged_for_contact():
    d = make_draft(["Copies of the work order for the road."])
    assert "NO_CONTACT" in check(render_letter(d)).codes


def test_life_liberty_line():
    d = make_draft(["Copy of the arrest memo."], life_or_liberty=True)
    assert "48 hours" in render_letter(d, APPLICANT)


def test_every_fee_mode_renders_and_passes():
    d = make_draft(["Copies of the work order for the road."])
    for mode in ["Indian Postal Order", "Demand Draft", "Online", "Cash", "BPL"]:
        a = Applicant(**{**APPLICANT.__dict__, "fee_mode": mode})
        assert check(render_letter(d, a)).risk == "Low", mode


def test_content_issues():
    assert content_issues(make_draft(["Copies of the work order."])) == []
    assert [i.code for i in content_issues(make_draft(["Why was the road not repaired?"]))] == ["WHY_QUESTION"]
    assert "PRIVATE_BODY" in [i.code for i in content_issues(make_draft(["x"], public_authority="ICICI Bank"))]


class FakeClient:
    """Stands in for anthropic.Anthropic(): returns queued drafts, records requests."""

    provider = "claude"

    def __init__(self, drafts, stop_reason="end_turn"):
        self.drafts = list(drafts)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))
        self.stop_reason = stop_reason

    def _parse(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(
            stop_reason=self.stop_reason, parsed_output=self.drafts.pop(0), content=[{"type": "text", "text": "{}"}]
        )


def test_ai_clean_draft_returns_first_try():
    client = FakeClient([make_draft(["Copies of the work order."])])
    d, log = draft_with_ai("road?", provider="claude", client=client)
    assert len(client.calls) == 1 and d.points == ["Copies of the work order."]
    assert client.calls[0]["output_format"] is Draft
    assert client.calls[0]["fallbacks"] == "default"


def test_ai_repair_loop_sends_checker_feedback():
    client = FakeClient([make_draft(["Why was the road not repaired?"]), make_draft(["Copies of the notings."])])
    d, log = draft_with_ai("road?", provider="claude", client=client)
    assert len(client.calls) == 2
    feedback = client.calls[1]["messages"][-1]["content"]
    assert client.calls[1]["messages"][-2]["role"] == "assistant"
    assert "rule checker" in feedback and "why" in feedback.lower()
    assert d.points == ["Copies of the notings."]
    assert "fixed" in log[-1]


def test_ai_gives_up_after_max_repairs():
    bad = make_draft(["Why was the road not repaired?"])
    client = FakeClient([bad, bad])
    d, log = draft_with_ai("road?", provider="claude", client=client, max_repairs=1)
    assert len(client.calls) == 2 and "Kept the draft" in log[-1]


def test_ai_refusal_raises():
    with pytest.raises(DraftError):
        draft_with_ai("road?", provider="claude", client=FakeClient([None], stop_reason="refusal"))


def test_first_appeal_no_reply():
    text = render_first_appeal(
        applicant=APPLICANT, public_authority="Municipal Corporation, Bilaspur", rti_date="01-09-2026"
    )
    assert "First Appellate Authority" in text and "Section 7(2)" in text and "free of cost" in text


def test_first_appeal_with_reply_and_grounds():
    text = render_first_appeal(
        applicant=APPLICANT,
        public_authority="X",
        rti_date="01-09-2026",
        reply_date="20-09-2026",
        grounds=["incomplete", "wrong_denial"],
        details="Point 2 ignored.",
    )
    assert "incomplete" in text and "Section 7(8)" in text and "3. Point 2 ignored." in text
    assert "free of cost" not in text


def test_offline_drops_demands_and_explains():
    d, _ = draft_offline("I applied for a scholarship in August 2025. Please release my money and punish the clerk.")
    assert not any("release" in p.lower() for p in d.points)
    assert any("Left out" in n for n in d.notes_for_user)


def test_offline_only_demands_raises():
    with pytest.raises(DraftError):
        draft_offline("Take strict action against the clerk.")


class FakeGemini:
    """Stands in for google.genai.Client(): client.models.generate_content(...)."""

    def __init__(self, outputs):
        self.outputs = list(outputs)  # Draft objects, or raw JSON strings
        self.calls = []
        self.models = SimpleNamespace(generate_content=self._gen)

    def _gen(self, **kw):
        self.calls.append(kw)
        out = self.outputs.pop(0)
        if isinstance(out, Draft):
            return SimpleNamespace(parsed=out, text=out.model_dump_json())
        return SimpleNamespace(parsed=None, text=out)


def test_gemini_backend_uses_schema_and_repairs():
    client = FakeGemini([make_draft(["Why was the road not repaired?"]), make_draft(["Copies of the notings."])])
    d, log = draft_with_ai("road?", provider="gemini", client=client)
    assert d.points == ["Copies of the notings."]
    first = client.calls[0]
    assert first["config"].response_schema is Draft and first["config"].response_mime_type == "application/json"
    roles = [c.role for c in client.calls[1]["contents"]]
    assert roles == ["user", "model", "user"]
    assert "rule checker" in client.calls[1]["contents"][-1].parts[0].text
    assert log[0].startswith("Gemini wrote")


def test_gemini_backend_parses_raw_json_and_rejects_garbage():
    good = make_draft(["Copies of the work order."]).model_dump_json()
    d, _ = draft_with_ai("road?", provider="gemini", client=FakeGemini([good]))
    assert d.points == ["Copies of the work order."]
    with pytest.raises(DraftError):
        draft_with_ai("road?", provider="gemini", client=FakeGemini(["not json"]))


def test_provider_picked_from_env(monkeypatch):
    from rti_sahayak.drafter import ai_provider

    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY", "RTI_AI_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    assert ai_provider() is None
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert ai_provider() == "claude"
    monkeypatch.setenv("GEMINI_API_KEY", "y")
    assert ai_provider() == "gemini"
    monkeypatch.setenv("RTI_AI_PROVIDER", "claude")
    assert ai_provider() == "claude"


def test_no_key_raises(monkeypatch):
    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY", "RTI_AI_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(DraftError):
        draft_with_ai("road?")
