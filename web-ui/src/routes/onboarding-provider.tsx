import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Checkbox from "@cloudscape-design/components/checkbox";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import Container from "@cloudscape-design/components/container";
import ContentLayout from "@cloudscape-design/components/content-layout";
import FormField from "@cloudscape-design/components/form-field";
import Header from "@cloudscape-design/components/header";
import Input from "@cloudscape-design/components/input";
import Select from "@cloudscape-design/components/select";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Spinner from "@cloudscape-design/components/spinner";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router";

import {
  providerOnboardingKey,
  type ProviderName,
  testProviderConnection,
  useProviderOnboarding,
  useSaveProviderModels,
} from "@/api/onboarding";

const PROVIDERS: {
  id: ProviderName;
  title: string;
  description: string;
  keyLabel?: string;
}[] = [
  {
    id: "openai_api",
    title: "OpenAI API",
    description: "Uses official OpenAI API billing. The key stays in data/secrets.toml.",
    keyLabel: "OpenAI API key",
  },
  {
    id: "anthropic_api",
    title: "Anthropic API",
    description: "Uses official Anthropic API billing. The key stays on this computer.",
    keyLabel: "Anthropic API key",
  },
  {
    id: "azure_openai",
    title: "Azure OpenAI",
    description: "Uses existing Azure OpenAI deployments through the current v1 endpoint.",
    keyLabel: "Azure OpenAI API key",
  },
  {
    id: "codex_cli",
    title: "Codex CLI",
    description: "Uses your locally installed and signed-in Codex CLI.",
  },
  {
    id: "claude_cli",
    title: "Claude Code CLI",
    description: "Uses your locally installed and signed-in Claude Code CLI.",
  },
  {
    id: "amazon_bedrock",
    title: "Amazon Bedrock",
    description: "Uses the standard AWS credential chain and models available in your region.",
  },
];

