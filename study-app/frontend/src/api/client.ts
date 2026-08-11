// Thin typed wrapper over the backend API. Uses the Vite dev-server proxy
// (/api -> http://127.0.0.1:8000), so in dev no base URL is needed.

import type {
  AgentMemory,
  ContentItem,
  Document,
  DocumentDetail,
  GenerateRequest,
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

export async function uploadDocument(file: File): Promise<Document> {
  const form = new FormData();
  form.append("file", file);
  return request<Document>("/api/documents", { method: "POST", body: form });
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
