import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import Container from "@cloudscape-design/components/container";
import ContentLayout from "@cloudscape-design/components/content-layout";
import FormField from "@cloudscape-design/components/form-field";
import Header from "@cloudscape-design/components/header";
import Input from "@cloudscape-design/components/input";
import SegmentedControl from "@cloudscape-design/components/segmented-control";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Spinner from "@cloudscape-design/components/spinner";
import Textarea from "@cloudscape-design/components/textarea";
import { useState } from "react";
import { useNavigate } from "react-router";

import {
  type ConfigurationResponse,
  type EditableConfiguration,
  type EvaluationCalibrationResponse,
  useConfiguration,
  useEvaluationCalibration,
  useFinishOnboarding,
  useOnboardingPlanUsage,
  useRandomCalibrationJob,
} from "@/api/configuration";
import { type ProviderState, useProviderOnboarding } from "@/api/onboarding";
import { type JobProfile, useResumeOnboarding } from "@/api/onboarding-resume";
import { useOnboardingSearches } from "@/api/onboarding-searches";

interface ReviewValues {
  allowedLocations: string;
  blockedLocations: string;
  blockedTitles: string;
  blockedCompanies: string;
  maximumJobAge: number;
  defaultJobs: number;
  dailyCalls: number;
  dailyCost: number;
  parallelEvaluations: number;
  atsMaxJobs: number;
  linkedInGuestMaxJobs: number;
  indeedMaxJobs: number;
  speedyApplyMaxJobs: number;
}

type ClaudePlan = "pro" | "max5" | "max20";

const CLAUDE_PLAN_CAPACITY_USD: Record<ClaudePlan, number> = {
  pro: 18,
  max5: 90,
  max20: 360,
};

const CODEX_WEEKLY_CAPACITY_USD: Record<string, number> = {
  Pro: 82.49,
};

/** Step 4: editable final settings, honest usage range, and atomic finish. */
export default function OnboardingReviewPage() {
  const configuration = useConfiguration();
  const provider = useProviderOnboarding();
  const resume = useResumeOnboarding();
  const searches = useOnboardingSearches();
  const [isFinished, setIsFinished] = useState(false);

  if (configuration.isPending || provider.isPending || resume.isPending || searches.isPending) {
    return <ReviewLoading />;
  }
  if (
    configuration.data === undefined
    || provider.data === undefined
    || resume.data === undefined
    || searches.data === undefined
  ) {
    return (
      <Box padding="xxl">
        <Alert type="error" header="Setup review could not be loaded">
          Refresh the page and try again.
        </Alert>
      </Box>
    );
  }
  if (isFinished) return <SetupComplete />;

  return (
    <ReviewSettings
      current={configuration.data}
      profile={resume.data.profile ?? null}
      provider={provider.data}
      resumeName={resume.data.original_name ?? null}
      enabledSearches={searches.data.searches.filter((search) => search.enabled).length}
      onFinished={() => setIsFinished(true)}
    />
  );
}

