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

// The triage keyboard map's high-traffic keys (lib/keyboard.ts wiring
// in routes/triage.tsx); the long tail (n/f/o) stays out of the bar.
const KEYBOARD_HINTS = "j/k move · a apply · h shortlist · s skip";

export function TopBar() {
  const location = useLocation();
  const zone = zoneForPath(location.pathname);

  return (
    <header className="flex h-12 shrink-0 items-center gap-4 border-b border-border bg-surface px-5">
      <h1 className="text-h1 text-ink">{zone?.label ?? "jobfeed"}</h1>
      {/* Meta area: zone tasks (T9+) fill this with counts/filters. */}
      <div className="flex-1" />
      <span aria-hidden="true" className="font-mono text-micro text-mute">
        {KEYBOARD_HINTS}
      </span>
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
