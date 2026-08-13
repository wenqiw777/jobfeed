import Box from "@cloudscape-design/components/box";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import type { ReactNode } from "react";

/** Cloudscape chart container with an accessible section heading. */
export function ChartCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section aria-label={title}>
      <Container header={<Header variant="h2">{title}</Header>}>
        {children}
      </Container>
    </section>
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
