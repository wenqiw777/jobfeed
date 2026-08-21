import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Checkbox from "@cloudscape-design/components/checkbox";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import Container from "@cloudscape-design/components/container";
import ContentLayout from "@cloudscape-design/components/content-layout";
import FileUpload from "@cloudscape-design/components/file-upload";
import FormField from "@cloudscape-design/components/form-field";
import Header from "@cloudscape-design/components/header";
import Input from "@cloudscape-design/components/input";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Spinner from "@cloudscape-design/components/spinner";
import Textarea from "@cloudscape-design/components/textarea";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router";

import {
  type JobProfile,
  useAnalyzeResume,
  useConfirmProfile,
  useResumeOnboarding,
  useUploadResume,
} from "@/api/onboarding-resume";

const FILE_I18N = {
  uploadButtonText: () => "Choose résumé",
  dropzoneText: () => "Drop a résumé here",
  removeFileAriaLabel: (_index: number, name: string) => `Remove ${name}`,
  errorIconAriaLabel: "Error",
};

/** Slice 2 onboarding: local extraction followed by editable AI suggestions. */
export default function OnboardingResumePage() {
  const navigate = useNavigate();
  const resume = useResumeOnboarding();
  const upload = useUploadResume();
  const analyze = useAnalyzeResume();
  const confirm = useConfirmProfile();
  const [files, setFiles] = useState<File[]>([]);
  const [profile, setProfile] = useState<JobProfile | null>(null);

  useEffect(() => {
    // Server analysis/reload is the source for a new editable draft.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setProfile(resume.data?.profile ?? null);
  }, [resume.data?.profile]);

  if (resume.isPending) {
    return <Box padding="xxl" textAlign="center"><Spinner size="large" /></Box>;
  }
  if (resume.isError || resume.data === undefined) {
    return <Box padding="xxl"><Alert type="error">Résumé setup could not be loaded.</Alert></Box>;
  }

  const state = resume.data;
  const error = upload.error ?? analyze.error ?? confirm.error;
  const isProfileDirty = profile !== null
    && JSON.stringify(profile) !== JSON.stringify(state.profile);

  return (
    <ContentLayout
      maxContentWidth={1080}
      header={
        <Header
          variant="h1"
          description="Step 2 of 4 · Upload locally, preview the text, then review AI suggestions."
          actions={<Button onClick={() => navigate("/setup")}>Back</Button>}
        >
          Upload your résumé
        </Header>
      }
    >
      <SpaceBetween size="l">
        <Alert type="info" header="You stay in control">
          The original file and extracted text stay under <code>data/</code>. Analysis uses your selected provider, and no suggestion becomes active until you confirm it.
        </Alert>

        <Container header={<Header variant="h2" description="PDF, DOCX, Markdown, or plain text · maximum 10 MB">Résumé file</Header>}>
          <SpaceBetween size="m">
            <FileUpload
              value={files}
              accept=".pdf,.docx,.md,.txt"
              showFileSize
              constraintText="Choose one text-based résumé. Image-only and encrypted PDFs are not supported."
              i18nStrings={FILE_I18N}
              onChange={({ detail }) => setFiles(detail.value.slice(-1))}
            />
            <SpaceBetween direction="horizontal" size="xs">
              <Button
                variant="primary"
                disabled={files.length !== 1}
                loading={upload.isPending}
                onClick={() => upload.mutate(files[0]!)}
              >
                {state.original_name ? "Replace and preview" : "Upload and preview"}
              </Button>
              {state.original_name && <Box color="text-body-secondary">Current: {state.original_name}</Box>}
            </SpaceBetween>
          </SpaceBetween>
        </Container>

        {state.extracted_text && (
          <Container header={<Header variant="h2" description="Confirm that headings, experience, and locations were extracted correctly.">Extracted text preview</Header>}>
            <SpaceBetween size="m">
              <Textarea value={state.extracted_text} readOnly rows={12} />
              <Alert type="warning">Selecting Analyze sends this extracted text to your chosen AI provider.</Alert>
              <Button loading={analyze.isPending} onClick={() => analyze.mutate(undefined)}>
                {state.profile ? "Analyze again" : "Analyze résumé"}
              </Button>
            </SpaceBetween>
          </Container>
        )}

        {profile && (
          <ProfileEditor
            profile={profile}
            isConfirmed={state.is_confirmed && !isProfileDirty}
            isSaving={confirm.isPending}
            onChange={setProfile}
            onConfirm={() => confirm.mutate(profile, {
              onSuccess: () => navigate("/setup/searches"),
            })}
          />
        )}

        {error && <Alert type="error">{error.message}</Alert>}
      </SpaceBetween>
    </ContentLayout>
  );
}

