import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { NotesContent } from "../types";

interface Props {
  content: NotesContent;
}

export function NotesView({ content }: Props) {
  return (
    <div className="notes">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>
        {content.markdown}
      </ReactMarkdown>
    </div>
  );
}
