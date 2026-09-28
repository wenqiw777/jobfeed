import pytest

from scripts.prepare_jd_contract_expansion import BUCKETS, select_samples


def test_cohort_excludes_old_jobs_and_repeated_companies():
    entries = [
        {
            "sample_id": i,
            "db_job_id": i,
            "company": f"company-{i}",
            "sampling_bucket": bucket,
        }
        for i, bucket in enumerate(BUCKETS, start=1)
    ]
    entries.insert(
        0,
        {
            "sample_id": 0,
            "db_job_id": 99,
            "company": "old",
            "sampling_bucket": BUCKETS[0],
        },
    )
    chosen = select_samples(entries, {99}, per_bucket=1)
    assert len(chosen) == len(BUCKETS)
    assert {entry["db_job_id"] for entry in chosen}.isdisjoint({99})
    assert len({entry["company"] for entry in chosen}) == len(BUCKETS)


def test_incomplete_sampling_fails_instead_of_filling_with_repeats():
    with pytest.raises(ValueError, match="not enough distinct companies"):
        select_samples([], set(), per_bucket=1)
