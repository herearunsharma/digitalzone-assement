import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import "./styles.css";

/**
 * TanStack Query owns all server state. The alternative — useEffect + useState
 * per view — means hand-rolling caching, in-flight dedup, and loading/error
 * flags in every component, which is where "sane state management" usually goes
 * wrong. Here the rule is simple: anything from the API lives in the query
 * cache, anything the user is typing lives in component state. Nothing is
 * duplicated between the two.
 */
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // The data only changes when someone runs an ingestion, which the user
      // triggers explicitly from this UI — so background refetching on window
      // focus is pure noise here.
      refetchOnWindowFocus: false,
      staleTime: 30_000,
      retry: 1,
    },
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </React.StrictMode>,
);
