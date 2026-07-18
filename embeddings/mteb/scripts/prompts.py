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
