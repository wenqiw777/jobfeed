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
        toast({ variant: "destructive", title: "Probe failed", description: error.message }),
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
        setText("");
        setResults(null);
        setChecked(new Set());
      },
      onError: (error) =>
        toast({ variant: "destructive", title: "Bulk add failed", description: error.message }),
    });
  };

  if (results !== null) {
    return (
      <ProbeReview
        results={results}
        checked={checked}
        isConfirming={bulkAdd.isPending}
        onToggle={toggle}
        onSetAll={setAll}
        onConfirm={confirm}
        onBack={() => setResults(null)}
      />
    );
  }

  const isOverLimit = entries.length > MAX_ENTRIES;
  return (
    <SpaceBetween size="m">
      <FormField
        label="Company slugs or board URLs"
        description="One entry per line. A probe reads the board but does not add it."
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
          loadingText="Probing company boards"
          onClick={runProbe}
        >
          Probe list
        </Button>
      </Box>
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
        ariaLabels={{ tableLabel: "Probe results" }}
        header={
          <Header
            variant="h3"
            counter={`(${props.checked.size} of ${props.results.length} selected)`}
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button variant="inline-link" onClick={() => props.onSetAll(true)}>Select all</Button>
                <Button variant="inline-link" onClick={() => props.onSetAll(false)}>None</Button>
              </SpaceBetween>
            }
          >
            Review probe results
          </Header>
        }
        columnDefinitions={[
          {
            id: "selection",
            header: "Add",
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
          { id: "vendor", header: "Vendor", cell: ({ result }) => result.vendor ?? "—" },
          { id: "result", header: "Probe result", cell: ({ result }) => <ProbeStatus row={result} /> },
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
          Add {props.checked.size} {props.checked.size === 1 ? "company" : "companies"}
        </Button>
        <Button disabled={props.isConfirming} onClick={props.onBack}>Back to list</Button>
      </SpaceBetween>
    </SpaceBetween>
  );
}

function ProbeStatus({ row }: { row: ProbeEntryResult }) {
  if (isResolved(row)) return <StatusIndicator type="success">Vendor matched</StatusIndicator>;
  if (row.error !== null) return <StatusIndicator type="error">{row.error}</StatusIndicator>;
  return <Alert type="info">no ATS detected</Alert>;
}
