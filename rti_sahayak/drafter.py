"""Turn a plain-language need into a ready-to-send RTI application.

Two drafters share one output format (`Draft`):

* `draft_with_ai`  - an LLM (Gemini or Claude) writes the draft as structured output,
  then the rule checker reviews it and the model gets one chance to fix anything
  flagged (generate -> verify -> repair).
* `draft_offline`  - a simple rule-based drafter. No API key needed; it is the
  baseline the AI drafter is measured against in `evals/`.

`render_letter` and `render_first_appeal` turn a Draft into the final text.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from . import checker, rules

DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"
# Tried in order when the main model is busy or too slow (free-tier 503/504/429).
DEFAULT_GEMINI_FALLBACKS = ("gemini-3.8-flash", "gemini-3.5-flash-lite")
# The model that last answered, remembered for 30 minutes so a busy model isn't retried on every request.
_gemini_last_good: tuple[str, float] | None = None
DEFAULT_CLAUDE_MODEL = "claude-opus-5-5"


class Draft(BaseModel):
    """What goes into an RTI application. This is the schema the AI fills in."""

    public_authority: str = Field(
        description="The government office that most likely holds the records, e.g. "
        "'Office of the Executive Engineer, Municipal Corporation, Bilaspur'. Use the user's "
        "own words for place names. Never invent a street address."
    )
    authority_is_guess: bool = Field(description="True if you inferred the office rather than the user naming it.")
    subject: str = Field(description="Short subject line, under 15 words, no 'RTI application' prefix.")
    points: list[str] = Field(
        description="1 to 8 numbered requests. Each asks for a specific record that already exists "
        "(copies of file notings, orders, registers, reports, status with dates), never 'why', "
        "never opinions, never action."
    )
    period: str = Field(
        description="Time period the records cover, e.g. '1 April 2024 to 31 March 2025' or '1 January 2023 to date', or '' if not needed."
    )
    life_or_liberty: bool = Field(
        description="True only if the information genuinely concerns someone's life or liberty."
    )
    language: Literal["en", "hi"] = Field(
        description="'hi' if the user wrote in Hindi (Devanagari or Hinglish asking for Hindi), else 'en'."
    )
    notes_for_user: list[str] = Field(
        description="Short, plain-language notes for the applicant: what you changed and why, and anything "
        "they must fill in. Max 4."
    )


@dataclass
class Applicant:
    name: str = ""
    address: str = ""
    email: str = ""
    phone: str = ""
    fee_mode: str = "Indian Postal Order"  # or Demand Draft, Online, Cash, BPL
    place: str = ""


SYSTEM_PROMPT = f"""You help Indian citizens write applications under the {rules.ACT}.
The reader is often a first-time applicant, maybe a student or someone with little legal knowledge.
Your job is to turn what they want to know into a draft that a Public Information Officer must answer.

How RTI works (follow this strictly):
- RTI gives copies of records that already exist (s.2(f)): files, file notings, orders, registers,
  reports, emails, contracts, bills, measurement books, attendance records, status with dates.
- A PIO does not have to explain, justify, give opinions, predict, or take action
  (CBSE v. Aditya Bandopadhyay, 2011). So never write "why", "explain", "when will", "take action".
  Rewrite them as requests for the records that would show the answer, e.g.
  "Why was my road not repaired?" -> "Copies of the work order, measurement book entries and
  completion certificate for the repair of <road>, and the file notings on its status."
- Grievances ("fix my problem", "refund me") become requests for the status of the complaint,
  the officer handling it with dates, the action-taken report, and the rule/timeline that applies.
- Private companies, private banks, private schools and private hospitals are not covered (s.2(h)).
  Address the RTI to the regulator that holds information about them (RBI, TRAI, state education
  department, state health department, RERA, Registrar of Companies) and say so in notes_for_user.
- Don't ask for other people's personal information (tax returns, medical, bank details)
  unless the user explains a public interest (s.8(1)(j)).
- Keep it focused: 1-8 points, one topic, a time period when it helps, under {rules.WORD_LIMIT} words.
- Life or liberty (detention, custody, threat to life) gets a 48-hour deadline: set life_or_liberty
  only when that is genuinely the case.
