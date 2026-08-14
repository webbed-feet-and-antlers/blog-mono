import { ContentListPage } from "./ContentListPage";

/** Global flashcards listing — all decks across all documents. */
export function FlashcardsPage() {
  return (
    <ContentListPage
      type="flashcards"
      title="Flashcards"
      emptyMessage="No flashcards yet. Generate some from any document, or ask the agent on the home page."
    />
  );
}
