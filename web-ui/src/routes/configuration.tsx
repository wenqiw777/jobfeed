import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Checkbox from "@cloudscape-design/components/checkbox";
import Container from "@cloudscape-design/components/container";
import ContentLayout from "@cloudscape-design/components/content-layout";
import ExpandableSection from "@cloudscape-design/components/expandable-section";
import Form from "@cloudscape-design/components/form";
import FormField from "@cloudscape-design/components/form-field";
import Header from "@cloudscape-design/components/header";
import Input from "@cloudscape-design/components/input";
import SpaceBetween from "@cloudscape-design/components/space-between";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Textarea from "@cloudscape-design/components/textarea";
import { type FormEvent, type ReactNode, useState } from "react";
import { useNavigate } from "react-router";

import {
  type EditableConfiguration,
  useConfiguration,
  useSaveConfiguration,
} from "@/api/configuration";
import type { components } from "@/api/types.gen";

type Sources = components["schemas"]["SourcesConfig"];
type SourceKey = keyof Sources;
type LinkedInSearch = string | components["schemas"]["SourcesLinkedInSearchConfig"];

/** First-run onboarding and persistent local workspace settings. */
export default function ConfigurationPage() {
  const current = useConfiguration().data!;
  const [form, setForm] = useState<EditableConfiguration>(() => {
    const editable: Partial<typeof current> = structuredClone(current);
    delete editable.configured;
    return structuredClone(editable);
  });
  const [validation, setValidation] = useState<string | null>(null);
  const save = useSaveConfiguration();
  const navigate = useNavigate();
  const isFirstRun = !current.configured;

  function updateSection<K extends keyof EditableConfiguration>(
    section: K,
    value: NonNullable<EditableConfiguration[K]>,
  ) {
    setForm((previous) => ({ ...previous, [section]: value }));
  }

  function updateSource<K extends SourceKey>(source: K, value: NonNullable<Sources[K]>) {
    updateSection("sources", { ...form.sources, [source]: value });
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    const error = validateSources(form.sources!);
    setValidation(error);
    if (error !== null) return;
    save.mutate(form, { onSuccess: () => navigate("/triage", { replace: true }) });
  }

  const llm = form.llm!;
  const scoring = form.scoring!;
  const sources = form.sources!;
  const filters = form.hard_filters!;
  const error = validation ?? save.error?.message;

  return (
    <div data-testid="cloudscape-configuration" className="jobfeed-configuration">
      <ContentLayout
        maxContentWidth={1180}
        header={
          <Header
            variant="h1"
            description="Choose what Jobfeed watches and how it scores. Settings and job data stay on this computer."
          >
            {isFirstRun ? "Set up your feed" : "Workspace settings"}
          </Header>
        }
      >
        <form onSubmit={submit}>
          <Form
            errorText={error}
            errorIconAriaLabel="Error"
            actions={
              <SpaceBetween direction="horizontal" size="xs" alignItems="center">
                <StatusIndicator type="success">
                  Changes apply without a restart
                </StatusIndicator>
                <Button
                  variant="primary"
                  formAction="submit"
                  loading={save.isPending}
                  loadingText="Saving settings"
                >
                  {isFirstRun ? "Save and open Jobfeed" : "Save changes"}
                </Button>
              </SpaceBetween>
            }
          >
            <SpaceBetween size="l">
              <LocalDataNotice />
              <SettingsSection
                title="Scoring profile"
                description="Models, evidence, and limits used to evaluate each role."
              >
                <div className="jobfeed-form-grid">
                  <Field label="Master resume" wide>
                    <Input value={llm.master_resume_path} onChange={({ detail }) => updateSection("llm", { ...llm, master_resume_path: detail.value })} />
                  </Field>
                  <Field label="Fast score model">
                    <Input value={llm.stage_a} onChange={({ detail }) => updateSection("llm", { ...llm, stage_a: detail.value })} />
                  </Field>
                  <Field label="Deep review model">
                    <Input value={llm.stage_b} onChange={({ detail }) => updateSection("llm", { ...llm, stage_b: detail.value })} />
                  </Field>
                  <NumberField label="Score threshold" value={scoring.stage_a_threshold} min={0} max={100} onChange={(value) => updateSection("scoring", { ...scoring, stage_a_threshold: value })} />
                  <NumberField label="Daily scoring limit" value={llm.max_daily_score_calls} min={0} onChange={(value) => updateSection("llm", { ...llm, max_daily_score_calls: value })} />
                  <NumberField label="Daily cost limit (USD)" value={llm.max_daily_cost_usd} min={0} step="0.5" onChange={(value) => updateSection("llm", { ...llm, max_daily_cost_usd: value })} />
                  <NumberField label="Parallel evaluations" value={llm.max_concurrent} min={1} onChange={(value) => updateSection("llm", { ...llm, max_concurrent: value })} />
                </div>
                <Box variant="small" color="text-body-secondary">
                  API keys are read from <code>{llm.openai_compat_api_key_env}</code> and are never stored in this form.
                </Box>
              </SettingsSection>

              <SettingsSection title="Job sources" description="Start narrow; additional feeds can be enabled later.">
                <SourceToggle label="Company career pages" detail="Greenhouse, Lever and Ashby companies" checked={sources.ats!.enabled} onChange={(enabled) => updateSource("ats", { ...sources.ats!, enabled })}>
                  <ListField label="Company slugs" value={sources.ats!.seed_companies} onChange={(value) => updateSource("ats", { ...sources.ats!, seed_companies: value })} />
                </SourceToggle>
                <SourceToggle label="LinkedIn guest search" detail="Anonymous search; no browser login" checked={sources.linkedin_guest!.enabled} onChange={(enabled) => updateSource("linkedin_guest", { ...sources.linkedin_guest!, enabled })}>
                  <ListField label="LinkedIn search URLs" value={sources.linkedin_guest!.search_urls} onChange={(value) => updateSource("linkedin_guest", { ...sources.linkedin_guest!, search_urls: value })} />
                </SourceToggle>
                <SourceToggle label="Indeed search" detail="JobSpy search URLs" checked={sources.indeed!.enabled} onChange={(enabled) => updateSource("indeed", { ...sources.indeed!, enabled })}>
                  <ListField label="Indeed search URLs" value={sources.indeed!.search_urls} onChange={(value) => updateSource("indeed", { ...sources.indeed!, search_urls: value })} />
                </SourceToggle>
                <SourceToggle label="SpeedyApply lists" detail="Curated GitHub markdown feeds" checked={sources.speedyapply!.enabled} onChange={(enabled) => updateSource("speedyapply", { ...sources.speedyapply!, enabled })}>
                  <ListField label="SpeedyApply URLs" value={sources.speedyapply!.search_urls} onChange={(value) => updateSource("speedyapply", { ...sources.speedyapply!, search_urls: value })} />
                </SourceToggle>
              </SettingsSection>

              <SettingsSection title="Signal filters" description="Remove noise before any model call.">
                <div className="jobfeed-form-grid">
                  <ListField label="Allowed locations" value={filters.location_allowlist} onChange={(value) => updateSection("hard_filters", { ...filters, location_allowlist: value })} />
                  <ListField label="Blocked locations" value={filters.location_blocklist} onChange={(value) => updateSection("hard_filters", { ...filters, location_blocklist: value })} />
                  <ListField label="Blocked companies" value={filters.company_blocklist} onChange={(value) => updateSection("hard_filters", { ...filters, company_blocklist: value })} />
                  <ListField label="Large companies" value={filters.big_company_list} onChange={(value) => updateSection("hard_filters", { ...filters, big_company_list: value })} />
                  <Field label="Only jobs posted within days">
                    <Input type="number" placeholder="Any age" value={filters.posted_within_days?.toString() ?? ""} nativeInputAttributes={{ min: 1 }} onChange={({ detail }) => updateSection("hard_filters", { ...filters, posted_within_days: detail.value === "" ? null : Number(detail.value) })} />
                  </Field>
                  <NumberField label="Large-company age limit" value={filters.big_company_days} min={1} onChange={(value) => updateSection("hard_filters", { ...filters, big_company_days: value })} />
                </div>
                <Checkbox checked={scoring.ml_gate_enabled} onChange={({ detail }) => updateSection("scoring", { ...scoring, ml_gate_enabled: detail.checked })}>
                  Use the local ML gate before paid scoring
                </Checkbox>
              </SettingsSection>

              <ExpandableSection variant="container" headerText="Advanced settings" headerDescription="Custom model endpoints, calibration, and authenticated search.">
                <SpaceBetween size="m">
                  <div className="jobfeed-form-grid">
                    <Field label="Personal calibration file" wide><Input placeholder="Optional Markdown path" value={llm.preamble_personal_path ?? ""} onChange={({ detail }) => updateSection("llm", { ...llm, preamble_personal_path: detail.value || null })} /></Field>
                    <Field label="OpenAI-compatible base URL"><Input value={llm.openai_compat_base_url} onChange={({ detail }) => updateSection("llm", { ...llm, openai_compat_base_url: detail.value })} /></Field>
                    <Field label="API key environment variable"><Input value={llm.openai_compat_api_key_env} onChange={({ detail }) => updateSection("llm", { ...llm, openai_compat_api_key_env: detail.value })} /></Field>
                    <NumberField label="Default evaluation batch" value={scoring.default_eval_limit} min={0} onChange={(value) => updateSection("scoring", { ...scoring, default_eval_limit: value })} />
                    <NumberField label="ML candidate cap" value={form.ml_gate!.max_candidates} min={1} onChange={(value) => updateSection("ml_gate", { ...form.ml_gate!, max_candidates: value })} />
                  </div>
                  <SourceToggle label="Authenticated LinkedIn search" detail="Uses your local browser profile" checked={sources.linkedin!.enabled} onChange={(enabled) => updateSource("linkedin", { ...sources.linkedin!, enabled })}>
                    <ListField label="Authenticated LinkedIn search URLs" value={linkedinValues(sources.linkedin!.search_urls)} onChange={(value) => updateSource("linkedin", { ...sources.linkedin!, search_urls: value })} />
                  </SourceToggle>
                </SpaceBetween>
              </ExpandableSection>
            </SpaceBetween>
          </Form>
        </form>
      </ContentLayout>
    </div>
  );
}

