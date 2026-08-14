// Shared types mirroring the backend Pydantic schemas (app/schemas.py).

export type TaskType = "notes" | "quiz" | "flashcards";
export type TabId = TaskType | "document" | "concepts";

export interface Document {
  id: string;
  filename: string;
  mime: string;
  page_count: number;
  char_count: number;
  uploaded_at: string;
  lesson_id?: string | null;
  module_id?: string | null;
  kind?: string;
  duration_seconds?: number | null;
  transcription_status?: string | null;
  transcription_error?: string | null;
  topic?: string | null;
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
  concept?: string;
}

export interface QuizContent {
  title: string;
  questions: QuizQuestion[];
}

export interface Flashcard {
  id: string;
  front: string;
  back: string;
  concept?: string;
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
  document?: Document | null;
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

// --- Modules & Lessons (organization hierarchy) ---

export interface Lesson {
  id: string;
  title: string;
  module_id: string;
  created_at: string;
  documents: Document[];
}

export interface Module {
  id: string;
  title: string;
  created_at: string;
  lessons: Lesson[];
  documents: Document[];
}

export interface ModuleTree {
  modules: Module[];
  unfiled: Document[];
}

// --- Lecture sessions ---

export interface SlideTimestamp {
  slide_number: number;
  audio_seconds: number;
}

export interface LectureSession {
  id: string;
  lesson_id: string | null;
  title: string;
  audio_doc_id: string | null;
  slides_doc_id: string | null;
  notes: string;
  duration_seconds: number;
  slide_timestamps: SlideTimestamp[];
  slide_count: number;
  status: string;
  created_at: string;
}

export interface LectureSessionDetail extends LectureSession {
  audio_doc?: Document | null;
  slides_doc?: Document | null;
}

// --- Concepts (knowledge graph + mastery) ---

export interface PrerequisiteMastery {
  concept: string;
  mastery_pct: number | null;
  seen: number;
}

export interface ConceptWithGraph {
  concept: string;
  mastery_pct: number | null;
  seen: number;
  correct: number;
  wrong: number;
  due: boolean;
  due_in_days: number | null;
  retrievability: number | null;
  stability: number | null;
  prerequisites: string[];
  related: string[];
  documents: string[];
  modules: string[];
  prerequisite_mastery: PrerequisiteMastery[];
  prerequisite_blocked: boolean;
}
