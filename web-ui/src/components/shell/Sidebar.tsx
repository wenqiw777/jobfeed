import SideNavigation, {
  type SideNavigationProps,
} from "@cloudscape-design/components/side-navigation";
import { useLocation, useNavigate } from "react-router";

import { useAttention, useJobsTabCounts, workflowAttentionTotal } from "@/api/queries";
import { ZONES, type Zone } from "@/components/shell/zones";

function useBadgeCounts(): Record<NonNullable<Zone["badge"]>, number | undefined> {
  const tabCounts = useJobsTabCounts();
  const attention = useAttention();
  return {
    queue: tabCounts.data?.tab_counts.queue,
    attention: attention.data ? workflowAttentionTotal(attention.data) : undefined,
  };
}

/** Cloudscape navigation for the application's product zones. */
export function Sidebar({ collapsed = false }: { collapsed?: boolean }) {
  const badges = useBadgeCounts();
  const location = useLocation();
  const navigate = useNavigate();
  const items: SideNavigationProps.Item[] = [
    ...ZONES.map((zone) => ({
      type: "link" as const,
      text: zoneText(zone, badges),
      href: zone.path,
      icon: <ZoneIcon zone={zone.path} />,
    })),
    { type: "divider" as const },
    {
      type: "link" as const,
      text: "Settings",
      href: "/setup",
      icon: <span aria-hidden="true">⚙</span>,
    },
  ];

  return (
    <SideNavigation
      collapsed={collapsed}
      activeHref={location.pathname}
      header={{ href: "/triage", text: "Workspace" }}
      items={items}
      onFollow={(event) => {
        event.preventDefault();
        navigate(event.detail.href);
      }}
    />
  );
}

function zoneText(
  zone: Zone,
  badges: Record<NonNullable<Zone["badge"]>, number | undefined>,
) {
  if (zone.badge === null) return zone.label;
  const count = badges[zone.badge];
  return count ? `${zone.label} ${count}` : zone.label;
}

function ZoneIcon({ zone }: { zone: string }) {
  const glyph: Record<string, string> = {
    "/triage": "◆",
    "/pipeline": "↗",
    "/library": "▤",
    "/insights": "◫",
    "/runs": "▶",
    "/performance": "⌁",
    "/sources": "◎",
  };
  return <span aria-hidden="true">{glyph[zone]}</span>;
}
