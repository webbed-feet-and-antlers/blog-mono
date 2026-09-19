/* writing-agent UI — the editor content is the shared artifact.
 * Every agent action posts the CURRENT markdown and applies the returned
 * markdown; AI edits push onto a history stack so they can be reverted. */

const $ = (sel) => document.querySelector(sel);

let currentSlug = null;
let history = []; // snapshots before each AI edit

const editor = new EasyMDE({
  element: $("#editor"),
  spellChecker: false,
  sideBySideFullscreen: false,
  status: ["lines", "words"],
  toolbar: [
    "bold", "italic", "heading", "|",
    "quote", "unordered-list", "ordered-list", "|",
    "code", "link", "|",
    "preview", "side-by-side", "|",
    {
      name: "fix-selection",
      className: "fa fa-wrench",
      title: "Ask the co-editor to revise the selected text",
      action: (cm) => {
        const sel = cm.getSelection();
        if (sel && sel.trim().length > 3) {
          $("#instruction").focus();
          chatNote("hint", `Will revise the selected passage (${sel.trim().split(/\s+/).length} words) when you send an instruction.`);
        } else {
          chatNote("hint", "Select some text in the editor first.");
        }
      },
    },
  ],
});

// ---------- helpers ----------

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch {}
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json();
}

function toast(msg, kind = "info") {
  $("#save-state").textContent = msg;
  $("#save-state").dataset.kind = kind;
  setTimeout(() => { if ($("#save-state").textContent === msg) $("#save-state").textContent = ""; }, 4000);
}

function setBusy(busy, label) {
  $("#agent-status").hidden = !busy;
  if (busy) $("#agent-stage").textContent = label || "Working…";
}

function chatNote(kind, text) {
  const p = document.createElement("p");
  p.className = kind; // "hint" | "you" | "agent" | "error"
  p.textContent = text;
  const log = $("#chat-log");
  log.appendChild(p);
  log.scrollTop = log.scrollHeight;
}

function updateHistoryUi() {
  $("#btn-revert").hidden = history.length === 0;
  $("#btn-revert").textContent = `↩ Revert AI edit (${history.length})`;
}

function pushHistory() {
  history.push(editor.value());
  if (history.length > 50) history.shift();
  updateHistoryUi();
}

function applyAgentMarkdown(md) {
  pushHistory();
  editor.value(md);
}

// ---------- drafts ----------

async function loadDrafts() {
  const drafts = await api("/api/drafts");
  const ul = $("#draft-list");
  ul.innerHTML = "";
  for (const d of drafts) {
    const li = document.createElement("li");
    const a = document.createElement("a");
    a.href = "#";
    a.textContent = d.slug;
    if (d.slug === currentSlug) li.classList.add("active");
    a.onclick = (e) => { e.preventDefault(); openDraft(d.slug); };
    li.appendChild(a);
    ul.appendChild(li);
  }
}

async function openDraft(slug) {
  const { markdown } = await api(`/api/drafts/${encodeURIComponent(slug)}`);
  currentSlug = slug;
  history = [];
  updateHistoryUi();
  editor.value(markdown);
  loadDrafts();
}

