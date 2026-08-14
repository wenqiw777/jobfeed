import SideNavigation, {
  type SideNavigationProps,
} from "@cloudscape-design/components/side-navigation";
import Icon, { type IconProps } from "@cloudscape-design/components/icon";
import { useLocation, useNavigate } from "react-router";

import { useJobsTabCounts } from "@/api/queries";
import { ZONES, type Zone } from "@/components/shell/zones";

function useBadgeCounts(): Record<NonNullable<Zone["badge"]>, number | undefined> {
  const tabCounts = useJobsTabCounts();
  return {
    queue: tabCounts.data?.tab_counts.queue,
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
      icon: <Icon name="settings" />,
    },
  ];

  return (
    <SideNavigation
      collapsed={collapsed}
      activeHref={location.pathname}
      header={{ href: "/triage", text: "Jobfeed" }}
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
  const icon: Record<string, IconProps.Name> = {
    "/triage": "filter",
    "/library": "folder-open",
    "/insights": "view-full",
    "/runs": "play",
    "/performance": "status-info",
    "/sources": "globe",
  };
  return <Icon name={icon[zone] ?? "status-info"} />;
}
