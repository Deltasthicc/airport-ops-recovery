"""
Airport Operations Recovery -- Enhanced Environment (v2)
=======================================================
11 Commands: REASSIGN_GATE, REBOOK_PASSENGER, SWAP_CREW, DELAY_FLIGHT,
  CANCEL_FLIGHT, HOLD_CONNECTION, MOVE_TO_REMOTE, BROADCAST_DELAY,
  ESCALATE_TO_SUPERVISOR, REQUEST_INFO, DONE

Features: time sim, weather, gate-aircraft compat, gate cooldown,
  tarmac timer (FAA 3h rule), compensation costs, passenger priority,
  hidden connections (task 6), broadcast requirements, supervisor hints,
  dynamic events, efficiency bonus, seeded reset.
"""
import copy, uuid, random
from typing import Any, Dict, List, Optional, Tuple
from openenv.core.env_server import Environment
from models import AirportAction, AirportObservation, AirportState
from server.scenarios import SCENARIOS, ALL_TASK_NAMES

COMP_REBOOK = 150; COMP_DELAY = 250; COMP_CANCEL = 600
TARMAC_LIMIT = 180  # FAA 3-hour tarmac rule

FAA_REGULATIONS = """FAA Regulations Summary:
  1. CREW DUTY LIMITS: Max 16h duty day. Crew at 0h remaining CANNOT fly.
  2. TARMAC RULE: Aircraft cannot remain on tarmac >3 hours (180 min) with passengers.
     Flights exceeding this MUST deplane, cancel, or depart. Violation: $27,500/pax fine.
  3. PASSENGER RIGHTS (EU261-inspired): Delays >3h = $250/pax. Cancellation = $600/pax.
     Rebooking = $150/pax processing cost.
  4. UNACCOMPANIED MINORS: Must have confirmed connection. Cannot be stranded overnight.
  5. GATE TURNAROUND: Minimum 10 min (2 steps) between aircraft at same gate.
  6. WIDE-BODY GATES: Aircraft types A330/A380/B777/B787 require [wide] gates only."""

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
        "BROADCAST_DELAY <flight_id>",
        "ESCALATE_TO_SUPERVISOR",
        "REQUEST_INFO <subject>  (gates/flights/crew/passengers/summary/costs/regulations/flight <ID>)",
        "DONE",
    ]
    MINUTES_PER_STEP = 5; GATE_COOLDOWN = 2

    def __init__(self):
        self._state = AirportState()
        self._flights={}; self._gates={}; self._passengers={}; self._crew={}
        self._issues={}; self._resolved={}
        self._score=0; self._max_score=1; self._max_steps=12
        self._clock=0; self._sh=14; self._sm=0
        self._desc=""; self._done=False; self._used_crew=set()
        self._fired=set(); self._wsev=1; self._wdesc=""
        self._comp=0; self._gcool={}; self._remote=set()
        self._broadcast=set(); self._escalated=False
        self._dynamic=[]; self._hidden_revealed=False

    def reset(self, seed=None, episode_id=None, **kwargs):
        task = kwargs.get("task", ALL_TASK_NAMES[0])
        if task not in SCENARIOS: task = ALL_TASK_NAMES[0]
        if seed is not None: random.seed(seed)
        sc = SCENARIOS[task]; self._load(sc, episode_id)
        return self._obs(
            f"DISRUPTION ALERT -- {sc['disruption_type'].upper().replace('_',' ')}\n"
            f"{self._desc}\n\n"
            f"Weather: {self._wdesc} (severity {self._wsev}/5)\n"
            f"Clock: {self._fmt()} (+{self.MINUTES_PER_STEP}min/step) | Steps: {self._max_steps}\n"
            f"Issues: {self._state.total_issues}")

    def _load(self, sc, eid=None):
        self._flights={f["flight_id"]:copy.deepcopy(f) for f in sc["flights"]}
        self._gates={g["gate_id"]:copy.deepcopy(g) for g in sc["gates"]}
        self._passengers={p["passenger_id"]:copy.deepcopy(p) for p in sc["passengers"]}
        self._crew={c["crew_id"]:copy.deepcopy(c) for c in sc["crew"]}
        self._issues=copy.deepcopy(sc["issues"])
        self._resolved={k:set() for k in ("gate_conflicts","passenger_rebookings","crew_swaps",
                                            "cancellations","held_connections","broadcasts")}
        self._score=0; self._max_score=sc["max_score"]; self._max_steps=sc["max_steps"]
        self._desc=sc["description"]; self._done=False; self._used_crew=set()
        self._fired=set(); self._gcool={}; self._remote=set()
        self._broadcast=set(); self._escalated=False; self._hidden_revealed=False
        self._wsev=sc.get("weather_severity",1); self._wdesc=sc.get("weather_desc","Clear")
        self._comp=0; self._clock=0; self._dynamic=sc.get("dynamic_events",[])
        h,m=sc["current_time"].split(":"); self._sh=int(h); self._sm=int(m)
        total=sum(len(self._issues.get(k,[])) for k in
                  ("gate_conflicts","passenger_rebookings","crew_swaps",
                   "cancellations_needed","held_connections","broadcasts_needed"))
        self._state=AirportState(episode_id=eid or str(uuid.uuid4()),step_count=0,
            task_name=sc["task_name"],disruption_type=sc["disruption_type"],
            total_flights=len(self._flights),resolved_issues=0,
            total_issues=total,max_steps=self._max_steps)

    def _fmt(self):
        t=self._sh*60+self._sm+self._clock; return f"{(t//60)%24:02d}:{t%60:02d}"

    def step(self, action: AirportAction, timeout_s=None, **kwargs):
        if self._done: return self._obs("Episode ended.",force_done=True)
        self._state.step_count += 1; self._clock += self.MINUTES_PER_STEP
        self._tick_cooldowns(); self._tick_tarmac()
        evts = self._check_events()
        reward, msg = self._exec(action.command.strip())
        self._score += reward
        if evts: msg = "".join(f"[!] {e}\n" for e in evts) + "\n" + msg
        # Tarmac warnings
        tarmac_warns = [f for f in self._flights.values()
                        if f.get("tarmac_minutes",0) >= TARMAC_LIMIT and f["status"]=="delayed"]
        if tarmac_warns:
            tw = ", ".join(f["flight_id"] for f in tarmac_warns)
            msg += f"\n[!!] TARMAC VIOLATION: {tw} exceed {TARMAC_LIMIT}min! Must act immediately."

        nr=self._nr(); done_all=nr>=self._state.total_issues
        done_steps=self._state.step_count>=self._max_steps; done_cmd=action.command.upper().strip().startswith("DONE")
        if done_all or done_steps or done_cmd:
            self._done=True
            bonus=min((self._max_steps-self._state.step_count)*0.01,0.10) if done_all else 0
            tpen=len([f for f in self._flights.values() if f.get("tarmac_minutes",0)>=TARMAC_LIMIT and f["status"]=="delayed"])*(-0.03)
            self._score+=bonus+tpen
            fs=min(max(self._score/self._max_score,0),1) if self._max_score>0 else 0
            if done_all: msg+=f"\n\n[OK] All resolved! Steps:{self._state.step_count} Bonus:+{bonus:.2f} Tarmac penalty:{tpen:.2f} Score:{fs:.2f} Cost:${self._comp:,.0f}"
            elif done_cmd: msg+=f"\n\nDONE. Score:{fs:.2f} Cost:${self._comp:,.0f}"
            else: msg+=f"\n\n[TIME] Out of steps. Score:{fs:.2f} Cost:${self._comp:,.0f}"
        self._state.resolved_issues=nr
        return self._obs(msg, reward=reward)

    def _tick_cooldowns(self):
        exp=[g for g,r in self._gcool.items() if r<=1]
        for g in exp:
            del self._gcool[g]
            if g in self._gates and self._gates[g]["status"]=="cooldown": self._gates[g]["status"]="available"
        for g in self._gcool: self._gcool[g]-=1

    def _tick_tarmac(self):
        for f in self._flights.values():
            if f["status"]=="delayed" and f["delay_minutes"]>0:
                f["tarmac_minutes"]=f.get("tarmac_minutes",0)+self.MINUTES_PER_STEP

    def _check_events(self):
        msgs=[]
        for ev in self._dynamic:
            if ev.get("step")==self._state.step_count and ev["step"] not in self._fired:
                self._fired.add(ev["step"])
                if ev["type"] in ("weather_update","weather_escalation"):
                    self._wsev=ev.get("severity",self._wsev); self._wdesc=ev.get("desc","")
                    msgs.append(f"WEATHER: {ev['desc']} (severity {self._wsev}/5)")
                elif ev["type"]=="crew_release":
                    cid=ev.get("crew_id")
                    if cid and cid in self._crew:
                        c=self._crew[cid]; fl=self._flights.get(c["assigned_flight"],{})
                        if fl.get("status")=="cancelled":
                            c["assigned_flight"]="RESERVE"; c["status"]="available"; c["duty_hours_remaining"]=6
                            msgs.append(f"CREW: {cid} now available as reserve.")
                else: msgs.append(ev.get("desc","Event."))
        return msgs

    def _exec(self, cmd):
        u=cmd.upper().strip()
        if u.startswith("REASSIGN_GATE"):    return self._gate(cmd)
        if u.startswith("REBOOK_PASSENGER"): return self._rebook(cmd)
        if u.startswith("SWAP_CREW"):        return self._crew_cmd(cmd)
        if u.startswith("DELAY_FLIGHT"):     return self._delay(cmd)
        if u.startswith("CANCEL_FLIGHT"):    return self._cancel(cmd)
        if u.startswith("HOLD_CONNECTION"):  return self._hold(cmd)
        if u.startswith("MOVE_TO_REMOTE"):   return self._move_remote(cmd)
        if u.startswith("BROADCAST_DELAY"):  return self._bcast(cmd)
        if u.startswith("ESCALATE_TO_SUPERVISOR"): return self._escalate()
        if u.startswith("REQUEST_INFO"):     return self._info(cmd)
        if u.startswith("DONE"):             return (0,"Finishing operations.")
        return (-0.02,"Unknown command. Try REQUEST_INFO for help.")

    def _gate(self, cmd):
        p=cmd.split()
        if len(p)<3: return (-0.02,"Usage: REASSIGN_GATE <flight> <gate>")
        fid,gid=p[1].upper(),p[2].upper()
        if fid not in self._flights: return (-0.02,f"Flight {fid} not found.")
        if gid not in self._gates: return (-0.02,f"Gate {gid} not found.")
        g=self._gates[gid]; f=self._flights[fid]
        if g["status"]=="blocked": return (-0.02,f"Gate {gid} blocked (security).")
        if g["status"]=="cooldown": return (-0.02,f"Gate {gid} in cooldown ({self._gcool.get(gid,0)} steps).")
        if g["status"]=="occupied" and g["assigned_flight"]!=fid:
            return (-0.02,f"Gate {gid} occupied by {g['assigned_flight']}.")
        if f.get("aircraft_size")=="wide_body" and g.get("size","standard")!="wide":
            return (-0.02,f"Gate {gid} too small for wide-body {f['aircraft_id']}. Need [wide] gate.")
        old=f["gate"]
        if old in self._gates and self._gates[old]["assigned_flight"]==fid:
            self._gates[old]["status"]="cooldown"; self._gates[old]["assigned_flight"]=None
            self._gcool[old]=self.GATE_COOLDOWN
        f["gate"]=gid; g["status"]="occupied"; g["assigned_flight"]=fid
        r=0
        for iss in self._issues.get("gate_conflicts",[]):
            if iss["flight_to_move"]==fid and fid not in self._resolved["gate_conflicts"]:
                self._resolved["gate_conflicts"].add(fid); r=iss["points"]; break
        m=f"{fid}: {old}->{gid}."
        if r>0: m+=f" [OK] Gate conflict resolved! (+{r:.2f})"
        return (r,m)

    def _rebook(self, cmd):
        p=cmd.split()
        if len(p)<3: return (-0.02,"Usage: REBOOK_PASSENGER <pax> <flight>")
        pid,nf=p[1].upper(),p[2].upper()
        if pid not in self._passengers: return (-0.02,f"Passenger {pid} not found.")
        if nf not in self._flights: return (-0.02,f"Flight {nf} not found.")
        pax=self._passengers[pid]
        if pax["status"]=="rebooked": return (-0.01,f"{pid} already rebooked.")
        tf=self._flights[nf]
        if tf["status"]=="cancelled": return (-0.02,f"Flight {nf} cancelled.")
        oc=pax.get("connection_flight"); od=self._flights[oc]["destination"] if oc and oc in self._flights else None
        nd=tf["destination"]
        r=0
        for iss in self._issues.get("passenger_rebookings",[]):
            if iss["passenger_id"]==pid and pid not in self._resolved["passenger_rebookings"]:
                if nf in iss["valid_flights"]: r=iss["points"]
                elif od and nd==od: r=iss["points"]*0.75
                else: r=iss["points"]*0.5
                self._resolved["passenger_rebookings"].add(pid); break
        pax["status"]="rebooked"; pax["connection_flight"]=nf; self._comp+=COMP_REBOOK
        pri=pax.get("priority","standard")
        m=f"{pid}[{pri}]->{nf} ({tf['origin']}->{tf['destination']})."
        if r>0: m+=f" [OK] (+{r:.2f})" if (od and nd==od) else f" [!] suboptimal (+{r:.2f})"
        return (r,m)

    def _crew_cmd(self, cmd):
        p=cmd.split()
        if len(p)<4: return (-0.02,"Usage: SWAP_CREW <crew> <from> <to>")
        cid,to=p[1].upper(),p[3].upper()
        if cid not in self._crew: return (-0.02,f"Crew {cid} not found.")
        if to not in self._flights: return (-0.02,f"Flight {to} not found.")
        c=self._crew[cid]
        if c["status"] in ("at_limit","exceeded"): return (-0.02,f"Crew {cid} at/exceeded limit.")
        if c["duty_hours_remaining"]<1 and c["status"]!="available": return (-0.02,f"Crew {cid} insufficient hours.")
        if cid in self._used_crew: return (-0.02,f"Crew {cid} already reassigned.")
        old=c["assigned_flight"]; c["assigned_flight"]=to; self._flights[to]["crew_id"]=cid
        if c["status"]=="available" and old=="RESERVE": self._used_crew.add(cid)
        r=0
        for iss in self._issues.get("crew_swaps",[]):
            if iss["flight"]==to and to not in self._resolved["crew_swaps"]:
                if cid in iss["valid_replacements"]: self._resolved["crew_swaps"].add(to); r=iss["points"]
                break
        m=f"Crew {cid}->{to} (was:{old})."
        if r>0: m+=f" [OK] (+{r:.2f})"
        return (r,m)

    def _delay(self, cmd):
        p=cmd.split()
        if len(p)<3: return (-0.02,"Usage: DELAY_FLIGHT <flight> <min>")
        fid=p[1].upper()
        try: mins=int(p[2])
        except: return (-0.02,"Minutes must be number.")
        if fid not in self._flights: return (-0.02,f"Flight {fid} not found.")
        if not 0<=mins<=360: return (-0.02,"0-360 min.")
        f=self._flights[fid]; f["delay_minutes"]+=mins; f["status"]="delayed"
        if f["delay_minutes"]>=180: self._comp+=f["passenger_count"]*COMP_DELAY*0.01
        return (0,f"{fid} +{mins}min (total:{f['delay_minutes']}min)")

    def _cancel(self, cmd):
        p=cmd.split()
        if len(p)<2: return (-0.02,"Usage: CANCEL_FLIGHT <flight>")
        fid=p[1].upper()
        if fid not in self._flights: return (-0.02,f"Flight {fid} not found.")
        f=self._flights[fid]
        if f["status"]=="cancelled": return (-0.01,f"{fid} already cancelled.")
        f["status"]="cancelled"; gid=f["gate"]
        if gid in self._gates and self._gates[gid]["assigned_flight"]==fid:
            self._gates[gid]["status"]="cooldown"; self._gates[gid]["assigned_flight"]=None; self._gcool[gid]=self.GATE_COOLDOWN
        self._comp+=f["passenger_count"]*COMP_CANCEL; f["tarmac_minutes"]=0
        r=0
        for iss in self._issues.get("cancellations_needed",[]):
            if iss["flight_id"]==fid and fid not in self._resolved["cancellations"]:
                self._resolved["cancellations"].add(fid); r=iss["points"]; break
        if r>0: return (r,f"{fid} cancelled (required). [OK] (+{r:.2f})")
        return (-0.05,f"{fid} cancelled. [!] NOT required. (-0.05)")

    def _hold(self, cmd):
        p=cmd.split()
        if len(p)<3: return (-0.02,"Usage: HOLD_CONNECTION <flight> <min>")
        fid=p[1].upper()
        try: mins=int(p[2])
        except: return (-0.02,"Minutes must be number.")
        if fid not in self._flights: return (-0.02,f"Flight {fid} not found.")
        if not 5<=mins<=60: return (-0.02,"Hold: 5-60 min.")
        f=self._flights[fid]; f["delay_minutes"]+=mins; f["status"]="delayed"
        r=0
        for iss in self._issues.get("held_connections",[]):
            if iss["departing_flight"]==fid and fid not in self._resolved["held_connections"]:
                r=iss["points"] if mins>=iss.get("hold_minutes",30) else iss["points"]*0.5
                self._resolved["held_connections"].add(fid); break
        m=f"Holding {fid} for {mins}min."
        if r>0: m+=f" [OK] Connection protected! (+{r:.2f})"
        return (r,m)

    def _move_remote(self, cmd):
        p=cmd.split()
        if len(p)<2: return (-0.02,"Usage: MOVE_TO_REMOTE <flight>")
        fid=p[1].upper()
        if fid not in self._flights: return (-0.02,f"Flight {fid} not found.")
        if fid in self._remote: return (-0.01,f"{fid} already at remote.")
        f=self._flights[fid]; gid=f["gate"]
        if gid in self._gates and self._gates[gid]["assigned_flight"]==fid:
            self._gates[gid]["status"]="cooldown"; self._gates[gid]["assigned_flight"]=None; self._gcool[gid]=self.GATE_COOLDOWN
        f["gate"]="REMOTE"; f["delay_minutes"]+=20; self._remote.add(fid)
        r=0
        for iss in self._issues.get("gate_conflicts",[]):
            if iss["flight_to_move"]==fid and fid not in self._resolved["gate_conflicts"]:
                self._resolved["gate_conflicts"].add(fid); r=iss["points"]; break
        m=f"{fid}->REMOTE. Gate {gid} freed. +20min boarding."
        if r>0: m+=f" [OK] Gate conflict resolved! (+{r:.2f})"
        return (r,m)

    def _bcast(self, cmd):
        p=cmd.split()
        if len(p)<2: return (-0.02,"Usage: BROADCAST_DELAY <flight>")
        fid=p[1].upper()
        if fid not in self._flights: return (-0.02,f"Flight {fid} not found.")
        if fid in self._broadcast: return (-0.01,f"{fid} already broadcast.")
        f=self._flights[fid]; self._broadcast.add(fid)
        r=0
        for iss in self._issues.get("broadcasts_needed",[]):
            if iss["flight_id"]==fid and fid not in self._resolved["broadcasts"]:
                self._resolved["broadcasts"].add(fid); r=iss["points"]; break
        m=f"[>>] Delay broadcast sent for {fid} ({f['delay_minutes']}min delay, {f['passenger_count']} pax)."
        if r>0: m+=f" [OK] Passengers informed! (+{r:.2f})"
        return (r,m)

    def _escalate(self):
        if self._escalated: return (0,"Already escalated this episode. Use REQUEST_INFO summary instead.")
        self._escalated=True
        # Generate hint: what's the highest-value unresolved issue?
        best_action="REQUEST_INFO summary"
        best_pts=0
        for iss in self._issues.get("cancellations_needed",[]):
            if iss["flight_id"] not in self._resolved["cancellations"] and iss["points"]>best_pts:
                best_pts=iss["points"]; best_action=f"CANCEL_FLIGHT {iss['flight_id']} (check maintenance first!)"
        for iss in self._issues.get("gate_conflicts",[]):
            if iss["flight_to_move"] not in self._resolved["gate_conflicts"] and iss["points"]>best_pts:
                best_pts=iss["points"]; best_action=f"Fix gate conflict for {iss['flight_to_move']}"
        for iss in self._issues.get("crew_swaps",[]):
            if iss["flight"] not in self._resolved["crew_swaps"] and iss["points"]>best_pts:
                best_pts=iss["points"]; best_action=f"Swap crew for flight {iss['flight']}"
        for iss in self._issues.get("broadcasts_needed",[]):
            if iss["flight_id"] not in self._resolved["broadcasts"] and iss["points"]>best_pts:
                best_pts=iss["points"]; best_action=f"BROADCAST_DELAY {iss['flight_id']}"
        un=sum(1 for p in self._passengers.values() if p["status"]=="needs_rebooking")
        return (0,f"[SUPERVISOR] SUPERVISOR HINT: Highest priority action ({best_pts:.2f} pts): {best_action}. "
                  f"{un} passengers still need rebooking. {self._nr()}/{self._state.total_issues} issues resolved.")

    def _info(self, cmd):
        parts=cmd.split(maxsplit=1)
        if len(parts)<2: return (0,"Subjects: gates, flights, crew, passengers, summary, costs, regulations, flight <ID>")
        s=parts[1].strip().lower()

        if s=="regulations": return (0, FAA_REGULATIONS)

        if s=="gates":
            L=[f"Gates at {self._fmt()}:"]
            for gid,g in sorted(self._gates.items()):
                sz=f"[wide]" if g.get("size","standard")=="wide" else ""
                cd=f" (cooldown:{self._gcool[gid]})" if gid in self._gcool else ""
                fl=f" -- {g['assigned_flight']}" if g["assigned_flight"] else ""
                L.append(f"  {gid}[{g['terminal']}]{sz}: {g['status']}{fl}{cd}")
            L.append(f"  -> {sum(1 for g in self._gates.values() if g['status']=='available')} available")
            return (0,"\n".join(L))

        if s=="flights":
            L=[f"Flights at {self._fmt()}:"]
            for fid,f in sorted(self._flights.items()):
                flag="[!]" if f["status"] in ("delayed","cancelled") else "  "
                tarmac=f" TARMAC:{f.get('tarmac_minutes',0)}min" if f.get("tarmac_minutes",0)>120 else ""
                bcast=" [BROADCAST SENT]" if fid in self._broadcast else ""
                L.append(f"  {flag}{fid}: {f['origin']}->{f['destination']} gate={f['gate']} "
                         f"status={f['status']} delay={f['delay_minutes']}min crew={f['crew_id']}{tarmac}{bcast}")
            return (0,"\n".join(L))

        if s=="crew":
            L=[f"Crew at {self._fmt()}:"]
            for cid,c in sorted(self._crew.items()):
                flag="[!]" if c["status"] in ("at_limit","exceeded") else "  "
                L.append(f"  {flag}{cid}: flight={c['assigned_flight']} hrs={c['duty_hours_remaining']:.1f} [{c['status']}]")
            if not self._crew: L.append("  No crew data.")
            return (0,"\n".join(L))

        if s=="passengers":
            L=[f"Passengers at {self._fmt()}:"]
            for pid,px in sorted(self._passengers.items()):
                conn=px["connection_flight"]; dest="?"
                # Hidden connections: show "UNKNOWN" until revealed
                if px.get("hidden") and not self._hidden_revealed:
                    conn="???"; dest="???"
                    self._hidden_revealed=True  # First REQUEST_INFO passengers reveals them
                elif conn and conn in self._flights:
                    dest=self._flights[conn]["destination"]
                flag="[OK]" if px["status"]=="rebooked" else "[X]"
                pri=px.get("priority","standard")
                tag=f"[{pri.upper()}]" if pri!="standard" else ""
                L.append(f"  {flag}{pid}{tag}: on={px['original_flight']} needs->{dest} via {conn} [{px['status']}]")
            un=sum(1 for p in self._passengers.values() if p["status"]=="needs_rebooking")
            L.append(f"  -> {un} need rebooking")
            return (0,"\n".join(L))

        if s=="summary":
            nr=self._nr(); tot=self._state.total_issues
            fs=min(max(self._score/self._max_score,0),1) if self._max_score>0 else 0
            tviols=len([f for f in self._flights.values() if f.get("tarmac_minutes",0)>=TARMAC_LIMIT and f["status"]=="delayed"])
            L=[f"=== Summary at {self._fmt()} ===",
               f"  Progress: {nr}/{tot} ({fs:.0%}) | Steps: {self._state.step_count}/{self._max_steps}",
               f"  Weather: {self._wsev}/5 -- {self._wdesc}",
               f"  Gates: {len(self._resolved['gate_conflicts'])}/{len(self._issues.get('gate_conflicts',[]))}",
               f"  Pax: {len(self._resolved['passenger_rebookings'])}/{len(self._issues.get('passenger_rebookings',[]))}",
               f"  Crew: {len(self._resolved['crew_swaps'])}/{len(self._issues.get('crew_swaps',[]))}",
               f"  Cancel: {len(self._resolved['cancellations'])}/{len(self._issues.get('cancellations_needed',[]))}",
               f"  Holds: {len(self._resolved['held_connections'])}/{len(self._issues.get('held_connections',[]))}",
               f"  Broadcasts: {len(self._resolved['broadcasts'])}/{len(self._issues.get('broadcasts_needed',[]))}",
               f"  Cost: ${self._comp:,.0f} | Tarmac violations: {tviols} | Remote: {len(self._remote)}"]
            return (0,"\n".join(L))

        if s=="costs":
            return (0,f"Costs at {self._fmt()}: ${self._comp:,.0f}\n"
                     f"  Rates: ${COMP_REBOOK}/rebook, ${COMP_DELAY}/delay(>3h), ${COMP_CANCEL}/cancel per pax\n"
                     f"  Tarmac violation fine: $27,500/pax")

        if s.startswith("flight "):
            fid=s.split()[1].upper()
            if fid not in self._flights: return (0,f"Flight {fid} not found.")
            f=self._flights[fid]
            info=(f"Flight {fid}: {f['origin']}->{f['destination']}\n"
                  f"  Sched:{f['scheduled_departure']} Actual:{f['actual_departure']}\n"
                  f"  Gate:{f['gate']} Status:{f['status']} Delay:{f['delay_minutes']}min\n"
                  f"  Crew:{f['crew_id']} Aircraft:{f['aircraft_id']} Pax:{f['passenger_count']}\n"
                  f"  Type:{f.get('flight_type','domestic')} Size:{f.get('aircraft_size','narrow_body')}\n"
                  f"  Tarmac time:{f.get('tarmac_minutes',0)}min" + (" [!] APPROACHING LIMIT!" if f.get("tarmac_minutes",0)>=150 else ""))
            maint={"AC-801":"full_disruption","AC-950":"overnight_recovery","AC-951":"overnight_recovery"}
            if f["aircraft_id"] in maint and self._state.task_name==maint[f["aircraft_id"]]:
                info+=f"\n  [!] MAINTENANCE ALERT: {f['aircraft_id']} critical issue. CANNOT fly. Must cancel."
            cpax=[pid for pid,px in self._passengers.items() if px["original_flight"]==fid or px["connection_flight"]==fid]
            if cpax: info+=f"\n  Connected pax: {', '.join(cpax)}"
            if fid in self._remote: info+="\n  [*] At remote stand"
            if fid in self._broadcast: info+="\n  [>>] Delay broadcast sent"
            return (0,info)

        return (0,f"Unknown '{s}'. Try: gates, flights, crew, passengers, summary, costs, regulations, flight <ID>")

    def _nr(self): return sum(len(v) for v in self._resolved.values())

    def _obs(self, msg="", reward=None, force_done=False):
        done=self._done or force_done
        fs=min(max(self._score/self._max_score,0),1) if self._max_score>0 else 0
        tviols=len([f for f in self._flights.values() if f.get("tarmac_minutes",0)>=TARMAC_LIMIT and f["status"]=="delayed"])
        return AirportObservation(
            done=done, reward=reward, current_time=self._fmt(),
            weather_severity=self._wsev, weather_description=self._wdesc,
            airport_status=f"Task:{self._state.task_name} | {self._fmt()} | W:{self._wsev}/5 | Issues:{self._nr()}/{self._state.total_issues} | Step:{self._state.step_count}/{self._max_steps}",
            flights=[{"id":fid,"route":f"{f['origin']}->{f['destination']}","scheduled":f["scheduled_departure"],
                      "actual":f["actual_departure"],"gate":f["gate"],"status":f["status"],
                      "delay_min":f["delay_minutes"],"crew":f["crew_id"],"aircraft":f["aircraft_id"],
                      "pax":f["passenger_count"],"type":f.get("flight_type","domestic"),
                      "ac_size":f.get("aircraft_size","narrow_body"),
                      "tarmac_min":f.get("tarmac_minutes",0)} for fid,f in sorted(self._flights.items())],
            gates=[{"id":gid,"terminal":g["terminal"],"status":g["status"],"flight":g["assigned_flight"],
                    "size":g.get("size","standard"),"cooldown":self._gcool.get(gid,0)}
                   for gid,g in sorted(self._gates.items())],
            passenger_issues=[{"id":pid,"original_flight":p["original_flight"],"connection":p["connection_flight"] if not (p.get("hidden") and not self._hidden_revealed) else "???",
                               "status":p["status"],"priority":p.get("priority","standard")}
                              for pid,p in sorted(self._passengers.items())],
            crew_issues=[{"id":cid,"flight":c["assigned_flight"],"duty_hrs":round(c["duty_hours_remaining"],1),
                          "status":c["status"]} for cid,c in sorted(self._crew.items())],
            pending_issues_count=self._state.total_issues-self._nr(),
            resolved_issues_count=self._nr(), total_issues_count=self._state.total_issues,
            compensation_cost_usd=self._comp, tarmac_violations=tviols,
            message=msg, available_commands=self.AVAILABLE_COMMANDS, score=fs)

    @property
    def state(self): return self._state
