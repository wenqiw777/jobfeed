import { X } from "lucide-react";

import type { AttentionResponse } from "@/api/queries";
import { cn } from "@/lib/utils";

/** The three workflow buckets the Pipeline attention bar surfaces. */
export type AttentionBucket = "follow_up_today" | "interview_prep" | "going_ghosted";

const BUCKETS: { key: AttentionBucket; label: string }[] = [
  { key: "follow_up_today", label: "Follow-up due" },
  { key: "interview_prep", label: "Interview prep" },
  { key: "going_ghosted", label: "Going ghosted" },
];

interface AttentionBarProps {
  attention: AttentionResponse | undefined;
  /** The bucket currently filtering the list (null = no filter). */
  activeBucket: AttentionBucket | null;
  /** Toggles: clicking the active chip clears the filter. */
  onToggle: (bucket: AttentionBucket) => void;
}

/**
 * Amber attention chips (consider-family tokens per DESIGN.md — tinted bg
 * + same-hue dark text, never gray on color). Each chip filters the list
 * below to its bucket's job ids; empty buckets are hidden, and the whole
 * bar disappears when every bucket is empty.
 */
export function AttentionBar({ attention, activeBucket, onToggle }: AttentionBarProps) {
  if (attention === undefined) {
    return null;
  }
  const buckets = BUCKETS.filter(({ key }) => attention[key].length > 0);
  if (buckets.length === 0) {
    return null;
  }
  return (
    <div
      role="toolbar"
      aria-label="Attention"
      className="flex flex-wrap items-center gap-1.5 border-b border-hairline px-4 py-2"
    >
      {buckets.map(({ key, label }) => (
        <Chip
          key={key}
          label={label}
          count={attention[key].length}
          isActive={activeBucket === key}
          onClick={() => onToggle(key)}
        />
      ))}
      {activeBucket !== null && (
        <button
          type="button"
          onClick={() => onToggle(activeBucket)}
          className="text-micro text-mute hover:text-ink"
        >
          Clear filter
        </button>
      )}
    </div>
  );
}

interface ChipProps {
  label: string;
  count: number;
  isActive: boolean;
  onClick: () => void;
}

function Chip({ label, count, isActive, onClick }: ChipProps) {
  return (
    <button
      type="button"
      aria-pressed={isActive}
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-pill border px-2.5 py-0.5 text-micro font-medium transition-colors",
        "border-consider/30 bg-consider-bg text-consider",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
        isActive ? "ring-1 ring-inset ring-consider" : "hover:border-consider/60",
      )}
    >
      {label}
      <span className="font-mono">{count}</span>
      {isActive && <X aria-hidden="true" className="h-3 w-3" />}
    </button>
  );
}
