import pytest

from writing_agent import llm


class _FakeChatCompletions:
    """Serves chat(), chat_score(), and observer calls; dispatches on the
    logprobs/model kwargs like the real endpoint."""

    def __init__(self):
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        if kwargs.get("logprobs"):
            msg = type("M", (), {"content": "The cat sat."})()
            lp_entry = type("E", (), {"token": "The", "logprob": -0.6})()
            lp = type("LP", (), {"content": [lp_entry]})()
            choice = type("C", (), {"message": msg, "logprobs": lp})()
        elif (kwargs.get("model") or "").startswith("fake/observer"):
            msg = type("M", (), {"content": "Observer continuation."})()
            choice = type("C", (), {"message": msg, "logprobs": None})()
        else:
            msg = type("M", (), {"content": "hello world"})()
            choice = type("C", (), {"message": msg, "logprobs": None})()
        return type("R", (), {"choices": [choice]})()


class _FakeEmbeddings:
    def __init__(self):
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        data = [
            type("D", (), {"embedding": [0.1 * i, 0.2]})()
            for i, _ in enumerate(kwargs["input"])
        ]
        return type("R", (), {"data": data})()


@pytest.fixture
def fake_client(monkeypatch):
    chat_completions = _FakeChatCompletions()
    embeddings = _FakeEmbeddings()

    class FakeChat:
        completions = chat_completions

    client = type(
        "Client", (), {"chat": FakeChat(), "embeddings": embeddings}
    )()
    monkeypatch.setattr(llm, "_client", client)
    return {"chat": chat_completions, "embeddings": embeddings}


async def test_chat_uses_openrouter_settings(fake_client, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    out = await llm.chat(
        [{"role": "user", "content": "hi"}], temperature=0.9, top_p=0.92
    )
    assert out == "hello world"
    kwargs = fake_client["chat"].kwargs
    assert kwargs["temperature"] == 0.9
    assert kwargs["top_p"] == 0.92


async def test_chat_score_returns_text_and_logprobs(fake_client, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    text, lps = await llm.chat_score("Some document prefix.")
    assert text == "The cat sat."
    assert lps == [-0.6]
    kwargs = fake_client["chat"].kwargs
    assert kwargs["temperature"] == 0.0
    assert kwargs["logprobs"] is True
    assert kwargs["top_logprobs"] == 1


async def test_chat_score_without_logprobs_omits_params(fake_client, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    text, lps = await llm.chat_score("prefix.", model="fake/observer", logprobs=False)
    assert text == "Observer continuation."
    assert lps == []
    kwargs = fake_client["chat"].kwargs
    assert "logprobs" not in kwargs and "top_logprobs" not in kwargs


async def test_embed_batches_and_returns_vectors(fake_client, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    vectors = await llm.embed(["one", "two", "three"])
    assert len(vectors) == 3
    assert vectors[0] == [0.0, 0.2]
    kwargs = fake_client["embeddings"].kwargs
    assert kwargs["input"] == ["one", "two", "three"]
    assert kwargs["model"] == "openai/text-embedding-3-small"


async def test_chat_json_extracts_wrapped_json(monkeypatch):
    async def fake_chat(messages, **kw):
        return 'Sure! {"a": 1}'

    result = await llm.chat_json_with(fake_chat)([{"role": "user", "content": "x"}])
    assert result == {"a": 1}
