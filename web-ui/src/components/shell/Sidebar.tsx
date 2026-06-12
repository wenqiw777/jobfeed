import { NavLink } from "react-router";

import { useAttention, useJobsTabCounts, workflowAttentionTotal } from "@/api/queries";
import { ZONES, type Zone } from "@/components/shell/zones";
import { cn } from "@/lib/utils";

function useBadgeCounts(): Record<NonNullable<Zone["badge"]>, number | undefined> {
  const tabCounts = useJobsTabCounts();
  const attention = useAttention();
  return {
    // Triage badge = the queue tab's global count (neutral request, A4).
    queue: tabCounts.data?.tab_counts["queue"],
    attention: attention.data ? workflowAttentionTotal(attention.data) : undefined,
  };
}

export function Sidebar() {
  const badges = useBadgeCounts();

  return (
    <aside className="flex w-44 shrink-0 flex-col border-r border-border bg-bg">
      <div className="px-4 pb-2 pt-4">
        <span className="text-h3 tracking-tight text-ink">jobfeed</span>
      </div>
      <nav aria-label="Zones" className="flex flex-col gap-0.5 px-2">
        {ZONES.map((zone) => (
          <NavLink
            key={zone.path}
            to={zone.path}
            className={({ isActive }) =>
              cn(
                "flex items-center justify-between rounded-control px-2.5 py-1.5 text-body-sm font-medium transition-colors",
                isActive
                  ? "bg-accent-bg text-accent"
                  : "text-ink-2 hover:bg-hairline hover:text-ink",
              )
            }
          >
            {({ isActive }) => (
              <>
                <span>{zone.label}</span>
                <ZoneBadge zone={zone} badges={badges} isActive={isActive} />
              </>
            )}
          </NavLink>
        ))}
      </nav>
    </aside>
  );
}

function ZoneBadge({
  zone,
  badges,
  isActive,
}: {
  zone: Zone;
  badges: Record<NonNullable<Zone["badge"]>, number | undefined>;
  isActive: boolean;
}) {
  if (zone.badge === null) {
    return null;
  }
  const count = badges[zone.badge];
  if (count === undefined || count === 0) {
    return null;
  }
  return (
    <span
      className={cn(
        "font-mono text-micro",
        isActive ? "text-accent" : "text-mute",
      )}
    >
      {count}
    </span>
  );
}