function ReviewSettings({
  current,
  profile,
  provider,
  resumeName,
  enabledSearches,
  onFinished,
}: {
  current: ConfigurationResponse;
  profile: JobProfile | null;
  provider: ProviderState;
  resumeName: string | null;
  enabledSearches: number;
  onFinished: () => void;
}) {
  const navigate = useNavigate();
  const finish = useFinishOnboarding();
  const planUsage = useOnboardingPlanUsage();
  const calibration = useEvaluationCalibration();
  const sampleJob = useRandomCalibrationJob();
  const [values, setValues] = useState<ReviewValues>(() => initialValues(current, profile));
  const [sampleJdOverride, setSampleJdOverride] = useState<string | null>(null);
  const [claudePlan, setClaudePlan] = useState<ClaudePlan>("max5");
  const expectedJobs = values.defaultJobs;
  const evaluationCalls = Math.min(expectedJobs, values.dailyCalls);
  const providerLabel = providerName(provider.provider ?? null);
  const sampleJd = sampleJdOverride ?? sampleJob.data?.jd_text ?? "";

  function update<K extends keyof ReviewValues>(key: K, value: ReviewValues[K]) {
    setValues((currentValues) => ({ ...currentValues, [key]: value }));
  }

  function submit() {
    finish.mutate(
      {
        configuration: buildConfiguration(current, values),
        expected_jobs: expectedJobs,
      },
      { onSuccess: onFinished },
    );
  }

  return (
    <ContentLayout
      maxContentWidth={1240}
      header={
        <Header
          variant="h1"
          description="Check the settings that affect relevance and model usage, then save the complete setup."
          actions={<Button onClick={() => navigate("/setup/companies")}>Back</Button>}
        >
          Review remaining settings
        </Header>
      }
    >
      <SpaceBetween size="l">
        <Alert type="info" header="Nothing starts automatically">
          Finish setup saves these choices. Starting the first scan remains a separate action in Runs.
        </Alert>

        <Container header={<Header variant="h2">Setup summary</Header>}>
          <ColumnLayout columns={4} variant="text-grid">
            <Summary label="AI provider" value={providerLabel} />
            <Summary label="Evaluation model" value={provider.detailed_model ?? provider.quick_model ?? "—"} />
            <Summary label="Résumé" value={resumeName ?? "—"} />
            <Summary label="Enabled searches" value={String(enabledSearches)} />
          </ColumnLayout>
        </Container>

        <Container
          header={
            <Header
              variant="h2"
              description="One value per line. These are initialized from the confirmed job profile."
            >
              Filters
            </Header>
          }
        >
          <ColumnLayout columns={2} borders="vertical">
            <SpaceBetween size="m">
              <ListField label="Allowed locations" value={values.allowedLocations} onChange={(value) => update("allowedLocations", value)} />
              <ListField label="Blocked titles" value={values.blockedTitles} onChange={(value) => update("blockedTitles", value)} />
            </SpaceBetween>
            <SpaceBetween size="m">
              <ListField label="Blocked locations" value={values.blockedLocations} onChange={(value) => update("blockedLocations", value)} />
              <ListField label="Blocked companies" value={values.blockedCompanies} onChange={(value) => update("blockedCompanies", value)} />
            </SpaceBetween>
          </ColumnLayout>
        </Container>

        <Container header={<Header variant="h2">Evaluation limits</Header>}>
          <ColumnLayout columns={3} borders="vertical">
            <SpaceBetween size="m">
              <NumberField label="Maximum job age in days" value={values.maximumJobAge} min={1} onChange={(value) => update("maximumJobAge", value)} />
              <Alert type="info" header="Personal filter learning starts after setup">
                Unified evaluations remain the teacher. Jobfeed will notify you after the filter passes future shadow validation; it never turns on automatically.
              </Alert>
            </SpaceBetween>
            <SpaceBetween size="m">
              <NumberField
                label="Maximum unique jobs to evaluate per run"
                description="Applied after location/date filtering and cross-source company + title deduplication. This same number drives the usage estimate below."
                value={values.defaultJobs}
                min={1}
                onChange={(value) => update("defaultJobs", value)}
              />
            </SpaceBetween>
            <SpaceBetween size="m">
              <NumberField label="Daily model-call limit" value={values.dailyCalls} min={0} onChange={(value) => update("dailyCalls", value)} />
              <NumberField label="Parallel evaluations" value={values.parallelEvaluations} min={1} onChange={(value) => update("parallelEvaluations", value)} />
              {provider.provider?.endsWith("_api") && (
                <NumberField label="Daily API cost limit in USD" value={values.dailyCost} min={0} onChange={(value) => update("dailyCost", value)} />
              )}
            </SpaceBetween>
          </ColumnLayout>
        </Container>

        <Container
          header={
            <Header
              variant="h2"
              description="Each value is one total per source, not per search URL. Cross-source duplicates are removed later."
            >
              Jobs fetched per scan
            </Header>
          }
        >
          <ColumnLayout columns={4} borders="vertical">
            <NumberField label="Company career pages maximum jobs per scan" value={values.atsMaxJobs} min={1} onChange={(value) => update("atsMaxJobs", value)} />
            <NumberField label="LinkedIn guest maximum jobs per scan" value={values.linkedInGuestMaxJobs} min={1} onChange={(value) => update("linkedInGuestMaxJobs", value)} />
            <NumberField label="Indeed maximum jobs per scan" value={values.indeedMaxJobs} min={1} onChange={(value) => update("indeedMaxJobs", value)} />
            <NumberField label="SpeedyApply maximum jobs per scan" value={values.speedyApplyMaxJobs} min={1} onChange={(value) => update("speedyApplyMaxJobs", value)} />
          </ColumnLayout>
          <Box margin={{ top: "m" }} variant="small" color="text-body-secondary">
            Company career pages keep only titles matching the job searches you confirmed, so unrelated ATS roles do not consume this limit.
          </Box>
        </Container>

        <Container
          header={
            <Header
              variant="h2"
              description="This is a disclosed range, not a promise of exact token or subscription usage."
            >
              Evaluation usage
            </Header>
          }
        >
          <SpaceBetween size="m">
            <ColumnLayout columns={2} variant="text-grid">
              <Summary label="Evaluations" value={`${evaluationCalls} evaluations`} />
              <Summary label="Total model calls" value={`${evaluationCalls} planned calls`} />
            </ColumnLayout>
            {provider.provider === "claude_cli" ? (
              <ClaudePlanSelector
                plan={claudePlan}
                onChange={setClaudePlan}
                calibration={calibration.data}
                expectedJobs={expectedJobs}
              />
            ) : (
              <PlanEstimate
                pending={planUsage.isPending}
                failed={planUsage.isError}
                usage={planUsage.data}
                calibration={calibration.data}
                expectedJobs={expectedJobs}
              />
            )}
            <Container
              header={
                <Header
                  variant="h3"
                  description="Runs one real unified evaluation call. Use a typical JD for a useful estimate."
                >
                  Measure one evaluation
                </Header>
              }
            >
              <SpaceBetween size="m">
                {sampleJob.data ? (
                  <Alert
                    type="info"
                    header="Representative job from your Indeed searches"
                  >
                    {sampleJob.data.title} at {sampleJob.data.company}
                  </Alert>
                ) : sampleJob.isPending ? (
                  <Box><Spinner /> Selecting a real job description…</Box>
                ) : (
                  <Alert type="warning" header="No complete Indeed job description found">
                    Paste a real job description below to measure this evaluation.
                  </Alert>
                )}
                <FormField
                  label="Representative job description"
                  constraintText="This calibration is not saved as a job."
                >
                  <Textarea
                    rows={5}
                    value={sampleJd}
                    onChange={({ detail }) => setSampleJdOverride(detail.value)}
                  />
                </FormField>
                <Box>
                  <Button
                    loading={calibration.isPending}
                    loadingText="Measuring one real call"
                    disabled={sampleJd.trim().length < 100}
                    onClick={() => calibration.mutate(sampleJd)}
                  >
                    Measure one evaluation
                  </Button>
                </Box>
                {calibration.error && (
                  <Alert type="error" header="Evaluation could not be measured">
                    {calibration.error.message}
                  </Alert>
                )}
                {calibration.data && (
                  <CalibrationResult
                    result={calibration.data}
                    expectedJobs={expectedJobs}
                    provider={provider.provider}
                  />
                )}
              </SpaceBetween>
            </Container>
            <Box color="text-body-secondary">
              Assumption: every selected JD receives one unified evaluation. The measured cost is an API-equivalent value, not an additional subscription charge.
            </Box>
          </SpaceBetween>
        </Container>

        {finish.error && <Alert type="error" header="Setup could not be saved">{finish.error.message}</Alert>}
        <Box textAlign="right">
          <Button
            variant="primary"
            loading={finish.isPending}
            loadingText="Finishing setup"
            onClick={submit}
          >
            Finish setup
          </Button>
        </Box>
      </SpaceBetween>
    </ContentLayout>
  );
}

