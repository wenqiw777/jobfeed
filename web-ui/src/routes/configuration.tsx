import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Checkbox from "@cloudscape-design/components/checkbox";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import Container from "@cloudscape-design/components/container";
import ContentLayout from "@cloudscape-design/components/content-layout";
import ExpandableSection from "@cloudscape-design/components/expandable-section";
import Form from "@cloudscape-design/components/form";
import FormField from "@cloudscape-design/components/form-field";
import Header from "@cloudscape-design/components/header";
import Input from "@cloudscape-design/components/input";
import Select from "@cloudscape-design/components/select";
import type { SelectProps } from "@cloudscape-design/components/select";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Textarea from "@cloudscape-design/components/textarea";
import { type FormEvent, type ReactNode, useState } from "react";
import { useNavigate } from "react-router";

import {
  type ConfigurationResponse,
  type EditableConfiguration,
  useConfiguration,
  useSaveConfiguration,
} from "@/api/configuration";
import type { components } from "@/api/types.gen";
import OnboardingProviderPage from "@/routes/onboarding-provider";

type Sources = components["schemas"]["SourcesConfig"];
type SourceKey = keyof Sources;
type LinkedInSearch = string | components["schemas"]["SourcesLinkedInSearchConfig"];

const MODEL_OPTIONS: SelectProps.Options = [
  {
    label: "Signed-in local apps",
    description: "Uses your installed Codex or Claude app. No API key required.",
    options: [
      { label: "GPT-5.6 Luna — Codex login", value: "codex-cli/gpt-5.6-luna" },
      { label: "GPT-5.6 Terra — Codex login", value: "codex-cli/gpt-5.6-terra" },
      { label: "GPT-5.6 Sol — Codex login", value: "codex-cli/gpt-5.6-sol" },
      { label: "Claude Sonnet 5 — Claude login", value: "claude-cli/claude-sonnet-5" },
      { label: "Claude Opus 4.8 — Claude login", value: "claude-cli/claude-opus-4-8" },
      { label: "Claude Fable 5 — Claude login", value: "claude-cli/claude-fable-5" },
    ],
  },
  {
    label: "Direct API",
    description: "Calls the configured OpenAI-compatible endpoint with an API key.",
    options: [
      { label: "GPT-5.6 Luna — API key", value: "openai-compat/gpt-5.6-luna" },
      { label: "GPT-5.6 Terra — API key", value: "openai-compat/gpt-5.6-terra" },
      { label: "GPT-5.6 Sol — API key", value: "openai-compat/gpt-5.6-sol" },
    ],
  },
  {
    label: "Older local models",
    description: "Still available for existing configurations.",
    options: [
      { label: "GPT-5.5 — Codex login", value: "codex-cli/gpt-5.5" },
      { label: "GPT-5.4 — Codex login", value: "codex-cli/gpt-5.4" },
      { label: "GPT-5.4 mini — Codex login", value: "codex-cli/gpt-5.4-mini" },
    ],
  },
];

/** First-run onboarding and persistent local workspace settings. */
export default function ConfigurationPage() {
  const current = useConfiguration().data!;
  if (!current.configured) return <OnboardingProviderPage />;
  return <WorkspaceSettings current={current} />;
}

