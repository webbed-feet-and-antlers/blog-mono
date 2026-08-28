import React from "react";
import ReactDOM from "react-dom/client";
import { ClerkProvider } from "@clerk/clerk-react";
import { shadcn } from "@clerk/ui/themes";
import { RouterProvider } from "@tanstack/react-router";
import { router } from "./router";
import { ThemeProvider } from "./theme";
import { ClerkTokenBridge } from "./components/Auth";
import "./index.css";

const clerkKey = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY as string | undefined;

if (!clerkKey) {
  // Fail loud and specific — a blank screen here would be mysterious.
  ReactDOM.createRoot(document.getElementById("root")!).render(
    <div className="flex min-h-screen items-center justify-center bg-background p-6">
      <div className="max-w-md space-y-2 text-center">
        <h1 className="text-lg font-semibold">Missing Clerk key</h1>
        <p className="text-sm text-muted-foreground">
          Set <code>VITE_CLERK_PUBLISHABLE_KEY</code> in{" "}
          <code>frontend/.env</code> (and <code>CLERK_SECRET_KEY</code> in{" "}
          <code>backend/.env</code>). Create a dev app at
          dashboard.clerk.com — see study-app/README.md.
        </p>
      </div>
    </div>,
  );
} else {
  ReactDOM.createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <ClerkProvider publishableKey={clerkKey} appearance={{ theme: shadcn }}>
        <ClerkTokenBridge />
        <ThemeProvider>
          <RouterProvider router={router} />
        </ThemeProvider>
      </ClerkProvider>
    </React.StrictMode>,
  );
}