function ClaudePlanSelector({
  plan,
  onChange,
  calibration,
  expectedJobs,
}: {
  plan: ClaudePlan;
  onChange: (plan: ClaudePlan) => void;
  calibration: EvaluationCalibrationResponse | undefined;
  expectedJobs: number;
}) {
  return (
    <Container
      header={
        <Header
          variant="h3"
          description="Claude does not expose whether a Max login is 5x or 20x. Choose your plan to estimate this run."
        >
          Claude Code 5-hour allowance
        </Header>
      }
    >
      <SpaceBetween size="s">
        <SegmentedControl
          label="Claude plan"
          selectedId={plan}
          options={[
            { id: "pro", text: "Pro · ≈$18" },
            { id: "max5", text: "Max 5x · ≈$90" },
            { id: "max20", text: "Max 20x · ≈$360" },
          ]}
          onChange={({ detail }) => onChange(detail.selectedId as ClaudePlan)}
        />
        <Box variant="small" color="text-body-secondary">
          Estimated API-equivalent capacity per 5-hour window. Actual limits vary with model, context, cache use, and Anthropic limit changes.
        </Box>
        {calibration && (
          <AllowanceEstimate
            evaluationCost={calibration.evaluation.cost_usd}
            expectedJobs={expectedJobs}
            capacity={CLAUDE_PLAN_CAPACITY_USD[plan]}
            windowLabel="5-hour"
          />
        )}
      </SpaceBetween>
    </Container>
  );
}

