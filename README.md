---
title: Airport Operations Recovery
colorFrom: blue
colorTo: red
sdk: docker
app_port: 8000
tags:
  - openenv
---

# Airport Operations Recovery System

An OpenEnv RL environment simulating real-world airport irregular operations (IROPS) recovery. AI agents must resolve gate conflicts, swap duty-limited crew, rebook stranded passengers, hold connections, broadcast delays, and manage cancellations -- under incomplete information, FAA regulations, weather pressure, tarmac limits, and evolving mid-episode events.

Airlines lose **$60B+ annually** to IROPS. This environment trains and evaluates AI agents on that exact problem.

## Why Airport Ops Recovery?

This is not a game. When a thunderstorm delays 4 flights at JFK, the disruption cascades instantly: gates get blocked by delayed inbound aircraft, crew exceed FAA 16-hour duty limits, passengers miss connections, and the FAA 3-hour tarmac rule starts ticking. Every recovery decision interacts with every other. Today this is handled by human coordinators under extreme pressure. This environment captures the full complexity and evaluates how well AI agents can handle it.

## Features

| Feature | Description |
|---------|-------------|
| **6 progressive tasks** | Easy -> Medium -> Hard -> Expert -> Nightmare -> Blackout |
| **11 action types** | REASSIGN_GATE, REBOOK_PASSENGER, SWAP_CREW, DELAY_FLIGHT, CANCEL_FLIGHT, HOLD_CONNECTION, MOVE_TO_REMOTE, BROADCAST_DELAY, ESCALATE_TO_SUPERVISOR, REQUEST_INFO, DONE |
| **Time simulation** | Clock advances 5 min per step |
| **Weather severity** | 1-5 scale, changes via dynamic mid-episode events |
| **Passenger priority** | VIP, family, unaccompanied_minor -- weighted scoring |
| **Gate-aircraft compatibility** | Wide-body aircraft only fit at [wide] gates |
| **Gate turnaround cooldown** | 2-step cooldown after freeing a gate |
| **FAA tarmac rule** | Flights >180min on tarmac get penalty at episode end |
| **Compensation tracking** | EU261-style: $150/rebook, $250/delay, $600/cancel per pax |
| **Hidden information** | Task 6: connections hidden until agent queries them |
| **Broadcast requirements** | Task 6: must inform passengers of delays >60min |
| **Supervisor escalation** | One-time hint about highest-priority action |
| **FAA regulations lookup** | REQUEST_INFO regulations returns duty limits and tarmac rules |
| **Dynamic events** | Weather changes, crew releases, ground crew fatigue |
| **Efficiency bonus** | +0.01 per unused step (max +0.10) |
| **3-tier partial credit** | 100% / 75% / 50% for rebooking accuracy |
| **Seeded reset** | Deterministic episodes for reproducibility |

## Action Space

| Command | Description |
|---------|-------------|
| `REASSIGN_GATE <flight> <gate>` | Move flight to gate (checks aircraft-gate compatibility) |
| `REBOOK_PASSENGER <pax> <flight>` | Rebook to later flight (same destination = full credit) |
| `SWAP_CREW <crew> <from> <to>` | Replace duty-limited crew with reserve |
| `DELAY_FLIGHT <flight> <minutes>` | Add delay (0-360 min, triggers compensation >3h) |
| `CANCEL_FLIGHT <flight>` | Cancel flight (penalised unless maintenance-required) |
| `HOLD_CONNECTION <flight> <minutes>` | Hold departure for connecting passengers |
| `MOVE_TO_REMOTE <flight>` | Park at remote stand -- frees gate, +20min boarding |
| `BROADCAST_DELAY <flight>` | Broadcast delay info to passengers (required in Task 6) |
| `ESCALATE_TO_SUPERVISOR` | Get one-time hint about highest-priority action |
| `REQUEST_INFO <subject>` | Free query: gates, flights, crew, passengers, summary, costs, regulations, flight ID |
| `DONE` | Signal operations complete |