/** First runnable onboarding milestone: provider connection and model choice. */
export default function OnboardingProviderPage() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const onboarding = useProviderOnboarding();
  const saveModels = useSaveProviderModels();
  const [isTesting, setIsTesting] = useState(false);
  const [testError, setTestError] = useState<string | null>(null);
  const [selected, setSelected] = useState<ProviderName | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [region, setRegion] = useState<string | null>(null);
  const [profile, setProfile] = useState<string | null>(null);
  const [endpoint, setEndpoint] = useState<string | null>(null);
  const [quickModel, setQuickModel] = useState<string | null>(null);
  const [detailedModel, setDetailedModel] = useState<string | null>(null);
  const [azurePrices, setAzurePrices] = useState<Record<string, AzurePriceDraft>>({});

  if (onboarding.isPending) {
    return <Box padding="xxl" textAlign="center"><Spinner size="large" /></Box>;
  }
  if (onboarding.isError || onboarding.data === undefined) {
    return <Box padding="xxl"><Alert type="error">Provider setup could not be loaded.</Alert></Box>;
  }

  const state = onboarding.data;
  const provider = selected ?? state.provider ?? null;
  const definition = PROVIDERS.find((item) => item.id === provider);
  const models = state.provider === provider && state.connected ? state.models : [];
  const bedrockRegion = region ?? state.region ?? "us-east-1";
  const bedrockProfile = profile ?? state.profile ?? "";
  const azureEndpoint = endpoint ?? state.endpoint ?? "";
  const quickChoice = quickModel ?? state.quick_model ?? models[0]?.id ?? null;
  const detailedChoice = detailedModel ?? state.detailed_model ?? models[0]?.id ?? null;
  const quick = provider === "azure_openai" ? quickChoice?.trim() || null : quickChoice;
  const detailed = provider === "azure_openai" ? detailedChoice?.trim() || null : detailedChoice;

  function choose(next: ProviderName) {
    setSelected(next);
    setApiKey("");
    setRegion(null);
    setProfile(null);
    setEndpoint(null);
    setQuickModel(null);
    setDetailedModel(null);
    setAzurePrices({});
    setTestError(null);
    saveModels.reset();
  }

  async function testConnection() {
    if (provider === null) return;
    setTestError(null);
    setIsTesting(true);
    try {
      const next = await testProviderConnection(
        provider,
        apiKey,
        provider === "amazon_bedrock"
          ? { region: bedrockRegion, profile: bedrockProfile }
          : provider === "azure_openai"
            ? { endpoint: azureEndpoint }
          : undefined,
      );
      queryClient.setQueryData(providerOnboardingKey, next);
      if (next.connected) setApiKey("");
      setQuickModel(preferredModel(next.models, "us.anthropic.claude-haiku-4-5-20251001-v1:0"));
      setDetailedModel(preferredModel(next.models, "us.anthropic.claude-sonnet-5"));
      if (provider === "azure_openai") setAzurePrices({});
    } catch (error) {
      setTestError(error instanceof Error ? error.message : "Connection test failed. Try again.");
    } finally {
      setIsTesting(false);
    }
  }

  function save() {
    if (provider === null || quick === null || detailed === null) return;
    const candidatePricing = provider === "azure_openai"
      ? [...new Set([quick, detailed])].map((deployment) =>
          azurePricePayload(deployment, azurePrice(deployment, state, azurePrices)),
        )
      : undefined;
    if (candidatePricing?.some((price) => price === null)) return;
    const deploymentPricing = candidatePricing?.filter(
      (price): price is NonNullable<typeof price> => price !== null,
    );
    saveModels.mutate(
      {
        provider,
        quick_model: quick,
        detailed_model: detailed,
        ...(deploymentPricing ? { deployment_pricing: deploymentPricing } : {}),
      },
      { onSuccess: () => navigate("/setup/resume") },
    );
  }

  return (
    <ContentLayout
      maxContentWidth={1080}
      header={<Header variant="h1" description="Step 1 of 4 · Connect securely, then choose the models Jobfeed will use.">Connect an AI provider</Header>}
    >
      <SpaceBetween size="l">
        <Alert type="info" header="Local by default">Secrets and setup progress stay in the local <code>data/</code> directory.</Alert>
        <Container
          header={
            <Header variant="h2" description="Choose how Jobfeed accesses an AI model.">
              Provider
            </Header>
          }
        >
          <SpaceBetween size="s">
            <FormField label="AI provider">
              <Select
                placeholder="Choose a provider"
                selectedOption={
                  definition
                    ? { label: definition.title, value: definition.id }
                    : null
                }
                options={PROVIDERS.map((item) => ({
                  label: item.title,
                  value: item.id,
                  description: item.description,
                }))}
                onChange={({ detail }) => {
                  const next = PROVIDERS.find(
                    (item) => item.id === detail.selectedOption.value,
                  );
                  if (next) choose(next.id);
                }}
              />
            </FormField>
            {definition && (
              <Box color="text-body-secondary">{definition.description}</Box>
            )}
          </SpaceBetween>
        </Container>

        {definition && (
          <Container header={<Header variant="h2">Test connection</Header>}>
            <SpaceBetween size="m">
              {definition.keyLabel && (
                <FormField label={definition.keyLabel} description="The key is write-only. Leave blank to reuse a previously saved key, if available.">
                  <Input
                    type="password"
                    value={apiKey}
                    onChange={({ detail }) => setApiKey(detail.value)}
                  />
                </FormField>
              )}
              {provider === "amazon_bedrock" && (
                <ColumnLayout columns={2} minColumnWidth={280}>
                  <FormField label="AWS region">
                    <Input
                      value={bedrockRegion}
                      onChange={({ detail }) => setRegion(detail.value)}
                    />
                  </FormField>
                  <FormField label="AWS profile" description="Optional. Leave blank to use the default AWS credential chain.">
                    <Input
                      value={bedrockProfile}
                      onChange={({ detail }) => setProfile(detail.value)}
                    />
                  </FormField>
                </ColumnLayout>
              )}
              {provider === "azure_openai" && (
                <FormField
                  label="Azure OpenAI endpoint"
                  description="Use the resource endpoint; Jobfeed normalizes it to /openai/v1."
                >
                  <Input
                    value={azureEndpoint}
                    placeholder="https://your-resource.openai.azure.com/openai/v1"
                    onChange={({ detail }) => setEndpoint(detail.value)}
                  />
                </FormField>
              )}
              {state.provider === provider && state.detail && (
                <Alert type={state.connected ? "success" : "error"}>{state.detail}</Alert>
              )}
              {testError && <Alert type="error">{testError}</Alert>}
              <Button loading={isTesting} onClick={testConnection}>
                {state.provider === provider && !state.connected ? "Try again" : state.provider === provider && state.connected ? "Test again" : "Test connection"}
              </Button>
            </SpaceBetween>
          </Container>
        )}

        {(models.length > 0 || (provider === "azure_openai" && state.connected)) && (
          <Container header={<Header variant="h2">Choose models</Header>}>
            <SpaceBetween size="m">
              <ColumnLayout columns={2} minColumnWidth={280}>
                {provider === "azure_openai" ? (
                  <>
                    <FormField label="Quick deployment name" description="Enter an existing Azure deployment alias.">
                      <Input value={quickChoice ?? ""} onChange={({ detail }) => setQuickModel(detail.value)} />
                    </FormField>
                    <FormField label="Detailed deployment name" description="May be the same deployment as Quick.">
                      <Input value={detailedChoice ?? ""} onChange={({ detail }) => setDetailedModel(detail.value)} />
                    </FormField>
                  </>
                ) : (
                  <>
                    <FormField label="Quick evaluation model">
                      <Select
                        selectedOption={modelOption(models, quick)}
                        options={models.map((model) => ({ label: model.label, value: model.id }))}
                        onChange={({ detail }) => setQuickModel(detail.selectedOption.value ?? null)}
                      />
                    </FormField>
                    <FormField label="Detailed review model">
                      <Select
                        selectedOption={modelOption(models, detailed)}
                        options={models.map((model) => ({ label: model.label, value: model.id }))}
                        onChange={({ detail }) => setDetailedModel(detail.selectedOption.value ?? null)}
                      />
                    </FormField>
                  </>
                )}
              </ColumnLayout>
              {provider === "azure_openai" && [...new Set([quick, detailed])].map((deployment) => {
                if (deployment === null) return null;
                const price = azurePrice(deployment, state, azurePrices);
                return (
                  <AzurePricingEditor
                    key={deployment}
                    deployment={deployment}
                    price={price}
                    catalog={state.pricing_catalog ?? []}
                    onChange={(next) => setAzurePrices((current) => ({ ...current, [deployment]: next }))}
                  />
                );
              })}
              {provider === "azure_openai" && (
                <Alert type="info">
                  Reference prices are editable estimates. Verify them against your Azure region and deployment pricing before confirming.
                </Alert>
              )}
              {saveModels.isError && <Alert type="error">{saveModels.error.message}</Alert>}
              <Button
                variant="primary"
                loading={saveModels.isPending}
                disabled={provider === "azure_openai" && !azurePricesReady([quick, detailed], state, azurePrices)}
                onClick={save}
              >
                Save and continue
              </Button>
            </SpaceBetween>
          </Container>
        )}
      </SpaceBetween>
    </ContentLayout>
  );
}

