import ButtonDropdown from "@cloudscape-design/components/button-dropdown";

import { ApiError } from "@/api/client";
import { useTriggerScan } from "@/api/queries";
import { toast } from "@/components/ui/use-toast";

const SOURCES = [
  { id: "all", text: "All sources" },
  { id: "ats", text: "Company career pages" },
  { id: "indeed", text: "Indeed" },
  { id: "linkedin-guest", text: "LinkedIn guest search" },
  { id: "speedyapply", text: "SpeedyApply lists" },
] as const;

/** Cloudscape source selector that starts a scan run. */
export function TriggerScanButton() {
  const trigger = useTriggerScan();

  const fire = (source: string) => {
    trigger.mutate(
      { source },
      {
        onSuccess: () => toast({ title: `Scan started (${sourceLabel(source)})` }),
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
            title: "Scan could not start",
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
      items={SOURCES}
      onItemClick={({ detail }) => fire(detail.id)}
    >
      Start scan
    </ButtonDropdown>
  );
}

function sourceLabel(source: string): string {
  return SOURCES.find((item) => item.id === source)?.text ?? source;
}
