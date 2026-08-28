import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
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
  const [quickModel, setQuickModel] = useState<string | null>(null);
  const [detailedModel, setDetailedModel] = useState<string | null>(null);

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
  const quick = quickModel ?? state.quick_model ?? models[0]?.id ?? null;
  const detailed = detailedModel ?? state.detailed_model ?? models[0]?.id ?? null;

  function choose(next: ProviderName) {
    setSelected(next);
    setApiKey("");
    setRegion(null);
    setProfile(null);
    setQuickModel(null);
    setDetailedModel(null);
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
          : undefined,
      );
      queryClient.setQueryData(providerOnboardingKey, next);
      if (next.connected) setApiKey("");
      setQuickModel(preferredModel(next.models, "us.anthropic.claude-haiku-4-5-20251001-v1:0"));
      setDetailedModel(preferredModel(next.models, "us.anthropic.claude-sonnet-5"));
    } catch (error) {
      setTestError(error instanceof Error ? error.message : "Connection test failed. Try again.");
    } finally {
      setIsTesting(false);
    }
  }

  function save() {
    if (provider === null || quick === null || detailed === null) return;
    saveModels.mutate(
      { provider, quick_model: quick, detailed_model: detailed },
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

        {models.length > 0 && (
          <Container header={<Header variant="h2">Choose models</Header>}>
            <SpaceBetween size="m">
              <ColumnLayout columns={2} minColumnWidth={280}>
                <FormField label="Quick evaluation model">
                  <Select
                    selectedOption={modelOption(models, quick)}
                    options={models.map((model) => ({ label: model.label, value: model.id }))}
                    onChange={({ detail }) => {
                      setQuickModel(detail.selectedOption.value ?? null);
                    }}
                  />
                </FormField>
                <FormField label="Detailed review model">
                  <Select
                    selectedOption={modelOption(models, detailed)}
                    options={models.map((model) => ({ label: model.label, value: model.id }))}
                    onChange={({ detail }) => {
                      setDetailedModel(detail.selectedOption.value ?? null);
                    }}
                  />
                </FormField>
              </ColumnLayout>
              {saveModels.isError && <Alert type="error">{saveModels.error.message}</Alert>}
              <Button variant="primary" loading={saveModels.isPending} onClick={save}>Save and continue</Button>
            </SpaceBetween>
          </Container>
        )}
      </SpaceBetween>
    </ContentLayout>
  );
}

function modelOption(models: { id: string; label: string }[], selected: string | null) {
  const model = models.find((item) => item.id === selected);
  return model ? { label: model.label, value: model.id } : null;
}

function preferredModel(models: { id: string }[], preferred: string): string | null {
  return models.find((model) => model.id === preferred)?.id ?? models[0]?.id ?? null;
}
