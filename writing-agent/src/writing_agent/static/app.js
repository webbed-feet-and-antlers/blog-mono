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
    if (out.semantic && !out.semantic.skipped && out.semantic.step_max != null) {
      lines.push(
        `semantic glide: largest step ${out.semantic.step_max.toFixed(2)}` +
        (out.semantic.failed ? "  ← FLAGGED (no semantic shift between sections)" : "")
      );
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

// ---------- shape graph (StoryScope-style radar + narrative space) ----------

let radarChart = null;
let scatterChart = null;
let rarityChart = null;

const GROUP_COLORS = { current: "#6ea8fe", draft: "#9d8cff", human: "#7ee2a8", ai: "#ff8f8f" };
const GROUP_LABELS = { human: "Your posts", current: "This draft", draft: "Other drafts", ai: "AI controls" };

// Deterministic jitter so points don't re-shuffle between renders.
function jitter(label) {
  let h = 0;
  for (const c of label) h = (h * 31 + c.charCodeAt(0)) % 997;
  return (h % 100) / 100 * 0.5 - 0.25;
}

function renderRarity(docs) {
  // StoryScope Figure 5 analogue: narrative-rarity percentile by group.
  // Violins need densities we don't have at this corpus size, so points
  // with solid mean / dashed median lines carry the same comparison.
  const order = ["human", "current", "draft", "ai"];
  const datasets = [];
  order.forEach((g, gi) => {
    const group = docs.filter((d) => d.group === g);
    if (!group.length) return;
    datasets.push({
      label: GROUP_LABELS[g],
      data: group.map((d) => ({ x: gi + jitter(d.label), y: d.rarity })),
      pointBackgroundColor: GROUP_COLORS[g],
      pointRadius: 5,
    });
    const rs = group.map((d) => d.rarity).sort((a, b) => a - b);
    const mean = rs.reduce((s, r) => s + r, 0) / rs.length;
    const median = rs[Math.floor(rs.length / 2)];
    datasets.push({
      data: [{ x: gi - 0.32, y: mean }, { x: gi + 0.32, y: mean }],
      showLine: true, borderColor: GROUP_COLORS[g], borderWidth: 2, pointRadius: 0,
    });
    datasets.push({
      data: [{ x: gi - 0.32, y: median }, { x: gi + 0.32, y: median }],
      showLine: true, borderColor: GROUP_COLORS[g], borderWidth: 1.5,
      borderDash: [5, 4], pointRadius: 0,
    });
  });
  if (rarityChart) rarityChart.destroy();
  rarityChart = new Chart($("#rarity"), {
    type: "scatter",
    data: { datasets },
    options: {
      plugins: {
        legend: { labels: { color: "#d8dee9", boxWidth: 10, font: { size: 10 } } },
        tooltip: { callbacks: { label: (c) => `${c.raw.label ?? ""} rarity ${(c.parsed.y * 100).toFixed(0)}%` } },
      },
      scales: {
        x: {
          min: -0.5, max: order.length - 0.5,
          ticks: { color: "#7b8794", font: { size: 10 }, callback: (_, i) => GROUP_LABELS[order[i]] ?? "" },
          grid: { color: "#263042" },
        },
        y: {
          min: -0.05, max: 1.05,
          title: { display: true, text: "Rarity percentile (vs. pooled)", color: "#7b8794", font: { size: 10 } },
          ticks: { color: "#7b8794", font: { size: 9 }, callback: (v) => `${Math.round(v * 100)}%` },
          grid: { color: "#263042" },
        },
      },
    },
  });
}

function renderScatter(docs) {
  // StoryScope LDA-figure analogue: narrative space with group centroids.
  const groups = ["human", "current", "draft", "ai"];
  const datasets = groups
    .map((g) => {
      const pts = docs.filter((d) => d.group === g);
      if (!pts.length) return null;
      const base = {
        label: GROUP_LABELS[g],
        data: pts.map((d) => ({ x: d.x, y: d.y, label: d.label })),
        pointBackgroundColor: GROUP_COLORS[g] + "aa",
        pointRadius: g === "current" ? 7 : 5,
      };
      const cx = pts.reduce((s, d) => s + d.x, 0) / pts.length;
      const cy = pts.reduce((s, d) => s + d.y, 0) / pts.length;
      const centroid = {
        data: [{ x: cx, y: cy, label: `${GROUP_LABELS[g]} centroid` }],
        pointStyle: "rectRot", rotation: 45,
        pointRadius: 9, pointBorderWidth: 1.5,
        pointBackgroundColor: GROUP_COLORS[g], pointBorderColor: "#0d1117",
        label: `${GROUP_LABELS[g]} ◆`,
      };
      return [base, centroid];
    })
    .filter(Boolean)
    .flat();
  if (scatterChart) scatterChart.destroy();
  scatterChart = new Chart($("#scatter"), {
    type: "scatter",
    data: { datasets },
    options: {
      plugins: {
        legend: { labels: { color: "#d8dee9", boxWidth: 10, font: { size: 10 }, filter: (i) => !i.text.includes("◆") } },
        tooltip: { callbacks: { label: (c) => c.raw.label || "" } },
      },
      scales: {
        x: { title: { display: true, text: "PC1", color: "#7b8794", font: { size: 10 } }, grid: { color: "#263042" }, ticks: { color: "#7b8794", font: { size: 9 } } },
        y: { title: { display: true, text: "PC2", color: "#7b8794", font: { size: 10 } }, grid: { color: "#263042" }, ticks: { color: "#7b8794", font: { size: 9 } } },
      },
    },
  });
}

async function runGraph() {
  setBusy(true, "Computing narrative space…");
  try {
    const out = await api("/api/shape/compare", { method: "POST", body: { markdown: editor.value() } });
    $("#charts").hidden = false;

    const labels = out.axes_names;
    const human = out.docs.filter((d) => d.group === "human");
    const ai = out.docs.find((d) => d.group === "ai");
    const current = out.docs.find((d) => d.group === "current");

    renderRarity(out.docs);
    renderScatter(out.docs);

    const humanMean = labels.map(
      (l) => (human.length ? human.reduce((s, d) => s + d.axes[l], 0) / human.length : 0)
    );
    if (radarChart) radarChart.destroy();
    radarChart = new Chart($("#radar"), {
      type: "radar",
      data: {
        labels,
        datasets: [
          { label: "this draft", data: labels.map((l) => current.axes[l]), borderColor: GROUP_COLORS.current, backgroundColor: "rgba(110,168,254,0.15)" },
          ...(human.length ? [{ label: "your human posts (mean)", data: humanMean, borderColor: GROUP_COLORS.human, backgroundColor: "rgba(126,226,168,0.10)" }] : []),
          { label: "AI-shaped control", data: labels.map((l) => ai.axes[l]), borderColor: GROUP_COLORS.ai, borderDash: [5, 4], backgroundColor: "transparent" },
        ],
      },
      options: {
        plugins: { legend: { labels: { color: "#d8dee9", boxWidth: 10, font: { size: 10 } } } },
        scales: { r: { min: 0, max: 1, grid: { color: "#263042" }, angleLines: { color: "#263042" }, pointLabels: { color: "#7b8794", font: { size: 9 } }, ticks: { display: false } } },
      },
    });

    if (out.authorship) {
      const a = out.authorship;
      $("#judge-line").textContent =
        `authorship: ${a.verdict === "human-side" ? "✓" : "⚠"} ${a.verdict} ` +
        `(dist-to-you ${a.human_dist} vs dist-to-AI ${a.ai_dist}, ratio ${a.ratio})` +
        (out.judge ? ` · judge: ${Object.entries(out.judge).filter(([k]) => k !== "notes").map(([k, v]) => `${k} ${v.toFixed(2)}`).join(" · ")}` : "");
    } else if (out.judge) {
      const axes = Object.entries(out.judge)
        .filter(([k]) => k !== "notes")
        .map(([k, v]) => `${k} ${v.toFixed(2)}`);
      $("#judge-line").textContent = `judge: ${axes.join(" · ")} — add posts to references/ for the authorship check`;
    } else {
      $("#judge-line").textContent = "judge: unavailable";
    }
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
$("#btn-graph").onclick = runGraph;
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
