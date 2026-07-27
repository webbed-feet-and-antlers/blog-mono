"""Prompt templates for the MTEB chunking + query-generation stages.

All templates are filled via ``str.format`` — keep braces literal in the
template text only where they belong to the schema example.
"""

from __future__ import annotations

# Shared system prompt: always reply with a single valid JSON object.
SYSTEM = (
    "You are a meticulous data engineer preparing a retrieval benchmark. "
    "Always respond with a single valid JSON object matching the requested "
    "schema. No prose. No markdown fences. No commentary."
)

# Stage 1 — chunk a GovReport report body into 2–8 topical, verbatim chunks.
CHUNK_USER_TEMPLATE = """\
Chunk the following U.S. government report into 2 to 8 coherent, self-contained sections \
that a retrieval system could index independently.

REPORT ID: {report_id}
REPORT BODY:
\"\"\"
{report_body}
\"\"\"

Hard requirements:
- Produce verbatim text only. Do NOT paraphrase, do NOT merge non-adjacent sentences, \
do NOT insert ellipses, do NOT invent content.
- 3 to 10 word unique title per chunk (specific to that chunk's topic).
- Skip headings, table of contents, page numbers, and other boilerplate.
- Cover the entire substantive body of the report (every paragraph must appear in some chunk).
- 100 to 1500 characters of body text per chunk.
- One topic = one chunk. Do not over-split a single topic across multiple chunks.

Return JSON of exactly this shape:
{{"chunks": [{{"title": "<unique 3-10 word title>", "text": "<verbatim body text>"}}]}}"""

# Stage 1 variant when the report was pre-split (long reports).
CHUNK_SECTION_USER_TEMPLATE = """\
This is section {section_index} of {section_total} from a longer U.S. government report. \
Chunk ONLY this section, independently of any other sections.

REPORT ID: {report_id}
SECTION {section_index}/{section_total}:
\"\"\"
{report_body}
\"\"\"

Hard requirements:
- Produce verbatim text only. Do NOT paraphrase, do NOT merge non-adjacent sentences, \
do NOT insert ellipses, do NOT invent content.
- 3 to 10 word unique title per chunk.
- Skip headings, table of contents, page numbers, and other boilerplate.
- 100 to 1500 characters of body text per chunk.
- One topic = one chunk. It is fine if this section yields only a single chunk.

Return JSON of exactly this shape:
{{"chunks": [{{"title": "<unique 3-10 word title>", "text": "<verbatim body text>"}}]}}"""

# Stage 2 — generate 1–3 retrieval queries that ONLY this chunk can answer.
QUERY_USER_TEMPLATE = """\
You are an analyst writing retrieval queries for a benchmark. Below is one verbatim chunk \
from a U.S. government report. Write 1 to 3 short, specific queries an analyst might type \
that this chunk — and ONLY this chunk — could answer.

CHUNK ID: {chunk_id}
CHUNK TITLE: {title}
CHUNK TEXT:
\"\"\"
{chunk_text}
\"\"\"

Hard requirements:
- Each query must be answerable ONLY by this chunk, not by generic knowledge or other parts of the report.
- Avoid generic questions ("What is this report about?"). Tie queries to named programs, dates, agencies, dollar amounts, or specific findings.
- Do NOT copy verbatim phrases from the chunk. Rephrase the question in an analyst's own words.
- Provide a concise gold `answer` (20-500 chars) derivable SOLELY from this chunk.
- Diversify across the 1-3 queries (different angles, not rephrasings of each other).

Return JSON of exactly this shape:
{{"queries": [{{"query": "<analyst-style question>", "answer": "<concise gold answer>"}}]}}"""

# Repair prompt — append to message history when JSON parsing/validation fails.
REPAIR_SYSTEM = (
    "Your previous response was not valid: {error}. "
    "Re-emit ONLY a valid JSON object matching the requested schema. "
    "No prose. No markdown fences. No commentary."
)


# ----- Stage 4: STS ---------------------------------------------------------

