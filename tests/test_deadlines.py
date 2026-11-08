from datetime import date, datetime

import pytest

from rti_sahayak import timeline
from rti_sahayak.deadlines import reply_due


def test_reply_due_basic():
    assert reply_due(date(2026, 9, 1)) == date(2026, 10, 1)


def test_reply_due_apio_and_third_party():
    assert reply_due(date(2026, 9, 1), via_apio=True) == date(2026, 10, 6)
    assert reply_due(date(2026, 9, 1), third_party=True) == date(2026, 10, 11)


def test_reply_due_life_liberty_is_48_hours():
    due = reply_due(date(2026, 9, 1), life_liberty=True, filed_at=datetime(2026, 9, 1, 11, 0))
    assert due == datetime(2026, 9, 3, 11, 0)


def test_waiting_for_reply():
    t = timeline("2026-11-01", today="2026-11-08")
    assert not t.deemed_refusal
    assert t.reply_due == date(2026, 12, 1)
    assert "Wait for the reply" in t.advice
    assert t.steps[-1].status == "upcoming"


def test_deemed_refusal_opens_first_appeal():
    t = timeline("2026-09-20", today="2026-10-25")
    assert t.deemed_refusal
    assert t.first_appeal_last_day == date(2026, 11, 19)  # 30 days after 20 Oct due date
    assert t.steps[-1].status == "open"
    assert "File a first appeal now" in t.advice


def test_window_missed():
    t = timeline("2026-01-01", today="2026-11-08")
    assert t.steps[-1].status == "missed"
    assert "condone" in t.advice


def test_reply_received_counts_from_reply():
    t = timeline("2026-10-01", reply_received="2026-10-20", today="2026-11-08")
    assert t.first_appeal_last_day == date(2026, 11, 19)
    assert "11 day(s) left" in t.advice


def test_late_reply_is_free():
    t = timeline("2026-09-01", reply_received="2026-10-15", today="2026-10-20")
    assert any("free of cost" in s.note for s in t.steps)


def test_first_appeal_pending_then_second_appeal():
    t = timeline("2026-06-01", reply_received="2026-06-20", first_appeal_filed="2026-07-01", today="2026-07-10")
    assert "pending" in t.advice and t.second_appeal_last_day is None
    t = timeline(
        "2026-06-01",
        reply_received="2026-06-20",
        first_appeal_filed="2026-07-01",
        faa_order_received="2026-07-25",
        today="2026-08-01",
    )
    assert t.second_appeal_last_day == date(2026, 10, 23)  # 90 days after order


def test_faa_silent_beyond_45_days_opens_second_appeal():
    t = timeline("2026-05-01", reply_received="2026-05-20", first_appeal_filed="2026-06-01", today="2026-08-01")
    assert t.second_appeal_last_day == date(2026, 10, 14)  # 45 + 90 days from appeal


def test_date_formats_and_errors():
    assert timeline("01-09-2026", today="08-11-2026").reply_due == date(2026, 10, 1)
    assert timeline("01/09/2026", today="2026-11-08").reply_due == date(2026, 10, 1)
    with pytest.raises(ValueError):
        timeline("tomorrow")
    with pytest.raises(ValueError):
        timeline("2026-12-01", today="2026-11-08")
    with pytest.raises(ValueError):
        timeline("2026-09-01", reply_received="2026-08-01", today="2026-11-08")
