/** Shared placeholder body for zone pages until their tasks land (T9–T12). */
export function ZonePlaceholder({ zone, hint }: { zone: string; hint: string }) {
  return (
    <section aria-label={`${zone} placeholder`} className="px-5 py-4">
      <p className="max-w-prose text-body text-mute">{hint}</p>
    </section>
  );
}
