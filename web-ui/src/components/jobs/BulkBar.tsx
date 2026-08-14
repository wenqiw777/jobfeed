import Button from "@cloudscape-design/components/button";
import Box from "@cloudscape-design/components/box";
import Container from "@cloudscape-design/components/container";
import SpaceBetween from "@cloudscape-design/components/space-between";

import { useBulkTransition, type TransitionStatus } from "@/api/queries";
import { toast } from "@/components/ui/use-toast";

interface BulkBarProps {
  selectedIds: string[];
  /** True total matching the current filters (the API's `total`). */
  total: number;
  isSelectingAll: boolean;
  onSelectPage: () => void;
  onSelectAllMatching: () => void;
  onClear: () => void;
  /** Receives failures so successful rows can leave the active list while
   * failed rows remain selected for retry. */
  onBulkResult: (failedIds: string[]) => void;
}

interface BulkAction {
  label: string;
  to: TransitionStatus;
  force: boolean;
}

const ACTIONS: BulkAction[] = [
  { label: "Move selected to Wait", to: "shortlisted", force: false },
  { label: "Ignore selected", to: "ignored", force: false },
];

/** Appears while rows are checkbox-selected; runs the bulk endpoint. */
export function BulkBar({
  selectedIds,
  total,
  isSelectingAll,
  onSelectPage,
  onSelectAllMatching,
  onClear,
  onBulkResult,
}: BulkBarProps) {
  const bulk = useBulkTransition();

  if (selectedIds.length === 0) {
    return null;
  }

  const run = ({ to, force }: BulkAction) => {
    bulk.mutate(
      { items: selectedIds.map((id) => ({ id, to })), force },
      {
        onSuccess: (result) => {
          const failedIds = result.failed.map((failure) => failure.id);
          toast({
            variant: failedIds.length > 0 ? "destructive" : "default",
            title: failedIds.length > 0
              ? "Some selected jobs could not be updated"
              : "Selected jobs updated",
            description:
              `${result.succeeded} succeeded · ${result.skipped} skipped · ` +
              `${failedIds.length} failed · ${result.cascaded} cascaded` +
              (failedIds.length > 0 ? ` · Failed IDs: ${failedIds.join(", ")}` : ""),
          });
          onBulkResult(failedIds);
        },
        onError: (error) => {
          toast({
            variant: "destructive",
            title: "Selected jobs could not be updated",
            description: error.message,
          });
        },
      },
    );
  };

  return (
    <div role="toolbar" aria-label="Bulk actions">
      <Container>
        <SpaceBetween size="xs">
          <SpaceBetween direction="horizontal" size="xs" alignItems="center">
            <Box variant="strong">{selectedIds.length} selected</Box>
            <div role="group" aria-label="Decision actions">
              <SpaceBetween direction="horizontal" size="xs">
                {ACTIONS.map((action) => (
                  <Button
                    key={action.to}
                    wrapText={false}
                    disabled={bulk.isPending || isSelectingAll}
                    onClick={() => run(action)}
                  >
                    {action.label}
                  </Button>
                ))}
              </SpaceBetween>
            </div>
          </SpaceBetween>
          <div role="group" aria-label="Selection controls">
            <SpaceBetween direction="horizontal" size="xs">
              <Button
                variant="link"
                wrapText={false}
                disabled={bulk.isPending || isSelectingAll}
                onClick={onSelectPage}
              >
                Select this page
              </Button>
              <Button
                variant="link"
                wrapText={false}
                loading={isSelectingAll}
                disabled={bulk.isPending}
                onClick={onSelectAllMatching}
              >
                {`Select all ${total} ${total === 1 ? "result" : "results"}`}
              </Button>
              <Button
                variant="link"
                wrapText={false}
                disabled={bulk.isPending || isSelectingAll}
                onClick={onClear}
              >
                Clear selection
              </Button>
            </SpaceBetween>
          </div>
        </SpaceBetween>
      </Container>
    </div>
  );
}