## Observation Space

Each step returns JSON with: `current_time`, `weather_severity`, `weather_description`, `flights[]` (with aircraft size, tarmac time), `gates[]` (with size, cooldown), `passenger_issues[]` (with priority, hidden status), `crew_issues[]`, `compensation_cost_usd`, `tarmac_violations`, `score`, `message`, `pending_issues_count`.

## 6 Tasks

| # | Task | Difficulty | Steps | Issues | Key Challenge |
|---|------|-----------|-------|--------|---------------|
| 1 | `single_delay` | Easy | 12 | 4 | 1 gate + 3 passengers |
| 2 | `cascading_delays` | Medium | 20 | 11 | 3 gates + 6 pax + 2 crew + weather |
| 3 | `full_disruption` | Hard | 30 | 17 | Hidden maintenance + security + 3 crew swaps |
| 4 | `international_hub` | Expert | 25 | 10 | Wide-body compat + HOLD_CONNECTION + VIP/minor |
| 5 | `overnight_recovery` | Nightmare | 35 | 20 | 2 cancellations + MOVE_TO_REMOTE + triage |
| 6 | `information_blackout` | Expert+ | 28 | 13 | Hidden connections + 3 broadcasts + tarmac timer |

## Reward Design

| Outcome | Reward |
|---------|--------|
| Gate conflict resolved | +0.04 to +0.40 |
| Passenger rebooked (correct) | +0.03 to +0.20 |
| Rebook correct dest, wrong flight | 75% |
| Rebook wrong destination | 50% |
| Crew swap | +0.06 to +0.15 |
| Hold connection | +0.10 to +0.15 |
| Broadcast delay | +0.08 to +0.10 |
| Required cancellation | +0.07 to +0.11 |
| Unnecessary cancellation | -0.05 |
| Invalid command | -0.02 |
| Tarmac violation (at end) | -0.03 each |
| Efficiency bonus | +0.01/step saved (max +0.10) |
| REQUEST_INFO / ESCALATE | Free |

## Baseline Scores

Using Qwen2.5-72B-Instruct:

| Task | Score | Notes |
|------|-------|-------|
| single_delay | ~0.70 | Gate + partial pax |
| cascading_delays | ~0.49 | Gates + pax, misses crew |
| full_disruption | ~0.18 | Gates only, misses maintenance |
| international_hub | TBD | Needs HOLD_CONNECTION awareness |
| overnight_recovery | TBD | Needs triage + MOVE_TO_REMOTE |
| information_blackout | TBD | Needs discovery + broadcast |

## Setup & Usage

```bash
# 1. Test (zero deps)
python test_environment.py

# 2. Lock + validate
pip install uv && uv lock
openenv validate

# 3. Docker
docker build -t airport-recovery .
docker run -d -p 8000:8000 airport-recovery

# 4. Inference (requires API key)
export HF_TOKEN="your_key"
export IMAGE_NAME="airport-recovery"
python inference.py

# 5. Deploy
openenv push --repo-id YOUR_USERNAME/airport-recovery
```

## Project Structure

```
|-- models.py              <- Pydantic types (Action, Observation, State)
|-- client.py              <- WebSocket EnvClient
|-- server/
|   |-- app.py             <- FastAPI + main() entry point
|   |-- environment.py     <- Core logic (11 commands, all features)
|   |-- scenarios.py       <- 6 task definitions
|-- inference.py           <- Baseline LLM agent (OpenAI Client)
|-- test_environment.py    <- 57-check test suite across 19 sections
|-- Dockerfile             <- Container (python:3.11-slim)
|-- requirements.txt       <- Dependencies
|-- openenv.yaml           <- OpenEnv manifest
|-- pyproject.toml         <- Package metadata
|-- .dockerignore / .gitignore
```

## Hardware

Runs within 2 vCPU / 8 GB RAM. Pure Python, no GPU, no heavy ML deps.
