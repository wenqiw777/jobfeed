import type { ReactNode } from "react";

import type { JobDetailResponse } from "@/api/queries";

type Evaluation = JobDetailResponse["evaluation"];
type Twins = JobDetailResponse["twins"];

export function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <h3 className="mb-1.5 text-label uppercase tracking-wide text-mute">{children}</h3>
  );
}

/** Same posting on other platforms (twin cluster), with each twin's status. */
export function TwinsLine({ twins }: { twins: Twins }) {
  if (twins.length === 0) {
    return null;
  }
  return (
    <p className="mt-1.5 flex flex-wrap items-center gap-x-1.5 text-micro text-mute">
      <span>also seen on:</span>
      {twins.map((twin) => (
        <span key={twin.job_id} className="inline-flex items-center gap-1">
          <a href={twin.url} target="_blank" rel="noreferrer" className="hover:underline">
            {twin.platform}
          </a>
          <span className="font-mono">({twin.status})</span>
        </span>
      ))}
    </p>
  );
}

/** Stage A one-line + the Stage B blocks (summary, fit, hooks). */
export function EvaluationSections({ evaluation }: { evaluation: Evaluation }) {
  const stageA = evaluation.stage_a;
  const stageB = evaluation.stage_b;
  if (stageA === null && stageB === null) {
    return null;
  }
  return (
    <>
      {stageA !== null && (
        <section className="border-b border-hairline px-4 py-3">
          <p className="text-body-sm italic text-ink-2">{stageA.one_line}</p>
        </section>
      )}
      {stageB !== null && <StageBBlocks stageB={stageB} />}
    </>
  );
}

function StageBBlocks({ stageB }: { stageB: NonNullable<Evaluation["stage_b"]> }) {
  // Real-data rows can null/omit any of these blocks (the contract now makes
  // jd_summary, strengths, gaps, and hooks optional). Render only the blocks
  // that carry content so an unscored/partial row never shows empty sections.
  const strengths = stageB.strengths ?? [];
  const gaps = stageB.gaps ?? [];
  const summary = stageB.jd_summary ?? "";
  return (
    <>
      {summary !== "" && (
        <section className="border-b border-hairline px-4 py-3">
          <SectionLabel>JD summary</SectionLabel>
          <p className="text-body-sm text-ink-2">{summary}</p>
        </section>
      )}
      {strengths.length > 0 && (
        <section className="border-b border-hairline px-4 py-3">
          <SectionLabel>Strengths</SectionLabel>
          <ul className="flex flex-col gap-1.5">
            {strengths.map((item) => (
              <li key={item.requirement} className="text-body-sm">
                <span className="font-medium text-ink">{item.requirement}</span>
                <span className="text-mute"> — {item.evidence}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
      {gaps.length > 0 && (
        <section className="border-b border-hairline px-4 py-3">
          <SectionLabel>Gaps</SectionLabel>
          <ul className="flex flex-col gap-1.5">
            {gaps.map((item) => (
              <li key={item.requirement} className="text-body-sm">
                <span className="font-medium text-ink">{item.requirement}</span>
                <span className="text-mute">
                  {" "}
                  · {item.severity} — {item.mitigation}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
      <HooksBlock hooks={stageB.hooks} />
    </>
  );
}

function HooksBlock({ hooks }: { hooks: NonNullable<Evaluation["stage_b"]>["hooks"] }) {
  // The lead hook carries the section; with no hooks at all (real-data rows
  // can omit the block), render nothing rather than an empty Resume hooks box.
  if (hooks === undefined || hooks.lead_with === "") {
    return null;
  }
  return (
    <section className="border-b border-hairline px-4 py-3">
      <SectionLabel>Resume hooks</SectionLabel>
      <p className="rounded-panel bg-accent-bg p-2.5 text-body-sm text-ink">{hooks.lead_with}</p>
      {hooks.supporting.length > 0 && (
        <>
          <h4 className="mb-1 mt-2 text-micro font-medium uppercase tracking-wide text-mute">
            Supporting
          </h4>
          <ul className="flex flex-col gap-1 text-body-sm text-ink-2">
            {hooks.supporting.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </>
      )}
      {hooks.avoid_mentioning.length > 0 && (
        <>
          <h4 className="mb-1 mt-2 text-micro font-medium uppercase tracking-wide text-mute">
            Avoid mentioning
          </h4>
          <ul className="flex flex-col gap-1 text-body-sm text-mute">
            {hooks.avoid_mentioning.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
