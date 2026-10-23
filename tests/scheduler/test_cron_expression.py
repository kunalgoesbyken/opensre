"""Cron lines are read the crontab way, although APScheduler 3.x counts weekdays from 0 = Monday."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from infrastructure.scheduling.scheduler.cron_expression import build_cron_trigger
from infrastructure.scheduling.scheduler.runner import compute_next_run
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind

_SUNDAY_NOON = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


def _fire_days(cron: str) -> set[str]:
    """The weekdays ``cron`` fires on during the two weeks after a Sunday noon."""
    trigger = build_cron_trigger(cron, "UTC")
    days: set[str] = set()
    fire = trigger.get_next_fire_time(None, _SUNDAY_NOON)
    while fire is not None and fire < _SUNDAY_NOON + timedelta(days=14):
        days.add(f"{fire:%a}")
        fire = trigger.get_next_fire_time(fire, fire)
    return days


@pytest.mark.parametrize(
    ("day_of_week", "fires_on"),
    [
        ("1-5", {"Mon", "Tue", "Wed", "Thu", "Fri"}),
        ("0", {"Sun"}),
        ("7", {"Sun"}),
        ("*/2", {"Sun", "Tue", "Thu", "Sat"}),
        # A step after a name range is ignored by APScheduler, so steps become lists.
        ("1-5/2", {"Mon", "Wed", "Fri"}),
        ("mon-fri", {"Mon", "Tue", "Wed", "Thu", "Fri"}),
    ],
)
def test_a_weekday_field_fires_on_its_crontab_days(day_of_week: str, fires_on: set[str]) -> None:
    assert _fire_days(f"0 10 * * {day_of_week}") == fires_on


def _fire_dates(cron: str, start: datetime, count: int) -> list[str]:
    """The first ``count`` fire dates of ``cron`` from ``start``."""
    trigger = build_cron_trigger(cron, "UTC")
    dates: list[str] = []
    fire = trigger.get_next_fire_time(None, start)
    while fire is not None and len(dates) < count:
        dates.append(f"{fire:%Y-%m-%d}")
        fire = trigger.get_next_fire_time(fire, fire)
    return dates


@pytest.mark.parametrize(
    ("cron", "fires_on"),
    [
        # The 1st of the month or any Monday, not only a Monday the 1st (2027-02-01).
        ("0 9 1 * 1", ["2026-10-05", "2026-10-12", "2026-10-19", "2026-10-26", "2026-11-01"]),
        # Friday the 13th style: the 13th or any Friday.
        ("0 9 13 * 5", ["2026-10-09", "2026-10-13", "2026-10-16", "2026-10-23", "2026-10-30"]),
        # A day both fields match fires once.
        ("0 9 1-7 * 0", ["2026-10-04", "2026-10-05", "2026-10-06", "2026-10-07", "2026-10-11"]),
    ],
)
def test_a_restricted_day_and_weekday_fire_on_either_like_crontab(
    cron: str, fires_on: list[str]
) -> None:
    assert _fire_dates(cron, _SUNDAY_NOON - timedelta(hours=12), 5) == fires_on


def test_the_month_still_limits_both_day_fields() -> None:
    # June 1st or a Monday in June; never a Monday in another month.
    assert _fire_dates("0 9 1 6 1", _SUNDAY_NOON, 3) == ["2027-06-01", "2027-06-07", "2027-06-14"]


@pytest.mark.parametrize(
    ("cron", "fires_on"),
    [
        # A field starting with ``*`` is unrestricted to crontab, even with a step,
        # so the other field still has to match: the 1st only on even weekdays.
        ("0 9 1 * */2", ["2026-11-01", "2026-12-01", "2027-04-01"]),
        ("0 9 */2 * 1", ["2026-10-05", "2026-10-19", "2026-11-09"]),
    ],
)
def test_a_star_day_field_keeps_both_fields_required(cron: str, fires_on: list[str]) -> None:
    assert _fire_dates(cron, _SUNDAY_NOON, 3) == fires_on


def test_a_restricted_day_and_weekday_are_both_validated() -> None:
    with pytest.raises(ValueError, match="0-7"):
        build_cron_trigger("0 10 1 * 8", "UTC")
    with pytest.raises(ValueError, match="invalid cron expression"):
        build_cron_trigger("0 10 32 * 1", "UTC")


def test_a_weekday_number_above_7_is_rejected_before_it_is_stored() -> None:
    with pytest.raises(ValueError, match="0-7"):
        build_cron_trigger("0 10 * * 8", "UTC")


def test_the_stored_next_run_skips_the_weekend() -> None:
    """``next_run`` is what the store keeps and the loop list shows; it must agree with the trigger."""
    # Arrange: a weekday loop, asked after Friday's slot has passed
    task = ScheduledTask(
        kind=TaskKind.MANUAL_LOOP,
        cron="0 8 * * 1-5",
        timezone="UTC",
        provider=Provider.INTERACTIVE_SHELL,
    )
    friday_after_slot = datetime(2026, 10, 9, 9, 0, tzinfo=UTC)

    # Act
    next_run = compute_next_run(task, friday_after_slot)

    # Assert: Monday, not Saturday
    assert next_run == "2026-10-12T08:00:00+00:00"
