import { act, renderHook } from "@testing-library/react";

import { useSelection } from "./use-selection";

test("toggle flips a single id and tracks count", () => {
  const { result } = renderHook(() => useSelection());
  expect(result.current.count).toBe(0);

  act(() => result.current.toggle("1"));
  expect(result.current.isSelected("1")).toBe(true);
  expect(result.current.count).toBe(1);

  act(() => result.current.toggle("1"));
  expect(result.current.isSelected("1")).toBe(false);
  expect(result.current.count).toBe(0);
});

test("selectMany adds a batch and clear empties", () => {
  const { result } = renderHook(() => useSelection());

  act(() => result.current.selectMany(["1", "2", "3"]));
  expect(result.current.count).toBe(3);
  expect([...result.current.selectedIds].sort()).toEqual(["1", "2", "3"]);

  act(() => result.current.clear());
  expect(result.current.count).toBe(0);
});

test("deselectMany removes a batch, keeping the rest", () => {
  const { result } = renderHook(() => useSelection());

  act(() => result.current.selectMany(["1", "2", "3"]));
  act(() => result.current.deselectMany(["1", "2"]));
  expect(result.current.selectedIds).toEqual(["3"]);
});

test("selectAll replaces the selection with exactly the given ids", () => {
  const { result } = renderHook(() => useSelection());

  act(() => result.current.toggle("99"));
  act(() => result.current.selectAll(["1", "2"]));
  expect([...result.current.selectedIds].sort()).toEqual(["1", "2"]);
  expect(result.current.isSelected("99")).toBe(false);
});
