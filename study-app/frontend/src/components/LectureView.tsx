import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Loader2 } from "lucide-react";
import { useNavigate, useRouterState } from "@tanstack/react-router";
import * as api from "../api/client";
import type { SlideTimestamp } from "../types";

export function LectureView() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const lectureId = pathname.split("/")[2] ?? "";

  const lecture = useQuery({
    queryKey: ["lecture", lectureId],
    queryFn: () => api.getLecture(lectureId),
    enabled: !!lectureId,
    refetchInterval: (query) => {
      // Poll while transcription is in progress.
      const audio = query.state.data?.audio_doc;
      if (audio?.transcription_status === "pending" || audio?.transcription_status === "transcribing") {
        return 5000;
      }
      return false;
    },
  });

  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const [notesValue, setNotesValue] = useState("");
  const notesTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const data = lecture.data;
  const timestamps: SlideTimestamp[] = data?.slide_timestamps ?? [];
  const slideCount = data?.slide_count ?? 0;

  // Determine the current slide from timestamps.
  const currentSlide = timestamps.length > 0
    ? (() => {
        let slide = 1;
        for (const ts of timestamps) {
          if (currentTime >= ts.audio_seconds) {
            slide = ts.slide_number;
          } else {
            break;
          }
        }
        return Math.min(slide, slideCount || 1);
      })()
    : 1;

  // Sync notes from server on load.
  useEffect(() => {
    if (data?.notes !== undefined) {
      setNotesValue(data.notes);
    }
  }, [data?.notes]);

  // Debounced notes save.
  function handleNotesChange(value: string) {
    setNotesValue(value);
    if (notesTimerRef.current) clearTimeout(notesTimerRef.current);
    notesTimerRef.current = setTimeout(async () => {
      await api.updateLectureNotes(lectureId, value);
      queryClient.invalidateQueries({ queryKey: ["lecture", lectureId] });
    }, 1500);
  }

  const audioDoc = data?.audio_doc;
  const transcriptStatus = audioDoc?.transcription_status;
  const transcript = (audioDoc as any)?.text ?? "";

  function formatDuration(secs: number): string {
    const m = Math.floor(secs / 60);
    const s = secs % 60;
    return `${m}:${s.toString().padStart(2, "0")}`;
  }

  if (lecture.isLoading || !data) {
    return (
      <div className="lecture-loading">
        <Loader2 size={24} className="spinner" />
        Loading lecture…
      </div>
    );
  }

  return (
    <div className="lecture-view">
      <div className="lecture-header">
        <button
          className="ghost icon-btn"
          onClick={() => navigate({ to: "/" })}
          title="Back"
        >
          <ArrowLeft size={20} />
        </button>
        <h2>{data.title}</h2>
        <span className="lecture-duration">{formatDuration(data.duration_seconds)}</span>
      </div>

      {/* Audio player */}
      {audioDoc && (
        <audio
          ref={audioRef}
          controls
          className="lecture-audio"
          src={api.getDocumentFileUrl(audioDoc.id)}
          onTimeUpdate={(e) => setCurrentTime(e.currentTarget.currentTime)}
        />
      )}

      <div className="lecture-body">
        {/* Slides */}
        {slideCount > 0 && (
          <div className="lecture-slides-section">
            <div className="slide-image-container">
              <img
                src={api.getSlideImageUrl(lectureId, currentSlide)}
                alt={`Slide ${currentSlide}`}
                className="slide-image"
              />
            </div>
            <div className="slide-nav">
              <span className="slide-counter">{currentSlide} / {slideCount}</span>
              {timestamps.length > 0 && (
                <span className="slide-ts-hint">
                  Auto-advancing · {timestamps.length} timestamps
                </span>
              )}
            </div>
          </div>
        )}

        <div className="lecture-content-split">
          {/* Transcript */}
          <div className="lecture-transcript-section">
            <h3 className="section-label">Transcript</h3>
            {transcriptStatus === "pending" || transcriptStatus === "transcribing" ? (
              <div className="transcription-banner">
                <Loader2 size={16} className="spinner" />
                {transcriptStatus === "transcribing"
                  ? "Transcribing audio…"
                  : "Waiting for transcription to start…"}
              </div>
            ) : transcriptStatus === "failed" ? (
              <div className="error">Transcription failed</div>
            ) : transcript ? (
              <pre className="doc-text lecture-transcript">{transcript}</pre>
            ) : (
              <div className="empty">No audio recording for this lecture.</div>
            )}
          </div>

          {/* Notes */}
          <div className="lecture-notes-section">
            <h3 className="section-label">Notes</h3>
            <textarea
              className="notes-textarea lecture-notes-editor"
              placeholder="Write notes…"
              value={notesValue}
              onChange={(e) => handleNotesChange(e.target.value)}
            />
          </div>
        </div>
      </div>
    </div>
  );
}
