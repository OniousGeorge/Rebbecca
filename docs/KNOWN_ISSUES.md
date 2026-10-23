# Known issues and gaps

First found 2026-10-21 while building and live-testing; reviewed and worked through 2026-10-22. Fixed items keep a one-line note; their stories are in `BREAKLOG.md`.

Priority: **H** = fix before any demo or real use, **M** = fix before the interview, **L** = nice to have / Phase 2.

## Still open

- [ ] **M: Privacy of real customer emails.** Not solvable in code. Gemini's free tier may use inputs to improve Google's products (Google's pricing page); Groq states it does not train on API data. With Groq as primary, Gemini only sees an email when Groq fails. *Decision needed before any real customer uses the inbox:* keep synthetic/consented use only, or switch the backup to a paid tier or a local model.
- [ ] **M: Gemini's free-tier rate limits are unknown.** Google only shows them in AI Studio (account-specific). Check them there. Groq's are known: 1,000 requests/day and 8,000 tokens/minute for `openai/gpt-oss-120b` (read from Groq's response headers), roughly 7 emails a minute or ~500 a day.
- [ ] **M: Gemini is an unreliable backup.** In the evaluation, 40% of Gemini calls failed (mostly `503` "high demand" on the free tier). With Groq as primary this only matters when Groq also fails; then the customer is handed to a person. *Options:* a paid Gemini tier, or a second Groq model as backup.
- [ ] **L: No way to hand a conversation back to the agent.** After a handoff the agent stays silent on that thread for good. *Phase 2:* a Gmail label the owner removes to hand it back.
- [ ] **L: Rate-limit counts live in memory.** A restart resets the per-sender and daily counts (conversations themselves now survive restarts).
- [ ] **L: Urgency does not notify anyone.** "emergency" urgency (no heat/cooling) now gets today's slots first, but the owner is not alerted unless it is a safety emergency.
- [ ] **L: Out-of-area customers are only logged,** not sent to the owner. Fine for the demo.
- [ ] **L: Sheets upsert reads then writes.** Safe with the single-instance lock; not safe for several processes writing one Sheet.
- [ ] **L: `trace` grows for the life of a conversation.** Decided to keep it as the conversation's history (useful in tests and debugging); it is bounded by the per-sender limit.

## Fixed 2026-10-22

### Behavior
- [x] **H: Emergency phrases missed common wordings.** `core/safety.py`: ordered words, up to 2 between, common endings; 24 wordings and 12 lookalikes tested; reply now covers smoke and fire too.
- [x] **M: Customer's preferred time was ignored.** Intake extracts the day and morning/afternoon (today's date given to the model, validated in code); scheduler offers from that day and says plainly when it is not possible; "same day" urgency offers today first; a named day also answers the urgency question.
- [x] **M: Cancel happened immediately.** Two steps; YES/NO read by code.
- [x] **M: Agent kept replying after an escalation.** `handed_off`: later emails are forwarded to the owner, no auto-reply; safety replies still go out.
- [x] **M: FAQ answered by inference.** Decided: strict. Anything the config does not state goes to a person (the spec's rule). One-line prompt change to reverse.
- [x] **L: Replies ignored Reply-To.** Used when on the sender's own domain; ignored otherwise so the open inbox cannot relay replies to strangers.

### Open inbox
- [x] **H: No per-sender limit** (fixed 2026-10-21): 10 emails per sender per hour.
- [x] **M: No total limit.** 200 emails per 24 hours across all senders; the owner is told once when it is reached.

### Performance and cost
- [x] **H: Latency.** Cause: each Gemini call took 5 to 12.5 s; each Groq call 0.5 to 0.9 s; the graph itself ~0 s. Fix: `LLM_PRIMARY=groq`.
- [x] **New, H: a model call could hang for minutes.** One Gemini call took 486 s despite the 15 s library timeout. Fix: our own 20 s deadline on every call in `core/llm.py`. Per-email numbers for both providers are in `evals/results/`.
- [x] **M: No token, latency or cost metrics.** `core/metrics.py`: every call and node recorded; one log line per email; paid-tier cost from published prices.
- [x] **M: Free-tier limits unverified.** Groq's checked (above). Gemini's are only visible in AI Studio (still open, above).

### Testing
- [x] **M: Too few live tests.** `evals/`: 17 multi-email scenarios with checks, run 3 times on the real models.
- [x] **M: No run with Groq as primary.** The evaluation runs with either primary (`--primary groq|gemini`).

### Robustness
- [x] **L: Fallback caught every exception.** Now only provider failures (`PROVIDER_FAILURES` in `core/llm.py`).
- [x] **L: Bare `except` around LLM calls hid bugs.** Still deliberate (any failure hands off to a person), but `core/failures.py` now logs the full traceback and records the error type.
- [x] **L: CSV not safe for concurrent writers.** Sheets is the main log now; only one agent can run (lock file).
- [x] **L: In-memory checkpointer.** SQLite (`data/conversations.sqlite`); a test restarts the app mid-conversation.
- [x] **New: two agents could run against one inbox.** `core/single_instance.py` lock.

### Documentation
- [x] **M: `docs/TECH_SPEC.md` behind the code.** Rewritten to match.
- [x] **M: `CLAUDE.md` and `kd-tech-interview-plan-v2.md` described Telegram.** Updated.
- [x] **L: `lessons/02_first_llm_call.py` passed the key as the model name.** Fixed.
