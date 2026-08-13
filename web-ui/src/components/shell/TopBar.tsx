import Box from "@cloudscape-design/components/box";
import ButtonDropdown from "@cloudscape-design/components/button-dropdown";
import Header from "@cloudscape-design/components/header";
import SpaceBetween from "@cloudscape-design/components/space-between";
import { useLocation } from "react-router";

import { zoneForPath } from "@/components/shell/zones";
import { useDensity, type Density } from "@/lib/density";

/** Route title, keyboard legend, and persistent display preferences. */
export function TopBar() {
  const location = useLocation();
  const zone = zoneForPath(location.pathname);
  const hints = zone?.hints ?? "";

  return (
    <div className="jobfeed-route-header">
      <Header
        variant="h1"
        description={hints || undefined}
        actions={<ViewMenu />}
      >
        {zone?.label ?? "Jobfeed"}
      </Header>
    </div>
  );
}

function ViewMenu() {
  const { density, setDensity } = useDensity();
  return (
    <SpaceBetween direction="horizontal" size="xs" alignItems="center">
      <Box variant="small" color="text-status-inactive">
        {density === "compact" ? "Compact" : "Comfortable"}
      </Box>
      <ButtonDropdown
        items={[
          { id: "compact", text: "Compact" },
          { id: "comfortable", text: "Comfortable" },
        ]}
        onItemClick={({ detail }) => setDensity(detail.id as Density)}
      >
        View
      </ButtonDropdown>
    </SpaceBetween>
  );
}
