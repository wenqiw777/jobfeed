import { type ComponentProps, type FormEvent, type ReactNode, useState } from "react";
import { ArrowRight, Check, Database } from "lucide-react";
import { useNavigate } from "react-router";

import {
  type EditableConfiguration,
  useConfiguration,
  useSaveConfiguration,
} from "@/api/configuration";
import type { components } from "@/api/types.gen";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";

type Sources = components["schemas"]["SourcesConfig"];
type SourceKey = keyof Sources;
type LinkedInSearch = string | components["schemas"]["SourcesLinkedInSearchConfig"];

const textareaClass =
  "min-h-20 w-full resize-y rounded-control border border-border-strong bg-surface px-2.5 py-2 font-mono text-body-sm text-ink placeholder:text-mute focus-visible:border-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent";

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

  function updateSource<K extends SourceKey>(
    source: K,
    value: NonNullable<Sources[K]>,
  ) {
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

  return (
    <main className="min-h-screen bg-bg px-4 py-8 sm:px-6 lg:py-12">
      <form onSubmit={submit} className="mx-auto grid max-w-6xl gap-6 lg:grid-cols-[17rem_minmax(0,1fr)]">
        <aside className="self-start lg:sticky lg:top-10">
          <p className="font-mono text-micro uppercase tracking-[0.16em] text-accent">
            {isFirstRun ? "First run" : "Workspace settings"}
          </p>
          <h1 className="mt-3 text-[2rem] font-semibold leading-[1.05] tracking-[-0.04em] text-ink">
            {isFirstRun ? "Set up your feed" : "Tune your feed"}
          </h1>
          <p className="mt-4 max-w-sm text-body leading-6 text-ink-2">
            Choose what Jobfeed watches and how it scores. Your data stays in the
            local SQLite file.
          </p>
          <ol className="mt-7 space-y-1 border-l border-border pl-4 text-body-sm">
            <SetupStep label="Scoring profile" detail="Resume, models, daily limits" />
            <SetupStep label="Job sources" detail="Company ATS and search feeds" />
            <SetupStep label="Signal filters" detail="Location, companies, freshness" />
          </ol>
          <div className="mt-7 flex items-start gap-2 border-t border-border pt-4 text-body-sm text-mute">
            <Database className="mt-0.5 h-4 w-4 shrink-0" />
            <span>Stored locally in data/jobfeed.sqlite</span>
          </div>
        </aside>

        <div className="space-y-4">
          <Section number="01" title="Scoring profile" description="The evidence Jobfeed uses to judge each role.">
            <div className="grid gap-4 md:grid-cols-2">
              <Field label="Master resume" wide>
                <Input
                  value={llm.master_resume_path}
                  onChange={(event) => updateSection("llm", { ...llm, master_resume_path: event.target.value })}
                />
              </Field>
              <Field label="Fast score model">
                <Input value={llm.stage_a} onChange={(event) => updateSection("llm", { ...llm, stage_a: event.target.value })} />
              </Field>
              <Field label="Deep review model">
                <Input value={llm.stage_b} onChange={(event) => updateSection("llm", { ...llm, stage_b: event.target.value })} />
              </Field>
              <Field label="Score threshold">
                <NumberInput value={scoring.stage_a_threshold} min={0} max={100} onChange={(value) => updateSection("scoring", { ...scoring, stage_a_threshold: value })} />
              </Field>
              <Field label="Daily scoring limit">
                <NumberInput value={llm.max_daily_score_calls} min={0} onChange={(value) => updateSection("llm", { ...llm, max_daily_score_calls: value })} />
              </Field>
              <Field label="Daily cost limit (USD)">
                <NumberInput value={llm.max_daily_cost_usd} min={0} step="0.5" onChange={(value) => updateSection("llm", { ...llm, max_daily_cost_usd: value })} />
              </Field>
              <Field label="Parallel evaluations">
                <NumberInput value={llm.max_concurrent} min={1} onChange={(value) => updateSection("llm", { ...llm, max_concurrent: value })} />
              </Field>
            </div>
            <p className="mt-4 text-body-sm text-mute">
              API keys are never stored here. Models using openai-compat read the environment variable named <span className="font-mono text-ink-2">{llm.openai_compat_api_key_env}</span>.
            </p>
          </Section>

          <Section number="02" title="Job sources" description="Start narrow. You can add more feeds from Settings later.">
            <SourceToggle label="Company career pages" detail="Greenhouse, Lever and Ashby companies" checked={sources.ats!.enabled} onChange={(enabled) => updateSource("ats", { ...sources.ats!, enabled })}>
              <Field label="Company slugs — one per line">
                <textarea aria-label="Company slugs" className={textareaClass} value={lines(sources.ats!.seed_companies)} onChange={(event) => updateSource("ats", { ...sources.ats!, seed_companies: parseLines(event.target.value) })} />
              </Field>
            </SourceToggle>
            <SourceToggle label="LinkedIn guest search" detail="Anonymous search; no browser login" checked={sources.linkedin_guest!.enabled} onChange={(enabled) => updateSource("linkedin_guest", { ...sources.linkedin_guest!, enabled })}>
              <Field label="Search URLs — one per line">
                <textarea aria-label="LinkedIn search URLs" className={textareaClass} value={lines(sources.linkedin_guest!.search_urls)} onChange={(event) => updateSource("linkedin_guest", { ...sources.linkedin_guest!, search_urls: parseLines(event.target.value) })} />
              </Field>
            </SourceToggle>
            <SourceToggle label="Indeed search" detail="JobSpy search URLs" checked={sources.indeed!.enabled} onChange={(enabled) => updateSource("indeed", { ...sources.indeed!, enabled })}>
              <Field label="Search URLs — one per line">
                <textarea aria-label="Indeed search URLs" className={textareaClass} value={lines(sources.indeed!.search_urls)} onChange={(event) => updateSource("indeed", { ...sources.indeed!, search_urls: parseLines(event.target.value) })} />
              </Field>
            </SourceToggle>
            <SourceToggle label="SpeedyApply lists" detail="Curated GitHub markdown feeds" checked={sources.speedyapply!.enabled} onChange={(enabled) => updateSource("speedyapply", { ...sources.speedyapply!, enabled })}>
              <Field label="List URLs — one per line">
                <textarea aria-label="SpeedyApply URLs" className={textareaClass} value={lines(sources.speedyapply!.search_urls)} onChange={(event) => updateSource("speedyapply", { ...sources.speedyapply!, search_urls: parseLines(event.target.value) })} />
              </Field>
            </SourceToggle>
          </Section>

          <Section number="03" title="Signal filters" description="Cut noise before any model call.">
            <div className="grid gap-4 md:grid-cols-2">
              <ListField label="Allowed locations" value={filters.location_allowlist} onChange={(value) => updateSection("hard_filters", { ...filters, location_allowlist: value })} />
              <ListField label="Blocked locations" value={filters.location_blocklist} onChange={(value) => updateSection("hard_filters", { ...filters, location_blocklist: value })} />
              <ListField label="Blocked companies" value={filters.company_blocklist} onChange={(value) => updateSection("hard_filters", { ...filters, company_blocklist: value })} />
              <ListField label="Large companies" value={filters.big_company_list} onChange={(value) => updateSection("hard_filters", { ...filters, big_company_list: value })} />
              <Field label="Only jobs posted within days">
                <Input type="number" min={1} placeholder="Any age" value={filters.posted_within_days ?? ""} onChange={(event) => updateSection("hard_filters", { ...filters, posted_within_days: event.target.value === "" ? null : Number(event.target.value) })} />
              </Field>
              <Field label="Large-company age limit">
                <NumberInput value={filters.big_company_days} min={1} onChange={(value) => updateSection("hard_filters", { ...filters, big_company_days: value })} />
              </Field>
            </div>
            <label className="mt-5 flex items-center gap-2 text-body-sm font-medium text-ink">
              <Checkbox checked={scoring.ml_gate_enabled} onCheckedChange={(checked) => updateSection("scoring", { ...scoring, ml_gate_enabled: checked === true })} />
              Use the local ML gate before paid scoring
            </label>
          </Section>

          <details className="rounded-panel border border-border bg-surface p-5 shadow-[var(--shadow-1)] sm:p-6">
            <summary className="flex cursor-pointer list-none items-center justify-between text-h2 text-ink">
              Advanced settings
              <span className="font-mono text-micro font-normal text-mute">optional</span>
            </summary>
            <p className="mb-5 mt-2 text-body-sm text-mute">Custom model endpoints, calibration notes, and authenticated LinkedIn search.</p>
            <div className="grid gap-4 md:grid-cols-2">
              <Field label="Personal calibration file" wide>
                <Input placeholder="Optional Markdown path" value={llm.preamble_personal_path ?? ""} onChange={(event) => updateSection("llm", { ...llm, preamble_personal_path: event.target.value || null })} />
              </Field>
              <Field label="OpenAI-compatible base URL">
                <Input value={llm.openai_compat_base_url} onChange={(event) => updateSection("llm", { ...llm, openai_compat_base_url: event.target.value })} />
              </Field>
              <Field label="API key environment variable">
                <Input value={llm.openai_compat_api_key_env} onChange={(event) => updateSection("llm", { ...llm, openai_compat_api_key_env: event.target.value })} />
              </Field>
              <Field label="Default evaluation batch">
                <NumberInput value={scoring.default_eval_limit} min={0} onChange={(value) => updateSection("scoring", { ...scoring, default_eval_limit: value })} />
              </Field>
              <Field label="ML candidate cap">
                <NumberInput value={form.ml_gate!.max_candidates} min={1} onChange={(value) => updateSection("ml_gate", { ...form.ml_gate!, max_candidates: value })} />
              </Field>
            </div>
            <div className="mt-5 border-t border-border pt-4">
              <SourceToggle label="Authenticated LinkedIn search" detail="Uses your local Playwright profile" checked={sources.linkedin!.enabled} onChange={(enabled) => updateSource("linkedin", { ...sources.linkedin!, enabled })}>
                <Field label="Search URLs — one per line">
                  <textarea aria-label="Authenticated LinkedIn search URLs" className={textareaClass} value={linkedinLines(sources.linkedin!.search_urls)} onChange={(event) => updateSource("linkedin", { ...sources.linkedin!, search_urls: parseLines(event.target.value) })} />
                </Field>
              </SourceToggle>
            </div>
          </details>

          {(validation ?? save.error?.message) && (
            <p role="alert" className="rounded-control border border-danger bg-danger-bg px-3 py-2 text-body-sm text-danger">
              {validation ?? save.error?.message}
            </p>
          )}
          <div className="flex items-center justify-between border-t border-border pt-5">
            <p className="flex items-center gap-1.5 text-body-sm text-mute"><Check className="h-4 w-4 text-apply" /> Valid settings apply without a restart.</p>
            <Button type="submit" variant="primary" size="md" disabled={save.isPending}>
              {save.isPending ? "Saving…" : isFirstRun ? "Save and open Jobfeed" : "Save changes"}
              {!save.isPending && <ArrowRight aria-hidden="true" className="h-4 w-4" />}
            </Button>
          </div>
        </div>
      </form>
    </main>
  );
}

