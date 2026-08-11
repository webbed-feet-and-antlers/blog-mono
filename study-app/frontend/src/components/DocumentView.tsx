import type { DocumentDetail } from "../types";

interface Props {
  doc: DocumentDetail;
}

/**
 * Renders the original uploaded document's extracted text.
 * This is the source material the agent reads to generate notes/quizzes/cards.
 */
export function DocumentView({ doc }: Props) {
  return (
    <div className="document-view">
      <div className="doc-meta-row">
        <span className="doc-meta-item">{doc.page_count > 1 ? `${doc.page_count} pages` : "1 page"}</span>
        <span className="doc-meta-sep">·</span>
        <span className="doc-meta-item">{(doc.char_count / 1000).toFixed(1)}k characters</span>
        <span className="doc-meta-sep">·</span>
        <span className="doc-meta-item">{doc.mime}</span>
      </div>
      <pre className="doc-text">{doc.text}</pre>
    </div>
  );
}