type AzurePriceDraft = {
  baseModel: string | null;
  input: string;
  output: string;
  cachedInput: string;
  confirmed: boolean;
};

type PricingReference = {
  base_model: string;
  input_usd_per_million: number;
  output_usd_per_million: number;
  cached_input_usd_per_million?: number | null;
};

function AzurePricingEditor({
  deployment,
  price,
  catalog,
  onChange,
}: {
  deployment: string;
  price: AzurePriceDraft;
  catalog: PricingReference[];
  onChange: (price: AzurePriceDraft) => void;
}) {
  const selected = catalog.find((item) => item.base_model === price.baseModel);
  return (
    <Container header={<Header variant="h3">Pricing · {deployment}</Header>}>
      <SpaceBetween size="m">
        <FormField label={`${deployment} base model`}>
          <Select
            selectedOption={selected ? { label: selected.base_model, value: selected.base_model } : null}
            options={catalog.map((item) => ({ label: item.base_model, value: item.base_model }))}
            filteringType="auto"
            onChange={({ detail }) => {
              const reference = catalog.find((item) => item.base_model === detail.selectedOption.value);
              if (!reference) return;
              onChange({
                baseModel: reference.base_model,
                input: String(reference.input_usd_per_million),
                output: String(reference.output_usd_per_million),
                cachedInput: reference.cached_input_usd_per_million == null ? "" : String(reference.cached_input_usd_per_million),
                confirmed: false,
              });
            }}
          />
        </FormField>
        <ColumnLayout columns={3} minColumnWidth={180}>
          <PriceInput label="Input · USD / 1M tokens" value={price.input} onChange={(input) => onChange({ ...price, input, confirmed: false })} />
          <PriceInput label="Output · USD / 1M tokens" value={price.output} onChange={(output) => onChange({ ...price, output, confirmed: false })} />
          <PriceInput label="Cached input · USD / 1M tokens" value={price.cachedInput} onChange={(cachedInput) => onChange({ ...price, cachedInput, confirmed: false })} />
        </ColumnLayout>
        <Checkbox
          ariaLabel={`Confirm prices for ${deployment}`}
          checked={price.confirmed}
          disabled={!validAzurePrice(price)}
          onChange={({ detail }) => onChange({ ...price, confirmed: detail.checked })}
        >
          I confirm these prices for <strong>{deployment}</strong>.
        </Checkbox>
      </SpaceBetween>
    </Container>
  );
}

