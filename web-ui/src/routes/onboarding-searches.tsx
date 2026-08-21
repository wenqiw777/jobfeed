import Alert from "@cloudscape-design/components/alert";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Checkbox from "@cloudscape-design/components/checkbox";
import ContentLayout from "@cloudscape-design/components/content-layout";
import ExpandableSection from "@cloudscape-design/components/expandable-section";
import FormField from "@cloudscape-design/components/form-field";
import Header from "@cloudscape-design/components/header";
import Input from "@cloudscape-design/components/input";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Spinner from "@cloudscape-design/components/spinner";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router";

import {
  type SearchSource,
  type SearchSuggestion,
  useOnboardingSearches,
  useSaveOnboardingSearches,
} from "@/api/onboarding-searches";
import "@/routes/onboarding-searches.css";

const SEARCH_AREA = "United States";

interface SearchPair {
  key: string;
  query: string;
  location: string;
  linkedin: SearchSuggestion | null;
  indeed: SearchSuggestion | null;
  enabled: boolean;
}

/** Step 3 onboarding: choose paired LinkedIn and Indeed searches. */
export default function OnboardingSearchesPage() {
  const query = useOnboardingSearches();

  if (query.isPending) {
    return <Box padding="xxl" textAlign="center"><Spinner size="large" /></Box>;
  }
  if (query.isError || query.data === undefined) {
    return <Box padding="xxl"><Alert type="error">{query.error?.message ?? "Search setup could not be loaded."}</Alert></Box>;
  }

  return (
    <SearchSelectionEditor
      key={query.data.profile_fingerprint ?? "unbound"}
      initialSearches={query.data.searches}
    />
  );
}

function SearchSelectionEditor({
  initialSearches,
}: {
  initialSearches: SearchSuggestion[];
}) {
  const navigate = useNavigate();
  const save = useSaveOnboardingSearches();
  const [searches, setSearches] = useState<SearchSuggestion[]>(initialSearches);
  const pairs = useMemo(() => pairSearches(searches), [searches]);
  const selectedCount = pairs.filter((pair) => pair.enabled).length;

  function setPairEnabled(pair: SearchPair, enabled: boolean) {
    setSearches((current) => current.map((item) => (
      matchesPair(item, pair) ? { ...item, enabled } : item
    )));
  }

  return (
    <ContentLayout
      maxContentWidth={1320}
      header={
        <Header
          variant="h1"
          description={`We found ${pairs.length} role directions from your résumé. Select the searches you want to run across the United States.`}
          actions={<Button onClick={() => navigate("/setup/resume")}>Back</Button>}
        >
          Choose searches to run
        </Header>
      }
    >
      <SpaceBetween size="l">
        <section className="search-guide" aria-label="Selection instructions">
          <div>
            <div className="search-guide-title">One choice activates both job sources</div>
            <div className="search-guide-copy">
              Each selected role runs once on LinkedIn Guest and once on Indeed across the United States.
            </div>
          </div>
          <div className="search-selection-count">{selectedCount} selected</div>
        </section>

        <section aria-label="Search choices">
          <div className="search-pair-legend" aria-hidden="true">
            <span />
            <span>Role</span>
            <span><i className="search-source-dot search-source-dot-linkedin" />LinkedIn Guest</span>
            <span><i className="search-source-dot search-source-dot-indeed" />Indeed</span>
          </div>
          <div className="search-pair-list">
            {pairs.map((pair) => (
              <SearchPairRow
                key={pair.key}
                pair={pair}
                onChange={(enabled) => setPairEnabled(pair, enabled)}
              />
            ))}
          </div>
        </section>

        <CustomSearchPairForm
          linkedinTemplate={searches.find((item) => item.source === "linkedin_guest") ?? null}
          indeedTemplate={searches.find((item) => item.source === "indeed") ?? null}
          onAdd={(items) => setSearches((current) => [...current, ...items])}
        />

        {save.error && <Alert type="error">{save.error.message}</Alert>}
        <section className="search-save-bar">
          <div>
            <div className="search-save-summary">
              {selectedCount} search pairs · {selectedCount * 2} source queries
            </div>
            <div className="search-save-note">You control which searches are saved.</div>
          </div>
          <Button
            variant="primary"
            loading={save.isPending}
            disabled={selectedCount === 0}
            onClick={() => save.mutate(searches, {
              onSuccess: () => navigate("/setup/companies"),
            })}
          >
            Save {selectedCount} {selectedCount === 1 ? "search" : "searches"} and continue
          </Button>
        </section>
      </SpaceBetween>
    </ContentLayout>
  );
}

