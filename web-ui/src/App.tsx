import { lazy, Suspense } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router";
import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Spinner from "@cloudscape-design/components/spinner";

import { useConfiguration } from "@/api/configuration";
import { Shell } from "@/components/shell/Shell";
import ConfigurationPage from "@/routes/configuration";
import LibraryPage from "@/routes/library";
import RunsPage from "@/routes/runs";
import SourcesPage from "@/routes/sources";
import TriagePage from "@/routes/triage";
import OnboardingResumePage from "@/routes/onboarding-resume";
import OnboardingSearchesPage from "@/routes/onboarding-searches";
import OnboardingCompaniesPage from "@/routes/onboarding-companies";
import OnboardingProviderPage from "@/routes/onboarding-provider";
import OnboardingReviewPage from "@/routes/onboarding-review";

// Keep dashboard routes lazy so ordinary triage startup stays small.
const InsightsPage = lazy(() => import("@/routes/insights"));
const PerformancePage = lazy(() => import("@/routes/performance"));

function InsightsFallback() {
  return <Loading label="Loading insights" />;
}

function PerformanceFallback() {
  return <Loading label="Loading performance" />;
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
  if (!configuration.data.configured && !location.pathname.startsWith("/setup")) {
    return <Navigate to="/setup" replace />;
  }

  return (
    <Routes>
      <Route path="/setup" element={<ConfigurationPage />} />
      <Route path="/setup/provider" element={<OnboardingProviderPage />} />
      <Route path="/setup/resume" element={<OnboardingResumePage />} />
      <Route path="/setup/searches" element={<OnboardingSearchesPage />} />
      <Route path="/setup/companies" element={<OnboardingCompaniesPage />} />
      <Route path="/setup/review" element={<OnboardingReviewPage />} />
      <Route element={<Shell />}>
        <Route path="/" element={<Navigate to="/triage" replace />} />
        <Route path="/triage" element={<TriagePage />} />
        <Route path="/pipeline" element={<Navigate to="/triage" replace />} />
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
  return <Loading label="Loading settings" />;
}

function ConfigurationUnavailable() {
  return (
    <Box padding="xxl">
      <Alert type="error" header="Settings could not be loaded">
        Check the Jobfeed terminal for the error, then refresh this page.
      </Alert>
    </Box>
  );
}

function Loading({ label }: { label: string }) {
  return (
    <Box padding="xxl" textAlign="center">
      <SpaceBetween size="s" alignItems="center">
        <Spinner size="large" />
        <Box color="text-body-secondary">{label}</Box>
      </SpaceBetween>
    </Box>
  );
}
