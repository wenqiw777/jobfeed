import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Checkbox from "@cloudscape-design/components/checkbox";
import Container from "@cloudscape-design/components/container";
import ContentLayout from "@cloudscape-design/components/content-layout";
import ExpandableSection from "@cloudscape-design/components/expandable-section";
import Header from "@cloudscape-design/components/header";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Spinner from "@cloudscape-design/components/spinner";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router";

import {
  type CompanyRecommendation,
  useCompanyCatalog,
  useCompanyRecommendations,
  useProbeRecommendedCompanies,
  useRefreshCompanyRecommendations,
} from "@/api/onboarding-companies";
import {
  type ProbeEntryResult,
  useBulkAddCompanies,
} from "@/api/queries";
import { ProbeFlow } from "@/components/sources/ProbeFlow";
import { isVendor } from "@/lib/vendors";
import "@/routes/onboarding-companies.css";

interface RecommendedRow {
  recommendation: CompanyRecommendation;
  probe: ProbeEntryResult;
}

/** Step 3 onboarding: AI candidates, automatic ATS checks, explicit activation. */
export default function OnboardingCompaniesPage() {
  const navigate = useNavigate();
  const recommendations = useCompanyRecommendations();
  const candidates = recommendations.data?.recommendations ?? [];
  const probes = useProbeRecommendedCompanies(candidates);
  const refresh = useRefreshCompanyRecommendations();

  if (recommendations.isPending || (candidates.length > 0 && probes.isPending)) {
    return <CompanyLoading />;
  }
  if (recommendations.isError || recommendations.data === undefined) {
    return (
      <Box padding="xxl">
        <Alert type="error" header="Company recommendations could not be generated">
          {recommendations.error?.message}
        </Alert>
      </Box>
    );
  }
  if (probes.isError || (candidates.length > 0 && probes.data === undefined)) {
    return (
      <Box padding="xxl">
        <Alert type="error" header="Company boards could not be checked">
          {probes.error?.message}
        </Alert>
      </Box>
    );
  }

  const rows = candidates.map((recommendation, index) => ({
    recommendation,
    probe: probes.data?.results[index] ?? {
      input: recommendation.slug,
      slug: recommendation.slug,
      vendor: null,
      error: null,
    },
  }));
  const editorKey = [
    recommendations.data.profile_fingerprint,
    rows.map(({ recommendation, probe }) => `${recommendation.slug}:${probe.vendor ?? "none"}`).join("|"),
  ].join(":");

  return (
    <ContentLayout
      maxContentWidth={1240}
      header={
        <Header
          variant="h1"
          description="We turned your job profile into company candidates and checked their career boards automatically."
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button onClick={() => navigate("/setup/searches")}>Back</Button>
              <Button
                loading={refresh.isPending}
                loadingText="Finding new matches"
                onClick={() => refresh.mutate()}
              >
                Find new matches
              </Button>
            </SpaceBetween>
          }
        >
          Choose companies to track
        </Header>
      }
    >
      <SpaceBetween size="l">
        <RecommendationEditor key={editorKey} rows={rows} />
        <section id="broad-company-catalog">
          <BroadCompanyCatalog />
        </section>
        <ExpandableSection
          variant="container"
          headerText="Add a company manually"
          headerDescription="Optional · paste a company name, slug, or supported board URL."
        >
          <ProbeFlow />
        </ExpandableSection>
        <Container>
          <div className="company-next-step">
            <div>
              <Box variant="h3">Companies ready</Box>
              <Box color="text-body-secondary">
                Next, review filters, limits, and estimated evaluation usage before saving setup.
              </Box>
            </div>
            <Button variant="primary" onClick={() => navigate("/setup/review")}>Continue to review setup</Button>
          </div>
        </Container>
      </SpaceBetween>
      <ScrollCue />
    </ContentLayout>
  );
}

function ScrollCue() {
  const [shouldHideCue, setShouldHideCue] = useState(false);

  useEffect(() => {
    const target = document.getElementById("broad-company-catalog");
    if (target === null || !("IntersectionObserver" in window)) return;
    const observer = new IntersectionObserver(([entry]) => {
      setShouldHideCue(
        entry === undefined || entry.isIntersecting || entry.boundingClientRect.top < 0,
      );
    }, { threshold: 0.2 });
    observer.observe(target);
    return () => observer.disconnect();
  }, []);

  if (shouldHideCue) return null;
  return (
    <a className="company-scroll-cue" href="#broad-company-catalog">
      <span>
        <span className="company-scroll-cue-title">More options below</span>
        <span className="company-scroll-cue-detail">Broad coverage + manual add</span>
      </span>
      <span className="company-scroll-cue-arrow" aria-hidden="true">↓</span>
    </a>
  );
}

