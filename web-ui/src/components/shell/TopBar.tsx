import { useLocation } from "react-router";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Button } from "@/components/ui/button";
import { zoneForPath } from "@/components/shell/zones";
import { useDensity, type Density } from "@/lib/density";

export function TopBar() {
  const location = useLocation();
  const zone = zoneForPath(location.pathname);
  const hints = zone?.hints ?? "";

  return (
    <header className="flex h-12 shrink-0 items-center gap-4 border-b border-border bg-surface px-5">
      <h1 className="text-h1 text-ink">{zone?.label ?? "jobfeed"}</h1>
      {/* Meta area: zone tasks (T9+) fill this with counts/filters. */}
      <div className="flex-1" />
      {hints !== "" && (
        <span aria-hidden="true" className="font-mono text-micro text-mute">
          {hints}
        </span>
      )}
      <ViewMenu />
    </header>
  );
}

function ViewMenu() {
  const { density, setDensity } = useDensity();

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="sm">
          View
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuLabel>Density</DropdownMenuLabel>
        <DropdownMenuRadioGroup
          value={density}
          onValueChange={(value) => setDensity(value as Density)}
        >
          <DropdownMenuRadioItem value="compact">Compact</DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="comfortable">Comfortable</DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