function WorkspaceSettings({ current }: { current: ConfigurationResponse }) {
  const [form, setForm] = useState<EditableConfiguration>(() => {
    const editable: Partial<typeof current> = structuredClone(current);
    delete editable.configured;
    delete editable.ml_gate_performance;
    return structuredClone(editable);
  });
  const [validation, setValidation] = useState<string | null>(null);
  const save = useSaveConfiguration();
  const navigate = useNavigate();

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
  const performance = current.ml_gate_performance;
  const error = validation ?? save.error?.message;

  return (
    <div data-testid="cloudscape-configuration">
      <ContentLayout
        maxContentWidth={1180}
        header={
          <Header
            variant="h1"
            description="Choose what Jobfeed watches and how it scores. Settings and job data stay on this computer."
          >
            Workspace settings
          </Header>
        }
      >
        <form onSubmit={submit}>
          <Form
            errorText={error}
            errorIconAriaLabel="Error"
            actions={
              <SpaceBetween direction="horizontal" size="xs" alignItems="center">
                <Box color="text-body-secondary">Saved changes apply without a restart.</Box>
                <Button
                  variant="primary"
                  formAction="submit"
                  loading={save.isPending}
                  loadingText="Saving settings"
                >
                  Save changes
                </Button>
              </SpaceBetween>
            }
          >
            <SpaceBetween size="l">
              <LocalDataNotice />
              <SettingsSection
                title="Evaluation settings"
                description="Models, evidence, and limits used to evaluate each role."
              >
                <ColumnLayout columns={2} minColumnWidth={280}>
                  <Field label="Resume file">
                    <Input value={llm.master_resume_path} onChange={({ detail }) => updateSection("llm", { ...llm, master_resume_path: detail.value })} />
                  </Field>
                  <Field label="Evaluation model">
                    <Select
                      selectedOption={modelOption(llm.stage_b)}
                      options={MODEL_OPTIONS}
                      onChange={({ detail }) => updateSection("llm", {
                        ...llm,
                        stage_a: detail.selectedOption.value!,
                        stage_b: detail.selectedOption.value!,
                      })}
                    />
                  </Field>
                  <NumberField label="Daily model call limit" value={llm.max_daily_score_calls} min={0} onChange={(value) => updateSection("llm", { ...llm, max_daily_score_calls: value })} />
                  <NumberField label="Daily cost limit (USD)" value={llm.max_daily_cost_usd} min={0} step="0.5" onChange={(value) => updateSection("llm", { ...llm, max_daily_cost_usd: value })} />
                  <NumberField label="Parallel evaluations" value={llm.max_concurrent} min={1} onChange={(value) => updateSection("llm", { ...llm, max_concurrent: value })} />
                </ColumnLayout>
                <Box variant="small" color="text-body-secondary">
                  Signed-in models use your installed Codex or Claude app. API-key models
                  call the configured endpoint with <code>{llm.openai_compat_api_key_env}</code>;
                  Jobfeed never stores the key.
                </Box>
              </SettingsSection>

              <SettingsSection title="Job sources" description="Start narrow; additional feeds can be enabled later.">
                <SourceToggle label="Company career pages" detail="Greenhouse, Lever and Ashby companies" checked={sources.ats!.enabled} onChange={(enabled) => updateSource("ats", { ...sources.ats!, enabled })}>
                  <SpaceBetween size="m">
                    <NumberField label="Company career pages maximum jobs per scan" value={sources.ats!.max_jobs} min={1} onChange={(value) => updateSource("ats", { ...sources.ats!, max_jobs: value })} />
                    <ListField label="ATS target job titles" value={sources.ats!.title_keywords} onChange={(value) => updateSource("ats", { ...sources.ats!, title_keywords: value })} />
                    <Box variant="small" color="text-body-secondary">Only matching ATS titles count toward this source limit. Onboarding fills these from your confirmed searches.</Box>
                    <ListField label="Company slugs" value={sources.ats!.seed_companies} onChange={(value) => updateSource("ats", { ...sources.ats!, seed_companies: value })} />
                  </SpaceBetween>
                </SourceToggle>
                <SourceToggle label="LinkedIn guest search" detail="Anonymous search; no browser login" checked={sources.linkedin_guest!.enabled} onChange={(enabled) => updateSource("linkedin_guest", { ...sources.linkedin_guest!, enabled })}>
                  <SpaceBetween size="m">
                    <NumberField label="LinkedIn guest maximum jobs per scan" value={sources.linkedin_guest!.max_jobs} min={1} onChange={(value) => updateSource("linkedin_guest", { ...sources.linkedin_guest!, max_jobs: value })} />
                    <ListField label="LinkedIn search URLs" value={sources.linkedin_guest!.search_urls} onChange={(value) => updateSource("linkedin_guest", { ...sources.linkedin_guest!, search_urls: value })} />
                  </SpaceBetween>
                </SourceToggle>
                <SourceToggle label="Indeed search" detail="JobSpy search URLs" checked={sources.indeed!.enabled} onChange={(enabled) => updateSource("indeed", { ...sources.indeed!, enabled })}>
                  <SpaceBetween size="m">
                    <NumberField label="Indeed maximum jobs per scan" value={sources.indeed!.max_jobs} min={1} onChange={(value) => updateSource("indeed", { ...sources.indeed!, max_jobs: value })} />
                    <ListField label="Indeed search URLs" value={sources.indeed!.search_urls} onChange={(value) => updateSource("indeed", { ...sources.indeed!, search_urls: value })} />
                  </SpaceBetween>
                </SourceToggle>
                <SourceToggle label="SpeedyApply lists" detail="Curated GitHub markdown feeds" checked={sources.speedyapply!.enabled} onChange={(enabled) => updateSource("speedyapply", { ...sources.speedyapply!, enabled })}>
                  <SpaceBetween size="m">
                    <NumberField label="SpeedyApply maximum jobs per scan" value={sources.speedyapply!.max_jobs} min={1} onChange={(value) => updateSource("speedyapply", { ...sources.speedyapply!, max_jobs: value })} />
                    <ListField label="SpeedyApply URLs" value={sources.speedyapply!.search_urls} onChange={(value) => updateSource("speedyapply", { ...sources.speedyapply!, search_urls: value })} />
                  </SpaceBetween>
                </SourceToggle>
              </SettingsSection>

              <SettingsSection title="Job filters" description="Exclude irrelevant jobs before evaluation.">
                <ColumnLayout columns={2} minColumnWidth={280}>
                  <ListField label="Allowed locations" value={filters.location_allowlist} onChange={(value) => updateSection("hard_filters", { ...filters, location_allowlist: value })} />
                  <ListField label="Blocked locations" value={filters.location_blocklist} onChange={(value) => updateSection("hard_filters", { ...filters, location_blocklist: value })} />
                  <ListField label="Blocked companies" value={filters.company_blocklist} onChange={(value) => updateSection("hard_filters", { ...filters, company_blocklist: value })} />
                  <ListField label="Large companies" value={filters.big_company_list} onChange={(value) => updateSection("hard_filters", { ...filters, big_company_list: value })} />
                  <Field label="Maximum job age (days)">
                    <Input type="number" placeholder="Any age" value={filters.posted_within_days?.toString() ?? ""} nativeInputAttributes={{ min: 1 }} onChange={({ detail }) => updateSection("hard_filters", { ...filters, posted_within_days: detail.value === "" ? null : Number(detail.value) })} />
                  </Field>
                  <NumberField label="Large-company maximum age (days)" value={filters.big_company_days} min={1} onChange={(value) => updateSection("hard_filters", { ...filters, big_company_days: value })} />
                </ColumnLayout>
                <Alert type={scoring.ml_gate_enabled ? "success" : "info"} header={`Personal job filter · ${form.ml_gate!.model_version}`}>
                  <SpaceBetween size="xs">
                    <Checkbox
                      checked={scoring.ml_gate_enabled}
                      onChange={({ detail }) => updateSection("scoring", { ...scoring, ml_gate_enabled: detail.checked })}
                    >
                      Enable personal ML filter
                    </Checkbox>
                    <Box variant="small" color="text-body-secondary">
                      {scoring.ml_gate_enabled
                        ? "Active. Jobs predicted irrelevant skip evaluation and remain recoverable in the Job library."
                        : "Uses the existing trained model immediately. Readiness checks remain advisory; recent recall can still pause filtering automatically."}
                    </Box>
                    {performance && (
                      <Box variant="small" color="text-body-secondary">
                        Model evaluation at threshold {performance.threshold}: {formatPercent(performance.recall)} recall · {formatPercent(performance.irrelevant_rejection)} irrelevant jobs rejected · {formatPercent(performance.precision)} precision · {formatPercent(performance.f1)} F1 · {performance.training_jobs.toLocaleString("en-US")} labeled jobs
                      </Box>
                    )}
                  </SpaceBetween>
                </Alert>
              </SettingsSection>

              <ExpandableSection variant="container" headerText="Advanced settings" headerDescription="Custom model endpoints, calibration, and authenticated search.">
                <SpaceBetween size="m">
                  <ColumnLayout columns={2} minColumnWidth={280}>
                    <Field label="Personal calibration file"><Input placeholder="Optional Markdown path" value={llm.preamble_personal_path ?? ""} onChange={({ detail }) => updateSection("llm", { ...llm, preamble_personal_path: detail.value || null })} /></Field>
                    <Field label="OpenAI-compatible base URL"><Input value={llm.openai_compat_base_url} onChange={({ detail }) => updateSection("llm", { ...llm, openai_compat_base_url: detail.value })} /></Field>
                    <Field label="API key environment variable"><Input value={llm.openai_compat_api_key_env} onChange={({ detail }) => updateSection("llm", { ...llm, openai_compat_api_key_env: detail.value })} /></Field>
                    <NumberField label="Maximum unique jobs to evaluate per run" value={scoring.default_eval_limit} min={1} onChange={(value) => updateSection("scoring", { ...scoring, default_eval_limit: value })} />
                    <NumberField label="Local filter candidate limit" value={form.ml_gate!.max_candidates} min={1} onChange={(value) => updateSection("ml_gate", { ...form.ml_gate!, max_candidates: value })} />
                  </ColumnLayout>
                  <SourceToggle label="Authenticated LinkedIn search" detail="Uses your local browser profile" checked={sources.linkedin!.enabled} onChange={(enabled) => updateSource("linkedin", { ...sources.linkedin!, enabled })}>
                    <SpaceBetween size="m">
                      <NumberField label="Authenticated LinkedIn maximum jobs per scan" value={sources.linkedin!.max_jobs} min={1} onChange={(value) => updateSource("linkedin", { ...sources.linkedin!, max_jobs: value })} />
                      <ListField label="Authenticated LinkedIn search URLs" value={linkedinValues(sources.linkedin!.search_urls)} onChange={(value) => updateSource("linkedin", { ...sources.linkedin!, search_urls: value })} />
                    </SpaceBetween>
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

function formatPercent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

function modelOption(value: string) {
  const option = MODEL_OPTIONS.flatMap((group) =>
    "options" in group ? group.options : [group],
  ).find(
    (candidate) => candidate.value === value,
  );
  return option ?? {
    label: value,
    value,
  };
}

function LocalDataNotice() {
  return <Alert type="info" header="Local by default">Your workspace is stored in <code>data/jobfeed.sqlite</code>. Ordinary use does not require Docker or PostgreSQL.</Alert>;
}

function SettingsSection({ title, description, children }: { title: string; description: string; children: ReactNode }) {
  return <Container header={<Header variant="h2" description={description}>{title}</Header>}><SpaceBetween size="m">{children}</SpaceBetween></Container>;
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <FormField label={label}>{children}</FormField>;
}

function NumberField({ label, value, min, max, step, onChange }: { label: string; value: number; min: number; max?: number; step?: string; onChange: (value: number) => void }) {
  return <Field label={label}><Input type="number" value={String(value)} step={step === "0.5" ? 0.5 : undefined} nativeInputAttributes={{ min, max }} onChange={({ detail }) => onChange(Number(detail.value))} /></Field>;
}

function SourceToggle({ label, detail, checked, onChange, children }: { label: string; detail: string; checked: boolean; onChange: (checked: boolean) => void; children: ReactNode }) {
  return (
    <Container>
      <SpaceBetween size="m">
        <Checkbox ariaLabel={label} checked={checked} onChange={({ detail: event }) => onChange(event.checked)}>
          <SpaceBetween size="xxs">
            <Box variant="strong">{label}</Box>
            <Box color="text-body-secondary">{detail}</Box>
          </SpaceBetween>
        </Checkbox>
        {checked && children}
      </SpaceBetween>
    </Container>
  );
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
