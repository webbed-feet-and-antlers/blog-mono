/**
 * Clerk auth plumbing components.
 *
 * - <ClerkTokenBridge /> keeps the session JWT fresh in the shared token
 *   store (src/auth.ts) for the non-React fetch/XHR/beacon helpers.
 * - <RequireAuth /> gates the whole app behind a Clerk session, rendering
 *   the embedded <SignIn /> flow when signed out.
 */

import { useEffect } from "react";
import { SignIn, SignUp, useAuth } from "@clerk/clerk-react";
import { useRouterState } from "@tanstack/react-router";
import { setAuthToken } from "../auth";

export function ClerkTokenBridge() {
  const { getToken } = useAuth();

  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      try {
        const token = await getToken();
        if (!cancelled) setAuthToken(token);
      } catch {
        // Signed out mid-refresh — the next successful refresh recovers.
      }
    };
    void refresh();
    // Clerk session JWTs live ~60s; refresh ahead of expiry, plus on focus
    // (covers long-idle tabs returning).
    const id = setInterval(() => void refresh(), 50_000);
    window.addEventListener("focus", refresh);
    return () => {
      cancelled = true;
      clearInterval(id);
      window.removeEventListener("focus", refresh);
    };
  }, [getToken]);

  return null;
}

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <div className="w-full max-w-md">{children}</div>
    </div>
  );
}

export function LoginPage() {
  return (
    <Centered>
      <SignIn fallbackRedirectUrl="/" signUpUrl="/signup" />
    </Centered>
  );
}

export function SignupPage() {
  return (
    <Centered>
      <SignUp fallbackRedirectUrl="/" signInUrl="/login" />
    </Centered>
  );
}

const AUTH_PATHS = ["/login", "/signup"];

export function RequireAuth({ children }: { children: React.ReactNode }) {
  const { isLoaded, isSignedIn } = useAuth();
  const pathname = useRouterState({ select: (s) => s.location.pathname });

  if (!isLoaded) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <p className="text-sm text-muted-foreground">Loading…</p>
      </div>
    );
  }

  if (!isSignedIn && !AUTH_PATHS.includes(pathname)) {
    return <LoginPage />;
  }

  return <>{children}</>;
}
