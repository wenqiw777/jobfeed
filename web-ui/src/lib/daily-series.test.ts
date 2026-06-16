import { zeroFillDaily, type DailyPoint } from "./daily-series";

const NOW = new Date("2026-06-11T15:30:00Z");

function point(day: string, discovered = 1, evaluated = 0, applied = 0): DailyPoint {
  return { day, discovered, evaluated, applied };
}

test("fills every day of the window, oldest first, zeros where sparse", () => {
  const filled = zeroFillDaily([point("2026-06-10", 4, 2, 1)], 3, NOW);

  expect(filled).toEqual([
    { day: "2026-06-09", discovered: 0, evaluated: 0, applied: 0 },
    { day: "2026-06-10", discovered: 4, evaluated: 2, applied: 1 },
    { day: "2026-06-11", discovered: 0, evaluated: 0, applied: 0 },
  ]);
});

test("an empty series becomes an all-zero window of the right length", () => {
  const filled = zeroFillDaily([], 30, NOW);

  expect(filled).toHaveLength(30);
  expect(filled[0]?.day).toBe("2026-05-13");
  expect(filled.at(-1)?.day).toBe("2026-06-11");
  expect(filled.every((entry) => entry.discovered === 0 && entry.applied === 0)).toBe(true);
});

test("payload days outside the window are dropped", () => {
  const filled = zeroFillDaily([point("2026-06-01"), point("2026-06-11")], 2, NOW);

  expect(filled.map((entry) => entry.day)).toEqual(["2026-06-10", "2026-06-11"]);
  expect(filled[1]?.discovered).toBe(1);
});

test("crosses month boundaries on UTC days, not local time", () => {
  const filled = zeroFillDaily([], 2, new Date("2026-06-01T00:10:00Z"));

  expect(filled.map((entry) => entry.day)).toEqual(["2026-05-31", "2026-06-01"]);
});
