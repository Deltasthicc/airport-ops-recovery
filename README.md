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

An OpenEnv RL environment simulating real-world airport irregular operations (IROPS) recovery. AI agents resolve gate conflicts, swap duty-limited crew, rebook stranded passengers, hold connections, broadcast delays, and manage cancellations under incomplete information, FAA regulations, weather pressure, tarmac limits, and evolving mid-episode events.

Airlines lose $60B+ annually to IROPS. This environment trains and evaluates AI agents on that exact problem.

## Why This Environment?

When a thunderstorm delays multiple flights at JFK, the disruption cascades instantly: gates get blocked by delayed inbound aircraft, crew exceed FAA 16-hour duty limits, passengers miss connections, and the FAA 3-hour tarmac rule starts ticking. Every recovery decision interacts with every other. Today this is managed by human coordinators under extreme pressure. This environment captures the full complexity and evaluates how well AI agents can handle it.

Key properties that make this a strong RL environment:
- Coupled decisions: reassigning a gate affects passengers, crews, and downstream flights
- Incomplete information: hidden maintenance issues, hidden connections (Task 6)
- Time pressure: clock advances 5 min per action, delays compound
- Triage required: in hard scenarios, you cannot resolve everything
- Dynamic events: weather changes, crew releases mid-episode

## Features

17 features across 6 progressive tasks:

- 6 tasks from Easy to Expert+ difficulty
- 11 action types (REASSIGN_GATE, REBOOK_PASSENGER, SWAP_CREW, DELAY_FLIGHT, CANCEL_FLIGHT, HOLD_CONNECTION, MOVE_TO_REMOTE, BROADCAST_DELAY, ESCALATE_TO_SUPERVISOR, REQUEST_INFO, DONE)
- Time simulation: clock advances 5 min per step
- Weather severity: 1-5 scale, changes via dynamic mid-episode events
- Passenger priority: VIP (2x), family (1.5x), unaccompanied_minor (2.5x)
- Gate-aircraft compatibility: wide-body aircraft only fit at [wide] gates
- Gate turnaround cooldown: 2-step cooldown after freeing a gate
- FAA tarmac rule: flights >180min on tarmac get penalty at episode end
- Compensation tracking: EU261-style costs ($150/rebook, $250/delay, $600/cancel per pax)
- Hidden information: Task 6 hides connections until agent queries them
- Broadcast requirements: Task 6 requires informing passengers of delays >60min
- Supervisor escalation: one-time hint about highest-priority action
- FAA regulations lookup: REQUEST_INFO regulations returns duty limits and rules
- Dynamic events: weather changes, crew releases, ground crew fatigue
- Efficiency bonus: +0.01 per unused step (max +0.10)
- 3-tier partial credit: 100%/75%/50% for rebooking accuracy
- Seeded reset: deterministic episodes for reproducibility

## Action Space

The agent sends text commands via AirportAction(command="..."):

- REASSIGN_GATE <flight> <gate> -- Move flight to gate (checks aircraft-gate compatibility)
- REBOOK_PASSENGER <pax> <flight> -- Rebook to later flight (same destination = full credit)
- SWAP_CREW <crew> <from> <to> -- Replace duty-limited crew with reserve
- DELAY_FLIGHT <flight> <minutes> -- Add delay (0-360 min, triggers compensation >3h)
- CANCEL_FLIGHT <flight> -- Cancel flight (penalised unless maintenance-required)
- HOLD_CONNECTION <flight> <minutes> -- Hold departure for connecting passengers
- MOVE_TO_REMOTE <flight> -- Park at remote stand, frees gate, +20min boarding
- BROADCAST_DELAY <flight> -- Broadcast delay info to passengers (required in Task 6)
- ESCALATE_TO_SUPERVISOR -- Get one-time hint about highest-priority action
- REQUEST_INFO <subject> -- Free query: gates, flights, crew, passengers, summary, costs, regulations, flight <ID>
- DONE -- Signal operations complete

## Observation Space

Each step returns an AirportObservation with these fields:

- current_time (str): Simulated clock, advances 5 min/step
- weather_severity (int): 1=clear to 5=extreme, changes mid-episode
- weather_description (str): Human-readable weather status
- flights (list): Each has id, route, gate, status, delay_min, crew, aircraft, pax, type, ac_size, tarmac_min
- gates (list): Each has id, terminal, status, flight, size (standard/wide), cooldown
- passenger_issues (list): Each has id, original_flight, connection, status, priority
- crew_issues (list): Each has id, flight, duty_hrs, status
- compensation_cost_usd (float): Running EU261-style compensation total
- tarmac_violations (int): Count of flights exceeding FAA 3-hour limit
- score (float): Current normalized score strictly in (0.01, 0.99)
- pending_issues_count (int): Issues still unresolved
- resolved_issues_count (int): Issues resolved so far
- total_issues_count (int): Total issues in this task
- message (str): Feedback on last action
- available_commands (list): All valid command formats

## 6 Tasks

Task 1 - single_delay (Easy, 12 steps, 4 issues):
Inbound AA101 delayed 90 min. Gate G3 blocked. 3 passengers miss connections.
Agent reassigns gate and rebooks to correct destinations.

