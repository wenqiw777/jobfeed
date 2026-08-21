import AppLayoutToolbar from "@cloudscape-design/components/app-layout-toolbar";
import SpaceBetween from "@cloudscape-design/components/space-between";
import TopNavigation from "@cloudscape-design/components/top-navigation";
import { useState } from "react";
import { Outlet, useLocation, useNavigate } from "react-router";

import { Sidebar } from "@/components/shell/Sidebar";
import { PersonalMLBanner } from "@/components/shell/PersonalMLBanner";
import { TopBar } from "@/components/shell/TopBar";
import { Toaster } from "@/components/ui/toaster";
import { useDensity } from "@/lib/density";

/** Cloudscape productivity frame shared by all configured routes. */
export function Shell() {
  const { density } = useDensity();
  const [navigationOpen, setNavigationOpen] = useState(true);
  const location = useLocation();
  const navigate = useNavigate();

  return (
    <div data-testid="jobfeed-workspace-layout">
      <div id="jobfeed-top-navigation">
        <TopNavigation
          visualContext="top-navigation"
          identity={{
            href: "/triage",
            title: "Jobfeed",
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
        notifications={<Toaster />}
        maxContentWidth={Number.MAX_VALUE}
        content={
          <div data-testid="jobfeed-route-surface" data-density={density}>
            <SpaceBetween size="l">
              <PersonalMLBanner />
              <TopBar />
              <Outlet />
            </SpaceBetween>
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
