"""
inference.py -- Airport Operations Recovery
===========================================
Baseline agent using OpenAI Client.
Runs all 5 tasks with [START]/[STEP]/[END] stdout format.

Required env vars:
  API_BASE_URL  -- LLM endpoint (has default)
  MODEL_NAME    -- Model identifier (has default)
  HF_TOKEN      -- API key (required, no default)
"""

import asyncio
import os
import textwrap
from typing import List, Optional

from openai import OpenAI

from client import AirportRecoveryEnv
from models import AirportAction

# --- Environment Variables (per submission guidelines) -------

IMAGE_NAME = os.getenv("IMAGE_NAME")  # If you are using docker image
API_KEY = os.getenv("HF_TOKEN") or os.getenv("API_KEY")

API_BASE_URL = os.getenv("API_BASE_URL") or "https://router.huggingface.co/v1"
MODEL_NAME = os.getenv("MODEL_NAME") or "Qwen/Qwen2.5-72B-Instruct"

# --- Config --------------------------------------------------

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
MAX_TOKENS = 150
SUCCESS_THRESHOLD = 0.3

SYSTEM_PROMPT = textwrap.dedent("""\
You are an expert airport operations recovery coordinator.

COMMANDS (respond with exactly ONE per turn, no extra text):
  REASSIGN_GATE <flight_id> <gate_id>
  REBOOK_PASSENGER <passenger_id> <new_flight_id>
  SWAP_CREW <crew_id> <from_flight> <to_flight>
  DELAY_FLIGHT <flight_id> <minutes>
  CANCEL_FLIGHT <flight_id>
  HOLD_CONNECTION <departing_flight> <minutes>
  MOVE_TO_REMOTE <flight_id>
  BROADCAST_DELAY <flight_id>
  ESCALATE_TO_SUPERVISOR
  REQUEST_INFO <subject>
  DONE

STRATEGY (follow this priority order):
1. ESCALATE_TO_SUPERVISOR -- get a hint on the most urgent action.
2. REQUEST_INFO summary -- understand the big picture.
3. REQUEST_INFO flights -- check for wide-body aircraft and high delays.
4. Fix gate conflicts: REASSIGN_GATE (check gate size for wide-body!). If no gates, MOVE_TO_REMOTE.
5. REQUEST_INFO crew -- find duty-limited crew. SWAP_CREW with reserve crew.
6. REQUEST_INFO passengers -- prioritize VIP, unaccompanied_minor, family. REBOOK to SAME destination.
7. If a flight uses suspicious aircraft (AC-801, AC-950, AC-951): REQUEST_INFO flight <id>, then CANCEL if maintenance.
8. HOLD_CONNECTION if a departure can wait for connecting passengers.
9. BROADCAST_DELAY for any significantly delayed flight.
10. DONE when all issues resolved.

RULES:
- Use exact IDs from the observation. One command per response.
- Wide-body aircraft ONLY fit at [wide] gates. Check before assigning.
- Gates have cooldown after being freed -- wait 2 steps before reusing.
- Rebook passengers to flights going to the SAME destination as their missed connection.
- Only cancel flights when aircraft has maintenance issue.
- No explanation, no quotes, just the command.
""")


# --- Logging -------------------------------------------------

def log_start(task: str, env: str, model: str) -> None:
    print(f"[START] task={task} env={env} model={model}", flush=True)


def log_step(step: int, action: str, reward: float,
             done: bool, error: Optional[str]) -> None:
    action_clean = action.replace("\n", " ").strip()
    done_str = str(done).lower()
    error_str = error if error else "null"
    print(f"[STEP] step={step} action={action_clean} "
          f"reward={reward:.2f} done={done_str} error={error_str}", flush=True)


def log_end(success: bool, steps: int, score: float,
            rewards: List[float]) -> None:
    rewards_str = ",".join(f"{r:.2f}" for r in rewards)
    print(f"[END] success={str(success).lower()} steps={steps} "
          f"score={score:.2f} rewards={rewards_str}", flush=True)


# --- Prompt Builder ------------------------------------------

