import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2, Mic, AlertCircle } from "lucide-react";
import * as api from "../api/client";
import type { DocumentDetail } from "../types";

interface Props {
  doc: DocumentDetail;
}

/**
 * Renders the document content — audio player for recordings, text for
 * PDFs/TXT/MD. For audio documents, polls for transcription status and
 * shows the transcript once available.
 */
export function DocumentView({ doc }: Props) {
  const queryClient = useQueryClient();
  const isAudio = doc.kind === "audio";
  const status = doc.transcription_status;

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
        <div className="transcription-banner">
          <Loader2 size={16} className="spinner" />
          Waiting for transcription to start…
        </div>
      )}
      {isAudio && status === "transcribing" && (
        <div className="transcription-banner">
          <Loader2 size={16} className="spinner" />
          Transcribing audio… this may take a minute.
        </div>
      )}
      {isAudio && status === "failed" && (
        <div className="error">
          <AlertCircle size={16} />
          Transcription failed: {doc.transcription_error || "Unknown error"}
        </div>
      )}

      {/* Transcript / extracted text */}
      {(!isAudio || status === "done") && doc.text && (
        <pre className="doc-text">{doc.text}</pre>
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
