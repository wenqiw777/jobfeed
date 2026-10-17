import SideNavigation, {
  type SideNavigationProps,
} from "@cloudscape-design/components/side-navigation";
import Icon, { type IconProps } from "@cloudscape-design/components/icon";
import Badge from "@cloudscape-design/components/badge";
import { useQuery } from "@tanstack/react-query";
import { useLocation, useNavigate } from "react-router";

import { apiFetch } from "@/api/client";
import { ZONES } from "@/components/shell/zones";

/** Cloudscape navigation for the application's product zones. */
export function Sidebar({ collapsed = false }: { collapsed?: boolean }) {
  const location = useLocation();
  const navigate = useNavigate();
  const pending = useQuery({
    queryKey: ["spam-review", "pending-count"],
    queryFn: () => apiFetch<{ total: number }>("/api/spam-review?status=pending&limit=1"),
    refetchInterval: 60_000,
    staleTime: 30_000,
    retry: false,
  });
  const identities = useQuery({
    queryKey: ["identity-review", "pending-count"],
    queryFn: () => apiFetch<{ job_total: number }>("/api/identity-review?status=pending&limit=1"),
    refetchInterval: 60_000, staleTime: 30_000, retry: false,
  });
  const items: SideNavigationProps.Item[] = [
    ...ZONES.map((zone) => ({
      type: "link" as const,
      text: zone.label,
      href: zone.path,
      icon: <ZoneIcon zone={zone.path} />,
      info: zone.path === "/spam-review" && (pending.data?.total ?? 0) > 0
        ? <Badge color="blue">{pending.data?.total}</Badge> : zone.path === "/needs-review" && (identities.data?.job_total ?? 0) > 0 ? <Badge color="blue">{identities.data?.job_total}</Badge> : undefined,
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
