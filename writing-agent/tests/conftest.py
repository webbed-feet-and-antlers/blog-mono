import pytest

from writing_agent.config import get_settings


@pytest.fixture(autouse=True)
def _fresh_settings():
    """Settings are lru_cached; tests must not inherit each other's env."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