Task 2 - cascading_delays (Medium, 20 steps, 11 issues):
Thunderstorm delays 4 flights. 3 gate conflicts, 6 stranded passengers,
2 crew at FAA duty limits needing reserve swaps. Dynamic weather event at step 8.

Task 3 - full_disruption (Hard, 30 steps, 17 issues):
Runway closed + weather + hidden maintenance on aircraft AC-801 + Terminal 3
security lockdown. Agent must discover maintenance via REQUEST_INFO, cancel
affected flight, resolve 3 gate conflicts, swap 3 crew, rebook 10 passengers.

Task 4 - international_hub (Expert, 25 steps, 10 issues):
Two transatlantic wide-body flights delayed. Wide-body aircraft can ONLY park
at [wide] gates. VIP and unaccompanied minor need priority rebooking. Agent
must use HOLD_CONNECTION. Weather escalates from severity 3 to 4 to 5.

Task 5 - overnight_recovery (Nightmare, 35 steps, 20 issues):
System-wide failure. 4 gate conflicts, 12 passengers (VIP/family/minor mix),
only 2 reserve crew for 4 crew issues, 2 mandatory cancellations (AC-950,
AC-951 maintenance). Agent must use MOVE_TO_REMOTE when gates run out.
Cannot save everyone -- must triage and prioritize.

Task 6 - information_blackout (Expert+, 28 steps, 13 issues):
Communications failure. Passenger connections HIDDEN until agent queries them.
Two flights approaching FAA 3-hour tarmac limit. Agent MUST broadcast delay
info for flights delayed >60min. Must discover information before acting.

## Reward Design

Rewards are given per-action (not just at episode end):

- Gate conflict resolved: +0.04 to +0.40 (varies by task)
- Passenger rebooked (correct flight): +0.03 to +0.20
- Passenger rebooked (correct dest, wrong flight): 75% credit
- Passenger rebooked (wrong destination): 50% credit
- Crew swap: +0.06 to +0.15
- Hold connection: +0.10 to +0.15
- Broadcast delay: +0.08 to +0.10
- Required cancellation: +0.07 to +0.11
- Move to remote (resolves conflict): Same as gate conflict points
- Unnecessary cancellation: -0.05
- Invalid / unknown command: -0.02
- Tarmac violation (at end): -0.03 each
- Efficiency bonus: +0.01/step saved (max +0.10)
- REQUEST_INFO / ESCALATE: Free (0.00)

Final score = clamp(total_reward / max_possible, 0.01, 0.99)

## Baseline Scores

Using Qwen2.5-72B-Instruct via HF Pro Inference:

- single_delay: 0.98-1.00 (gate + all pax)
- cascading_delays: 0.24-0.76 (gates + some pax/crew)
- full_disruption: 0.19-0.41 (gates + some crew, misses maintenance)
- international_hub: 0.10-0.26 (gate conflicts only)
- overnight_recovery: 0.00-0.12 (partial gates, gets stuck)
- information_blackout: 0.24-0.44 (some gates + pax, partial broadcasts)

## Setup and Usage

Test (zero dependencies):

    python test_environment.py

Generate lockfile:

    pip install uv
    uv lock

Validate:

    openenv validate

Run locally:

    pip install -r requirements.txt
    uvicorn server.app:app --host 0.0.0.0 --port 8000

Docker:

    docker build -t airport-recovery .
    docker run -d -p 8000:8000 --name airport-test airport-recovery
    curl http://localhost:8000/health

Run inference:

    export API_BASE_URL="https://router.huggingface.co/v1"
    export MODEL_NAME="Qwen/Qwen2.5-72B-Instruct"
    export HF_TOKEN="your_token"
    export IMAGE_NAME="airport-recovery"
    python inference.py

Deploy to HF Spaces:

    openenv push --repo-id YOUR_USERNAME/airport-recovery

## API Endpoints

- GET  /health -- Returns {"status": "healthy"}
- POST /reset  -- Reset environment. Body: {"task": "single_delay"}
- POST /step   -- Take action. Body: {"action": {"command": "REQUEST_INFO summary"}}
- GET  /state  -- Get current state
- WS   /ws     -- WebSocket for inference client

## Project Structure

    __init__.py             -- Package exports (client + models)
    models.py               -- Pydantic types (Action, Observation, State)
    client.py               -- WebSocket EnvClient
    server/
        __init__.py
        app.py              -- FastAPI wiring + main() entry point
        environment.py      -- Core logic (11 commands, all features)
        scenarios.py        -- 6 task definitions
    inference.py            -- Baseline LLM agent (OpenAI Client)
    test_environment.py     -- 177-check test suite across 33 sections
    Dockerfile              -- Container (python:3.11-slim)
    requirements.txt        -- Dependencies
    openenv.yaml            -- OpenEnv manifest
    pyproject.toml          -- Package metadata

## Hardware

Runs within 2 vCPU / 8 GB RAM. Pure Python, no GPU, no heavy ML deps.
Inference runtime under 20 minutes for all 6 tasks.
