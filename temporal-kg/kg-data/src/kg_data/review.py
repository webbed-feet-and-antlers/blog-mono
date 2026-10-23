"""Review page — a self-contained HTML viewer for eyeballing the corpus.

    uv run python -m kg_data review [--split train]

Writes data/review.html (no server, no deps): each doc rendered the way its
channel reads (Slack as a chat thread, Confluence/Drive/meeting notes as
markdown pages, email with a subject header), gold head/tail mentions
highlighted inline from their char offsets, quads listed with an automatic
"current at doc date?" check against the world timeline, likely instruction
violations (unlisted proper nouns) flagged per doc, corpus-level quality
signals (per-channel noise vs the real grounding corpora, repetition,
coverage), and good/bad verdicts you can export as JSON.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

from .config import DATA_DIR
from .schema import Doc, Quad, World

MONTHS_DAYS = set(
    "January February March April May June July August September October "
    "November December Monday Tuesday Wednesday Thursday Friday Saturday "
    "Sunday Jan Feb Mar Apr Jun Jul Aug Sep Sept Oct Nov Dec Mon Tue Wed "
    "Thu Fri Sat Sun".split()
)
COMMON_CAPS = set(
    "I We It This That They The There Then And But If When Where Why How "
    "What Who OK QA PR CEO AM PM UTC API ETL JSON HTTP SQL CPU QA "
    "Attendees Subject Date Author Summary Timeline Overview Root Cause "
    "Impact Action Items Next Steps Meeting Notes Incident Review "
    "Postmortem Design Doc Plan Status Update Team Channel Background "
    "Context Goals Non Goals Migration Rollout Lessons Learned "
    "Architecture Dependencies Ownership Appendix References TL DR "
    "RCA SLA SLO On Call Headsup Heads Update Quick Sync Weekly"
    .split()
)
ABBREV_TOKENS = {"pls", "thx", "cuz", "w/", "w/o", "ppl", "prob", "tmr",
                 "u", "ur", "imo", "imho", "afk", "brb", "lol", "tbh",
                 "ngl", "fwiw", "gonna", "wanna", "kinda", "y'all"}
EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\u2600-\u27BF\u2190-\u21FF\u2B00-\u2BFF]")


def _noise_signals(text: str) -> dict:
    """Cheap messiness proxies comparable to grounding noise_stats."""
    sents = [s for s in re.split(r"(?<=[.!?])\s+|\n", text) if s.strip()]
    n = len(sents) or 1
    lc = sum(1 for s in sents if s[:1].islower())
    abbrev = sum(1 for s in sents
                 if any(f" {a.strip()} " in f" {s.lower()} "
                        for a in ABBREV_TOKENS))
    return {"sentences": len(sents),
            "lc_start_rate": round(lc / n, 2),
            "abbrev": abbrev,
            "emoji": len(EMOJI_RE.findall(text))}


def _unlisted_proper_nouns(text: str, known_lower: set[str],
                           corpus_lower: set[str] | None = None) -> list[str]:
    """Capitalized mid-sentence tokens that are not world entities, authors,
    calendar/common words — a heuristic trap-line for instruction violations
    (the LLM was told not to name other real-world products) and for invented
    speakers. Excludes sentence/list starts, contractions, and any token whose
    lowercase form occurs somewhere in the corpus (that's just Title Case on
    a common noun, like headings)."""
    flagged: dict[str, int] = {}
    for m in re.finditer(r"[A-Z][A-Za-z0-9\-']{2,}", text):
        token = m.group(0)
        if "'" in token:  # contractions aren't proper nouns
            continue
        before = text[:m.start()].rstrip()
        at_start = (not before) or before[-1] in ".!?:;)]}\"'" \
            or before[-2:] in ("-", "–", "—", " *", " -")
        if at_start:
            continue
        if token.lower() in known_lower or token in COMMON_CAPS \
                or token in MONTHS_DAYS or token.isupper() and len(token) <= 4:
            continue
        if corpus_lower and token.lower() in corpus_lower:
            continue
        flagged[token] = flagged.get(token, 0) + 1
    return sorted(flagged, key=lambda t: -flagged[t])


def _quad_payloads(quads: list[Quad]) -> list[dict]:
    return [{
        "id": q.id,
        "head": {"eid": q.head.entity_id, "text": q.head.text,
                 "start": q.head.start, "end": q.head.end},
        "tail": {"eid": q.tail.entity_id, "text": q.tail.text,
                 "start": q.tail.start, "end": q.tail.end},
        "relation": q.relation,
        "valid_from": q.valid_from.isoformat() if q.valid_from else None,
        "valid_to": q.valid_to.isoformat() if q.valid_to else None,
        "time_source": q.time_source,
        "claim_status": q.claim_status,
        "discovered": q.discovered,
        "hedged": q.hedged,
    } for q in quads]


def build_payload(worlds: list[World], docs: list[Doc], quads: list[Quad],
                  questions: list, grounding_stats: dict | None = None) -> dict:
    worlds_by_id = {w.id: w for w in worlds}
    quads_by_rev: dict[str, list[Quad]] = {}
    for q in quads:
        quads_by_rev.setdefault(q.rev_id, []).append(q)

    # lowercase occurrence set across the whole corpus: a capitalized token
    # whose lowercase form appears anywhere is Title Case, not a proper noun
    corpus_lower: set[str] = set()
    for doc in docs:
        for rev in doc.revisions:
            corpus_lower.update(
                t for t in re.findall(r"[a-z][a-z0-9'\-]{2,}",
                                      rev.text.lower()) if "'" not in t)

    doc_payloads = []
    for doc in docs:
        world = worlds_by_id.get(doc.world_id)
        known_lower = set()
        author_names = set()
        if world is not None:
            for e in world.entities:
                for s in [e.name, *e.aliases]:
                    known_lower.update(s.lower().split())
        for part in doc.author.name.split():
            known_lower.add(part.lower())
        known_lower.discard("")
        revs = []
        for rev in doc.revisions:
            rev_quads = quads_by_rev.get(rev.rev_id, [])
            current_flags = []
            for q in rev_quads:
                if world is None or q.claim_status == "conflict_injected":
                    current_flags.append(None)  # rendered as "expected ✗"
                    continue
                # non-functional predicates hold several tails at once —
                # the stated tail must match ANY segment active at rev time
                ok = any(
                    s.tail_id == q.tail.entity_id
                    and s.valid_from <= rev.sim_time
                    and (s.valid_to is None or rev.sim_time < s.valid_to)
                    for s in world.segments_for(q.relation, q.head.entity_id)
                )
                current_flags.append(ok)
            revs.append({
                "rev_id": rev.rev_id,
                "sim_time": rev.sim_time.isoformat(),
                "kind": rev.revision_kind,
                "text": rev.text,
                "noise": _noise_signals(rev.text),
                "unlisted": _unlisted_proper_nouns(rev.text, known_lower,
                                                   corpus_lower),
                "quads": _quad_payloads(rev_quads),
                "quad_current": current_flags,
            })
        doc_payloads.append({
            "id": doc.id,
            "world_id": doc.world_id,
            "channel": doc.channel,
            "doc_type": doc.doc_type,
            "author": {"name": doc.author.name, "role": doc.author.role,
                       "offboarded": doc.author.offboarded_on.isoformat()
                       if doc.author.offboarded_on else None},
            "created_at": doc.created_at.isoformat(),
            "deleted_at": doc.deleted_at.isoformat() if doc.deleted_at else None,
            "n_revisions": len(doc.revisions),
            "revisions": revs,
        })

    # corpus-level signals
    by_channel: dict[str, list[dict]] = {}
    for d in doc_payloads:
        by_channel.setdefault(d["channel"], []).append(d)
    channel_stats = {}
    for ch, ds in by_channel.items():
        revs = [r for d in ds for r in d["revisions"]]
        opener_counts: dict[str, int] = {}
        for r in revs:
            first = next((ln for ln in r["text"].splitlines() if ln.strip()
                          and not ln.strip().startswith("#")), "")
            opener = " ".join(first.split()[:5])[:60]
            if opener:
                opener_counts[opener] = opener_counts.get(opener, 0) + 1
        channel_stats[ch] = {
            "docs": len(ds),
            "mean_words": round(sum(len(r["text"].split()) for r in revs)
                                / len(revs), 1),
            "lc_start_rate": round(sum(r["noise"]["lc_start_rate"]
                                       for r in revs) / len(revs), 2),
            "unlisted_docs": sum(1 for r in revs if r["unlisted"]),
            "top_openers": sorted(opener_counts.items(),
                                  key=lambda kv: -kv[1])[:3],
        }

    pred_counts: dict[str, int] = {}
    for q in quads:
        pred_counts[q.relation] = pred_counts.get(q.relation, 0) + 1

    audit_stats = {
        "discovered": sum(1 for q in quads if q.discovered),
        "hedged": sum(1 for q in quads if q.hedged),
    }

    worlds_payload = []
    for w in worlds:
        company = next((e.name for e in w.entities if e.type == "company"), "?")
        timelines = []
        for tl in w.timelines:
            label = w.predicate(tl.predicate_id).label
            vol = w.predicate(tl.predicate_id).volatility
            for seg in sorted(tl.segments, key=lambda s: s.valid_from):
                timelines.append({
                    "predicate": tl.predicate_id, "label": label,
                    "volatility": vol,
                    "head": w.entity(seg.head_id).name,
                    "tail": w.entity(seg.tail_id).name,
                    "from": seg.valid_from.isoformat(),
                    "to": seg.valid_to.isoformat() if seg.valid_to else None,
                })
        worlds_payload.append({"id": w.id, "company": company, "lore": w.lore,
                               "timelines": timelines})

    return {
        "docs": doc_payloads,
        "channel_stats": channel_stats,
        "predicate_counts": dict(sorted(pred_counts.items(),
                                        key=lambda kv: -kv[1])),
        "n_quads": len(quads),
        "n_conflicts": sum(1 for q in quads
                           if q.claim_status == "conflict_injected"),
        "n_discovered": audit_stats["discovered"],
        "n_hedged": audit_stats["hedged"],
        "questions": [q.model_dump(mode="json") for q in questions],
        "worlds": worlds_payload,
        "grounding": grounding_stats or {},
    }


def render_html(payload: dict) -> str:
    data_json = json.dumps(payload, ensure_ascii=False)
    return HTML_TEMPLATE.replace("__DATA__", data_json)


def build_review(split: str | None = None, *,
                 out: Path | None = None) -> Path:
    from . import export as export_mod

    worlds = export_mod.load_worlds()
    docs = export_mod.load_docs(split)
    quads = export_mod.load_quads(split)
    questions = export_mod.load_questions(split)
    wids = {d.world_id for d in docs}
    worlds = [w for w in worlds if w.id in wids]

    grounding_stats = {}
    stats_path = DATA_DIR / "grounding" / "noise_stats.json"
    if stats_path.exists():
        grounding_stats = json.loads(stats_path.read_text())

    payload = build_payload(worlds, docs, quads, questions, grounding_stats)
    out = out or (DATA_DIR / "review" / f"review_{split or 'all'}.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(payload), encoding="utf-8")
    return out


HTML_TEMPLATE = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>kg-data review</title>
<style>
:root{--bg:#0f1117;--card:#181b24;--ink:#e6e8ee;--dim:#8b91a3;--head:#7aa2ff;
--tail:#4fd6a4;--conflict:#ff6b81;--flag:#ffb454;--line:#262a36}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--ink);font:14px/1.5 -apple-system,system-ui,sans-serif;margin:0;padding:0 0 80px}
header{position:sticky;top:0;background:#12141cdd;border-bottom:1px solid var(--line);padding:10px 16px;backdrop-filter:blur(6px);z-index:9}
h1{font-size:16px;margin:0 0 6px}
.chips{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
.chip{border:1px solid var(--line);border-radius:999px;padding:2px 10px;cursor:pointer;color:var(--dim);background:transparent;font-size:12px}
.chip.on{color:var(--ink);border-color:var(--head);background:#1c2440}
select,button{background:#1c2030;color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:3px 8px;font-size:12px;cursor:pointer}
main{max-width:980px;margin:0 auto;padding:14px 16px}
section.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:10px;margin-bottom:14px}
.stats .box{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px;font-size:12.5px}
.stats b{color:var(--ink)} .stats .dim{color:var(--dim)}
.doc{background:var(--card);border:1px solid var(--line);border-radius:12px;margin:14px 0;padding:12px 14px}
.doc .meta{display:flex;gap:8px;flex-wrap:wrap;align-items:center;font-size:12px;color:var(--dim);margin-bottom:8px}
.badge{border-radius:6px;padding:1px 8px;font-size:11.5px;font-weight:600}
.b-slack{background:#3a2430;color:#ff9db1}.b-confluence{background:#1c2f4a;color:#8db8ff}
.b-drive{background:#1d3a2f;color:#7fe0b0}.b-email{background:#3a3324;color:#ffd98a}
.b-meeting_notes{background:#2c2440;color:#c4a8ff}
.revhead{font-size:12px;color:var(--dim);margin:8px 0 4px}
.body{white-space:pre-wrap;font-size:13.5px}
.slackline{margin:3px 0}.slackline .who{color:#9db4ff;font-weight:600}.slackline .t{color:var(--dim);font-size:11px}
.body h3,.body h4{margin:8px 0 4px}.body li{margin-left:18px;list-style:disc}
mark.h{background:#2b3f75;color:#a8c4ff;border-radius:3px;padding:0 2px}
mark.t{background:#1e4436;color:#8fe9c4;border-radius:3px;padding:0 2px}
mark.c{background:#522233;color:#ff9db1;border-radius:3px;padding:0 2px;text-decoration:underline wavy #ff6b81}
mark.d{background:transparent;color:#c9a8ff;border:1px dashed #7a5fc9;border-radius:3px;padding:0 2px}
table.q{border-collapse:collapse;width:100%;font-size:12.5px;margin-top:8px}
table.q td{border-top:1px solid var(--line);padding:4px 6px;vertical-align:top}
.ok{color:#4fd6a4}.bad-x{color:var(--conflict)}.flagbox{color:var(--flag);font-size:12px;margin-top:6px}
.verdict{display:flex;gap:6px;margin-top:8px}
.verdict button{font-size:12px}.verdict .on-good{border-color:#4fd6a4;color:#4fd6a4}
.verdict .on-bad{border-color:var(--conflict);color:var(--conflict)}
.verdict input{flex:1;background:#101319;color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:3px 8px;font-size:12px}
.qa{background:var(--card);border:1px solid var(--line);border-radius:10px;margin:10px 0;padding:10px 12px;font-size:13px}
.qa .k{font-size:11px;color:var(--dim)}.qa .a{color:#4fd6a4;margin-top:3px}
.tabs{display:flex;gap:6px;margin:16px 0 6px}
details.wtl{background:var(--card);border:1px solid var(--line);border-radius:10px;margin:8px 0;padding:8px 12px;font-size:12.5px}
#progress{font-size:12px;color:var(--dim)}
</style></head><body>
<header>
<h1>kg-data review — <span id="title"></span></h1>
<div class="chips" id="filters"></div>
<div class="chips" style="margin-top:6px">
<select id="kind"><option value="docs">documents</option><option value="questions">eval questions</option><option value="worlds">world timelines</option></select>
<button onclick="sample(20)">sample 20</button>
<button onclick="resetSample()">show all</button>
<span id="progress"></span>
<button onclick="exportVerdicts()" style="margin-left:auto">⬇ export verdicts</button>
</div>
</header>
<main><section class="stats" id="stats"></section><div id="content"></div></main>
<script>
const DATA = __DATA__;
const state = {channel:"all", flaggedOnly:false, conflictsOnly:false, discoveredOnly:false, sample:null, verdicts:JSON.parse(localStorage.getItem("kg_verdicts")||"{}")};

function esc(s){const d=document.createElement("div");d.textContent=s;return d.innerHTML}
function spanClass(q,role){if(q.claim_status==="conflict_injected")return "c";if(q.discovered)return "d";return role==="head"?"h":"t"}

function highlight(rev){
  const spans=[]; rev.quads.forEach((q,i)=>{
    const cls=spanClass(q,"head");
    spans.push({s:q.head.start,e:q.head.end,cls,title:q.relation+" · head"+(q.discovered?" · discovered":"")+(q.hedged?" · hedged":"")});
    spans.push({s:q.tail.start,e:q.tail.end,cls:spanClass(q,"tail"),title:q.relation+" · tail"+(q.discovered?" · discovered":"")+(q.hedged?" · hedged":"")});
  });
  const uniq=new Map(); spans.forEach(sp=>{const k=sp.s+"-"+sp.e;if(!uniq.has(k)||uniq.get(k).cls!=="c")uniq.set(k,sp)});
  const sorted=[...uniq.values()].sort((a,b)=>a.s-b.s||b.e-a.e);
  let out="",cur=0;
  for(const sp of sorted){ if(sp.s<cur)continue;
    out+=esc(rev.text.slice(cur,sp.s))+'<mark class="'+sp.cls+'" title="'+esc(sp.title)+'">'+esc(rev.text.slice(sp.s,sp.e))+'</mark>';cur=sp.e;}
  out+=esc(rev.text.slice(cur));
  return out;
}

function renderBody(doc,rev){
  const hl=highlight(rev);
  if(doc.channel==="slack"){
    return hl.split("\n").map(line=>{
      const m=line.match(/^(.*?)\s?(\[\d{1,2}:\d{2}\s?[AP]M\]):\s(.*)$/);
      if(m&&!m[1].includes("<mark")) return '<div class="slackline"><span class="who">'+m[1]+'</span> <span class="t">'+m[2]+'</span>: '+m[3]+'</div>';
      return '<div class="slackline">'+line+'</div>';
    }).join("");
  }
  return hl.split("\n").map(line=>{
    if(line.startsWith("<")) return line; // keeps inline marks on markdown lines
    let m;
    if(m=line.match(/^###&gt;\s?/)) return "";
    if(m=line.match(/^##\s+(.*)/)) return "<h4>"+m[1]+"</h4>";
    if(m=line.match(/^#\s+(.*)/)) return "<h3>"+m[1]+"</h3>";
    if(m=line.match(/^[-*]\s+(.*)/)) return "<li>"+m[1]+"</li>";
    return line? line : "";
  }).join("\n");
}

function revBlock(doc,rev){
  const rows=rev.quads.map((q,i)=>{
    const cur=rev.quad_current[i];
    const ok=cur===null?' <span style="color:var(--dim)">· injected conflict (expected ✗)</span>':(cur?' <span class="ok">✓ true@docdate</span>':' <span class="bad-x">✗ not current</span>');
    const tags=(q.discovered?' <span style="color:#c9a8ff">· discovered</span>':"")+(q.hedged?' <span style="color:var(--flag)">· hedged</span>':"");
    return "<tr><td>"+esc(q.head.text)+"</td><td>"+esc(q.relation)+tags+
      "</td><td>"+esc(q.tail.text)+"</td><td>"+(q.valid_from||"—")+"</td><td>"+q.time_source+ok+"</td></tr>";
  }).join("");
  const noise='<span class="dim" style="font-size:11px">noise: lc-start '+rev.noise.lc_start_rate+" · abbrev "+rev.noise.abbrev+" · emoji "+rev.noise.emoji+" · "+rev.noise.sentences+" sents</span>";
  const unlisted=rev.unlisted.length?'<div class="flagbox">⚠ unlisted proper nouns: '+rev.unlisted.map(esc).join(", ")+"</div>":"";
  return '<div class="revhead">'+rev.rev_id+" · "+rev.sim_time+" · "+rev.kind+" · "+noise+"</div>"+
    '<div class="body">'+renderBody(doc,rev)+"</div>"+unlisted+
    (rows?'<table class="q"><tr style="color:var(--dim);font-size:11px"><td>head</td><td>relation</td><td>tail</td><td>valid_from</td><td>time_source</td></tr>'+rows+"</table>":"");
}

function docCard(doc){
  const v=state.verdicts[doc.id]||{};
  const del=doc.deleted_at?' <span class="flagbox" style="margin:0">🗑 deleted '+doc.deleted_at+"</span>":"";
  const off=doc.author.offboarded?' · <span title="offboarded">🚪</span>':"";
  return '<div class="doc" id="'+doc.id+'"><div class="meta">'+
    '<span class="badge b-'+doc.channel+'">'+doc.channel+"</span> · "+
    esc(doc.doc_type)+" · "+doc.created_at+" · "+esc(doc.author.name)+" ("+esc(doc.author.role)+")"+off+" · "+doc.id+del+"</div>"+
    doc.revisions.map(r=>revBlock(doc,r)).join("<hr style='border-color:var(--line)'>")+
    '<div class="verdict"><button onclick="mark(\''+doc.id+'\',\'good\')" class="'+(v.v==="good"?"on-good":"")+'">✓ good</button>'+
    '<button onclick="mark(\''+doc.id+'\',\'bad\')" class="'+(v.v==="bad"?"on-bad":"")+'">✗ bad</button>'+
    '<input placeholder="note…" value="'+esc(v.n||"")+'" onchange="note(\''+doc.id+'\',this.value)"></div></div>';
}

function mark(id,v){const cur=state.verdicts[id]||{};state.verdicts[id]={v:cur.v===v?null:v,n:cur.n||""};localStorage.setItem("kg_verdicts",JSON.stringify(state.verdicts));render()}
function note(id,n){const cur=state.verdicts[id]||{};state.verdicts[id]={v:cur.v||null,n};localStorage.setItem("kg_verdicts",JSON.stringify(state.verdicts))}
function exportVerdicts(){
  const blob=new Blob([JSON.stringify(state.verdicts,null,1)],{type:"application/json"});
  const a=document.createElement("a");a.href=URL.createObjectURL(blob);a.download="review_verdicts.json";a.click();
}
function sample(n){const docs=DATA.docs.filter(docFilter);const byCh={};docs.forEach(d=>{(byCh[d.channel]=byCh[d.channel]||[]).push(d)});
  state.sample=new Set();Object.values(byCh).forEach(list=>{list.sort(()=>Math.random()-.5);list.slice(0,Math.max(1,Math.round(n*list.length/docs.length))).forEach(d=>state.sample.add(d.id))});render()}
function resetSample(){state.sample=null;render()}
function docFilter(d){
  if(state.channel!=="all"&&d.channel!==state.channel)return false;
  if(state.sample&&!state.sample.has(d.id))return false;
  if(state.flaggedOnly&&!d.revisions.some(r=>r.unlisted.length))return false;
  if(state.conflictsOnly&&!d.revisions.some(r=>r.quads.some(q=>q.claim_status==="conflict_injected")))return false;
  if(state.discoveredOnly&&!d.revisions.some(r=>r.quads.some(q=>q.discovered)))return false;
  return true;
}

function renderStats(){
  const s=DATA.channel_stats; const g=DATA.grounding;
  let ch=Object.entries(s).map(([k,v])=>{const real=g.ubuntu&&k==="slack"?" (real IRC "+g.ubuntu.lowercase_start_rate+")":"";
    return "<b>"+k+"</b>: "+v.docs+" docs · "+v.mean_words+" w · lc-start "+v.lc_start_rate+real+
      " · <span style='color:"+(v.unlisted_docs?"var(--flag)":"var(--dim)")+"'>"+v.unlisted_docs+" flagged</span>"+
      (v.top_openers.length&&v.top_openers[0][1]>1?" · opener×"+v.top_openers[0][1]:"")}).join("<br>");
  const pc=Object.entries(DATA.predicate_counts).slice(0,14).map(([k,v])=>k+" "+v).join(" · ");
  document.getElementById("stats").innerHTML=
    "<div class='box'><b>"+DATA.docs.length+"</b> docs · <b>"+DATA.n_quads+"</b> gold quads · <b>"+DATA.n_conflicts+"</b> conflicts · <b>"+DATA.n_discovered+"</b> discovered · "+DATA.n_hedged+" hedged · "+DATA.questions.length+" questions<br><span class='dim'>"+pc+"</span></div>"+
    "<div class='box'>"+ch+"</div>";
}

function render(){
  renderStats();
  const f=document.getElementById("filters");
  const chans=["all",...Object.keys(DATA.channel_stats)];
  f.innerHTML=chans.map(c=>'<button class="chip '+(state.channel===c?"on":"")+'" onclick="state.channel=\''+c+'\';render()">'+c+"</button>").join("")+
    '<button class="chip '+(state.flaggedOnly?"on":"")+'" onclick="state.flaggedOnly=!state.flaggedOnly;render()">⚠ flagged</button>'+
    '<button class="chip '+(state.conflictsOnly?"on":"")+'" onclick="state.conflictsOnly=!state.conflictsOnly;render()">conflicts</button>'+
    '<button class="chip '+(state.discoveredOnly?"on":"")+'" onclick="state.discoveredOnly=!state.discoveredOnly;render()">discovered</button>';
  const kind=document.getElementById("kind").value;
  const c=document.getElementById("content");
  const done=Object.values(state.verdicts).filter(v=>v.v).length;
  document.getElementById("progress").textContent=done+" reviewed";
  document.getElementById("title").textContent=DATA.docs.length+" docs · "+DATA.worlds.length+" worlds";
  if(kind==="docs"){const docs=DATA.docs.filter(docFilter);c.innerHTML=docs.slice(0,120).map(docCard).join("")+(docs.length>120?"<p class='dim'>"+(docs.length-120)+" more…</p>":"");}
  else if(kind==="questions"){
    const byKind={};DATA.questions.forEach(q=>{(byKind[q.kind]=byKind[q.kind]||[]).push(q)});
    c.innerHTML=Object.entries(byKind).map(([k,qs])=>"<h3 style='color:var(--dim)'>"+k+" ("+qs.length+")</h3>"+qs.map(q=>
      "<div class='qa'><div class='k'>"+q.as_of+" · "+(q.gold_doc_ids||[]).map(d=>'<a style="color:#7aa2ff" href="#'+d+'">'+d+"</a>").join(" ")+"</div>"+
      esc(q.question)+"<div class='a'>→ "+esc(q.answer)+"</div>"+(q.notes?"<div class='k'>"+esc(q.notes)+"</div>":"")+"</div>").join("")).join("");
  } else {
    c.innerHTML=DATA.worlds.map(w=>"<details class='wtl'><summary><b>"+w.company+"</b> — "+w.id+" · "+w.timelines.length+" timeline rows</summary><p class='dim'>"+esc(w.lore)+"</p>"+
      "<table class='q'>"+w.timelines.map(t=>"<tr><td>"+esc(t.head)+"</td><td>"+t.predicate+"</td><td>"+esc(t.tail)+"</td><td>"+t.from+" → "+(t.to||"now")+"</td><td>"+t.volatility+"</td></tr>").join("")+"</table></details>").join("");
  }
}
render();
</script></body></html>
"""
