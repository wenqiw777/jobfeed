/**
 * Toast store lifecycle: dismissal flips `open` (so the exit animation
 * plays) and removal — which frees one of the 3 queue slots — follows
 * after the animation delay.
 */
import { act, renderHook } from "@testing-library/react";

import { toast, useToast } from "./use-toast";

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  // Drain the module-level queue so tests stay independent.
  const { result } = renderHook(() => useToast());
  act(() => {
    result.current.dismiss();
    vi.advanceTimersByTime(300);
  });
  vi.useRealTimers();
});

test("programmatic dismiss closes immediately and frees the slot after the delay", () => {
  const { result } = renderHook(() => useToast());

  let handle!: ReturnType<typeof toast>;
  act(() => {
    handle = toast({ title: "first" });
    toast({ title: "second" });
    toast({ title: "third" });
  });
  expect(result.current.toasts).toHaveLength(3);

  act(() => {
    handle.dismiss();
  });

  // Still occupying a slot while the fade-out plays, but closed.
  expect(result.current.toasts).toHaveLength(3);
  expect(result.current.toasts.find((item) => item.id === handle.id)?.open).toBe(false);

  act(() => {
    vi.advanceTimersByTime(200);
  });

  // Slot freed: the entry is gone and a new toast fits without evicting.
  expect(result.current.toasts).toHaveLength(2);
  act(() => {
    toast({ title: "fourth" });
  });
  expect(result.current.toasts.map((item) => item.title)).toEqual([
    "fourth",
    "third",
    "second",
  ]);
});

test("close-button path (onOpenChange false) plays the exit animation before removal", () => {
  const { result } = renderHook(() => useToast());

  act(() => {
    toast({ title: "closable" });
  });
  const entry = result.current.toasts[0]!;

  act(() => {
    entry.onOpenChange?.(false);
  });

  // Not yanked from the DOM mid-animation: still rendered, just closed.
  expect(result.current.toasts).toHaveLength(1);
  expect(result.current.toasts[0]?.open).toBe(false);

  act(() => {
    vi.advanceTimersByTime(200);
  });
  expect(result.current.toasts).toHaveLength(0);
});

test("dismissing twice does not double-schedule removal", () => {
  const { result } = renderHook(() => useToast());

  let handle!: ReturnType<typeof toast>;
  act(() => {
    handle = toast({ title: "once" });
  });
  act(() => {
    handle.dismiss();
    handle.dismiss();
  });
  act(() => {
    vi.advanceTimersByTime(200);
  });

  expect(result.current.toasts).toHaveLength(0);
});
