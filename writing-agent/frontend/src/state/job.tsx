import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type { ChatEntry, JobEvent } from "@/api/types";

interface JobCtx {
  jobId: string | null;
  topic: string;
  stage: string | null;
  events: JobEvent[];
  running: boolean;
  doneEvent: JobEvent | null;
  chat: ChatEntry[];
  startJob: (jobId: string, topic: string) => void;
  pushEvent: (e: JobEvent) => void;
  finishJob: (e: JobEvent) => void;
  addChat: (role: ChatEntry["role"], text: string) => void;
}

const Ctx = createContext<JobCtx | null>(null);

export function JobProvider({ children }: { children: ReactNode }) {
  const [jobId, setJobId] = useState<string | null>(null);
  const [topic, setTopic] = useState("");
  const [stage, setStage] = useState<string | null>(null);
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [running, setRunning] = useState(false);
  const [doneEvent, setDoneEvent] = useState<JobEvent | null>(null);
  const [chat, setChat] = useState<ChatEntry[]>([]);

  const startJob = useCallback((id: string, t: string) => {
    setJobId(id);
    setTopic(t);
    setStage(null);
    setEvents([]);
    setDoneEvent(null);
    setRunning(true);
  }, []);

  const pushEvent = useCallback((e: JobEvent) => {
    setStage(e.stage);
    setEvents((prev) => [...prev, e]);
  }, []);

  const finishJob = useCallback((e: JobEvent) => {
    setStage(e.stage);
    setEvents((prev) => [...prev, e]);
    setDoneEvent(e);
    setRunning(false);
  }, []);

  const addChat = useCallback((role: ChatEntry["role"], text: string) => {
    setChat((prev) => [...prev, { role, text, at: Date.now() }]);
  }, []);

  const value = useMemo<JobCtx>(
    () => ({
      jobId, topic, stage, events, running, doneEvent, chat,
      startJob, pushEvent, finishJob, addChat,
    }),
    [jobId, topic, stage, events, running, doneEvent, chat, startJob, pushEvent, finishJob, addChat],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useJob(): JobCtx {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useJob outside JobProvider");
  return ctx;
}
