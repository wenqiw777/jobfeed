/**
 * useSSE — reactive EventSource hook with auto-reconnect.
 *
 * When `url` is non-null an EventSource is opened.  Every `data:` frame
 * is JSON-parsed into `T` and surfaced through `data`.  An `event: done`
 * frame is treated as graceful stream completion — the connection is
 * closed without triggering a reconnect.
 *
 * When `url` is null the hook is disabled: no connection, data stays
 * null.  Changing `url` resets the stream state, tears down the old
 * source, and opens a new one.
 *
 * Reconnect uses truncated exponential backoff (1 s, 2 s, 4 s, … up to
 * 30 s) on non-`done` disconnects.
 */
import { useEffect, useRef, useState } from "react";

export interface SSEState<T> {
  data: T | null;
  isConnected: boolean;
  /** True once the stream finished gracefully (`event: done`). Stays
   * false on transport errors, so consumers can tell completion apart
   * from a dropped connection. Resets when the url changes. */
  isDone: boolean;
  error: Error | null;
}

const BASE_DELAY_MS = 1_000;
const MAX_DELAY_MS = 30_000;

export function useSSE<T>(url: string | null): SSEState<T> {
  const [data, setData] = useState<T | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  // The url whose stream finished gracefully. `isDone` is derived by
  // comparing against the current url, so it resets on url changes
  // without a synchronous setState in the effect body.
  const [doneUrl, setDoneUrl] = useState<string | null>(null);

  // Track whether the stream finished gracefully (event: done) so the
  // cleanup path knows not to reconnect.
  const doneRef = useRef(false);

  // Reset stream state the moment the url changes (the sanctioned
  // adjust-state-during-render pattern — no effect-body setState).
  const [prevUrl, setPrevUrl] = useState<string | null>(url);
  if (prevUrl !== url) {
    setPrevUrl(url);
    setData(null);
    setIsConnected(false);
    setError(null);
    setDoneUrl(null);
  }

  useEffect(() => {
    if (url === null) {
      return;
    }

    let es: EventSource | null = null;
    let attempt = 0;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let cancelled = false;
    doneRef.current = false;

    function connect(): void {
      if (cancelled) return;
      es = new EventSource(url!);

      es.onopen = () => {
        if (cancelled) return;
        attempt = 0;
        setIsConnected(true);
        setError(null);
        // A stale done marker from a previous stream on the same url
        // must not leak into this one.
        setDoneUrl(null);
      };

      es.onmessage = (event: MessageEvent) => {
        if (cancelled) return;
        try {
          setData(JSON.parse(event.data) as T);
        } catch {
          // Non-JSON data frame — ignore.
        }
      };

      es.addEventListener("done", (event: Event) => {
        if (cancelled) return;
        doneRef.current = true;
        const me = event as MessageEvent;
        if (me.data) {
          try {
            setData(JSON.parse(me.data) as T);
          } catch {
            // Best-effort parse of the final frame.
          }
        }
        es?.close();
        setIsConnected(false);
        setDoneUrl(url);
      });

      es.onerror = () => {
        if (cancelled) return;
        es?.close();
        setIsConnected(false);
        if (doneRef.current) return;
        setError(new Error("SSE connection lost"));
        const delay = Math.min(BASE_DELAY_MS * 2 ** attempt, MAX_DELAY_MS);
        attempt += 1;
        reconnectTimer = setTimeout(connect, delay);
      };
    }

    connect();

    return () => {
      cancelled = true;
      es?.close();
      if (reconnectTimer !== null) clearTimeout(reconnectTimer);
      setIsConnected(false);
    };
  }, [url]);

  const isDone = url !== null && doneUrl === url;
  return { data, isConnected, isDone, error };
}
