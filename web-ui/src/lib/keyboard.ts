import { useEffect, useRef } from "react";

export interface KeyboardMapOptions {
  /** Master switch — false unbinds the listener entirely. */
  enabled?: boolean;
}

/**
 * Keys where a held key may keep firing: movement reads naturally under
 * auto-repeat. Action keys (h/s/a/n/f/o, Enter) fire once per physical
 * press — a held "s" must not archive the whole queue.
 */
const REPEATABLE_KEYS = new Set(["ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight", "j", "k"]);

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false;
  }
  const tag = target.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") {
    return true;
  }
  // isContentEditable is unimplemented in some DOM shims (happy-dom);
  // fall back to the attribute.
  return target.isContentEditable || target.getAttribute("contenteditable") === "true";
}

/**
 * Bind a `event.key -> handler` map on window keydown.
 *
 * Guards (each one is a "typing must never triage" rule):
 * - modifier chords (ctrl/meta/alt) stay with the browser;
 * - events originating in inputs/textareas/selects/contenteditable are
 *   ignored — single-key shortcuts and typing share the same keys;
 * - any mounted [role="dialog"] suspends the map — Radix traps focus
 *   inside the dialog but window listeners still fire, so without this
 *   a stray "s" while a dialog is open would archive the selected row;
 * - auto-repeat (event.repeat) only drives REPEATABLE_KEYS (movement).
 *
 * Matched keys are preventDefault-ed (arrows must not scroll the page).
 */
export function useKeyboardMap(
  map: Record<string, () => void>,
  options: KeyboardMapOptions = {},
): void {
  const { enabled = true } = options;
  // The latest map lives in a ref so the listener binds once per
  // enabled-flip, not once per render. Synced in an effect (never during
  // render); keydown events always fire after effects have run.
  const mapRef = useRef(map);
  useEffect(() => {
    mapRef.current = map;
  });

  useEffect(() => {
    if (!enabled) {
      return;
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.ctrlKey || event.metaKey || event.altKey) {
        return;
      }
      if (event.repeat && !REPEATABLE_KEYS.has(event.key)) {
        return;
      }
      if (isEditableTarget(event.target)) {
        return;
      }
      if (document.querySelector('[role="dialog"]') !== null) {
        return;
      }
      const handler = mapRef.current[event.key];
      if (handler === undefined) {
        return;
      }
      event.preventDefault();
      handler();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [enabled]);
}
