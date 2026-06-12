import { dateInputToIso, followupAtFromNow, formatRelativeAge } from "./dates";

const NOW = new Date("2026-06-11T12:00:00");

describe("formatRelativeAge", () => {
  test("null and unparseable render an em dash", () => {
    expect(formatRelativeAge(null, NOW)).toBe("—");
    expect(formatRelativeAge("not-a-date", NOW)).toBe("—");
  });

  test("sub-hour and future timestamps render <1h", () => {
    expect(formatRelativeAge("2026-06-11T11:30:00", NOW)).toBe("<1h");
    expect(formatRelativeAge("2026-06-11T13:00:00", NOW)).toBe("<1h");
  });

  test("hours under a day render Xh", () => {
    expect(formatRelativeAge("2026-06-11T00:00:00", NOW)).toBe("12h");
  });

  test("days count calendar boundaries, not 24h blocks", () => {
    // 11:00pm three days back is < 3*24h away but crossed 3 midnights.
    expect(formatRelativeAge("2026-06-08T23:00:00", NOW)).toBe("3d");
    expect(formatRelativeAge("2026-06-10T01:00:00", NOW)).toBe("1d");
  });

  test("older than 30 days renders an absolute M/D/YY date", () => {
    expect(formatRelativeAge("2026-01-05T10:00:00", NOW)).toBe("1/5/26");
  });
});

describe("followupAtFromNow", () => {
  test("offsets by whole days and emits an offset-carrying ISO string", () => {
    const at = followupAtFromNow(7, new Date("2026-06-11T12:00:00Z"));
    expect(at).toBe("2026-06-18T12:00:00.000Z");
  });
});

describe("dateInputToIso", () => {
  test("date-input value becomes 09:00 local as ISO", () => {
    const iso = dateInputToIso("2026-07-01");
    expect(iso).toBe(new Date(2026, 6, 1, 9, 0, 0).toISOString());
  });

  test("blank or malformed input returns null", () => {
    expect(dateInputToIso("")).toBeNull();
    expect(dateInputToIso("garbage")).toBeNull();
  });
});
