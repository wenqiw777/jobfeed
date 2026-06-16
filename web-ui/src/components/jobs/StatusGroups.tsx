import { ChevronDown, ChevronRight } from "lucide-react";

import type { JobSummary } from "@/api/queries";
import { JobRow } from "@/components/jobs/JobRow";

export type GroupKey = "applied" | "interviewing" | "offer" | "closed";

export interface StatusGroup {
  key: GroupKey;
  label: string;
  jobs: JobSummary[];
}

// Closed is a DISPLAY group (plan D13), not a status: rejected + ghosted.
const GROUP_ORDER: { key: GroupKey; label: string; statuses: readonly string[] }[] = [
  { key: "applied", label: "Applied", statuses: ["applied"] },
  { key: "interviewing", label: "Interviewing", statuses: ["interviewing"] },
  { key: "offer", label: "Offer", statuses: ["offer"] },
  { key: "closed", label: "Closed", statuses: ["rejected", "ghosted"] },
];

/**
 * Group pipeline rows per D13, preserving the API order inside each
 * group. Empty groups are dropped (like empty attention buckets — no
 * dead headers). O(groups x jobs) over the 10^2-scale pipeline corpus.
 */
export function groupJobs(jobs: JobSummary[]): StatusGroup[] {
  return GROUP_ORDER.map(({ key, label, statuses }) => ({
    key,
    label,
    jobs: jobs.filter((job) => statuses.includes(job.status)),
  })).filter((group) => group.jobs.length > 0);
}

interface StatusGroupsProps {
  groups: StatusGroup[];
  collapsedKeys: ReadonlySet<GroupKey>;
  selectedId: string | null;
  onToggleGroup: (key: GroupKey) => void;
  onOpen: (id: string) => void;
}

/**
 * Collapsible status-grouped list reusing JobRow (checkbox hidden —
 * Pipeline has no bulk actions). Not virtualized: pipeline corpora are
 * 10^2-scale and group headers break the uniform-row-height assumption.
 */
export function StatusGroups({
  groups,
  collapsedKeys,
  selectedId,
  onToggleGroup,
  onOpen,
}: StatusGroupsProps) {
  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      {groups.map((group) => (
        <GroupSection
          key={group.key}
          group={group}
          isCollapsed={collapsedKeys.has(group.key)}
          selectedId={selectedId}
          onToggle={() => onToggleGroup(group.key)}
          onOpen={onOpen}
        />
      ))}
    </div>
  );
}

interface GroupSectionProps {
  group: StatusGroup;
  isCollapsed: boolean;
  selectedId: string | null;
  onToggle: () => void;
  onOpen: (id: string) => void;
}

function GroupSection({ group, isCollapsed, selectedId, onToggle, onOpen }: GroupSectionProps) {
  const Chevron = isCollapsed ? ChevronRight : ChevronDown;
  return (
    <section>
      <button
        type="button"
        aria-expanded={!isCollapsed}
        onClick={onToggle}
        className="flex w-full items-center gap-1.5 border-b border-hairline bg-bg px-3 py-1.5 text-left hover:bg-accent-bg/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent"
      >
        <Chevron aria-hidden="true" className="h-3.5 w-3.5 text-mute" />
        <span className="text-label uppercase tracking-wide text-ink-2">{group.label}</span>
        <span className="font-mono text-micro text-mute">{group.jobs.length}</span>
      </button>
      {!isCollapsed && (
        <div role="list" aria-label={group.label}>
          {group.jobs.map((job) => (
            <JobRow
              key={job.id}
              job={job}
              isActive={job.id === selectedId}
              isChecked={false}
              isCollapsing={false}
              showCheckbox={false}
              onOpen={onOpen}
            />
          ))}
        </div>
      )}
    </section>
  );
}
