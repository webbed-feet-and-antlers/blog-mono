import { useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";
import { ZoomIn, ZoomOut, Loader2, AlertCircle } from "lucide-react";

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
}

const MIN_SCALE = 0.5;
const MAX_SCALE = 3;
const SCALE_STEP = 0.2;

/**
 * In-app PDF viewer built on react-pdf (PDF.js). Renders every page in one
 * scrollable column with a zoom control — no browser PDF chrome, styled to
 * match the rest of the app.
 */
export function PdfViewer({ url, filename }: Props) {
  const [numPages, setNumPages] = useState<number>(0);
  const [scale, setScale] = useState<number>(1.0);
  const [error, setError] = useState<string | null>(null);

  function onDocumentLoadSuccess({ numPages }: { numPages: number }) {
    setNumPages(numPages);
    setError(null);
  }

  function onDocumentLoadError(err: Error) {
    setError(err.message || "Failed to load PDF");
  }

  function zoomIn() {
    setScale((s) => Math.min(MAX_SCALE, +(s + SCALE_STEP).toFixed(2)));
  }
  function zoomOut() {
    setScale((s) => Math.max(MIN_SCALE, +(s - SCALE_STEP).toFixed(2)));
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

      <div className="pdf-canvas">
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
                  scale={scale}
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
