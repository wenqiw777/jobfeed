import { lazy, Suspense } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router";

import { useConfiguration } from "@/api/configuration";
import { Shell } from "@/components/shell/Shell";
import { Skeleton } from "@/components/ui/skeleton";
import ConfigurationPage from "@/routes/configuration";
import LibraryPage from "@/routes/library";
import PipelinePage from "@/routes/pipeline";
import RunsPage from "@/routes/runs";
import SourcesPage from "@/routes/sources";
import TriagePage from "@/routes/triage";

// Insights and Performance are the recharts consumers — route-level lazy
// keeps the charting bundle (~880KB pre-split) out of the eager chunk
// every other zone pays for.
const InsightsPage = lazy(() => import("@/routes/insights"));
const PerformancePage = lazy(() => import("@/routes/performance"));

/** Suspense fallback while the insights chunk loads — skeleton page, the
 * same shape the route shows while its query is pending (no spinners). */
function InsightsFallback() {
  return (
    <div className="flex flex-col gap-3 px-4 py-3" aria-label="Loading insights">
      <Skeleton className="h-20 w-full" />
      <div className="grid gap-3 lg:grid-cols-2">
        <Skeleton className="h-64 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    </div>
  );
}

function PerformanceFallback() {
  return (
    <div className="flex flex-col gap-3 px-4 py-3" aria-label="Loading performance">
      <Skeleton className="h-20 w-full" />
      <div className="grid gap-3 lg:grid-cols-2">
        <Skeleton className="h-64 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    </div>
  );
}

export default function App() {
  const location = useLocation();
  const configuration = useConfiguration();

  if (configuration.isPending) {
    return <ConfigurationLoading />;
  }
  if (configuration.isError || configuration.data === undefined) {
    return <ConfigurationUnavailable />;
  }
  if (!configuration.data.configured && location.pathname !== "/setup") {
    return <Navigate to="/setup" replace />;
  }

  return (
    <Routes>
      <Route path="/setup" element={<ConfigurationPage />} />
      <Route element={<Shell />}>
        <Route path="/" element={<Navigate to="/triage" replace />} />
        <Route path="/triage" element={<TriagePage />} />
        <Route path="/pipeline" element={<PipelinePage />} />
        <Route path="/library" element={<LibraryPage />} />
        <Route
          path="/insights"
          element={
            <Suspense fallback={<InsightsFallback />}>
              <InsightsPage />
            </Suspense>
          }
        />
        <Route path="/runs" element={<RunsPage />} />
        <Route
          path="/performance"
          element={
            <Suspense fallback={<PerformanceFallback />}>
              <PerformancePage />
            </Suspense>
          }
        />
        <Route path="/sources" element={<SourcesPage />} />
        <Route path="*" element={<Navigate to="/triage" replace />} />
      </Route>
    </Routes>
  );
}

function ConfigurationLoading() {
  return (
    <main className="min-h-screen bg-bg px-5 py-12" aria-label="Loading settings">
      <div className="mx-auto max-w-4xl space-y-3">
        <Skeleton className="h-14 w-72" />
        <Skeleton className="h-96 w-full" />
      </div>
    </main>
  );
}

function ConfigurationUnavailable() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-bg px-5">
      <div className="max-w-md rounded-panel border border-border bg-surface p-5">
        <h1 className="text-h1 text-ink">Configuration unavailable</h1>
        <p className="mt-2 text-body text-ink-2">
          Jobfeed could not read local settings. Refresh after checking the server log.
        </p>
      </div>
    </main>
  );
}
