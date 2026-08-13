import ButtonDropdown from "@cloudscape-design/components/button-dropdown";

import { ApiError } from "@/api/client";
import { useTriggerScan } from "@/api/queries";
import { toast } from "@/components/ui/use-toast";

const SOURCES = ["all", "ats", "indeed", "linkedin-guest", "speedyapply"] as const;

/** Cloudscape source selector that starts a scan run. */
export function TriggerScanButton() {
  const trigger = useTriggerScan();

  const fire = (source: string) => {
    trigger.mutate(
      { source },
      {
        onSuccess: () => toast({ title: `Scan started (${source})` }),
        onError: (error) => {
          if (error instanceof ApiError && error.status === 409) {
            toast({
              title: "Scan already running",
              description: "Wait for the current scan to finish.",
              variant: "destructive",
            });
            return;
          }
          toast({
            title: "Scan failed",
            description: error instanceof Error ? error.message : String(error),
            variant: "destructive",
          });
        },
      },
    );
  };

  return (
    <ButtonDropdown
      variant="primary"
      loading={trigger.isPending}
      loadingText="Starting scan"
      items={SOURCES.map((source) => ({ id: source, text: source }))}
      onItemClick={({ detail }) => fire(detail.id)}
    >
      Scan
    </ButtonDropdown>
  );
}
