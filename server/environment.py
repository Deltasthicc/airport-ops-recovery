"""
Airport Operations Recovery -- Enhanced Environment
===================================================
Features:
  Core: REASSIGN_GATE, REBOOK_PASSENGER, SWAP_CREW, DELAY_FLIGHT, CANCEL_FLIGHT
  Advanced: HOLD_CONNECTION, MOVE_TO_REMOTE
  New: BROADCAST_DELAY, ESCALATE_TO_SUPERVISOR
  Info: REQUEST_INFO (gates/flights/crew/passengers/summary/costs/regulations/flight ID)
  Mechanics: time sim, weather severity, gate compatibility, gate cooldown,
             compensation costs, passenger priority, dynamic events,
             tarmac timer (FAA 3h rule), delay notification tracking, hints
"""
import copy, uuid
from typing import Any, Dict, List, Optional, Tuple
from openenv.core.env_server import Environment
from models import AirportAction, AirportObservation, AirportState
from server.scenarios import SCENARIOS, ALL_TASK_NAMES

COMP_DELAY_PER_PAX = 250
COMP_CANCEL_PER_PAX = 600
COMP_REBOOK_PER_PAX = 150
TARMAC_LIMIT_MINUTES = 180  # FAA 3-hour tarmac rule

FAA_REGULATIONS = """FAA & DOT Regulations Reference:
  - Crew duty limit: Max 8-10 flight hours per duty period (14 CFR Section117)
  - Tarmac delay rule: Carriers must return pax to terminal after 3h on domestic,
    4h on international flights (49 USC Section42301)
  - Compensation: DOT requires rebooking on next available flight at no extra cost.
    EU261 (transatlantic): EUR250-600 per pax for cancellations/long delays.
  - Unaccompanied minors: Must be supervised at all times. Priority rebooking required.
  - Security incidents: Terminal closures require TSA clearance before reopening.
  - Wide-body gates: Aircraft > 45m wingspan require wide-body stands (ICAO Code E/F)."""

