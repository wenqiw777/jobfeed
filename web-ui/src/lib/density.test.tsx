import { fireEvent, render, screen } from "@testing-library/react";

import { DensityProvider, useDensity } from "./density";

function Probe() {
  const { density, setDensity } = useDensity();
  return (
    <div>
      <span data-testid="mode">{density}</span>
      <button onClick={() => setDensity("comfortable")}>go comfortable</button>
      <button onClick={() => setDensity("compact")}>go compact</button>
    </div>
  );
}

function renderProbe() {
  return render(
    <DensityProvider>
      <Probe />
    </DensityProvider>,
  );
}

beforeEach(() => {
  window.localStorage.clear();
});

test("defaults to Cloudscape compact density", () => {
  renderProbe();

  expect(screen.getByTestId("mode")).toHaveTextContent("compact");
});

test("toggle switches the Cloudscape density and persists", () => {
  renderProbe();

  fireEvent.click(screen.getByText("go comfortable"));

  expect(screen.getByTestId("mode")).toHaveTextContent("comfortable");
  expect(window.localStorage.getItem("jobfeed:density")).toBe("comfortable");
});

test("persisted density survives a re-mount (reload)", () => {
  const first = renderProbe();
  fireEvent.click(screen.getByText("go comfortable"));
  first.unmount();

  renderProbe();

  expect(screen.getByTestId("mode")).toHaveTextContent("comfortable");
});

test("garbage in localStorage falls back to compact", () => {
  window.localStorage.setItem("jobfeed:density", "cozy");

  renderProbe();

  expect(screen.getByTestId("mode")).toHaveTextContent("compact");
});

test("throwing localStorage (privacy mode) still works in-memory", () => {
  const getSpy = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new Error("denied");
  });
  const setSpy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("denied");
  });

  try {
    renderProbe();

    expect(screen.getByTestId("mode")).toHaveTextContent("compact");

    // Persistence fails silently; the toggle itself must not crash.
    fireEvent.click(screen.getByText("go comfortable"));
    expect(screen.getByTestId("mode")).toHaveTextContent("comfortable");
  } finally {
    getSpy.mockRestore();
    setSpy.mockRestore();
  }
});

test("useDensity outside the provider throws", () => {
  // Silence React's error boundary noise for the expected throw.
  const spy = vi.spyOn(console, "error").mockImplementation(() => {});
  expect(() => render(<Probe />)).toThrow(/DensityProvider/);
  spy.mockRestore();
});
