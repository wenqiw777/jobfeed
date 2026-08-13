import Alert from "@cloudscape-design/components/alert";
import Badge from "@cloudscape-design/components/badge";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Checkbox from "@cloudscape-design/components/checkbox";
import Header from "@cloudscape-design/components/header";
import Modal from "@cloudscape-design/components/modal";
import SpaceBetween from "@cloudscape-design/components/space-between";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Table from "@cloudscape-design/components/table";
import { useState } from "react";

import { useRemoveCompany, type CompanyOut } from "@/api/queries";
import { toast } from "@/components/ui/use-toast";

interface CompaniesTableProps {
  companies: CompanyOut[];
  includeRemoved: boolean;
  isLoading: boolean;
  error: Error | null;
  onIncludeRemovedChange: (includeRemoved: boolean) => void;
}

/** Cloudscape resource table for tracked company boards. */
export function CompaniesTable({
  companies,
  includeRemoved,
  isLoading,
  error,
  onIncludeRemovedChange,
}: CompaniesTableProps) {
  const [removeTarget, setRemoveTarget] = useState<string | null>(null);
  const remove = useRemoveCompany();

  const confirmRemove = () => {
    if (removeTarget === null || remove.isPending) return;
    remove.mutate(
      { slug: removeTarget },
      {
        onSuccess: () => {
          toast({ title: `Stopped tracking ${removeTarget}` });
          setRemoveTarget(null);
        },
        onError: (mutationError) =>
          toast({
            variant: "destructive",
            title: "Remove failed",
            description: mutationError.message,
          }),
      },
    );
  };

  return (
    <>
      <Table
        trackBy="slug"
        items={companies}
        loading={isLoading}
        loadingText="Loading companies"
        skeleton={{ totalRows: 4 }}
        ariaLabels={{ tableLabel: "Tracked companies" }}
        contentDensity="compact"
        columnDefinitions={[
          {
            id: "company",
            header: "Company",
            isRowHeader: true,
            cell: (company) => (
              <span data-testid={`company-row-${company.slug}`}>{company.slug}</span>
            ),
          },
          { id: "vendor", header: "Vendor", cell: (company) => company.vendor ?? "—" },
          {
            id: "health",
            header: "Discovery health",
            cell: (company) => <DiscoveryHealth company={company} />,
          },
          {
            id: "state",
            header: "State",
            cell: (company) =>
              company.removed ? <Badge color="grey">Removed</Badge> : <Badge color="green">Active</Badge>,
          },
          {
            id: "actions",
            header: "Actions",
            cell: (company) =>
              company.removed ? null : (
                <Button
                  variant="inline-link"
                  ariaLabel={`Remove ${company.slug}`}
                  onClick={() => setRemoveTarget(company.slug)}
                >
                  Remove
                </Button>
              ),
          },
        ]}
        header={
          <Header
            variant="h2"
            counter={`(${companies.length})`}
            description="Active rows are included in the next ATS scan. Discovery failures reset after a successful fetch."
            actions={
              <Checkbox
                checked={includeRemoved}
                onChange={({ detail }) => onIncludeRemovedChange(detail.checked)}
                ariaLabel="Include removed"
              >
                Include removed
              </Checkbox>
            }
          >
            Tracked companies
          </Header>
        }
        empty={
          <Box textAlign="center" color="inherit">
            <SpaceBetween size="xs">
              <b>No companies tracked</b>
              <Box variant="p" color="text-body-secondary">
                Probe a board above, review the vendor match, then add it to the scan roster.
              </Box>
            </SpaceBetween>
          </Box>
        }
      />
      {error !== null && <Alert type="error" header="Companies could not be loaded">{error.message}</Alert>}
      <Modal
        visible={removeTarget !== null}
        onDismiss={() => setRemoveTarget(null)}
        header={`Stop tracking ${removeTarget ?? "company"}?`}
        closeAriaLabel="Close remove confirmation"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button onClick={() => setRemoveTarget(null)}>Cancel</Button>
              <Button variant="primary" loading={remove.isPending} onClick={confirmRemove}>
                Remove
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <Alert type="warning">
          Scans will skip this company. Existing jobs remain in Library, and re-adding the slug resumes tracking.
        </Alert>
      </Modal>
    </>
  );
}

function DiscoveryHealth({ company }: { company: CompanyOut }) {
  if (company.consecutive_discover_failures === 0) {
    return <StatusIndicator type="success">Healthy · 0 failures</StatusIndicator>;
  }
  return (
    <StatusIndicator type="warning">
      {company.consecutive_discover_failures} consecutive failures
    </StatusIndicator>
  );
}
