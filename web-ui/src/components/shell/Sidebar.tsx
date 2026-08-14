import SideNavigation, {
  type SideNavigationProps,
} from "@cloudscape-design/components/side-navigation";
import Icon, { type IconProps } from "@cloudscape-design/components/icon";
import { useLocation, useNavigate } from "react-router";

import { ZONES } from "@/components/shell/zones";

/** Cloudscape navigation for the application's product zones. */
export function Sidebar({ collapsed = false }: { collapsed?: boolean }) {
  const location = useLocation();
  const navigate = useNavigate();
  const items: SideNavigationProps.Item[] = [
    ...ZONES.map((zone) => ({
      type: "link" as const,
      text: zone.label,
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
