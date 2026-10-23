import time

import pytest
from langchain_core.runnables import RunnableLambda
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq

from core import llm
from core.llm import with_backup


def _provider_down(_):
    raise TimeoutError("429 rate limit / timed out")


def _our_bug(_):
    raise TypeError("a bug in our own code")


def test_backup_answers_when_primary_fails():
    primary = RunnableLambda(_provider_down)
    backup = RunnableLambda(lambda _: "from backup")
    assert with_backup(primary, backup).invoke("hi") == "from backup"


def test_primary_answers_when_healthy():
    primary = RunnableLambda(lambda _: "from primary")
    backup = RunnableLambda(lambda _: "from backup")
    assert with_backup(primary, backup).invoke("hi") == "from primary"


def test_error_when_both_fail():
    primary = RunnableLambda(_provider_down)
    backup = RunnableLambda(_provider_down)
    with pytest.raises(TimeoutError):
        with_backup(primary, backup).invoke("hi")


def test_a_bug_in_our_code_is_not_hidden_by_the_fallback():
    calls = []
    primary = RunnableLambda(_our_bug)
    backup = RunnableLambda(lambda _: calls.append("backup") or "from backup")
    with pytest.raises(TypeError):
        with_backup(primary, backup).invoke("hi")
    assert calls == []  # the backup was never asked


def test_missing_env_vars_are_named(monkeypatch):
    for name in llm.REQUIRED_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(llm, "load_dotenv", lambda: None)
    with pytest.raises(RuntimeError, match="GEMINI_KEY"):
        llm.get_llm()


@pytest.fixture
def fake_env(monkeypatch):
    monkeypatch.setattr(llm, "load_dotenv", lambda: None)
    monkeypatch.setenv("GEMINI_KEY", "fake")
    monkeypatch.setenv("GEMINI_NAME", "gemini-test")
    monkeypatch.setenv("GROQ_KEY", "fake")
    monkeypatch.setenv("GROQ_NAME", "groq-test")


def test_gemini_is_primary_by_default(fake_env, monkeypatch):
    monkeypatch.delenv("LLM_PRIMARY", raising=False)
    primary, backup = llm.build_models()
    assert isinstance(primary, ChatGoogleGenerativeAI)
    assert isinstance(backup, ChatGroq)


def test_llm_primary_can_switch_to_groq(fake_env, monkeypatch):
    monkeypatch.setenv("LLM_PRIMARY", "Groq")
    primary, backup = llm.build_models()
    assert isinstance(primary, ChatGroq)
    assert isinstance(backup, ChatGoogleGenerativeAI)


def test_an_unknown_primary_is_rejected(fake_env, monkeypatch):
    monkeypatch.setenv("LLM_PRIMARY", "gpt")
    with pytest.raises(RuntimeError, match="LLM_PRIMARY"):
        llm.get_llm()


def test_every_model_reports_to_the_metrics_recorder(fake_env, monkeypatch):
    monkeypatch.delenv("LLM_PRIMARY", raising=False)
    primary, backup = llm.build_models()
    assert llm.metrics.recorder in primary.callbacks
    assert llm.metrics.recorder in backup.callbacks


def test_get_llm_puts_a_deadline_on_both_models(fake_env, monkeypatch):
    monkeypatch.delenv("LLM_PRIMARY", raising=False)
    chain = llm.get_llm()
    assert chain.runnable.name == "deadline"
    assert chain.fallbacks[0].name == "deadline"


# --- the deadline -----------------------------------------------------------------
def _hangs(_):
    time.sleep(3)
    return "too late"


def test_a_hung_primary_is_cut_off_and_the_backup_answers():
    primary = RunnableLambda(_hangs)
    backup = RunnableLambda(lambda _: "from backup")
    start = time.perf_counter()
    answer = with_backup(primary, backup, deadline=0.3).invoke("hi")
    assert answer == "from backup"
    assert time.perf_counter() - start < 2  # did not wait the 3 seconds


def test_a_fast_model_is_not_affected_by_the_deadline():
    primary = RunnableLambda(lambda _: "from primary")
    backup = RunnableLambda(lambda _: "from backup")
    assert with_backup(primary, backup, deadline=5).invoke("hi") == "from primary"


def test_when_both_hang_a_timeout_is_raised():
    with pytest.raises(TimeoutError):
        with_backup(RunnableLambda(_hangs), RunnableLambda(_hangs), deadline=0.2).invoke("hi")
