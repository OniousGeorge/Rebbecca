import uuid

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from core import metrics
from core.metrics import Metrics, describe, summarize


def finished_call(recorder, provider="google_genai", model="gemini-3.1-flash-lite", tokens=(100, 20)):
    run_id = uuid.uuid4()
    recorder.on_chat_model_start({}, [], run_id=run_id, metadata={"ls_provider": provider, "ls_model_name": model})
    message = AIMessage(
        content="ok",
        usage_metadata={"input_tokens": tokens[0], "output_tokens": tokens[1], "total_tokens": sum(tokens)},
    )
    recorder.on_llm_end(LLMResult(generations=[[ChatGeneration(message=message)]]), run_id=run_id)


def failed_call(recorder, provider="google_genai"):
    run_id = uuid.uuid4()
    recorder.on_chat_model_start({}, [], run_id=run_id, metadata={"ls_provider": provider, "ls_model_name": "m"})
    recorder.on_llm_error(TimeoutError("timed out"), run_id=run_id)


def test_a_successful_call_records_provider_tokens_and_time():
    recorder = Metrics()
    finished_call(recorder)
    call = recorder.calls[0]
    assert call["provider"] == "google_genai"
    assert call["ok"] is True
    assert (call["input_tokens"], call["output_tokens"]) == (100, 20)
    assert call["seconds"] >= 0


def test_a_failed_call_is_recorded_with_its_error():
    recorder = Metrics()
    failed_call(recorder)
    assert recorder.calls[0]["ok"] is False
    assert "TimeoutError" in recorder.calls[0]["error"]


def test_summary_adds_up_calls_failures_and_providers(monkeypatch):
    monkeypatch.setitem(metrics.PRICES, "gemini-3.1-flash-lite", (0.10, 0.40))
    recorder = Metrics()
    failed_call(recorder)
    finished_call(recorder, tokens=(1_000_000, 0))
    finished_call(recorder, provider="groq", model="unpriced-model", tokens=(10, 10))

    summary = summarize(recorder.calls)
    assert summary["llm_calls"] == 3
    assert summary["failed_calls"] == 1
    assert summary["by_provider"]["google_genai"] == {
        "calls": 2,
        "failed": 1,
        "seconds": summary["by_provider"]["google_genai"]["seconds"],
    }
    assert summary["input_tokens"] == 1_000_010
    assert summary["cost_known"] is False  # one call used a model with no price


def test_cost_uses_the_price_table(monkeypatch):
    monkeypatch.setitem(metrics.PRICES, "gemini-3.1-flash-lite", (0.10, 0.40))
    recorder = Metrics()
    finished_call(recorder, tokens=(1_000_000, 500_000))
    summary = summarize(recorder.calls)
    assert summary["cost_known"] is True
    assert abs(summary["cost_usd"] - 0.30) < 1e-9
    assert "~$0.30000" in describe(summary)


def test_node_times_are_recorded_by_the_wrapper():
    recorder = Metrics()
    wrapped = recorder.timed("router", lambda state: {"trace": ["router"]})
    assert wrapped({}) == {"trace": ["router"]}
    summary = summarize([], recorder.node_times)
    assert list(summary["node_seconds"]) == ["router"]


def test_mark_separates_one_email_from_the_next():
    recorder = Metrics()
    finished_call(recorder)
    calls_before, _ = recorder.mark()
    finished_call(recorder)
    assert len(recorder.calls[calls_before:]) == 1
