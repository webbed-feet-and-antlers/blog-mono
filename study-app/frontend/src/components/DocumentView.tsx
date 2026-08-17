import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Mic, AlertCircle, FileText, FileType } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import * as api from "../api/client";
import { track } from "../api/track";
import type { DocumentDetail } from "../types";
import { PdfViewer } from "./PdfViewer";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Spinner } from "@/components/ui/spinner";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";

interface Props {
  doc: DocumentDetail;
}

/**
 * Renders the document content:
 *  - audio: player + transcript (polled while transcribing)
 *  - PDF: the rendered PDF in an iframe (native viewer), with a toggle to
 *    the extracted text if the user wants it
 *  - TXT/MD: rendered as markdown prose
 */
export function DocumentView({ doc }: Props) {
  const queryClient = useQueryClient();
  const isAudio = doc.kind === "audio";
  const status = doc.transcription_status;
  const isPdf = doc.mime === "application/pdf";
  const [view, setView] = useState<"pdf" | "text">("pdf");

  // Poll for transcription updates if pending/transcribing.
  useEffect(() => {
    if (!isAudio || status === "done" || status === "failed") return;
    const interval = setInterval(() => {
      queryClient.invalidateQueries({ queryKey: ["document", doc.id] });
    }, 5000);
    return () => clearInterval(interval);
  }, [isAudio, status, doc.id]);

  return (
    <div className="document-view">
      <div className="doc-meta-row">
        {isAudio ? (
          <>
            <Mic size={14} />
            <span className="doc-meta-item">Audio recording</span>
          </>
        ) : (
          <span className="doc-meta-item">
            {doc.page_count > 1 ? `${doc.page_count} pages` : "1 page"}
          </span>
        )}
        <span className="doc-meta-sep">·</span>
        <span className="doc-meta-item">
          {(doc.char_count / 1000).toFixed(1)}k characters
        </span>
        <span className="doc-meta-sep">·</span>
        <span className="doc-meta-item">{doc.mime}</span>
      </div>

      {/* Audio player */}
      {isAudio && (
        <div className="audio-player-container">
          <audio
            controls
            className="audio-player"
            src={api.getDocumentFileUrl(doc.id)}
          />
        </div>
      )}

      {/* Transcription status */}
      {isAudio && status === "pending" && (
        <Alert className="mb-4 border-accent-strong bg-accent text-muted-foreground">
          <Spinner className="size-4" />
          <AlertDescription>
            Waiting for transcription to start…
          </AlertDescription>
        </Alert>
      )}
      {isAudio && status === "transcribing" && (
        <Alert className="mb-4 border-accent-strong bg-accent text-muted-foreground">
          <Spinner className="size-4" />
          <AlertDescription>
            Transcribing audio… this may take a minute.
          </AlertDescription>
        </Alert>
      )}
      {isAudio && status === "failed" && (
        <Alert variant="destructive" className="mb-4">
          <AlertCircle />
          <AlertDescription>
            Transcription failed: {doc.transcription_error || "Unknown error"}
          </AlertDescription>
        </Alert>
      )}

      {/* PDF: render the original file in an iframe (native viewer), with a
          toggle to the extracted text. */}
      {isPdf && doc.text !== undefined && (
        <>
          <ToggleGroup
            type="single"
            value={view}
            onValueChange={(v) => {
              if (v && v !== view) {
                track("view.mode_toggled", { document_id: doc.id, mode: v });
                setView(v as "pdf" | "text");
              }
            }}
            className="w-full justify-start gap-1 border-b bg-sidebar px-4 py-2"
          >
            <ToggleGroupItem
              value="pdf"
              className="gap-1.5 rounded-md px-3 py-1 text-xs font-medium text-muted-foreground shadow-none data-[state=on]:bg-card data-[state=on]:text-primary"
            >
              <FileType size={14} />
              Document
            </ToggleGroupItem>
            <ToggleGroupItem
              value="text"
              className="gap-1.5 rounded-md px-3 py-1 text-xs font-medium text-muted-foreground shadow-none data-[state=on]:bg-card data-[state=on]:text-primary"
            >
              <FileText size={14} />
              Extracted text
            </ToggleGroupItem>
          </ToggleGroup>
          {view === "pdf" ? (
            <PdfViewer
              url={api.getDocumentFileUrl(doc.id)}
              filename={doc.filename}
              docId={doc.id}
            />
          ) : (
            doc.text && (
              <div className="doc-prose">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {doc.text}
                </ReactMarkdown>
              </div>
            )
          )}
        </>
      )}

      {/* TXT/MD: rendered as markdown prose */}
      {!isAudio && !isPdf && doc.text && (
        <div className="doc-prose">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{doc.text}</ReactMarkdown>
        </div>
      )}

      {/* Empty state for audio that hasn't been transcribed yet */}
      {isAudio && !doc.text && status !== "done" && status !== "failed" && (
        <div className="empty">
          <Mic size={32} strokeWidth={1.4} style={{ opacity: 0.3, marginBottom: 8 }} />
          Transcript will appear here once transcription completes.
        </div>
      )}
    </div>
  );
}