function LocalDataNotice() {
  return <Alert type="info" header="Local by default">Your workspace is stored in <code>data/jobfeed.sqlite</code>. Ordinary use does not require Docker or PostgreSQL.</Alert>;
}

function SettingsSection({ title, description, children }: { title: string; description: string; children: ReactNode }) {
  return <Container header={<Header variant="h2" description={description}>{title}</Header>}><SpaceBetween size="m">{children}</SpaceBetween></Container>;
}

function Field({ label, wide = false, children }: { label: string; wide?: boolean; children: ReactNode }) {
  return <div className={wide ? "jobfeed-form-wide" : undefined}><FormField label={label}>{children}</FormField></div>;
}

function NumberField({ label, value, min, max, step, onChange }: { label: string; value: number; min: number; max?: number; step?: string; onChange: (value: number) => void }) {
  return <Field label={label}><Input type="number" value={String(value)} step={step === "0.5" ? 0.5 : undefined} nativeInputAttributes={{ min, max }} onChange={({ detail }) => onChange(Number(detail.value))} /></Field>;
}

function SourceToggle({ label, detail, checked, onChange, children }: { label: string; detail: string; checked: boolean; onChange: (checked: boolean) => void; children: ReactNode }) {
  return <div className="jobfeed-source-toggle"><Checkbox ariaLabel={label} checked={checked} onChange={({ detail: event }) => onChange(event.checked)}><span className="jobfeed-source-label"><strong>{label}</strong><span>{detail}</span></span></Checkbox>{checked && <div className="jobfeed-source-fields">{children}</div>}</div>;
}

function ListField({ label, value, onChange }: { label: string; value?: string[]; onChange: (value: string[]) => void }) {
  return <Field label={`${label} — one per line`}><Textarea ariaLabel={label} rows={3} resize="vertical" value={lines(value)} onChange={({ detail }) => onChange(parseLines(detail.value))} /></Field>;
}

function lines(values: string[] | undefined) { return (values ?? []).join("\n"); }
function linkedinValues(values: LinkedInSearch[] | undefined) { return (values ?? []).map((value) => typeof value === "string" ? value : value.url); }
function parseLines(value: string) { return value.split("\n").map((item) => item.trim()).filter(Boolean); }

function validateSources(sources: Sources): string | null {
  if (sources.linkedin_guest?.enabled && !sources.linkedin_guest.search_urls?.length) return "Add at least one LinkedIn search URL.";
  if (sources.indeed?.enabled && !sources.indeed.search_urls?.length) return "Add at least one Indeed search URL.";
  if (sources.speedyapply?.enabled && !sources.speedyapply.search_urls?.length) return "Add at least one SpeedyApply list URL.";
  if (sources.linkedin?.enabled && !sources.linkedin.search_urls?.length) return "Add at least one authenticated LinkedIn search URL.";
  return null;
}
