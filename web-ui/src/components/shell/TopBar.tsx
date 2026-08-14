import Box from "@cloudscape-design/components/box";
import ButtonDropdown from "@cloudscape-design/components/button-dropdown";
import Header from "@cloudscape-design/components/header";
import SpaceBetween from "@cloudscape-design/components/space-between";
import { useLocation } from "react-router";

import { zoneForPath } from "@/components/shell/zones";
import { useDensity, type Density } from "@/lib/density";

/** Route title and persistent display preferences. */
export function TopBar() {
  const location = useLocation();
  const zone = zoneForPath(location.pathname);

  return <Header variant="h1" actions={<ViewMenu />}>{zone?.label ?? "Jobfeed"}</Header>;
}

function ViewMenu() {
  const { density, setDensity } = useDensity();
  return (
    <SpaceBetween direction="horizontal" size="xs" alignItems="center">
      <Box variant="small" color="text-status-inactive">
        {density === "compact" ? "Compact rows" : "Comfortable rows"}
      </Box>
      <ButtonDropdown
        items={[
          { id: "compact", text: "Compact rows" },
          { id: "comfortable", text: "Comfortable rows" },
        ]}
        onItemClick={({ detail }) => setDensity(detail.id as Density)}
      >
        Row density
      </ButtonDropdown>
    </SpaceBetween>
  );
}
