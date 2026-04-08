"""
Inference Script -- Airport Operations Recovery
================================================
MANDATORY:
- API_BASE_URL, MODEL_NAME have defaults
- HF_TOKEN read from env (no default)
- IMAGE_NAME for from_docker_image()
- Uses OpenAI Client
- Stdout: [START], [STEP], [END] format exactly

STDOUT FORMAT:
    [START] task=<task_name> env=<benchmark> model=<model_name>
    [STEP]  step=<n> action=<action_str> reward=<0.00> done=<true|false> error=<msg|null>
    [END]   success=<true|false> steps=<n> score=<score> rewards=<r1,r2,...,rn>
"""

import asyncio
import os
import textwrap
from typing import List, Optional

from openai import OpenAI

from client import AirportRecoveryEnv
from models import AirportAction

# --- Environment Variables (matching sample exactly) ----------
IMAGE_NAME = os.getenv("IMAGE_NAME")  # If you are using docker image
API_KEY = os.getenv("HF_TOKEN") or os.getenv("API_KEY")

API_BASE_URL = os.getenv("API_BASE_URL") or "https://router.huggingface.co/v1"
MODEL_NAME = os.getenv("MODEL_NAME") or "Qwen/Qwen2.5-72B-Instruct"

# --- Config ---------------------------------------------------
BENCHMARK = "airport_recovery"
TASKS = [
    "single_delay",
    "cascading_delays",
    "full_disruption",
    "international_hub",
    "overnight_recovery",
    "information_blackout",
]
MAX_STEPS = {
    "single_delay": 12,
    "cascading_delays": 20,
    "full_disruption": 30,
    "international_hub": 25,
    "overnight_recovery": 35,
    "information_blackout": 28,
}
TEMPERATURE = 0.3
MAX_TOKENS = 120
SUCCESS_SCORE_THRESHOLD = 0.3

SYSTEM_PROMPT = textwrap.dedent("""\
You are an expert airport operations recovery coordinator.

COMMANDS (exactly ONE per turn, nothing else):
  REASSIGN_GATE <flight_id> <gate_id>
  REBOOK_PASSENGER <passenger_id> <new_flight_id>
  SWAP_CREW <reserve_crew_id> <from_assignment> <to_flight_id>
  DELAY_FLIGHT <flight_id> <minutes>
  CANCEL_FLIGHT <flight_id>
  HOLD_CONNECTION <departing_flight> <minutes>
  MOVE_TO_REMOTE <flight_id>
  BROADCAST_DELAY <flight_id>
  ESCALATE_TO_SUPERVISOR
  REQUEST_INFO <subject>
  DONE

CRITICAL RULES:
1. GATE CONFLICTS: When a delayed inbound flight blocks an outbound flight's gate,
   move the OUTBOUND flight to an available gate. Example: if AA505 needs G1 but
   delayed AA101 is at G1, do: REASSIGN_GATE AA505 G5 (move AA505 to available G5).
   Do NOT move the delayed inbound.

2. SWAP_CREW: Syntax is SWAP_CREW <reserve_id> RESERVE <flight_needing_crew>.
   Example: SWAP_CREW CRW-30 RESERVE AA505.

3. REBOOK_PASSENGER: Rebook to a flight going to the SAME destination.

4. CANCEL_FLIGHT: ONLY cancel if REQUEST_INFO flight <id> shows MAINTENANCE ALERT.

5. BROADCAST_DELAY: Required for flights delayed >60min in some tasks.

6. Do NOT repeat REQUEST_INFO for the same subject. Read once, act on it.

EFFICIENT STRATEGY (minimize steps):
  Step 1: REQUEST_INFO summary
  Step 2: REQUEST_INFO gates (note available gates)
  Steps 3-5: REASSIGN_GATE for each blocked outbound flight -> available gate
  Step 6: REQUEST_INFO crew (if crew issues exist)
  Steps 7-8: SWAP_CREW for each duty-limited crew
  Step 9: REQUEST_INFO passengers
  Steps 10+: REBOOK_PASSENGER each stranded passenger to correct destination
  Then: BROADCAST_DELAY for delayed flights, HOLD_CONNECTION if needed
  Finally: DONE

Use exact IDs from observations. One command per response. No explanation or quotes.
""")


# --- Logging (matches required stdout format EXACTLY) ---------

def log_start(task: str, env: str, model: str) -> None:
    print(f"[START] task={task} env={env} model={model}", flush=True)


def log_step(step: int, action: str, reward: float, done: bool, error: Optional[str]) -> None:
    action_clean = action.replace("\n", " ").strip()
    done_val = str(done).lower()
    error_val = error if error else "null"
    print(
        f"[STEP] step={step} action={action_clean} reward={reward:.2f} "
        f"done={done_val} error={error_val}",
        flush=True,
    )


def log_end(success: bool, steps: int, score: float, rewards: List[float]) -> None:
    rewards_str = ",".join(f"{r:.2f}" for r in rewards)
    print(
        f"[END] success={str(success).lower()} steps={steps} "
        f"score={score:.2f} rewards={rewards_str}",
        flush=True,
    )


# --- Prompt Builder -------------------------------------------

