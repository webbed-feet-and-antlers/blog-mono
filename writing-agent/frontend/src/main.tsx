import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "sonner";
import { App } from "@/App";
import { EditorProvider } from "@/state/editor";
import { JobProvider } from "@/state/job";
import "./index.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: false },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <EditorProvider>
        <JobProvider>
          <App />
          <Toaster
            position="bottom-right"
            toastOptions={{
              style: {
                background: "var(--panel)",
                color: "var(--text)",
                border: "1px solid var(--line)",
                fontSize: "13px",
              },
            }}
          />
        </JobProvider>
      </EditorProvider>
    </QueryClientProvider>
  </StrictMode>,
);