class AirportRecoveryEnvironment(Environment):
    SUPPORTS_CONCURRENT_SESSIONS = True
    AVAILABLE_COMMANDS = [
        "REASSIGN_GATE <flight_id> <gate_id>",
        "REBOOK_PASSENGER <passenger_id> <new_flight_id>",
        "SWAP_CREW <crew_id> <from_flight> <to_flight>",
        "DELAY_FLIGHT <flight_id> <minutes>",
        "CANCEL_FLIGHT <flight_id>",
        "HOLD_CONNECTION <departing_flight> <minutes>",
        "MOVE_TO_REMOTE <flight_id>",
        "BROADCAST_DELAY <flight_id>  (notify passengers of delay status)",
        "ESCALATE_TO_SUPERVISOR  (get strategic hint -- free, once per episode)",
        "REQUEST_INFO <subject>  (gates/flights/crew/passengers/summary/costs/regulations/flight ID)",
        "DONE",
    ]
    MINUTES_PER_STEP = 5
    GATE_COOLDOWN_STEPS = 2

    def __init__(self):
        self._state = AirportState()
        self._flights = {}; self._gates = {}; self._passengers = {}; self._crew = {}
        self._issues = {}; self._resolved = {}
        self._score = 0.0; self._max_score = 1.0; self._max_steps = 12
        self._clock_minutes = 0; self._start_hour = 14; self._start_min = 0
        self._description = ""; self._done = False; self._used_reserve_crew = set()
        self._fired_events = set(); self._weather_severity = 1; self._weather_desc = ""
        self._compensation_usd = 0.0; self._gate_cooldowns = {}
        self._remote_flights = set()
        self._broadcast_flights = set()     # Flights whose passengers have been notified
        self._escalation_used = False       # Once-per-episode supervisor hint
        self._dynamic_events = []
        self._hint = ""

    # -- RESET ------------------------------------------------
    def reset(self, seed=None, episode_id=None, **kwargs) -> AirportObservation:
        task_name = kwargs.get("task", ALL_TASK_NAMES[0])
        if task_name not in SCENARIOS:
            task_name = ALL_TASK_NAMES[0]
        sc = SCENARIOS[task_name]
        self._load(sc, episode_id)
        return self._obs(
            f"DISRUPTION ALERT -- {sc['disruption_type'].upper().replace('_',' ')}\n"
            f"{self._description}\n\n"
            f"Weather: {self._weather_desc} (severity {self._weather_severity}/5)\n"
            f"Steps: {self._max_steps} | Clock: {self._fmt()} (+{self.MINUTES_PER_STEP}min/step)\n"
            f"Issues to resolve: {self._state.total_issues}\n\n"
            f"Tip: Use REQUEST_INFO regulations to review FAA rules.\n"
            f"Tip: Use ESCALATE_TO_SUPERVISOR for a strategic hint (once per episode).")

    def _load(self, sc, eid=None):
        self._flights = {f["flight_id"]: copy.deepcopy(f) for f in sc["flights"]}
        self._gates = {g["gate_id"]: copy.deepcopy(g) for g in sc["gates"]}
        self._passengers = {p["passenger_id"]: copy.deepcopy(p) for p in sc["passengers"]}
        self._crew = {c["crew_id"]: copy.deepcopy(c) for c in sc["crew"]}
        self._issues = copy.deepcopy(sc["issues"])
        self._resolved = {"gate_conflicts": set(), "passenger_rebookings": set(),
                          "crew_swaps": set(), "cancellations": set(), "held_connections": set()}
        self._score = 0; self._max_score = sc["max_score"]; self._max_steps = sc["max_steps"]
        self._description = sc["description"]; self._done = False
        self._used_reserve_crew = set(); self._fired_events = set()
        self._gate_cooldowns = {}; self._remote_flights = set()
        self._broadcast_flights = set(); self._escalation_used = False; self._hint = ""
        self._weather_severity = sc.get("weather_severity", 1)
        self._weather_desc = sc.get("weather_desc", "Clear")
        self._compensation_usd = 0; self._clock_minutes = 0
        self._dynamic_events = sc.get("dynamic_events", [])
        h, m = sc["current_time"].split(":")
        self._start_hour = int(h); self._start_min = int(m)
        total = sum(len(self._issues.get(k, [])) for k in
                    ("gate_conflicts", "passenger_rebookings", "crew_swaps",
                     "cancellations_needed", "held_connections"))
        self._state = AirportState(
            episode_id=eid or str(uuid.uuid4()), step_count=0,
            task_name=sc["task_name"], disruption_type=sc["disruption_type"],
            total_flights=len(self._flights), resolved_issues=0,
            total_issues=total, max_steps=self._max_steps)

    def _fmt(self):
        t = self._start_hour * 60 + self._start_min + self._clock_minutes
        return f"{(t // 60) % 24:02d}:{t % 60:02d}"

    # -- STEP -------------------------------------------------
    def step(self, action: AirportAction, timeout_s=None, **kwargs) -> AirportObservation:
        if self._done:
            return self._obs("Episode ended.", force_done=True)

        self._state.step_count += 1
        self._clock_minutes += self.MINUTES_PER_STEP
        self._tick_cooldowns()

        cmd = action.command.strip()
        events = self._check_events()
        reward, msg = self._exec(cmd)
        self._score += reward

        if events:
            msg = "".join(f"[!] {e}\n" for e in events) + "\n" + msg

        # Check tarmac alerts
        tarmac = self._get_tarmac_alerts()
        if tarmac:
            msg += "\n\n[!!] TARMAC ALERTS: " + ", ".join(tarmac)

        nr = self._nresolved()
        done_all = nr >= self._state.total_issues
        done_steps = self._state.step_count >= self._max_steps
        done_cmd = cmd.upper().startswith("DONE")

        if done_all or done_steps or done_cmd:
            self._done = True
            bonus = min((self._max_steps - self._state.step_count) * 0.01, 0.10) if done_all else 0
            self._score += bonus
            fs = min(max(self._score / self._max_score, 0.01), 0.99) if self._max_score > 0 else 0.01
            if done_all:
                msg += (f"\n\n[OK] All resolved in {self._state.step_count} steps! "
                        f"Bonus:+{bonus:.2f} Score:{fs:.2f} Cost:${self._compensation_usd:,.0f}")
            elif done_cmd:
                msg += f"\n\nAgent DONE. Score:{fs:.2f} Cost:${self._compensation_usd:,.0f}"
            else:
                msg += f"\n\n[TIME] Out of steps. Score:{fs:.2f} Cost:${self._compensation_usd:,.0f}"

        self._state.resolved_issues = nr
        return self._obs(msg, reward=reward)

    def _tick_cooldowns(self):
        expired = [gid for gid, r in self._gate_cooldowns.items() if r <= 1]
        for gid in expired:
            del self._gate_cooldowns[gid]
            if gid in self._gates and self._gates[gid]["status"] == "cooldown":
                self._gates[gid]["status"] = "available"
        for gid in self._gate_cooldowns:
            self._gate_cooldowns[gid] -= 1

    def _get_tarmac_alerts(self):
        """FAA tarmac rule: flights delayed >= 180min need attention."""
        alerts = []
        for fid, f in self._flights.items():
            if f["delay_minutes"] >= TARMAC_LIMIT_MINUTES and f["status"] not in ("cancelled", "departed"):
                alerts.append(f"{fid}({f['delay_minutes']}min)")
        return alerts

    def _check_events(self):
        msgs = []
        for ev in self._dynamic_events:
            step = ev.get("step")
            if step == self._state.step_count and step not in self._fired_events:
                self._fired_events.add(step)
                if ev["type"] in ("weather_update", "weather_escalation"):
                    self._weather_severity = ev.get("severity", self._weather_severity)
                    self._weather_desc = ev.get("desc", "")
                    msgs.append(f"WEATHER: {ev['desc']} (severity {self._weather_severity}/5)")
                elif ev["type"] == "crew_release":
                    cid = ev.get("crew_id")
                    if cid and cid in self._crew:
                        c = self._crew[cid]
                        fl = self._flights.get(c["assigned_flight"], {})
                        if fl.get("status") == "cancelled":
                            c["assigned_flight"] = "RESERVE"
                            c["status"] = "available"
                            c["duty_hours_remaining"] = 6.0
                            msgs.append(f"CREW: {cid} now available as reserve (6h).")
                        else:
                            msgs.append(ev.get("desc", ""))
                    else:
                        msgs.append(ev.get("desc", ""))
                else:
                    msgs.append(ev.get("desc", "Event occurred."))
        return msgs

    # -- DISPATCH ---------------------------------------------
    def _exec(self, cmd):
        u = cmd.upper().strip()
        if u.startswith("REASSIGN_GATE"):       return self._cmd_gate(cmd)
        if u.startswith("REBOOK_PASSENGER"):    return self._cmd_rebook(cmd)
        if u.startswith("SWAP_CREW"):           return self._cmd_crew(cmd)
        if u.startswith("DELAY_FLIGHT"):        return self._cmd_delay(cmd)
        if u.startswith("CANCEL_FLIGHT"):       return self._cmd_cancel(cmd)
        if u.startswith("HOLD_CONNECTION"):      return self._cmd_hold(cmd)
        if u.startswith("MOVE_TO_REMOTE"):      return self._cmd_remote(cmd)
        if u.startswith("BROADCAST_DELAY"):     return self._cmd_broadcast(cmd)
        if u.startswith("ESCALATE_TO_SUPERVISOR"): return self._cmd_escalate()
        if u.startswith("REQUEST_INFO"):        return self._cmd_info(cmd)
        if u.startswith("DONE"):                return (0, "Finishing operations.")
        return (-0.02, "Unknown command. Use REQUEST_INFO for help.")

    # -- REASSIGN_GATE ----------------------------------------
    def _cmd_gate(self, cmd):
        p = cmd.split()
        if len(p) < 3: return (-0.02, "Usage: REASSIGN_GATE <flight_id> <gate_id>")
        fid, gid = p[1].upper(), p[2].upper()
        if fid not in self._flights: return (-0.02, f"Flight {fid} not found.")
        if gid not in self._gates: return (-0.02, f"Gate {gid} not found.")
        g = self._gates[gid]; f = self._flights[fid]
        if g["status"] == "blocked": return (-0.02, f"Gate {gid} blocked (security).")
        if g["status"] == "cooldown":
            return (-0.02, f"Gate {gid} in turnaround cooldown ({self._gate_cooldowns.get(gid, 0)} steps).")
        if g["status"] == "occupied" and g["assigned_flight"] != fid:
            return (-0.02, f"Gate {gid} occupied by {g['assigned_flight']}.")
        if f.get("aircraft_size") == "wide_body" and g.get("size", "standard") != "wide":
            return (-0.02, f"Gate {gid} too small for wide-body {f['aircraft_id']}. Need [wide] gate.")
        old = f["gate"]
        if old in self._gates and self._gates[old]["assigned_flight"] == fid:
            self._gates[old]["status"] = "cooldown"
            self._gates[old]["assigned_flight"] = None
            self._gate_cooldowns[old] = self.GATE_COOLDOWN_STEPS
        f["gate"] = gid; g["status"] = "occupied"; g["assigned_flight"] = fid
        r = 0
        for iss in self._issues["gate_conflicts"]:
            if iss["flight_to_move"] == fid and fid not in self._resolved["gate_conflicts"]:
                self._resolved["gate_conflicts"].add(fid); r = iss["points"]; break
        m = f"{fid}: {old}->{gid}."
        if r > 0: m += f" [OK] Gate conflict resolved! (+{r:.2f})"
        return (r, m)

    # -- REBOOK_PASSENGER -------------------------------------
    def _cmd_rebook(self, cmd):
        p = cmd.split()
        if len(p) < 3: return (-0.02, "Usage: REBOOK_PASSENGER <pax_id> <flight_id>")
        pid, nf = p[1].upper(), p[2].upper()
        if pid not in self._passengers: return (-0.02, f"Passenger {pid} not found.")
        if nf not in self._flights: return (-0.02, f"Flight {nf} not found.")
        pax = self._passengers[pid]
        if pax["status"] == "rebooked": return (-0.01, f"{pid} already rebooked.")
        tf = self._flights[nf]
        if tf["status"] == "cancelled": return (-0.02, f"Flight {nf} cancelled.")
        orig_conn = pax.get("connection_flight")
        orig_dest = self._flights[orig_conn]["destination"] if orig_conn and orig_conn in self._flights else None
        new_dest = tf["destination"]
        r = 0
        for iss in self._issues["passenger_rebookings"]:
            if iss["passenger_id"] == pid and pid not in self._resolved["passenger_rebookings"]:
                if nf in iss["valid_flights"]:
                    r = iss["points"]
                elif orig_dest and new_dest == orig_dest:
                    r = iss["points"] * 0.75
                else:
                    r = iss["points"] * 0.5
                self._resolved["passenger_rebookings"].add(pid); break
        pax["status"] = "rebooked"; pax["connection_flight"] = nf
        self._compensation_usd += COMP_REBOOK_PER_PAX
        pri = pax.get("priority", "standard")
        m = f"{pid}[{pri}]->{nf} ({tf['origin']}->{tf['destination']})."
        if r > 0:
            m += f" [OK] (+{r:.2f})" if (orig_dest and new_dest == orig_dest) else f" [!] suboptimal (+{r:.2f})"
        return (r, m)

    # -- SWAP_CREW --------------------------------------------
    def _cmd_crew(self, cmd):
        p = cmd.split()
        if len(p) < 4: return (-0.02, "Usage: SWAP_CREW <crew_id> <from> <to>")
        cid, frm, to = p[1].upper(), p[2].upper(), p[3].upper()
        if cid not in self._crew: return (-0.02, f"Crew {cid} not found.")
        if to not in self._flights: return (-0.02, f"Flight {to} not found.")
        c = self._crew[cid]
        if c["status"] in ("at_limit", "exceeded"):
            return (-0.02, f"Crew {cid} at/exceeded duty limit ({c['duty_hours_remaining']}h).")
        if c["duty_hours_remaining"] < 1 and c["status"] != "available":
            return (-0.02, f"Crew {cid} insufficient hours.")
        if cid in self._used_reserve_crew:
            return (-0.02, f"Crew {cid} already reassigned this episode.")
        old = c["assigned_flight"]; c["assigned_flight"] = to
        self._flights[to]["crew_id"] = cid
        if c["status"] == "available" and old == "RESERVE":
            self._used_reserve_crew.add(cid)
        r = 0
        for iss in self._issues["crew_swaps"]:
            if iss["flight"] == to and to not in self._resolved["crew_swaps"]:
                if cid in iss["valid_replacements"]:
                    self._resolved["crew_swaps"].add(to); r = iss["points"]
                break
        m = f"Crew {cid}->{to} (was:{old})."
        if r > 0: m += f" [OK] (+{r:.2f})"
        return (r, m)

    # -- DELAY_FLIGHT -----------------------------------------
    def _cmd_delay(self, cmd):
        p = cmd.split()
        if len(p) < 3: return (-0.02, "Usage: DELAY_FLIGHT <flight_id> <minutes>")
        fid = p[1].upper()
        try: mins = int(p[2])
        except ValueError: return (-0.02, "Minutes must be a number.")
        if fid not in self._flights: return (-0.02, f"Flight {fid} not found.")
        if not 0 <= mins <= 360: return (-0.02, "Delay: 0-360 min.")
        f = self._flights[fid]; f["delay_minutes"] += mins; f["status"] = "delayed"
        if f["delay_minutes"] >= TARMAC_LIMIT_MINUTES:
            self._compensation_usd += f["passenger_count"] * COMP_DELAY_PER_PAX * 0.01
        return (0, f"{fid} +{mins}min (total:{f['delay_minutes']}min)")

    # -- CANCEL_FLIGHT ----------------------------------------
    def _cmd_cancel(self, cmd):
        p = cmd.split()
        if len(p) < 2: return (-0.02, "Usage: CANCEL_FLIGHT <flight_id>")
        fid = p[1].upper()
        if fid not in self._flights: return (-0.02, f"Flight {fid} not found.")
        f = self._flights[fid]
        if f["status"] == "cancelled": return (-0.01, f"{fid} already cancelled.")
        f["status"] = "cancelled"; gid = f["gate"]
        if gid in self._gates and self._gates[gid]["assigned_flight"] == fid:
            self._gates[gid]["status"] = "cooldown"
            self._gates[gid]["assigned_flight"] = None
            self._gate_cooldowns[gid] = self.GATE_COOLDOWN_STEPS
        self._compensation_usd += f["passenger_count"] * COMP_CANCEL_PER_PAX
        r = 0
        for iss in self._issues["cancellations_needed"]:
            if iss["flight_id"] == fid and fid not in self._resolved["cancellations"]:
                self._resolved["cancellations"].add(fid); r = iss["points"]; break
        if r > 0:
            return (r, f"{fid} cancelled (required -- {iss.get('reason','issue')}). "
                       f"Gate {gid} in cooldown. [OK] (+{r:.2f})")
        return (-0.05, f"{fid} cancelled. [!] NOT required. {f['passenger_count']} pax affected. (-0.05)")

    # -- HOLD_CONNECTION --------------------------------------
    def _cmd_hold(self, cmd):
        p = cmd.split()
        if len(p) < 3: return (-0.02, "Usage: HOLD_CONNECTION <departing_flight> <minutes>")
        fid = p[1].upper()
        try: mins = int(p[2])
        except ValueError: return (-0.02, "Minutes must be number.")
        if fid not in self._flights: return (-0.02, f"Flight {fid} not found.")
        if not 5 <= mins <= 60: return (-0.02, "Hold: 5-60 minutes.")
        f = self._flights[fid]; f["delay_minutes"] += mins; f["status"] = "delayed"
        r = 0
        for iss in self._issues.get("held_connections", []):
            if iss["departing_flight"] == fid and fid not in self._resolved["held_connections"]:
                r = iss["points"] if mins >= iss.get("hold_minutes", 30) else iss["points"] * 0.5
                self._resolved["held_connections"].add(fid); break
        m = f"Holding {fid} +{mins}min for connecting passengers."
        if r > 0: m += f" [OK] Connection protected! (+{r:.2f})"
        return (r, m)

    # -- MOVE_TO_REMOTE ---------------------------------------
    def _cmd_remote(self, cmd):
        p = cmd.split()
        if len(p) < 2: return (-0.02, "Usage: MOVE_TO_REMOTE <flight_id>")
        fid = p[1].upper()
        if fid not in self._flights: return (-0.02, f"Flight {fid} not found.")
        if fid in self._remote_flights: return (-0.01, f"{fid} already at remote stand.")
        f = self._flights[fid]; gid = f["gate"]
        if gid in self._gates and self._gates[gid]["assigned_flight"] == fid:
            self._gates[gid]["status"] = "cooldown"
            self._gates[gid]["assigned_flight"] = None
            self._gate_cooldowns[gid] = self.GATE_COOLDOWN_STEPS
        f["gate"] = "REMOTE"; f["delay_minutes"] += 20
        self._remote_flights.add(fid)
        r = 0
        for iss in self._issues["gate_conflicts"]:
            if iss["flight_to_move"] == fid and fid not in self._resolved["gate_conflicts"]:
                self._resolved["gate_conflicts"].add(fid); r = iss["points"]; break
        m = f"{fid}->remote stand. Gate {gid} freed (cooldown). +20min boarding."
        if r > 0: m += f" [OK] Gate conflict resolved! (+{r:.2f})"
        return (r, m)

    # -- BROADCAST_DELAY (NEW) --------------------------------
    def _cmd_broadcast(self, cmd):
        """Notify passengers of a flight's delay status. Free action, adds realism."""
        p = cmd.split()
        if len(p) < 2: return (-0.02, "Usage: BROADCAST_DELAY <flight_id>")
        fid = p[1].upper()
        if fid not in self._flights: return (-0.02, f"Flight {fid} not found.")
        if fid in self._broadcast_flights:
            return (0, f"Passengers on {fid} already notified.")
        f = self._flights[fid]
        self._broadcast_flights.add(fid)
        pax_count = f["passenger_count"]
        return (0, f"[>>] Broadcast sent to {pax_count} passengers on {fid}: "
                   f"'{fid} delayed {f['delay_minutes']}min. New departure: {f['actual_departure']}. "
                   f"Rebooking assistance available at gate {f['gate']}.'")

    # -- ESCALATE_TO_SUPERVISOR (NEW) -------------------------
    def _cmd_escalate(self):
        """Get a strategic hint. Free, but only once per episode."""
        if self._escalation_used:
            return (0, "Supervisor already consulted this episode.")
        self._escalation_used = True
        hint = self._generate_hint()
        self._hint = hint
        return (0, f"[SUPERVISOR] SUPERVISOR HINT:\n{hint}")

    def _generate_hint(self):
        """Analyze current state and return the most urgent action."""
        nr = self._nresolved(); tot = self._state.total_issues
        # Priority 1: Required cancellations not done
        for iss in self._issues.get("cancellations_needed", []):
            if iss["flight_id"] not in self._resolved["cancellations"]:
                fid = iss["flight_id"]; f = self._flights.get(fid, {})
                return (f"URGENT: Flight {fid} (aircraft {f.get('aircraft_id','?')}) has a maintenance issue. "
                        f"Check with REQUEST_INFO flight {fid}, then CANCEL_FLIGHT {fid}.")
        # Priority 2: Gate conflicts
        for iss in self._issues["gate_conflicts"]:
            if iss["flight_to_move"] not in self._resolved["gate_conflicts"]:
                fid = iss["flight_to_move"]
                avail = [gid for gid, g in self._gates.items() if g["status"] == "available"]
                if avail:
                    return f"Gate conflict: {fid} needs to move. Available gates: {', '.join(avail)}. Try REASSIGN_GATE {fid} {avail[0]}."
                return f"Gate conflict: {fid} needs to move but no gates available. Try MOVE_TO_REMOTE {fid}."
        # Priority 3: Crew swaps
        for iss in self._issues["crew_swaps"]:
            if iss["flight"] not in self._resolved["crew_swaps"]:
                reserves = [cid for cid in iss["valid_replacements"] if cid not in self._used_reserve_crew]
                if reserves:
                    return f"Crew issue on {iss['flight']}. Swap with: SWAP_CREW {reserves[0]} RESERVE {iss['flight']}"
        # Priority 4: Passenger rebookings
        unbooked = [iss for iss in self._issues["passenger_rebookings"]
                    if iss["passenger_id"] not in self._resolved["passenger_rebookings"]]
        if unbooked:
            iss = unbooked[0]
            return f"{len(unbooked)} passengers need rebooking. Start with: REBOOK_PASSENGER {iss['passenger_id']} {iss['valid_flights'][0]}"
        return f"All {tot} issues appear resolved. You can issue DONE."

    # -- REQUEST_INFO -----------------------------------------
    def _cmd_info(self, cmd):
        parts = cmd.split(maxsplit=1)
        if len(parts) < 2:
            return (0, "Subjects: gates, flights, crew, passengers, summary, costs, regulations, flight <ID>")
        s = parts[1].strip().lower()

        if s == "gates":
            L = [f"Gates at {self._fmt()}:"]
            for gid, g in sorted(self._gates.items()):
                sz = f"[{g.get('size', 'std')}]" if g.get('size', 'standard') != 'standard' else ''
                cd = f" (cooldown:{self._gate_cooldowns[gid]})" if gid in self._gate_cooldowns else ""
                fl = f" -- {g['assigned_flight']}" if g['assigned_flight'] else ""
                L.append(f"  {gid}[{g['terminal']}]{sz}: {g['status']}{fl}{cd}")
            L.append(f"  -> {sum(1 for g in self._gates.values() if g['status'] == 'available')} available")
            return (0, "\n".join(L))

        if s == "flights":
            L = [f"Flights at {self._fmt()}:"]
            for fid, f in sorted(self._flights.items()):
                flag = "[!]" if f["status"] in ("delayed", "cancelled") else "  "
                notif = "[>>]" if fid in self._broadcast_flights else "  "
                ftype = f"[intl]" if f.get('flight_type', 'domestic') != 'domestic' else ''
                ac_sz = f"[wide]" if f.get('aircraft_size', 'narrow_body') != 'narrow_body' else ''
                tarmac = "[!!]" if f["delay_minutes"] >= TARMAC_LIMIT_MINUTES else ""
                L.append(f"  {flag}{notif}{fid}{ftype}{ac_sz}: {f['origin']}->{f['destination']} "
                         f"gate={f['gate']} status={f['status']} delay={f['delay_minutes']}min "
                         f"crew={f['crew_id']} ac={f['aircraft_id']}{tarmac}")
            return (0, "\n".join(L))

        if s == "crew":
            L = [f"Crew at {self._fmt()}:"]
            for cid, c in sorted(self._crew.items()):
                flag = "[!]" if c["status"] in ("at_limit", "exceeded") else "  "
                L.append(f"  {flag}{cid}: flight={c['assigned_flight']} "
                         f"hrs={c['duty_hours_remaining']:.1f} [{c['status']}]")
            if not self._crew: L.append("  No crew data.")
            return (0, "\n".join(L))

        if s == "passengers":
            L = [f"Passengers at {self._fmt()}:"]
            for pid, px in sorted(self._passengers.items()):
                conn = px["connection_flight"]; dest = "?"
                if conn and conn in self._flights:
                    dest = self._flights[conn]["destination"]
                flag = "[OK]" if px["status"] == "rebooked" else "[X]"
                pri = px.get("priority", "standard")
                pri_tag = f"[{pri.upper()}]" if pri != "standard" else ""
                L.append(f"  {flag}{pid}{pri_tag}: on={px['original_flight']} needs->{dest} [{px['status']}]")
            un = sum(1 for p in self._passengers.values() if p["status"] == "needs_rebooking")
            L.append(f"  -> {un} need rebooking")
            return (0, "\n".join(L))

        if s == "summary":
            nr = self._nresolved(); tot = self._state.total_issues
            fs = min(max(self._score / self._max_score, 0.01), 0.99) if self._max_score > 0 else 0.01
            tarmac = self._get_tarmac_alerts()
            L = [f"=== Operations Summary at {self._fmt()} ===",
                 f"  Progress: {nr}/{tot} ({fs:.0%}) | Steps: {self._state.step_count}/{self._max_steps}",
                 f"  Weather: severity {self._weather_severity}/5 -- {self._weather_desc}",
                 f"  Gates:  {len(self._resolved['gate_conflicts'])}/{len(self._issues.get('gate_conflicts', []))}",
                 f"  Pax:    {len(self._resolved['passenger_rebookings'])}/{len(self._issues.get('passenger_rebookings', []))}",
                 f"  Crew:   {len(self._resolved['crew_swaps'])}/{len(self._issues.get('crew_swaps', []))}",
                 f"  Cancel: {len(self._resolved['cancellations'])}/{len(self._issues.get('cancellations_needed', []))}",
                 f"  Holds:  {len(self._resolved['held_connections'])}/{len(self._issues.get('held_connections', []))}",
                 f"  Compensation: ${self._compensation_usd:,.0f}",
                 f"  Tarmac alerts: {', '.join(tarmac) if tarmac else 'None'}",
                 f"  Broadcasts sent: {len(self._broadcast_flights)}",
                 f"  Supervisor hint used: {'Yes' if self._escalation_used else 'No'}"]
            return (0, "\n".join(L))

        if s == "costs":
            return (0, f"Compensation at {self._fmt()}: ${self._compensation_usd:,.0f}\n"
                       f"  Rates: ${COMP_REBOOK_PER_PAX}/rebook, "
                       f"${COMP_DELAY_PER_PAX}/delay(>3h), ${COMP_CANCEL_PER_PAX}/cancel per pax")

        if s == "regulations":
            return (0, FAA_REGULATIONS)

        if s.startswith("flight "):
            fid = s.split()[1].upper()
            if fid not in self._flights: return (0, f"Flight {fid} not found.")
            f = self._flights[fid]
            info = (f"Flight {fid}: {f['origin']}->{f['destination']}\n"
                    f"  Sched:{f['scheduled_departure']} Actual:{f['actual_departure']}\n"
                    f"  Gate:{f['gate']} Status:{f['status']} Delay:{f['delay_minutes']}min\n"
                    f"  Crew:{f['crew_id']} Aircraft:{f['aircraft_id']} Pax:{f['passenger_count']}\n"
                    f"  Type:{f.get('flight_type', 'domestic')} Size:{f.get('aircraft_size', 'narrow_body')}\n"
                    f"  Notified: {'Yes' if fid in self._broadcast_flights else 'No'}")
            if fid in self._remote_flights:
                info += "\n  [*] At remote stand (+20min boarding)"
            if f["delay_minutes"] >= TARMAC_LIMIT_MINUTES:
                info += f"\n  [!!] TARMAC ALERT: {f['delay_minutes']}min exceeds FAA 3-hour limit!"
            maint = {"AC-801": "full_disruption", "AC-950": "overnight_recovery", "AC-951": "overnight_recovery"}
            if f["aircraft_id"] in maint and self._state.task_name == maint[f["aircraft_id"]]:
                info += f"\n  [!] MAINTENANCE ALERT: {f['aircraft_id']} critical issue. CANNOT fly. Must cancel."
            cpax = [pid for pid, px in self._passengers.items()
                    if px["original_flight"] == fid or px["connection_flight"] == fid]
            if cpax: info += f"\n  Connected pax: {', '.join(cpax)}"
            return (0, info)

        return (0, f"Unknown '{s}'. Try: gates, flights, crew, passengers, summary, costs, regulations, flight <ID>")

    # -- HELPERS -----------------------------------------------
    def _nresolved(self):
        return sum(len(v) for v in self._resolved.values())

    def _obs(self, msg="", reward=None, force_done=False):
        done = self._done or force_done
        fs = min(max(self._score / self._max_score, 0.01), 0.99) if self._max_score > 0 else 0.01
        return AirportObservation(
            done=done, reward=reward, current_time=self._fmt(),
            weather_severity=self._weather_severity,
            weather_description=self._weather_desc,
            airport_status=(f"Task:{self._state.task_name} | {self._fmt()} | "
                           f"Weather:{self._weather_severity}/5 | "
                           f"Issues:{self._nresolved()}/{self._state.total_issues} | "
                           f"Step:{self._state.step_count}/{self._max_steps}"),
            flights=[{"id": fid, "route": f"{f['origin']}->{f['destination']}",
                      "scheduled": f["scheduled_departure"], "actual": f["actual_departure"],
                      "gate": f["gate"], "status": f["status"], "delay_min": f["delay_minutes"],
                      "crew": f["crew_id"], "aircraft": f["aircraft_id"],
                      "pax": f["passenger_count"], "type": f.get("flight_type", "domestic"),
                      "ac_size": f.get("aircraft_size", "narrow_body"),
                      "notified": fid in self._broadcast_flights}
                     for fid, f in sorted(self._flights.items())],
            gates=[{"id": gid, "terminal": g["terminal"], "status": g["status"],
                    "flight": g["assigned_flight"], "size": g.get("size", "standard"),
                    "cooldown": self._gate_cooldowns.get(gid, 0)}
                   for gid, g in sorted(self._gates.items())],
            passenger_issues=[{"id": pid, "original_flight": p["original_flight"],
                               "connection": p["connection_flight"], "status": p["status"],
                               "priority": p.get("priority", "standard")}
                              for pid, p in sorted(self._passengers.items())],
            crew_issues=[{"id": cid, "flight": c["assigned_flight"],
                          "duty_hrs": round(c["duty_hours_remaining"], 1),
                          "status": c["status"]}
                         for cid, c in sorted(self._crew.items())],
            tarmac_alerts=self._get_tarmac_alerts(),
            pending_issues_count=self._state.total_issues - self._nresolved(),
            resolved_issues_count=self._nresolved(),
            total_issues_count=self._state.total_issues,
            compensation_cost_usd=self._compensation_usd,
            message=msg,
            available_commands=self.AVAILABLE_COMMANDS,
            score=fs,
            hint=self._hint)

    @property
    def state(self) -> AirportState:
        return self._state
