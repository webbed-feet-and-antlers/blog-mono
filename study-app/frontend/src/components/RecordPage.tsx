import { useRef, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { Mic, Square, ArrowLeft, FileText, ChevronLeft, ChevronRight, UploadCloud, Loader2 } from "lucide-react";
import { useRecorder, formatTime, blobToFile } from "../hooks/useRecorder";
import * as api from "../api/client";

interface SlideTimestampState {
  slide_number: number;
  audio_seconds: number;
}

export function RecordPage() {
  const navigate = useNavigate();
  const { isRecording, elapsedSec, start, stop } = useRecorder();
  const slidesInputRef = useRef<HTMLInputElement>(null);

  const [title, setTitle] = useState("");
  const [notes, setNotes] = useState("");
  const [slideCount, setSlideCount] = useState(0);
  const [currentSlide, setCurrentSlide] = useState(1);
  const [slideTimestamps, setSlideTimestamps] = useState<SlideTimestampState[]>([]);
  const [slidesUploaded, setSlidesUploaded] = useState(false);
  const [uploadingSlides, setUploadingSlides] = useState(false);
  const [saving, setSaving] = useState(false);
  const slidesDocIdRef = useRef<string | null>(null);
  const startPromiseRef = useRef<Promise<Blob> | null>(null);

  async function handleSlidesUpload(files: FileList | null) {
    if (!files || files.length === 0) return;
    setUploadingSlides(true);
    try {
      const doc = await api.uploadDocument(files[0]);
      slidesDocIdRef.current = doc.id;
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
      return [...filtered, { slide_number: slideNum, audio_seconds: elapsedSec }];
    });
  }

  function nextSlide() {
    if (currentSlide < slideCount) {
      const next = currentSlide + 1;
      setCurrentSlide(next);
      recordSlideTimestamp(next);
    }
  }

  function prevSlide() {
    if (currentSlide > 1) {
      setCurrentSlide(currentSlide - 1);
    }
  }

  async function handleStartRecording() {
    startPromiseRef.current = start();
    // Record the first slide timestamp.
    recordSlideTimestamp(1);
  }

  async function handleStopAndSave() {
    if (!startPromiseRef.current) return;
    setSaving(true);

    stop();
    const audioBlob = await startPromiseRef.current;

    // Upload audio.
    const audioFile = blobToFile(audioBlob);
    const audioDoc = await api.uploadDocument(audioFile);

    // Create the lecture session.
    const sessionTitle = title.trim() || `Lecture ${new Date().toLocaleDateString()}`;
    const session = await api.createLecture({
      title: sessionTitle,
      audio_doc_id: audioDoc.id,
      slides_doc_id: slidesDocIdRef.current ?? undefined,
      notes,
      duration_seconds: elapsedSec,
      slide_timestamps: slideTimestamps,
      slide_count: slideCount,
    });

    setSaving(false);
    navigate({ to: "/lecture/$lectureId", params: { lectureId: session.id } });
  }

  return (
    <div className="record-page">
      <div className="record-header">
        <button
          className="ghost icon-btn"
          onClick={() => navigate({ to: "/" })}
          title="Back"
        >
          <ArrowLeft size={20} />
        </button>
        <input
          className="record-title-input"
          placeholder="Lecture title…"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
      </div>

      <div className="record-timer">
        {isRecording && <div className="recording-pulse" />}
        <span className={`timer-text ${isRecording ? "recording" : ""}`}>
          {formatTime(elapsedSec)}
        </span>
      </div>

      <div className="record-controls">
        {!isRecording ? (
          <button className="record-big-btn" onClick={handleStartRecording}>
            <Mic size={28} />
            Start recording
          </button>
        ) : (
          <button className="record-big-btn recording" onClick={handleStopAndSave} disabled={saving}>
            {saving ? (
              <>
                <Loader2 size={24} className="spinner" />
                Saving…
              </>
            ) : (
              <>
                <Square size={24} />
                Stop & save
              </>
            )}
          </button>
        )}
      </div>

      <div className="record-body">
        {/* Slides section */}
        <div className="record-slides">
          {!slidesUploaded ? (
            <div
              className="slide-upload-zone"
              onClick={() => slidesInputRef.current?.click()}
            >
              <input
                ref={slidesInputRef}
                type="file"
                accept=".pdf"
                style={{ display: "none" }}
                onChange={(e) => handleSlidesUpload(e.target.files)}
              />
              {uploadingSlides ? (
                <>
                  <Loader2 size={24} className="spinner" />
                  Uploading slides…
                </>
              ) : (
                <>
                  <UploadCloud size={28} />
                  <span>Upload slides (PDF)</span>
                  <span style={{ fontSize: "0.72rem", color: "var(--text-faint)" }}>
                    Slides will be displayed during recording
                  </span>
                </>
              )}
            </div>
          ) : (
            <div className="slide-display">
              <div className="slide-image-container">
                <img
                  src={`/api/lectures/_placeholder/slides/${currentSlide}`}
                  alt={`Slide ${currentSlide}`}
                  className="slide-image"
                  onError={(e) => {
                    // Placeholder endpoint doesn't exist yet — hide broken image.
                    (e.target as HTMLImageElement).style.display = "none";
                  }}
                />
                <div className="slide-placeholder">
                  <FileText size={48} strokeWidth={1} />
                  <span>Slide {currentSlide} of {slideCount}</span>
                </div>
              </div>
              <div className="slide-nav">
                <button
                  className="ghost icon-btn"
                  onClick={prevSlide}
                  disabled={currentSlide <= 1}
                >
                  <ChevronLeft size={20} />
                </button>
                <span className="slide-counter">{currentSlide} / {slideCount}</span>
                <button
                  className="ghost icon-btn"
                  onClick={nextSlide}
                  disabled={currentSlide >= slideCount}
                >
                  <ChevronRight size={20} />
                </button>
              </div>
              {slideTimestamps.length > 0 && (
                <div className="slide-timestamps">
                  {slideTimestamps.slice(-3).map((t, i) => (
                    <span key={i} className="slide-ts-badge">
                      Slide {t.slide_number} @ {formatTime(Math.round(t.audio_seconds))}
                    </span>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Notes section */}
        <div className="record-notes">
          <div className="notes-label">Notes</div>
          <textarea
            className="notes-textarea"
            placeholder="Write notes during the lecture…"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
          />
        </div>
      </div>
    </div>
  );
}
