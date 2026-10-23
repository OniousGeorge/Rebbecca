"""Turns a caught exception into a short note for the state, and logs its full traceback.

Nodes deliberately catch every exception around LLM calls, because any failure should end in a
handoff to a human, never a crash. Logging the traceback keeps real bugs visible.
"""
import logging

log = logging.getLogger("agent")


def record_failure(where, error):
    """Log the traceback and return a one-line note such as 'router: TimeoutError: timed out'."""
    log.error("%s failed", where, exc_info=error)
    return where + ": " + type(error).__name__ + ": " + str(error)

