import { render, screen } from "@testing-library/react";

import type { JobDetailResponse } from "@/api/queries";
import { EvaluationSections, TwinsLine } from "@/components/jobs/DetailSections";

type Evaluation = JobDetailResponse["evaluation"];

function evaluation(over: Partial<Evaluation> = {}): Evaluation {
  return {
    summary: "Backend platform role.",
    eligibility_status: "pass",
    eligibility_checks: [{
      kind: "work_authorization",
      requirement: "US work authorization",
      status: "met",
      candidate_evidence: "Authorized to work in the US",
      reason: "Resume and profile agree.",
    }],
    requirements: [{
      requirement: "Production Python",
      priority: "must_have",
      category: "skill",
      match: "strong",
      resume_evidence: "Built Python services for 4 years",
      evidence_type: "explicit",
    }],
    match_score: 20,
    match_tier: "weak_match",
    evaluation_status: "completed",
    one_line: "Canonical weak match.",
    ats_visibility_score: 40,
    evaluator_version: "unified-v2",
    model: "mock-unified",
    ...over,
  };
}

test("renders unified summary, eligibility, and requirement evidence", () => {
  render(<EvaluationSections evaluation={evaluation()} />);
  expect(screen.getByText("Canonical weak match.")).toBeInTheDocument();
  expect(screen.getByText("Backend platform role.")).toBeInTheDocument();
  expect(screen.getByText("Eligibility checks")).toBeInTheDocument();
  expect(screen.getByText("US work authorization")).toBeInTheDocument();
  expect(screen.getByText("Requirement evidence")).toBeInTheDocument();
  expect(screen.getByText("Production Python · must_have · skill")).toBeInTheDocument();
  expect(screen.getByText("strong · Built Python services for 4 years · explicit"))
    .toBeInTheDocument();
});

test("missing optional unified details render no empty sections", () => {
  render(
    <EvaluationSections
      evaluation={evaluation({
        summary: null,
        one_line: null,
        eligibility_checks: [],
        requirements: [],
      })}
    />,
  );
  expect(screen.queryByText("Evaluation summary")).toBeNull();
  expect(screen.queryByText("Eligibility checks")).toBeNull();
  expect(screen.queryByText("Requirement evidence")).toBeNull();
});

test("shows each twin source once when multiple URLs share a platform", () => {
  render(
    <TwinsLine
      twins={[
        { job_id: "2", platform: "linkedin_guest", status: "new", url: "https://example.com/2" },
        { job_id: "3", platform: "linkedin_guest", status: "new", url: "https://example.com/3" },
      ]}
    />,
  );

  expect(screen.getAllByText("linkedin_guest (new)")).toHaveLength(1);
});
