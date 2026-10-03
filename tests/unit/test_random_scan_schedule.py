"""Durable daily random slots do not create repeats or catch-up scans."""

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

    first = scheduler.ensure_plan(tmp_path, DAY, draw=draw)
    assert first["minutes"] == [67, 547, 1027]
    assert calls == list(scheduler.WINDOWS)
    assert scheduler.ensure_plan(tmp_path, DAY, draw=draw) == first
    assert len(calls) == len(scheduler.WINDOWS)


def test_new_day_gets_a_fresh_plan(tmp_path):
    scheduler.ensure_plan(tmp_path, DAY, draw=lambda start, _end: start)
    following = scheduler.ensure_plan(
        tmp_path, date(2026, 10, 4), draw=lambda _start, end: end - 1
    )
    assert following["day"] == "2026-10-04"
    assert following["minutes"] == [179, 659, 1139]


def test_runs_each_chosen_minute_only_once_even_on_failure(tmp_path):
    scheduler.ensure_plan(tmp_path, DAY, draw=lambda start, _end: start + 7)
    calls = []
    for hour, minute in [(1, 7), (9, 7), (17, 7)]:
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
    assert len(calls) == len(scheduler.WINDOWS)


def test_wrong_day_late_trigger_and_missing_plan_do_not_scan(tmp_path):
    calls = []

    def run():
        calls.append(True)
        return 0

    assert (
        scheduler.run_due(tmp_path, DAY, now=datetime(2026, 10, 3, 1, 7), run=run) == 0
    )
    scheduler.ensure_plan(tmp_path, DAY, draw=lambda start, _end: start + 7)
    for now in [
        datetime(2026, 10, 4, 1, 7),
        datetime(2026, 10, 3, 1, 8),
        datetime(2026, 10, 3, 8, 0),
    ]:
        assert scheduler.run_due(tmp_path, DAY, now=now, run=run) == 0
    assert calls == []


def test_corrupt_plan_fails_without_running(tmp_path):
    plan = scheduler.ensure_plan(tmp_path, DAY, draw=lambda start, _end: start)
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
    plan = scheduler.ensure_plan(tmp_path, DAY, draw=lambda start, _end: start + 7)
    agent = scheduler.scan_agent(tmp_path, plan)
    assert agent["StartCalendarInterval"] == [
        {"Hour": 1, "Minute": 7},
        {"Hour": 9, "Minute": 7},
        {"Hour": 17, "Minute": 7},
    ]
    assert agent["ProgramArguments"][-3:] == ["run", "--day", "2026-10-03"]
    assert agent["RunAtLoad"] is False
