"""Rule-based checker for RTI applications.

Each check looks for one mistake that commonly gets an RTI returned, refused or
answered badly. Checks are plain regex + logic so they are fast, free, testable
and explainable: every issue says what is wrong, how to fix it, and which rule
it comes from.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from . import rules

ERROR = "error"  # likely to get the application returned or refused
WARNING = "warning"  # likely to get a weak or "not information" reply
TIP = "tip"  # would make the application stronger

_SEVERITY_ORDER = {ERROR: 0, WARNING: 1, TIP: 2}


@dataclass
class Issue:
    code: str
    severity: str
    title: str
    why: str
    fix: str
    law: str
    quote: str = ""


@dataclass
class Report:
    issues: list[Issue] = field(default_factory=list)
    word_count: int = 0

    @property
    def risk(self) -> str:
        if any(i.severity == ERROR for i in self.issues):
            return "High"
        if any(i.severity == WARNING for i in self.issues):
            return "Medium"
        return "Low"

    @property
    def codes(self) -> list[str]:
        return [i.code for i in self.issues]

    def to_dict(self) -> dict:
        return {"risk": self.risk, "word_count": self.word_count, "issues": [asdict(i) for i in self.issues]}


# ---------------------------------------------------------------- helpers


def _sentences(text: str) -> list[str]:
    """Split into lines/sentences, keeping numbered points as their own items."""
    parts = re.split(r"(?<=[.?!])\s+|\n+", text)
    return [p.strip() for p in parts if p and p.strip()]


def _strip_point_number(s: str) -> str:
    return re.sub(r"^\s*(\(?\d+[.)]|\(?[a-z][.)]|[-*•])\s*", "", s, flags=re.I)


def _find(pattern: str, text: str) -> re.Match | None:
    return re.search(pattern, text, flags=re.I)


def _quote(s: str, limit: int = 90) -> str:
    s = " ".join(s.split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


# ---------------------------------------------------------------- checks


def _check_addressee(text: str) -> list[Issue]:
    if _find(
        r"\b(public\s+information\s+officer|c?\.?\s?p\.?\s?i\.?\s?o\b|s\.?p\.?i\.?o\b|"
        r"(jan|lok)\s*soochna\s*adhikari|(लोक|जन)\s*सूचना)",
        text,
    ):
        return []
    return [
        Issue(
            "NO_PIO",
            ERROR,
            "Not addressed to the Public Information Officer",
            "An RTI must go to the PIO (CPIO for central offices, SPIO/PIO for state offices) of a specific public authority. "
            "Letters addressed to 'Sir', a minister or the department in general are often treated as complaints, not RTIs.",
            "Start with: 'To, The Public Information Officer, <office name and address>'.",
            rules.REF["s6_1"],
        )
    ]


_PIN = re.compile(r"(?<!\d)[1-9]\d{2}\s?\d{3}(?!\d)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_ADDRESS_WORD = re.compile(
    r"\b(address|applicant|house\s*no|h\.?\s*no|flat|village|ward|district|tehsil|nagar|colony|road|street|marg|"
    r"sector|near|post|po|ps)\b|\u092a\u0924\u093e|\u0917\u094d\u0930\u093e\u092e|\u0906\u0935\u0947\u0926\u0915",  # pata, gram, aavedak
    re.I,
)
# A 6-digit number right after "IPO No." / "receipt" / "DD" is a payment reference, not a PIN code.
_NOT_PIN_CONTEXT = re.compile(r"\b(ipo|order|dd|draft|cheque|receipt|no|number|reg|\d+[a-z]?)\W{0,4}$", re.I)


def _has_postal_address(text: str) -> bool:
    """A PIN code on a line that looks like an address (or right after one), not a fee/receipt number."""
    lines = text.splitlines()
    for n, line in enumerate(lines):
        for m in _PIN.finditer(line):
            before = line[max(0, m.start() - 25) : m.start()]
            if _NOT_PIN_CONTEXT.search(before):
                continue
            context = line + " " + (lines[n - 1] if n else "")
            if _ADDRESS_WORD.search(context) or "," in line:
                return True
    return False


def _check_contact(text: str) -> list[Issue]:
    if _EMAIL.search(text) or _has_postal_address(text):
        return []
    return [
        Issue(
            "NO_CONTACT",
            ERROR,
            "No address to send the reply to",
            "The PIO needs a postal address (with PIN code) or an email to send the information. "
            "Without it the application can't be answered.",
            "Add your full postal address with PIN code, and an email or phone number, at the end.",
            rules.REF["s6_2"],
        )
    ]


def _check_fee(text: str) -> list[Issue]:
    if _find(r"\b(bpl|below\s+poverty\s+line)\b", text):
        proof = r"(certificate|card|copy|\u092a\u094d\u0930\u092e\u093e\u0923)"  # Hindi "praman" (certificate)
        if _find(
            rf"\b(bpl|below\s+poverty\s+line)\b.{{0,80}}{proof}|{proof}.{{0,40}}\b(bpl|below\s+poverty\s+line)\b", text
        ):
            return []
        return [
            Issue(
                "BPL_NO_PROOF",
                ERROR,
                "BPL fee waiver claimed without proof",
                "Applicants below the poverty line don't pay the fee, but only if a copy of the BPL certificate is attached.",
                "Add: 'I belong to the BPL category; a self-attested copy of my BPL certificate is attached.'",
                rules.REF["rule5"],
            )
        ]
    if _find(
        r"(₹|rs\.?|inr|rupees)\s*10\b|\b10\s*(/-|rupees|rs)|"
        r"postal\s+order|\bipo\b|demand\s+draft|\bd\.?d\.?\b|banker'?s\s+cheque|"
        r"fee\s+(has\s+been\s+|is\s+)?paid|paid\s+online|application\s+fee",
        text,
    ):
        return []
    return [
        Issue(
            "NO_FEE",
            ERROR,
            "Fee not mentioned",
            f"Central RTIs need a Rs {rules.APPLICATION_FEE_RS} fee (IPO, demand draft, cash with receipt, or online). "
            "Missing fee is one of the most common reasons RTIs are returned.",
            f"Add: 'The application fee of Rs {rules.APPLICATION_FEE_RS} is paid by Indian Postal Order No. ____.' "
            "(Filing on rtionline.gov.in? The fee is paid online, so just say so.)",
            rules.REF["rule3"],
        )
    ]


def _check_length(text: str, word_count: int) -> list[Issue]:
    if word_count <= rules.WORD_LIMIT:
        return []
    return [
        Issue(
            "TOO_LONG",
            WARNING,
            f"Longer than {rules.WORD_LIMIT} words ({word_count})",
            "The central rules ask that an application ordinarily stay within 500 words (excluding annexures). "
            "It can't be rejected only for this, but long, mixed requests are often answered partly or transferred in pieces.",
            "Cut background story; keep only the list of records you want. Split unrelated topics into separate RTIs.",
            rules.REF["rule3"],
        )
    ]


_WHY = re.compile(
    r"^(why\b|for\s+what\s+reason|what\s+(is|was|are|were)\s+the\s+reasons?\b|"
    r"(please\s+|kindly\s+)?(explain|justify|clarify)\b|"
    r"(is|was|are|were)\s+it\s+(legal|illegal|right|wrong|fair|justified|valid)\b|"
    r"how\s+can\s+(you|they|the\s+department)\b|"
    r"(kyun|kyon)\b|क्यों)",
    re.I,
)
_FUTURE = re.compile(
    r"^(when\s+will|will\s+(you|the|they|my)\b|what\s+will\b|what\s+action\s+will|"
    r"should\s+(i|we|the)\b|can\s+i\b|what\s+should|in\s+your\s+opinion|what\s+do\s+you\s+think|"
    r"what\s+would|if\s+.+\s+then\s+what)",
    re.I,
)


# Hindi puts the question word mid-sentence, so these are matched anywhere.
_WHY_HI = re.compile(r"\u0915\u094d\u092f\u094b\u0902|\u0915\u093f\u0938\s+\u0915\u093e\u0930\u0923")  # kyon, kis karan
_FUTURE_HI = re.compile(
    r"\u0915\u092c\s+(\u0924\u0915\s+)?\S*.*?(\u0917\u093e|\u0917\u0940|\u0917\u0947)[\s?\u0964]*$"  # kab (tak) ... ga/gi/ge
)


def _is_record_request(core: str) -> bool:
    """Points that already ask for records (and only quote a question) are fine."""
    return bool(
        re.match(
            r"^(copies|certified\s+copies|the\s+present\s+status|the\s+name\s+and\s+designation|"
            r"\u0928\u093f\u092e\u094d\u0928\u0932\u093f\u0916\u093f\u0924)",
            core,
            re.I,
        )
    )


def _check_questions(text: str) -> list[Issue]:
    issues: list[Issue] = []
    why_quotes, future_quotes = [], []
    for s in _sentences(text):
        core = _strip_point_number(s)
        if _is_record_request(core) or re.match(r"^(subject|sub|\u0935\u093f\u0937\u092f)\s*[:.-]", core, re.I):
            continue
        if _WHY.search(core) or _WHY_HI.search(core):
            why_quotes.append(core)
        elif _FUTURE.search(core) or _FUTURE_HI.search(core):
            future_quotes.append(core)
    if why_quotes:
        issues.append(
            Issue(
                "WHY_QUESTION",
                WARNING,
                "Asks 'why' or asks for an explanation",
                "RTI gives you records that already exist. A PIO doesn't have to explain, justify or give reasons that "
                "aren't written down, so 'why' questions usually get 'not information under s.2(f)'.",
                "Ask for the record instead: 'Provide copies of the file notings, orders and correspondence showing the "
                "reasons for <decision>.'",
                f"{rules.REF['s2f']}; {rules.REF['aditya']}",
                _quote(why_quotes[0]),
            )
        )
    if future_quotes:
        issues.append(
            Issue(
                "HYPOTHETICAL",
                WARNING,
                "Asks about the future or for an opinion",
                "Questions like 'when will…' or 'what should I do' ask the PIO to predict or advise. That isn't information "
                "held on record, so it is usually refused.",
                "Ask for what exists today: the current status, the file notings, the timeline or target date recorded in the file.",
                f"{rules.REF['s2f']}; {rules.REF['aditya']}",
                _quote(future_quotes[0]),
            )
        )
    return issues


_GRIEVANCE = re.compile(
    r"(take\s+(strict\s+|necessary\s+|immediate\s+)?action|resolve\s+my|solve\s+my|"
    r"redress|(please|kindly)\s+(refund|repair|fix|release|transfer\s+me|cancel|punish|suspend)|"
    r"i\s+demand|compensat(e|ion)\s+me|direct\s+(the|him|her|them)\s+to)",
    re.I,
)


def _check_grievance(text: str) -> list[Issue]:
    m = _GRIEVANCE.search(text)
    if not m:
        return []
    start = max(0, m.start() - 40)
    return [
        Issue(
            "GRIEVANCE",
            WARNING,
            "Reads like a complaint, not an information request",
            "Under RTI the PIO can only give information. They can't take action, refund money or fix your problem, "
            "so demands for action are ignored or the letter is treated as a grievance.",
            "Ask for the records that will help you push your case: status of your complaint, the officer it is with, "
            "the action-taken report, and the rules/timelines that apply. File the grievance separately (e.g. CPGRAMS).",
            rules.REF["s2f"],
            _quote(text[start : m.end() + 40]),
        )
    ]


_PRIVATE = re.compile(
    r"\b(pvt\.?\s*ltd|private\s+limited|private\s+(unaided|school|hospital|college|company|bank|builder)|"
    r"hdfc\s+bank|icici\s+bank|axis\s+bank|kotak|yes\s+bank|indusind|bandhan\s+bank|"
    r"flipkart|amazon|swiggy|zomato|paytm|phonepe|reliance\s+jio|\bjio\b|airtel|vodafone|\bvi\b\s+network|"
    r"ola\b|uber\b|byju'?s|tata\s+motors|maruti)",
    re.I,
)

_REGULATOR_HINT = (
    "Banks (private) → RBI; telecom → TRAI/DoT; private schools → the state education department/CBSE; "
    "private hospitals → the state health department; builders → the state RERA; companies → the Registrar of Companies."
)


# Regulators that hold records about private companies (the offices _REGULATOR_HINT points people to).
_REGULATOR_ADDRESSEE = re.compile(
    r"(reserve\s+bank|\brbi\b|banking\s+ombudsman|\btrai\b|telecom\s+regulatory|\bdot\b|department\s+of\s+telecom|"
    r"\bterm\s+cell|education\s+(officer|department|directorate)|\bdeo\b|directorate\s+of\s+(school\s+)?education|\bcbse\b|"
    r"health\s+(department|services)|\bcmho\b|\brera\b|real\s+estate\s+regulatory|registrar\s+of\s+companies|"
    r"\bsebi\b|\birdai\b)",
    re.I,
)


def _addressee(text: str) -> str:
    """The office the letter is addressed to: the lines after 'To,' up to the subject or a blank line."""
    m = re.search(r"^\s*To,?\s*$(.*?)(^\s*$|^\s*Subject)", text, re.I | re.M | re.S)
    if not m:
        return ""
    return re.sub(r"(the\s+)?public\s+information\s+officer,?", " ", m.group(1), flags=re.I).strip()


def _check_private(text: str) -> list[Issue]:
    m = _PRIVATE.search(text)
    if not m:
        return []
    to = _addressee(text)
    to = re.sub(r"<[^>]*>", "", to)  # ignore placeholders like <District Name>
    if to and not _PRIVATE.search(to) and _REGULATOR_ADDRESSEE.search(to):
        return []  # addressed to a regulator/government office that holds records about the company: that's fine
    return [
        Issue(
            "PRIVATE_BODY",
            WARNING,
            "Seems to target a private company",
            "Private companies are not 'public authorities', so you can't file an RTI on them directly. "
            "You can, however, ask a government regulator for information it holds about them.",
            "Send the RTI to the regulator instead. " + _REGULATOR_HINT,
            rules.REF["s2h"] + "; s.2(f) covers private-body information a public authority can access by law",
            m.group(0),
        )
    ]


_THIRD_PARTY = re.compile(
    r"((income\s+tax\s+returns?|\bitrs?\b)\s+(filed\s+)?(of|by)\s+(?!me\b|my\s+own)|medical\s+(records?|history)\s+of|bank\s+(statement|account)s?\s+of|"
    r"(phone|mobile)\s+number\s+of|caste\s+certificate\s+of|property\s+(details\s+)?of\s+my\s+(neighbou?r|relative|brother|husband|wife)|"
    r"(answer\s*sheet|marks)\s+of\s+(another|other|my\s+(friend|classmate))|personal\s+(file|details)\s+of)",
    re.I,
)


def _check_third_party(text: str) -> list[Issue]:
    m = _THIRD_PARTY.search(text)
    if not m:
        return []
    return [
        Issue(
            "THIRD_PARTY",
            WARNING,
            "Asks for someone else's personal information",
            "Personal details of another person (tax returns, medical records, bank details, phone numbers) are usually "
            "refused under s.8(1)(j) unless there is a larger public interest, and they trigger a 40-day third-party process.",
            "Ask for official records about the public act instead (e.g. the sanction order, the inspection report), "
            "and say why disclosure serves the public interest if you still need personal details.",
            rules.REF["s8_1_j"],
            m.group(0),
        )
    ]


_AADHAAR = re.compile(r"\b[2-9]\d{3}\s?\d{4}\s?\d{4}\b")
_PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")


def _check_oversharing(text: str) -> list[Issue]:
    m = _AADHAAR.search(text) or _PAN.search(text)
    if not m and not _find(r"\b(aadhaa?r|pan)\s*(no\.?|number|card)\s*[:\-]?\s*\w", text):
        return []
    return [
        Issue(
            "OVERSHARING",
            TIP,
            "Includes ID numbers you don't need to share",
            "You don't have to give Aadhaar, PAN or any reason for asking. Only contact details are needed, and RTI "
            "letters pass through many hands.",
            "Remove Aadhaar/PAN numbers. Name, address and (optionally) email/phone are enough.",
            rules.REF["s6_2"],
            m.group(0) if m else "",
        )
    ]


_VAGUE = re.compile(
    r"\b(all|complete|entire|whole)\s+(the\s+)?(information|details|documents|records|files|data)\b"
    r"(\s+(related|relating|regarding|about|pertaining)\s+to)?",
    re.I,
)
_PERIOD = re.compile(
    r"\b(19|20)\d{2}\b|\bfrom\s+\d|\bsince\s+\d|\bbetween\b|\bperiod\b|\blast\s+\d+\s+(years?|months?)\b|"
    r"\d{1,2}\s*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b|"
    r"\b(january|february|march|april|june|july|august|september|october|november|december)\b|"
    r"\bdated\b|\d{1,2}[./-]\d{1,2}[./-]\d{2,4}",
    re.I,
)


def _check_vague(text: str) -> list[Issue]:
    m = _VAGUE.search(text)
    # Years in the names of the Act/Rules ("Act, 2005") are not a time period.
    without_law_names = re.sub(r"\b(act|rules)\s*,?\s*(19|20)\d{2}", "", text, flags=re.I)
    # Nor is the date the letter was signed.
    without_law_names = re.sub(
        r"^\s*(date|dated|\u0926\u093f\u0928\u093e\u0902\u0915)\s*:.*$", "", without_law_names, flags=re.I | re.M
    )
    if not m or _PERIOD.search(without_law_names):
        return []
    return [
        Issue(
            "VAGUE",
            WARNING,
            "Too broad: 'all information' with no time period",
            "Very broad requests tend to get a vague reply, or an offer to 'come and inspect the files' instead of copies, "
            "because s.7(9) lets the PIO change the form when a request would disproportionately divert resources.",
            "Name the exact records (file notings, sanction order, inspection report, attendance register…) and a period, "
            "e.g. '1 April 2024 to 31 March 2025'.",
            "s.7(9) RTI Act: the form of the reply can change if a request would disproportionately divert resources",
            _quote(m.group(0)),
        )
    ]


def _check_many_points(text: str) -> list[Issue]:
    points = [s for s in text.splitlines() if re.match(r"^\s*(\(?\d+[.)]|\(?[a-z][.)]|[-*•])\s+\S", s)]
    if len(points) <= 10:
        return []
    return [
        Issue(
            "TOO_MANY_POINTS",
            TIP,
            f"{len(points)} separate points in one RTI",
            "Long lists across many topics are often answered partly, transferred in pieces, or charged extra pages. "
            "Focused RTIs get better replies.",
            "Keep 3–8 closely related points per application; send other topics as separate RTIs.",
            rules.REF["rule3"],
        )
    ]


def _check_act_named(text: str) -> list[Issue]:
    if _find(r"right\s+to\s+information|\brti\b|सूचना\s*का\s*अधिकार", text):
        return []
    return [
        Issue(
            "ACT_NOT_NAMED",
            TIP,
            "Doesn't say it's an RTI application",
            "Naming the Act makes it clear the 30-day legal deadline applies; otherwise the letter can be treated as general mail.",
            "Use the subject line: 'Application under Section 6(1) of the Right to Information Act, 2005'.",
            rules.REF["s6_1"],
        )
    ]


def _check_life_liberty(text: str) -> list[Issue]:
    if not _find(
        r"\b(arrest(ed)?|detain(ed|ment)|custody|missing\s+person|bail|life\s+(is\s+)?(in\s+)?danger|"
        r"threat\s+to\s+(my\s+)?life|hunger|starv)|"
        r"\u0939\u093f\u0930\u093e\u0938\u0924|\u0917\u093f\u0930\u092b\u094d\u0924\u093e\u0930",
        text,
    ):  # hirasat, giraftar
        return []
    if _find(
        r"life\s+(or|and)\s+liberty|\u091c\u0940\u0935\u0928\s+(\u092f\u093e|\u0914\u0930|\u090f\u0935\u0902)\s+\u0938\u094d\u0935\u0924\u0902\u0924\u094d\u0930\u0924\u093e",
        text,
    ):  # jeevan ya swatantrata
        return []
    return [
        Issue(
            "LIFE_LIBERTY",
            TIP,
            "Could qualify for the 48-hour deadline",
            "If the information concerns someone's life or liberty (e.g. detention, custody, a threat to life), the PIO "
            "must reply within 48 hours instead of 30 days, but only if you say so.",
            "Add a line: 'This information concerns the life or liberty of a person; please reply within 48 hours under "
            "the proviso to Section 7(1).' Use it only when it's genuinely true.",
            rules.REF["s7_1"],
        )
    ]


_CHECKS = [
    _check_addressee,
    _check_contact,
    _check_fee,
    _check_questions,
    _check_grievance,
    _check_private,
    _check_third_party,
    _check_vague,
    _check_many_points,
    _check_oversharing,
    _check_act_named,
    _check_life_liberty,
]


def check(text: str) -> Report:
    """Run every check on an RTI application and return a sorted report."""
    text = text or ""
    word_count = len(re.findall(r"\S+", text))
    report = Report(word_count=word_count)
    if not text.strip():
        report.issues.append(
            Issue(
                "EMPTY", ERROR, "Nothing to check", "The application is empty.", "Paste your RTI application text.", ""
            )
        )
        return report
    for fn in _CHECKS:
        report.issues.extend(fn(text))
    report.issues.extend(_check_length(text, word_count))
    report.issues.sort(key=lambda i: _SEVERITY_ORDER[i.severity])
    return report