function PriceInput({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <FormField label={label}>
      <Input
        type="number"
        value={value}
        inputMode="decimal"
        onChange={({ detail }) => onChange(detail.value)}
      />
    </FormField>
  );
}

function azurePrice(
  deployment: string,
  state: ReturnType<typeof useProviderOnboarding>["data"],
  drafts: Record<string, AzurePriceDraft>,
): AzurePriceDraft {
  const draft = drafts[deployment];
  if (draft) return draft;
  const saved = state?.deployment_pricing?.find((item) => item.deployment === deployment);
  if (saved) {
    return {
      baseModel: saved.base_model,
      input: String(saved.input_usd_per_million),
      output: String(saved.output_usd_per_million),
      cachedInput: saved.cached_input_usd_per_million == null ? "" : String(saved.cached_input_usd_per_million),
      confirmed: true,
    };
  }
  return { baseModel: null, input: "", output: "", cachedInput: "", confirmed: false };
}

function validAzurePrice(price: AzurePriceDraft): boolean {
  const required = [price.input, price.output].map(Number);
  const cached = price.cachedInput === "" ? null : Number(price.cachedInput);
  return price.baseModel !== null && required.every((value) => Number.isFinite(value) && value >= 0)
    && (cached === null || (Number.isFinite(cached) && cached >= 0));
}

function azurePricesReady(
  deployments: (string | null)[],
  state: ReturnType<typeof useProviderOnboarding>["data"],
  drafts: Record<string, AzurePriceDraft>,
): boolean {
  return [...new Set(deployments)].every((deployment) => deployment !== null
    && azurePrice(deployment, state, drafts).confirmed
    && validAzurePrice(azurePrice(deployment, state, drafts)));
}

function azurePricePayload(deployment: string, price: AzurePriceDraft) {
  if (!price.confirmed || !validAzurePrice(price) || price.baseModel === null) return null;
  return {
    deployment,
    base_model: price.baseModel,
    input_usd_per_million: Number(price.input),
    output_usd_per_million: Number(price.output),
    cached_input_usd_per_million: price.cachedInput === "" ? null : Number(price.cachedInput),
  };
}

function modelOption(models: { id: string; label: string }[], selected: string | null) {
  const model = models.find((item) => item.id === selected);
  return model ? { label: model.label, value: model.id } : null;
}

function preferredModel(models: { id: string }[], preferred: string): string | null {
  return models.find((model) => model.id === preferred)?.id ?? models[0]?.id ?? null;
}
