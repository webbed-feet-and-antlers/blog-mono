import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Reusable audio recording hook (MediaRecorder API).
 * Supports start, pause, resume, stop, real-time audio volume visualizer levels,
 * and audio input device enumeration.
 */
export function useRecorder() {
  const [isRecording, setIsRecording] = useState(false);
  const [isPaused, setIsPaused] = useState(false);
  const [elapsedSec, setElapsedSec] = useState(0);
  const [audioLevel, setAudioLevel] = useState(0); // 0 to 100
  const [availableDevices, setAvailableDevices] = useState<MediaDeviceInfo[]>([]);
  const [selectedDeviceId, setSelectedDeviceId] = useState<string>("");

  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const resolveRef = useRef<((blob: Blob) => void) | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const animFrameRef = useRef<number | null>(null);

  // Enumerate input devices
  const loadDevices = useCallback(async () => {
    if (!navigator.mediaDevices?.enumerateDevices) return;
    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      const audioInputs = devices.filter((d) => d.kind === "audioinput");
      setAvailableDevices(audioInputs);
      if (audioInputs.length > 0 && !selectedDeviceId) {
        setSelectedDeviceId(audioInputs[0].deviceId);
      }
    } catch (err) {
      console.warn("Failed to enumerate devices:", err);
    }
  }, [selectedDeviceId]);

  useEffect(() => {
    loadDevices();
  }, [loadDevices]);

  // Audio level analysis setup
  const setupAudioAnalysis = useCallback((stream: MediaStream) => {
    try {
      const AudioContextClass = window.AudioContext || (window as any).webkitAudioContext;
      if (!AudioContextClass) return;

      const audioCtx = new AudioContextClass();
      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 64;
      const source = audioCtx.createMediaStreamSource(stream);
      source.connect(analyser);

      audioCtxRef.current = audioCtx;
      analyserRef.current = analyser;

      const dataArray = new Uint8Array(analyser.frequencyBinCount);
      const updateLevel = () => {
        analyser.getByteFrequencyData(dataArray);
        let sum = 0;
        for (let i = 0; i < dataArray.length; i++) {
          sum += dataArray[i];
        }
        const avg = sum / dataArray.length;
        const normalized = Math.min(100, Math.round((avg / 255) * 100 * 2));
        setAudioLevel(normalized);
        animFrameRef.current = requestAnimationFrame(updateLevel);
      };
      updateLevel();
    } catch (err) {
      console.warn("Audio level analysis init failed:", err);
    }
  }, []);

  const cleanupAudioAnalysis = useCallback(() => {
    if (animFrameRef.current) {
      cancelAnimationFrame(animFrameRef.current);
      animFrameRef.current = null;
    }
    if (audioCtxRef.current && audioCtxRef.current.state !== "closed") {
      audioCtxRef.current.close().catch(() => {});
    }
    audioCtxRef.current = null;
    analyserRef.current = null;
    setAudioLevel(0);
  }, []);

  const start = useCallback(async (deviceIdOverride?: string): Promise<Blob> => {
    const targetDeviceId = deviceIdOverride || selectedDeviceId;
    const constraints: MediaStreamConstraints = {
      audio: targetDeviceId ? { deviceId: { exact: targetDeviceId } } : true,
    };

    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia(constraints);
    } catch (err) {
      // Fallback to basic audio constraints if exact device fails
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    }

    // Refresh device list after permission is granted
    loadDevices();

    const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
      ? "audio/webm;codecs=opus"
      : MediaRecorder.isTypeSupported("audio/webm")
      ? "audio/webm"
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

    setupAudioAnalysis(stream);

    recorder.start();
    setIsRecording(true);
    setIsPaused(false);
    setElapsedSec(0);

    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = setInterval(() => {
      setElapsedSec((t) => t + 1);
    }, 1000);

    return blobPromise;
  }, [selectedDeviceId, loadDevices, setupAudioAnalysis]);

  const pause = useCallback(() => {
    if (recorderRef.current && isRecording && recorderRef.current.state === "recording") {
      recorderRef.current.pause();
      setIsPaused(true);
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
    }
  }, [isRecording]);

  const resume = useCallback(() => {
    if (recorderRef.current && isRecording && recorderRef.current.state === "paused") {
      recorderRef.current.resume();
      setIsPaused(false);
      timerRef.current = setInterval(() => {
        setElapsedSec((t) => t + 1);
      }, 1000);
    }
  }, [isRecording]);

  const stop = useCallback(() => {
    if (recorderRef.current && (isRecording || isPaused)) {
      if (recorderRef.current.state !== "inactive") {
        recorderRef.current.stop();
      }
      setIsRecording(false);
      setIsPaused(false);
      if (timerRef.current) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
      cleanupAudioAnalysis();
    }
  }, [isRecording, isPaused, cleanupAudioAnalysis]);

  // Cleanup on unmount.
  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
      cleanupAudioAnalysis();
    };
  }, [cleanupAudioAnalysis]);

  return {
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
  };
}

export function formatTime(secs: number): string {
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = secs % 60;
  if (h > 0) {
    return `${h}:${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
  }
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export function blobToFile(blob: Blob): File {
  const ext = blob.type.includes("webm") ? "webm" : "m4a";
  return new File([blob], `recording-${Date.now()}.${ext}`, { type: blob.type });
}
