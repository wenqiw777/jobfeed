"""Local NER preserves evidence without claiming eligibility or absence."""

import pytest

from scripts.demo_jd_local_ner import merge_entities, windows


def test_windows_cover_every_character_and_tail():
    text = "0123456789" * 91
    chunks = windows(text, width=100, overlap=25)
    covered = set()
    for offset, chunk in chunks:
        assert text[offset : offset + len(chunk)] == chunk
        covered.update(range(offset, offset + len(chunk)))
    assert covered == set(range(len(text)))
    assert chunks[-1][0] + len(chunks[-1][1]) == len(text)


def test_empty_and_short_windows():
    assert windows("") == []
    assert windows("short") == [(0, "short")]


@pytest.mark.parametrize("width,overlap", [(0, 0), (10, 10), (10, -1)])
def test_invalid_window_parameters(width, overlap):
    with pytest.raises(ValueError):
        windows("text", width, overlap)


def test_offsets_duplicates_and_no_detection():
    text = "prefix Bachelor degree suffix"
    entity = {
        "start": 0,
        "end": 15,
        "text": "Bachelor degree",
        "label": "education degree",
        "score": 0.8,
    }
    merged = merge_entities(text, [(7, [entity]), (7, [{**entity, "score": 0.9}])])
    assert merged == [{**entity, "start": 7, "end": 22, "score": 0.9}]
    assert merge_entities(text, []) == []


def test_reject_invented_evidence():
    with pytest.raises(ValueError, match="evidence"):
        merge_entities(
            "Bachelor",
            [
                (
                    0,
                    [
                        {
                            "start": 0,
                            "end": 8,
                            "text": "Master",
                            "label": "degree",
                            "score": 0.8,
                        }
                    ],
                )
            ],
        )