STS_USER_TEMPLATE = """\
You are scoring semantic textual similarity for a retrieval benchmark.

Below is a JSON list of text-pairs. For EACH pair, rate the semantic similarity \
of the two passages on a 0.0 to 5.0 scale, where:
  5.0 = exactly the same meaning
  4.0 = close to the same meaning; minor differences
  3.0 = roughly similar; some important differences
  2.0 = related topic but different specifics
  1.0 = barely related
  0.0 = completely unrelated

Score each pair independently. Use the full range. Use one decimal place.

PAIRS (JSON):
{pairs_json}

Return JSON of exactly this shape (one entry per input pair, preserving pair_id):
{{"scores": [{{"pair_id": "<pair_id>", "score": <0.0-5.0>}}]}}"""


# ----- Stage 5: Summary STS -------------------------------------------------

SUMMARY_STS_USER_TEMPLATE = """\
You are scoring how relevant a report passage is to its summary.

Below is a JSON list of (summary, passage) pairs. For each pair, rate on a 0.0 \
to 5.0 scale how directly the passage is relevant to the summary's topic:
  5.0 = the passage directly addresses the summary's main point
  4.0 = the passage substantively supports the summary
  3.0 = the passage is topically related and partially supports the summary
  2.0 = the passage is on a related topic but doesn't support the summary
  1.0 = barely related to the summary
  0.0 = completely unrelated to the summary

Score each pair independently. Use one decimal place.

PAIRS (JSON):
{pairs_json}

Return JSON of exactly this shape (preserve pair_id ordering):
{{"scores": [{{"pair_id": "<pair_id>", "score": <0.0-5.0>}}]}}"""


# ----- Stage 6: Clustering --------------------------------------------------

CLUSTERING_USER_TEMPLATE = """\
You are classifying U.S. government report passages into a fixed topic vocabulary.

Assign each passage to EXACTLY ONE of the following topics (verbatim, \
case-sensitive):
{topic_vocab}

Choose the BEST single fit. If a passage touches multiple topics, pick the most \
salient one. Use the topic string verbatim (do not paraphrase, pluralize, or \
abbreviate).

PASSAGES (JSON):
{chunks_json}

Return JSON of exactly this shape (one entry per passage, preserving chunk_id):
{{"assignments": [{{"chunk_id": "<chunk_id>", "topic": "<one of the topics above>"}}]}}"""


# ----- Stage 7: Reranking ---------------------------------------------------

RERANKING_USER_TEMPLATE = """\
You are scoring how relevant each candidate passage is to a retrieval query.

QUERY:
\"\"\"{query}\"\"\"

For each candidate below, score its relevance to the query on a 0 to 3 scale:
  3 = directly answers the query
  2 = substantively relevant (would be useful)
  1 = topically related but doesn't answer the query
  0 = irrelevant

Score each candidate independently. Be strict: a candidate only earns 3 if it \
substantively answers THIS specific query.

CANDIDATES (JSON):
{candidates_json}

Return JSON of exactly this shape (one entry per candidate, preserving chunk_id):
{{"query_id": "{query_id}", "scores": [{{"chunk_id": "<chunk_id>", "score": <0-3>}}]}}"""


# ----- Stage 8: Cross-report retrieval --------------------------------------

CROSS_REPORT_USER_TEMPLATE = """\
You are judging whether each passage from OTHER reports substantively answers \
a given query.

QUERY:
\"\"\"{query}\"\"\"

For each candidate passage below, judge whether it substantively answers the \
query (true) or not (false). Only mark true if the passage contains information \
that genuinely addresses the query — not merely topically related.

CANDIDATES (JSON):
{candidates_json}

Return JSON of exactly this shape (one entry per candidate, preserving chunk_id):
{{"query_id": "{query_id}", "matches": [{{"chunk_id": "<chunk_id>", "relevant": <true|false>}}]}}"""


# ----- Stage 9: Pair Classification -----------------------------------------

PAIR_CLASSIFY_USER_TEMPLATE = """\
You are judging whether two passages discuss the SAME SPECIFIC topic.

For each text-pair below, output 1 if both passages discuss the same specific \
topic (not merely the same broad category), otherwise 0. Be strict: same broad \
domain (e.g. both about "healthcare") but different subtopics counts as 0.

PAIRS (JSON):
{pairs_json}

Return JSON of exactly this shape (one entry per pair, preserving pair_id):
{{"items": [{{"pair_id": "<pair_id>", "label": <0|1>}}]}}"""
