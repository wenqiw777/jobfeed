import {
  dateInputToIso,
  dateTimeInputToIso,
  followupAtFromNow,
  formatEstimatedPostedDate,
  formatLocalDateTime,
  formatRunAxisDateTime,
  formatRelativeAge,
} from "./dates";

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

  test("older than 30 days renders an unambiguous readable date", () => {
    expect(formatRelativeAge("2026-01-05T10:00:00", NOW)).toBe("Jan 5, 2026");
  });
});

test("labels a missing posting date as an estimate from its added date", () => {
  expect(formatEstimatedPostedDate("2026-06-14T12:00:00Z")).toBe("~Jun 14, 2026");
});

describe("formatLocalDateTime", () => {
  test("renders local wall-clock YYYY-MM-DD HH:mm with zero padding", () => {
    // A local-offset-free input keeps the expectation TZ-independent.
    const local = new Date(2026, 5, 3, 8, 5, 0).toISOString();
    expect(formatLocalDateTime(local)).toBe("2026-06-03 08:05");
  });

  test("null and unparseable render an em dash", () => {
    expect(formatLocalDateTime(null)).toBe("—");
    expect(formatLocalDateTime("not-a-date")).toBe("—");
  });
});

test("formatRunAxisDateTime uses a readable compact local timestamp", () => {
  const local = new Date(2026, 5, 3, 8, 5).toISOString();
  expect(formatRunAxisDateTime(local)).toBe("6/3 08:05");
  expect(formatRunAxisDateTime("not-a-date")).toBe("Unknown time");
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

describe("dateTimeInputToIso", () => {
  test("datetime-local value parses as local time and emits UTC ISO", () => {
    const iso = dateTimeInputToIso("2026-07-01T14:30");
    expect(iso).toBe(new Date(2026, 6, 1, 14, 30, 0).toISOString());
  });

  test("blank or malformed input returns null", () => {
    expect(dateTimeInputToIso("")).toBeNull();
    expect(dateTimeInputToIso("garbage")).toBeNull();
  });
});
