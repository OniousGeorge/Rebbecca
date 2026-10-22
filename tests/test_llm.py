import pytest
from langchain_core.runnables import RunnableLambda

from core import llm
from core.llm import with_backup


def _failing(_):
    raise RuntimeError("429 rate limit")


def test_backup_answers_when_primary_fails():
    primary = RunnableLambda(_failing)
    backup = RunnableLambda(lambda _: "from backup")
    assert with_backup(primary, backup).invoke("hi") == "from backup"


def test_primary_answers_when_healthy():
    primary = RunnableLambda(lambda _: "from primary")
    backup = RunnableLambda(lambda _: "from backup")
    assert with_backup(primary, backup).invoke("hi") == "from primary"


def test_error_when_both_fail():
    primary = RunnableLambda(_failing)
    backup = RunnableLambda(_failing)
    with pytest.raises(RuntimeError):
        with_backup(primary, backup).invoke("hi")


def test_missing_env_vars_are_named(monkeypatch):
    for name in llm.REQUIRED_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(llm, "load_dotenv", lambda: None)
    with pytest.raises(RuntimeError, match="GEMINI_KEY"):
        llm.get_llm()
