import SegmentedControl from "@cloudscape-design/components/segmented-control";

const WINDOW_PRESETS = [7, 30, 90] as const;

/** Shared performance window selector. */
export function TimeFilter({ windowDays, onChange }: { windowDays: number; onChange: (days: number) => void }) {
  return (
    <div role="group" aria-label="Window">
      <SegmentedControl
        label="Window"
        selectedId={String(windowDays)}
        options={WINDOW_PRESETS.map((preset) => ({ id: String(preset), text: `${preset}d` }))}
        onChange={({ detail }) => onChange(Number(detail.selectedId))}
      />
    </div>
  );
}