function SearchPairRow({
  pair,
  onChange,
}: {
  pair: SearchPair;
  onChange: (enabled: boolean) => void;
}) {
  return (
    <article className={`search-pair-row${pair.enabled ? " search-pair-row-selected" : ""}`}>
      <div className="search-pair-check">
        <Checkbox
          ariaLabel={`Select ${pair.query}`}
          checked={pair.enabled}
          onChange={({ detail }) => onChange(detail.checked)}
        />
      </div>
      <div className="search-pair-identity">
        <div className="search-pair-title">{pair.query}</div>
      </div>
      <SourcePreview source="linkedin_guest" search={pair.linkedin} />
      <SourcePreview source="indeed" search={pair.indeed} />
    </article>
  );
}

function SourcePreview({
  source,
  search,
}: {
  source: SearchSource;
  search: SearchSuggestion | null;
}) {
  const label = source === "linkedin_guest" ? "LinkedIn Guest" : "Indeed";
  return (
    <div className={`search-source-preview search-source-preview-${source}`}>
      <div className="search-source-name">{label}</div>
      <div className="search-source-query">
        {search ? search.query : "Not available"}
      </div>
    </div>
  );
}

function CustomSearchPairForm({
  linkedinTemplate,
  indeedTemplate,
  onAdd,
}: {
  linkedinTemplate: SearchSuggestion | null;
  indeedTemplate: SearchSuggestion | null;
  onAdd: (items: SearchSuggestion[]) => void;
}) {
  const [query, setQuery] = useState("");
  const canAdd = query.trim() !== ""
    && linkedinTemplate !== null
    && indeedTemplate !== null;

  function add() {
    if (!canAdd || linkedinTemplate === null || indeedTemplate === null) return;
    const base = `manual-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const trimmedQuery = query.trim();
    onAdd([
      manualSearch(
        base,
        "linkedin_guest",
        trimmedQuery,
        SEARCH_AREA,
        replaceSearchQuery(linkedinTemplate.url, "keywords", trimmedQuery),
        true,
      ),
      manualSearch(
        base,
        "indeed",
        trimmedQuery,
        SEARCH_AREA,
        replaceSearchQuery(indeedTemplate.url, "q", trimmedQuery),
        true,
      ),
    ]);
    setQuery("");
  }

  return (
    <ExpandableSection
      variant="container"
      headerText="Add a custom search pair"
      headerDescription="Optional · enter a role and both source URLs are created automatically."
    >
      <SpaceBetween size="m">
        <FormField
          label="Role or query"
          description="LinkedIn Guest and Indeed searches will use the same United States filters as the suggestions above."
        >
          <Input value={query} onChange={({ detail }) => setQuery(detail.value)} />
        </FormField>
        <Button disabled={!canAdd} onClick={add}>Add search pair</Button>
      </SpaceBetween>
    </ExpandableSection>
  );
}

function pairSearches(searches: SearchSuggestion[]): SearchPair[] {
  const pairs = new Map<string, SearchPair>();
  for (const search of searches) {
    const key = `${search.query}\u0000${search.location}`;
    const pair = pairs.get(key) ?? {
      key,
      query: search.query,
      location: search.location,
      linkedin: null,
      indeed: null,
      enabled: false,
    };
    if (search.source === "linkedin_guest") pair.linkedin = search;
    else pair.indeed = search;
    pair.enabled ||= search.enabled;
    pairs.set(key, pair);
  }
  return [...pairs.values()];
}

function matchesPair(search: SearchSuggestion, pair: SearchPair) {
  return search.query === pair.query && search.location === pair.location;
}

function manualSearch(
  base: string,
  source: SearchSource,
  query: string,
  location: string,
  url: string,
  enabled: boolean,
): SearchSuggestion {
  return {
    id: `${base}-${source}`,
    source,
    query: query.trim(),
    location: location.trim(),
    url: url.trim(),
    enabled,
  };
}

function replaceSearchQuery(url: string, parameter: string, query: string) {
  const next = new URL(url);
  next.searchParams.set(parameter, query);
  return next.toString();
}
