"""Observed application chains require independently verified target job facts."""

import asyncio
import json
from datetime import UTC, datetime

import httpx
import pytest

from jobfeed.adapters.sources import application_routes
from jobfeed.adapters.sources.application_routes import (
    ApplicationRouteResolver,
    public_application_url,
)
from jobfeed.domain.application_route import RenderedApplicationPage
from jobfeed.domain.models import JobPosting

WRAPPER = "https://careers.acme.com/jobs/123"
ATS = "https://boards.greenhouse.io/acme/jobs/123"
MAX_PAGES = 6
HTTP_SLOTS = 2
BROWSER_SLOTS = 3
BODY = (
    "Build distributed software services and scalable machine learning systems. "
    "Implement production APIs with Python and monitor deployment reliability. "
    "Collaborate with engineers to improve testing, observability and performance. "
    "Design data pipelines and evaluate models using real customer feedback. "
    "Own architecture and deployment of maintainable products across teams. "
) * 3


def posting(**changes):
    values = dict(  # noqa: C408 - fixture overrides source fields
        platform="linkedin",
        canonical_id="123",
        title="Software Engineer",
        company="Acme",
        location="Boston",
        discovered_at=datetime.now(UTC),
        url="https://www.linkedin.com/jobs/view/123",
        apply_url=WRAPPER,
        jd_text=BODY,
    )
    values.update(changes)
    return JobPosting(**values)


def page(  # noqa: PLR0913 - each fixture fact can fail independently
    *,
    title="Software Engineer",
    company="Acme",
    req="123",
    body=BODY,
    link=None,
    extra="",
):
    facts = {
        "@type": "JobPosting",
        "title": title,
        "description": body,
        "hiringOrganization": {"name": company},
        "identifier": {"value": req},
    }
    apply = f'<a href="{link}">Apply now</a>' if link else ""
    return (
        f"<main><h1>{title}</h1>{apply}{extra}</main>"
        '<script type="application/ld+json">' + json.dumps(facts) + "</script>"
    )


async def allow(_url):
    return True


