import Button from "@cloudscape-design/components/button";

import { useRetryRun, useStopRun } from "@/api/queries";
import { toast } from "@/components/ui/use-toast";

export function RunActionButton({ runId, isRunning }: { runId: string; isRunning: boolean }) {
  const stop = useStopRun();
  const retry = useRetryRun();

  if (isRunning) {
    return (
      <Button
        loading={stop.isPending}
        loadingText="Stopping"
        onClick={() => stop.mutate(
          { runId },
          {
            onSuccess: () => toast({ title: "Run stopped" }),
            onError: (error) => toast({
              title: "Run could not be stopped",
              description: error instanceof Error ? error.message : String(error),
              variant: "destructive",
            }),
          },
        )}
      >
        Stop
      </Button>
    );
  }

  return (
    <Button
      loading={retry.isPending}
      loadingText="Retrying"
      onClick={() => retry.mutate(
        { runId },
        {
          onSuccess: () => toast({ title: "Run retry started" }),
          onError: (error) => toast({
            title: "Run could not be retried",
            description: error instanceof Error ? error.message : String(error),
            variant: "destructive",
          }),
        },
      )}
    >
      Retry
    </Button>
  );
}
