import { StrictMode, type ReactNode } from "react";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { useRealJobsList } from "./queries";

afterEach(() => vi.unstubAllGlobals());

it("shares the pending list request across the startup remount", async () => {
  const pending: Array<(response: Response) => void> = [];
  const fetch = vi.fn(() => new Promise<Response>(resolve => pending.push(resolve)));
  vi.stubGlobal("fetch", fetch);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <StrictMode><QueryClientProvider client={client}>{children}</QueryClientProvider></StrictMode>
  );
  const first = renderHook(
    () => useRealJobsList({ decision: "results", limit: 25, offset: 0 }), { wrapper },
  );
  first.unmount();
  const { result, unmount } = renderHook(
    () => useRealJobsList({ decision: "results", limit: 25, offset: 0 }), { wrapper },
  );
  await act(async () => {
    for (const resolve of pending) resolve(new Response(JSON.stringify({ jobs: [], total: 0, tab_counts: {} })));
  });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(fetch).toHaveBeenCalledTimes(1);
  unmount();
  client.clear();
});