function SetupComplete() {
  const navigate = useNavigate();
  return (
    <ContentLayout
      maxContentWidth={760}
      header={<Header variant="h1">Setup complete</Header>}
    >
      <Container>
        <SpaceBetween size="m">
          <Alert type="success" header="Your onboarding choices are active">
            No scan has started. Open Runs when you are ready to choose and start the first source scan.
          </Alert>
          <Box>
            <Button variant="primary" onClick={() => navigate("/triage")}>Open Jobfeed</Button>
          </Box>
        </SpaceBetween>
      </Container>
    </ContentLayout>
  );
}

function ListField({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <FormField label={label} constraintText="One value per line">
      <Textarea value={value} rows={3} onChange={({ detail }) => onChange(detail.value)} />
    </FormField>
  );
}

function NumberField({ label, description, value, min, max, onChange }: { label: string; description?: string; value: number; min: number; max?: number; onChange: (value: number) => void }) {
  return (
    <FormField label={label} description={description}>
      <Input type="number" value={String(value)} onChange={({ detail }) => onChange(boundedNumber(detail.value, min, max))} />
    </FormField>
  );
}

function Summary({ label, value }: { label: string; value: string }) {
  return <Box><Box variant="awsui-key-label">{label}</Box><Box>{value}</Box></Box>;
}

function PlanEstimate({
  pending,
  failed,
  usage,
  calibration,
  expectedJobs,
}: {
  pending: boolean;
  failed: boolean;
  usage: ReturnType<typeof useOnboardingPlanUsage>["data"];
  calibration: EvaluationCalibrationResponse | undefined;
  expectedJobs: number;
}) {
  if (pending) {
    return <Box><Spinner /> Reading live plan usage…</Box>;
  }
  if (
    failed
    || usage?.source !== "live"
    || usage.plan_name == null
    || usage.used_percent == null
    || usage.remaining_percent == null
  ) {
    return (
      <Box>
        <Box variant="awsui-key-label">Plan estimate</Box>
        <Box>Live plan percentage unavailable</Box>
      </Box>
    );
  }
  const reset = usage.resets_at == null
    ? null
    : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" })
      .format(new Date(usage.resets_at * 1000));
  const capacity = CODEX_WEEKLY_CAPACITY_USD[usage.plan_name];
  const projected = calibration === undefined || capacity === undefined
    ? "Measure one evaluation below"
    : allowanceEstimate(
      calibration.evaluation.cost_usd,
      expectedJobs,
      capacity,
      "7-day",
    );
  return (
    <ColumnLayout columns={4} variant="text-grid">
      <Summary label="Current plan" value={`${usage.plan_name} plan`} />
      <Summary
        label={usage.window_minutes === 10_080 ? "Current 7-day window" : "Current usage window"}
        value={`${usage.used_percent}% used · ${usage.remaining_percent}% remaining`}
      />
      <Summary label="Estimated plan use" value={projected} />
      <Summary label="Allowance resets" value={reset ?? "Reset time unavailable"} />
    </ColumnLayout>
  );
}

