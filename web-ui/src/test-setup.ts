import "@testing-library/jest-dom";

// Radix popper-positioned components (dropdown, select, tooltip) probe
// browser APIs that happy-dom may not implement; stub the gaps.
if (typeof globalThis.ResizeObserver === "undefined") {
  class ResizeObserverStub {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  }
  globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;
}

if (typeof Element.prototype.scrollIntoView !== "function") {
  Element.prototype.scrollIntoView = () => {};
}

// TanStack Virtual collapses its range to null when the scroll container
// measures 0x0 — and happy-dom (no layout engine) reports 0 for every
// element's offsetWidth/offsetHeight and getBoundingClientRect. Give all
// elements a fixed laptop-ish size (pixel layout is not under test); the
// no-op ResizeObserver stub above keeps it from being re-observed away.
Object.defineProperty(HTMLElement.prototype, "offsetWidth", {
  configurable: true,
  get: () => 1024,
});
Object.defineProperty(HTMLElement.prototype, "offsetHeight", {
  configurable: true,
  get: () => 768,
});
Element.prototype.getBoundingClientRect = () =>
  ({
    x: 0,
    y: 0,
    width: 1024,
    height: 768,
    top: 0,
    right: 1024,
    bottom: 768,
    left: 0,
    toJSON: () => ({}),
  }) as DOMRect;

if (typeof Element.prototype.hasPointerCapture !== "function") {
  Element.prototype.hasPointerCapture = () => false;
}
if (typeof Element.prototype.setPointerCapture !== "function") {
  Element.prototype.setPointerCapture = () => {};
}
if (typeof Element.prototype.releasePointerCapture !== "function") {
  Element.prototype.releasePointerCapture = () => {};
}
