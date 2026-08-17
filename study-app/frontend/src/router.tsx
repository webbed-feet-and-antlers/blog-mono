import {
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  redirect,
  useNavigate,
  useRouterState,
} from "@tanstack/react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Toaster } from "@/components/ui/sonner";
import {
  SidebarInset,
  SidebarProvider,
  SidebarTrigger,
} from "@/components/ui/sidebar";
import { Separator } from "@/components/ui/separator";
import { AppSidebar } from "./components/Sidebar";
import { RecommendationPanel } from "./components/RecommendationPanel";
import { DocTabView, pendingGenerate } from "./components/DocTabView";
import { RecordPage } from "./components/RecordPage";
import { LectureView } from "./components/LectureView";
import { StudySessionLoader } from "./components/StudySessionView";
import { ConceptsPage } from "./components/ConceptsPage";
import { ModulesPage } from "./components/ModulesPage";
import { QuizzesPage } from "./components/QuizzesPage";
import { FlashcardsPage } from "./components/FlashcardsPage";
import type { TabId } from "./types";

// Shared query client — created once, used by all routes.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: { refetchOnWindowFocus: false, retry: 1 },
  },
});

// --- Layout shell (shadcn Sidebar + main content via <Outlet>) ---

// Routes that render as focused, full-screen experiences (recorder, lecture
// playback, study session) get no sidebar — the page owns its own layout.
const FOCUSED_ROUTES = ["/record", "/lecture", "/study"];

function Layout() {
  const navigate = useNavigate();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const focused = FOCUSED_ROUTES.some(
    (r) => pathname === r || pathname.startsWith(`${r}/`),
  );

  const providers = (children: React.ReactNode) => (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider delayDuration={300}>
        {children}
        <Toaster richColors position="bottom-right" />
      </TooltipProvider>
    </QueryClientProvider>
  );

  if (focused) {
    return providers(
      <main className="main main-focused">
        <Outlet />
      </main>,
    );
  }

  return providers(
    <SidebarProvider
      style={
        { "--sidebar-width": "300px", "--sidebar-width-icon": "3rem" } as React.CSSProperties
      }
    >
      <AppSidebar
        pathname={pathname}
        onNavigate={(id: string) =>
          navigate({ to: "/documents/$docId", params: { docId: id } })
        }
        onHome={() => navigate({ to: "/" })}
        onRecord={() => navigate({ to: "/record" })}
        onConcepts={() => navigate({ to: "/concepts" })}
        onDrive={() => navigate({ to: "/modules" })}
        onQuizzes={() => navigate({ to: "/quizzes" })}
        onFlashcards={() => navigate({ to: "/flashcards" })}
      />
      <SidebarInset>
        <header className="flex h-12 shrink-0 items-center gap-2 border-b bg-background px-4">
          <SidebarTrigger className="-ml-1" />
          <Separator orientation="vertical" className="!h-4" />
        </header>
        <div className="main flex-1">
          <Outlet />
        </div>
      </SidebarInset>
    </SidebarProvider>,
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

// /concepts → global concepts overview (inside Layout shell, sidebar visible)
const conceptsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/concepts",
  component: () => {
    const navigate = useNavigate();
    return <ConceptsPage onStudySession={() => navigate({ to: "/study" })} />;
  },
});

// /modules → module browser organized by semester (inside Layout shell)
const modulesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/modules",
  component: ModulesPage,
});

// /quizzes → all quizzes across all documents
const quizzesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/quizzes",
  component: QuizzesPage,
});

// /flashcards → all flashcard decks across all documents
const flashcardsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/flashcards",
  component: FlashcardsPage,
});

const routeTree = rootRoute.addChildren([
  indexRoute,
  docRoute,
  docTabRoute,
  recordRoute,
  lectureRoute,
  studyRoute,
  conceptsRoute,
  modulesRoute,
  quizzesRoute,
  flashcardsRoute,
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
