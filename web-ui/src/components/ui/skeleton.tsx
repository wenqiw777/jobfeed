import { cn } from "@/lib/utils";

/** Loading placeholder — DESIGN.md bans content-area spinners. */
function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("animate-pulse rounded-control bg-hairline", className)}
      {...props}
    />
  );
}

export { Skeleton };
