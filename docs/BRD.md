# Business Requirements Document (BRD)
**Project:** HVAC Lead Intake & Booking Agent (Project 1)
**Version:** 0.1 (draft) | **Status:** Draft for review | **Owner:** Project owner

## 1. Purpose
Define the business need, scope, and success criteria for an AI agent that handles inbound customer email for a small HVAC service business: responding instantly, qualifying the lead, booking an appointment, logging it, and handing off to a human when needed.

The project is also a portfolio demonstration for a custom-AI-agent role (KD Tech's "missed-call follow-up" use case), built entirely on free tools with synthetic data.

## 2. Background and problem
Small service businesses (HVAC, plumbing, salons) lose revenue when inquiries go unanswered while the owner or staff are on a job. Customers who do not get a quick reply often call a competitor. Manual follow-up is inconsistent, bookings are handled by hand, and lead information ends up scattered across email threads.

## 3. Business objectives
| ID | Objective |
|---|---|
| BO-1 | Respond to every inbound inquiry within minutes, at any hour |
| BO-2 | Convert qualified inquiries into scheduled appointments without staff effort |
| BO-3 | Capture every lead and booking in one place (the CRM sheet) |
| BO-4 | Route urgent, unclear, or sensitive cases to a human quickly and with full context |
| BO-5 | Never give wrong information (invented hours, prices, or double-booked slots) |
| BO-6 | Be reconfigurable for a different business by editing configuration, not code |

## 4. Scope
### In scope (v1 demo)
- Inbound customer email to one company inbox (Gmail)
- Classifying messages: new lead, FAQ, reschedule, cancel, escalate, spam
- Collecting lead details over a multi-email conversation
- Offering and booking appointment slots (local JSON calendar)
- Logging leads and bookings to Google Sheets
- Escalation notice by email to a named individual
- Per-client configuration (YAML)
- Automated test scenarios, token/latency tracking, cost-per-conversation figure

### Out of scope (v1)
- Voice, SMS, chat widgets, Telegram
- Real Google Calendar integration (stretch goal)
- Payments, invoicing, quoting, dispatch/technician routing
- Real customer data (synthetic only)
- Durable hosting, multi-tenant deployment, admin UI (Phase 2)

## 5. Stakeholders and users
| Role | Interest |
|---|---|
| Business owner | More booked jobs, less time on admin, trust that the agent will not mislead customers |
| Office/dispatch staff | Clear lead records and fast escalations with context |
| Customer | Quick, accurate answers and easy scheduling |
| Interviewer / evaluator | Evidence of reliable, explainable, cost-aware agent engineering |
| Project owner (developer) | A defensible, well-documented project that is understood end to end |

## 6. Business requirements
| ID | Requirement | Priority |
|---|---|---|
| BR-1 | The agent shall reply to each valid inbound email in the same thread | Must |
| BR-2 | The agent shall classify each message and handle FAQ, new lead, reschedule, cancel, and escalation paths | Must |
| BR-3 | The agent shall collect name, contact details, service address or area, issue description, and urgency before booking | Must |
| BR-4 | The agent shall offer only open slots within business hours and shall never double-book | Must |
| BR-5 | The agent shall answer questions about hours, services, areas, and prices only from approved business configuration; otherwise it shall escalate | Must |
| BR-6 | The agent shall log each lead and booking to Google Sheets | Must |
| BR-7 | The agent shall notify a named person by email when a case is escalated, including a summary and the conversation | Must |
| BR-8 | Safety emergencies (for example a gas smell) shall be escalated immediately and the customer told to contact emergency services | Must |
| BR-9 | The agent shall ignore auto-replies, no-reply senders, and its own messages, and shall never answer the same email twice | Must |
| BR-10 | Business-specific content (name, tone, hours, services, FAQs, contacts, timezone) shall come from configuration | Must |
| BR-11 | The system shall fall back to a second LLM provider when the primary is rate-limited or unavailable | Should |
| BR-12 | The system shall record token usage and latency per conversation and report an estimated cost per conversation | Should |
| BR-13 | Urgent jobs shall be flagged in the CRM log | Should |
| BR-14 | Real Google Calendar can replace the local calendar without changing agent logic | Could |

## 7. Success criteria (targets for the demo)
| Metric | Target |
|---|---|
| Scenario test pass rate (about 15 multi-turn scenarios, 3 runs each) | 90% or higher; failures documented |
| Double-bookings in testing | 0 |
| Answers containing invented hours/prices | 0 |
| Emergency scenarios escalated correctly | 100% |
| Duplicate replies after restart | 0 |
| Time from inbound email to first reply | Under 2 minutes (demo conditions) |
| Estimated cost per conversation | Reported, using paid-tier rates for the model used |
| Cost to build and run | $0 |

These are targets, not measurements. Actual results will be recorded in the README.

## 8. Assumptions
- One business, one inbox, one calendar for the demo
- Customers write in English
- All data is synthetic; no real personal data is processed
- Free LLM tiers (Gemini, Groq) are available with sufficient limits for demo volume
- Google Cloud project, OAuth consent, and a dedicated demo Gmail account can be set up

## 9. Constraints
- Free tools only; no paid services
- Built by one person, with Claude Code assistance, on a short timeline (see schedule in `kd-tech-interview-plan-v2.md`)
- Gmail OAuth in "Testing" status requires re-authorization about every 7 days
- Free LLM tiers are rate-limited and their terms may allow use of inputs for training

## 10. Risks and mitigations
| Risk | Impact | Mitigation |
|---|---|---|
| LLM misroutes or invents information | Wrong replies, lost trust | Constrained outputs, guardrails in code, YAML-only facts, scenario tests |
| Free-tier rate limits or outages | Agent stops replying | Provider failover, fallback "we received your message" reply, escalation |
| Email reply loops | Spam, quota exhaustion | Loop guard on no-reply/auto-reply/own address; idempotent processing |
| OAuth token expiry before a demo | Demo fails | Re-authorize before demo; checklist item |
| Privacy of customer data | Compliance exposure | Synthetic data only in v1; local model or paid terms before real data |
| Scope too large for the timeline | Unfinished demo | Cut order defined: Calendar stretch, polish, failover. Never cut tests, README, BREAKLOG |

## 11. Dependencies
Gmail API, Google Sheets API, Gemini API, Groq API, LangGraph/LangChain libraries, a Google Cloud project with OAuth consent configured.

## 12. Phase 2 (after the interview, if pursued)
Free always-on hosting; durable state (SQLite/Postgres); one config and credential set per client; retries and alerts; paid LLM fallback and cost tracking; real Google Calendar; a two-week pilot with a friendly small business; legal review of any SMS/voice expansion.

## 13. Glossary
- **Agent:** an LLM wrapped in code that gives it a job, memory, and tools.
- **Orchestration:** code that controls which step runs next and how steps share information.
- **Escalation:** handing a conversation to a human.
- **CRM:** the record of leads and bookings (a Google Sheet in v1).
