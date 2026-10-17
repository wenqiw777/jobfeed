import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Alert from "@cloudscape-design/components/alert";
import Badge from "@cloudscape-design/components/badge";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import Container from "@cloudscape-design/components/container";
import ContentLayout from "@cloudscape-design/components/content-layout";
import ExpandableSection from "@cloudscape-design/components/expandable-section";
import Header from "@cloudscape-design/components/header";
import Link from "@cloudscape-design/components/link";
import Modal from "@cloudscape-design/components/modal";
import Pagination from "@cloudscape-design/components/pagination";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Spinner from "@cloudscape-design/components/spinner";
import Tabs from "@cloudscape-design/components/tabs";
import { apiFetch, apiPost } from "@/api/client";

interface Source { id: number; platform: string; url: string; apply_url?: string; jd_text?: string; jd_quality?: string }
interface Job { id: string; company: string; title: string; location: string; status: string; url: string; apply_url?: string; sources: Source[] }
interface Pair { id: string; left: Job; right: Job; reasons?: string[]; decision?: string; target_id?: string; decided_at?: string }
interface Choice { item: Pair; target: Job }
const PAGE_SIZE = 10;
const reasons: Record<string, string> = {
  multiple_identity_candidates: "Several records may describe the same opening",
  conflicting_role_facts: "Source records disagree on role details",
  conflicting_requisition_ids: "Different job identifiers were found",
  explicit_status_conflict: "The records have different application statuses",
};