function SetupStep({ label, detail }: { label: string; detail: string }) {
  return <li className="py-2"><span className="block font-medium text-ink">{label}</span><span className="text-mute">{detail}</span></li>;
}

function Section({ number, title, description, children }: { number: string; title: string; description: string; children: ReactNode }) {
  return <section className="rounded-panel border border-border bg-surface p-5 shadow-[var(--shadow-1)] sm:p-6"><div className="mb-5 flex items-start gap-3"><span className="font-mono text-micro text-accent">{number}</span><div><h2 className="text-h2 text-ink">{title}</h2><p className="mt-1 text-body-sm text-mute">{description}</p></div></div>{children}</section>;
}

function Field({ label, wide = false, children }: { label: string; wide?: boolean; children: ReactNode }) {
  return <label className={wide ? "md:col-span-2" : ""}><span className="mb-1.5 block text-body-sm font-medium text-ink-2">{label}</span>{children}</label>;
}

function NumberInput({ value, onChange, ...props }: { value: number; onChange: (value: number) => void } & Omit<ComponentProps<typeof Input>, "value" | "onChange">) {
  return <Input type="number" value={value} onChange={(event) => onChange(Number(event.target.value))} {...props} />;
}

function SourceToggle({ label, detail, checked, onChange, children }: { label: string; detail: string; checked: boolean; onChange: (checked: boolean) => void; children: ReactNode }) {
  return <div className="border-t border-border py-4 first:border-t-0 first:pt-0"><label className="flex cursor-pointer items-start gap-3"><Checkbox aria-label={label} checked={checked} onCheckedChange={(value) => onChange(value === true)} className="mt-0.5" /><span><span className="block text-body-sm font-medium text-ink">{label}</span><span className="text-body-sm text-mute">{detail}</span></span></label>{checked && <div className="ml-7 mt-3">{children}</div>}</div>;
}

function ListField({ label, value, onChange }: { label: string; value?: string[]; onChange: (value: string[]) => void }) {
  return <Field label={`${label} — one per line`}><textarea aria-label={label} className={textareaClass} value={lines(value)} onChange={(event) => onChange(parseLines(event.target.value))} /></Field>;
}

function lines(values: string[] | undefined): string { return (values ?? []).join("\n"); }
function linkedinLines(values: LinkedInSearch[] | undefined): string {
  return (values ?? []).map((value) => typeof value === "string" ? value : value.url).join("\n");
}
function parseLines(value: string): string[] { return value.split("\n").map((item) => item.trim()).filter(Boolean); }

function validateSources(sources: Sources): string | null {
  if (sources.linkedin_guest?.enabled && !sources.linkedin_guest.search_urls?.length) return "Add at least one LinkedIn search URL.";
  if (sources.indeed?.enabled && !sources.indeed.search_urls?.length) return "Add at least one Indeed search URL.";
  if (sources.speedyapply?.enabled && !sources.speedyapply.search_urls?.length) return "Add at least one SpeedyApply list URL.";
  if (sources.linkedin?.enabled && !sources.linkedin.search_urls?.length) return "Add at least one authenticated LinkedIn search URL.";
  return null;
}
