import pytest

from writing_agent import llm


class _FakeChatCompletions:
    """Serves both chat() and chat_score(): dispatches on the logprobs kwarg
    (only chat_score passes it), like the real endpoint."""

    def __init__(self):
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        if kwargs.get("logprobs"):
            msg = type("M", (), {"content": "The cat sat."})()
            lp_entry = type("E", (), {"token": "The", "logprob": -0.6})()
            lp = type("LP", (), {"content": [lp_entry]})()
            choice = type("C", (), {"message": msg, "logprobs": lp})()
        else:
            msg = type("M", (), {"content": "hello world"})()
            choice = type("C", (), {"message": msg, "logprobs": None})()
        return type("R", (), {"choices": [choice]})()


@pytest.fixture
def fake_client(monkeypatch):
    chat_completions = _FakeChatCompletions()

    class FakeChat:
        completions = chat_completions

    client = type("Client", (), {"chat": FakeChat()})()
    monkeypatch.setattr(llm, "_client", client)
    return chat_completions


async def test_chat_uses_openrouter_settings(fake_client, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    out = await llm.chat(
        [{"role": "user", "content": "hi"}], temperature=0.9, top_p=0.92
    )
    assert out == "hello world"
    assert fake_client.kwargs["temperature"] == 0.9
    assert fake_client.kwargs["top_p"] == 0.92


async def test_chat_score_returns_text_and_logprobs(fake_client, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    text, lps = await llm.chat_score("Some document prefix.")
    assert text == "The cat sat."
    assert lps == [-0.6]
    assert fake_client.kwargs["temperature"] == 0.0
    assert fake_client.kwargs["logprobs"] is True
    assert fake_client.kwargs["top_logprobs"] == 1


async def test_chat_json_extracts_wrapped_json(monkeypatch):
    async def fake_chat(messages, **kw):
        return 'Sure! {"a": 1}'

    result = await llm.chat_json_with(fake_chat)([{"role": "user", "content": "x"}])
    assert result == {"a": 1}
