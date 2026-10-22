# Known issues and gaps

Found while building and live-testing (2026-10-21). Not yet fixed. When one is fixed, move its story to `BREAKLOG.md` and tick it here.

Priority: **H** = fix before any demo or real use, **M** = fix before the interview, **L** = nice to have / Phase 2.

## Behavior

- [ ] **H: Emergency phrase list misses common wordings.** The code-level safety check matches "smell gas", "gas leak", "smells like gas", "smell of gas". It would miss "gas smell", "smelling gas", "I can smell natural gas", "CO detector going off". The router LLM may still choose `escalate`, but safety should not rely on that. *Fix:* widen the phrase list (or match "gas" near "smell/leak/odor"), add tests for 10+ variants, and add false-positive tests ("gas furnace", "gas bill").
- [ ] **M: Customer's preferred time is ignored.** "Today if possible" got the next three slots with no explanation (it was evening, so the earliest was tomorrow 8 AM). Scheduler never reads the customer's requested day or time. *Fix:* extract a requested date/time as structured output, validate it in code, offer slots near it, and say plainly when same-day is not available (CLAUDE.md "Dates" rule).
- [ ] **M: Cancel happens immediately, with no confirmation.** CLAUDE.md says cancel should confirm first. *Fix:* two-step cancel (`active_flow = "cancel"`, ask "Cancel your Thu 9:30 visit? Reply YES").
- [ ] **M: After an escalation the agent keeps replying on that thread.** The spec says handoff should stop automated replies. *Fix:* a `human_handoff` flag per thread that makes the router return no reply until a human clears it.
- [ ] **M: FAQ answers by inference.** "Do you repair boat engines?" was answered "we do not" from the services list instead of escalating as not-in-config. Reasonable, but the rule says answer only what the facts state. *Decision needed:* allow clear negatives from the services list, or escalate everything not stated.
- [ ] **L: Urgency only affects the log.** "emergency" and "same_day" urgency do not change slot order or notify anyone.
- [ ] **L: Out-of-area customers are only logged.** No owner notice; fine for the demo.

## Open inbox (decision made 2026-10-21: anyone may email the agent)

- [x] **H: No limit on how many replies one sender can trigger.** Fixed 2026-10-21: `core/ratelimit.py`; 10 handled emails per sender per hour, then the agent stays silent and tells the owner once. Remaining gap: the counts live in memory, so a restart resets them.
- [ ] **M: Anyone can make the agent spend LLM quota and write to the calendar and log.** The per-sender cap helps, but many different senders can still add up. *Fix:* a daily total cap.
- [ ] **M: Privacy.** Real strangers' emails go to free-tier LLMs whose terms may allow training on inputs (see CLAUDE.md "Secrets and data"). Only use the open inbox with synthetic or consented content.
- [ ] **L: Replies go to the From address, not Reply-To.**

## Performance and cost

- [ ] **H: Latency is high.** Live run: 4 to 25 seconds per email (one email took 25 s for about three LLM calls), although a single lite-model call measured about 1 s. Cause unknown. *Investigate:* per-node and per-provider timings; whether Gemini hits the 15 s timeout and falls over to Groq; free-tier rate limiting. *Options:* skip the router LLM when a flow is open and the email is a plain slot choice, merge router and intake calls, shorter prompts, Groq as primary.
- [ ] **M: No token, latency or cost metrics.** `core/metrics.py` from the spec is not built, so the "cost per conversation" number does not exist yet.
- [ ] **M: Free-tier limits unverified.** Gemini `gemini-3.1-flash-lite` and Groq `openai/gpt-oss-120b` limits and terms not checked against expected demo volume.

## Testing

- [ ] **M: Only 12 live router cases and one live conversation.** The plan calls for about 15 multi-turn scenarios, each run 3 times, with a pass rate. Mid-flow topic changes and `continues_active_flow` are barely covered live.
- [ ] **M: Scenario suite for Groq as the active model.** Everything live so far ran mostly on Gemini. Run the same scenarios with Groq forced as primary to see if results differ.

## Robustness (mostly Phase 2)

- [ ] **L: `with_fallbacks` catches every exception**, including programming bugs. Narrow to rate-limit, timeout and server errors.
- [ ] **L: Router, intake, FAQ and scheduler catch bare `Exception` around LLM calls.** Intentional (any failure should hand off to a human) but hides bugs. Consider logging the traceback.
- [ ] **L: `trace` grows for the life of a thread.** Fine for tests; decide whether to reset per run or keep as history.
- [ ] **L: CSV log is not safe for concurrent writers.** Fine for one process; the Sheets adapter replaces it.
- [ ] **L: In-memory checkpointer.** Conversation memory is lost on restart (Phase 2: SQLite or Postgres).

## Documentation debt

- [ ] **M: `docs/TECH_SPEC.md` is behind the code.** Missing: escalation always flows into the logger, the `out_of_area` route, the `booking` flow value, `reschedule_of`, per-run field resets, `core/messages.py`, `app.py`, `chat.py`, the adapters actually built (`calendar_json`, `csv_log`, `console_mail`).
- [ ] **M: `CLAUDE.md` status checklist and `kd-tech-interview-plan-v2.md`** still describe Telegram and unfinished items.
- [ ] **L: `lessons/02_first_llm_call.py`** still passes the API key as the model name (scratch file, gitignored).
