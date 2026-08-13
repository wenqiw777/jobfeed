import Checkbox from "@cloudscape-design/components/checkbox";

import type { JobSummary } from "@/api/queries";
import { VerdictPill } from "@/components/jobs/VerdictPill";
import { formatRelativeAge } from "@/lib/dates";
import { useDensity } from "@/lib/density";
import { cn } from "@/lib/utils";

interface JobRowProps {
  job: JobSummary;
  /** The detail-pane row (single selection), not the checkbox state. */
  isActive: boolean;
  isChecked: boolean;
  /** Plays the decide collapse; the caller omits it under reduced motion. */
  isCollapsing: boolean;
  /** Bulk-selection checkbox; zones without bulk actions (Pipeline) hide it. */
  showCheckbox?: boolean;
  onOpen: (id: string) => void;
  /** Required only when the checkbox shows; checkbox-less zones omit it. */
  onToggle?: (id: string) => void;
}

/**
 * One triage row, both densities (DESIGN.md): compact 32px single line,
 * comfortable 46px two lines. Selection = bg tint + inset ring — side
 * stripes are banned.
 */
export function JobRow({
  job,
  isActive,
  isChecked,
  isCollapsing,
  showCheckbox = true,
  onOpen,
  onToggle,
}: JobRowProps) {
  const { density, rowHeightClass } = useDensity();
  const age = formatRelativeAge(job.discovered_at);
  const score = job.stage_b_fit_score ?? job.stage_a_score;
  const scoreLabel = score === null ? "—" : String(score);

  return (
    <div
      role="listitem"
      data-testid={`job-row-${job.id}`}
      data-active={isActive || undefined}
      data-collapsing={isCollapsing || undefined}
      className={cn("relative", isCollapsing && "origin-top animate-row-collapse")}
    >
      {/* The checkbox is a sibling of the open button (never nested inside
          a button role) overlaying its leading gutter. */}
      {showCheckbox && (
        <div className="absolute left-2.5 top-1/2 z-10 -translate-y-1/2">
          <Checkbox
            checked={isChecked}
            onChange={() => onToggle?.(job.id)}
            ariaLabel={`Select ${job.company} ${job.title}`}
          />
        </div>
      )}
      <button
        type="button"
        aria-label={`Open ${job.company} ${job.title}`}
        onClick={() => onOpen(job.id)}
        className={cn(
          "flex w-full items-center gap-2 border-b border-hairline pr-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent",
          showCheckbox ? "pl-9" : "pl-3",
          rowHeightClass,
          isActive
            ? "bg-accent-bg ring-1 ring-inset ring-accent-border"
            : isChecked
              ? "bg-accent-bg/50"
              : "hover:bg-bg",
        )}
      >
        {density === "compact" ? (
          <CompactBody job={job} isActive={isActive} scoreLabel={scoreLabel} age={age} />
        ) : (
          <ComfortableBody job={job} isActive={isActive} scoreLabel={scoreLabel} age={age} />
        )}
      </button>
    </div>
  );
}

interface BodyProps {
  job: JobSummary;
  isActive: boolean;
  scoreLabel: string;
  age: string;
}

/** Single line: pill · company·title · score (mono) · age (mono). */
function CompactBody({ job, isActive, scoreLabel, age }: BodyProps) {
  return (
    <>
      <VerdictPill verdict={job.verdict} stageBStatus={job.stage_b_status} />
      <span className="min-w-0 flex-1 truncate text-compact">
        <span className={cn("font-medium", isActive ? "text-accent" : "text-ink")}>
          {job.company}
        </span>
        <span className="text-mute"> · {job.title}</span>
      </span>
      <span className="font-mono text-compact text-ink-2">{scoreLabel}</span>
      <span className="w-10 text-right font-mono text-micro text-mute">{age}</span>
    </>
  );
}

/** Two lines: title; company · age. Pill + score trail on the right. */
function ComfortableBody({ job, isActive, scoreLabel, age }: BodyProps) {
  return (
    <>
      <span className="flex min-w-0 flex-1 flex-col justify-center gap-0.5">
        <span
          className={cn(
            "truncate text-body-sm font-medium",
            isActive ? "text-accent" : "text-ink",
          )}
        >
          {job.title}
        </span>
        <span className="flex items-center gap-1.5 text-micro text-mute">
          <span className="truncate">{job.company}</span>
          <span aria-hidden="true">·</span>
          <span className="font-mono">{age}</span>
        </span>
      </span>
      <VerdictPill verdict={job.verdict} stageBStatus={job.stage_b_status} />
      <span className="w-7 text-right font-mono text-compact text-ink-2">{scoreLabel}</span>
    </>
  );
}
