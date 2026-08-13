import AppLayoutToolbar from "@cloudscape-design/components/app-layout-toolbar";
import TopNavigation from "@cloudscape-design/components/top-navigation";
import { useState } from "react";
import { Outlet, useLocation, useNavigate } from "react-router";

import { Sidebar } from "@/components/shell/Sidebar";
import { TopBar } from "@/components/shell/TopBar";
import { useDensity } from "@/lib/density";

/** Cloudscape productivity frame shared by all configured routes. */
export function Shell() {
  const { density } = useDensity();
  const [navigationOpen, setNavigationOpen] = useState(true);
  const location = useLocation();
  const navigate = useNavigate();

  return (
    <div data-testid="jobfeed-workspace-layout" className="jobfeed-workspace">
      <div id="jobfeed-top-navigation">
        <TopNavigation
          visualContext="top-navigation"
          identity={{
            href: "/triage",
            title: "jobfeed",
            onFollow: (event) => {
              event.preventDefault();
              navigate("/triage");
            },
          }}
          utilities={[
            {
              type: "button",
              text: "Settings",
              iconName: "settings",
              href: "/setup",
              onFollow: (event) => {
                event.preventDefault();
                navigate("/setup");
              },
            },
          ]}
        />
      </div>
      <AppLayoutToolbar
        headerSelector="#jobfeed-top-navigation"
        ariaLabels={{
          navigation: "Zones",
          navigationClose: "Collapse navigation",
          navigationToggle: "Open navigation",
          notifications: "Notifications",
        }}
        contentType={contentType(location.pathname)}
        navigation={<Sidebar collapsed={!navigationOpen} />}
        navigationOpen={navigationOpen}
        navigationCloseBehavior="collapse"
        navigationCollapsedWidth={48}
        navigationWidth={216}
        onNavigationChange={({ detail }) => setNavigationOpen(detail.open)}
        toolsHide
        maxContentWidth={Number.MAX_VALUE}
        content={
          <div
            data-testid="jobfeed-route-surface"
            data-density={density}
            className="jobfeed-route-surface"
          >
            <TopBar />
            <div className="jobfeed-route-content">
              <Outlet />
            </div>
          </div>
        }
      />
    </div>
  );
}

function contentType(pathname: string) {
  if (["/triage", "/library", "/sources", "/runs"].includes(pathname)) {
    return "table" as const;
  }
  if (["/insights", "/performance"].includes(pathname)) {
    return "dashboard" as const;
  }
  return "default" as const;
}
