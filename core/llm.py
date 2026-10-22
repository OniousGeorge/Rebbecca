"""LLM factory: Gemini as primary, Groq as automatic backup.

Nodes never build models themselves. They call get_llm() and use the result.
"""
import os

from dotenv import load_dotenv
from langchain_core.runnables import Runnable
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq
from pydantic import BaseModel

REQUIRED_ENV = ("GEMINI_KEY", "GEMINI_NAME", "GROQ_KEY", "GROQ_NAME")
TIMEOUT_SECONDS = 15


def _check_env() -> None:
    for name in REQUIRED_ENV:
        if not os.getenv(name):
            raise RuntimeError(f"Missing environment variable in .env: {name}")


def _gemini(temperature=0, max_retries=1, timeout=None, api_key=None):
    # The key is passed explicitly because our .env names are not the library defaults.
    if api_key is None:
        api_key = os.environ["GEMINI_KEY"]
    return ChatGoogleGenerativeAI(
        model=os.environ["GEMINI_NAME"],
        api_key=api_key,
        temperature=temperature,
        max_retries=max_retries,
        timeout=timeout,
    )


def _groq(temperature=0, max_retries=1, timeout=None, api_key=None):
    if api_key is None:
        api_key = os.environ["GROQ_KEY"]
    return ChatGroq(
        model=os.environ["GROQ_NAME"],
        api_key=api_key,
        temperature=temperature,
        max_retries=max_retries,
        timeout=timeout,
    )


def with_backup(primary, backup, schema: type[BaseModel] | None = None) -> Runnable:
    """Combine two models so the backup answers if the primary raises an error.

    When a schema is given, structured output is applied to EACH model first.
    Otherwise the backup would return plain text instead of a schema object.
    """
    if schema is not None:
        primary = primary.with_structured_output(schema)
        backup = backup.with_structured_output(schema)
    return primary.with_fallbacks([backup])


def get_llm(schema: type[BaseModel] | None = None, temperature: float = 0) -> Runnable:
    load_dotenv()
    _check_env()
    # max_retries=1: fail over quickly instead of retrying a rate-limited provider.
    # timeout: a provider that is merely slow counts as failed, so the backup answers.
    primary = _gemini(temperature=temperature, max_retries=1, timeout=TIMEOUT_SECONDS)
    backup = _groq(temperature=temperature, max_retries=1, timeout=TIMEOUT_SECONDS)
    return with_backup(primary, backup, schema)


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
