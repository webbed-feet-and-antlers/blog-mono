// Shared types mirroring the backend Pydantic schemas (app/schemas.py).

export type TaskType = "notes" | "quiz" | "flashcards";

export interface Document {
  id: string;
  filename: string;
  mime: string;
  page_count: number;
  char_count: number;
  uploaded_at: string;
}

export interface DocumentDetail extends Document {
  text: string;
}

export interface QuizQuestion {
  id: string;
  prompt: string;
  options: string[];
  answer_idx: number;
  explanation: string;
}

export interface QuizContent {
  title: string;
  questions: QuizQuestion[];
}

export interface Flashcard {
  id: string;
  front: string;
  back: string;
}

export interface FlashcardContent {
  title: string;
  cards: Flashcard[];
}

export interface NotesContent {
  markdown: string;
}

// `content` shape depends on `type`; narrow at the call site.
export interface ContentItem {
  id: string;
  document_id: string;
  type: TaskType;
  content: QuizContent | FlashcardContent | NotesContent;
  created_at: string;
}

export interface QuizAttempt {
  id: string;
  content_id: string;
  score: number;
  correct_count: number;
  total_count: number;
  answers: Record<string, number>;
  taken_at: string;
}

export interface AgentMemory {
  id: string;
  scope: string;
  ref_id: string;
  key: string;
  value: unknown;
  updated_at: string;
}

export interface GenerateRequest {
  document_id: string;
  task_type: TaskType;
  instructions?: string;
}
