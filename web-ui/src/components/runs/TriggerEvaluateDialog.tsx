import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Form from "@cloudscape-design/components/form";
import FormField from "@cloudscape-design/components/form-field";
import Input from "@cloudscape-design/components/input";
import Modal from "@cloudscape-design/components/modal";
import RadioGroup from "@cloudscape-design/components/radio-group";
import SpaceBetween from "@cloudscape-design/components/space-between";
import { useState } from "react";

import { ApiError } from "@/api/client";
import { useTriggerEvaluate } from "@/api/queries";
import { toast } from "@/components/ui/use-toast";

type Stage = "a" | "b" | "both";

const STAGE_OPTIONS = [
  { label: "Both stages", value: "both", description: "Fast score, then deep review" },
  { label: "Stage A only", value: "a", description: "Fast score only" },
  { label: "Stage B only", value: "b", description: "Deep review for eligible jobs" },
];

/** Cloudscape evaluation form with stage and optional batch limit. */
export function TriggerEvaluateButton() {
  const [isOpen, setIsOpen] = useState(false);
  const [stage, setStage] = useState<Stage>("both");
  const [limitText, setLimitText] = useState("");
  const trigger = useTriggerEvaluate();
  const limit = limitText.trim() === "" ? null : Number(limitText);
  const isLimitInvalid = limit !== null && (Number.isNaN(limit) || limit < 1);

  const submit = () => {
    if (isLimitInvalid) return;
    trigger.mutate(
      { stage, limit },
      {
        onSuccess: () => {
          setIsOpen(false);
          setStage("both");
          setLimitText("");
          toast({ title: `Evaluate started (stage ${stage})` });
        },
        onError: (error) => handleError(error),
      },
    );
  };

  const handleError = (error: unknown) => {
    if (error instanceof ApiError && error.status === 409) {
      toast({
        title: "Evaluate already running",
        description: "Wait for the current evaluation to finish.",
        variant: "destructive",
      });
      return;
    }
    toast({
      title: "Evaluate failed",
      description: error instanceof Error ? error.message : String(error),
      variant: "destructive",
    });
  };

  return (
    <>
      <Button iconName="gen-ai" onClick={() => setIsOpen(true)}>Evaluate</Button>
      <Modal
        visible={isOpen}
        onDismiss={() => setIsOpen(false)}
        closeAriaLabel="Close evaluation form"
        header="Trigger Evaluate"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button onClick={() => setIsOpen(false)}>Cancel</Button>
              <Button
                variant="primary"
                disabled={isLimitInvalid}
                loading={trigger.isPending}
                loadingText="Starting evaluation"
                onClick={submit}
              >
                Start evaluate
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <Form>
          <SpaceBetween size="l">
            <FormField label="Stage" description="Choose which evaluation work to schedule.">
              <RadioGroup
                value={stage}
                items={STAGE_OPTIONS}
                ariaLabel="Stage"
                onChange={({ detail }) => setStage(detail.value as Stage)}
              />
            </FormField>
            <FormField
              label="Limit (optional)"
              description="Leave empty to process every eligible job."
              errorText={isLimitInvalid ? "Limit must be a number of at least 1." : undefined}
            >
              <Input
                type="number"
                value={limitText}
                placeholder="all"
                ariaLabel="Limit"
                invalid={isLimitInvalid}
                nativeInputAttributes={{ min: 1 }}
                onChange={({ detail }) => setLimitText(detail.value)}
              />
            </FormField>
          </SpaceBetween>
        </Form>
      </Modal>
    </>
  );
}
