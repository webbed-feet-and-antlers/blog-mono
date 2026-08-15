import { useEffect, useRef, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import {
  Mic,
  Square,
  ArrowLeft,
  FileText,
  ChevronLeft,
  ChevronRight,
  UploadCloud,
  Loader2,
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
import { useRecorder, formatTime, blobToFile } from "../hooks/useRecorder";
import * as api from "../api/client";
import { track } from "../api/track";
import { FileToModuleModal, type FilingTarget } from "./FileToModuleModal";

interface SlideTimestampState {
  slide_number: number;
  audio_seconds: number;
}

const DRAFT_KEY = "study_app_recording_draft";

export function RecordPage() {
  const navigate = useNavigate();
  const {
    isRecording,
    isPaused,
    elapsedSec,
    audioLevel,
    availableDevices,
    selectedDeviceId,
    setSelectedDeviceId,
    start,
    pause,
    resume,
    stop,
  } = useRecorder();

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

  // Restore draft on mount
  useEffect(() => {
    try {
      const saved = localStorage.getItem(DRAFT_KEY);
      if (saved) {
        const draft = JSON.parse(saved);
        if (draft.title) setTitle(draft.title);
        if (draft.notes) setNotes(draft.notes);
      }
    } catch {
      // ignore JSON errors
    }
  }, []);

  // Save draft on edit
  useEffect(() => {
    if (title || notes) {
      localStorage.setItem(DRAFT_KEY, JSON.stringify({ title, notes }));
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
  }, [currentSlide, slideCount, elapsedSec, notes]);

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
    startPromiseRef.current = start();
    // Post initial baseline timestamp for slide 1 at 0s
    recordSlideTimestamp(1);
  }

  // Set while the "add to module" prompt is open — the recording keeps
  // running until a choice is made, so nothing is lost on Skip/close.
  const [filingPrompt, setFilingPrompt] = useState(false);

  function handleStopAndSave() {
    if (!startPromiseRef.current) return;
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

    localStorage.removeItem(DRAFT_KEY);
    setSaving(false);
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
      stop();
    }
    localStorage.removeItem(DRAFT_KEY);
    setShowLeaveModal(false);
    navigate({ to: "/" });
  }

  const currentSlideTimestamp = slideTimestamps.find(
    (t) => t.slide_number === currentSlide
  );

  return (
    <div className={`record-page ${isFullscreenSlide ? "fullscreen-active" : ""}`}>
      {/* Header Bar */}
      <div className="record-header">
        <button
          className="ghost icon-btn"
          onClick={handleBackClick}
          title="Back to dashboard"
        >
          <ArrowLeft size={20} />
        </button>
        <input
          className="record-title-input"
          placeholder="Lecture title…"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />

        {/* Audio Input Device Dropdown */}
        {availableDevices.length > 0 && (
          <div className="mic-select-container">
            <Settings size={15} className="mic-select-icon" />
            <select
              className="mic-select"
              value={selectedDeviceId}
              onChange={(e) => setSelectedDeviceId(e.target.value)}
              disabled={isRecording || isPaused}
              title="Select Microphone Input"
            >
              {availableDevices.map((dev, idx) => (
                <option key={dev.deviceId || idx} value={dev.deviceId}>
                  {dev.label || `Microphone ${idx + 1}`}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>

      {/* Recording Display & Audio Level Visualizer */}
      <div className="record-timer-bar">
        <div className="record-timer">
          {isRecording && <div className="recording-pulse" />}
          {isPaused && <div className="recording-pulse paused" />}
          <span
            className={`timer-text ${
              isRecording ? "recording" : isPaused ? "paused" : ""
            }`}
          >
            {formatTime(elapsedSec)}
          </span>
          {isPaused && <span className="paused-badge">PAUSED</span>}
        </div>

        {/* Audio Level Visualizer Bar */}
        {(isRecording || isPaused) && (
          <div className="audio-meter-bar" title={`Microphone input level: ${audioLevel}%`}>
            <div
              className={`audio-meter-fill ${isPaused ? "paused" : ""}`}
              style={{ width: `${isPaused ? 0 : audioLevel}%` }}
            />
          </div>
        )}
      </div>

      {/* Main Recording Action Buttons */}
      <div className="record-controls">
        {!isRecording && !isPaused ? (
          <button className="record-big-btn" onClick={handleStartRecording}>
            <Mic size={24} />
            Start recording
          </button>
        ) : (
          <div className="recording-action-group">
            {isRecording ? (
              <button
                className="record-action-btn pause-btn"
                onClick={() => { track("recording.paused", { duration_secs: elapsedSec }); pause(); }}
                title="Pause recording"
              >
                <Pause size={20} />
                Pause
              </button>
            ) : (
              <button
                className="record-action-btn resume-btn"
                onClick={resume}
                title="Resume recording"
              >
                <Play size={20} />
                Resume
              </button>
            )}

            <button
              className="record-big-btn recording"
              onClick={handleStopAndSave}
              disabled={saving}
            >
              {saving ? (
                <>
                  <Loader2 size={24} className="spinner" />
                  Saving…
                </>
              ) : (
                <>
                  <Square size={22} />
                  Stop & save
                </>
              )}
            </button>
          </div>
        )}
      </div>

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
                  <Loader2 size={28} className="spinner" />
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
                    <Loader2 size={24} className="spinner" />
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
                <button
                  className="ghost icon-btn slide-fullscreen-btn"
                  onClick={() => setIsFullscreenSlide(!isFullscreenSlide)}
                  title={isFullscreenSlide ? "Exit full view" : "Full view"}
                >
                  {isFullscreenSlide ? <Minimize2 size={18} /> : <Maximize2 size={18} />}
                </button>
              </div>

              {/* Navigation & Explicit Post Controls */}
              <div className="slide-nav">
                <button
                  className="ghost icon-btn"
                  onClick={prevSlide}
                  disabled={currentSlide <= 1}
                  title="Preview previous slide (Left Arrow)"
                >
                  <ChevronLeft size={20} />
                </button>

                {/* Jump / Preview dropdown */}
                <select
                  className="slide-jump-select"
                  value={currentSlide}
                  onChange={(e) => jumpToSlide(Number(e.target.value))}
                  title="Preview specific slide"
                >
                  {Array.from({ length: slideCount }, (_, i) => i + 1).map((n) => {
                    const isPosted = slideTimestamps.some((t) => t.slide_number === n);
                    return (
                      <option key={n} value={n}>
                        Slide {n} / {slideCount} {isPosted ? "✓" : ""}
                      </option>
                    );
                  })}
                </select>

                <button
                  className="ghost icon-btn"
                  onClick={nextSlide}
                  disabled={currentSlide >= slideCount}
                  title="Preview next slide (Right Arrow)"
                >
                  <ChevronRight size={20} />
                </button>

                {/* Explicit Post Slide Button */}
                <button
                  className={`post-slide-btn ${currentSlideTimestamp ? "posted" : ""}`}
                  onClick={postCurrentSlide}
                  title={
                    currentSlideTimestamp
                      ? `Slide ${currentSlide} is posted @ ${formatTime(
                          Math.round(currentSlideTimestamp.audio_seconds)
                        )}. Click to update timestamp to ${formatTime(elapsedSec)}.`
                      : `Post Slide ${currentSlide} timestamp at ${formatTime(elapsedSec)} (Cmd+P)`
                  }
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
              <span className="notes-label">Lecture Notes</span>
              <button
                className="notes-ts-btn"
                onClick={insertTimestamp}
                title="Insert current time marker into notes (Cmd+T / Ctrl+T)"
              >
                <Tag size={13} />
                Insert {formatTime(elapsedSec)}
              </button>
            </div>
            <textarea
              ref={notesTextareaRef}
              className="notes-textarea"
              placeholder="Type lecture notes here... Use [Cmd+T] to insert timestamp or [Cmd+P] to post current slide."
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
            />
          </div>
        )}
      </div>

      {/* Confirmation Leave Modal */}
      {filingPrompt && (
        <FileToModuleModal
          noun="lecture"
          skipLabel="Save unfiled"
          onSelect={saveLecture}
        />
      )}

      {showLeaveModal && (
        <div className="modal-backdrop">
          <div className="modal-content">
            <div className="modal-header">
              <AlertTriangle size={24} className="text-warning" />
              <h3>Leave Recording Studio?</h3>
            </div>
            <p>
              {isRecording || isPaused
                ? "You have an active recording in progress. Leaving will stop and discard your current session."
                : "You have unsaved notes in this recording session."}
            </p>
            <div className="modal-actions">
              <button className="ghost" onClick={() => setShowLeaveModal(false)}>
                Continue Recording
              </button>
              <button className="record-big-btn recording" onClick={confirmLeave}>
                <RotateCcw size={16} /> Discard & Leave
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
