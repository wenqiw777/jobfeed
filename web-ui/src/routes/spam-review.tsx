import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Alert from "@cloudscape-design/components/alert";
import Badge from "@cloudscape-design/components/badge";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Container from "@cloudscape-design/components/container";
import ContentLayout from "@cloudscape-design/components/content-layout";
import Header from "@cloudscape-design/components/header";
import Link from "@cloudscape-design/components/link";
import Modal from "@cloudscape-design/components/modal";
import Pagination from "@cloudscape-design/components/pagination";
import SpaceBetween from "@cloudscape-design/components/space-between";
import Spinner from "@cloudscape-design/components/spinner";
import Tabs from "@cloudscape-design/components/tabs";
import { apiFetch, apiPost } from "@/api/client";

interface Finding {
  job_id: string; company: string; title: string; disposition: string;
  effective_disposition: string; category: string; evidence: string[]; url: string;
  job_decision: string | null; company_decision: string | null;
}
interface Decision { item: Finding; scope: "job" | "company"; decision: "allow" | "block" | "reset" }
const categories: Record<string, string> = {
  publisher_attribution: "Posting platform identifies a different employer",
  publisher_redirect: "Third-party posting and application redirect",
  unfinished_template: "Unfilled job description template",
  ai_contributor_work: "Task-based AI training or evaluation work",
  training_placement: "Training followed by client placement",
  possible_publisher_or_template: "Employer or posting needs verification",
};
const PAGE_SIZE = 25;

export default function SpamReviewPage() {
  const client = useQueryClient();
  const [view, setView] = useState({ status: "pending", page: 1 });
  const [confirmation, setConfirmation] = useState<Decision | null>(null);
  const query = useQuery({ queryKey: ["spam-review", view], queryFn: () => apiFetch<{ total: number; items: Finding[] }>(`/api/spam-review?status=${view.status}&limit=${PAGE_SIZE}&offset=${(view.page - 1) * PAGE_SIZE}`) });
  const mutation = useMutation({
    mutationFn: ({ item, scope, decision }: Decision) => apiPost(`/api/spam-review/${item.job_id}/decision`, { scope, decision }),
    onSuccess: async () => { setConfirmation(null); await client.invalidateQueries(); },
  });
  function decide(value: Decision) {
    if (value.scope === "company") setConfirmation(value);
    else mutation.mutate(value);
  }
  return <ContentLayout header={<Header variant="h2" description="Review uncertain postings and undo automatic exclusions.">Posting checks</Header>}>
    <SpaceBetween size="l">
      <Alert type="info">Uncertain postings stay in Results and remain eligible for scoring. Only clear findings are automatically excluded. Keeping a company overrides automatic checks, but does not change your existing blocklist or other job filters.</Alert>
      <Tabs activeTabId={view.status} onChange={({ detail }) => setView({ status: detail.activeTabId, page: 1 })} tabs={[
        { id: "pending", label: "Needs review" }, { id: "blocked", label: "Automatically blocked" }, { id: "reviewed", label: "Your decisions" },
      ]} />
      {query.isPending && <Spinner />}
      {query.isError && <Alert type="error" header="Could not load findings"><Button onClick={() => void query.refetch()}>Retry</Button></Alert>}
      {mutation.isError && <Alert type="error">Decision was not saved. Please try again.</Alert>}
      {query.data && <>
        <Box color="text-body-secondary">{query.data.total} findings · New postings are checked automatically when saved or enriched.</Box>
        {query.data.items.length === 0 && <Container><Box textAlign="center" padding="l">{view.status === "pending" ? "No postings need your review." : "No findings in this view."}</Box></Container>}
        {query.data.items.map(item => <Container key={item.job_id} header={<Header variant="h2" description={item.title} actions={<Link href={safeUrl(item.url)} external>Original posting</Link>}>{item.company}</Header>}>
          <SpaceBetween size="m">
            <SpaceBetween direction="horizontal" size="s"><Badge color={item.effective_disposition === "block" ? "red" : "blue"}>{item.effective_disposition === "block" ? "Excluded" : item.effective_disposition === "allow" ? "Kept" : "Needs review"}</Badge><Box>{categories[item.category] ?? "Company decision"}</Box></SpaceBetween>
            {item.company_decision === "auto_block" && <Alert type="warning">Company automatically excluded after repeated independent publisher evidence. You can keep this job or the company.</Alert>}
            {item.evidence.map((text, i) => <Box key={i} variant="p">{text}</Box>)}
            <SpaceBetween direction="horizontal" size="s">
              <Button disabled={mutation.isPending} onClick={() => decide({ item, scope: "job", decision: "allow" })}>Keep job</Button>
              <Button disabled={mutation.isPending} onClick={() => decide({ item, scope: "job", decision: "block" })}>Block job</Button>
              <Button disabled={mutation.isPending || unnamed(item.company)} onClick={() => decide({ item, scope: "company", decision: "allow" })}>Keep company</Button>
              <Button disabled={mutation.isPending || unnamed(item.company)} onClick={() => decide({ item, scope: "company", decision: "block" })}>Block company</Button>
              {item.job_decision && <Button disabled={mutation.isPending} onClick={() => decide({ item, scope: "job", decision: "reset" })}>Reset job decision</Button>}
              {item.company_decision && item.company_decision !== "auto_block" && <Button disabled={mutation.isPending} onClick={() => decide({ item, scope: "company", decision: "reset" })}>Reset company decision</Button>}
            </SpaceBetween>
          </SpaceBetween>
        </Container>)}
        {query.data.total > PAGE_SIZE && <Pagination currentPageIndex={view.page} pagesCount={Math.ceil(query.data.total / PAGE_SIZE)} onChange={({ detail }) => setView({ ...view, page: detail.currentPageIndex })} />}
      </>}
    </SpaceBetween>
    <Modal visible={confirmation !== null} onDismiss={() => setConfirmation(null)} header="Confirm company decision" footer={<SpaceBetween direction="horizontal" size="s"><Button onClick={() => setConfirmation(null)}>Cancel</Button><Button variant="primary" loading={mutation.isPending} onClick={() => confirmation && mutation.mutate(confirmation)}>{confirmation?.decision === "block" ? "Confirm company block" : "Confirm company decision"}</Button></SpaceBetween>}>
      {confirmation && <SpaceBetween size="m"><Box variant="strong">{confirmation.item.company}</Box><Box>{confirmation.decision === "block" ? "Exclude all current and future postings with this exact company name from Results and scoring. Historical records will be retained." : confirmation.decision === "allow" ? "Keep this company's postings from automatic spam exclusion. Individually blocked jobs and your existing blocklist still apply." : "Remove your company decision and use automatic screening again."}</Box></SpaceBetween>}
    </Modal>
  </ContentLayout>;
}
function unnamed(company: string) { return ["", "unknown", "confidential"].includes(company.trim().toLowerCase()); }
function safeUrl(url: string) { try { const parsed = new URL(url); return ["http:", "https:"].includes(parsed.protocol) ? url : undefined; } catch { return undefined; } }
