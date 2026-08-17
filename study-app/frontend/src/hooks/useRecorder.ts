import { useCallback, useEffect, useRef, useState } from "react";

/** Where a recording's audio comes from. */
export type AudioSource = "mic" | "mic+screen" | "screen";

export interface StartOptions {
  source?: AudioSource;
  deviceIdOverride?: string;
}

/**
 * Reusable audio recording hook (MediaRecorder API).
 * Supports start, pause, resume, stop, real-time audio volume visualizer levels,
 * and audio input device enumeration.
 *
 * `start({ source })` picks the audio source: microphone only (default),
 * the audio of a shared tab/screen (`getDisplayMedia`), or both mixed through
 * a Web Audio graph — the recorder always emits a single audio track.
 */
export function useRecorder() {
  const [isRecording, setIsRecording] = useState(false);
  const [isPaused, setIsPaused] = useState(false);
  const [elapsedSec, setElapsedSec] = useState(0);
  const [audioLevel, setAudioLevel] = useState(0); // 0 to 100
  const [availableDevices, setAvailableDevices] = useState<MediaDeviceInfo[]>([]);
  const [selectedDeviceId, setSelectedDeviceId] = useState<string>("");
  const [screenAudioActive, setScreenAudioActive] = useState(false);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const resolveRef = useRef<((blob: Blob) => void) | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const animFrameRef = useRef<number | null>(null);
  const micStreamRef = useRef<MediaStream | null>(null);
  const displayStreamRef = useRef<MediaStream | null>(null);
  const mixCtxRef = useRef<AudioContext | null>(null);
  // The browser's own "Stop sharing" bar ends the session like Stop & save;
  // the track's onended handler calls stop via this ref (stop is defined
  // after start, so a direct reference isn't in scope there).
  const stopRef = useRef<() => void>(() => {});

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

  // Stop every source stream and close the mixing graph. Idempotent.
  const teardownSources = useCallback(() => {
    micStreamRef.current?.getTracks().forEach((t) => t.stop());
    displayStreamRef.current?.getTracks().forEach((t) => {
      t.onended = null;
      t.stop();
    });
    micStreamRef.current = null;
    displayStreamRef.current = null;
    if (mixCtxRef.current && mixCtxRef.current.state !== "closed") {
      mixCtxRef.current.close().catch(() => {});
    }
    mixCtxRef.current = null;
    setScreenAudioActive(false);
  }, []);

  const start = useCallback(async (opts?: StartOptions): Promise<Blob> => {
    const source = opts?.source ?? "mic";
    const wantScreen = source !== "mic";
    const wantMic = source !== "screen";

    // Screen/tab audio first — the picker is the step most likely to be
    // cancelled, and cancelling shouldn't have grabbed the mic yet.
    let displayStream: MediaStream | null = null;
    if (wantScreen) {
      if (!navigator.mediaDevices?.getDisplayMedia) {
        throw new Error(
          "Screen audio isn't supported by this browser — use Chrome or Edge, or record with the microphone."
        );
      }
      try {
        // video is required to open the picker; only the audio track is kept
        displayStream = await navigator.mediaDevices.getDisplayMedia({
          video: true,
          audio: true,
        });
      } catch {
        throw new Error(
          "Screen audio cancelled — nothing was recorded. Try again and pick the tab or screen playing the lecture."
        );
      }
      if (displayStream.getAudioTracks().length === 0) {
        displayStream.getTracks().forEach((t) => t.stop());
        throw new Error(
          "No audio was shared. Pick the tab playing the lecture (or, on Windows, an entire screen) and tick “Share tab audio” in the picker."
        );
      }
      displayStream.getVideoTracks().forEach((t) => t.stop());
    }

    let micStream: MediaStream | null = null;
    if (wantMic) {
      const targetDeviceId = opts?.deviceIdOverride || selectedDeviceId;
      try {
        micStream = await navigator.mediaDevices.getUserMedia({
          audio: targetDeviceId ? { deviceId: { exact: targetDeviceId } } : true,
        });
      } catch {
        try {
          // Fallback to basic audio constraints if exact device fails
          micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        } catch (err) {
          displayStream?.getTracks().forEach((t) => t.stop());
          throw err;
        }
      }
    }

    // One recorded stream: both sources mixed through a Web Audio graph, or
    // the single source directly (the default mic path is unchanged).
    let stream: MediaStream;
    if (micStream && displayStream) {
      const AudioContextClass =
        window.AudioContext || (window as any).webkitAudioContext;
      const mixCtx = new AudioContextClass();
      const dest = mixCtx.createMediaStreamDestination();
      mixCtx.createMediaStreamSource(micStream).connect(dest);
      mixCtx.createMediaStreamSource(displayStream).connect(dest);
      stream = dest.stream;
      mixCtxRef.current = mixCtx;
    } else {
      stream = (micStream ?? displayStream)!;
    }
    micStreamRef.current = micStream;
    displayStreamRef.current = displayStream;

    if (displayStream) {
      setScreenAudioActive(true);
      displayStream.getAudioTracks().forEach((t) => {
        t.onended = () => stopRef.current();
      });
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
        teardownSources();
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
  }, [selectedDeviceId, loadDevices, setupAudioAnalysis, teardownSources]);

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
      teardownSources();
    };
  }, [cleanupAudioAnalysis, teardownSources]);

  useEffect(() => {
    stopRef.current = stop;
  }, [stop]);

  return {
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
