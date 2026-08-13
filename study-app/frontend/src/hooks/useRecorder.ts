import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Reusable audio recording hook (MediaRecorder API).
 *
 * Extracted from the Sidebar recording logic. Exposes a clean interface
 * for both the quick-record button (Sidebar) and the dedicated recording
 * page (RecordPage).
 */
export function useRecorder() {
  const [isRecording, setIsRecording] = useState(false);
  const [elapsedSec, setElapsedSec] = useState(0);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const resolveRef = useRef<((blob: Blob) => void) | null>(null);

  const start = useCallback(async (): Promise<Blob> => {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
      ? "audio/webm;codecs=opus"
      : "audio/mp4";
    const recorder = new MediaRecorder(stream, { mimeType });
    recorderRef.current = recorder;
    chunksRef.current = [];

    recorder.ondataavailable = (e) => {
      if (e.data.size > 0) chunksRef.current.push(e.data);
    };

    const blobPromise = new Promise<Blob>((resolve) => {
      resolveRef.current = resolve;
      recorder.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(chunksRef.current, { type: mimeType });
        resolve(blob);
      };
    });

    recorder.start();
    setIsRecording(true);
    setElapsedSec(0);
    timerRef.current = setInterval(() => {
      setElapsedSec((t) => t + 1);
    }, 1000);

    return blobPromise;
  }, []);

  const stop = useCallback(() => {
    if (recorderRef.current && isRecording) {
      recorderRef.current.stop();
      setIsRecording(false);
      if (timerRef.current) clearInterval(timerRef.current);
    }
  }, [isRecording]);

  // Cleanup on unmount.
  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, []);

  return { isRecording, elapsedSec, start, stop };
}

export function formatTime(secs: number): string {
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export function blobToFile(blob: Blob): File {
  const ext = blob.type.includes("webm") ? "webm" : "m4a";
  return new File([blob], `recording-${Date.now()}.${ext}`, { type: blob.type });
}