def build_user_prompt(step, obs, last_reward, history):
    """Build prompt with conflict detection for the LLM."""
    # Detect gate conflicts (two flights sharing same gate)
    gate_flights = {}
    for f in obs.flights:
        g = f['gate']
        if g and g != 'REMOTE':
            gate_flights.setdefault(g, []).append(f['id'])
    conflicts = {g: fids for g, fids in gate_flights.items() if len(fids) > 1}

    fl = "\n".join(
        f"  {'[!]' if f['status'] in ('delayed','cancelled') else '   '}"
        f"{f['id']}:{f['route']} gate={f['gate']} status={f['status']} "
        f"delay={f['delay_min']}min crew={f['crew']} ac={f.get('aircraft','?')}"
        for f in obs.flights
    )
    gl = "\n".join(
        f"  {g['id']}[{g['terminal']}]"
        f"{'[wide]' if g.get('size','std')=='wide' else ''}: "
        f"{g['status']}{' ('+g['flight']+')' if g.get('flight') else ''}"
        f"{' cd='+str(g['cooldown']) if g.get('cooldown',0)>0 else ''}"
        for g in obs.gates
    )
    avail_gates = [
        g['id'] + ('[wide]' if g.get('size','std')=='wide' else '')
        for g in obs.gates if g['status'] == 'available'
    ]
    pl = "\n".join(
        f"  {p['id']}[{p.get('priority','std')}]: "
        f"on={p['original_flight']} conn={p['connection']} [{p['status']}]"
        for p in obs.passenger_issues if p['status'] == 'needs_rebooking'
    )
    cl = "\n".join(
        f"  {c['id']}: flt={c['flight']} hrs={c['duty_hrs']} [{c['status']}]"
        for c in obs.crew_issues
        if c['status'] in ('at_limit', 'exceeded', 'available')
    )

    conflict_str = ""
    if conflicts:
        conflict_str = "\n[!] GATE CONFLICTS (move the OUTBOUND scheduled flight to available gate):\n"
        for g, fids in conflicts.items():
            delayed = [fid for fid in fids
                       for f in obs.flights if f['id'] == fid and f['status'] == 'delayed']
            scheduled = [fid for fid in fids
                         for f in obs.flights if f['id'] == fid and f['status'] == 'scheduled']
            if delayed and scheduled:
                conflict_str += (
                    f"  Gate {g}: {delayed[0]}(delayed) blocks {scheduled[0]}(scheduled)"
                    f" -> REASSIGN_GATE {scheduled[0]} <available_gate>\n"
                )

    return (
        f"Step {step}|{obs.airport_status}|Weather:{obs.weather_severity}/5|"
        f"Cost:${obs.compensation_cost_usd:,.0f}|Tarmac:{obs.tarmac_violations}|"
        f"Last reward:{last_reward:+.2f}\n"
        f"AVAILABLE GATES: {', '.join(avail_gates) if avail_gates else 'NONE'}\n"
        f"{conflict_str}"
        f"\nFLIGHTS:\n{fl or '  None'}\nGATES:\n{gl or '  None'}\n"
        f"UNRESOLVED PASSENGERS:\n{pl or '  None (all rebooked)'}\n"
        f"CREW ISSUES:\n{cl or '  None'}\n"
        f"MSG:{obs.message[:400]}\n"
        f"HIST:{chr(10).join(history[-3:]) if history else 'None'}\nCommand:"
    )


# --- LLM Interaction ------------------------------------------

_consecutive_failures = 0


def get_model_command(client, step, obs, last_reward, history):
    """Ask the LLM for the next command. Handles API failures gracefully."""
    global _consecutive_failures
    prompt = build_user_prompt(step, obs, last_reward, history)

    try:
        completion = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS,
            stream=False,
        )
        text = (completion.choices[0].message.content or "").strip()
        command = text.split("\n")[0].strip()
        _consecutive_failures = 0
        return command if command else "REQUEST_INFO summary"

    except Exception as exc:
        _consecutive_failures += 1
        err_msg = str(exc)
        if "402" in err_msg or "429" in err_msg or "credit" in err_msg.lower():
            print(f"[DEBUG] API credits exhausted. Ending task.", flush=True)
            return "DONE"
        if _consecutive_failures >= 3:
            print(f"[DEBUG] 3 consecutive failures. Ending task.", flush=True)
            return "DONE"
        print(f"[DEBUG] LLM error ({_consecutive_failures}/3): {exc}", flush=True)
        return "REQUEST_INFO summary"


# --- Task Runner ----------------------------------------------

async def run_task(client: OpenAI, task_name: str) -> None:
    """Run a single task episode with full logging."""
    global _consecutive_failures
    _consecutive_failures = 0

    max_steps = MAX_STEPS.get(task_name, 15)
    env = await AirportRecoveryEnv.from_docker_image(IMAGE_NAME)

    history: List[str] = []
    rewards: List[float] = []
    steps_taken = 0
    score = 0.0
    success = False

    log_start(task=task_name, env=BENCHMARK, model=MODEL_NAME)

    try:
        result = await env.reset(task=task_name)
        obs = result.observation
        last_reward = 0.0

        for step_num in range(1, max_steps + 1):
            if result.done:
                break

            command = get_model_command(client, step_num, obs, last_reward, history)

            result = await env.step(AirportAction(command=command))
            obs = result.observation
            reward = result.reward or 0.0
            done = result.done
            error = None

            rewards.append(reward)
            steps_taken = step_num
            last_reward = reward

            log_step(step=step_num, action=command, reward=reward, done=done, error=error)
            history.append(f"S{step_num}: {command} -> {reward:+.2f}")

            if done:
                break

        score = getattr(obs, "score", 0.0)
        score = min(max(score, 0.0), 1.0)
        success = score >= SUCCESS_SCORE_THRESHOLD

    finally:
        try:
            await env.close()
        except Exception as e:
            print(f"[DEBUG] env.close() error: {e}", flush=True)

        log_end(success=success, steps=steps_taken, score=score, rewards=rewards)


# --- Main -----------------------------------------------------

async def main() -> None:
    client = OpenAI(base_url=API_BASE_URL, api_key=API_KEY)

    for task_name in TASKS:
        await run_task(client, task_name)


if __name__ == "__main__":
    asyncio.run(main())
