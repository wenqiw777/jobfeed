/**
 * Minimal typed fetch wrapper for the jobfeed API.
 *
 * Every non-2xx response is parsed against the backend's single error
 * shape — `{error: {code, message, request_id}}` (web/errors.py) — and
 * rethrown as ApiError so callers can branch on `code` and surface
 * `requestId` for log correlation.
 */

interface ErrorBody {
  error: { code: string; message: string; request_id: string };
}

/**
 * The single error type apiFetch throws.
 *
 * - HTTP errors with the backend body: backend `code`/`message`/`requestId`.
 * - HTTP errors with any other body: `code: "http_error"`, no requestId.
 * - Fetch rejections (server down, DNS, CORS, aborted): `status: 0`,
 *   `code: "network_error"`, no requestId — the request never produced
 *   a response.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | null;

  constructor(
    status: number,
    code: string,
    message: string,
    requestId: string | null = null,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }
}

function isErrorBody(body: unknown): body is ErrorBody {
  if (typeof body !== "object" || body === null || !("error" in body)) {
    return false;
  }
  const error = (body as { error: unknown }).error;
  if (typeof error !== "object" || error === null) {
    return false;
  }
  const { code, message, request_id } = error as Record<string, unknown>;
  return (
    typeof code === "string" &&
    typeof message === "string" &&
    typeof request_id === "string"
  );
}

async function toApiError(response: Response): Promise<ApiError> {
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    // Non-JSON error body (proxy hiccup etc.); fall through to the generic shape.
  }
  if (isErrorBody(body)) {
    const { code, message, request_id } = body.error;
    return new ApiError(response.status, code, message, request_id);
  }
  return new ApiError(response.status, "http_error", `HTTP ${response.status}`);
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      headers: { Accept: "application/json", ...init?.headers },
    });
  } catch (cause) {
    // No response at all (server down, DNS, CORS, abort) — wrap so callers
    // handle exactly one error type.
    const message = cause instanceof Error ? cause.message : String(cause);
    throw new ApiError(0, "network_error", message);
  }
  if (!response.ok) {
    throw await toApiError(response);
  }
  return (await response.json()) as T;
}