- Write the points in the user's language (Hindi or English), in simple formal language.
- Today's date is given in <today>. Read relative dates against it: "in March" or "last year" mean the
  most recent one before today, never a year you assume.
- Do not invent facts, names, file numbers or addresses. Use placeholders like <ward number> for
  things the user must fill in, and mention them in notes_for_user.
"""


class DraftError(RuntimeError):
    """The AI drafter could not produce a draft (refusal, API error, bad output)."""


# Checks that apply to the content of the requests themselves (the rest depend on
# applicant details that `render_letter` adds).
_CONTENT_CODES = {"WHY_QUESTION", "HYPOTHETICAL", "GRIEVANCE", "THIRD_PARTY", "VAGUE", "TOO_MANY_POINTS"}


def content_issues(d: Draft) -> list[checker.Issue]:
    """Run the checker on just the requests (and the authority, for the private-body check)."""
    body = "\n".join(f"{i}. {p}" for i, p in enumerate(d.points, 1))
    if d.period:
        body += f"\nPeriod: {d.period}"
    report = checker.check(body)
    issues = [i for i in report.issues if i.code in _CONTENT_CODES]
    if not d.points:
        issues.append(checker.Issue("NO_POINTS", checker.ERROR, "No requests", "", "", ""))
    priv = checker.check(d.public_authority)
    issues += [i for i in priv.issues if i.code == "PRIVATE_BODY"]
    return issues


class _ClaudeBackend:
    """Claude via the Anthropic SDK, with structured output and server-side refusal fallback."""

    name = "Claude"

    def __init__(self, client=None, model: str | None = None):
        import anthropic

        self._sdk = anthropic
        self.client = client or anthropic.Anthropic()
        self.model = model or os.environ.get("RTI_CLAUDE_MODEL", DEFAULT_CLAUDE_MODEL)
        self.messages: list[dict] = []

    def ask(self, user_text: str) -> Draft:
        a = self._sdk
        self.messages.append({"role": "user", "content": user_text})
        try:
            resp = self.client.beta.messages.parse(
                model=self.model,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                messages=list(self.messages),
                output_format=Draft,
                output_config={"effort": "medium"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except a.AuthenticationError as e:
            raise DraftError("The API key was rejected. Check ANTHROPIC_API_KEY.") from e
        except a.RateLimitError as e:
            raise DraftError("Too many requests right now. Try again in a minute.") from e
        except a.APIStatusError as e:
            raise DraftError(f"The AI service returned an error ({e.status_code}).") from e
        except a.APIConnectionError as e:
            raise DraftError("Couldn't reach the AI service.") from e
        if resp.stop_reason == "refusal":
            raise DraftError("The AI declined this request.")
        if resp.parsed_output is None:
            raise DraftError("The AI returned something that wasn't a valid draft.")
        self.messages.append({"role": "assistant", "content": resp.content})
        return resp.parsed_output


class _GeminiBackend:
    """Gemini via the google-genai SDK, with a JSON response schema built from `Draft`."""

    name = "Gemini"

    def __init__(self, client=None, model: str | None = None):
        from google import genai
        from google.genai import errors, types

        self._types, self._errors = types, errors
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        # Free-tier Gemini is often busy (503) or slow (504). Retry once, give each request a deadline,
        # and if the model still can't answer, move on to the next model in the list.
        self.client = client or genai.Client(
            api_key=key,
            http_options=types.HttpOptions(
                timeout=45_000,  # ms
                retry_options=types.HttpRetryOptions(
                    attempts=2, initial_delay=2.0, max_delay=10.0, http_status_codes=[429, 500, 502, 503]
                ),
            ),
        )
        self.model = model or os.environ.get("RTI_GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
        env_fb = os.environ.get("RTI_GEMINI_FALLBACKS")
        fallbacks = [m.strip() for m in env_fb.split(",")] if env_fb is not None else list(DEFAULT_GEMINI_FALLBACKS)
        self.models = [self.model] + [m for m in fallbacks if m and m != self.model]
        good = _gemini_last_good
        if client is None and good and good[0] in self.models and time.monotonic() - good[1] < 1800:
            self.models = [good[0]] + [m for m in self.models if m != good[0]]
        self._remember = client is None
        self.contents: list = []

    def _call(self, model: str):
        t = self._types
        return self.client.models.generate_content(
            model=model,
            contents=list(self.contents),
            config=t.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                response_schema=Draft,
                # Filling a small form doesn't need long thinking, and it makes replies much faster.
                thinking_config=t.ThinkingConfig(thinking_level=t.ThinkingLevel.LOW),
            ),
        )

    def ask(self, user_text: str) -> Draft:
        t = self._types
        self.contents.append(t.Content(role="user", parts=[t.Part(text=user_text)]))
        last: Exception | None = None
        for model in list(self.models):
            try:
                resp = self._call(model)
            except self._errors.APIError as e:
                if e.code in (400, 401, 403) and "key" in str(e).lower():
                    raise DraftError("The API key was rejected. Check GEMINI_API_KEY.") from e
                if e.code in (404, 429, 500, 502, 503, 504):
                    last = e  # not found, busy or too slow: try the next model
                    continue
                raise DraftError(_api_error_text(e)) from e
            except Exception as e:  # network errors and client-side timeouts surface as httpx exceptions
                last = e
                continue
            self.model = model
            if self._remember:
                global _gemini_last_good
                _gemini_last_good = (model, time.monotonic())
            self.models = [model] + [m for m in self.models if m != model]  # stick with the one that works
            break
        else:
            if isinstance(last, self._errors.APIError):
                if last.code == 429:
                    raise DraftError(
                        "Too many requests right now (or the free quota is used up). Try again later."
                    ) from last
                if last.code == 404 and len(self.models) == 1:
                    raise DraftError(
                        f"Gemini model '{self.model}' was not found. Set RTI_GEMINI_MODEL to a current model."
                    ) from last
                raise DraftError(_api_error_text(last)) from last
            raise DraftError("Couldn't reach the AI service.") from last
        draft = resp.parsed if isinstance(resp.parsed, Draft) else None
        if draft is None:
            try:
                draft = Draft.model_validate_json(resp.text or "")
            except Exception as e:
                raise DraftError(
                    "The AI returned something that wasn't a valid draft (it may have been blocked)."
                ) from e
        self.contents.append(t.Content(role="model", parts=[t.Part(text=resp.text or draft.model_dump_json())]))
        return draft


def _api_error_text(e) -> str:
    detail = " ".join(str(getattr(e, "message", "") or getattr(e, "status", "") or "").split())[:160]
    return f"The AI service returned an error ({e.code}{': ' + detail if detail else ''})."


def ai_provider() -> str | None:
    """Which AI is configured: 'gemini' if GEMINI_API_KEY/GOOGLE_API_KEY is set, else 'claude' if
    ANTHROPIC_API_KEY is set, else None. RTI_AI_PROVIDER overrides the choice."""
    forced = os.environ.get("RTI_AI_PROVIDER", "").strip().lower()
    if forced in ("gemini", "claude"):
        return forced
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return "gemini"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "claude"
    return None


_BACKENDS = {"gemini": _GeminiBackend, "claude": _ClaudeBackend}


def draft_with_ai(
    need: str,
    *,
    authority_hint: str = "",
    provider: str | None = None,
    client=None,
    model: str | None = None,
    max_repairs: int = 1,
) -> tuple[Draft, list[str]]:
    """Ask the AI for a Draft, then let the rule checker review it and request fixes.

    `provider` is "gemini" or "claude" (default: whichever key is set, see `ai_provider`).
    Returns (draft, log) where log is a list of human-readable steps for the UI.
    Raises DraftError if no usable draft could be produced.
    """
    if not need or not need.strip():
        raise DraftError("Tell me what information you want first.")
    provider = provider or ai_provider()
    if provider not in _BACKENDS:
        raise DraftError("No AI key set. Add GEMINI_API_KEY (or ANTHROPIC_API_KEY).")
    ai = _BACKENDS[provider](client=client, model=model)

    user = (
        f"<today>{date.today().strftime('%d %B %Y')}</today>\n"
        f"<what_i_want_to_know>\n{need.strip()}\n</what_i_want_to_know>"
    )
    if authority_hint.strip():
        user += f"\n<office_i_think_holds_it>{authority_hint.strip()}</office_i_think_holds_it>"
    log: list[str] = []

    for attempt in range(max_repairs + 1):
        draft = ai.ask(user)
        problems = content_issues(draft)
        if attempt == 0:
            log.append(f"{ai.name} ({ai.model}) wrote a draft with {len(draft.points)} point(s).")
        if not problems:
            log.append(
                "The rule checker found no problems in the requests."
                if attempt == 0
                else f"{ai.name} fixed everything the checker flagged."
            )
            return draft, log
        log.append("Checker flagged: " + ", ".join(p.title for p in problems) + ".")
        if attempt == max_repairs:
            log.append("Kept the draft; see the checker results below.")
            return draft, log
        feedback = "\n".join(
            f"- {p.title}: {p.why} Fix: {p.fix}" + (f' (in: "{p.quote}")' if p.quote else "") for p in problems
        )
        user = f"A rule checker reviewed your draft and found these problems:\n{feedback}\nReturn the corrected draft."
    raise AssertionError("unreachable")


# ------------------------------------------------------------------ offline drafter

_REQUEST_START = re.compile(
    r"^(why|what|when|where|who|which|how|whether|is|are|was|were|did|does|do|has|have|can|will|"
    r"i\s+want\s+to\s+know|i\s+would\s+like\s+to\s+know|please\s+(tell|give|provide|share)|"
    r"tell\s+me|give\s+me|provide|kindly)\b",
    re.I,
)


def _to_record_request(s: str) -> str:
    """Wrap one question as a request for records. Quotes the user's own words
    rather than rewriting them, so the offline drafter never produces broken grammar."""
    s = s.strip()
    s = re.sub(
        r"^(i\s+want\s+to\s+know|i\s+would\s+like\s+to\s+know|please\s+tell\s+me|tell\s+me|"
        r"kindly\s+tell\s+me)\s*(that|about|if)?\s*",
        "",
        s,
        flags=re.I,
    )
    s = s[:1].upper() + s[1:]
    q = f'"{s}"'
    low = s.lower()
    if re.match(r"^(why|for\s+what\s+reason)\b", low):
        return f"Copies of the file notings, orders and correspondence that record the reasons, in the matter of: {q}"
    if re.match(r"^when\s+will\b", low):
        return (
            "The present status, with copies of the file notings recording any target date or timeline, "
            f"in the matter of: {q}"
        )
    if re.search(r"\b(status|pending|progress)\b", low):
        return (
            "The present status, the dates of each action taken, and the name and designation of the officer "
            f"with whom it is pending, in the matter of: {q}"
        )
    if re.match(r"^(how\s+much|what\s+(was|is)\s+the\s+(amount|cost|budget))", low):
        return f"Copies of the sanction orders, bills and payment records relating to: {q}"
    if re.match(r"^(who|which\s+officer)", low):
        return f"The name and designation of the officer(s) responsible, as recorded in the file, relating to: {q}"
    return f"Copies of the records relating to: {q}"


def _to_record_request_hi(s: str) -> str:
    q = f'"{s.strip()}"'
    if re.search(r"\u0915\u094d\u092f\u094b\u0902", s):  # kyon (why)
        return f"निम्नलिखित विषय में कारण दर्ज करने वाली फ़ाइल नोटिंग, आदेशों और पत्राचार की प्रतियाँ: {q}"
    if re.search(r"\u0915\u092c", s):  # kab (when)
        return f"निम्नलिखित विषय की वर्तमान स्थिति, तथा निर्धारित समय-सीमा दर्ज करने वाली फ़ाइल नोटिंग की प्रतियाँ: {q}"
    if re.search(r"\u0915\u093f\u0924\u0928", s):  # kitna (how much)
        return f"निम्नलिखित विषय से संबंधित स्वीकृति आदेशों, बिलों और भुगतान अभिलेखों की प्रतियाँ: {q}"
    return f"निम्नलिखित विषय से संबंधित अभिलेखों की प्रतियाँ: {q}"


def draft_offline(need: str, *, authority_hint: str = "") -> tuple[Draft, list[str]]:
    """Rule-based drafter (no AI). Simple and predictable; used as a baseline."""
    need = (need or "").strip()
    if not need:
        raise DraftError("Tell me what information you want first.")
    hindi = bool(re.search(r"[ऀ-ॿ]", need))
    sentences = [s.strip() for s in re.split(r"(?<=[.?!।])\s+|\n+", need) if s.strip()]
    notes = ["Made without AI: simple rewrites only. Read each point and edit it so it asks for specific records."]
    dropped = [s for s in sentences if checker._GRIEVANCE.search(s) or checker._THIRD_PARTY.search(s)]
    if dropped:
        notes.append(
            "Left out: " + "; ".join(f'"{d}"' for d in dropped) + ". RTI can't order action or give other "
            "people's personal details, so ask for the status and records of your case instead."
        )
    kept = [s for s in sentences if s not in dropped]
    asks = [s for s in kept if s.endswith("?") or _REQUEST_START.match(s)] or kept
    if not asks:
        raise DraftError(
            "Everything you wrote asks for action or someone's personal details, which RTI can't do. "
            "Try asking for the status or records of your case."
        )
    points = [_to_record_request_hi(s) if hindi else _to_record_request(s) for s in asks][:8]
    authority = authority_hint.strip()
    priv = checker.check(need + " " + authority)
    if "PRIVATE_BODY" in priv.codes:
        notes.append(
            "This looks like a private company, which RTI doesn't cover. Send it to the regulator instead: "
            + checker._REGULATOR_HINT
        )
    if not authority:
        authority = (
            "<उस सरकारी कार्यालय का नाम और पता जिसके पास ये अभिलेख हैं>"
            if hindi
            else "<name and address of the government office that holds these records>"
        )
        notes.append("Fill in the office name and address.")
    period = _period_from(need, hindi)
    return Draft(
        public_authority=authority,
        authority_is_guess=not authority_hint.strip(),
        subject=_subject_from(asks[0]),
        points=points,
        period=period,
        life_or_liberty=bool(re.search(r"\b(arrest|detain|custody|threat\s+to\s+(my\s+)?life)", need, re.I)),
        language="hi" if hindi else "en",
        notes_for_user=notes,
    ), ["Drafted offline with simple rules."]


def _period_from(need: str, hindi: bool) -> str:
    years = sorted(set(re.findall(r"\b(?:19|20)\d{2}\b", need)))
    if not years:
        return ""
    if len(years) > 1:
        return f"{years[0]} से {years[-1]}" if hindi else f"{years[0]} to {years[-1]}"
    if re.search(r"\bsince\b|\u0938\u0947", need, re.I):  # "since" / Hindi "se"
        return f"{years[0]} से अब तक" if hindi else f"{years[0]} to date"
    return years[0]


def _subject_from(s: str) -> str:
    words = s.strip().rstrip("?.!\u0964").split()
    short = " ".join(words[:10]) + ("…" if len(words) > 10 else "")
    return f'"{short}"'


# ------------------------------------------------------------------ rendering

_FEE_EN = {
    "Indian Postal Order": f"The application fee of Rs {rules.APPLICATION_FEE_RS} is paid by Indian Postal Order No. ________.",
    "Demand Draft": f"The application fee of Rs {rules.APPLICATION_FEE_RS} is paid by Demand Draft No. ________ in favour of the Accounts Officer.",
    "Online": f"The application fee of Rs {rules.APPLICATION_FEE_RS} has been paid online.",
    "Cash": f"The application fee of Rs {rules.APPLICATION_FEE_RS} has been paid in cash; receipt No. ________.",
    "BPL": "I belong to the Below Poverty Line (BPL) category. A self-attested copy of my BPL certificate is attached, "
    "so no fee is payable (Rule 5, RTI Rules 2012).",
}
_FEE_HI = {
    "Indian Postal Order": f"आवेदन शुल्क ₹{rules.APPLICATION_FEE_RS} भारतीय पोस्टल ऑर्डर क्रमांक ________ द्वारा जमा है।",
    "Demand Draft": f"आवेदन शुल्क ₹{rules.APPLICATION_FEE_RS} डिमांड ड्राफ्ट क्रमांक ________ (लेखा अधिकारी के नाम) द्वारा जमा है।",
    "Online": f"आवेदन शुल्क ₹{rules.APPLICATION_FEE_RS} ऑनलाइन जमा किया गया है।",
    "Cash": f"आवेदन शुल्क ₹{rules.APPLICATION_FEE_RS} नकद जमा किया गया है; रसीद क्रमांक ________।",
    "BPL": "मैं गरीबी रेखा से नीचे (BPL) श्रेणी से हूँ। BPL प्रमाणपत्र की स्वप्रमाणित प्रति संलग्न है, अतः शुल्क देय नहीं है।",
}


def _contact_lines(a: Applicant, hindi: bool) -> list[str]:
    labels = (
        ("नाम", "पता", "ईमेल", "फ़ोन", "स्थान", "दिनांक", "हस्ताक्षर")
        if hindi
        else ("Name", "Address", "Email", "Phone", "Place", "Date", "Signature")
    )
    lines = [
        f"{labels[0]}: {a.name or '________'}",
        f"{labels[1]}: {a.address or ('________ (पिन कोड सहित)' if hindi else '________ (with PIN code)')}",
    ]
    if a.email:
        lines.append(f"{labels[2]}: {a.email}")
    if a.phone:
        lines.append(f"{labels[3]}: {a.phone}")
    lines += [
        f"{labels[4]}: {a.place or '________'}",
        f"{labels[5]}: {date.today().strftime('%d-%m-%Y')}",
        f"{labels[6]}: ________",
    ]
    return lines


_PIO_PREFIX = re.compile(
    r"^\s*((the\s+)?office\s+of\s+)?(the\s+)?(central\s+|state\s+)?(public\s+information\s+officer|c?pio|s?pio)\b[\s,:-]*",
    re.I,
)
_PIO_PREFIX_HI = re.compile(r"^\s*(कार्यालय\s+)?(लोक|जन)\s+सूचना\s+अधिकारी[\s,:-]*")


def _clean_authority(name: str) -> str:
    """The letter already says 'The Public Information Officer,' so drop it if the office name repeats it."""
    cleaned = _PIO_PREFIX_HI.sub("", _PIO_PREFIX.sub("", name)).strip(" ,")
    return cleaned or name.strip()


def _clean_period(period: str) -> str:
    """Models often write '1 January 2023 to till date'; make it read '1 January 2023 to date'."""
    return re.sub(
        r"\bto\s+till\s+date\b|\btill\s+date\b|\bto\s+the\s+present\b|\bto\s+present\b",
        "to date",
        period.strip(),
        flags=re.I,
    )


def render_letter(d: Draft, a: Applicant | None = None) -> str:
    """The final application text, ready to print or paste into rtionline.gov.in."""
    a = a or Applicant()
    d = d.model_copy(
        update={
            "public_authority": _clean_authority(d.public_authority),
            "period": _clean_period(d.period),
            "points": [re.sub(r"\bto\s+till\s+date\b", "to date", p, flags=re.I) for p in d.points],
        }
    )
    points = "\n".join(f"{i}. {p}" for i, p in enumerate(d.points, 1))
    fee_key = a.fee_mode if a.fee_mode in _FEE_EN else "Indian Postal Order"
    if d.language == "hi":
        period = f" (अवधि: {d.period})" if d.period else ""
        parts = [
            "सेवा में,",
            "लोक सूचना अधिकारी,",
            d.public_authority,
            "",
            f"विषय: सूचना का अधिकार अधिनियम, 2005 की धारा 6(1) के अंतर्गत आवेदन – {d.subject}",
            "",
            "महोदय/महोदया,",
            f"कृपया निम्नलिखित सूचना प्रदान करें{period}:",
            points,
            "",
            "मैं भारत का/की नागरिक हूँ। " + _FEE_HI[fee_key],
        ]
        if d.life_or_liberty:
            parts.append("यह सूचना एक व्यक्ति के जीवन या स्वतंत्रता से संबंधित है; कृपया धारा 7(1) के परंतुक के अंतर्गत 48 घंटे में सूचना दें।")
        parts += [
            "यदि यह सूचना किसी अन्य लोक प्राधिकरण के पास है, तो कृपया धारा 6(3) के अंतर्गत 5 दिनों में आवेदन स्थानांतरित करें।",
            "कृपया सूचना नीचे दिए गए पते/ईमेल पर भेजें।",
            "",
            "आवेदक:",
            *_contact_lines(a, True),
        ]
    else:
        period = f" for the period {d.period}" if d.period else ""
        parts = [
            "To,",
            "The Public Information Officer,",
            d.public_authority,
            "",
            f"Subject: Application under Section 6(1) of the Right to Information Act, 2005 – {d.subject}",
            "",
            "Sir/Madam,",
            f"Please provide the following information{period}:",
            points,
            "",
            "I am a citizen of India. " + _FEE_EN[fee_key],
        ]
        if d.life_or_liberty:
            parts.append(
                "This information concerns the life or liberty of a person. Please provide it within 48 hours "
                "under the proviso to Section 7(1)."
            )
        parts += [
            "If any of this information is held by another public authority, please transfer the application to it "
            "within 5 days under Section 6(3) and inform me.",
            "Please send the information to the address/email below. I am willing to pay any additional fee as per rules.",
            "",
            "Applicant:",
            *_contact_lines(a, False),
        ]
    return "\n".join(parts)


GROUNDS = {
    "no_reply": "No reply was received within the time allowed under Section 7(1), which amounts to a deemed refusal "
    "under Section 7(2).",
    "incomplete": "The reply is incomplete: information on some points was not provided.",
    "wrong_denial": "Information was refused under Section 8/9 without explaining how the exemption applies, as "
    "required by Section 7(8).",
    "misleading": "The information provided is incorrect or misleading.",
    "fee": "Additional fee was demanded although the reply was late; under Section 7(6) the information should be "
    "free of cost.",
}


def render_first_appeal(
    *,
    applicant: Applicant,
    public_authority: str,
    rti_date: str,
    rti_ref: str = "",
    reply_date: str = "",
    grounds: list[str] | None = None,
    details: str = "",
) -> str:
    """A first appeal under Section 19(1), addressed to the First Appellate Authority."""
    grounds = grounds or (["no_reply"] if not reply_date else ["incomplete"])
    ground_lines = "\n".join(f"{i}. {GROUNDS[g]}" for i, g in enumerate(grounds, 1) if g in GROUNDS)
    if details.strip():
        ground_lines += f"\n{len(grounds) + 1}. {details.strip()}"
    reply = f"The PIO replied on {reply_date} (copy attached)." if reply_date else "No reply has been received to date."
    free = " free of cost under Section 7(6)" if (not reply_date or "fee" in grounds) else ""
    return "\n".join(
        [
            "To,",
            "The First Appellate Authority,",
            public_authority or "<office name and address>",
            "",
            "Subject: First Appeal under Section 19(1) of the Right to Information Act, 2005",
            "",
            "Sir/Madam,",
            f"I filed an application under the RTI Act on {rti_date}{f' (Ref./Reg. No. {rti_ref})' if rti_ref else ''} "
            f"with the Public Information Officer of your office (copy attached). {reply}",
            "",
            "Grounds of appeal:",
            ground_lines,
            "",
            "Prayer:",
            f"1. Direct the PIO to provide the complete information sought{free}, within 15 days.",
            "2. Give me an opportunity to be heard before deciding this appeal.",
            "",
            "Attachments: copy of the RTI application, proof of fee/posting, reply of the PIO (if any).",
            "",
            "Appellant:",
            *_contact_lines(applicant, False),
        ]
    )
