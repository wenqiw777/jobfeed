"""Dice must be blocked globally, beyond LinkedIn discovery."""

import pytest

from jobfeed.domain.intermediary import blocked_publisher_company


@pytest.mark.parametrize("company", ["Dice", "DICE", " Jobs   via Dice "])
def test_dice_is_globally_blocked(company: str) -> None:
    assert blocked_publisher_company(company)


def test_dice_therapeutics_is_not_the_publisher() -> None:
    assert not blocked_publisher_company("Dice Therapeutics")
