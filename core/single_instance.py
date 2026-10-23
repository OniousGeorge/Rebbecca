"""Makes sure only one copy of the agent runs at a time. Two copies would both answer the same email.

The operating system releases the lock when the process exits, even if it crashes.
"""
import os


class AlreadyRunning(RuntimeError):
    pass


def hold_lock(path):
    """Lock `path` for as long as this process runs. Keep the returned handle: closing it releases the lock."""
    handle = open(path, "a+")
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise AlreadyRunning("Another copy of the agent is already running (lock file: " + str(path) + ")")
    return handle
