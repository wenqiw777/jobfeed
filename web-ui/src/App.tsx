import { Navigate, Route, Routes } from "react-router";

import { Shell } from "@/components/shell/Shell";
import InsightsPage from "@/routes/insights";
import LibraryPage from "@/routes/library";
import PipelinePage from "@/routes/pipeline";
import RunsPage from "@/routes/runs";
import SourcesPage from "@/routes/sources";
import TriagePage from "@/routes/triage";

export default function App() {
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route path="/" element={<Navigate to="/triage" replace />} />
        <Route path="/triage" element={<TriagePage />} />
        <Route path="/pipeline" element={<PipelinePage />} />
        <Route path="/library" element={<LibraryPage />} />
        <Route path="/insights" element={<InsightsPage />} />
        <Route path="/runs" element={<RunsPage />} />
        <Route path="/sources" element={<SourcesPage />} />
        <Route path="*" element={<Navigate to="/triage" replace />} />
      </Route>
    </Routes>
  );
}
