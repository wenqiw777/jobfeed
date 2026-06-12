/**
 * apiFetch error contract: every failure mode — backend error envelope,
 * foreign/non-JSON bodies, network-level rejection — surfaces as ApiError.
 */
import { ApiError, apiFetch } from "./client";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

async function expectApiError(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (error) {
    expect(error).toBeInstanceOf(ApiError);
    return error as ApiError;
  }
  throw new Error("expected apiFetch to reject");
}

afterEach(() => {
  vi.unstubAllGlobals();
});

test("2xx resolves with the parsed JSON body", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(200, { jobs: [] })));

  await expect(apiFetch("/api/jobs")).resolves.toEqual({ jobs: [] });
});

test("backend error envelope maps onto ApiError fields", async () => {
  const body = {
    error: { code: "not_found", message: "job missing", request_id: "abc123" },
  };
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(404, body)));

  const error = await expectApiError(apiFetch("/api/jobs/9"));
  expect(error.status).toBe(404);
  expect(error.code).toBe("not_found");
  expect(error.message).toBe("job missing");
  expect(error.requestId).toBe("abc123");
});

test("non-JSON error body falls back to the generic http_error shape", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(new Response("<html>502</html>", { status: 502 })),
  );

  const error = await expectApiError(apiFetch("/api/jobs"));
  expect(error.status).toBe(502);
  expect(error.code).toBe("http_error");
  expect(error.message).toBe("HTTP 502");
  expect(error.requestId).toBeNull();
});

test("JSON error body with wrong field types is not trusted", async () => {
  // `code` is a number and message/request_id are missing — the guard
  // must reject it instead of leaking non-strings into ApiError.
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(jsonResponse(500, { error: { code: 500 } })),
  );

  const error = await expectApiError(apiFetch("/api/jobs"));
  expect(error.code).toBe("http_error");
  expect(error.message).toBe("HTTP 500");
});

test("network-level fetch rejection wraps into ApiError(0, network_error)", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockRejectedValue(new TypeError("Failed to fetch")),
  );

  const error = await expectApiError(apiFetch("/api/jobs"));
  expect(error.status).toBe(0);
  expect(error.code).toBe("network_error");
  expect(error.message).toBe("Failed to fetch");
  expect(error.requestId).toBeNull();
});
