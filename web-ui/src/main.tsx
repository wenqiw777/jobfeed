import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import "@cloudscape-design/global-styles/index.css";
import "@/styles.css";

import App from "./App";
import { DensityProvider } from "@/lib/density";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Local tool over loopback: refetches are cheap, but a triage
      // session shouldn't spam the API on every focus change.
      staleTime: 15_000,
      retry: 1,
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <DensityProvider>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </DensityProvider>
    </QueryClientProvider>
  </StrictMode>,
);
