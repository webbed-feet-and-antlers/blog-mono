import { useEffect, useRef } from "react";
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { markdown } from "@codemirror/lang-markdown";
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { EditorState } from "@codemirror/state";
import { EditorView, keymap } from "@codemirror/view";
import { tags as t } from "@lezer/highlight";

// All colors ride on CSS variables, so the light/dark switch needs no
// editor reconfiguration.
const highlight = HighlightStyle.define([
  { tag: t.heading, color: "var(--accent)", fontWeight: "600" },
  { tag: t.link, color: "var(--accent)", textDecoration: "underline" },
  { tag: t.url, color: "var(--muted)" },
  { tag: t.emphasis, fontStyle: "italic" },
  { tag: t.strong, fontWeight: "700" },
  { tag: t.strikethrough, textDecoration: "line-through", color: "var(--muted)" },
  { tag: t.monospace, color: "var(--warn)" },
  { tag: t.quote, color: "var(--muted)" },
  { tag: t.processingInstruction, color: "var(--muted)" },
]);

interface Props {
  value: string;
  onChange: (doc: string) => void;
  onSelection?: (text: string) => void;
}

export function CodeMirror({ value, onChange, onSelection }: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const changeRef = useRef(onChange);
  const selectRef = useRef(onSelection);
  changeRef.current = onChange;
  selectRef.current = onSelection;

  useEffect(() => {
    const view = new EditorView({
      state: EditorState.create({
        doc: value,
        extensions: [
          history(),
          keymap.of([...defaultKeymap, ...historyKeymap, indentWithTab]),
          markdown(),
          syntaxHighlighting(highlight),
          EditorView.lineWrapping,
          EditorView.theme({
            "&": { color: "var(--text)", backgroundColor: "transparent" },
            ".cm-content": { caretColor: "var(--accent)" },
            ".cm-cursor, .cm-dropCursor": { borderLeftColor: "var(--accent)" },
            "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection": {
              backgroundColor: "var(--accent-dim)",
            },
            ".cm-gutters": {
              backgroundColor: "transparent",
              color: "var(--muted)",
              border: "none",
            },
            ".cm-activeLine": { backgroundColor: "color-mix(in srgb, var(--panel) 70%, transparent)" },
          }),
          EditorView.updateListener.of((u) => {
            if (u.docChanged) changeRef.current(u.state.doc.toString());
            if (u.selectionSet) {
              const sel = u.state.selection.main;
              selectRef.current?.(u.state.sliceDoc(sel.from, sel.to));
            }
          }),
        ],
      }),
      parent: hostRef.current!,
    });
    viewRef.current = view;
    return () => {
      view.destroy();
      viewRef.current = null;
    };
    // Mount once — value flows through the dispatch effect below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const view = viewRef.current;
    if (view && value !== view.state.doc.toString()) {
      view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: value } });
    }
  }, [value]);

  return <div ref={hostRef} className="h-full overflow-hidden" />;
}
