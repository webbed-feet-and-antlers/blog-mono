from .types import ChunkSpan


def tokenize_doc_with_sep(
    chunk_texts: list[str],
    tokenizer,
    max_length: int,
) -> tuple[dict, list[ChunkSpan]]:
    sep_token = tokenizer.sep_token or tokenizer.eos_token or "\n"
    joined = sep_token.join(chunk_texts)
    enc = tokenizer(
        joined,
        return_tensors="pt",
        max_length=max_length,
        truncation=True,
        padding=False,
    )
    input_ids = enc["input_ids"].squeeze(0)
    sep_token_id = tokenizer.sep_token_id or tokenizer.eos_token_id
    sep_positions = (input_ids == sep_token_id).nonzero(as_tuple=True)[0].tolist()
    spans = []
    start_pos = 0
    for sep_pos in sep_positions:
        if start_pos < sep_pos:
            spans.append(ChunkSpan(start_pos, sep_pos, ""))
        start_pos = sep_pos + 1
    if start_pos < input_ids.shape[0]:
        spans.append(ChunkSpan(start_pos, input_ids.shape[0], ""))
    for i, sp in enumerate(spans):
        if i < len(chunk_texts):
            sp.text = chunk_texts[i]
    return enc, spans
