import { cn } from "@/lib/utils";

const WINDOW_PRESETS = [7, 30, 90] as const;

/** 7d / 30d / 90d toggle — state lifted to page level. */
export function TimeFilter({
  windowDays,
  onChange,
}: {
  windowDays: number;
  onChange: (days: number) => void;
}) {
  return (
    <div className="flex items-center gap-1.5" role="group" aria-label="Window">
      {WINDOW_PRESETS.map((preset) => (
        <button
          key={preset}
          type="button"
          aria-pressed={windowDays === preset}
          onClick={() => onChange(preset)}
          className={cn(
            "rounded-pill border px-2.5 py-0.5 font-mono text-micro transition-colors",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
            windowDays === preset
              ? "border-accent-border bg-accent-bg text-accent"
              : "border-border text-mute hover:border-border-strong hover:text-ink-2",
          )}
        >
          {preset}d
        </button>
      ))}
    </div>
  );
}
