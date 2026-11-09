"""RTI Sahayak web demo (Gradio). Run: python app.py  (or `rti-sahayak` once installed)

With GEMINI_API_KEY (or ANTHROPIC_API_KEY) set, drafting uses AI. Without it, everything still
works: drafting falls back to the rule-based drafter, and checking and deadlines
never need AI.
"""

from __future__ import annotations

import os
import tempfile
from datetime import date, timedelta

import gradio as gr

from . import Applicant, check, draft_offline, draft_with_ai, render_letter, timeline
from .checker import ERROR, TIP, WARNING, Report
from .drafter import GROUNDS, DraftError, ai_provider, render_first_appeal

PROVIDER = ai_provider()
AI_ON = PROVIDER is not None
REPO_URL = "https://github.com/NatikChhabra/rti-sahayak"

ICON = {ERROR: "🔴", WARNING: "🟠", TIP: "🔵"}
RISK_LINE = {
    "High": "🔴 **High risk** of being returned or refused. Fix the red items first.",
    "Medium": "🟠 **Medium risk**: it will probably be accepted, but some points may get a weak reply.",
    "Low": "🟢 **Looks good.** No common mistakes found.",
}


def _esc(t: str) -> str:
    """Escape text so placeholders like <office name> aren't swallowed as HTML tags."""
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def report_md(r: Report) -> str:
    out = [RISK_LINE[r.risk], f"<small>{r.word_count} words</small>", ""]
    for i in r.issues:
        quote = f"\n  > {_esc(i.quote)}" if i.quote else ""
        out.append(
            f"{ICON[i.severity]} **{_esc(i.title)}**{quote}\n\n"
            f"  {_esc(i.why)}\n\n  ✅ **Fix:** {_esc(i.fix)}\n\n  <small>📖 {_esc(i.law)}</small>\n"
        )
    return "\n".join(out)


# ------------------------------------------------------------------ handlers


def _s(x) -> str:
    """Gradio sends None for fields that were never rendered (e.g. a closed accordion)."""
    return (x or "").strip()


