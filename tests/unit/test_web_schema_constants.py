"""Drift guards: web Literal aliases must mirror the domain/service constants.

``web/schemas/jobs_list.py`` redeclares the tab and sort vocabularies as
``Literal`` types so FastAPI rejects unknown values at the edge. If those
Literals drift from ``VALID_TABS`` / ``VALID_SORTS``, a value the schema
accepts would blow up later as a runtime 500 — fail a unit test instead.
"""

from __future__ import annotations

from typing import get_args

from jobfeed.domain.models_views import VALID_TABS
from jobfeed.domain.status import STATUS_VALUES
from jobfeed.services._jobs_view_sort import LIBRARY_SORT_KEYS, VALID_SORTS
from jobfeed.web.schemas.jobs_list import SortName, TabName
from jobfeed.web.schemas.workflow import StatusName


def test_tab_name_literal_matches_valid_tabs() -> None:
    """``TabName`` must stay in lockstep with ``VALID_TABS``."""
    assert set(get_args(TabName)) == set(VALID_TABS)


def test_sort_name_literal_matches_valid_sorts() -> None:
    """``SortName`` must stay in lockstep with ``VALID_SORTS``."""
    assert set(get_args(SortName)) == set(VALID_SORTS)


def test_library_sort_keys_cover_valid_sorts() -> None:
    """Every accepted sort name must have a sort-key implementation."""
    assert set(LIBRARY_SORT_KEYS) == set(VALID_SORTS)


def test_status_name_literal_matches_status_values() -> None:
    """``StatusName`` must stay in lockstep with domain ``STATUS_VALUES``."""
    assert set(get_args(StatusName)) == set(STATUS_VALUES)
