import Badge from "@cloudscape-design/components/badge";
import Box from "@cloudscape-design/components/box";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import SpaceBetween from "@cloudscape-design/components/space-between";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { runsKeys, type RunSummary } from "@/api/queries";
import { formatLocalDateTime } from "@/lib/dates";
import { useSSE, type SSEState } from "@/lib/use-sse";

const LIVE_COUNTERS: { key: keyof RunSummary; label: string; color?: "red" }[] = [
  { key: "jobs_discovered", label: "discovered" },
  { key: "jobs_inserted", label: "inserted" },
  { key: "jobs_scored", label: "scored" },
  { key: "stage_a_scored", label: "stage A" },
  { key: "stage_b_scored", label: "stage B" },
  { key: "errors", label: "errors", color: "red" },
];

/** Identity needed to attach the progress stream for an active run. */
export interface ActiveRun {
  run_id: string;
  source: string;
  started_at: string;
}

interface LiveRunRowProps {
  run: ActiveRun;
  onDone?: () => void;
}

/** SSE-backed Cloudscape progress card for an active pipeline run. */
export function LiveRunRow({ run, onDone }: LiveRunRowProps) {
  const queryClient = useQueryClient();
  const sse: SSEState<RunSummary> = useSSE<RunSummary>(
    `/api/runs/${run.run_id}/progress`,
  );
  const hasFiredDone = useRef(false);

  useEffect(() => {
    if (sse.isDone && !hasFiredDone.current) {
      hasFiredDone.current = true;
      void queryClient.invalidateQueries({ queryKey: runsKeys.list({}) });
      onDone?.();
    }
  }, [sse.isDone, queryClient, onDone]);

  const counters = LIVE_COUNTERS.filter(
    ({ key }) => sse.data !== null && (sse.data[key] as number) > 0,
  );

  return (
    <li data-testid={`live-run-${run.run_id}`}>
      <Container
        header={
          <Header
            variant="h3"
            description={`${formatLocalDateTime(run.started_at)} · ${run.run_id}`}
            actions={<LiveStatus sse={sse} />}
          >
            {run.source} run
          </Header>
        }
      >
        <ColumnLayout columns={2} variant="text-grid">
          <div>
            <Box variant="awsui-key-label">Progress stream</Box>
            <Box>{sse.isConnected ? "Receiving live counters" : "Connecting to live counters"}</Box>
          </div>
          <div>
            <Box variant="awsui-key-label">Activity</Box>
            <SpaceBetween direction="horizontal" size="xs">
              {counters.length === 0 ? (
                <Box color="text-body-secondary">Waiting for the first update</Box>
              ) : (
                counters.map(({ key, label, color }) => (
                  <Badge key={key} color={color}>{String(sse.data![key])} {label}</Badge>
                ))
              )}
            </SpaceBetween>
          </div>
        </ColumnLayout>
      </Container>
    </li>
  );
}

function LiveStatus({ sse }: { sse: SSEState<RunSummary> }) {
  if (sse.isDone) return <StatusIndicator type="success">Completed</StatusIndicator>;
  if (sse.error !== null) return <StatusIndicator type="warning">Reconnecting</StatusIndicator>;
  return <StatusIndicator type="in-progress">Running</StatusIndicator>;
}
