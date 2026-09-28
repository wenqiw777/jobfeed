from datetime import UTC, datetime
from types import SimpleNamespace

from jobfeed.domain.enrichment_retry import retry_policy
from jobfeed.domain.models import PipelineRun
from jobfeed.services.run_orchestration import _record_failure


def test_permission_url_digits_do_not_become_http_status():
    code, _ = retry_policy(
        "Cannot access contents of url "
        '"https://joinbytedance.com/7667303429264115973". '
        "Extension manifest must request permission to access this host.",
        datetime.now(UTC),
    )
    assert code == "missing_permission"


def test_structured_script_error_is_not_a_missing_description():
    code, _ = retry_policy("unavailable", datetime.now(UTC), code="script_unavailable")
    assert code == "script_unavailable"


def test_parallel_failure_attributes_failed_sources_not_last_completed():
    run = PipelineRun(run_id="test", source="all", started_at=datetime.now(UTC))
    run.scan_source = "jobright"
    run.scan_progress = {
        "linkedin": {"phase": "failed"},
        "speedyapply": {"phase": "failed"},
        "jobright": {"phase": "completed"},
    }
    _record_failure(
        SimpleNamespace(run=run, kind="scan"),
        RuntimeError("Scan has failed source work; inspect source progress"),
    )
    assert run.failed_source == "linkedin, speedyapply"


def test_permission_has_no_timed_native_retry():
    now = datetime.now(UTC)
    _, retry_at = retry_policy("Missing extension permission", now)
    assert retry_at == datetime.max.replace(tzinfo=UTC)
