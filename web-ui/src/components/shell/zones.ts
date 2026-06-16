/** The six zones (plan D5). Shared by Sidebar (nav) and TopBar (title/hints). */
export interface Zone {
  path: string;
  label: string;
  /** Which sidebar badge this zone shows, if any. */
  badge: "queue" | "attention" | null;
  /** Keyboard hints for the top bar; empty = the zone has no keyboard map. */
  hints: string;
}

// Hints mirror each route's useKeyboardMap wiring (high-traffic keys only;
// triage's long tail n/f/o stays out of the bar).
export const ZONES: Zone[] = [
  { path: "/triage", label: "Triage", badge: "queue", hints: "j/k move · a apply · h shortlist · s skip" },
  { path: "/pipeline", label: "Pipeline", badge: "attention", hints: "j/k move · enter detail · o open" },
  { path: "/library", label: "Library", badge: null, hints: "" },
  { path: "/insights", label: "Insights", badge: null, hints: "" },
  { path: "/runs", label: "Runs", badge: null, hints: "" },
  { path: "/performance", label: "Performance", badge: null, hints: "" },
  { path: "/sources", label: "Sources", badge: null, hints: "" },
];

export function zoneForPath(pathname: string): Zone | undefined {
  return ZONES.find(
    (zone) => pathname === zone.path || pathname.startsWith(`${zone.path}/`),
  );
}
