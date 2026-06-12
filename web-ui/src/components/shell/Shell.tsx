import { Outlet } from "react-router";

import { Sidebar } from "@/components/shell/Sidebar";
import { TopBar } from "@/components/shell/TopBar";
import { useDensity } from "@/lib/density";

/** App frame: sidebar (bg) + top bar and content (surface). */
export function Shell() {
  const { density } = useDensity();

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar />
        {/* data-density lets zone styles + tests track the active mode. */}
        <main data-density={density} className="flex-1 overflow-auto bg-surface">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
