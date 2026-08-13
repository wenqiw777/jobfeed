/** Product zones shared by sidebar navigation and route titles. */
export interface Zone {
  path: string;
  label: string;
  /** Which sidebar badge this zone shows, if any. */
  badge: "queue" | null;
}

export const ZONES: Zone[] = [
  { path: "/triage", label: "Triage", badge: "queue" },
  { path: "/library", label: "Library", badge: null },
  { path: "/insights", label: "Insights", badge: null },
  { path: "/runs", label: "Runs", badge: null },
  { path: "/performance", label: "Performance", badge: null },
  { path: "/sources", label: "Sources", badge: null },
];

export function zoneForPath(pathname: string): Zone | undefined {
  return ZONES.find(
    (zone) => pathname === zone.path || pathname.startsWith(`${zone.path}/`),
  );
}
