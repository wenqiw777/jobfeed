from scripts.demo_seniority_cascade import decide, rule


def job(title, body):
    return {"title": title, "jd_text": body}


def test_member_of_staff_is_not_staff_engineer():
    assert rule(job("Member of Technical Staff", ""))["decision"] == "uncertain"


def test_conflicting_title_goes_to_review():
    assert (
        rule(job("Senior Software Engineer", "Requirements\n2 years of experience"))[
            "decision"
        ]
        == "uncertain"
    )


def test_explicit_required_years_and_preferred():
    assert (
        rule(job("Software Engineer", "Requirements\n5 years of experience"))[
            "decision"
        ]
        == "block"
    )
    assert (
        rule(job("Software Engineer", "Preferred\n5 years of experience"))["decision"]
        == "uncertain"
    )


def test_bad_quote_and_failure_pass():
    j = job("Software Engineer", "We build APIs.")
    assert decide(j, None)["decision"] == "keep"
    assert (
        decide(
            j, {("decision"): ("block"), ("reason"): ("x"), ("evidence"): [("5 years")]}
        )[("decision")]
        == "keep"
    )


def test_supported_block():
    j = job("Software Engineer", "You must have 6+ years building APIs.")
    assert (
        decide(
            j,
            {
                ("decision"): ("block"),
                ("reason"): ("6 years"),
                ("evidence"): [("6+ years building APIs")],
            },
        )["decision"]
        == "block"
    )


def test_luna_conflicting_senior_and_three_years_pass():
    j = job("Software Engineer", "Senior Engineer\n3+ years of experience")
    assert (
        decide(
            j,
            {
                ("decision"): ("block"),
                ("reason"): ("senior"),
                ("evidence"): [("Senior Engineer")],
            },
        )["decision"]
        == "keep"
    )


def test_multilevel_title_is_not_blocked():
    j = job(
        "Principal AI Engineer",
        (
            "Multiple Openings Levels Available\nAI Engineer | "
            "Senior AI Engineer | Principal AI Engineer"
        ),
    )
    assert rule(j)["decision"] == "uncertain"