def build_user_prompt(step, obs, last_reward, history):
    flights = "\n".join(
        f"  {f['id']}: {f['route']} gate={f['gate']} status={f['status']} "
        f"delay={f['delay_min']}min crew={f['crew']} ac={f.get('aircraft','?')} "
        f"type={f.get('type','dom')} size={f.get('ac_size','narrow')} "
        f"notified={f.get('notified',False)}"
        for f in obs.flights)
    gates = "\n".join(
        f"  {g['id']}[{g['terminal']}]"
        f"{'[wide]' if g.get('size','standard')=='wide' else ''}: "
        f"{g['status']}"
        f"{' ('+g['flight']+')' if g.get('flight') else ''}"
        f"{' cd='+str(g['cooldown']) if g.get('cooldown',0)>0 else ''}"
        for g in obs.gates)
    passengers = "\n".join(
        f"  {p['id']}[{p.get('priority','std')}]: "
        f"flight={p['original_flight']} conn={p['connection']} [{p['status']}]"
        for p in obs.passenger_issues)
    crew = "\n".join(
        f"  {c['id']}: flight={c['flight']} hrs={c['duty_hrs']} [{c['status']}]"
        for c in obs.crew_issues)
    history_block = "\n".join(history[-5:]) if history else "None"
    tarmac = ", ".join(obs.tarmac_alerts) if obs.tarmac_alerts else "None"

    return (
        f"Step {step} | {obs.airport_status}\n"
        f"Weather:{obs.weather_severity}/5 | "
        f"Cost:${obs.compensation_cost_usd:,.0f} | "
        f"Last:{last_reward:+.2f} | Tarmac alerts:{tarmac}\n\n"
        f"FLIGHTS:\n{flights or '  None'}\n\n"
        f"GATES:\n{gates or '  None'}\n\n"
        f"PASSENGERS:\n{passengers or '  None'}\n\n"
        f"CREW:\n{crew or '  None'}\n\n"
        f"FEEDBACK: {obs.message[:600]}\n\n"
        f"HISTORY:\n{history_block}\n\n"
        f"Your command:")


# --- LLM Interaction ----------------------------------------

_consecutive_failures = 0


def get_model_command(client, step, obs, last_reward, history):
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
            print("[DEBUG] API credits exhausted. Ending task.", flush=True)
            return "DONE"
        if _consecutive_failures >= 3:
            print("[DEBUG] 3 consecutive failures. Ending task.", flush=True)
            return "DONE"
        print(f"[DEBUG] LLM error ({_consecutive_failures}/3): {exc}", flush=True)
        return "REQUEST_INFO summary"


# --- Task Runner ---------------------------------------------

async def run_task(client: OpenAI, task_name: str) -> None:
    global _consecutive_failures
    _consecutive_failures = 0

    max_steps = MAX_STEPS.get(task_name, 15)
    env = await AirportRecoveryEnv.from_docker_image(IMAGE_NAME)

    history: List[str] = []
    rewards: List[float] = []
    steps_taken = 0
    score = 0.01
    success = False

    log_start(task=task_name, env=BENCHMARK, model=MODEL_NAME)

    try:
        result = await env.reset(task=task_name)
        obs = result.observation
        last_reward = 0.0

        for step_num in range(1, max_steps + 1):
            if result.done:
                break

            command = get_model_command(
                client, step_num, obs, last_reward, history)

            result = await env.step(AirportAction(command=command))
            obs = result.observation
            reward = result.reward or 0.0
            done = result.done
            error = None

            rewards.append(reward)
            steps_taken = step_num
            last_reward = reward

            log_step(step=step_num, action=command,
                     reward=reward, done=done, error=error)
            history.append(f"S{step_num}: {command} -> {reward:+.2f}")

            if done:
                break

        score = getattr(obs, "score", 0.0)
        score = min(max(score, 0.01), 0.99)
        success = score >= SUCCESS_THRESHOLD

    finally:
        try:
            await env.close()
        except Exception as e:
            print(f"[DEBUG] env.close() error: {e}", flush=True)

        log_end(success=success, steps=steps_taken,
                score=score, rewards=rewards)


# --- Main ----------------------------------------------------

async def main() -> None:
    client = OpenAI(base_url=API_BASE_URL, api_key=API_KEY)
    for task_name in TASKS:
        await run_task(client, task_name)


if __name__ == "__main__":
    asyncio.run(main())
