"""LLM factory: one primary model and one automatic backup (Gemini and Groq).

Nodes never build models themselves. They call get_llm() and use the result.
Set LLM_PRIMARY=groq in .env to make Groq the primary and Gemini the backup.
"""
import os
from concurrent.futures import ThreadPoolExecutor

import groq
import httpx
from dotenv import load_dotenv
from google.genai import errors as genai_errors
from langchain_core.exceptions import ModelError, OutputParserException
from langchain_core.runnables import Runnable, RunnableLambda
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_google_genai.chat_models import GoogleGenerativeAIError
from langchain_groq import ChatGroq
from pydantic import BaseModel, ValidationError

from core import metrics

REQUIRED_ENV = ("GEMINI_KEY", "GEMINI_NAME", "GROQ_KEY", "GROQ_NAME")
TIMEOUT_SECONDS = 15  # passed to the provider libraries
DEADLINE_SECONDS = 20  # enforced by us, because a library timeout was once ignored (a call hung 486 s)

# Calls run in these threads so we can stop waiting for them. A call that is given up on keeps
# running in its thread until the provider answers or its own timeout fires; its result is ignored.
_deadline_threads = ThreadPoolExecutor(max_workers=16, thread_name_prefix="llm-call")

# Only these failures switch to the backup model: provider, network, quota and bad-output errors.
# A bug in our own code (TypeError, KeyError, ...) is raised instead of being hidden by a fallback.
PROVIDER_FAILURES = (
    ModelError,  # LangChain's standard model errors: rate limit, timeout, auth, not found, server
    OutputParserException,  # the model's answer did not fit the schema
    ValidationError,
    GoogleGenerativeAIError,
    genai_errors.APIError,
    groq.GroqError,
    httpx.HTTPError,  # network failures and timeouts
    TimeoutError,
    ConnectionError,
)


def _check_env() -> None:
    for name in REQUIRED_ENV:
        if not os.getenv(name):
            raise RuntimeError(f"Missing environment variable in .env: {name}")


def _gemini(temperature=0, max_retries=1, timeout=None, api_key=None, callbacks=None):
    # The key is passed explicitly because our .env names are not the library defaults.
    if api_key is None:
        api_key = os.environ["GEMINI_KEY"]
    return ChatGoogleGenerativeAI(
        model=os.environ["GEMINI_NAME"],
        api_key=api_key,
        temperature=temperature,
        max_retries=max_retries,
        timeout=timeout,
        callbacks=callbacks,
    )


def _groq(temperature=0, max_retries=1, timeout=None, api_key=None, callbacks=None):
    if api_key is None:
        api_key = os.environ["GROQ_KEY"]
    return ChatGroq(
        model=os.environ["GROQ_NAME"],
        api_key=api_key,
        temperature=temperature,
        max_retries=max_retries,
        timeout=timeout,
        callbacks=callbacks,
    )


def primary_name():
    name = os.getenv("LLM_PRIMARY", "gemini").strip().lower()
    if name not in ("gemini", "groq"):
        raise RuntimeError("LLM_PRIMARY must be 'gemini' or 'groq', not '" + name + "'")
    return name


def with_deadline(runnable, seconds):
    """Stop waiting for `runnable` after `seconds` by raising TimeoutError, so the backup can answer."""

    def call(value, config):
        future = _deadline_threads.submit(runnable.invoke, value, config)
        try:
            return future.result(timeout=seconds)
        except TimeoutError:
            future.cancel()
            raise TimeoutError("no answer within " + str(seconds) + " seconds")

    return RunnableLambda(call, name="deadline")


def with_backup(primary, backup, schema: type[BaseModel] | None = None, deadline=None) -> Runnable:
    """Combine two models so the backup answers if the primary raises an error.

    When a schema is given, structured output is applied to EACH model first.
    Otherwise the backup would return plain text instead of a schema object.
    With a deadline, a model that is merely slow also counts as failed.
    """
    if schema is not None:
        primary = primary.with_structured_output(schema)
        backup = backup.with_structured_output(schema)
    if deadline is not None:
        primary = with_deadline(primary, deadline)
        backup = with_deadline(backup, deadline)
    return primary.with_fallbacks([backup], exceptions_to_handle=PROVIDER_FAILURES)


def build_models(temperature: float = 0):
    """(primary, backup) chat models in the order LLM_PRIMARY asks for."""
    # max_retries=1: fail over quickly instead of retrying a rate-limited provider.
    # callbacks: every call is recorded in core.metrics (time, tokens, failures).
    gemini = _gemini(temperature=temperature, max_retries=1, timeout=TIMEOUT_SECONDS, callbacks=[metrics.recorder])
    groq_model = _groq(temperature=temperature, max_retries=1, timeout=TIMEOUT_SECONDS, callbacks=[metrics.recorder])
    if primary_name() == "groq":
        return groq_model, gemini
    return gemini, groq_model


def get_llm(schema: type[BaseModel] | None = None, temperature: float = 0) -> Runnable:
    load_dotenv()
    _check_env()
    primary, backup = build_models(temperature)
    return with_backup(primary, backup, schema, deadline=DEADLINE_SECONDS)


if __name__ == "__main__":
    # Manual check against the real APIs: python -m core.llm
    import sys

    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to cp1252
    load_dotenv()
    _check_env()
    question = "In one sentence, what does an HVAC technician do?"

    # .text gives the answer as a plain string; .content can be a list of blocks.
    print("GEMINI :", _gemini(temperature=0).invoke(question).text)
    print("GROQ   :", _groq(temperature=0).invoke(question).text)

    # Break the primary on purpose with a bad key and confirm Groq answers.
    broken = _gemini(api_key="bad-key", max_retries=0)
    chain = with_backup(broken, _groq(temperature=0))
    print("FAILOVER:", chain.invoke(question).text)
