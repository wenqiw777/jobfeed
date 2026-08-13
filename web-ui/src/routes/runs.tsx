import ContentLayout from "@cloudscape-design/components/content-layout";
import Header from "@cloudscape-design/components/header";
import SpaceBetween from "@cloudscape-design/components/space-between";
import { useCallback, useState } from "react";

import { useActiveRuns, useRuns } from "@/api/queries";
import { LiveRunRow } from "@/components/runs/LiveRunRow";
import { RunHistoryTable } from "@/components/runs/RunHistoryTable";
import { TriggerEvaluateButton } from "@/components/runs/TriggerEvaluateDialog";
import { TriggerScanButton } from "@/components/runs/TriggerScanDialog";

const PAGE_SIZE = 25;

/** Pipeline triggers, live SSE progress, and historical run evidence. */
export default function RunsPage() {
  const [page, setPage] = useState(0);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const runs = useRuns({ limit: PAGE_SIZE, offset: page * PAGE_SIZE });
  const activeRuns = useActiveRuns();

  const handleDone = useCallback(() => {
    void activeRuns.refetch();
  }, [activeRuns]);

  const activeIds = new Set(activeRuns.data?.runs.map((run) => run.run_id) ?? []);
  const historyRuns = runs.data?.runs.filter((run) => !activeIds.has(run.run_id)) ?? [];
  const isHistoryLoading = runs.isPending && !runs.isFetched;

  return (
    <div data-testid="cloudscape-runs">
      <ContentLayout
        maxContentWidth={1280}
        header={
          <Header
            variant="h2"
            description="Start scans and evaluations, watch live counters, and inspect each run."
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <TriggerEvaluateButton />
                <TriggerScanButton />
              </SpaceBetween>
            }
          >
            Run operations
          </Header>
        }
      >
        <SpaceBetween size="l">
          {activeRuns.data !== undefined && activeRuns.data.runs.length > 0 && (
            <section aria-label="Active runs">
              <SpaceBetween size="s">
                <Header
                  variant="h2"
                  counter={`(${activeRuns.data.runs.length})`}
                  description="Counters update from the run progress stream until completion."
                >
                  Active runs
                </Header>
                <ul aria-label="Active runs">
                  <SpaceBetween size="s">
                    {activeRuns.data.runs.map((run) => (
                      <LiveRunRow key={run.run_id} run={run} onDone={handleDone} />
                    ))}
                  </SpaceBetween>
                </ul>
              </SpaceBetween>
            </section>
          )}
          <RunHistoryTable
            runs={historyRuns}
            total={runs.data?.total ?? 0}
            page={page}
            pageSize={PAGE_SIZE}
            isLoading={isHistoryLoading}
            isPlaceholder={runs.isPlaceholderData}
            error={runs.error}
            hasActiveRuns={activeIds.size > 0}
            expandedId={expandedId}
            onExpandedChange={setExpandedId}
            onPageChange={setPage}
          />
        </SpaceBetween>
      </ContentLayout>
    </div>
  );
}
