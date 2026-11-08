"""RTI deadline calculator: when the reply is due, and when each appeal window opens and closes.

Days are counted as calendar days from the day after the event (the usual way the
Commissions count them). Results are guidance, not legal advice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from . import rules


@dataclass
class Step:
    label: str
    when: date | datetime
    status: str  # "done", "open", "upcoming", "missed", "info"
    note: str = ""

    @property
    def when_text(self) -> str:
        if isinstance(self.when, datetime):
            return self.when.strftime("%d %b %Y, %I:%M %p")
        return self.when.strftime("%d %b %Y")


@dataclass
class Timeline:
    steps: list[Step] = field(default_factory=list)
    reply_due: date | datetime | None = None
    first_appeal_last_day: date | None = None
    second_appeal_last_day: date | None = None
    deemed_refusal: bool = False
    advice: str = ""


def _as_date(d: date | datetime) -> date:
    return d.date() if isinstance(d, datetime) else d


def _parse(d: str | date | None) -> date | None:
    if d is None or d == "":
        return None
    if isinstance(d, date):
        return d
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(d.strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Couldn't read the date '{d}'. Use YYYY-MM-DD or DD-MM-YYYY.")


def reply_due(
    filed: date,
    *,
    via_apio: bool = False,
    third_party: bool = False,
    life_liberty: bool = False,
    filed_at: datetime | None = None,
) -> date | datetime:
    """Last day (or moment, for 48-hour cases) for the PIO to reply. s.7(1), s.5(2), s.11(3)."""
    if life_liberty:
        start = filed_at or datetime.combine(filed, datetime.min.time()).replace(hour=10)
        return start + timedelta(hours=rules.LIFE_LIBERTY_HOURS)
    days = rules.THIRD_PARTY_DAYS if third_party else rules.REPLY_DAYS
    if via_apio:
        days += rules.APIO_EXTRA_DAYS
    return filed + timedelta(days=days)


def timeline(
    filed: str | date,
    *,
    today: str | date | None = None,
    reply_received: str | date | None = None,
    first_appeal_filed: str | date | None = None,
    faa_order_received: str | date | None = None,
    via_apio: bool = False,
    third_party: bool = False,
    life_liberty: bool = False,
) -> Timeline:
    """Build the full timeline for one RTI, with the status of each step as of `today`."""
    filed_d = _parse(filed)
    if filed_d is None:
        raise ValueError("Please enter the date you filed the RTI.")
    today_d = _parse(today) or date.today()
    reply_d = _parse(reply_received)
    fa_d = _parse(first_appeal_filed)
    faa_d = _parse(faa_order_received)

    for name, d in (("reply", reply_d), ("first appeal", fa_d), ("appeal order", faa_d)):
        if d and d < filed_d:
            raise ValueError(f"The {name} date is before the filing date.")
    if filed_d > today_d:
        raise ValueError("The filing date is in the future.")

    t = Timeline()
    t.steps.append(Step("RTI filed", filed_d, "done"))

    due = reply_due(filed_d, via_apio=via_apio, third_party=third_party, life_liberty=life_liberty)
    t.reply_due = due
    if life_liberty:
        due_note = "48 hours: life or liberty (s.7(1) proviso)"
    elif third_party:
        due_note = f"{rules.THIRD_PARTY_DAYS} days: third-party information (s.11(3))"
    else:
        due_note = f"{rules.REPLY_DAYS} days (s.7(1))"
    if via_apio and not life_liberty:
        due_note += " + 5 days because it was filed through an Assistant PIO (s.5(2))"

    if reply_d:
        late = reply_d > _as_date(due)
        t.steps.append(Step("Reply due", due, "info", due_note))
        t.steps.append(
            Step(
                "Reply received",
                reply_d,
                "done",
                "Late: information should now be free of cost (s.7(6))." if late else "",
            )
        )
        appeal_start = reply_d
    else:
        overdue = today_d > _as_date(due)
        t.deemed_refusal = overdue
        t.steps.append(
            Step(
                "Reply due",
                due,
                "missed" if overdue else "upcoming",
                due_note + (". No reply = deemed refusal (s.7(2)); you can appeal now." if overdue else ""),
            )
        )
        appeal_start = _as_date(due)

    fa_last = appeal_start + timedelta(days=rules.FIRST_APPEAL_DAYS)
    t.first_appeal_last_day = fa_last
    if fa_d:
        t.steps.append(
            Step(
                "First appeal filed",
                fa_d,
                "done",
                "" if fa_d <= fa_last else "Filed after 30 days: ask the FAA to condone the delay (s.19(1) proviso).",
            )
        )
        faa_due = fa_d + timedelta(days=rules.FAA_DECISION_DAYS)
        faa_max = fa_d + timedelta(days=rules.FAA_DECISION_MAX_DAYS)
        if faa_d:
            t.steps.append(Step("First appeal decided", faa_d, "done"))
            second_start = faa_d
        else:
            overdue = today_d > faa_max
            t.steps.append(
                Step(
                    "First appeal decision due",
                    faa_due,
                    "missed" if today_d > faa_due else "upcoming",
                    f"30 days, at most 45 with written reasons (by {faa_max.strftime('%d %b %Y')}) (s.19(6))",
                )
            )
            second_start = faa_max if overdue or today_d > faa_due else None
        if second_start:
            sa_last = second_start + timedelta(days=rules.SECOND_APPEAL_DAYS)
            t.second_appeal_last_day = sa_last
            t.steps.append(
                Step(
                    "Second appeal to Information Commission: last day",
                    sa_last,
                    "open" if today_d <= sa_last else "missed",
                    "90 days (s.19(3)). Late appeals can be admitted for sufficient cause.",
                )
            )
    else:
        can_appeal_now = reply_d is not None or t.deemed_refusal
        if can_appeal_now:
            status = "open" if today_d <= fa_last else "missed"
            note = "30 days (s.19(1)). Late appeals can be admitted if you show sufficient cause."
        else:
            status = "upcoming"
            note = "Opens if there is no reply by the due date, or once you get an unsatisfactory reply."
        t.steps.append(Step("First appeal: last day", fa_last, status, note))

    t.advice = _advice(t, today_d, reply_d, fa_d, faa_d)
    return t


def _advice(t: Timeline, today: date, reply_d, fa_d, faa_d) -> str:
    if faa_d or (fa_d and t.second_appeal_last_day):
        if t.second_appeal_last_day and today <= t.second_appeal_last_day:
            days = (t.second_appeal_last_day - today).days
            return (
                f"If the first appeal order didn't help (or never came), file a second appeal with the "
                f"Information Commission. You have {days} day(s) left."
            )
        return "The second-appeal window has passed. You can still file it with a request to condone the delay."
    if fa_d:
        return "Your first appeal is pending. Wait for the decision; if none comes in 45 days, go to the Information Commission."
    if t.deemed_refusal:
        days = (t.first_appeal_last_day - today).days
        if days >= 0:
            return (
                f"The PIO missed the deadline, which counts as a refusal. File a first appeal now; "
                f"{days} day(s) left in the normal window."
            )
        return "The normal first-appeal window has passed. You can still appeal and ask the FAA to condone the delay."
    if reply_d:
        days = (t.first_appeal_last_day - today).days
        if days >= 0:
            return f"Not happy with the reply? File a first appeal. {days} day(s) left."
        return "The normal first-appeal window has passed. You can still appeal and ask the FAA to condone the delay."
    due = _as_date(t.reply_due)
    return f"Wait for the reply. It is due by {due.strftime('%d %b %Y')} ({(due - today).days} day(s) from today)."
