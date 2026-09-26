"""Respect the selected 1000/700 scan budgets within a bounded limit."""

import pytest
from pydantic import ValidationError

from jobfeed.config_sources import SourcesBoardExtensionConfig


@pytest.mark.parametrize("count", [700, 1000])
def test_selected_scan_budget_is_valid(count):
    assert SourcesBoardExtensionConfig(max_jobs=count).max_jobs == count


def test_scan_budget_above_limit_is_rejected():
    with pytest.raises(ValidationError):
        SourcesBoardExtensionConfig(max_jobs=1001)
