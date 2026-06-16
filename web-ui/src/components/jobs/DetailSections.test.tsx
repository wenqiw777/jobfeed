import { render, screen } from "@testing-library/react";

import type { JobDetailResponse } from "@/api/queries";
import { EvaluationSections } from "@/components/jobs/DetailSections";

type Evaluation = JobDetailResponse["evaluation"];
type StageB = NonNullable<Evaluation["stage_b"]>;

function stageB(over: Partial<StageB> = {}): StageB {
  return {
    verdict: "apply",
    fit_score: 90,
    jd_summary: "Backend role.",
    strengths: [{ requirement: "Python", evidence: "8 years" }],
    gaps: [{ requirement: "Go", severity: "minor", mitigation: "ramp" }],
    hooks: { lead_with: "Lead with infra.", supporting: ["Owns CI"], avoid_mentioning: ["Java"] },
    ...over,
  };
}

function evaluation(over: Partial<Evaluation> = {}): Evaluation {
  return {
    stage_a: { score: 80, one_line: "Solid fit." },
    stage_b_status: "completed",
    stage_b: stageB(),
    ...over,
  };
}

test("renders every Stage B block when all fields are present", () => {
  render(<EvaluationSections evaluation={evaluation()} />);
  expect(screen.getByText("Backend role.")).toBeInTheDocument();
  expect(screen.getByText("Strengths")).toBeInTheDocument();
  expect(screen.getByText("Gaps")).toBeInTheDocument();
  expect(screen.getByText("Resume hooks")).toBeInTheDocument();
  expect(screen.getByText("Lead with infra.")).toBeInTheDocument();
});

test("null fit_score does not crash and renders the rest", () => {
  // The header Score component owns the "—" for a null fit; the blocks body
  // must still render without throwing on the null.
  const evalNullFit = evaluation({
    stage_b: stageB({ fit_score: null }),
  });
  render(<EvaluationSections evaluation={evalNullFit} />);
  expect(screen.getByText("Backend role.")).toBeInTheDocument();
  expect(screen.getByText("Strengths")).toBeInTheDocument();
});

test("null jd_summary suppresses the JD summary section (no empty label)", () => {
  render(
    <EvaluationSections evaluation={evaluation({ stage_b: stageB({ jd_summary: null }) })} />,
  );
  expect(screen.queryByText("JD summary")).toBeNull();
  // The other blocks still render.
  expect(screen.getByText("Strengths")).toBeInTheDocument();
  expect(screen.getByText("Resume hooks")).toBeInTheDocument();
});

test("missing strengths/gaps render no Strengths/Gaps sections", () => {
  // The real missing-score shape: empty fit JSON -> server sends [] (and the
  // contract makes them optional, so undefined is also possible here).
  render(
    <EvaluationSections
      evaluation={evaluation({
        stage_b: stageB({ fit_score: null, strengths: [], gaps: [] }),
      })}
    />,
  );
  expect(screen.queryByText("Strengths")).toBeNull();
  expect(screen.queryByText("Gaps")).toBeNull();
  expect(screen.getByText("Backend role.")).toBeInTheDocument();
});

test("a fully unscored Stage B (verdict only) renders no empty sections", () => {
  // Mirrors the verdict-independent fallback for a completed row with an
  // empty fit JSON and no usable summary: an unscored shell with no content.
  const empty: StageB = {
    verdict: "",
    fit_score: null,
    jd_summary: "",
    strengths: [],
    gaps: [],
    hooks: { lead_with: "", supporting: [], avoid_mentioning: [] },
  };
  render(
    <EvaluationSections
      evaluation={{ stage_a: null, stage_b_status: "completed", stage_b: empty }}
    />,
  );
  expect(screen.queryByText("JD summary")).toBeNull();
  expect(screen.queryByText("Strengths")).toBeNull();
  expect(screen.queryByText("Gaps")).toBeNull();
  expect(screen.queryByText("Resume hooks")).toBeNull();
});
