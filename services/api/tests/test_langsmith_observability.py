from app.config.settings import Settings
from app.observability import langsmith as langsmith_module


def test_setup_langsmith_sets_v1_and_v2_tracing_env(monkeypatch) -> None:
    monkeypatch.setattr(langsmith_module, "_LANGSMITH_INITIALIZED", False)
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.delenv("LANGSMITH_TRACING_V2", raising=False)

    settings = Settings(
        langsmith_enabled=True,
        langsmith_tracing=True,
        langsmith_api_key="test-key",
        langsmith_project="test-project",
        langsmith_endpoint="https://api.smith.langchain.com",
    )

    assert langsmith_module.setup_langsmith(settings) is True
    assert langsmith_module.os.environ["LANGSMITH_TRACING"] == "true"
    assert langsmith_module.os.environ["LANGSMITH_TRACING_V2"] == "true"
