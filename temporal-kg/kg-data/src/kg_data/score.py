"""Stage 8 — scoring.

Modes:
  quads    — precision/recall/F1 for triple match (relation matches the gold
             predicate id OR its label, case/space-normalized), plus temporal
             match rates over matched triples (exact bounds, interval overlap).
  stream   — final-graph F1: dedupe predictions into a graph, drop support
             from deleted docs, compare against "stated AND still-true" gold.
  questions — accuracy over an answers file (entity-alias-aware).

All pure code, no LLM.
"""

from __future__ import annotations

import re
from datetime import date

from .schema import Doc, EvalQuestion, Quad, World

FAR_FUTURE = date(2100, 1, 1)


def norm(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[_\s]+", " ", s)
    return re.sub(r"[^a-z0-9 ]", "", s)


class Resolver:
    """Maps surface strings to gold entity ids per world (alias-aware)."""

    def __init__(self, worlds: list[World]):
        self._by_world: dict[str, dict[str, str]] = {}
        for w in worlds:
            table: dict[str, str] = {}
            for e in w.entities:
                for surface in [e.name, *e.aliases]:
                    table[norm(surface)] = e.id
                table[norm(e.id)] = e.id
            self._by_world[w.id] = table

    def resolve(self, world_id: str, surface: str) -> str:
        return self._by_world.get(world_id, {}).get(norm(surface), norm(surface))

    def relation_tokens(self, world_id: str, relation_id: str,
                        worlds_by_id: dict[str, World]) -> set[str]:
        """Acceptable normalized forms of a gold relation."""
        forms = {norm(relation_id)}
        w = worlds_by_id.get(world_id)
        if w:
            try:
                forms.add(norm(w.predicate(relation_id).label))
            except StopIteration:
                pass
        return forms


def _triple_key(quad: Quad, resolver: Resolver, worlds_by_id) -> tuple:
    """(head, relation, label, tail) — relation/label normalized so a
    predicted phrase matches the gold predicate id OR its human label."""
    head = resolver.resolve(quad.world_id, quad.head.text)
    tail = resolver.resolve(quad.world_id, quad.tail.text)
    rel = norm(quad.relation)
    label = rel
    world = worlds_by_id.get(quad.world_id)
    if world is not None:
        try:
            label = norm(world.predicate(quad.relation).label)
        except StopIteration:
            pass
    return head, rel, label, tail


def _matches(pred_key, gold_key) -> bool:
    p_head, p_rel, _, p_tail = pred_key
    g_head, g_rel, g_label, g_tail = gold_key
    return p_head == g_head and p_tail == g_tail and p_rel in (g_rel, g_label)


def _interval_overlap(a_from, a_to, b_from, b_to) -> bool:
    a_to = a_to or FAR_FUTURE
    b_to = b_to or FAR_FUTURE
    a_from = a_from or date(1900, 1, 1)
    b_from = b_from or date(1900, 1, 1)
    return a_from <= b_to and b_from <= a_to


def _prf(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4)}


