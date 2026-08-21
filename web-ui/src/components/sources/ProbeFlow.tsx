import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Checkbox from "@cloudscape-design/components/checkbox";
import FormField from "@cloudscape-design/components/form-field";
import Header from "@cloudscape-design/components/header";
import SpaceBetween from "@cloudscape-design/components/space-between";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Table from "@cloudscape-design/components/table";
import Textarea from "@cloudscape-design/components/textarea";
import { useState } from "react";

import {
  useBulkAddCompanies,
  useProbeCompanies,
  type ProbeEntryResult,
} from "@/api/queries";
import { toast } from "@/components/ui/use-toast";
import { isVendor } from "@/lib/vendors";

const MAX_ENTRIES = 200;

interface TrackedCompany {
  slug: string;
  vendor: "greenhouse" | "ashby" | "lever";
}

interface CompletedAddition {
  rows: TrackedCompany[];
  inserted: number;
  batchSize: number;
}

function mergeTrackedCompanies(
  current: TrackedCompany[],
  added: TrackedCompany[],
): TrackedCompany[] {
  const bySlug = new Map(current.map((row) => [row.slug, row]));
  added.forEach((row) => bySlug.set(row.slug, row));
  return [...bySlug.values()];
}

function isResolved(result: ProbeEntryResult): boolean {
  return result.slug !== null && isVendor(result.vendor) && result.error === null;
}

function parseEntries(text: string): string[] {
  return text.split("\n").map((line) => line.trim()).filter(Boolean);
}

