from writing_agent.config import get_settings


def test_defaults_load_from_toml():
    s = get_settings()
    assert s.thresholds.sentence_sd_min == 8.0
    assert s.thresholds.sentence_variance_min == 15.0
    assert s.thresholds.transition_density_max == 0.15
    assert s.thresholds.continuation_overlap_max == 0.55
    assert s.thresholds.continuation_bits_max == 0.9
    assert s.thresholds.max_revisions == 3
    assert s.models.draft == "anthropic/claude-sonnet-5"
    assert s.models.scorer == "~openai/gpt-mini-latest"
    assert s.sampling.stylist_temp == 0.9
    assert s.openrouter_base_url == "https://openrouter.ai/api/v1"


def test_env_overrides_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-123")
    get_settings.cache_clear()
    try:
        s = get_settings()
        assert s.openrouter_api_key == "sk-test-123"
    finally:
        get_settings.cache_clear()
