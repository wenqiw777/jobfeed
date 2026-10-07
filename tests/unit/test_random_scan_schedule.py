"""Durable daily random slots do not create repeats or catch-up scans."""

# ruff: noqa: PLR2004

import json
from datetime import date, datetime

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
        choose=lambda windows, _count: [windows[i] for i in (0, 6, 11)],
    )
    assert first["minutes"] == [7, 727, 1327]
    assert calls == [(0, 120), (720, 840), (1320, 1440)]
    assert scheduler.ensure_plan(tmp_path, DAY, draw=draw) == first
    assert len(calls) == 3


def test_new_day_gets_a_fresh_plan(tmp_path):
    scheduler.ensure_plan(
        tmp_path,
        DAY,
        draw=lambda start, _end: start,
        choose=lambda windows, count: windows[:count],
    )
    following = scheduler.ensure_plan(
        tmp_path,
        date(2026, 10, 4),
        draw=lambda _start, end: end - 1,
        choose=lambda windows, count: windows[-count:],
    )
    assert following["day"] == "2026-10-04"
    assert following["minutes"] == [1199, 1319, 1439]


def test_runs_each_chosen_minute_only_once_even_on_failure(tmp_path):
    scheduler.ensure_plan(
        tmp_path,
        DAY,
        draw=lambda start, _end: start + 7,
        choose=lambda windows, _count: [windows[i] for i in (0, 4, 8)],
    )
    calls = []
    for hour, minute in [(0, 7), (8, 7), (16, 7)]:
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
        choose=lambda windows, _count: [windows[i] for i in (0, 4, 8)],
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
        choose=lambda windows, count: windows[:count],
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
        choose=lambda windows, _count: [windows[i] for i in (0, 4, 8)],
    )
    agent = scheduler.scan_agent(tmp_path, plan)
    assert agent["StartCalendarInterval"] == [
        {"Hour": 0, "Minute": 7},
        {"Hour": 8, "Minute": 7},
        {"Hour": 16, "Minute": 7},
    ]
    assert agent["ProgramArguments"][-3:] == ["run", "--day", "2026-10-03"]
    assert agent["RunAtLoad"] is False


def test_all_twelve_slots_can_be_selected(tmp_path):
    expected = tuple((start, start + 120) for start in range(0, 1440, 120))
    assert expected == scheduler.WINDOWS
    for slot in range(12):
        plan = scheduler.ensure_plan(
            tmp_path / str(slot),
            DAY,
            choose=lambda windows, count, slot=slot: [
                windows[(slot + i) % 12] for i in range(count)
            ],
            draw=lambda _start, end: end - 1,
        )
        assert len({minute // 120 for minute in plan["minutes"]}) == 3
        assert slot in {minute // 120 for minute in plan["minutes"]}


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
