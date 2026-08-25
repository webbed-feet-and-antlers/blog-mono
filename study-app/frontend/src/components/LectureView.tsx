import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, ArrowLeft } from "lucide-react";
import { useNavigate, useRouterState } from "@tanstack/react-router";
import * as api from "../api/client";
import { track } from "../api/track";
import type { SlideTimestamp } from "../types";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Textarea } from "@/components/ui/textarea";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

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
  // Throttle playback telemetry to one event per 5s.
  const lastPlaybackTrackRef = useRef(0);

  function emitPlayback(positionSecs: number, event: string) {
    const now = Date.now();
    if (now - lastPlaybackTrackRef.current < 5000) return;
    lastPlaybackTrackRef.current = now;
    track("lecture.playback", {
      lecture_id: lectureId,
      position_secs: Math.round(positionSecs),
      event,
    });
  }

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

  if (lecture.isError) {
    return (
      <div className="lecture-loading">
        <AlertCircle size={24} />
        Lecture not found — it may have been deleted.
        <Button variant="ghost" onClick={() => navigate({ to: "/" })}>
          Back to home
        </Button>
      </div>
    );
  }

  if (lecture.isLoading || !data) {
    return (
      <div className="lecture-loading">
        <Spinner className="size-6" />
        Loading lecture…
      </div>
    );
  }

  return (
    <div className="lecture-view">
      <div className="lecture-header">
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => navigate({ to: "/" })}
              aria-label="Back"
            >
              <ArrowLeft size={20} />
            </Button>
          </TooltipTrigger>
          <TooltipContent>Back</TooltipContent>
        </Tooltip>
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
          onSeeked={(e) => emitPlayback(e.currentTarget.currentTime, "seek")}
          onPause={(e) => emitPlayback(e.currentTarget.currentTime, "pause")}
          onPlay={(e) => emitPlayback(e.currentTarget.currentTime, "resume")}
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
              <Alert className="border-accent-strong bg-accent text-muted-foreground">
                <Spinner className="size-4" />
                <AlertDescription>
                  {transcriptStatus === "transcribing"
                    ? "Transcribing audio…"
                    : "Waiting for transcription to start…"}
                </AlertDescription>
              </Alert>
            ) : transcriptStatus === "failed" ? (
              <Alert variant="destructive">
                <AlertDescription>Transcription failed</AlertDescription>
              </Alert>
            ) : transcript ? (
              <pre className="doc-text lecture-transcript">{transcript}</pre>
            ) : (
              <div className="empty">No audio recording for this lecture.</div>
            )}
          </div>

          {/* Notes */}
          <div className="lecture-notes-section">
            <h3 className="section-label">Notes</h3>
            <Textarea
              className="lecture-notes-editor"
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
