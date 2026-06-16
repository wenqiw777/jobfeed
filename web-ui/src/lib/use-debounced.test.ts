import { renderHook } from "@testing-library/react";

import { useDebouncedCallback } from "./use-debounced";

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

test("does not fire before the delay elapses", () => {
  const spy = vi.fn();
  const { result } = renderHook(() => useDebouncedCallback(spy, 250));

  result.current("a");
  vi.advanceTimersByTime(249);
  expect(spy).not.toHaveBeenCalled();

  vi.advanceTimersByTime(1);
  expect(spy).toHaveBeenCalledExactlyOnceWith("a");
});

test("a call burst collapses into one trailing call with the last args", () => {
  const spy = vi.fn();
  const { result } = renderHook(() => useDebouncedCallback(spy, 250));

  for (const value of ["a", "ac", "acm", "acme"]) {
    result.current(value);
    vi.advanceTimersByTime(100); // each call resets the timer
  }
  expect(spy).not.toHaveBeenCalled();

  vi.advanceTimersByTime(250);
  expect(spy).toHaveBeenCalledExactlyOnceWith("acme");
});

test("always invokes the latest callback, not the one from bind time", () => {
  const first = vi.fn();
  const second = vi.fn();
  const { result, rerender } = renderHook(({ fn }) => useDebouncedCallback(fn, 250), {
    initialProps: { fn: first },
  });

  result.current("x");
  rerender({ fn: second });
  vi.advanceTimersByTime(250);

  expect(first).not.toHaveBeenCalled();
  expect(second).toHaveBeenCalledExactlyOnceWith("x");
});

test("unmount drops the pending call", () => {
  const spy = vi.fn();
  const { result, unmount } = renderHook(() => useDebouncedCallback(spy, 250));

  result.current("a");
  unmount();
  vi.advanceTimersByTime(500);

  expect(spy).not.toHaveBeenCalled();
});
