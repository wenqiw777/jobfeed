import Button from "@cloudscape-design/components/button";
import Container from "@cloudscape-design/components/container";
import ContentLayout from "@cloudscape-design/components/content-layout";
import FormField from "@cloudscape-design/components/form-field";
import Header from "@cloudscape-design/components/header";
import Input from "@cloudscape-design/components/input";
import Select from "@cloudscape-design/components/select";
import SpaceBetween from "@cloudscape-design/components/space-between";
import { useState } from "react";

import {
  useAddCompany,
  useCompanies,
  useProbeCompanies,
  type CompanyVendor,
} from "@/api/queries";
import { CompaniesTable } from "@/components/sources/CompaniesTable";
import { ProbeFlow } from "@/components/sources/ProbeFlow";
import { toast } from "@/components/ui/use-toast";
import { isVendor, VENDORS } from "@/lib/vendors";

const VENDOR_OPTIONS = VENDORS.map((vendor) => ({ label: vendor, value: vendor }));

/** Source-board inventory and ATS discovery controls. */
export default function SourcesPage() {
  const [includeRemoved, setIncludeRemoved] = useState(false);
  const companies = useCompanies(includeRemoved);

  return (
    <div data-testid="cloudscape-sources">
      <ContentLayout
        maxContentWidth={1180}
        header={
          <Header
            variant="h2"
            description="Discover ATS vendors, review every match, and control which company boards scans cover."
          >
            Company sources
          </Header>
        }
      >
        <SpaceBetween size="l">
          <Container
            header={
              <Header
                variant="h2"
                description="Paste board slugs or URLs. Probing identifies the vendor without changing the tracked list."
              >
                Add company boards
              </Header>
            }
          >
            <SpaceBetween size="l">
              <ProbeFlow />
              <SingleAddForm />
            </SpaceBetween>
          </Container>
          <CompaniesTable
            companies={companies.data?.companies ?? []}
            includeRemoved={includeRemoved}
            isLoading={companies.isPending}
            error={companies.error}
            onIncludeRemovedChange={setIncludeRemoved}
          />
        </SpaceBetween>
      </ContentLayout>
    </div>
  );
}

/** Adds a single company after an optional vendor probe. */
function SingleAddForm() {
  const [slug, setSlug] = useState("");
  const [vendor, setVendor] = useState<CompanyVendor | "">("");
  const probe = useProbeCompanies();
  const add = useAddCompany();

  const runProbeOne = () => {
    const entry = slug.trim();
    if (entry === "" || probe.isPending) return;
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
    if (trimmed === "" || vendor === "" || add.isPending) return;
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
    <Container
      header={
        <Header variant="h3" description="Use this compact path for a known board.">
          Add one company
        </Header>
      }
    >
      <SpaceBetween size="m">
        <div className="jobfeed-form-grid">
          <FormField label="Company slug or board URL">
            <Input
              value={slug}
              onChange={({ detail }) => setSlug(detail.value)}
              placeholder="acme or https://boards.greenhouse.io/acme"
              ariaLabel="Company slug"
              disabled={probe.isPending || add.isPending}
            />
          </FormField>
          <FormField label="ATS vendor">
            <Select
              selectedOption={
                vendor === "" ? null : { label: vendor, value: vendor }
              }
              options={VENDOR_OPTIONS}
              onChange={({ detail }) =>
                setVendor((detail.selectedOption.value ?? "") as CompanyVendor | "")
              }
              placeholder="Choose vendor"
              ariaLabel="Vendor"
              disabled={add.isPending}
            />
          </FormField>
        </div>
        <SpaceBetween direction="horizontal" size="xs">
          <Button
            disabled={slug.trim() === ""}
            loading={probe.isPending}
            loadingText="Probing company"
            onClick={runProbeOne}
          >
            Probe
          </Button>
          <Button
            variant="primary"
            disabled={slug.trim() === "" || vendor === ""}
            loading={add.isPending}
            loadingText="Adding company"
            onClick={submit}
          >
            Add
          </Button>
        </SpaceBetween>
      </SpaceBetween>
    </Container>
  );
}