def on_draft(need, authority, name, address, email, phone, place, fee_mode):
    need, authority = _s(need), _s(authority)
    applicant = Applicant(
        name=_s(name),
        address=_s(address),
        email=_s(email),
        phone=_s(phone),
        place=_s(place),
        fee_mode=fee_mode or "Indian Postal Order",
    )
    try:
        if AI_ON:
            try:
                d, log = draft_with_ai(need, authority_hint=authority)
            except DraftError as e:
                if "Tell me" in str(e):
                    raise
                d, log = draft_offline(need, authority_hint=authority)
                log = [f"AI drafting failed ({e}); used the offline drafter instead."] + log
        else:
            d, log = draft_offline(need, authority_hint=authority)
    except DraftError as e:
        raise gr.Error(str(e)) from None

    letter = render_letter(d, applicant)
    report = check(letter)
    notes_list = list(d.notes_for_user)
    if not (applicant.address or applicant.email):
        # The blanks are left for the user on purpose; don't score the draft down for them.
        report.issues = [i for i in report.issues if i.code != "NO_CONTACT"]
        notes_list.append("Add your name and address under 'Your details' (or fill in the blanks) before you send it.")
    elif any(i.code == "NO_CONTACT" for i in report.issues):
        notes_list.append("Add the 6-digit PIN code to your address (or an email) so the reply reaches you.")
    steps = "\n".join(f"- {_esc(s)}" for s in log)
    notes = "\n".join(f"- {_esc(n)}" for n in notes_list)
    guess = (
        (
            "\n\n⚠️ **Check the office name.** It was guessed from your description; find the exact PIO "
            "address on the department's website or [rtionline.gov.in](https://rtionline.gov.in)."
        )
        if d.authority_is_guess
        else ""
    )
    info = f"**How this was made**\n{steps}\n\n**Notes for you**\n{notes}{guess}"

    path = os.path.join(tempfile.mkdtemp(), "rti_application.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(letter)
    return letter, info, "**Checker results**\n\n" + report_md(report), gr.update(value=path, visible=True)


def on_check(text):
    return report_md(check(_s(text)))


def on_timeline(filed, reply, fa, faa, apio, third, life):
    try:
        t = timeline(
            _s(filed),
            today=date.today(),
            reply_received=_s(reply) or None,
            first_appeal_filed=_s(fa) or None,
            faa_order_received=_s(faa) or None,
            via_apio=bool(apio),
            third_party=bool(third),
            life_liberty=bool(life),
        )
    except ValueError as e:
        raise gr.Error(str(e)) from None
    icon = {"done": "✅", "open": "🟢", "upcoming": "⏳", "missed": "🔴", "info": "ℹ️"}
    rows = ["| | Step | Date | Notes |", "|---|---|---|---|"]
    rows += [f"| {icon[s.status]} | {s.label} | **{s.when_text}** | {s.note} |" for s in t.steps]
    return f"### 👉 {t.advice}\n\n" + "\n".join(rows)


def on_appeal(name, address, email, authority, rti_date, rti_ref, reply_date, grounds, details):
    if not _s(rti_date):
        raise gr.Error("Enter the date you filed the RTI (in the Deadlines box above).")
    keys = [k for k, v in GROUND_LABELS.items() if v in (grounds or [])]
    return render_first_appeal(
        applicant=Applicant(name=_s(name), address=_s(address), email=_s(email)),
        public_authority=_s(authority),
        rti_date=_s(rti_date),
        rti_ref=_s(rti_ref),
        reply_date=_s(reply_date),
        grounds=keys or None,
        details=_s(details),
    )


GROUND_LABELS = {
    "no_reply": "No reply in 30 days",
    "incomplete": "Reply was incomplete",
    "wrong_denial": "Refused without proper reason",
    "misleading": "Information is wrong or misleading",
    "fee": "Asked for extra fee after a late reply",
}
assert set(GROUND_LABELS) == set(GROUNDS)

# ------------------------------------------------------------------ examples

EX_NEEDS = [
    [
        "Why has the road in ward 12 of Bilaspur not been repaired since 2023? When will it be fixed? "
        "How much money was sanctioned for it?",
        "Municipal Corporation, Bilaspur, Chhattisgarh",
    ],
    ["I applied for a post-matric scholarship in August 2025 but haven't received it. Please release my money.", ""],
    ["HDFC Bank charged me a penalty I don't understand and won't explain it.", ""],
    ["मेरे गाँव में 2024 में बने हैंडपंप का पैसा कितना मंज़ूर हुआ और काम कब पूरा होगा?", "ग्राम पंचायत, तखतपुर"],
]

BAD_RTI = """Dear Sir,
Why did you not give my scholarship? Please take strict action against the clerk and release my money.
I want all information about scholarships.
My Aadhaar 2345 6789 1234.
Ramesh"""

GOOD_RTI = """To,
The Public Information Officer,
Office of the District Collector, Bilaspur, Chhattisgarh

Subject: Application under Section 6(1) of the Right to Information Act, 2005 – post-matric scholarship 2025-26

Please provide, for the period 1 August 2025 to date:
1. The present status of my scholarship application No. CG/PMS/2025/____, with the dates of each action taken and the name and designation of the officer with whom it is pending.
2. Copies of the file notings and orders on my application.
3. A copy of the rules or circular that fixes the time limit for disbursing the scholarship.

I am a citizen of India. The application fee of Rs 10 is paid by Indian Postal Order No. ________.

Applicant: Ramesh Kumar, House No 14, Sarkanda, Bilaspur 495006
Email: ramesh@example.com"""

# ------------------------------------------------------------------ layout

CSS = """
.gradio-container {max-width: 980px !important; margin: auto;}
#hero h1 {margin-bottom: 0.2em;}
#hero p {font-size: 1.05em;}
footer {visibility: hidden;}
"""

with gr.Blocks(title="RTI Sahayak") as demo:
    gr.Markdown(
        "# 📨 RTI Sahayak\n"
        "**Write, check and track Right to Information applications (India).** "
        "Over 53,000 RTI complaints were returned in a single year for avoidable mistakes. "
        "This catches them before you post.\n\n"
        f"<small>{f'🤖 AI drafting on ({PROVIDER.title()})' if AI_ON else '⚙️ Offline mode: rule-based drafting (no AI key set)'} · "
        f"Central RTI Act 2005 + RTI Rules 2012 · Not legal advice · [Source code]({REPO_URL})</small>",
        elem_id="hero",
    )

    with gr.Tab("✍️ Write my RTI"):
        need = gr.Textbox(
            label="What do you want to know? (English or Hindi, in your own words)",
            lines=4,
            placeholder="e.g. Why hasn't the road outside my house been repaired? How much money was sanctioned?",
        )
        authority = gr.Textbox(
            label="Which government office? (optional)", placeholder="e.g. Municipal Corporation, Bilaspur"
        )
        with gr.Accordion("Your details (optional, added to the letter)", open=False):
            with gr.Row():
                name = gr.Textbox(label="Name")
                place = gr.Textbox(label="Place")
            address = gr.Textbox(label="Address with PIN code")
            with gr.Row():
                email = gr.Textbox(label="Email")
                phone = gr.Textbox(label="Phone")
            fee_mode = gr.Radio(
                ["Indian Postal Order", "Demand Draft", "Online", "Cash", "BPL"],
                value="Indian Postal Order",
                label="How will you pay the ₹10 fee?",
            )
        draft_btn = gr.Button("Write my RTI", variant="primary")
        gr.Examples(EX_NEEDS, inputs=[need, authority], label="Try an example")
        with gr.Row():
            with gr.Column(scale=3):
                letter = gr.Textbox(label="Your RTI application", lines=22, buttons=["copy"], interactive=True)
                download = gr.DownloadButton("⬇️ Download .txt", visible=False)
            with gr.Column(scale=2):
                info = gr.Markdown()
                draft_report = gr.Markdown()
        draft_btn.click(
            on_draft,
            [need, authority, name, address, email, phone, place, fee_mode],
            [letter, info, draft_report, download],
        )

    with gr.Tab("🔍 Check my RTI"):
        gr.Markdown(
            "Paste an RTI you've written. It checks 15 common mistakes that get RTIs returned or "
            "answered with 'not information', and shows how to fix each one."
        )
        rti_text = gr.Textbox(label="Your RTI application", lines=12)
        check_btn = gr.Button("Check it", variant="primary")
        gr.Examples([[BAD_RTI], [GOOD_RTI]], inputs=[rti_text], label="Try an example (bad, then fixed)")
        check_out = gr.Markdown()
        check_btn.click(on_check, rti_text, check_out)
        rti_text.submit(on_check, rti_text, check_out)

    with gr.Tab("📅 Deadlines & appeal"):
        gr.Markdown("Dates as **YYYY-MM-DD** or **DD-MM-YYYY**. Leave a box empty if it hasn't happened yet.")
        with gr.Row():
            filed = gr.Textbox(label="RTI filed on", placeholder="2026-09-01")
            reply = gr.Textbox(label="Reply received on")
        with gr.Row():
            fa = gr.Textbox(label="First appeal filed on")
            faa = gr.Textbox(label="First appeal order received on")
        with gr.Row():
            apio = gr.Checkbox(label="Filed through an Assistant PIO (+5 days)")
            third = gr.Checkbox(label="Involves third-party information (40 days)")
            life = gr.Checkbox(label="Life or liberty (48 hours)")
        tl_btn = gr.Button("Show my deadlines", variant="primary")
        _d = lambda n: (date.today() - timedelta(days=n)).isoformat()  # noqa: E731  (examples stay current)
        gr.Examples(
            [[_d(35), "", "", ""], [_d(40), _d(20), "", ""], [_d(5), "", "", ""]],
            inputs=[filed, reply, fa, faa],
            label="Try an example",
        )
        tl_out = gr.Markdown()
        tl_btn.click(on_timeline, [filed, reply, fa, faa, apio, third, life], tl_out)

        with gr.Accordion("Draft my first appeal", open=False):
            with gr.Row():
                a_name = gr.Textbox(label="Your name")
                a_email = gr.Textbox(label="Email")
            a_address = gr.Textbox(label="Address with PIN code")
            a_auth = gr.Textbox(label="Office (same as your RTI)")
            a_ref = gr.Textbox(label="RTI registration / reference no. (if any)")
            a_grounds = gr.CheckboxGroup(list(GROUND_LABELS.values()), label="What went wrong?")
            a_details = gr.Textbox(label="Anything else to add (optional)", lines=2)
            appeal_btn = gr.Button("Write my first appeal")
            appeal_out = gr.Textbox(label="Your first appeal", lines=20, buttons=["copy"])
            appeal_btn.click(
                on_appeal, [a_name, a_address, a_email, a_auth, filed, a_ref, reply, a_grounds, a_details], appeal_out
            )

    gr.Markdown(
        "<small>RTI Sahayak gives general guidance based on the RTI Act, 2005 and the central RTI Rules, 2012. "
        "State rules (fees, formats) can differ. It is not legal advice. Your text is not stored. "
        f"Built by Natik Chhabra with Claude as a coding partner · [GitHub]({REPO_URL})</small>"
    )


def main() -> None:
    demo.launch(theme=gr.themes.Soft(primary_hue="indigo"), css=CSS)


if __name__ == "__main__":
    main()