function CalibrationResult({
  result,
  expectedJobs,
  provider,
}: {
  result: EvaluationCalibrationResponse;
  expectedJobs: number;
  provider: ProviderState["provider"];
}) {
  const expectedTokens = totalTokens(result.evaluation);
  const expectedCost = result.evaluation.cost_usd;
  const allowance = allowanceChange(result);
  return (
    <SpaceBetween size="m">
      <Alert type="success" header="Measured with your current résumé, models, and sample JD">
        This is a measured baseline. Actual JDs can be longer or shorter.
      </Alert>
      <ColumnLayout columns={3} variant="text-grid">
        <Summary
          label="API-equivalent cost"
          value={`About ${formatUsd(expectedCost, 4)} per JD`}
        />
        <Summary
          label="One evaluation run"
          value={`About ${formatUsd(expectedCost * expectedJobs, 2)} for ${expectedJobs} JDs`}
        />
        <Summary
          label="Measured tokens"
          value={`About ${formatNumber(Math.round(expectedTokens))} tokens per JD`}
        />
      </ColumnLayout>
      <ColumnLayout columns={provider === "claude_cli" ? 1 : 2} variant="text-grid">
        <Summary
          label={`Evaluation · ${result.evaluation.model}`}
          value={`${formatNumber(expectedTokens)} tokens · ${formatUsd(result.evaluation.cost_usd, 4)} · ${formatSeconds(result.evaluation.latency_ms)}`}
        />
        {provider !== "claude_cli" && (
          <Summary label="Codex allowance change" value={allowance} />
        )}
      </ColumnLayout>
      {provider !== "claude_cli" && (
        <Box color="text-body-secondary">
          Codex allowance meter resolution: {result.allowance_resolution_percent} percentage point.
        </Box>
      )}
    </SpaceBetween>
  );
}

function AllowanceEstimate({
  evaluationCost,
  expectedJobs,
  capacity,
  windowLabel,
}: {
  evaluationCost: number;
  expectedJobs: number;
  capacity: number;
  windowLabel: string;
}) {
  const oneExpected = (evaluationCost / capacity) * 100;
  const plannedExpected = oneExpected * expectedJobs;
  return (
    <ColumnLayout columns={2} variant="text-grid">
      <Summary
        label="One JD"
        value={`Estimated ${formatPercent(oneExpected)} of your ${windowLabel} allowance`}
      />
      <Summary
        label={`${expectedJobs} JDs`}
        value={`Estimated ${formatPercent(plannedExpected)} of your ${windowLabel} allowance`}
      />
    </ColumnLayout>
  );
}

function allowanceEstimate(
  evaluationCost: number,
  expectedJobs: number,
  capacity: number,
  windowLabel: string,
): string {
  const projected = (
    (evaluationCost * expectedJobs)
    / capacity
  ) * 100;
  return `Estimated ${formatPercent(projected)} of your ${windowLabel} allowance`;
}

function formatPercent(value: number): string {
  const digits = value < 0.01 ? 3 : 2;
  return `${value.toFixed(digits)}%`;
}

function totalTokens(call: { input_tokens: number; output_tokens: number }): number {
  return call.input_tokens + call.output_tokens;
}

function allowanceChange(result: EvaluationCalibrationResponse): string {
  const before = result.allowance_before_percent;
  const after = result.allowance_after_percent;
  if (before == null || after == null) return "Allowance change unavailable";
  if (before === after) return "No whole percentage point detected";
  return `${before}% → ${after}% used`;
}

function formatUsd(value: number, digits: number): string {
  return `$${value.toFixed(digits)}`;
}

