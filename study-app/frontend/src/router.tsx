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
import { RecordPage } from "./components/RecordPage";
import { LectureView } from "./components/LectureView";
import { StudySessionLoader } from "./components/StudySessionView";
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
          onHome={() => navigate({ to: "/" })}
          onRecord={() => navigate({ to: "/record" })}
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
        onStudySession={() => navigate({ to: "/study" })}
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

// /record → dedicated recording page (no sidebar — focused mode)
const recordRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/record",
  component: () => (
    <QueryClientProvider client={queryClient}>
      <RecordPage />
    </QueryClientProvider>
  ),
});

// /lecture/$lectureId → lecture playback view (no sidebar — immersive)
const lectureRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/lecture/$lectureId",
  component: () => (
    <QueryClientProvider client={queryClient}>
      <LectureView />
    </QueryClientProvider>
  ),
});

// /study → study session (no sidebar — focused study mode)
const studyRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/study",
  component: () => {
    const navigate = useNavigate();
    return (
      <QueryClientProvider client={queryClient}>
        <StudySessionLoader onExit={() => navigate({ to: "/" })} />
      </QueryClientProvider>
    );
  },
});

const routeTree = rootRoute.addChildren([
  indexRoute,
  docRoute,
  docTabRoute,
  recordRoute,
  lectureRoute,
  studyRoute,
]);

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
