import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Form from "@cloudscape-design/components/form";
import FormField from "@cloudscape-design/components/form-field";
import Input from "@cloudscape-design/components/input";
import Modal from "@cloudscape-design/components/modal";
import SpaceBetween from "@cloudscape-design/components/space-between";
import { useState } from "react";

import { ApiError } from "@/api/client";
import { useTriggerEvaluate } from "@/api/queries";
import { toast } from "@/components/ui/use-toast";

/** Cloudscape unified evaluation form with an optional batch limit. */
export function TriggerEvaluateButton() {
  const [isOpen, setIsOpen] = useState(false);
  const [limitText, setLimitText] = useState("");
  const trigger = useTriggerEvaluate();
  const limit = limitText.trim() === "" ? null : Number(limitText);
  const isLimitInvalid = limit !== null && (Number.isNaN(limit) || limit < 1);

  const submit = () => {
    if (isLimitInvalid) return;
    trigger.mutate(
      { limit },
      {
        onSuccess: () => {
          setIsOpen(false);
          setLimitText("");
          toast({ title: "Evaluation started" });
        },
        onError: (error) => handleError(error),
      },
    );
  };

  const handleError = (error: unknown) => {
    if (error instanceof ApiError && error.status === 409) {
      toast({
        title: "Evaluation already running",
        description: "Wait for the current evaluation to finish.",
        variant: "destructive",
      });
      return;
    }
    toast({
      title: "Evaluation could not start",
      description: error instanceof Error ? error.message : String(error),
      variant: "destructive",
    });
  };

  return (
    <>
      <Button iconName="gen-ai" onClick={() => setIsOpen(true)}>Start evaluation</Button>
      <Modal
        visible={isOpen}
        onDismiss={() => setIsOpen(false)}
        closeAriaLabel="Close evaluation form"
        header="Start evaluation"
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
                Start evaluation
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <Form>
          <SpaceBetween size="l">
            <FormField
              label="Maximum jobs (optional)"
              description="Leave empty to use the configured default of 150 jobs."
              errorText={isLimitInvalid ? "Maximum jobs must be a number of at least 1." : undefined}
            >
              <Input
                type="number"
                value={limitText}
                placeholder="Configured default: 150"
                ariaLabel="Maximum jobs"
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
