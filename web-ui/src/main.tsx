import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// Self-hosted fonts (D11): bundled from @fontsource, no CDN requests.
import "@fontsource/geist/400.css";
import "@fontsource/geist/500.css";
import "@fontsource/geist/600.css";
import "@fontsource/geist/700.css";
import "@fontsource/geist-mono/400.css";
import "@fontsource/geist-mono/500.css";
import "@cloudscape-design/global-styles/index.css";

import "./styles.css";

import App from "./App";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import { DensityProvider } from "@/lib/density";
import { setGlobalTheme } from "@cloudscape-design/components/theming";

setGlobalTheme({
  tokens: {
    fontFamilyBase: "Geist, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif",
    fontFamilyMonospace: "Geist Mono, ui-monospace, SFMono-Regular, monospace",
    colorTextAccent: "rgb(39 66 214)",
    colorBackgroundButtonPrimaryDefault: "rgb(39 66 214)",
    colorBackgroundButtonPrimaryHover: "rgb(31 55 184)",
    colorBorderButtonNormalDefault: "rgb(174 183 239)",
    borderRadiusButton: "6px",
    borderRadiusContainer: "8px",
  },
});

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
        <TooltipProvider delayDuration={300}>
          <BrowserRouter>
            <App />
          </BrowserRouter>
          <Toaster />
        </TooltipProvider>
      </DensityProvider>
    </QueryClientProvider>
  </StrictMode>,
);
