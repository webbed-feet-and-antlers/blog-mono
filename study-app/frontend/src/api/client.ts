// Thin typed wrapper over the backend API. Uses the Vite dev-server proxy
// (/api -> http://127.0.0.1:8000), so in dev no base URL is needed.

import type {
  AgentMemory,
  ConceptWithGraph,
  ContentItem,
  Document,
  DocumentDetail,
  GenerateRequest,
  Lesson,
  Module,
  ModuleTree,
  QuizAttempt,
  TaskType,
} from "../types";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      // non-JSON error body
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// --- Documents ---

export async function uploadDocument(
  file: File,
  lessonId?: string,
  onProgress?: (pct: number) => void,
): Promise<Document> {
  const form = new FormData();
  form.append("file", file);
  const qs = lessonId ? `?lesson_id=${lessonId}` : "";

  // Use XMLHttpRequest for upload progress support (essential for large audio).
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/documents${qs}`);
    if (onProgress) {
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) {
          onProgress(Math.round((e.loaded / e.total) * 100));
        }
      };
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText));
      } else {
        try {
          const body = JSON.parse(xhr.responseText);
          reject(new ApiError(xhr.status, body.detail || xhr.statusText));
        } catch {
          reject(new ApiError(xhr.status, xhr.statusText));
        }
      }
    };
    xhr.onerror = () => reject(new ApiError(0, "Network error"));
    xhr.send(form);
  });
}

export function getDocumentFileUrl(id: string): string {
  return `/api/documents/${id}/file`;
}

export async function listDocuments(): Promise<Document[]> {
  return request<Document[]>("/api/documents");
}

export async function getDocument(id: string): Promise<DocumentDetail> {
  return request<DocumentDetail>(`/api/documents/${id}`);
}

export async function deleteDocument(id: string): Promise<void> {
  await request<void>(`/api/documents/${id}`, { method: "DELETE" });
}

// --- Generation ---

export async function generate(req: GenerateRequest): Promise<ContentItem> {
  return request<ContentItem>("/api/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
}

/**
 * Stream generation progress via SSE. Calls onStatus for each status update,
 * onDone with the final ContentItem, or onError on failure.
 *
 * Uses fetch + ReadableStream (not EventSource) because this is a POST.
 */
export async function generateStream(
  req: GenerateRequest,
  callbacks: {
    onStatus: (status: string) => void;
    onDone: (item: ContentItem) => void;
    onError: (message: string) => void;
  },
): Promise<void> {
  const res = await fetch("/api/generate/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });

  if (!res.ok || !res.body) {
    callbacks.onError(`HTTP ${res.status}`);
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // SSE events are separated by double newlines.
      const events = buffer.split("\n\n");
      buffer = events.pop() ?? ""; // last partial chunk stays in buffer

      for (const raw of events) {
        const eventLine = raw.match(/^event: (.+)$/m);
        const dataLine = raw.match(/^data: (.+)$/m);
        if (!eventLine || !dataLine) continue;

        const eventType = eventLine[1].trim();
        const data = JSON.parse(dataLine[1]);

        if (eventType === "status") {
          callbacks.onStatus(data.status);
        } else if (eventType === "done") {
          callbacks.onDone(data.item);
        } else if (eventType === "error") {
          callbacks.onError(data.message);
        }
      }
    }
  } catch (err) {
    callbacks.onError((err as Error).message);
  }
}

// --- Content ---

export async function listContent(
  documentId?: string,
  type?: TaskType,
): Promise<ContentItem[]> {
  const params = new URLSearchParams();
  if (documentId) params.set("document_id", documentId);
  if (type) params.set("type", type);
  const qs = params.toString();
  return request<ContentItem[]>(`/api/content${qs ? `?${qs}` : ""}`);
}

export async function deleteContent(id: string): Promise<void> {
  await request<void>(`/api/content/${id}`, { method: "DELETE" });
}

// --- Quiz ---

export async function submitQuiz(
  contentId: string,
  answers: Record<string, number>,
): Promise<QuizAttempt> {
  return request<QuizAttempt>(`/api/quiz/${contentId}/attempt`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ answers }),
  });
}

// --- Memory (debug) ---

export async function listMemory(
  refId?: string,
): Promise<AgentMemory[]> {
  const params = new URLSearchParams();
  if (refId) params.set("ref_id", refId);
  const qs = params.toString();
  return request<AgentMemory[]>(`/api/memory${qs ? `?${qs}` : ""}`);
}

// --- Proactive decks ---

export async function listProactiveDecks(): Promise<ContentItem[]> {
  return request<ContentItem[]>("/api/memory/proactive");
}

// --- Learner profile ---

export interface LearnerProfile {
  learner_level: string;
  preferred_difficulty: string;
  preferred_formats: {
    quiz_length: number | null;
    card_style: string | null;
    notes_depth: string | null;
  };
  study_goal: string;
  stats: {
    total_quizzes: number;
    total_flashcard_reviews: number;
    avg_score: number | null;
    score_history: { score: number; difficulty: string; ts: string }[];
    flashcard_known_ratio: number | null;
    first_interaction: string | null;
    last_interaction: string | null;
  };
  updated_at: string | null;
}

export async function getLearnerProfile(): Promise<LearnerProfile> {
  return request<LearnerProfile>("/api/memory/profile");
}

// --- Recommendations ---

export interface Recommendation {
  action: string;
  title: string;
  rationale: string;
  document_id: string | null;
  tab: string | null;
  ready: boolean;
  deck?: { title: string; cards: any[] } | null;
  content_id?: string;
  strategy_name?: string;
  dismissible?: boolean;
  score?: number;
}

export interface RecommendationResponse {
  primary: Recommendation;
  alternatives: Recommendation[];
  context: {
    due_count: number;
    learner_level: string;
    total_concepts: number;
    mastered_count: number;
    welcome_back: string | null;
    total_quizzes: number;
  };
  impression_id: string;
}

export async function getRecommendation(): Promise<RecommendationResponse> {
  return request<RecommendationResponse>("/api/recommend");
}

export async function submitRecommendationFeedback(
  impressionId: string,
  strategyName: string,
  action: string,
  durationSecs?: number,
): Promise<void> {
  await request<{ status: string }>("/api/recommend/feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      impression_id: impressionId,
      strategy_name: strategyName,
      action,
      duration_secs: durationSecs,
    }),
  });
}

// --- Flashcard reviews ---

export async function submitFlashcardReview(
  contentId: string,
  results: { card_id: string; known: boolean; concept: string }[],
): Promise<{ recorded: number }> {
  return request<{ recorded: number }>(
    `/api/flashcards/${contentId}/review`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ results }),
    },
  );
}

// --- Modules & Lessons (organization hierarchy) ---

export async function listModuleTree(): Promise<ModuleTree> {
  return request<ModuleTree>("/api/modules");
}

export async function createModule(title: string): Promise<Module> {
  return request<Module>("/api/modules", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
}

export async function renameModule(id: string, title: string): Promise<Module> {
  return request<Module>(`/api/modules/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
}

export async function deleteModule(id: string): Promise<void> {
  await request<void>(`/api/modules/${id}`, { method: "DELETE" });
}

export async function createLesson(
  moduleId: string,
  title: string,
): Promise<Lesson> {
  return request<Lesson>(`/api/modules/${moduleId}/lessons`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
}

export async function renameLesson(
  id: string,
  title: string,
): Promise<Lesson> {
  return request<Lesson>(`/api/lessons/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
}

export async function deleteLesson(id: string): Promise<void> {
  await request<void>(`/api/lessons/${id}`, { method: "DELETE" });
}

export async function moveDocument(
  docId: string,
  lessonId: string | null,
): Promise<Document> {
  return request<Document>(`/api/documents/${docId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ lesson_id: lessonId }),
  });
}

// --- Concepts (knowledge graph) ---

export async function listConcepts(): Promise<ConceptWithGraph[]> {
  return request<ConceptWithGraph[]>("/api/concepts");
}
