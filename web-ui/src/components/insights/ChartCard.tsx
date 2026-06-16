import type { ReactNode } from "react";

/** Shared chart panel: token-styled card with an accessible heading. */
export function ChartCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section
      aria-label={title}
      className="rounded-panel border border-border bg-surface px-4 py-3"
    >
      <h2 className="mb-2 text-h3 text-ink">{title}</h2>
      {children}
    </section>
  );
}

/** Teaching empty state (DESIGN.md: never a bare "no data"). */
export function ChartEmpty({ children }: { children: ReactNode }) {
  return (
    <div className="grid h-40 place-items-center px-6 text-center">
      <p className="text-body-sm text-mute">{children}</p>
    </div>
  );
}