function ProfileEditor({
  profile,
  isConfirmed,
  isSaving,
  onChange,
  onConfirm,
}: {
  profile: JobProfile;
  isConfirmed: boolean;
  isSaving: boolean;
  onChange: (profile: JobProfile) => void;
  onConfirm: () => void;
}) {
  function update<K extends keyof JobProfile>(key: K, value: JobProfile[K]) {
    onChange({ ...profile, [key]: value });
  }

  return (
    <Container header={<Header variant="h2" description="AI suggestions are editable. Check every field before confirming.">Review job profile</Header>}>
      <SpaceBetween size="l">
        {isConfirmed && <Alert type="success" header="Profile confirmed">Your edited preferences will resume here.</Alert>}
        <ColumnLayout columns={2} minColumnWidth={300}>
          <ListField label="Desired job titles" value={profile.desired_titles} onChange={(value) => update("desired_titles", value)} />
          <ListField label="Seniority or level" value={profile.seniority_levels} onChange={(value) => update("seniority_levels", value)} />
          <ListField label="Target countries" value={profile.target_countries} onChange={(value) => update("target_countries", value)} />
          <ListField label="Target locations" value={profile.target_locations} onChange={(value) => update("target_locations", value)} />
          <ListField label="Industries" value={profile.industries} onChange={(value) => update("industries", value)} />
          <ListField label="Company sizes" value={profile.company_sizes} onChange={(value) => update("company_sizes", value)} />
          <TextField label="Work authorization or sponsorship" value={profile.work_authorization} onChange={(value) => update("work_authorization", value)} />
          <TextField label="Graduation, start date, or hiring window" value={profile.hiring_timeline} onChange={(value) => update("hiring_timeline", value)} />
          <ListField label="Excluded titles" value={profile.excluded_titles} onChange={(value) => update("excluded_titles", value)} />
          <ListField label="Excluded companies" value={profile.excluded_companies} onChange={(value) => update("excluded_companies", value)} />
          <ListField label="Excluded locations" value={profile.excluded_locations} onChange={(value) => update("excluded_locations", value)} />
          <ListField label="Excluded keywords" value={profile.excluded_keywords} onChange={(value) => update("excluded_keywords", value)} />
        </ColumnLayout>
        <FormField label="Work modes">
          <SpaceBetween direction="horizontal" size="l">
            {(["remote", "hybrid", "on-site"] as const).map((mode) => (
              <Checkbox
                key={mode}
                checked={profile.work_modes.includes(mode)}
                onChange={({ detail }) => update(
                  "work_modes",
                  detail.checked
                    ? [...profile.work_modes, mode]
                    : profile.work_modes.filter((value) => value !== mode),
                )}
              >
                {mode}
              </Checkbox>
            ))}
          </SpaceBetween>
        </FormField>
        <FormField label="Maximum posting age (days)">
          <Input
            type="number"
            value={String(profile.maximum_posting_age_days)}
            nativeInputAttributes={{ min: 1, max: 365 }}
            onChange={({ detail }) => update("maximum_posting_age_days", Number(detail.value))}
          />
        </FormField>
        <Container header={<Header variant="h3">Résumé-derived evidence</Header>}>
          {profile.resume_evidence.length > 0 ? (
            <ul>{profile.resume_evidence.map((evidence) => <li key={evidence}>{evidence}</li>)}</ul>
          ) : (
            <Box color="text-body-secondary">No explicit evidence was returned.</Box>
          )}
        </Container>
        <Button variant="primary" loading={isSaving} onClick={onConfirm}>Confirm profile</Button>
      </SpaceBetween>
    </Container>
  );
}

function ListField({ label, value, onChange }: { label: string; value: string[]; onChange: (value: string[]) => void }) {
  return (
    <FormField label={label} description="One value per line">
      <Textarea value={value.join("\n")} onChange={({ detail }) => onChange(lines(detail.value))} />
    </FormField>
  );
}

function TextField({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return <FormField label={label}><Input value={value} onChange={({ detail }) => onChange(detail.value)} /></FormField>;
}

function lines(value: string) {
  return value.split("\n").map((line) => line.trim()).filter(Boolean);
}
