import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { reloadForStaleChunk } from "./components/shell/PageErrorBoundary";
import { AppErrorBoundary } from "./components/shell/AppErrorBoundary";
// The Backtest page's fonts (2026-11-03), bundled so nothing is fetched; used only there (styles.css).
import "@fontsource-variable/inter";
import "@fontsource-variable/jetbrains-mono";
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      staleTime: 3_000,
    },
  },
});

// Vite's own signal that a page's preloaded dependencies are gone (a rebuild since this tab opened):
// reload once, the same remedy PageErrorBoundary applies to the page chunk itself.
window.addEventListener("vite:preloadError", (event) => {
  if (reloadForStaleChunk()) event.preventDefault();
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AppErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </QueryClientProvider>
    </AppErrorBoundary>
  </React.StrictMode>,
);
