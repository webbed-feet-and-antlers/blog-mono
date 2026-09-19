import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { useInvalidateDrafts } from "@/api/hooks";

export interface DiffRequest {
  title: string;
  before: string;
  after: string;
  source: string; // version tag snapshotted on accept
}

interface EditorCtx {
  slug: string | null;
  markdown: string;
  dirty: boolean;
  savedAt: number | null;
  selection: string;
  diff: DiffRequest | null;
  versionsOpen: boolean;
  openDraft: (slug: string) => Promise<void>;
  setContent: (md: string) => void;
  save: (source?: string) => Promise<void>;
  proposeChange: (req: DiffRequest) => void;
  acceptChange: () => Promise<void>;
  rejectChange: () => void;
  setSelection: (s: string) => void;
  setVersionsOpen: (open: boolean) => void;
  newBlankDraft: () => void;
}

const Ctx = createContext<EditorCtx | null>(null);

const LAST_SLUG_KEY = "wa-last-slug";

export function EditorProvider({ children }: { children: ReactNode }) {
  const [slug, setSlug] = useState<string | null>(null);
  const [markdown, setMarkdownRaw] = useState("");
  const [savedContent, setSavedContent] = useState("");
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [selection, setSelection] = useState("");
  const [diff, setDiff] = useState<DiffRequest | null>(null);
  const [versionsOpen, setVersionsOpen] = useState(false);
  const invalidate = useInvalidateDrafts();
  const restoreTried = useRef(false);

  const dirty = markdown !== savedContent;

  const openDraft = useCallback(
    async (target: string) => {
      try {
        const res = await api<{ slug: string; markdown: string }>(
          `/api/drafts/${encodeURIComponent(target)}`,
        );
        setSlug(res.slug);
        setMarkdownRaw(res.markdown);
        setSavedContent(res.markdown);
        setSavedAt(Date.now());
        localStorage.setItem(LAST_SLUG_KEY, res.slug);
        invalidate();
      } catch (err) {
        toast.error(`Could not open ${target}: ${(err as Error).message}`);
      }
    },
    [invalidate],
  );

  // Restore the last-open draft on first mount.
  if (!restoreTried.current) {
    restoreTried.current = true;
    const last = localStorage.getItem(LAST_SLUG_KEY);
    if (last) void openDraft(last);
  }

  const setContent = useCallback((md: string) => setMarkdownRaw(md), []);

  const deriveSlug = useCallback((md: string, current: string | null) => {
    if (current) return current;
    const heading = md.split("\n").find((l) => l.startsWith("#"));
    const title = (heading ?? "untitled draft").replace(/^#+\s*/, "").trim();
    const s = title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
    return s || "draft";
  }, []);

  const save = useCallback(
    async (source = "human") => {
      const target = deriveSlug(markdown, slug);
      await api(`/api/drafts/${encodeURIComponent(target)}`, {
        method: "PUT",
        body: { markdown, source },
      });
      setSlug(target);
      setSavedContent(markdown);
      setSavedAt(Date.now());
      localStorage.setItem(LAST_SLUG_KEY, target);
      invalidate();
    },
    [deriveSlug, invalidate, markdown, slug],
  );

  const proposeChange = useCallback((req: DiffRequest) => setDiff(req), []);

  const acceptChange = useCallback(async () => {
    if (!diff) return;
    const md = diff.after;
    setDiff(null);
    setMarkdownRaw(md);
    try {
      const target = deriveSlug(md, slug);
      await api(`/api/drafts/${encodeURIComponent(target)}`, {
        method: "PUT",
        body: { markdown: md, source: diff.source },
      });
      setSlug(target);
      setSavedContent(md);
      setSavedAt(Date.now());
      localStorage.setItem(LAST_SLUG_KEY, target);
      invalidate();
      toast.success(`${diff.title} applied`);
    } catch (err) {
      toast.error(`Applied locally but save failed: ${(err as Error).message}`);
    }
  }, [deriveSlug, diff, invalidate, slug]);

  const rejectChange = useCallback(() => setDiff(null), []);

  const newBlankDraft = useCallback(() => {
    setSlug(null);
    setMarkdownRaw("");
    setSavedContent("");
    setSavedAt(null);
    localStorage.removeItem(LAST_SLUG_KEY);
  }, []);

  const value = useMemo<EditorCtx>(
    () => ({
      slug,
      markdown,
      dirty,
      savedAt,
      selection,
      diff,
      versionsOpen,
      openDraft,
      setContent,
      save,
      proposeChange,
      acceptChange,
      rejectChange,
      setSelection,
      setVersionsOpen,
      newBlankDraft,
    }),
    [
      slug, markdown, dirty, savedAt, selection, diff, versionsOpen,
      openDraft, setContent, save, proposeChange, acceptChange, rejectChange,
      newBlankDraft,
    ],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useEditor(): EditorCtx {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useEditor outside EditorProvider");
  return ctx;
}