async function saveDraft() {
  if (!currentSlug) {
    const titleLine = (editor.value().split("\n").find((l) => l.startsWith("#")) || "untitled draft")
      .replace(/^#+\s*/, "").trim();
    currentSlug = titleLine.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "draft";
  }
  await api(`/api/drafts/${encodeURIComponent(currentSlug)}`, { method: "PUT", body: { markdown: editor.value() } });
  toast("saved", "ok");
  loadDrafts();
}

// ---------- agent: full pipeline ----------

async function runAgent(form) {
  const fd = new FormData(form);
  const topic = fd.get("topic").trim();
  const research = fd.get("research");
  const persona = fd.get("persona");
  if (!topic) return;
  form.hidden = true;
  setBusy(true, "Starting…");
  try {
    const { job_id } = await api("/api/drafts", { method: "POST", body: { topic, research, persona } });
    await pollJob(job_id, topic);
  } catch (err) {
    setBusy(false);
    toast(err.message, "error");
  }
}

async function pollJob(jobId, topic) {
  const timer = setInterval(async () => {
    try {
      const job = await api(`/api/jobs/${jobId}`);
      const secs = job.elapsed != null ? ` — ${Math.round(job.elapsed)}s` : "";
      setBusy(true, job.stage + secs);
      if (!job.done) return;
      clearInterval(timer);
      setBusy(false);
      if (job.error) {
        chatNote("error", `Agent failed after ${Math.round(job.elapsed)}s: ${job.error}`);
        (job.trace || []).slice(-3).forEach((e) => chatNote("hint", `${e.t}s  ${e.event}`));
        toast(job.error, "error");
        $("#new-draft").hidden = false;
        return;
      }
      currentSlug = job.result.slug;
      history = [];
      updateHistoryUi();
      editor.value(await (await api(`/api/drafts/${job.result.slug}`)).markdown);
      $("#scorecard").textContent = job.result.scorecard || "";
      chatNote("agent", `Drafted “${topic}” in ${Math.round(job.elapsed)}s. Gates output in the panel — your turn: edit freely, or send me instructions.`);
      loadDrafts();
    } catch (err) {
      clearInterval(timer);
      setBusy(false);
      toast(err.message, "error");
    }
  }, 1200);
}

// ---------- gates ----------

async function runLint() {
  setBusy(true, "Linting…");
  try {
    const out = await api("/api/lint", { method: "POST", body: { markdown: editor.value() } });
    $("#scorecard").textContent = out.scorecard;
    renderFlagged(out.report);
  } catch (err) { toast(err.message, "error"); }
  setBusy(false);
}

function renderFlagged(report) {
  const box = $("#flagged-list");
  box.innerHTML = "";
  if (!report.failures.length) {
    const p = document.createElement("p");
    p.className = "ok";
    p.textContent = report.passed ? "All gates pass." : "";
    box.appendChild(p);
    return;
  }
  for (const f of report.failures) {
    const div = document.createElement("div");
    div.className = "flagged";
    const head = document.createElement("strong");
    head.textContent = `block ${f.block_index}`;
    div.appendChild(head);
    const ul = document.createElement("ul");
    for (const r of f.reasons) {
      const li = document.createElement("li");
      li.textContent = r;
      ul.appendChild(li);
    }
    div.appendChild(ul);
    const btn = document.createElement("button");
    btn.textContent = "Fix this block";
    btn.onclick = () => fixBlocks([f.block_index]);
    div.appendChild(btn);
    box.appendChild(div);
  }
}

async function runShape() {
  setBusy(true, "Judging discourse shape…");
  try {
    const out = await api("/api/shape", { method: "POST", body: { markdown: editor.value() } });
    const m = out.metrics;
    const lines = [
      "DISCOURSE SHAPE (StoryScope)",
      `temporal+retro/1k: ${(m.temporal_jumps_per_1k + m.retro_explanations_per_1k).toFixed(1)}   unresolved: ${m.unresolved_markers}   summary markers: ${Object.keys(m.summary_hit_blocks || {}).length}`,
      `incident anchors/1k: ${m.incident_anchors_per_1k.toFixed(1)}   reader address/1k: ${m.reader_address_per_1k.toFixed(1)}   fragments/1k: ${m.fragments_per_1k.toFixed(1)}`,
      `top-5 content share: ${(m.top5_content_share * 100).toFixed(0)}%   section length SD: ${m.section_length_sd.toFixed(0)}`,
    ];
    if (out.judge) {
      const axes = Object.entries(out.judge)
        .filter(([k]) => k !== "notes")
        .map(([k, v]) => `${k} ${v.toFixed(2)}${v >= 0.7 ? " ⚑" : ""}`);
      lines.push(`judge: ${axes.join("   ")}`);
      if (out.judge.notes) lines.push(`judge notes: ${out.judge.notes}`);
    } else {
      lines.push("judge: skipped");
    }
    for (const f of out.failures) {
      lines.push(`FLAG block ${f.block_index}: ${f.reason}`);
    }
    lines.push(out.passed ? "RESULT: PASS" : "RESULT: FAIL");
    $("#scorecard").textContent = lines.join("\n");
    renderFlagged({
      failures: out.failures
        .filter((f) => f.block_index !== null)
        .map((f) => ({ block_index: f.block_index, reasons: [f.reason] })),
      passed: out.passed,
    });
  } catch (err) { toast(err.message, "error"); }
  setBusy(false);
}

async function runScore() {
  setBusy(true, "Scoring surprisal…");
  try {
    const out = await api("/api/score", { method: "POST", body: { markdown: editor.value() } });
    if (out.skipped) {
      $("#scorecard").textContent = out.warning;
    } else {
      const lines = out.blocks.map((b) =>
        `block ${b.block_index}: overlap ${(b.overlap * 100).toFixed(0)}%` +
        (b.mean_bits != null ? `, ${b.mean_bits.toFixed(2)} bits` : "") +
        (b.failed ? "  ← FLAGGED (blind model reproduced it)" : "")
      );
      $("#scorecard").textContent = ["CONTINUATION PREDICTABILITY", ...lines].join("\n");
    }
  } catch (err) { toast(err.message, "error"); }
  setBusy(false);
}

async function fixBlocks(indices) {
  setBusy(true, "Rewriting flagged blocks…");
  try {
    const out = await api("/api/fix", { method: "POST", body: { markdown: editor.value(), block_indices: indices } });
    if (!out.changed) {
      toast("nothing flagged — all gates pass", "ok");
    } else {
      applyAgentMarkdown(out.markdown);
      chatNote("agent", `Rewrote block${(indices || Object.keys(out.reasons)).length > 1 ? "s" : ""} ${Object.keys(out.reasons).join(", ")}. Revert if I made it worse.`);
      await saveDraft();
      await runLint();
    }
  } catch (err) { toast(err.message, "error"); }
  setBusy(false);
}

// ---------- co-editor ----------

async function sendInstruction() {
  const input = $("#instruction");
  const instruction = input.value.trim();
  if (!instruction) return;
  const selection = editor.codemirror.getSelection();
  const hasSelection = selection && selection.trim().length > 3;
  input.value = "";
  chatNote("you", `${instruction}${hasSelection ? "  [selection]" : ""}`);
  setBusy(true, "Co-editor thinking…");
  try {
    const out = await api("/api/revise", {
      method: "POST",
      body: {
        markdown: editor.value(),
        instruction,
        selection: hasSelection ? selection : null,
      },
    });
    applyAgentMarkdown(out.markdown);
    const scope = out.scope === "selection" ? "the selected passage" : "the document";
    chatNote("agent", `Rewrote ${scope}. Revert if I made it worse.`);
    await saveDraft();
  } catch (err) {
    chatNote("error", err.message);
  }
  setBusy(false);
}

// ---------- wiring ----------

$("#btn-new").onclick = () => { $("#new-draft").hidden = !$("#new-draft").hidden; };
$("#new-draft").onsubmit = (e) => { e.preventDefault(); runAgent(e.target); };
$("#btn-save").onclick = saveDraft;
$("#btn-lint").onclick = runLint;
$("#btn-score").onclick = runScore;
$("#btn-shape").onclick = runShape;
$("#btn-fix").onclick = () => fixBlocks(null);
$("#btn-revise").onclick = sendInstruction;
$("#btn-revert").onclick = async () => {
  if (!history.length) return;
  editor.value(history.pop());
  updateHistoryUi();
  await saveDraft();
  toast("reverted", "ok");
};

document.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === "s") { e.preventDefault(); saveDraft(); }
  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); sendInstruction(); }
});

loadDrafts();
