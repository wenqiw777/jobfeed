"""Durable daily random slots do not create repeats or catch-up scans."""

# ruff: noqa: PLR2004

import json
from datetime import UTC, date, datetime
from unittest.mock import Mock

import pytest

from scripts import jobfeed_random_schedule as scheduler

DAY = date(2026, 10, 3)


def test_draws_once_in_each_window_and_reuses_saved_choices(tmp_path):
    calls = []

    def draw(start, end):
        calls.append((start, end))
        return start + 7

    first = scheduler.ensure_plan(
        tmp_path,
        DAY,
        draw=draw,
    )
    assert first["minutes"] == [487, 847, 1147]
    assert calls == [(480, 600), (840, 960), (1140, 1260)]
    assert scheduler.ensure_plan(tmp_path, DAY, draw=draw) == first
    assert len(calls) == 3


def test_new_day_gets_a_fresh_plan(tmp_path):
    scheduler.ensure_plan(
        tmp_path,
        DAY,
        draw=lambda start, _end: start,
    )
    following = scheduler.ensure_plan(
        tmp_path,
        date(2026, 10, 4),
        draw=lambda _start, end: end - 1,
    )
    assert following["day"] == "2026-10-04"
    assert following["minutes"] == [599, 959, 1259]


def test_runs_each_chosen_minute_only_once_even_on_failure(tmp_path):
    scheduler.ensure_plan(
        tmp_path,
        DAY,
        draw=lambda start, _end: start + 7,
    )
    calls = []
    for hour, minute in [(8, 7), (14, 7), (19, 7)]:
        now = datetime(2026, 10, 3, hour, minute)
        assert (
            scheduler.run_due(
                tmp_path, DAY, now=now, run=lambda: calls.append(True) or 1
            )
            == 1
        )
        assert (
            scheduler.run_due(
                tmp_path, DAY, now=now, run=lambda: calls.append(True) or 0
            )
            == 0
        )
    assert len(calls) == 3


def test_wrong_day_late_trigger_and_missing_plan_do_not_scan(tmp_path):
    calls = []

    def run():
        calls.append(True)
        return 0

    assert (
        scheduler.run_due(tmp_path, DAY, now=datetime(2026, 10, 3, 0, 7), run=run) == 0
    )
    scheduler.ensure_plan(
        tmp_path,
        DAY,
        draw=lambda start, _end: start + 7,
    )
    for now in [
        datetime(2026, 10, 4, 0, 7),
        datetime(2026, 10, 3, 0, 8),
        datetime(2026, 10, 3, 8, 0),
    ]:
        assert scheduler.run_due(tmp_path, DAY, now=now, run=run) == 0
    assert calls == []


def test_corrupt_plan_fails_without_running(tmp_path):
    plan = scheduler.ensure_plan(
        tmp_path,
        DAY,
        draw=lambda start, _end: start,
    )
    plan["minutes"] = [0, 0, 0]
    scheduler.plan_path(tmp_path, DAY).write_text(json.dumps(plan))
    calls = []

    with pytest.raises(ValueError):
        scheduler.run_due(
            tmp_path,
            DAY,
            now=datetime(2026, 10, 3, 0, 0),
            run=lambda: calls.append(True) or 0,
        )
    assert calls == []


def test_launch_agent_contains_three_drawn_slots_and_day_guard(tmp_path):
    plan = scheduler.ensure_plan(
        tmp_path,
        DAY,
        draw=lambda start, _end: start + 7,
    )
    agent = scheduler.scan_agent(tmp_path, plan)
    assert agent["StartCalendarInterval"] == [
        {"Hour": 8, "Minute": 7},
        {"Hour": 14, "Minute": 7},
        {"Hour": 19, "Minute": 7},
    ]
    assert agent["ProgramArguments"][-3:] == ["run", "--day", "2026-10-03"]
    assert agent["RunAtLoad"] is False


def test_only_daytime_windows_can_be_selected(tmp_path):
    assert scheduler.WINDOWS == ((480, 600), (840, 960), (1140, 1260))
    plan = scheduler.ensure_plan(tmp_path, DAY, draw=lambda _start, end: end - 1)
    assert plan["minutes"] == [599, 959, 1259]


