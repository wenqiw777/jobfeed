import { renderHook, act } from "@testing-library/react";

import { useSSE } from "./use-sse";

// ---------------------------------------------------------------------------
// EventSource stub — minimal implementation for testing the hook's
// lifecycle without a real SSE server.
// ---------------------------------------------------------------------------

type ESListener = (event: MessageEvent) => void;
type ESErrorListener = (event: Event) => void;

class FakeEventSource {
  url: string;
  onopen: (() => void) | null = null;
  onmessage: ESListener | null = null;
  onerror: ESErrorListener | null = null;
  readyState = 0; // CONNECTING
  private listeners = new Map<string, ESListener[]>();
  closed = false;

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: ESListener): void {
    const list = this.listeners.get(type) ?? [];
    list.push(listener);
    this.listeners.set(type, list);
  }

  removeEventListener(): void {
    // Not needed for tests.
  }

  close(): void {
    this.closed = true;
    this.readyState = 2; // CLOSED
  }

  // Test helpers

  /** Simulate server opening the connection. */
  _open(): void {
    this.readyState = 1; // OPEN
    this.onopen?.();
  }

  /** Simulate a data: frame. */
  _message(data: string): void {
    const event = new MessageEvent("message", { data });
    this.onmessage?.(event);
  }

  /** Simulate an event: done frame. */
  _done(data = ""): void {
    const event = new MessageEvent("done", { data });
    const listeners = this.listeners.get("done") ?? [];
    for (const listener of listeners) {
      listener(event);
    }
  }

  /** Simulate a connection error. */
  _error(): void {
    this.onerror?.(new Event("error"));
  }

  static instances: FakeEventSource[] = [];
  static reset(): void {
    FakeEventSource.instances = [];
  }
}

beforeEach(() => {
  FakeEventSource.reset();
  vi.stubGlobal("EventSource", FakeEventSource);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

test("returns null data when url is null (disabled)", () => {
  const { result } = renderHook(() => useSSE<{ n: number }>(null));
  expect(result.current.data).toBeNull();
  expect(result.current.isConnected).toBe(false);
  expect(result.current.isDone).toBe(false);
  expect(result.current.error).toBeNull();
  expect(FakeEventSource.instances).toHaveLength(0);
});

test("connects and parses data frames", () => {
  const { result } = renderHook(() => useSSE<{ count: number }>("/api/runs/r1/progress"));
  expect(FakeEventSource.instances).toHaveLength(1);
  const es = FakeEventSource.instances[0]!;

  act(() => es._open());
  expect(result.current.isConnected).toBe(true);
  expect(result.current.isDone).toBe(false);

  act(() => es._message(JSON.stringify({ count: 5 })));
  expect(result.current.data).toEqual({ count: 5 });

  act(() => es._message(JSON.stringify({ count: 10 })));
  expect(result.current.data).toEqual({ count: 10 });
});

test("done event closes connection, sets isDone, without error", () => {
  const { result } = renderHook(() => useSSE<{ count: number }>("/api/runs/r1/progress"));
  const es = FakeEventSource.instances[0]!;

  act(() => es._open());
  act(() => es._done(JSON.stringify({ count: 42 })));

  expect(result.current.data).toEqual({ count: 42 });
  expect(result.current.isConnected).toBe(false);
  expect(result.current.isDone).toBe(true);
  expect(result.current.error).toBeNull();
  expect(es.closed).toBe(true);
});

test("transport error keeps isDone false and schedules a reconnect", () => {
  vi.useFakeTimers();
  const { result } = renderHook(() => useSSE<{ count: number }>("/api/runs/r1/progress"));
  const es = FakeEventSource.instances[0]!;

  act(() => es._open());
  act(() => es._message(JSON.stringify({ count: 7 })));
  act(() => es._error());

  // Disconnected with stale data — but explicitly NOT done.
  expect(result.current.isConnected).toBe(false);
  expect(result.current.isDone).toBe(false);
  expect(result.current.data).toEqual({ count: 7 });
  expect(result.current.error).toBeInstanceOf(Error);

  // Backoff reconnect opens a new source.
  act(() => {
    vi.advanceTimersByTime(1_000);
  });
  expect(FakeEventSource.instances).toHaveLength(2);
});

test("changing url after done resets isDone", () => {
  const { result, rerender } = renderHook(({ url }) => useSSE<{ count: number }>(url), {
    initialProps: { url: "/api/runs/r1/progress" as string | null },
  });
  const es = FakeEventSource.instances[0]!;
  act(() => es._open());
  act(() => es._done(JSON.stringify({ count: 1 })));
  expect(result.current.isDone).toBe(true);

  rerender({ url: "/api/runs/r2/progress" });
  expect(result.current.isDone).toBe(false);
});

test("cleans up on unmount", () => {
  const { unmount } = renderHook(() => useSSE<unknown>("/api/runs/r1/progress"));
  const es = FakeEventSource.instances[0]!;

  act(() => es._open());
  unmount();
  expect(es.closed).toBe(true);
});

test("changing url tears down old and opens new connection", () => {
  const { rerender } = renderHook(({ url }) => useSSE<unknown>(url), {
    initialProps: { url: "/api/runs/r1/progress" as string | null },
  });
  const first = FakeEventSource.instances[0]!;
  act(() => first._open());

  rerender({ url: "/api/runs/r2/progress" });
  expect(first.closed).toBe(true);
  expect(FakeEventSource.instances).toHaveLength(2);
  expect(FakeEventSource.instances[1]!.url).toBe("/api/runs/r2/progress");
});