export default function IdentityReviewPage() {
  const client = useQueryClient();
  const [view, setView] = useState({ status: "pending", page: 1 });
  const [choice, setChoice] = useState<Choice | null>(null);
  const [saved, setSaved] = useState("");
  const query = useQuery({ queryKey: ["identity-review", view], queryFn: () => apiFetch<{ total: number; items: Pair[] }>(`/api/identity-review?status=${view.status}&limit=${PAGE_SIZE}&offset=${(view.page - 1) * PAGE_SIZE}`) });
  const mutation = useMutation({
    mutationFn: ({ item, target }: { item: Pair; target?: Job }) => apiPost(`/api/identity-review/${item.left.id}/${item.right.id}/decision`, target ? { decision: "merge", target_id: Number(target.id) } : { decision: "separate" }),
    onSuccess: async (_data, variables) => {
      setChoice(null); setSaved(variables.target ? `Merged into job #${variables.target.id}. Sources and history were preserved.` : "Kept as separate jobs. This pair will not be automatically merged or flagged again.");
      await client.invalidateQueries();
      if (query.data?.items.length === 1 && view.page > 1) setView({ ...view, page: view.page - 1 });
    },
  });
  return <ContentLayout header={<Header variant="h2" description="Decide whether these records are the same opening or separate jobs.">Compare possible duplicates</Header>}>
    <SpaceBetween size="l">
      <Alert type="info">Scoring continues while you review. Official job descriptions take priority, followed by LinkedIn. Your decision controls whether records are combined.</Alert>
      <Tabs activeTabId={view.status} onChange={({ detail }) => { setView({ status: detail.activeTabId, page: 1 }); setSaved(""); }} tabs={[{ id: "pending", label: "Pending review" }, { id: "reviewed", label: "Your decisions" }]} />
      {saved && <Alert type="success" dismissible onDismiss={() => setSaved("")}>{saved}</Alert>}
      {query.isPending && <Spinner />}
      {query.isError && <Alert type="error" header="Could not load reviews"><Button onClick={() => void query.refetch()}>Retry</Button></Alert>}
      {mutation.isError && <Alert type="error" header="Decision was not saved">{mutation.error.message}</Alert>}
      {query.data && <SpaceBetween size="l">
        <Box color="text-body-secondary">{query.data.total} {view.status === "pending" ? `${query.data.total === 1 ? "comparison" : "comparisons"} to review` : `${query.data.total === 1 ? "saved decision" : "saved decisions"}`}</Box>
        {!query.data.items.length && <Container><Box textAlign="center" padding="l">{view.status === "pending" ? "You're all caught up. No duplicate records need a decision." : "Your decisions will appear here."}</Box></Container>}
        {query.data.items.map(item => <Container key={item.id} header={<Header variant="h2" description={item.decision ? new Date(item.decided_at!).toLocaleString() : (item.reasons ?? []).map(r => reasons[r] ?? "Possible duplicate records").join(" · ")} actions={<Badge color={item.decision ? "green" : "blue"}>{item.decision === "merge" ? `Merged into #${item.target_id}` : item.decision === "separate" ? "Kept separate" : "Possible duplicate"}</Badge>}>{item.left.company === item.right.company ? item.left.company : `${item.left.company} / ${item.right.company}`}</Header>}>
          <SpaceBetween size="l">
            <ColumnLayout columns={2} variant="text-grid"><JobCard job={item.left} /><JobCard job={item.right} /></ColumnLayout>
            {!item.decision && <SpaceBetween direction="horizontal" size="s">
              <Button disabled={mutation.isPending} onClick={() => mutation.mutate({ item })}>Keep separate</Button>
              <Button disabled={mutation.isPending} onClick={() => { mutation.reset(); setChoice({ item, target: item.left }); }}>Merge into job #{item.left.id}</Button>
              <Button disabled={mutation.isPending} onClick={() => { mutation.reset(); setChoice({ item, target: item.right }); }}>Merge into job #{item.right.id}</Button>
            </SpaceBetween>}
          </SpaceBetween>
        </Container>)}
        {query.data.total > PAGE_SIZE && <Pagination currentPageIndex={view.page} pagesCount={Math.ceil(query.data.total / PAGE_SIZE)} onChange={({ detail }) => setView({ ...view, page: detail.currentPageIndex })} />}
      </SpaceBetween>}
    </SpaceBetween>
    <Modal visible={choice !== null} onDismiss={() => !mutation.isPending && setChoice(null)} header="Merge these job records?" footer={<SpaceBetween direction="horizontal" size="s"><Button disabled={mutation.isPending} onClick={() => setChoice(null)}>Cancel</Button><Button variant="primary" loading={mutation.isPending} onClick={() => choice && mutation.mutate({ item: choice.item, target: choice.target })}>Confirm merge</Button></SpaceBetween>}>
      {choice && <SpaceBetween size="m"><Box variant="strong">Keep job #{choice.target.id}: {choice.target.company} · {choice.target.title}</Box><Box>Both records will become one job. Source postings, application records and evaluation history will be preserved. Other possible duplicates will remain for you to review.</Box><Alert type="warning">This merge cannot be undone from this page. Choose Keep separate if these are different openings.</Alert>{mutation.isError && <Alert type="error">{mutation.error.message}</Alert>}</SpaceBetween>}
    </Modal>
  </ContentLayout>;
}

function JobCard({ job }: { job: Job }) {
  return <SpaceBetween size="m">
    <Header variant="h3" description={`Job #${job.id} · ${job.location || "Location not listed"}`}>{job.title}</Header>
    <SpaceBetween direction="horizontal" size="s"><Badge color="grey">{job.status}</Badge><Link href={safeUrl(job.url)} external>Original posting</Link>{job.apply_url && job.apply_url !== job.url && <Link href={safeUrl(job.apply_url)} external>Application page</Link>}</SpaceBetween>
    <Box color="text-body-secondary" fontSize="body-s"><span style={{ overflowWrap: "anywhere" }}>{job.apply_url || job.url}</span></Box>
    <ExpandableSection headerText={`${job.sources.length} source posting${job.sources.length === 1 ? "" : "s"}`}>
      <SpaceBetween size="m">{job.sources.map(source => <SpaceBetween key={source.id} size="xs"><Link href={safeUrl(source.url)} external>{source.platform} · source #{source.id}</Link><ExpandableSection headerText="Read job description"><Box><span style={{ whiteSpace: "pre-wrap" }}>{source.jd_text || "No description available."}</span></Box></ExpandableSection></SpaceBetween>)}</SpaceBetween>
    </ExpandableSection>
  </SpaceBetween>;
}
function safeUrl(url: string) { try { return ["http:", "https:"].includes(new URL(url).protocol) ? url : undefined; } catch { return undefined; } }
