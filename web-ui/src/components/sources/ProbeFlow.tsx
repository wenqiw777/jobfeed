import { useState } from "react";

import {
  useBulkAddCompanies,
  useProbeCompanies,
  type ProbeEntryResult,
} from "@/api/queries";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { toast } from "@/components/ui/use-toast";
import { cn } from "@/lib/utils";
import { isVendor } from "@/lib/vendors";

/** ProbeBody accepts 1..200 entries per request. */
const MAX_ENTRIES = 200;

/** A row the confirm step may insert: slug resolved, vendor known. */
function isResolved(result: ProbeEntryResult): boolean {
  return result.slug !== null && isVendor(result.vendor) && result.error === null;
}

function parseEntries(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line !== "");
}

/**
 * Bulk add: paste a list (one slug or board URL per line) → probe each
 * entry's vendor → review the per-entry results → confirm the checked
 * rows into POST /companies/bulk. The probe writes nothing, so backing
 * out of the review step costs nothing.
 */
export function ProbeFlow() {
  const [text, setText] = useState("");
  const [results, setResults] = useState<ProbeEntryResult[] | null>(null);
  const [checked, setChecked] = useState<ReadonlySet<number>>(new Set());
  const probe = useProbeCompanies();
  const bulkAdd = useBulkAddCompanies();

  const entries = parseEntries(text);

  const runProbe = () => {
    if (entries.length === 0 || entries.length > MAX_ENTRIES || probe.isPending) {
      return;
    }
    probe.mutate(entries, {
      onSuccess: (response) => {
        setResults(response.results);
        // Resolved rows are pre-checked; error/miss rows stay uncheckable.
        setChecked(
          new Set(response.results.flatMap((row, index) => (isResolved(row) ? [index] : []))),
        );
      },
      onError: (error) =>
        toast({ variant: "destructive", title: "Probe failed", description: error.message }),
    });
  };

  const toggle = (index: number) => {
    setChecked((current) => {
      const next = new Set(current);
      if (next.has(index)) {
        next.delete(index);
      } else {
        next.add(index);
      }
      return next;
    });
  };

  const setAll = (on: boolean) => {
    if (results === null) {
      return;
    }
    setChecked(
      on
        ? new Set(results.flatMap((row, index) => (isResolved(row) ? [index] : [])))
        : new Set(),
    );
  };

  const confirm = () => {
    if (results === null || checked.size === 0 || bulkAdd.isPending) {
      return;
    }
    const rows = results.flatMap((row, index) =>
      checked.has(index) && row.slug !== null && isVendor(row.vendor)
        ? [{ slug: row.slug, vendor: row.vendor }]
        : [],
    );
    bulkAdd.mutate(rows, {
      onSuccess: ({ inserted }) => {
        toast({
          title: `${inserted} added`,
          // The server skips active slugs — surface the delta honestly.
          description:
            inserted < rows.length ? `${rows.length - inserted} already tracked` : undefined,
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

  return (
    <div>
      <textarea
        value={text}
        onChange={(event) => setText(event.target.value)}
        placeholder={"acme\nhttps://boards.greenhouse.io/initech\n…one entry per line"}
        rows={5}
        disabled={probe.isPending}
        aria-label="Company entries"
        className="w-full resize-y rounded-control border border-border-strong bg-surface px-2.5 py-1.5 font-mono text-compact text-ink placeholder:text-mute focus-visible:border-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      />
      <div className="mt-2 flex items-center gap-3">
        <Button
          variant="primary"
          size="sm"
          disabled={entries.length === 0 || entries.length > MAX_ENTRIES || probe.isPending}
          onClick={runProbe}
        >
          {probe.isPending ? "Probing…" : "Probe list"}
        </Button>
        {entries.length > 0 && (
          <span className="font-mono text-micro text-mute">
            {entries.length} {entries.length === 1 ? "entry" : "entries"}
            {entries.length > MAX_ENTRIES && ` (max ${MAX_ENTRIES})`}
          </span>
        )}
      </div>
    </div>
  );
}

interface ProbeReviewProps {
  results: ProbeEntryResult[];
  checked: ReadonlySet<number>;
  isConfirming: boolean;
  onToggle: (index: number) => void;
  onSetAll: (on: boolean) => void;
  onConfirm: () => void;
  onBack: () => void;
}

function ProbeReview({
  results,
  checked,
  isConfirming,
  onToggle,
  onSetAll,
  onConfirm,
  onBack,
}: ProbeReviewProps) {
  return (
    <div>
      <div className="flex items-center gap-2 pb-1.5">
        <span className="text-micro text-mute">
          {checked.size} of {results.length} selected
        </span>
        <button type="button" className="text-micro text-accent hover:underline" onClick={() => onSetAll(true)}>
          Select all
        </button>
        <button type="button" className="text-micro text-accent hover:underline" onClick={() => onSetAll(false)}>
          None
        </button>
      </div>
      <ul aria-label="Probe results" className="border-t border-border">
        {results.map((row, index) => (
          <ProbeRow
            key={index}
            row={row}
            isChecked={checked.has(index)}
            onToggle={() => onToggle(index)}
          />
        ))}
      </ul>
      <div className="mt-2.5 flex items-center gap-2">
        <Button
          variant="primary"
          size="sm"
          disabled={checked.size === 0 || isConfirming}
          onClick={onConfirm}
        >
          {isConfirming ? "Adding…" : `Add ${checked.size} ${checked.size === 1 ? "company" : "companies"}`}
        </Button>
        <Button size="sm" disabled={isConfirming} onClick={onBack}>
          Back to list
        </Button>
      </div>
    </div>
  );
}

function ProbeRow({
  row,
  isChecked,
  onToggle,
}: {
  row: ProbeEntryResult;
  isChecked: boolean;
  onToggle: () => void;
}) {
  const resolved = isResolved(row);
  return (
    <li
      className={cn(
        "grid grid-cols-[1.25rem_minmax(0,2fr)_minmax(0,2fr)_6rem] items-center gap-2 border-b border-hairline px-1 py-1 text-compact",
        !resolved && row.error !== null && "bg-danger-bg/40",
      )}
    >
      <Checkbox
        checked={isChecked}
        disabled={!resolved}
        onCheckedChange={onToggle}
        aria-label={`Add ${row.input}`}
      />
      <span className="truncate font-mono text-mute" title={row.input}>
        {row.input}
      </span>
      {resolved ? (
        <>
          <span className="truncate font-mono text-ink">{row.slug}</span>
          <span className="text-ink-2">{row.vendor}</span>
        </>
      ) : (
        <span className={cn("col-span-2 truncate", row.error !== null ? "text-danger" : "text-mute")}>
          {row.error ?? "no ATS detected"}
        </span>
      )}
    </li>
  );
}