/** Probes a pasted board list, then adds only reviewed vendor matches. */
export function ProbeFlow() {
  const [text, setText] = useState("");
  const [results, setResults] = useState<ProbeEntryResult[] | null>(null);
  const [checked, setChecked] = useState<ReadonlySet<number>>(new Set());
  const [completed, setCompleted] = useState<CompletedAddition | null>(null);
  const [isAddingMore, setIsAddingMore] = useState(true);
  const probe = useProbeCompanies();
  const bulkAdd = useBulkAddCompanies();
  const entries = parseEntries(text);

  const runProbe = () => {
    if (entries.length === 0 || entries.length > MAX_ENTRIES || probe.isPending) return;
    probe.mutate(entries, {
      onSuccess: (response) => {
        setResults(response.results);
        setChecked(new Set(response.results.flatMap((row, i) => isResolved(row) ? [i] : [])));
      },
      onError: (error) =>
        toast({
          variant: "destructive",
          title: "Boards could not be checked",
          description: error.message,
        }),
    });
  };

  const toggle = (index: number) => {
    setChecked((current) => {
      const next = new Set(current);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  };

  const setAll = (isSelected: boolean) => {
    if (results === null) return;
    setChecked(
      isSelected
        ? new Set(results.flatMap((row, index) => isResolved(row) ? [index] : []))
        : new Set(),
    );
  };

  const confirm = () => {
    if (results === null || checked.size === 0 || bulkAdd.isPending) return;
    const rows = results.flatMap((row, index) =>
      checked.has(index) && row.slug !== null && isVendor(row.vendor)
        ? [{ slug: row.slug, vendor: row.vendor }]
        : [],
    );
    bulkAdd.mutate(rows, {
      onSuccess: ({ inserted }) => {
        toast({
          title: `${inserted} added`,
          description: inserted < rows.length ? `${rows.length - inserted} already tracked` : undefined,
        });
        setCompleted((current) => ({
          rows: mergeTrackedCompanies(current?.rows ?? [], rows),
          inserted,
          batchSize: rows.length,
        }));
        setIsAddingMore(false);
        setText("");
        setResults(null);
        setChecked(new Set());
      },
      onError: (error) =>
        toast({
          variant: "destructive",
          title: "Companies could not be added",
          description: error.message,
        }),
    });
  };

  if (results !== null) {
    return (
      <SpaceBetween size="l">
        {completed !== null && <CompletedAdditionSummary completed={completed} />}
        <ProbeReview
          results={results}
          checked={checked}
          isConfirming={bulkAdd.isPending}
          onToggle={toggle}
          onSetAll={setAll}
          onConfirm={confirm}
          onBack={() => setResults(null)}
        />
      </SpaceBetween>
    );
  }

  if (completed !== null && !isAddingMore) {
    return (
      <CompletedAdditionSummary
        completed={completed}
        onAddMore={() => setIsAddingMore(true)}
      />
    );
  }

  const isOverLimit = entries.length > MAX_ENTRIES;
  return (
    <SpaceBetween size="l">
      {completed !== null && <CompletedAdditionSummary completed={completed} />}
      <SpaceBetween size="m">
        <FormField
          label="Company slugs or board URLs"
          description="One entry per line. Checking a board does not add it."
          constraintText={`${entries.length} of ${MAX_ENTRIES} entries`}
          errorText={isOverLimit ? `Use no more than ${MAX_ENTRIES} entries.` : undefined}
        >
          <Textarea
            value={text}
            onChange={({ detail }) => setText(detail.value)}
            placeholder={"acme\nhttps://boards.greenhouse.io/initech\n…one entry per line"}
            rows={5}
            resize="vertical"
            disabled={probe.isPending}
            ariaLabel="Company entries"
            invalid={isOverLimit}
          />
        </FormField>
        <Box>
          <Button
            variant="primary"
            disabled={entries.length === 0 || isOverLimit}
            loading={probe.isPending}
            loadingText="Checking company boards"
            onClick={runProbe}
          >
            Check boards
          </Button>
        </Box>
      </SpaceBetween>
    </SpaceBetween>
  );
}

function CompletedAdditionSummary({
  completed,
  onAddMore,
}: {
  completed: CompletedAddition;
  onAddMore?: () => void;
}) {
  const existing = completed.batchSize - completed.inserted;
  return (
    <SpaceBetween size="m">
      <Alert type="success" header={`${completed.inserted} new ${completed.inserted === 1 ? "company" : "companies"} added`}>
        <SpaceBetween size="xxs">
          {existing > 0 && (
            <Box>{existing} {existing === 1 ? "was" : "were"} already tracked</Box>
          )}
          <Box>Every company below is tracked and will be included in the next ATS scan.</Box>
        </SpaceBetween>
      </Alert>
      <Table
        items={completed.rows}
        trackBy="slug"
        contentDensity="compact"
        ariaLabels={{ tableLabel: "Added companies" }}
        header={
          <Header variant="h3" counter={`(${completed.rows.length})`}>
            Manually added companies
          </Header>
        }
        columnDefinitions={[
          { id: "company", header: "Company", cell: (row) => row.slug },
          { id: "vendor", header: "Board provider", cell: (row) => row.vendor },
          {
            id: "status",
            header: "Status",
            cell: () => <StatusIndicator type="success">Tracked</StatusIndicator>,
          },
        ]}
      />
      {onAddMore !== undefined && (
        <Box>
          <Button onClick={onAddMore}>Add more companies</Button>
        </Box>
      )}
    </SpaceBetween>
  );
}

interface ProbeReviewProps {
  results: ProbeEntryResult[];
  checked: ReadonlySet<number>;
  isConfirming: boolean;
  onToggle: (index: number) => void;
  onSetAll: (isSelected: boolean) => void;
  onConfirm: () => void;
  onBack: () => void;
}

function ProbeReview(props: ProbeReviewProps) {
  const rows = props.results.map((result, index) => ({ result, index }));
  return (
    <SpaceBetween size="m">
      <Table
        items={rows}
        trackBy="index"
        contentDensity="compact"
        ariaLabels={{ tableLabel: "Board check results" }}
        header={
          <Header
            variant="h3"
            counter={`(${props.checked.size} of ${props.results.length} selected)`}
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button variant="inline-link" onClick={() => props.onSetAll(true)}>Select all matched</Button>
                <Button variant="inline-link" onClick={() => props.onSetAll(false)}>Clear selection</Button>
              </SpaceBetween>
            }
          >
            Review matched boards
          </Header>
        }
        columnDefinitions={[
          {
            id: "selection",
            header: "Select",
            width: 72,
            cell: ({ result, index }) => (
              <Checkbox
                checked={props.checked.has(index)}
                disabled={!isResolved(result)}
                ariaLabel={`Add ${result.input}`}
                onChange={() => props.onToggle(index)}
              />
            ),
          },
          { id: "input", header: "Input", cell: ({ result }) => result.input },
          { id: "company", header: "Company", cell: ({ result }) => result.slug ?? "—" },
          { id: "vendor", header: "Board provider", cell: ({ result }) => result.vendor ?? "—" },
          { id: "result", header: "Check result", cell: ({ result }) => <ProbeStatus row={result} /> },
        ]}
      />
      <SpaceBetween direction="horizontal" size="xs">
        <Button
          variant="primary"
          disabled={props.checked.size === 0}
          loading={props.isConfirming}
          loadingText="Adding companies"
          onClick={props.onConfirm}
        >
          Add {props.checked.size} selected {props.checked.size === 1 ? "company" : "companies"}
        </Button>
        <Button disabled={props.isConfirming} onClick={props.onBack}>Edit list</Button>
      </SpaceBetween>
    </SpaceBetween>
  );
}

function ProbeStatus({ row }: { row: ProbeEntryResult }) {
  if (isResolved(row)) return <StatusIndicator type="success">Vendor matched</StatusIndicator>;
  if (row.error !== null) return <StatusIndicator type="error">{row.error}</StatusIndicator>;
  return <Alert type="info">No supported board detected</Alert>;
}
