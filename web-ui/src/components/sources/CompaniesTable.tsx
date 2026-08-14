import Alert from "@cloudscape-design/components/alert";
import Badge from "@cloudscape-design/components/badge";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Checkbox from "@cloudscape-design/components/checkbox";
import Header from "@cloudscape-design/components/header";
import Input from "@cloudscape-design/components/input";
import Modal from "@cloudscape-design/components/modal";
import Pagination from "@cloudscape-design/components/pagination";
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

const PAGE_SIZE = 25;

/** Cloudscape resource table for tracked company boards. */
export function CompaniesTable({
  companies,
  includeRemoved,
  isLoading,
  error,
  onIncludeRemovedChange,
}: CompaniesTableProps) {
  const [removeTarget, setRemoveTarget] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const remove = useRemoveCompany();
  const normalizedSearch = search.trim().toLocaleLowerCase();
  const filteredCompanies = companies.filter(
    (company) =>
      normalizedSearch.length === 0 ||
      company.slug.toLocaleLowerCase().includes(normalizedSearch) ||
      company.vendor?.toLocaleLowerCase().includes(normalizedSearch),
  );
  const pagesCount = Math.max(1, Math.ceil(filteredCompanies.length / PAGE_SIZE));
  const currentPage = Math.min(page, pagesCount - 1);
  const pageStart = currentPage * PAGE_SIZE;
  const pageCompanies = filteredCompanies.slice(pageStart, pageStart + PAGE_SIZE);

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
            title: "Could not stop tracking",
            description: mutationError.message,
          }),
      },
    );
  };

  return (
    <>
      <Table
        trackBy="slug"
        items={pageCompanies}
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
          { id: "vendor", header: "Board provider", cell: (company) => company.vendor ?? "—" },
          {
            id: "health",
            header: "Scan health",
            cell: (company) => <DiscoveryHealth company={company} />,
          },
          {
            id: "state",
            header: "Tracking status",
            cell: (company) =>
              company.removed ? <Badge color="grey">Stopped</Badge> : <Badge color="green">Tracked</Badge>,
          },
          {
            id: "actions",
            header: "Actions",
            cell: (company) =>
              company.removed ? null : (
                <Button
                  variant="inline-link"
                  ariaLabel={`Stop tracking ${company.slug}`}
                  onClick={() => setRemoveTarget(company.slug)}
                >
                  Stop tracking
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
              <SpaceBetween direction="horizontal" size="s" alignItems="center">
                <Input
                  value={search}
                  type="search"
                  placeholder="Search companies"
                  ariaLabel="Search companies"
                  clearAriaLabel="Clear search"
                  onChange={({ detail }) => {
                    setSearch(detail.value);
                    setPage(0);
                  }}
                />
                <Checkbox
                  checked={includeRemoved}
                  onChange={({ detail }) => onIncludeRemovedChange(detail.checked)}
                  ariaLabel="Show stopped companies"
                >
                  Show stopped companies
                </Checkbox>
              </SpaceBetween>
            }
          >
            Tracked companies
          </Header>
        }
        pagination={
          filteredCompanies.length > PAGE_SIZE ? (
            <SpaceBetween direction="horizontal" size="s" alignItems="center">
              <Box color="text-body-secondary">
                {pageStart + 1}–{Math.min(pageStart + PAGE_SIZE, filteredCompanies.length)} of{" "}
                {filteredCompanies.length} companies
              </Box>
              <Pagination
                currentPageIndex={currentPage + 1}
                pagesCount={pagesCount}
                ariaLabels={{
                  previousPageLabel: "Previous page",
                  nextPageLabel: "Next page",
                  pageLabel: (pageNumber) => `Page ${pageNumber}`,
                }}
                onChange={({ detail }) => setPage(detail.currentPageIndex - 1)}
              />
            </SpaceBetween>
          ) : null
        }
        empty={
          <Box textAlign="center" color="inherit">
            <SpaceBetween size="xs">
              <b>{normalizedSearch.length === 0 ? "No companies tracked" : "No companies match"}</b>
              <Box variant="p" color="text-body-secondary">
                Check a board above, review the match, then add it to future scans.
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
                Stop tracking
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