function BroadCompanyCatalog() {
  const catalog = useCompanyCatalog();
  const bulkAdd = useBulkAddCompanies();
  const [inserted, setInserted] = useState<number | null>(null);
  const companies = catalog.data?.companies ?? [];
  const sourceCount = Object.keys(catalog.data?.source_counts ?? {}).length;

  function addAll() {
    if (companies.length === 0) return;
    bulkAdd.mutate(companies, {
      onSuccess: (response) => setInserted(response.inserted),
    });
  }

  return (
    <Container
      header={
        <Header
          variant="h2"
          description="Import companies found in public new-grad and internship lists. Every row comes from a real Greenhouse, Ashby, or Lever job URL."
        >
          Broad coverage
        </Header>
      }
    >
      <SpaceBetween size="m">
        {catalog.data === undefined && !catalog.isError && (
          <Box>
            <Button
              loading={catalog.isFetching}
              loadingText="Loading public company lists"
              onClick={() => void catalog.refetch()}
            >
              Load broad company catalog
            </Button>
          </Box>
        )}
        {catalog.isError && (
          <Alert type="error" header="Broad company catalog could not be loaded">
            {catalog.error.message}
          </Alert>
        )}
        {catalog.data !== undefined && (
          <SpaceBetween size="s">
            <Alert type="info" header={`${companies.length} companies available now`}>
              Deduplicated from {sourceCount} public job lists. Existing tracked companies are skipped automatically.
            </Alert>
            {bulkAdd.error && <Alert type="error">{bulkAdd.error.message}</Alert>}
            {inserted !== null && (
              <Alert type="success" header={`${inserted} new companies added`}>
                The remaining companies were already tracked.
              </Alert>
            )}
            <Box>
              <Button
                variant="primary"
                disabled={companies.length === 0 || inserted !== null}
                loading={bulkAdd.isPending}
                loadingText="Adding broad company catalog"
                onClick={addAll}
              >
                Add all {companies.length} companies
              </Button>
            </Box>
          </SpaceBetween>
        )}
      </SpaceBetween>
    </Container>
  );
}

function CompanyLoading() {
  return (
    <Box padding="xxl" textAlign="center">
      <SpaceBetween size="s" alignItems="center">
        <Spinner size="large" />
        <Box variant="h2">Finding companies and checking career boards</Box>
        <Box color="text-body-secondary">
          Recommendations are generated once; every board is verified before it can be added.
        </Box>
      </SpaceBetween>
    </Box>
  );
}

function RecommendationEditor({ rows }: { rows: RecommendedRow[] }) {
  const bulkAdd = useBulkAddCompanies();
  const [selected, setSelected] = useState<ReadonlySet<string>>(
    new Set(rows.filter(({ probe }) => isResolved(probe)).map(({ recommendation }) => recommendation.slug)),
  );
  const [inserted, setInserted] = useState<number | null>(null);
  const supportedCount = rows.filter(({ probe }) => isResolved(probe)).length;

  function toggle(slug: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(slug)) next.delete(slug);
      else next.add(slug);
      return next;
    });
  }

  function confirm() {
    const companies = rows.flatMap(({ recommendation, probe }) => (
      selected.has(recommendation.slug) && isVendor(probe.vendor)
        ? [{ slug: recommendation.slug, vendor: probe.vendor }]
        : []
    ));
    bulkAdd.mutate(companies, {
      onSuccess: (response) => setInserted(response.inserted),
    });
  }

  if (rows.length === 0) {
    return (
      <Alert type="info" header="No recommendations yet">
        Try finding new matches, or add a company manually below.
      </Alert>
    );
  }

  return (
    <Container
      header={
        <Header
          variant="h2"
          counter={`(${supportedCount} supported)`}
          description="Supported boards are selected for you. Uncheck anything you do not want to track."
        >
          Recommended for your profile
        </Header>
      }
      footer={
        <SpaceBetween size="s">
          {bulkAdd.error && <Alert type="error">{bulkAdd.error.message}</Alert>}
          {inserted !== null && (
            <Alert type="success" header={`${inserted} ${inserted === 1 ? "company" : "companies"} added`}>
              Only selected companies with verified boards were added.
            </Alert>
          )}
          <SpaceBetween direction="horizontal" size="xs" alignItems="center">
            <Button
              variant="primary"
              disabled={selected.size === 0 || inserted !== null}
              loading={bulkAdd.isPending}
              loadingText="Adding companies"
              onClick={confirm}
            >
              Add {selected.size} {selected.size === 1 ? "company" : "companies"}
            </Button>
            <Box color="text-body-secondary">
              {selected.size} selected · {rows.length - supportedCount} unsupported
            </Box>
          </SpaceBetween>
        </SpaceBetween>
      }
    >
      <section className="company-recommendation-list" aria-label="Recommended companies">
        {rows.map(({ recommendation, probe }) => (
          <article
            key={recommendation.slug}
            className={`company-recommendation-row${isResolved(probe) ? " company-recommendation-row-supported" : ""}`}
          >
            <Checkbox
              checked={selected.has(recommendation.slug)}
              disabled={!isResolved(probe) || inserted !== null}
              ariaLabel={`Track ${recommendation.name}`}
              onChange={() => toggle(recommendation.slug)}
            />
            <div>
              <div className="company-recommendation-name">{recommendation.name}</div>
              <div className="company-recommendation-rationale">{recommendation.rationale}</div>
            </div>
            <div className="company-recommendation-board">
              <ProbeStatus probe={probe} />
            </div>
          </article>
        ))}
      </section>
    </Container>
  );
}

function isResolved(probe: ProbeEntryResult): boolean {
  return probe.slug !== null && isVendor(probe.vendor) && probe.error === null;
}

function ProbeStatus({ probe }: { probe: ProbeEntryResult }) {
  if (isVendor(probe.vendor) && probe.error === null) {
    return <StatusIndicator type="success">{probe.vendor} verified</StatusIndicator>;
  }
  if (probe.error !== null) {
    return <StatusIndicator type="error">{probe.error}</StatusIndicator>;
  }
  return <StatusIndicator type="stopped">No supported board found</StatusIndicator>;
}
