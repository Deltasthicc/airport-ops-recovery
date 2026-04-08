"""
inference.py -- Airport Operations Recovery
===========================================
Baseline agent using OpenAI Client. 6 tasks, 11 commands.

Required env vars:
  API_BASE_URL  -- LLM endpoint (default provided)
  MODEL_NAME    -- Model identifier (default provided)
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
API_BASE_URL = os.getenv("API_BASE_URL", "https://router.huggingface.co/v1")
MODEL_NAME = os.getenv("MODEL_NAME", "Qwen/Qwen2.5-72B-Instruct")
HF_TOKEN = os.getenv("HF_TOKEN")
if HF_TOKEN is None:
    raise ValueError("HF_TOKEN environment variable is required")
IMAGE_NAME = os.getenv("IMAGE_NAME", "airport-recovery")

# --- Config --------------------------------------------------
BENCHMARK = "airport_recovery"
TASKS = ["single_delay","cascading_delays","full_disruption",
         "international_hub","overnight_recovery","information_blackout"]
MAX_STEPS = {"single_delay":12,"cascading_delays":20,"full_disruption":30,
             "international_hub":25,"overnight_recovery":35,"information_blackout":28}
TEMPERATURE = 0.3
MAX_TOKENS = 120

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

CRITICAL RULES -- READ CAREFULLY:
1. GATE CONFLICTS: When a delayed inbound flight blocks an outbound flight's gate,
   move the OUTBOUND flight to an available gate. Example: if AA505 needs G1 but
   delayed AA101 is at G1, do: REASSIGN_GATE AA505 G5 (move AA505 to available G5).
   Do NOT move the delayed inbound -- it will leave when it departs.

2. SWAP_CREW: Syntax is SWAP_CREW <reserve_id> RESERVE <flight_needing_crew>.
   Example: SWAP_CREW CRW-30 RESERVE AA505. The reserve goes FROM "RESERVE" TO the flight.

3. REBOOK_PASSENGER: Rebook to a flight going to the SAME destination.
   Look at the passenger's missed connection destination, find a later flight to that city.

4. CANCEL_FLIGHT: ONLY cancel if REQUEST_INFO flight <id> shows MAINTENANCE ALERT.
   Never cancel without checking first.

5. BROADCAST_DELAY: Required for flights delayed >60min in some tasks.

6. Do NOT repeat REQUEST_INFO for the same subject multiple times. Read it once, act on it.

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

# --- Logging -------------------------------------------------
def log_start(task, env, model):
    print(f"[START] task={task} env={env} model={model}", flush=True)

def log_step(step, action, reward, done, error):
    print(f"[STEP] step={step} action={action.replace(chr(10),' ').strip()} "
          f"reward={reward:.2f} done={str(done).lower()} error={error or 'null'}", flush=True)

def log_end(success, steps, score, rewards):
    rewards_str = ",".join(f"{r:.2f}" for r in rewards)
    print(f"[END] success={str(success).lower()} steps={steps} "
          f"score={score:.2f} rewards={rewards_str}", flush=True)

# --- Prompt --------------------------------------------------
def build_prompt(step, obs, lr, hist):
    # Detect gate conflicts (two flights sharing same gate)
    gate_flights = {}
    for f in obs.flights:
        g = f['gate']
        if g != 'REMOTE' and g:
            gate_flights.setdefault(g, []).append(f['id'])
    conflicts = {g: fids for g, fids in gate_flights.items() if len(fids) > 1}

    fl = "\n".join(f"  {'[!]' if f['status'] in ('delayed','cancelled') else '  '}"
                   f"{f['id']}:{f['route']} gate={f['gate']} status={f['status']} "
                   f"delay={f['delay_min']}min crew={f['crew']} ac={f.get('aircraft','?')}"
                   for f in obs.flights)
    gl = "\n".join(f"  {g['id']}[{g['terminal']}]"
                   f"{'[wide]' if g.get('size','std')=='wide' else ''}: "
                   f"{g['status']}{' ('+g['flight']+')' if g.get('flight') else ''}"
                   f"{' cd='+str(g['cooldown']) if g.get('cooldown',0)>0 else ''}"
                   for g in obs.gates)
    avail_gates = [g['id'] + ('[wide]' if g.get('size','std')=='wide' else '')
                   for g in obs.gates if g['status'] == 'available']
    pl = "\n".join(f"  {p['id']}[{p.get('priority','std')}]: "
                   f"on={p['original_flight']} conn={p['connection']} [{p['status']}]"
                   for p in obs.passenger_issues if p['status'] == 'needs_rebooking')
    cl = "\n".join(f"  {c['id']}: flt={c['flight']} hrs={c['duty_hrs']} [{c['status']}]"
                   for c in obs.crew_issues if c['status'] in ('at_limit','exceeded','available'))

    conflict_str = ""
    if conflicts:
        conflict_str = "\n[!] GATE CONFLICTS (move the OUTBOUND scheduled flight to an available gate):\n"
        for g, fids in conflicts.items():
            delayed = [fid for fid in fids for f in obs.flights if f['id']==fid and f['status']=='delayed']
            scheduled = [fid for fid in fids for f in obs.flights if f['id']==fid and f['status']=='scheduled']
            if delayed and scheduled:
                conflict_str += f"  Gate {g}: {delayed[0]}(delayed) blocks {scheduled[0]}(scheduled) -> REASSIGN_GATE {scheduled[0]} <available_gate>\n"

    return (f"Step {step}|{obs.airport_status}|Weather:{obs.weather_severity}/5|"
            f"Cost:${obs.compensation_cost_usd:,.0f}|Tarmac:{obs.tarmac_violations}|"
            f"Last reward:{lr:+.2f}\n"
            f"AVAILABLE GATES: {', '.join(avail_gates) if avail_gates else 'NONE'}\n"
            f"{conflict_str}"
            f"\nFLIGHTS:\n{fl or'  None'}\nGATES:\n{gl or'  None'}\n"
            f"UNRESOLVED PASSENGERS:\n{pl or'  None (all rebooked)'}\n"
            f"CREW ISSUES:\n{cl or'  None'}\n"
            f"MSG:{obs.message[:400]}\n"
            f"HIST:{chr(10).join(hist[-3:]) if hist else'None'}\nCommand:")

# --- LLM -----------------------------------------------------
_fails = 0
def get_cmd(client, step, obs, lr, hist):
    global _fails
    try:
        r = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role":"system","content":SYSTEM_PROMPT},
                      {"role":"user","content":build_prompt(step,obs,lr,hist)}],
            temperature=TEMPERATURE, max_tokens=MAX_TOKENS, stream=False)
        t = (r.choices[0].message.content or "").strip().split("\n")[0].strip()
        _fails = 0
        return t if t else "REQUEST_INFO summary"
    except Exception as e:
        _fails += 1
        if "402" in str(e) or "429" in str(e) or "credit" in str(e).lower() or _fails >= 3:
            print(f"[DEBUG] API issue. Ending task.", flush=True)
            return "DONE"
        print(f"[DEBUG] LLM error ({_fails}/3): {e}", flush=True)
        return "REQUEST_INFO summary"

# --- Runner --------------------------------------------------
async def run_task(client, task):
    global _fails; _fails = 0
    env = await AirportRecoveryEnv.from_docker_image(IMAGE_NAME)
    hist, rewards, steps = [], [], 0
    score, success = 0.001, False
    log_start(task, BENCHMARK, MODEL_NAME)
    try:
        result = await env.reset(task=task)
        obs, lr = result.observation, 0.0
        for s in range(1, MAX_STEPS.get(task, 15) + 1):
            if result.done: break
            cmd = get_cmd(client, s, obs, lr, hist)
            result = await env.step(AirportAction(command=cmd))
            obs = result.observation
            r = result.reward or 0.0
            rewards.append(r); steps = s; lr = r
            log_step(s, cmd, r, result.done, None)
            hist.append(f"S{s}:{cmd}->{r:+.2f}")
            if result.done: break
        score = min(max(getattr(obs, "score", 0.001), 0.001), 0.999)
        success = score >= 0.3
    finally:
        try: await env.close()
        except: pass
        log_end(success, steps, score, rewards)

async def main():
    client = OpenAI(base_url=API_BASE_URL, api_key=HF_TOKEN)
    for task in TASKS:
        await run_task(client, task)

if __name__ == "__main__":
    asyncio.run(main())
