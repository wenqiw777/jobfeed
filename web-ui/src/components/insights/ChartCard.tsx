import Box from "@cloudscape-design/components/box";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import type { ReactNode } from "react";

/** Cloudscape data container with an accessible section heading. */
export function ChartCard({ title, description, children }: { title: string; description?: string; children: ReactNode }) {
  return (
    <Container fitHeight header={<Header variant="h2" description={description}>{title}</Header>}>
      <section aria-label={title}>
        {children}
      </section>
    </Container>
  );
}

/** Teaching empty state with enough context to suggest a next action. */
export function ChartEmpty({ children }: { children: ReactNode }) {
  return (
    <Box padding="xxl" textAlign="center" color="text-body-secondary">
      {children}
    </Box>
  );
}
