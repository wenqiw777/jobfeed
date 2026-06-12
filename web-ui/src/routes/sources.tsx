import { useState } from "react";

import {
  useAddCompany,
  useCompanies,
  useProbeCompanies,
  type CompanyVendor,
} from "@/api/queries";
import { CompaniesTable } from "@/components/sources/CompaniesTable";
import { ProbeFlow } from "@/components/sources/ProbeFlow";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/use-toast";
import { isVendor, VENDORS } from "@/lib/vendors";

/**
 * Sources: which ATS company boards `scan --ats` covers. Bulk paste+probe
 * is the primary flow; the single-add form handles one-offs (probe fills
 * the vendor, or pin it by hand).
 */
export default function SourcesPage() {
  const [includeRemoved, setIncludeRemoved] = useState(false);
  const companies = useCompanies(includeRemoved);

  return (
    <div className="flex h-full min-h-0 flex-col gap-4 overflow-y-auto px-4 py-3">
      <section aria-label="Add companies" className="rounded-panel border border-border bg-surface px-4 py-3">
        <h2 className="text-h3 text-ink">Add companies</h2>
        <p className="mb-2 mt-0.5 text-micro text-mute">
          Paste slugs or board URLs (one per line), probe their ATS vendor, then confirm.
        </p>
        <ProbeFlow />
        <SingleAddForm />
      </section>
      <section aria-label="Tracked companies" className="min-h-0">
        <div className="flex items-center gap-3 pb-1.5">
          <h2 className="text-h3 text-ink">Tracked</h2>
          <CompanyCount companies={companies} />
          <label className="ml-auto flex items-center gap-1.5 text-micro text-mute">
            <Checkbox
              checked={includeRemoved}
              onCheckedChange={(value) => setIncludeRemoved(value === true)}
              aria-label="Include removed"
            />
            include removed
          </label>
        </div>
        <CompaniesBody companies={companies} />
      </section>
    </div>
  );
}

function CompanyCount({ companies }: { companies: ReturnType<typeof useCompanies> }) {
  if (companies.data === undefined) {
    return null;
  }
  return (
    <span className="font-mono text-micro text-mute">{companies.data.companies.length}</span>
  );
}

function CompaniesBody({ companies }: { companies: ReturnType<typeof useCompanies> }) {
  if (companies.isPending) {
    return (
      <div className="flex flex-col gap-1 py-2" aria-label="Loading companies">
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-7 w-full" />
        ))}
      </div>
    );
  }
  if (companies.isError) {
    return <p className="py-4 text-body-sm text-danger">{companies.error.message}</p>;
  }
  if (companies.data.companies.length === 0) {
    return (
      <div className="grid place-items-center px-6 py-10 text-center">
        <div>
          <p className="text-body-sm font-medium text-ink-2">No companies tracked</p>
          <p className="mt-1 text-micro text-mute">
            Paste a list above and probe — confirmed companies are picked up by the next{" "}
            <span className="font-mono">scan --ats</span>.
          </p>
        </div>
      </div>
    );
  }
  return <CompaniesTable companies={companies.data.companies} />;
}

/**
 * One-off add: slug (or board URL) + vendor. Probe resolves the entry and
 * pre-selects the vendor; Add needs both fields pinned.
 */
function SingleAddForm() {
  const [slug, setSlug] = useState("");
  const [vendor, setVendor] = useState<CompanyVendor | "">("");
  const probe = useProbeCompanies();
  const add = useAddCompany();

  const runProbeOne = () => {
    const entry = slug.trim();
    if (entry === "" || probe.isPending) {
      return;
    }
    probe.mutate([entry], {
      onSuccess: ({ results }) => {
        const result = results[0];
        if (result?.slug != null && isVendor(result.vendor)) {
          setSlug(result.slug);
          setVendor(result.vendor);
          return;
        }
        toast({
          variant: "destructive",
          title: "Probe found no vendor",
          description: result?.error ?? "No ATS board answered for this entry.",
        });
      },
      onError: (error) =>
        toast({ variant: "destructive", title: "Probe failed", description: error.message }),
    });
  };

  const submit = () => {
    const trimmed = slug.trim().toLowerCase();
    if (trimmed === "" || vendor === "" || add.isPending) {
      return;
    }
    add.mutate(
      { slug: trimmed, vendor },
      {
        onSuccess: (company) => {
          toast({ title: `${company.slug} added` });
          setSlug("");
          setVendor("");
        },
        onError: (error) =>
          toast({ variant: "destructive", title: "Add failed", description: error.message }),
      },
    );
  };

  return (
    <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-hairline pt-3">
      <span className="text-micro text-mute">Or add one:</span>
      <Input
        value={slug}
        onChange={(event) => setSlug(event.target.value)}
        placeholder="slug or board URL"
        aria-label="Company slug"
        className="w-56 font-mono"
      />
      <Select value={vendor} onValueChange={(value) => setVendor(value as CompanyVendor)}>
        <SelectTrigger className="w-36" aria-label="Vendor">
          <SelectValue placeholder="vendor" />
        </SelectTrigger>
        <SelectContent>
          {VENDORS.map((name) => (
            <SelectItem key={name} value={name}>
              {name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Button size="sm" disabled={slug.trim() === "" || probe.isPending} onClick={runProbeOne}>
        {probe.isPending ? "Probing…" : "Probe"}
      </Button>
      <Button
        variant="primary"
        size="sm"
        disabled={slug.trim() === "" || vendor === "" || add.isPending}
        onClick={submit}
      >
        Add
      </Button>
    </div>
  );
}
