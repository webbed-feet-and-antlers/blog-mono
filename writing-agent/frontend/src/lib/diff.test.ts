import { describe, expect, it } from "vitest";
import { collapsedRows, lineDiff, wordDiff } from "./diff";

describe("wordDiff", () => {
  it("marks changed words and keeps shared text", () => {
    const parts = wordDiff("the cat sat", "the dog sat");
    expect(parts.some((p) => p.type === "del" && p.text.includes("cat"))).toBe(true);
    expect(parts.some((p) => p.type === "add" && p.text.includes("dog"))).toBe(true);
    expect(parts.some((p) => p.type === "same" && p.text.includes("the"))).toBe(true);
  });

  it("returns a single add for empty before", () => {
    const parts = wordDiff("", "new text");
    expect(parts).toEqual([{ type: "add", text: "new text" }]);
  });
});

describe("lineDiff", () => {
  it("pairs adjacent del/add lines into mod rows with word detail", () => {
    const rows = lineDiff("one\ntwo\nthree", "one\ntWO\nthree");
    expect(rows.some((r) => r.type === "mod")).toBe(true);
    const mod = rows.find((r) => r.type === "mod");
    if (mod?.type === "mod") {
      expect(mod.a).toBe("two");
      expect(mod.b).toBe("tWO");
    }
  });

  it("keeps pure insertions and deletions", () => {
    const rows = lineDiff("a\nb", "a\nb\nc");
    expect(rows.some((r) => r.type === "add" && r.text === "c")).toBe(true);
    const del = lineDiff("a\nb\nc", "a\nb");
    expect(del.some((r) => r.type === "del" && r.text === "c")).toBe(true);
  });
});

describe("collapsedRows", () => {
  it("collapses long unchanged runs but keeps context around changes", () => {
    const lines = Array.from({ length: 20 }, (_, i) => `line ${i}`);
    const rows = lineDiff(lines.join("\n"), lines.with(10, "CHANGED").join("\n"));
    const collapsed = collapsedRows(rows, 2);
    const gap = collapsed.find((r) => r.type === "gap");
    expect(gap).toBeTruthy();
    if (gap?.type === "gap") expect(gap.count).toBeGreaterThan(5);
    // context rows around the change survive
    expect(collapsed.some((r) => r.type === "same" && r.text === "line 8")).toBe(true);
    expect(collapsed.some((r) => r.type === "mod")).toBe(true);
  });
});
