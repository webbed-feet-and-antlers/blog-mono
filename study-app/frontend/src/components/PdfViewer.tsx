import { useEffect, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";
import { ZoomIn, ZoomOut, Loader2, AlertCircle } from "lucide-react";
import { track } from "../api/track";

// Configure the PDF.js worker. With Vite, importing the worker entry as a
// URL string lets the bundler emit it as an asset and gives us a stable
// src in both dev and production builds.
// biome-ignore lint/style/noNonNullAssertion: bundled worker asset
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

interface Props {
  url: string;
  filename: string;
  docId?: string;
}

const MIN_SCALE = 0.5;
const MAX_SCALE = 3;
const SCALE_STEP = 0.2;

/**
 * In-app PDF viewer built on react-pdf (PDF.js). Renders every page in one
 * scrollable column with a zoom control — no browser PDF chrome, styled to
 * match the rest of the app.
 */
export function PdfViewer({ url, filename, docId }: Props) {
  const [numPages, setNumPages] = useState<number>(0);
  const [scale, setScale] = useState<number>(1.0);
  const [error, setError] = useState<string | null>(null);
  // Rate-limit zoom telemetry: at most one event per 2s.
  const lastZoomTrackRef = useRef(0);
  // Track the canvas width so pages can fit-to-width at zoom 1x. The zoom
  // buttons then act as a multiplier over that fitted width.
  const canvasRef = useRef<HTMLDivElement>(null);
  const [canvasWidth, setCanvasWidth] = useState<number>(0);

  useEffect(() => {
    const el = canvasRef.current;
    if (!el) return;
    const measure = () => setCanvasWidth(el.clientWidth);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  function onDocumentLoadSuccess({ numPages }: { numPages: number }) {
    setNumPages(numPages);
    setError(null);
  }

  function onDocumentLoadError(err: Error) {
    setError(err.message || "Failed to load PDF");
  }

  function trackZoom(nextScale: number) {
    const now = Date.now();
    if (now - lastZoomTrackRef.current < 2000) return;
    lastZoomTrackRef.current = now;
    track("zoom.changed", { document_id: docId ?? null, scale: nextScale });
  }

  function zoomIn() {
    setScale((s) => {
      const next = Math.min(MAX_SCALE, +(s + SCALE_STEP).toFixed(2));
      trackZoom(next);
      return next;
    });
  }
  function zoomOut() {
    setScale((s) => {
      const next = Math.max(MIN_SCALE, +(s - SCALE_STEP).toFixed(2));
      trackZoom(next);
      return next;
    });
  }

  return (
    <div className="pdf-viewer">
      <div className="pdf-toolbar">
        <button
          type="button"
          className="pdf-icon-btn"
          onClick={zoomOut}
          disabled={scale <= MIN_SCALE}
          aria-label="Zoom out"
        >
          <ZoomOut size={16} />
        </button>
        <span className="pdf-zoom-level">{Math.round(scale * 100)}%</span>
        <button
          type="button"
          className="pdf-icon-btn"
          onClick={zoomIn}
          disabled={scale >= MAX_SCALE}
          aria-label="Zoom in"
        >
          <ZoomIn size={16} />
        </button>

        <span className="pdf-filename" title={filename}>
          {filename}
        </span>
      </div>

      <div className="pdf-canvas" ref={canvasRef}>
        <Document
          file={url}
          onLoadSuccess={onDocumentLoadSuccess}
          onLoadError={onDocumentLoadError}
          loading={
            <div className="pdf-loading">
              <Loader2 size={20} className="spinner" />
              Loading document…
            </div>
          }
        >
          {error ? (
            <div className="error pdf-error">
              <AlertCircle size={16} />
              Could not display this PDF: {error}
            </div>
          ) : (
            <div className="pdf-pages">
              {Array.from({ length: numPages }, (_, i) => (
                <Page
                  key={`page_${i + 1}`}
                  pageNumber={i + 1}
                  /* Fit to the available width at 1x; the zoom multiplier
                     scales on top. Falls back to react-pdf's default when
                     the container hasn't been measured yet. */
                  width={
                    canvasWidth > 0
                      ? Math.max(240, (canvasWidth - 48) * scale)
                      : undefined
                  }
                  renderTextLayer
                  renderAnnotationLayer
                />
              ))}
            </div>
          )}
        </Document>
      </div>
    </div>
  );
}
