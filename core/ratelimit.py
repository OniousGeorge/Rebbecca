"""Limits how many emails one sender can make the agent handle in a time window."""
import time


class SenderLimiter:
    def __init__(self, max_per_window=10, window_seconds=3600, clock=None):
        self.max_per_window = max_per_window
        self.window_seconds = window_seconds
        self.clock = clock or time.time
        self.handled = {}  # sender -> times of emails we handled
        self.notified = {}  # sender -> when we last told the owner about the limit

    def _recent(self, sender):
        """Times this sender was handled within the window; older ones are forgotten."""
        cutoff = self.clock() - self.window_seconds
        recent = []
        for moment in self.handled.get(sender, []):
            if moment > cutoff:
                recent.append(moment)
        self.handled[sender] = recent
        return recent

    def allow(self, sender):
        return len(self._recent(sender)) < self.max_per_window

    def record(self, sender):
        self._recent(sender).append(self.clock())

    def first_block(self, sender):
        """True only the first time a blocked sender is seen in a window, so the owner is told once."""
        last = self.notified.get(sender)
        if last is not None and self.clock() - last < self.window_seconds:
            return False
        self.notified[sender] = self.clock()
        return True
