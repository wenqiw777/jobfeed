import { useCallback, useEffect, useRef } from "react";

/**
 * Trailing-edge debounced callback (~250ms default): a call burst
 * collapses into one invocation with the last arguments. Callback-flavored
 * (not value-flavored) on purpose: the Library search must commit the
 * settled text TOGETHER with its page reset in one state update, and a
 * value-debounce would need a setState-in-effect to compose them (banned
 * by the hooks lint). Pending calls are dropped on unmount.
 */
export function useDebouncedCallback<A extends unknown[]>(
  callback: (...args: A) => void,
  delayMs = 250,
): (...args: A) => void {
  // Latest-ref pattern: the timer always invokes the newest callback
  // without rebinding the debounced identity every render.
  const callbackRef = useRef(callback);
  useEffect(() => {
    callbackRef.current = callback;
  });

  const timerRef = useRef<number | null>(null);
  useEffect(
    () => () => {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
      }
    },
    [],
  );

  return useCallback(
    (...args: A) => {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
      }
      timerRef.current = window.setTimeout(() => {
        timerRef.current = null;
        callbackRef.current(...args);
      }, delayMs);
    },
    [delayMs],
  );
}
