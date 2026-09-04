import { useEffect, useRef, useState } from "react";
import { useUser } from "@clerk/clerk-react";
import { useNavigate } from "@tanstack/react-router";
import {
  Mic,
  MicOff,
  Monitor,
  Square,
  ArrowLeft,
  FileText,
  ChevronLeft,
  ChevronRight,
  UploadCloud,
  Pause,
  Play,
  Clock,
  Settings,
  Tag,
  Maximize2,
  Minimize2,
  AlertTriangle,
  RotateCcw,
  Bookmark,
  Check,
  X,
} from "lucide-react";
import {
  useRecorder,
  formatTime,
  blobToFile,
  type AudioSource,
} from "../hooks/useRecorder";
import * as api from "../api/client";
import { track } from "../api/track";
import { toast } from "sonner";
import { FileToModuleModal, type FilingTarget } from "./FileToModuleModal";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Spinner } from "@/components/ui/spinner";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

interface SlideTimestampState {
  slide_number: number;
  audio_seconds: number;
}

const DRAFT_KEY_BASE = "study_app_recording_draft";
const METER_SEGMENTS = 24;

export function RecordPage() {
  // Drafts are per-account — two people sharing a browser keep separate drafts.
  const userId = useUser().user?.id ?? "anon";
  const draftKey = `${DRAFT_KEY_BASE}:${userId}`;
  const navigate = useNavigate();
  const {
    isRecording,
    isPaused,
    elapsedSec,
    audioLevel,
    availableDevices,
    selectedDeviceId,
    setSelectedDeviceId,
    screenAudioActive,
    start,
    pause,
    resume,
    stop,
  } = useRecorder();

  // Screen/tab audio capture is Chrome/Edge-only (getDisplayMedia audio);
  // unsupported browsers only see the microphone option.
  const screenAudioSupported =
    typeof navigator !== "undefined" &&
    !!navigator.mediaDevices?.getDisplayMedia;

  const slidesInputRef = useRef<HTMLInputElement>(null);
  const notesTextareaRef = useRef<HTMLTextAreaElement>(null);
  const startPromiseRef = useRef<Promise<Blob> | null>(null);

  const [title, setTitle] = useState("");
  const [notes, setNotes] = useState("");
  const [slidesDocId, setSlidesDocId] = useState<string | null>(null);
  const [slideCount, setSlideCount] = useState(0);
  const [currentSlide, setCurrentSlide] = useState(1);
  const [slideTimestamps, setSlideTimestamps] = useState<SlideTimestampState[]>([]);
  const [slidesUploaded, setSlidesUploaded] = useState(false);
  const [uploadingSlides, setUploadingSlides] = useState(false);
  const [slideLoading, setSlideLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [isFullscreenSlide, setIsFullscreenSlide] = useState(false);
  const [showLeaveModal, setShowLeaveModal] = useState(false);
  // Set when getUserMedia fails on start (permission denied / no mic) so the
  // failed click is surfaced instead of silently doing nothing.
  const [micError, setMicError] = useState<string | null>(null);
  // Audio source for the next recording (microphone / + screen / screen only).
  const [audioSource, setAudioSource] = useState<AudioSource>("mic");
  // Set while the "add to module" prompt is open — the recording keeps
  // running until a choice is made, so nothing is lost on Skip/close.
  const [filingPrompt, setFilingPrompt] = useState(false);
  // Briefly notes when a previous session's title/notes were restored
  const [draftRestored, setDraftRestored] = useState(false);

  // Restore draft on mount
  useEffect(() => {
    try {
      const saved = localStorage.getItem(
        draftKey,
      );
      if (saved) {
        const draft = JSON.parse(saved);
        if (draft.title) setTitle(draft.title);
        if (draft.notes) setNotes(draft.notes);
        if (draft.title || draft.notes) setDraftRestored(true);
      }
    } catch {
      // ignore JSON errors
    }
  }, []);

  // Save draft on edit
  useEffect(() => {
    if (title || notes) {
      localStorage.setItem(
        draftKey,
        JSON.stringify({ title, notes }),
      );
    }
  }, [title, notes]);

  // Window beforeunload warning if actively recording
  useEffect(() => {
    function handleBeforeUnload(e: BeforeUnloadEvent) {
      if (isRecording || isPaused) {
        e.preventDefault();
        e.returnValue = "";
      }
    }
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [isRecording, isPaused]);

  // Log a start only when recording actually began (a cancelled share picker
  // or denied mic never flips isRecording), with the source that was chosen.
  const startLoggedRef = useRef(false);
  useEffect(() => {
    if (isRecording && !startLoggedRef.current) {
      startLoggedRef.current = true;
      track("recording.started", { source: audioSource });
    }
    if (!isRecording) startLoggedRef.current = false;
  }, [isRecording, audioSource]);

  // A live session can also end from outside the page — clicking "Stop
  // sharing" in the browser's bar stops the recorder in useRecorder. Route
  // that into the same stop-and-save flow the Stop button uses, so the
  // captured audio isn't stranded (guards mirror handleStopAndSave).
  useEffect(() => {
    if (
      !isRecording &&
      !isPaused &&
      !saving &&
      !filingPrompt &&
      !showLeaveModal &&
      startPromiseRef.current &&
      elapsedSec >= 1
    ) {
      setFilingPrompt(true);
    }
  }, [isRecording, isPaused, saving, filingPrompt, showLeaveModal, elapsedSec]);

  // Keyboard navigation for slides preview & note timestamp shortcut (Cmd+T / Ctrl+T)
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      const activeEl = document.activeElement;
      const isTyping =
        activeEl &&
        (activeEl.tagName === "INPUT" ||
          activeEl.tagName === "TEXTAREA" ||
          activeEl.tagName === "SELECT" ||
          (activeEl as HTMLElement).isContentEditable);

      // Cmd+T or Ctrl+T in textarea to insert timestamp tag
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "t") {
        if (isTyping && activeEl === notesTextareaRef.current) {
          e.preventDefault();
          insertTimestamp();
          return;
        }
      }

      // Cmd+P or Ctrl+P to post current slide timestamp
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "p") {
        e.preventDefault();
        postCurrentSlide();
        return;
      }

      // Escape exits slide full view. Skip while a modal is open — Radix
      // handles Esc for those and closing both at once feels broken.
      if (
        e.key === "Escape" &&
        isFullscreenSlide &&
        !filingPrompt &&
        !showLeaveModal
      ) {
        setIsFullscreenSlide(false);
        return;
      }

      if (isTyping) return;

      if (e.key === "ArrowRight" || e.key === "PageDown") {
        e.preventDefault();
        nextSlide();
      } else if (e.key === "ArrowLeft" || e.key === "PageUp") {
        e.preventDefault();
        prevSlide();
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [currentSlide, slideCount, elapsedSec, notes, isFullscreenSlide, filingPrompt, showLeaveModal]);

  async function handleSlidesUpload(files: FileList | null) {
    if (!files || files.length === 0) return;
    setUploadingSlides(true);
    try {
      const doc = await api.uploadDocument(files[0]);
      setSlidesDocId(doc.id);
      setSlideCount(doc.page_count || 1);
      setSlidesUploaded(true);
      setCurrentSlide(1);
    } catch (err) {
      console.error("Slide upload failed:", err);
      toast.error("Couldn't upload slides", {
        description:
          "Make sure the file is a valid PDF or PowerPoint deck, then try again.",
      });
    }
    setUploadingSlides(false);
  }

  function recordSlideTimestamp(slideNum: number) {
    setSlideTimestamps((prev) => {
      const filtered = prev.filter((t) => t.slide_number !== slideNum);
      const updated = [...filtered, { slide_number: slideNum, audio_seconds: elapsedSec }];
      return updated.sort((a, b) => a.audio_seconds - b.audio_seconds);
    });
  }

  function postCurrentSlide() {
    recordSlideTimestamp(currentSlide);
  }

  function removeSlideTimestamp(slideNum: number) {
    setSlideTimestamps((prev) => prev.filter((t) => t.slide_number !== slideNum));
  }

  // Preview / flick through slides without auto-posting timestamps
  function nextSlide() {
    if (currentSlide < slideCount) {
      setCurrentSlide(currentSlide + 1);
      setSlideLoading(true);
    }
  }

  function prevSlide() {
    if (currentSlide > 1) {
      setCurrentSlide(currentSlide - 1);
      setSlideLoading(true);
    }
  }

  function jumpToSlide(num: number) {
    if (num >= 1 && num <= slideCount) {
      setCurrentSlide(num);
      setSlideLoading(true);
    }
  }

  function insertTimestamp() {
    const tag = `[${formatTime(elapsedSec)}] `;
    const el = notesTextareaRef.current;
    if (el) {
      const startPos = el.selectionStart;
      const endPos = el.selectionEnd;
      const newText = notes.substring(0, startPos) + tag + notes.substring(endPos);
      setNotes(newText);
      setTimeout(() => {
        el.focus();
        el.setSelectionRange(startPos + tag.length, startPos + tag.length);
      }, 0);
    } else {
      setNotes((prev) => prev + (prev && !prev.endsWith("\n") ? "\n" : "") + tag);
    }
  }

  async function handleStartRecording() {
    setMicError(null);
    const startPromise = start({ source: audioSource });
    startPromiseRef.current = startPromise;
    // The start promise only rejects before recording begins (permission
    // denied, picker cancelled, no audio shared) — a running recorder always
    // resolves on stop.
    startPromise.catch((err) => {
      console.error("Failed to start recording:", err);
      startPromiseRef.current = null;
      setMicError(
        err instanceof Error && err.message
          ? err.message
          : "Microphone unavailable — check this site's microphone permission in your browser settings, then try again."
      );
    });
    // Post initial baseline timestamp for slide 1 at 0s
    recordSlideTimestamp(1);
  }

  function handleStopAndSave() {
    // Guard against saving an empty recording (nothing recorded yet).
    if (!startPromiseRef.current || elapsedSec < 1) return;
    setFilingPrompt(true);
  }

  async function saveLecture(target: FilingTarget | null) {
    setFilingPrompt(false);
    if (!startPromiseRef.current) return;
    setSaving(true);

    stop();
    const audioBlob = await startPromiseRef.current;

    // File the recording (and its slides) into the chosen module/lesson.
    const lessonId = target?.lessonId;
    const moduleId = target && !lessonId ? target.moduleId : undefined;

    const audioFile = blobToFile(audioBlob);
    const audioDoc = await api.uploadDocument(audioFile, lessonId, undefined, moduleId);

    // Slides were uploaded earlier (unfiled) — move them alongside.
    if (slidesDocId) {
      await api.moveDocument(slidesDocId, { lessonId, moduleId }).catch(() => {
        // Filing failure shouldn't lose the lecture.
      });
    }

    const sessionTitle = title.trim() || `Lecture ${new Date().toLocaleDateString()}`;
    const session = await api.createLecture({
      title: sessionTitle,
      audio_doc_id: audioDoc.id,
      slides_doc_id: slidesDocId ?? undefined,
      notes,
      duration_seconds: elapsedSec,
      slide_timestamps: slideTimestamps,
      slide_count: slideCount,
      lesson_id: lessonId ?? undefined,
    });

    localStorage.removeItem(
      draftKey,
    );
    setSaving(false);
    toast.success("Lecture saved", { description: sessionTitle });
    navigate({ to: "/lecture/$lectureId", params: { lectureId: session.id } });
  }

  function handleBackClick() {
    if (isRecording || isPaused || notes.trim() !== "") {
      setShowLeaveModal(true);
    } else {
      navigate({ to: "/" });
    }
  }

  function confirmLeave() {
    if (isRecording || isPaused) {
      track("recording.discarded", { duration_secs: elapsedSec });
      startPromiseRef.current = null; // discarded — don't trigger the save flow
      stop();
    }
    localStorage.removeItem(
      draftKey,
    );
    setShowLeaveModal(false);
    navigate({ to: "/" });
  }

  const currentSlideTimestamp = slideTimestamps.find(
    (t) => t.slide_number === currentSlide
  );

  const litSegments = Math.round(
    ((isPaused ? 0 : audioLevel) / 100) * METER_SEGMENTS
  );

  return (
    <div className={`record-page ${isFullscreenSlide ? "fullscreen-active" : ""}`}>
      {/* State changes announced once per transition (not every timer tick) */}
      <span className="sr-only" aria-live="polite">
        {isRecording && !isPaused
          ? "Recording"
          : isPaused
            ? "Recording paused"
            : "Recording stopped"}
      </span>

      {/* Header Bar */}
      <div className="record-header">
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              onClick={handleBackClick}
              aria-label="Back to dashboard"
            >
              <ArrowLeft size={20} />
            </Button>
          </TooltipTrigger>
          <TooltipContent>Back to dashboard</TooltipContent>
        </Tooltip>
        <Input
          className="record-title-input h-9 min-w-0 flex-1 border-0 bg-transparent px-2 text-base shadow-none focus-visible:ring-0 dark:bg-transparent"
          placeholder="Lecture title…"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />

        {/* Audio source: microphone, screen/tab audio, or both mixed */}
        <Select
          value={audioSource}
          onValueChange={(v) => setAudioSource(v as AudioSource)}
          disabled={isRecording || isPaused}
        >
          <SelectTrigger
            className="h-8 w-48 gap-1.5 text-xs"
            aria-label="Audio source"
          >
            <Monitor size={14} className="shrink-0 text-muted-foreground" />
            <SelectValue placeholder="Microphone" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="mic">Microphone</SelectItem>
            <SelectItem value="mic+screen" disabled={!screenAudioSupported}>
              Microphone + screen audio
            </SelectItem>
            <SelectItem value="screen" disabled={!screenAudioSupported}>
              Screen audio only
            </SelectItem>
          </SelectContent>
        </Select>

        {/* Audio Input Device Dropdown */}
        {availableDevices.length > 0 && (
          <Select
            value={selectedDeviceId ?? undefined}
            onValueChange={setSelectedDeviceId}
            disabled={isRecording || isPaused || audioSource === "screen"}
          >
            <SelectTrigger
              className="h-8 w-52 gap-1.5 text-xs"
              aria-label="Select microphone input"
            >
              <Settings size={14} className="shrink-0 text-muted-foreground" />
              <SelectValue placeholder="Microphone" />
            </SelectTrigger>
            <SelectContent>
              {availableDevices.map((dev, idx) => (
                <SelectItem key={dev.deviceId || idx} value={dev.deviceId || `mic-${idx}`}>
                  {dev.label || `Microphone ${idx + 1}`}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </div>

      {/* Mic-start failure (permission denied / no input device) */}
      {micError && (
        <Alert variant="destructive" className="record-mic-alert">
          <MicOff />
          <AlertTitle>Couldn't start recording</AlertTitle>
          <AlertDescription>{micError}</AlertDescription>
          <button
            className="alert-dismiss"
            onClick={() => setMicError(null)}
            aria-label="Dismiss"
          >
            <X size={14} />
          </button>
        </Alert>
      )}

      {/* Sticky transport: status/timer + level meter + controls in one row.
          Only rendered while a session is live — the idle state has its own
          hero CTA below. */}
      {(isRecording || isPaused) && (
        <div className="record-transport" role="region" aria-label="Recording controls">
          <div className="record-timer">
            {/* Note: isRecording stays true while paused (see useRecorder),
                so paused-state checks must come first. */}
            <div className={`recording-pulse ${isPaused ? "paused" : ""}`} />
            <span
              className={`timer-text ${isPaused ? "paused" : "recording"}`}
              role="timer"
            >
              {formatTime(elapsedSec)}
            </span>
            {isPaused && <span className="paused-badge">PAUSED</span>}
          </div>

          {/* Segmented level meter (decorative, driven by audioLevel) */}
          <div
            className="audio-meter"
            title={`${
              audioSource === "screen"
                ? "Screen audio"
                : audioSource === "mic+screen"
                  ? "Mixed mic + screen"
                  : "Microphone"
            } level: ${audioLevel}%`}
            aria-hidden="true"
          >
            {Array.from({ length: METER_SEGMENTS }, (_, i) => (
              <span
                key={i}
                className={`meter-seg ${
                  i < litSegments ? (i >= METER_SEGMENTS - 4 ? "hot" : "on") : ""
                }`}
              />
            ))}
          </div>

          {screenAudioActive && (
            <span
              className="screen-audio-chip"
              title="The recording captures the shared tab/screen's audio. Clicking 'Stop sharing' in your browser ends the session."
            >
              <Monitor size={13} />
              Screen audio — “Stop sharing” ends it
            </span>
          )}

          <div className="transport-actions">
            {isPaused ? (
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-9 rounded-full border-ok/30 bg-ok/10 px-4 text-ok hover:bg-ok/20 hover:text-ok"
                    onClick={resume}
                  >
                    <Play size={16} />
                    Resume
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Resume recording</TooltipContent>
              </Tooltip>
            ) : (
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-9 rounded-full px-4"
                    onClick={() => {
                      track("recording.paused", { duration_secs: elapsedSec });
                      pause();
                    }}
                  >
                    <Pause size={16} />
                    Pause
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Pause recording</TooltipContent>
              </Tooltip>
            )}
            <Button
              size="sm"
              className="h-9 rounded-full bg-destructive px-5 text-white shadow-[0_4px_14px_rgba(220,38,38,0.28)] hover:bg-destructive/90"
              onClick={handleStopAndSave}
              disabled={saving || elapsedSec < 1}
            >
              {saving ? <Spinner className="size-4" /> : <Square size={15} />}
              {saving ? "Saving…" : "Stop & save"}
            </Button>
          </div>
        </div>
      )}

      {/* Idle: calm hero — the timer only appears once a session is live */}
      {!isRecording && !isPaused && (
        <div className="record-idle-hero">
          <Button
            size="lg"
            className="h-[52px] rounded-full px-8 text-base shadow-[var(--shadow-accent)]"
            onClick={handleStartRecording}
          >
            <Mic size={22} />
            Start recording
          </Button>
          <p className="idle-hint">
            Upload slides to follow along live — notes are saved as you type.
          </p>
        </div>
      )}

      {/* Main Workspace Body */}
      <div className="record-body">
        {/* Slides section */}
        <div className={`record-slides ${isFullscreenSlide ? "fullscreen-slide-panel" : ""}`}>
          {!slidesUploaded ? (
            <div
              className="slide-upload-zone"
              onClick={() => slidesInputRef.current?.click()}
            >
              <input
                ref={slidesInputRef}
                type="file"
                accept=".pdf,.pptx,.ppt"
                style={{ display: "none" }}
                onChange={(e) => handleSlidesUpload(e.target.files)}
              />
              {uploadingSlides ? (
                <>
                  <Spinner className="size-7" />
                  <span>Uploading slides…</span>
                </>
              ) : (
                <>
                  <UploadCloud size={32} />
                  <span className="upload-title">Upload slides</span>
                  <span className="upload-hint">
                    PDF or PowerPoint slides will render live during recording
                  </span>
                </>
              )}
            </div>
          ) : (
            <div className="slide-display">
              <div className="slide-image-container">
                {slideLoading && (
                  <div className="slide-loader-overlay">
                    <Spinner className="size-6" />
                  </div>
                )}
                {slidesDocId ? (
                  <img
                    src={api.getDocumentSlideImageUrl(slidesDocId, currentSlide)}
                    alt={`Slide ${currentSlide}`}
                    className="slide-image"
                    onLoad={() => setSlideLoading(false)}
                    onError={(e) => {
                      setSlideLoading(false);
                      (e.target as HTMLImageElement).style.display = "none";
                    }}
                  />
                ) : (
                  <div className="slide-placeholder">
                    <FileText size={48} strokeWidth={1} />
                    <span>Slide {currentSlide} of {slideCount}</span>
                  </div>
                )}

                {/* Fullscreen toggle button */}
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="slide-fullscreen-btn"
                      onClick={() => setIsFullscreenSlide(!isFullscreenSlide)}
                      aria-label={isFullscreenSlide ? "Exit full view" : "Full view"}
                    >
                      {isFullscreenSlide ? <Minimize2 size={18} /> : <Maximize2 size={18} />}
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>
                    {isFullscreenSlide ? "Exit full view" : "Full view"}
                  </TooltipContent>
                </Tooltip>
              </div>

              {/* Navigation & Explicit Post Controls */}
              <div className="slide-nav">
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={prevSlide}
                      disabled={currentSlide <= 1}
                      aria-label="Preview previous slide"
                    >
                      <ChevronLeft size={20} />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>Previous slide (←)</TooltipContent>
                </Tooltip>

                {/* Jump / Preview dropdown */}
                <Select
                  value={String(currentSlide)}
                  onValueChange={(v) => jumpToSlide(Number(v))}
                >
                  <SelectTrigger className="slide-jump-select h-8 w-auto gap-1 text-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {Array.from({ length: slideCount }, (_, i) => i + 1).map((n) => {
                      const isPosted = slideTimestamps.some((t) => t.slide_number === n);
                      return (
                        <SelectItem key={n} value={String(n)} data-posted={isPosted || undefined}>
                          Slide {n} / {slideCount}
                        </SelectItem>
                      );
                    })}
                  </SelectContent>
                </Select>

                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={nextSlide}
                      disabled={currentSlide >= slideCount}
                      aria-label="Preview next slide"
                    >
                      <ChevronRight size={20} />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>Next slide (→)</TooltipContent>
                </Tooltip>

                {/* Explicit Post Slide Button */}
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button
                      className={`post-slide-btn ${currentSlideTimestamp ? "posted" : ""}`}
                      onClick={postCurrentSlide}
                    >
                      {currentSlideTimestamp ? (
                        <>
                          <Check size={14} />
                          Posted @ {formatTime(Math.round(currentSlideTimestamp.audio_seconds))} (Update)
                        </>
                      ) : (
                        <>
                          <Bookmark size={14} />
                          Post Slide {currentSlide} @ {formatTime(elapsedSec)}
                        </>
                      )}
                    </button>
                  </TooltipTrigger>
                  <TooltipContent>
                    {currentSlideTimestamp
                      ? `Slide ${currentSlide} is posted @ ${formatTime(
                          Math.round(currentSlideTimestamp.audio_seconds)
                        )}. Click to update to ${formatTime(elapsedSec)}.`
                      : `Post Slide ${currentSlide} timestamp at ${formatTime(elapsedSec)}`}
                  </TooltipContent>
                </Tooltip>
              </div>

              {/* Posted Slide Timestamps Bar */}
              {slideTimestamps.length > 0 && (
                <div className="slide-timestamps">
                  <span className="timestamps-label">Posted Slides:</span>
                  {slideTimestamps.map((t) => (
                    <span
                      key={t.slide_number}
                      className={`slide-ts-badge ${
                        t.slide_number === currentSlide ? "active" : ""
                      }`}
                    >
                      <button
                        className="slide-ts-click"
                        onClick={() => jumpToSlide(t.slide_number)}
                        title={`Preview Slide ${t.slide_number}`}
                      >
                        <Clock size={11} /> Slide {t.slide_number} @ {formatTime(Math.round(t.audio_seconds))}
                      </button>
                      <button
                        className="slide-ts-remove"
                        onClick={() => removeSlideTimestamp(t.slide_number)}
                        title={`Unpost timestamp for Slide ${t.slide_number}`}
                      >
                        <X size={10} />
                      </button>
                    </span>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Notes section */}
        {!isFullscreenSlide && (
          <div className="record-notes">
            <div className="notes-header-bar">
              <div className="notes-header-left">
                <span className="notes-label">Lecture Notes</span>
                {draftRestored && (
                  <span className="draft-restored">
                    Draft restored from your last session
                    <button
                      onClick={() => setDraftRestored(false)}
                      aria-label="Dismiss draft notice"
                    >
                      <X size={11} />
                    </button>
                  </span>
                )}
              </div>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-7 gap-1.5 px-2.5 text-xs"
                    onClick={insertTimestamp}
                  >
                    <Tag size={13} />
                    Insert {formatTime(elapsedSec)}
                  </Button>
                </TooltipTrigger>
                <TooltipContent>
                  Insert current time marker into notes (⌘T / Ctrl+T)
                </TooltipContent>
              </Tooltip>
            </div>
            <Textarea
              ref={notesTextareaRef}
              className="notes-textarea"
              placeholder="Type lecture notes here…"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
            />
          </div>
        )}
      </div>

      {/* Keyboard shortcut hints */}
      <div className="record-shortcuts" aria-hidden="true">
        <span>
          <kbd>←</kbd>
          <kbd>→</kbd> preview slides
        </span>
        <span className="shortcut-sep" />
        <span>
          <kbd>⌘</kbd>
          <kbd>P</kbd> post slide timestamp
        </span>
        <span className="shortcut-sep" />
        <span>
          <kbd>⌘</kbd>
          <kbd>T</kbd> insert note timestamp
        </span>
      </div>

      {/* Confirmation Leave Modal */}
      {filingPrompt && (
        <FileToModuleModal
          noun="lecture"
          skipLabel="Save unfiled"
          onSelect={saveLecture}
        />
      )}

      <AlertDialog open={showLeaveModal} onOpenChange={setShowLeaveModal}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2">
              <AlertTriangle size={24} className="text-warn" />
              Leave Recording Studio?
            </AlertDialogTitle>
            <AlertDialogDescription>
              {isRecording || isPaused
                ? "You have an active recording in progress. Leaving will stop and discard your current session."
                : "You have unsaved notes in this recording session."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Continue Recording</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-white hover:bg-destructive/90"
              onClick={confirmLeave}
            >
              <RotateCcw size={16} /> Discard & Leave
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