def test_legacy_overnight_slot_never_starts(tmp_path):
    path = scheduler.plan_path(tmp_path, DAY)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {"day": DAY.isoformat(), "minutes": [242, 884, 1332], "attempted": []}
        )
    )
    run = Mock()
    scheduler.run_due(tmp_path, DAY, now=datetime(2026, 10, 3, 4, 2), run=run)
    scheduler.run_due(tmp_path, DAY, now=datetime(2026, 10, 3, 22, 12), run=run)
    run.assert_not_called()


def test_existing_daily_plan_is_preserved(tmp_path):
    path = scheduler.plan_path(tmp_path, DAY)
    path.parent.mkdir(parents=True)
    legacy = {"day": DAY.isoformat(), "minutes": [139, 648, 1085], "attempted": [139]}
    path.write_text(json.dumps(legacy))
    assert scheduler.ensure_plan(tmp_path, DAY) == legacy


def test_midnight_planner_checks_current_minute_after_install(tmp_path, monkeypatch):
    events = []
    monkeypatch.setattr(scheduler, "ROOT", tmp_path)
    monkeypatch.setattr(
        scheduler.sys,
        "argv",
        ["schedule", "plan", "--day", DAY.isoformat(), "--run-due"],
    )
    monkeypatch.setattr(
        scheduler, "install_plan", lambda _root, _plan: events.append("installed")
    )
    monkeypatch.setattr(
        scheduler, "run_due", lambda _root, _day: events.append("due_checked") or 0
    )
    assert scheduler.main() == 0
    assert events == ["installed", "due_checked"]


def test_manual_plan_install_does_not_start_a_scan(tmp_path, monkeypatch):
    monkeypatch.setattr(scheduler, "ROOT", tmp_path)
    monkeypatch.setattr(
        scheduler.sys, "argv", ["schedule", "plan", "--day", DAY.isoformat()]
    )
    monkeypatch.setattr(scheduler, "install_plan", lambda _root, _plan: None)
    monkeypatch.setattr(
        scheduler, "run_due", lambda _root, _day: pytest.fail("unexpected scan")
    )
    assert scheduler.main() == 0


def test_tick_runs_persisted_slot_once_and_rolls_over_day(tmp_path):
    calls = []
    scheduler.ensure_plan(tmp_path, DAY, draw=lambda start, _end: start + 7)
    now = datetime(2026, 10, 3, 8, 7, 30, tzinfo=scheduler.TIMEZONE)
    scheduler.tick(tmp_path, now=now, run=lambda: calls.append(True) or 0)
    scheduler.tick(tmp_path, now=now, run=lambda: calls.append(True) or 0)
    assert calls == [True]
    tomorrow = datetime(2026, 10, 4, 12, 0, tzinfo=scheduler.TIMEZONE)
    scheduler.tick(tmp_path, now=tomorrow, run=lambda: 0)
    assert scheduler.plan_path(tmp_path, tomorrow.date()).exists()


def test_polling_agent_uses_interval_and_no_dated_calendar(tmp_path):
    agent = scheduler.poll_agent(tmp_path)
    assert agent["StartInterval"] == 30
    assert "StartCalendarInterval" not in agent
    assert agent["ProgramArguments"][-1] == "tick"
    assert "--day" not in agent["ProgramArguments"]


def test_tick_uses_detroit_date_even_when_host_day_is_utc(tmp_path, monkeypatch):
    clock = Mock()
    # At 00:07 UTC it is still the previous Detroit calendar day.
    now = datetime(2026, 10, 4, 0, 7, tzinfo=UTC).astimezone(scheduler.TIMEZONE)
    clock.now.return_value = now
    monkeypatch.setattr(scheduler, "datetime", clock)
    scheduler.tick(tmp_path, run=lambda: 0)
    clock.now.assert_called_once_with(scheduler.TIMEZONE)
    assert scheduler.plan_path(tmp_path, DAY).exists()
    assert not scheduler.plan_path(tmp_path, date(2026, 10, 4)).exists()