function formatNumber(value: number): string {
  return new Intl.NumberFormat().format(value);
}

function formatSeconds(milliseconds: number): string {
  return `${(milliseconds / 1000).toFixed(1)}s`;
}

function initialValues(current: ConfigurationResponse, profile: JobProfile | null): ReviewValues {
  const filters = current.hard_filters!;
  const allowedLocations = filters.location_allowlist ?? [];
  const blockedLocations = filters.location_blocklist ?? [];
  const blockedTitles = filters.title_blocklist ?? [];
  const blockedCompanies = filters.company_blocklist ?? [];
  return {
    allowedLocations: toLines(allowedLocations.length > 0 ? allowedLocations : profile?.target_locations ?? []),
    blockedLocations: toLines(blockedLocations.length > 0 ? blockedLocations : profile?.excluded_locations ?? []),
    blockedTitles: toLines(blockedTitles.length > 0 ? blockedTitles : profile?.excluded_titles ?? []),
    blockedCompanies: toLines(blockedCompanies.length > 0 ? blockedCompanies : profile?.excluded_companies ?? []),
    maximumJobAge: filters.posted_within_days ?? profile?.maximum_posting_age_days ?? 30,
    defaultJobs: current.scoring!.default_eval_limit!,
    dailyCalls: current.llm!.max_daily_score_calls!,
    dailyCost: current.llm!.max_daily_cost_usd!,
    parallelEvaluations: current.llm!.max_concurrent!,
    atsMaxJobs: current.sources!.ats!.max_jobs!,
    linkedInGuestMaxJobs: current.sources!.linkedin_guest!.max_jobs!,
    indeedMaxJobs: current.sources!.indeed!.max_jobs!,
    speedyApplyMaxJobs: current.sources!.speedyapply!.max_jobs!,
  };
}

function buildConfiguration(current: ConfigurationResponse, values: ReviewValues): EditableConfiguration {
  const editable = structuredClone(current) as EditableConfiguration & { configured?: boolean };
  delete editable.configured;
  editable.llm!.max_concurrent = values.parallelEvaluations;
  editable.llm!.max_daily_score_calls = values.dailyCalls;
  editable.llm!.max_daily_cost_usd = values.dailyCost;
  editable.scoring!.default_eval_limit = values.defaultJobs;
  editable.scoring!.ml_gate_enabled = false;
  editable.ml_gate!.threshold_override = null;
  editable.hard_filters!.location_allowlist = fromLines(values.allowedLocations);
  editable.hard_filters!.location_blocklist = fromLines(values.blockedLocations);
  editable.hard_filters!.title_blocklist = fromLines(values.blockedTitles);
  editable.hard_filters!.company_blocklist = fromLines(values.blockedCompanies);
  editable.hard_filters!.posted_within_days = values.maximumJobAge;
  editable.sources!.ats!.max_jobs = values.atsMaxJobs;
  editable.sources!.linkedin_guest!.max_jobs = values.linkedInGuestMaxJobs;
  editable.sources!.indeed!.max_jobs = values.indeedMaxJobs;
  editable.sources!.speedyapply!.max_jobs = values.speedyApplyMaxJobs;
  return editable;
}

function toLines(values: string[]): string {
  return values.join("\n");
}

function fromLines(value: string): string[] {
  return value.split("\n").map((line) => line.trim()).filter(Boolean);
}

function boundedNumber(value: string, min: number, max = Number.MAX_SAFE_INTEGER): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return min;
  return Math.min(max, Math.max(min, parsed));
}

function providerName(provider: string | null): string {
  return ({
    openai_api: "OpenAI API",
    anthropic_api: "Anthropic API",
    codex_cli: "Codex CLI",
    claude_cli: "Claude CLI",
  } as Record<string, string>)[provider ?? ""] ?? "Not connected";
}

function ReviewLoading() {
  return (
    <Box padding="xxl" textAlign="center">
      <SpaceBetween size="s" alignItems="center">
        <Spinner size="large" />
        <Box color="text-body-secondary">Loading final setup review</Box>
      </SpaceBetween>
    </Box>
  );
}
