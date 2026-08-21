import { useState } from "react";
import { useNavigate } from "react-router";
import Alert from "@cloudscape-design/components/alert";
import Button from "@cloudscape-design/components/button";
import Modal from "@cloudscape-design/components/modal";
import SpaceBetween from "@cloudscape-design/components/space-between";

import {
  useActivatePersonalML,
  usePersonalMLStatus,
  type PersonalMLStatus,
} from "@/api/configuration";

/** Persistent workspace notice shown only when filtering needs a user decision. */
export function PersonalMLBanner() {
  const [dismissed, setDismissed] = useState(false);
  const [reviewOpen, setReviewOpen] = useState(false);
  const navigate = useNavigate();
  const status = usePersonalMLStatus();
  const activate = useActivatePersonalML();

  if (dismissed || status.data === undefined) return null;
  if (status.data.state === "ready") {
    return <>
      <Alert
          type="success"
          header="Your personal job filter is ready"
          action={
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="primary" onClick={() => setReviewOpen(true)}>
                Review and turn on
              </Button>
              <Button onClick={() => setDismissed(true)}>Keep learning</Button>
            </SpaceBetween>
          }
        >
          {readyEvidence(status.data)}
        </Alert>
      <Modal
        visible={reviewOpen}
        header="Turn on personal job filtering"
        onDismiss={() => setReviewOpen(false)}
        footer={
          <SpaceBetween direction="horizontal" size="xs">
            <Button onClick={() => setReviewOpen(false)}>Cancel</Button>
            <Button
              variant="primary"
              loading={activate.isPending}
              onClick={() => activate.mutate(undefined, { onSuccess: () => setReviewOpen(false) })}
            >
              Turn on personal filter
            </Button>
          </SpaceBetween>
        }
      >
        <SpaceBetween size="s">
          <div>{readyEvidence(status.data)}</div>
          <div>
            Jobs rejected by the filter remain recoverable in the Job library. The filter pauses automatically if recent recall falls below 90%.
          </div>
          {activate.isError && <Alert type="error">{activate.error.message}</Alert>}
        </SpaceBetween>
      </Modal>
    </>;
  }
  if (status.data.state === "paused") {
    return (
      <Alert
        type="warning"
        header="Personal job filtering is paused"
        action={<Button onClick={() => navigate("/setup")}>Review filter</Button>}
      >
        Recent recall fell below 90%. No jobs are being hidden while the filter relearns.
      </Alert>
    );
  }
  return null;
}

function readyEvidence(status: PersonalMLStatus): string {
  const recall = percent(status.quick_pass_recall);
  const rejection = percent(status.quick_fail_rejection);
  const reduction = percent(status.estimated_call_reduction);
  return `Validated on ${status.shadow_count} future jobs · ${recall} recall · ${rejection} irrelevant jobs rejected · about ${reduction} fewer Quick evaluations`;
}

function percent(value: number | null): string {
  return `${Math.round((value ?? 0) * 100)}%`;
}