async def resolve(routes, job=None, **kwargs):
    calls = []

    async def handler(request):
        calls.append(str(request.url))
        result = routes[str(request.url)]
        if isinstance(result, httpx.HTTPError):
            raise result
        return (
            result
            if isinstance(result, httpx.Response)
            else httpx.Response(200, text=result)
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await ApplicationRouteResolver(
            client=client, url_guard=allow, **kwargs
        )(job or posting())
    return result, calls


async def test_direct_ats_identity_uses_no_network():
    result, calls = await resolve({}, posting(apply_url=ATS))
    assert result.status == "resolved"
    assert result.ats_url == ATS
    assert not calls


async def test_wrapper_target_apply_reaches_verified_ats():
    result, calls = await resolve({WRAPPER: page(link=ATS), ATS: page()})
    assert result.status == "resolved"
    assert result.ats_url == ATS
    assert calls == [WRAPPER, ATS]
    assert result.hops[-1].kind == "target_apply_link"


async def test_relative_apply_base_and_second_intermediate_page():
    next_url = "https://careers.acme.com/redirect/123"
    html = '<base href="https://careers.acme.com/redirect/">' + page(link="123")
    result, calls = await resolve(
        {WRAPPER: html, next_url: page(link=ATS), ATS: page()}
    )
    assert result.status == "resolved"
    assert calls == [WRAPPER, next_url, ATS]


async def test_redirect_chain_does_not_skip_final_verification():
    result, _ = await resolve(
        {
            WRAPPER: httpx.Response(302, headers={"location": ATS}),
            ATS: page(company="Other"),
        }
    )
    assert result.status == "unresolved"
    assert result.ats_url is None


@pytest.mark.parametrize(
    "changes",
    [
        {"company": "Other"},
        {"req": "999"},
        {"title": "Data Engineer"},
        {"body": "A completely different job description."},
    ],
)
async def test_wrong_target_facts_do_not_merge(changes):
    result, _ = await resolve({WRAPPER: page(link=ATS), ATS: page(**changes)})
    assert result.status == "unresolved"
    assert result.ats_url is None


async def test_recommendation_link_and_board_home_are_not_candidates():
    extra = (
        f'<section class="recommended"><a href="{ATS}">Apply</a></section>'
        '<a href="https://boards.greenhouse.io/acme">All careers</a>'
    )
    result, calls = await resolve({WRAPPER: page(extra=extra)})
    assert result.status != "resolved"
    assert calls == [WRAPPER]


async def test_multiple_requisitions_ambiguous():
    extra = '<a href="https://boards.greenhouse.io/acme/jobs/999">Apply</a>'
    result, calls = await resolve({WRAPPER: page(link=ATS, extra=extra)})
    assert result.status == "ambiguous"
    assert calls == [WRAPPER]


async def test_matching_embedded_job_is_verified_but_iframe_alone_is_not():
    result, _ = await resolve(
        {WRAPPER: page(extra=f'<iframe src="{ATS}"></iframe>'), ATS: page()}
    )
    assert result.status == "resolved"
    result, _ = await resolve({WRAPPER: f'<iframe src="{ATS}"></iframe>'})
    assert result.status != "resolved"


async def test_dynamic_reader_supplies_same_checked_facts():
    async def chrome(job, url):
        assert job.company == "Acme"
        assert url == WRAPPER
        return RenderedApplicationPage(url=url, html=page(link=ATS))

    result, _ = await resolve(
        {WRAPPER: "<main>Loading</main>", ATS: page()}, chrome_reader=chrome
    )
    assert result.status == "resolved"


async def test_loops_limits_and_missing_dynamic_reader():
    result, _ = await resolve(
        {WRAPPER: httpx.Response(302, headers={"location": WRAPPER})}
    )
    assert result.reason == "navigation_loop"
    result, _ = await resolve({WRAPPER: "x" * 2_000_001})
    assert result.reason == "page_size_limit"
    result, _ = await resolve({WRAPPER: "<main>Loading</main>"})
    assert result.status == "blocked"


async def test_guard_blocks_private_redirect_before_request():
    calls = []

    async def guard(url):
        calls.append(url)
        return "127.0.0.1" not in url

    async def handler(request):
        assert str(request.url) == WRAPPER
        return httpx.Response(302, headers={"location": "http://127.0.0.1/"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await ApplicationRouteResolver(client=client, url_guard=guard)(
            posting()
        )
    assert result.status == "blocked"
    assert calls[-1] == "http://127.0.0.1/"


async def test_cancellation_propagates():
    started = asyncio.Event()

    async def handler(_request):
        started.set()
        await asyncio.Event().wait()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        task = asyncio.create_task(
            ApplicationRouteResolver(client=client, url_guard=allow)(posting())
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.parametrize(
    "ats,req",
    [
        (
            "https://acme.wd5.myworkdayjobs.com/external/job/Boston/Engineer_J00179159",
            "J00179159",
        ),
        ("https://jobs.lever.co/acme/12345678-1234-1234-1234-123456789abc", "R123"),
        ("https://jobs.ashbyhq.com/acme/12345678-1234-1234-1234-123456789abc", "R123"),
        (ATS + "?source=LinkedIn", "R123"),
    ],
)
async def test_supported_vendors_and_tracking_keep_verified_route(ats, req):
    result, calls = await resolve(
        {WRAPPER: page(link=ats, req=req), ats: page(req=req)}
    )
    assert result.status == "resolved"
    assert result.ats_url == ats
    assert calls[-1] == ats


async def test_workday_observed_page_and_vendor_endpoint_verify_target():
    ats = "https://acme.wd5.myworkdayjobs.com/external/job/Boston/Engineer_J00179159"
    api = "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/external/job/Boston/Engineer_J00179159"
    result, calls = await resolve(
        {
            WRAPPER: page(link=ats, req="J00179159"),
            ats: "<html><script>postingAvailable: true</script></html>",
            api: json.dumps(
                {
                    "jobPostingInfo": {
                        "title": "Software Engineer",
                        "jobDescription": BODY,
                        "jobReqId": "J00179159",
                        "company": "Acme",
                    }
                }
            ),
        }
    )
    assert result.status == "resolved"
    assert calls == [WRAPPER, ats, api]


async def test_navigation_edge_bound_never_fetches_sixth_hop():
    routes = {}
    for index in range(7):
        url = WRAPPER if index == 0 else f"https://careers.acme.com/hop/{index}"
        routes[url] = httpx.Response(
            302, headers={"location": f"https://careers.acme.com/hop/{index + 1}"}
        )
    result, calls = await resolve(routes)
    assert result.reason == "navigation_edge_limit"
    assert len(calls) == MAX_PAGES
    assert len(result.hops) == MAX_PAGES


async def test_http_budget_is_bounded(monkeypatch):
    monkeypatch.setattr(application_routes, "_HTTP_BUDGET", 0.01)

    async def handler(_request):
        await asyncio.Event().wait()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await ApplicationRouteResolver(client=client, url_guard=allow)(
            posting()
        )
    assert result.status == "failed"
    assert result.reason == "http_time_budget"


async def test_public_guard_rejects_literal_private_and_resolved_private(monkeypatch):
    for url in [
        "http://127.0.0.1/",
        "https://[::1]/",
        "http://169.254.169.254/",
        "http://localhost/",
        "https://user:pass@example.com/",
        "file:///a",
    ]:
        assert not await public_application_url(url)

    async def private_dns(*_args, **_kwargs):
        return [(2, 1, 6, "", ("10.1.2.3", 443))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", private_dns)
    assert not await public_application_url("https://public-looking.example.com/")


async def test_http_two_slots_do_not_reduce_three_browser_calls():
    active_http = active_browser = peak_http = peak_browser = 0
    all_browsers = asyncio.Event()
    release_browser = asyncio.Event()

    async def handler(_request):
        nonlocal active_http, peak_http
        active_http += 1
        peak_http = max(peak_http, active_http)
        await asyncio.sleep(0.01)
        active_http -= 1
        return httpx.Response(200, text="<main>Loading</main>")

    async def chrome(_job, url):
        nonlocal active_browser, peak_browser
        active_browser += 1
        peak_browser = max(peak_browser, active_browser)
        if active_browser == BROWSER_SLOTS:
            all_browsers.set()
        await release_browser.wait()
        active_browser -= 1
        return RenderedApplicationPage(url, page())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        resolver = ApplicationRouteResolver(
            client=client, url_guard=allow, chrome_reader=chrome
        )
        tasks = [asyncio.create_task(resolver(posting())) for _ in range(3)]
        try:
            await asyncio.wait_for(all_browsers.wait(), timeout=1)
            release_browser.set()
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    assert peak_http == HTTP_SLOTS
    assert peak_browser == BROWSER_SLOTS


async def test_same_ats_identity_with_tracking_aliases_is_one_candidate():
    extra = f'<a href="{ATS}?source=LinkedIn">Apply</a>'
    result, _ = await resolve({WRAPPER: page(link=ATS, extra=extra), ATS: page()})
    assert result.status == "resolved"


async def test_contradictory_structured_employer_is_not_overridden_by_dom():
    html = page(company="Other", extra=f'<p>Acme</p><a href="{ATS}">Apply</a>')
    result, calls = await resolve({WRAPPER: html})
    assert result.status == "unresolved"
    assert calls == [WRAPPER]


async def test_chrome_budget_and_errors_have_durable_failure_reason(monkeypatch):
    monkeypatch.setattr(application_routes, "_CHROME_BUDGET", 0.01)

    async def chrome(_job, _url):
        await asyncio.Event().wait()

    result, _ = await resolve({WRAPPER: "Loading"}, chrome_reader=chrome)
    assert result.status == "blocked"
    assert result.reason == "chrome_time_budget"

    async def broken(_job, _url):
        raise RuntimeError("Extension unavailable")

    result, _ = await resolve({WRAPPER: "Loading"}, chrome_reader=broken)
    assert result.status == "failed"
    assert result.reason == "chrome_reader_error"


async def test_chrome_render_time_does_not_consume_http_budget(monkeypatch):
    monkeypatch.setattr(application_routes, "_HTTP_BUDGET", 0.03)

    async def chrome(_job, url):
        await asyncio.sleep(0.04)
        return RenderedApplicationPage(url, page(link=ATS))

    result, calls = await resolve(
        {WRAPPER: "Loading", ATS: page()}, chrome_reader=chrome
    )
    assert result.status == "resolved"
    assert calls == [WRAPPER, ATS]


async def test_current_job_sidebar_apply_outside_main_is_selected():
    html = page() + f'<aside class="job-sidebar"><a href="{ATS}">Apply now</a></aside>'
    result, calls = await resolve({WRAPPER: html, ATS: page()})
    assert result.status == "resolved"
    assert calls == [WRAPPER, ATS]


async def test_other_job_sidebar_requisition_is_not_current_job_apply():
    html = page() + (
        '<aside class="job-sidebar" data-job-id="999">'
        f'<a href="{ATS}">Apply now</a></aside>'
    )
    result, calls = await resolve({WRAPPER: html})
    assert result.status != "resolved"
    assert calls == [WRAPPER]


async def test_chrome_observer_failure_status_and_reason_are_preserved():
    async def chrome(_job, _url):
        raise application_routes.ApplicationPageReadError(
            "blocked", "missing_extension_permission"
        )

    result, _ = await resolve({WRAPPER: "Loading"}, chrome_reader=chrome)
    assert result.status == "blocked"
    assert result.reason == "missing_extension_permission"


async def test_intermediate_requisition_conflict_is_rejected():
    intermediate = "https://careers.acme.com/redirect/123"
    result, _ = await resolve(
        {
            WRAPPER: page(link=intermediate),
            intermediate: page(link=ATS, req="999"),
            ATS: page(),
        }
    )
    assert result.status == "unresolved"
    assert result.reason == "requisition_mismatch"


async def test_small_shared_description_section_is_not_full_source_corroboration():
    shared = BODY[:400]
    unrelated = (
        "Different responsibilities for a separate financial reporting role. " * 30
    )
    result, _ = await resolve(
        {WRAPPER: page(link=ATS), ATS: page(body=shared)},
        posting(jd_text=shared + unrelated),
    )
    assert result.status == "unresolved"


async def test_browser_budget_is_cumulative_across_intermediate_pages(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(application_routes, "monotonic", lambda: clock[0])
    second = "https://careers.acme.com/redirect/second"
    third = "https://careers.acme.com/redirect/third"
    destinations = {WRAPPER: second, second: third, third: ATS}
    browser_calls = []

    async def chrome(_job, url):
        browser_calls.append(url)
        clock[0] += 15.0
        return RenderedApplicationPage(url, page(link=destinations[url]))

    result, calls = await resolve(
        {WRAPPER: "Loading", second: "Loading", third: "Loading", ATS: page()},
        chrome_reader=chrome,
    )
    assert result.status == "blocked"
    assert result.reason == "chrome_time_budget"
    assert browser_calls == [WRAPPER, second]
    assert ATS not in calls


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(403),
        httpx.ConnectError("public endpoint unavailable"),
    ],
)
async def test_http_blocked_owned_page_without_apply_renders_once(response):
    browser_calls = []

    async def chrome(_job, url):
        browser_calls.append(url)
        return RenderedApplicationPage(url, page())

    result, _ = await resolve(
        {WRAPPER: response},
        chrome_reader=chrome,
    )
    assert result.status == "unresolved"
    assert result.reason == "target_apply_link_missing"
    assert browser_calls == [WRAPPER]


@pytest.mark.parametrize("action_suffix", ["/apply", "/apply/"])
async def test_workday_apply_action_reads_job_details_without_action_suffix(
    action_suffix,
):
    target = (
        "https://acme.wd5.myworkdayjobs.com/External/job/Boston/Engineer_R123"
        + action_suffix
        + "?source=Applied_LinkedIn"
    )
    cxs = (
        "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/External"
        "/job/Boston/Engineer_R123"
    )
    result, calls = await resolve(
        {
            WRAPPER: page(req="R123", link=target),
            target: "<h1>Careers at Acme</h1>",
            cxs + action_suffix: httpx.Response(422),
            cxs: httpx.Response(
                200,
                json={
                    "jobPostingInfo": {
                        "title": "Software Engineer",
                        "jobDescription": BODY,
                        "jobReqId": "R123",
                    }
                },
            ),
        }
    )
    assert result.status == "resolved"
    assert result.ats_url == target
    assert calls == [WRAPPER, target, cxs]
