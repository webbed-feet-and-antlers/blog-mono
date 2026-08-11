import {
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  redirect,
  useRouterState,
  useNavigate,
} from "@tanstack/react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Sidebar } from "./components/Sidebar";
import { RecommendationPanel } from "./components/RecommendationPanel";
import { DocTabView, pendingGenerate } from "./components/DocTabView";
import type { TabId } from "./types";

// Shared query client — created once, used by all routes.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: { refetchOnWindowFocus: false, retry: 1 },
  },
});

// --- Layout shell (shared sidebar + main content via <Outlet>) ---

function Layout() {
  const navigate = useNavigate();
  const docId = useRouterState({
    select: (s) => s.location.pathname.split("/")[2],
  });

  return (
    <QueryClientProvider client={queryClient}>
      <div className="app">
        <Sidebar
          selectedId={docId ?? null}
          onNavigate={(id: string) =>
            navigate({ to: "/documents/$docId", params: { docId: id } })
          }
        />
        <main className="main">
          <Outlet />
        </main>
      </div>
    </QueryClientProvider>
  );
}

// --- Route tree ---

const rootRoute = createRootRoute({ component: Layout });

// / → recommendation panel (home)
const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  component: () => {
    const navigate = useNavigate();
    return (
      <RecommendationPanel
        onNavigate={(docId: string, tab?: string) =>
          tab
            ? navigate({
                to: "/documents/$docId/$tab",
                params: { docId, tab },
              })
            : navigate({ to: "/documents/$docId", params: { docId } })
        }
        onGenerate={(docId: string, taskType: string) => {
          pendingGenerate.value = true;
          navigate({
            to: "/documents/$docId/$tab",
            params: { docId, tab: taskType },
          });
        }}
      />
    );
  },
});

// /documents/$docId → redirect to default tab
const docRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/documents/$docId",
  beforeLoad: ({ params }) => {
    throw redirect({
      to: "/documents/$docId/$tab",
      params: { docId: params.docId, tab: "document" as TabId },
    });
  },
});

// /documents/$docId/$tab → the document view
const docTabRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/documents/$docId/$tab",
  component: DocTabView,
});

const routeTree = rootRoute.addChildren([indexRoute, docRoute, docTabRoute]);

export const router = createRouter({
  routeTree,
  defaultPreload: "intent",
});

// Type declaration for type-safe navigation.
declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
