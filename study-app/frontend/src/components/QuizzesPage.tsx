import { ContentListPage } from "./ContentListPage";

/** Global quizzes listing — all quizzes across all documents. */
export function QuizzesPage() {
  return (
    <ContentListPage
      type="quiz"
      title="Quizzes"
      emptyMessage="No quizzes yet. Generate one from any document, or ask the agent on the home page."
    />
  );
}
