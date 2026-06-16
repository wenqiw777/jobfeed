import { renderHook } from "@testing-library/react";

import { useKeyboardMap } from "./keyboard";

/** The full triage key set — every binding must fire its own handler. */
const TRIAGE_KEYS = ["ArrowDown", "ArrowUp", "j", "k", "Enter", "a", "h", "s", "n", "f", "o"];

function makeMap(): Record<string, ReturnType<typeof vi.fn>> {
  return Object.fromEntries(TRIAGE_KEYS.map((key) => [key, vi.fn()]));
}

function press(key: string, init: KeyboardEventInit = {}): void {
  window.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, ...init }));
}

afterEach(() => {
  document.body.innerHTML = "";
});

test("each triage binding fires exactly its own handler", () => {
  const map = makeMap();
  renderHook(() => useKeyboardMap(map));

  for (const key of TRIAGE_KEYS) {
    press(key);
  }
  for (const key of TRIAGE_KEYS) {
    expect(map[key], key).toHaveBeenCalledTimes(1);
  }
});

test("unmapped keys do nothing", () => {
  const map = makeMap();
  renderHook(() => useKeyboardMap(map));

  press("x");
  for (const key of TRIAGE_KEYS) {
    expect(map[key]).not.toHaveBeenCalled();
  }
});

test("modifier chords are left to the browser", () => {
  const map = makeMap();
  renderHook(() => useKeyboardMap(map));

  press("j", { ctrlKey: true });
  press("j", { metaKey: true });
  press("j", { altKey: true });
  expect(map["j"]).not.toHaveBeenCalled();
});

test("events from inputs, textareas, and contenteditable are ignored", () => {
  const map = makeMap();
  renderHook(() => useKeyboardMap(map));

  for (const make of [
    () => document.createElement("input"),
    () => document.createElement("textarea"),
    () => {
      const div = document.createElement("div");
      div.setAttribute("contenteditable", "true");
      return div;
    },
  ]) {
    const el = make();
    document.body.appendChild(el);
    el.dispatchEvent(new KeyboardEvent("keydown", { key: "s", bubbles: true }));
  }
  expect(map["s"]).not.toHaveBeenCalled();
});

test("an open dialog suspends the whole map", () => {
  const map = makeMap();
  renderHook(() => useKeyboardMap(map));

  const dialog = document.createElement("div");
  dialog.setAttribute("role", "dialog");
  document.body.appendChild(dialog);

  press("s");
  expect(map["s"]).not.toHaveBeenCalled();

  dialog.remove();
  press("s");
  expect(map["s"]).toHaveBeenCalledTimes(1);
});

test("auto-repeat drives movement keys but never action keys", () => {
  const map = makeMap();
  renderHook(() => useKeyboardMap(map));

  for (const key of ["ArrowDown", "ArrowUp", "j", "k"]) {
    press(key, { repeat: true });
    expect(map[key], key).toHaveBeenCalledTimes(1);
  }
  for (const key of ["a", "h", "s", "n", "f", "o", "Enter"]) {
    press(key, { repeat: true });
    expect(map[key], key).not.toHaveBeenCalled();
    // The initial (non-repeat) keydown still fires.
    press(key);
    expect(map[key], key).toHaveBeenCalledTimes(1);
  }
});

test("enabled:false unbinds the listener", () => {
  const map = makeMap();
  renderHook(() => useKeyboardMap(map, { enabled: false }));

  press("j");
  expect(map["j"]).not.toHaveBeenCalled();
});

test("matched keys are preventDefault-ed (arrows must not scroll)", () => {
  const map = makeMap();
  renderHook(() => useKeyboardMap(map));

  const event = new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true, cancelable: true });
  window.dispatchEvent(event);
  expect(event.defaultPrevented).toBe(true);
});