def score_quads(gold: list[Quad], pred: list[Quad], worlds: list[World],
                *, per_doc: bool = False) -> dict:
    """Triple-match P/R/F1 + temporal match rates over matched triples."""
    worlds_by_id = {w.id: w for w in worlds}
    resolver = Resolver(worlds)

    gold_entries = [(q, _triple_key(q, resolver, worlds_by_id)) for q in gold]

    matched: list[tuple[Quad, Quad]] = []
    used_gold: set[int] = set()
    fp = 0
    for p in pred:
        p_key = _triple_key(p, resolver, worlds_by_id)
        hit = None
        for i, (gq, g_key) in enumerate(gold_entries):
            if i in used_gold:
                continue
            if p.doc_id == gq.doc_id and _matches(p_key, g_key):
                hit = (i, gq)
                break
        if hit:
            used_gold.add(hit[0])
            matched.append((p, hit[1]))
        else:
            fp += 1
    fn = len(gold_entries) - len(used_gold)
    out = {"n_gold": len(gold_entries), "n_pred": len(pred),
           **_prf(len(matched), fp, fn)}

    temporal_exact = temporal_overlap = temporal_gold = 0
    for p, g in matched:
        if g.valid_from is not None:
            temporal_gold += 1
            exact = (p.valid_from == g.valid_from
                     and (p.valid_to is None) == (g.valid_to is None))
            temporal_exact += exact
            temporal_overlap += _interval_overlap(
                p.valid_from, p.valid_to, g.valid_from, g.valid_to)
    out["temporal"] = {
        "gold_with_bounds": temporal_gold,
        "exact_match": temporal_exact,
        "overlap_match": temporal_overlap,
    }

    if per_doc:
        per = {}
        for doc_id in {q.doc_id for q in gold}:
            g = [q for q in gold if q.doc_id == doc_id]
            p = [q for q in pred if q.doc_id == doc_id]
            per[doc_id] = score_quads(g, p, worlds)["f1"]
        out["per_doc_f1"] = per
    return out


def score_stream(gold: list[Quad], pred: list[Quad], docs: list[Doc],
                 worlds: list[World], *, as_of: date | None = None) -> dict:
    """Final-graph F1: union of predicted triples (deleted docs' support
    removed), compared against gold facts that were stated and are still
    true at `as_of` (default: each world's end)."""
    worlds_by_id = {w.id: w for w in worlds}
    resolver = Resolver(worlds)
    deleted = {d.id for d in docs if d.deleted_at is not None}

    def pred_graph() -> set:
        keys = set()
        for p in pred:
            if p.doc_id in deleted:
                continue
            k = _triple_key(p, resolver, worlds_by_id)
            keys.add((k[0], k[3], k[1]))  # head, tail, relation
        return keys

    gold_set = set()
    for g in gold:
        if g.claim_status != "accurate" or g.doc_id in deleted:
            continue
        w = worlds_by_id.get(g.world_id)
        end = as_of or (w.end if w else FAR_FUTURE)
        if g.valid_to is not None and g.valid_to <= end:
            continue  # superseded before the evaluation point
        k = _triple_key(g, resolver, worlds_by_id)
        gold_set.add((k[0], k[3], k[1]))

    pg = pred_graph()
    tp = len(pg & gold_set)
    return {"n_gold_current": len(gold_set), "n_pred_graph": len(pg),
            **_prf(tp, len(pg) - tp, len(gold_set) - tp)}


def score_questions(questions: list[EvalQuestion],
                    answers: dict[str, str], worlds: list[World]) -> dict:
    """Accuracy over answered questions; alias-aware entity matching."""
    worlds_by_id = {w.id: w for w in worlds}
    by_kind: dict[str, list[int]] = {}
    correct = 0
    answered = 0
    for q in questions:
        ans = answers.get(q.id)
        if ans is None:
            continue
        answered += 1
        ok = _answer_matches(q, ans, worlds_by_id)
        by_kind.setdefault(q.kind, []).append(int(ok))
        correct += ok
    return {
        "answered": answered, "correct": correct,
        "accuracy": round(correct / answered, 4) if answered else 0.0,
        "by_kind": {k: round(sum(v) / len(v), 3) for k, v in by_kind.items()},
    }


def _answer_matches(q: EvalQuestion, ans: str,
                    worlds_by_id: dict[str, World]) -> bool:
    a, exp = norm(ans), norm(q.answer)
    if not a:
        return False
    if a == exp or exp.startswith(a) or a.startswith(exp.split(" (")[0]):
        return True
    # entity-alias match against the expected entity
    if q.answer_entity_id:
        w = worlds_by_id.get(q.world_id)
        if w:
            try:
                ent = w.entity(q.answer_entity_id)
            except StopIteration:
                return False
            surfaces = [norm(s) for s in [ent.name, *ent.aliases]]
            return any(s and s in a for s in surfaces)
    return False
