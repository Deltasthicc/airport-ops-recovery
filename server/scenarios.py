"""
Airport Operations Recovery -- 5 Scenario Definitions
=====================================================
Features per scenario:
  - Passenger priority: vip (2x), family (1.5x), unaccompanied_minor (2.5x), standard (1x)
  - Gate size: wide (handles all), narrow (narrow-body only)
  - Aircraft size: wide_body, narrow_body -- wide can't park at narrow gates
  - Flight type: international (higher priority), domestic
  - Weather severity: 1=clear to 5=extreme
  - Gate turnaround: cooldown_steps (how many steps after freeing before reuse)
"""
from typing import Any, Dict, List

def _f(fid, orig, dest, sched, actual, gate, status, crew, ac, delay=0, pax=120,
       ac_size="narrow_body", flight_type="domestic"):
    return dict(flight_id=fid, origin=orig, destination=dest,
                scheduled_departure=sched, actual_departure=actual,
                gate=gate, status=status, crew_id=crew, aircraft_id=ac,
                delay_minutes=delay, passenger_count=pax,
                aircraft_size=ac_size, flight_type=flight_type)

def _g(gid, term, status, flight=None, size="standard", cooldown=0):
    return dict(gate_id=gid, terminal=term, status=status,
                assigned_flight=flight, available_at=None,
                size=size, cooldown_steps=cooldown)

def _p(pid, orig, conn=None, priority="standard"):
    return dict(passenger_id=pid, original_flight=orig,
                connection_flight=conn, status="needs_rebooking",
                priority=priority)

def _c(cid, flight, hrs, status="available"):
    return dict(crew_id=cid, assigned_flight=flight,
                duty_hours_remaining=hrs, status=status)

PRIORITY_MULTIPLIER = {"standard":1.0, "vip":2.0, "family":1.5, "unaccompanied_minor":2.5}

# =============================================================
# TASK 1: Single Delay (Easy) -- 12 steps, 4 issues
# =============================================================
TASK_EASY = dict(
    task_name="single_delay",
    disruption_type="mechanical_delay",
    description=(
        "Inbound AA101 (ORD->JFK) delayed 90min by mechanical issue. "
        "Gate G3 needed by on-time UA202. 3 passengers miss connections. "
        "PAX-001/002 miss DL303->ATL. PAX-003 misses AA405->MIA."),
    max_steps=12, current_time="14:00", weather_severity=1, weather_desc="Clear skies",
    flights=[
        _f("AA101","ORD","JFK","14:30","16:00","G3","delayed","CRW-10","AC-701",delay=90,pax=145),
        _f("UA202","LAX","JFK","14:45","14:45","G3","scheduled","CRW-11","AC-702",pax=132),
        _f("DL303","JFK","ATL","15:30","15:30","G5","scheduled","CRW-12","AC-703",pax=98),
        _f("AA405","JFK","MIA","15:45","15:45","G7","scheduled","CRW-13","AC-704",pax=110),
        _f("DL604","JFK","ATL","19:00","19:00","G6","scheduled","CRW-15","AC-706",pax=75),
        _f("AA705","JFK","MIA","19:30","19:30","G8","scheduled","CRW-16","AC-707",pax=80),
    ],
    gates=[_g("G1","T1","available"),_g("G2","T1","available"),
           _g("G3","T1","occupied",flight="AA101"),_g("G4","T1","available"),
           _g("G5","T1","occupied",flight="DL303"),_g("G6","T2","occupied",flight="DL604"),
           _g("G7","T2","occupied",flight="AA405"),_g("G8","T2","occupied",flight="AA705")],
    passengers=[_p("PAX-001","AA101",conn="DL303"),_p("PAX-002","AA101",conn="DL303"),
                _p("PAX-003","AA101",conn="AA405")],
    crew=[],
    issues={"gate_conflicts":[{"flight_to_move":"AA101","blocking_flight":"UA202","points":0.40}],
            "passenger_rebookings":[
                {"passenger_id":"PAX-001","valid_flights":["DL604"],"points":0.20},
                {"passenger_id":"PAX-002","valid_flights":["DL604"],"points":0.20},
                {"passenger_id":"PAX-003","valid_flights":["AA705"],"points":0.20}],
            "crew_swaps":[],"cancellations_needed":[],"held_connections":[]},
    max_score=1.0,
    dynamic_events=[],
)

# =============================================================
# TASK 2: Cascading Weather Delays (Medium) -- 20 steps, 11 issues
# =============================================================
TASK_MEDIUM = dict(
    task_name="cascading_delays",
    disruption_type="weather_delays",
    description=(
        "Thunderstorm delays 4 inbound flights 60-120min. 3 gate conflicts. "
        "2 crew at duty limits. 6 passengers miss connections."),
    max_steps=20, current_time="16:00", weather_severity=3, weather_desc="Thunderstorms, moderate turbulence",
    flights=[
        _f("AA101","ORD","JFK","16:00","17:30","G1","delayed","CRW-10","AC-701",delay=90,pax=145),
        _f("UA202","SFO","JFK","16:15","17:15","G2","delayed","CRW-11","AC-702",delay=60,pax=132),
        _f("DL303","ATL","JFK","16:30","18:30","G3","delayed","CRW-12","AC-703",delay=120,pax=155),
        _f("SW404","DEN","JFK","16:45","17:45","G4","delayed","CRW-13","AC-704",delay=60,pax=98),
        _f("AA505","JFK","LAX","17:00","17:00","G1","scheduled","CRW-20","AC-711",pax=160),
        _f("UA606","JFK","SEA","17:30","17:30","G2","scheduled","CRW-21","AC-712",pax=110),
        _f("DL707","JFK","MIA","18:00","18:00","G3","scheduled","CRW-22","AC-713",pax=130),
        _f("AA808","JFK","LAX","20:00","20:00","G7","scheduled","CRW-23","AC-714",pax=90),
        _f("UA909","JFK","SEA","20:30","20:30","G8","scheduled","CRW-24","AC-715",pax=75),
        _f("DL010","JFK","MIA","21:00","21:00","G9","scheduled","CRW-25","AC-716",pax=88),
    ],
    gates=[_g("G1","T1","occupied",flight="AA101"),_g("G2","T1","occupied",flight="UA202"),
           _g("G3","T1","occupied",flight="DL303"),_g("G4","T1","occupied",flight="SW404"),
           _g("G5","T1","available"),_g("G6","T2","available"),
           _g("G7","T2","occupied",flight="AA808"),_g("G8","T2","occupied",flight="UA909"),
           _g("G9","T2","occupied",flight="DL010"),_g("G10","T2","available")],
    passengers=[_p("PAX-101","AA101",conn="AA505"),_p("PAX-102","AA101",conn="AA505"),
                _p("PAX-103","UA202",conn="UA606"),_p("PAX-104","UA202",conn="UA606"),
                _p("PAX-105","DL303",conn="DL707"),_p("PAX-106","DL303",conn="DL707")],
    crew=[_c("CRW-20","AA505",0.5,"at_limit"),_c("CRW-22","DL707",0.3,"at_limit"),
          _c("CRW-30","RESERVE",8.0),_c("CRW-31","RESERVE",7.5)],
    issues={"gate_conflicts":[
                {"flight_to_move":"AA505","blocking_flight":"AA101","points":0.10},
                {"flight_to_move":"UA606","blocking_flight":"UA202","points":0.10},
                {"flight_to_move":"DL707","blocking_flight":"DL303","points":0.10}],
            "passenger_rebookings":[
                {"passenger_id":"PAX-101","valid_flights":["AA808"],"points":0.07},
                {"passenger_id":"PAX-102","valid_flights":["AA808"],"points":0.07},
                {"passenger_id":"PAX-103","valid_flights":["UA909"],"points":0.07},
                {"passenger_id":"PAX-104","valid_flights":["UA909"],"points":0.07},
                {"passenger_id":"PAX-105","valid_flights":["DL010"],"points":0.07},
                {"passenger_id":"PAX-106","valid_flights":["DL010"],"points":0.07}],
            "crew_swaps":[
                {"crew_to_replace":"CRW-20","flight":"AA505","valid_replacements":["CRW-30","CRW-31"],"points":0.14},
                {"crew_to_replace":"CRW-22","flight":"DL707","valid_replacements":["CRW-30","CRW-31"],"points":0.14}],
            "cancellations_needed":[],"held_connections":[]},
    max_score=1.0,
    dynamic_events=[{"step":8,"type":"weather_update","severity":2,
                     "desc":"Thunderstorm weakening. Delays stabilizing."}],
)

# =============================================================
# TASK 3: Full Disruption (Hard) -- 30 steps, 17 issues
# =============================================================
TASK_HARD = dict(
    task_name="full_disruption",
    disruption_type="runway_closure_plus_weather",
    description=(
        "CRITICAL: Runway 27L closed. 8 flights disrupted. 3 crew exceeded limits. "
        "Hidden maintenance on AC-801 (SW808). T3 gates blocked by security. "
        "Cancel unrecoverable flight, resolve everything else."),
    max_steps=30, current_time="18:00", weather_severity=4, weather_desc="Severe weather, runway debris",
    flights=[
        _f("AA101","ORD","JFK","18:00","20:00","G1","delayed","CRW-10","AC-801",delay=120,pax=145),
        _f("UA202","SFO","JFK","18:15","20:15","G2","delayed","CRW-11","AC-802",delay=120,pax=132),
        _f("DL303","ATL","JFK","18:30","21:00","G3","delayed","CRW-12","AC-803",delay=150,pax=155),
        _f("SW404","DEN","JFK","18:45","20:45","G4","delayed","CRW-13","AC-804",delay=120,pax=98),
        _f("AA505","JFK","LAX","19:00","19:00","G1","scheduled","CRW-40","AC-811",pax=160),
        _f("UA606","JFK","SEA","19:15","19:15","G2","scheduled","CRW-41","AC-812",pax=110),
        _f("DL707","JFK","MIA","19:30","19:30","G3","scheduled","CRW-42","AC-813",pax=130),
        _f("SW808","JFK","BOS","21:00","21:00","G5","scheduled","CRW-43","AC-801",pax=85),
        _f("AA909","JFK","LAX","22:00","22:00","G7","scheduled","CRW-50","AC-821",pax=70),
        _f("UA010","JFK","SEA","22:30","22:30","G8","scheduled","CRW-51","AC-822",pax=55),
        _f("DL111","JFK","MIA","23:00","23:00","G9","scheduled","CRW-52","AC-823",pax=60),
        _f("AA212","JFK","BOS","22:00","22:00","G10","scheduled","CRW-53","AC-824",pax=45),
    ],
    gates=[_g("G1","T1","occupied",flight="AA101"),_g("G2","T1","occupied",flight="UA202"),
           _g("G3","T1","occupied",flight="DL303"),_g("G4","T1","occupied",flight="SW404"),
           _g("G5","T1","occupied",flight="SW808"),_g("G6","T2","available"),
           _g("G7","T2","occupied",flight="AA909"),_g("G8","T2","occupied",flight="UA010"),
           _g("G9","T2","occupied",flight="DL111"),_g("G10","T2","occupied",flight="AA212"),
           _g("G11","T3","blocked"),_g("G12","T3","blocked"),_g("G13","T3","blocked"),
           _g("G14","T2","available"),_g("G15","T2","available")],
    passengers=[_p("PAX-201","AA101",conn="AA505"),_p("PAX-202","AA101",conn="AA505"),
                _p("PAX-203","UA202",conn="UA606"),_p("PAX-204","UA202",conn="UA606"),
                _p("PAX-205","DL303",conn="DL707"),_p("PAX-206","DL303",conn="DL707"),
                _p("PAX-207","DL303",conn="DL707"),
                _p("PAX-208","SW808"),_p("PAX-209","SW808"),_p("PAX-210","SW808")],
    crew=[_c("CRW-40","AA505",0.3,"at_limit"),_c("CRW-41","UA606",0.2,"exceeded"),
          _c("CRW-42","DL707",0.5,"at_limit"),
          _c("CRW-60","RESERVE",8.0),_c("CRW-61","RESERVE",7.5),_c("CRW-62","RESERVE",6.0)],
    issues={"gate_conflicts":[
                {"flight_to_move":"AA505","blocking_flight":"AA101","points":0.06},
                {"flight_to_move":"UA606","blocking_flight":"UA202","points":0.06},
                {"flight_to_move":"DL707","blocking_flight":"DL303","points":0.06}],
            "passenger_rebookings":[
                {"passenger_id":"PAX-201","valid_flights":["AA909"],"points":0.05},
                {"passenger_id":"PAX-202","valid_flights":["AA909"],"points":0.05},
                {"passenger_id":"PAX-203","valid_flights":["UA010"],"points":0.05},
                {"passenger_id":"PAX-204","valid_flights":["UA010"],"points":0.05},
                {"passenger_id":"PAX-205","valid_flights":["DL111"],"points":0.05},
                {"passenger_id":"PAX-206","valid_flights":["DL111"],"points":0.05},
                {"passenger_id":"PAX-207","valid_flights":["DL111"],"points":0.05},
                {"passenger_id":"PAX-208","valid_flights":["AA212"],"points":0.05},
                {"passenger_id":"PAX-209","valid_flights":["AA212"],"points":0.05},
                {"passenger_id":"PAX-210","valid_flights":["AA212"],"points":0.05}],
            "crew_swaps":[
                {"crew_to_replace":"CRW-40","flight":"AA505","valid_replacements":["CRW-60","CRW-61","CRW-62"],"points":0.07},
                {"crew_to_replace":"CRW-41","flight":"UA606","valid_replacements":["CRW-60","CRW-61","CRW-62"],"points":0.07},
                {"crew_to_replace":"CRW-42","flight":"DL707","valid_replacements":["CRW-60","CRW-61","CRW-62"],"points":0.07}],
            "cancellations_needed":[{"flight_id":"SW808","reason":"maintenance_ac801","points":0.11}],
            "held_connections":[]},
    max_score=1.0,
    dynamic_events=[{"step":10,"type":"crew_release","crew_id":"CRW-43","condition":"SW808_cancelled",
                     "desc":"SW808 crew CRW-43 now available as reserve (6h duty)."}],
)

# =============================================================
# TASK 4: International Hub Crisis (Expert) -- 25 steps, 14 issues
# VIP passengers, wide-body aircraft, gate compatibility, weather
# =============================================================
TASK_EXPERT = dict(
    task_name="international_hub",
    disruption_type="international_hub_disruption",
    description=(
        "International hub crisis. Two wide-body transatlantic flights delayed, "
        "creating gate conflicts with domestic departures. VIP passengers and an "
        "unaccompanied minor need priority rebooking. One crew exceeded limits. "
        "Wide-body aircraft (AC-901, AC-902) ONLY fit at wide gates (G20-G24). "
        "Weather deteriorating -- act fast before severity reaches 5."),
    max_steps=25, current_time="15:00", weather_severity=3,
    weather_desc="Deteriorating -- expected to reach severe by 16:30",
    flights=[
        # Delayed international wide-body inbound
        _f("BA301","LHR","JFK","15:00","17:00","G20","delayed","CRW-70","AC-901",delay=120,pax=280,ac_size="wide_body",flight_type="international"),
        _f("LH502","FRA","JFK","15:30","17:30","G21","delayed","CRW-71","AC-902",delay=120,pax=310,ac_size="wide_body",flight_type="international"),
        # Outbound flights blocked
        _f("AA601","JFK","LAX","16:00","16:00","G20","scheduled","CRW-72","AC-811",pax=155),
        _f("DL702","JFK","ATL","16:30","16:30","G21","scheduled","CRW-73","AC-812",pax=120),
        # Domestic on-time
        _f("UA803","JFK","ORD","17:00","17:00","G5","scheduled","CRW-74","AC-813",pax=140),
        _f("SW904","JFK","DEN","17:30","17:30","G6","scheduled","CRW-75","AC-814",pax=95),
        # Rebooking options
        _f("AA610","JFK","LAX","20:00","20:00","G22","scheduled","CRW-76","AC-815",pax=60),
        _f("DL711","JFK","ATL","20:30","20:30","G23","scheduled","CRW-77","AC-816",pax=45),
        _f("UA812","JFK","ORD","21:00","21:00","G7","scheduled","CRW-78","AC-817",pax=50),
    ],
    gates=[
        _g("G5","T1","occupied",flight="UA803"),_g("G6","T1","occupied",flight="SW904"),
        _g("G7","T1","available"),_g("G8","T1","available"),
        # Wide-body capable gates (T3-International)
        _g("G20","T3","occupied",flight="BA301",size="wide"),
        _g("G21","T3","occupied",flight="LH502",size="wide"),
        _g("G22","T3","occupied",flight="AA610",size="wide"),
        _g("G23","T3","occupied",flight="DL711",size="wide"),
        _g("G24","T3","available",size="wide"),
    ],
    passengers=[
        # VIP passengers (higher rebooking priority)
        _p("PAX-301","BA301",conn="AA601",priority="vip"),
        _p("PAX-302","BA301",conn="AA601",priority="standard"),
        _p("PAX-303","BA301",conn="UA803",priority="unaccompanied_minor"),
        _p("PAX-304","LH502",conn="DL702",priority="vip"),
        _p("PAX-305","LH502",conn="DL702",priority="family"),
        _p("PAX-306","LH502",conn="UA803",priority="standard"),
    ],
    crew=[_c("CRW-72","AA601",0.4,"at_limit"),
          _c("CRW-80","RESERVE",8.0),_c("CRW-81","RESERVE",7.0)],
    issues={"gate_conflicts":[
                {"flight_to_move":"AA601","blocking_flight":"BA301","points":0.10},
                {"flight_to_move":"DL702","blocking_flight":"LH502","points":0.10}],
            "passenger_rebookings":[
                {"passenger_id":"PAX-301","valid_flights":["AA610"],"points":0.10},  # VIP
                {"passenger_id":"PAX-302","valid_flights":["AA610"],"points":0.05},
                {"passenger_id":"PAX-303","valid_flights":["UA812"],"points":0.12},  # Unaccompanied minor
                {"passenger_id":"PAX-304","valid_flights":["DL711"],"points":0.10},  # VIP
                {"passenger_id":"PAX-305","valid_flights":["DL711"],"points":0.08},  # Family
                {"passenger_id":"PAX-306","valid_flights":["UA812"],"points":0.05}],
            "crew_swaps":[
                {"crew_to_replace":"CRW-72","flight":"AA601","valid_replacements":["CRW-80","CRW-81"],"points":0.15}],
            "cancellations_needed":[],"held_connections":[
                {"departing_flight":"UA803","arriving_flight":"BA301","connecting_pax":["PAX-303"],
                 "hold_minutes":30,"points":0.15}]},
    max_score=1.0,
    dynamic_events=[{"step":8,"type":"weather_escalation","severity":4,
                     "desc":"Weather worsening to severe. Additional delays possible."},
                    {"step":15,"type":"weather_escalation","severity":5,
                     "desc":"EXTREME WEATHER. All remaining operations critical."}],
)

# =============================================================
# TASK 5: Overnight Recovery (Nightmare) -- 35 steps, 20 issues
# Minimal resources, multiple failures, triage required
# =============================================================
TASK_NIGHTMARE = dict(
    task_name="overnight_recovery",
    disruption_type="cascading_system_failure",
    description=(
        "NIGHTMARE SCENARIO: System-wide cascading failure during evening operations. "
        "6 flights delayed, 2 aircraft grounded (maintenance on AC-950, AC-951). "
        "4 crew at duty limits but only 2 reserves available. 12 passengers stranded "
        "including VIPs and families. Only 3 available gates. "
        "You CANNOT save everyone -- triage required. Prioritize high-value passengers."),
    max_steps=35, current_time="21:00", weather_severity=2,
    weather_desc="Light rain, visibility reduced",
    flights=[
        # Delayed inbound
        _f("AA401","ORD","JFK","21:00","23:00","G1","delayed","CRW-90","AC-950",delay=120,pax=160),
        _f("UA502","SFO","JFK","21:15","23:15","G2","delayed","CRW-91","AC-951",delay=120,pax=145),
        _f("DL603","ATL","JFK","21:30","23:00","G3","delayed","CRW-92","AC-903",delay=90,pax=130),
        _f("SW704","DEN","JFK","21:45","23:15","G4","delayed","CRW-93","AC-904",delay=90,pax=95),
        _f("BA805","LHR","JFK","22:00","00:00","G20","delayed","CRW-94","AC-960",delay=120,pax=290,ac_size="wide_body",flight_type="international"),
        _f("LH906","FRA","JFK","22:15","00:15","G21","delayed","CRW-95","AC-961",delay=120,pax=275,ac_size="wide_body",flight_type="international"),
        # Outbound -- blocked
        _f("AA511","JFK","LAX","22:00","22:00","G1","scheduled","CRW-A0","AC-911",pax=150),
        _f("UA612","JFK","SEA","22:15","22:15","G2","scheduled","CRW-A1","AC-912",pax=105),
        _f("DL713","JFK","MIA","22:30","22:30","G3","scheduled","CRW-A2","AC-913",pax=125),
        _f("SW814","JFK","DEN","22:45","22:45","G4","scheduled","CRW-A3","AC-914",pax=88),
        # Grounded -- must cancel (AC-950 and AC-951 maintenance)
        _f("AA915","JFK","BOS","00:30","00:30","G5","scheduled","CRW-A4","AC-950",pax=75),
        _f("UA016","JFK","ORD","01:00","01:00","G6","scheduled","CRW-A5","AC-951",pax=80),
        # Rebooking targets
        _f("AA520","JFK","LAX","02:00","02:00","G7","scheduled","CRW-A6","AC-921",pax=40),
        _f("UA621","JFK","SEA","02:30","02:30","G8","scheduled","CRW-A7","AC-922",pax=30),
        _f("DL722","JFK","MIA","02:00","02:00","G9","scheduled","CRW-A8","AC-923",pax=35),
        _f("AA823","JFK","BOS","02:00","02:00","G10","scheduled","CRW-A9","AC-924",pax=25),
        _f("UA024","JFK","ORD","02:30","02:30","G11","scheduled","CRW-B0","AC-925",pax=30),
        _f("SW925","JFK","DEN","03:00","03:00","G12","scheduled","CRW-B1","AC-926",pax=20),
    ],
    gates=[
        _g("G1","T1","occupied",flight="AA401"),_g("G2","T1","occupied",flight="UA502"),
        _g("G3","T1","occupied",flight="DL603"),_g("G4","T1","occupied",flight="SW704"),
        _g("G5","T1","occupied",flight="AA915"),_g("G6","T1","occupied",flight="UA016"),
        _g("G7","T1","available"),_g("G8","T1","available"),
        _g("G9","T2","occupied",flight="DL722"),_g("G10","T2","occupied",flight="AA823"),
        _g("G11","T2","occupied",flight="UA024"),_g("G12","T2","occupied",flight="SW925"),
        _g("G13","T2","available"),
        _g("G20","T3","occupied",flight="BA805",size="wide"),
        _g("G21","T3","occupied",flight="LH906",size="wide"),
    ],
    passengers=[
        _p("PAX-401","AA401",conn="AA511",priority="vip"),
        _p("PAX-402","AA401",conn="AA511",priority="family"),
        _p("PAX-403","UA502",conn="UA612",priority="standard"),
        _p("PAX-404","UA502",conn="UA612",priority="unaccompanied_minor"),
        _p("PAX-405","DL603",conn="DL713",priority="standard"),
        _p("PAX-406","DL603",conn="DL713",priority="vip"),
        _p("PAX-407","SW704",conn="SW814",priority="family"),
        _p("PAX-408","SW704",conn="SW814",priority="standard"),
        _p("PAX-409","AA915",priority="standard"),  # Flight to cancel
        _p("PAX-410","AA915",priority="family"),
        _p("PAX-411","UA016",priority="standard"),
        _p("PAX-412","UA016",priority="vip"),
    ],
    crew=[_c("CRW-A0","AA511",0.3,"at_limit"),_c("CRW-A1","UA612",0.2,"exceeded"),
          _c("CRW-A2","DL713",0.4,"at_limit"),_c("CRW-A3","SW814",0.1,"exceeded"),
          _c("CRW-C0","RESERVE",8.0),_c("CRW-C1","RESERVE",7.0)],
    issues={"gate_conflicts":[
                {"flight_to_move":"AA511","blocking_flight":"AA401","points":0.04},
                {"flight_to_move":"UA612","blocking_flight":"UA502","points":0.04},
                {"flight_to_move":"DL713","blocking_flight":"DL603","points":0.04},
                {"flight_to_move":"SW814","blocking_flight":"SW704","points":0.04}],
            "passenger_rebookings":[
                {"passenger_id":"PAX-401","valid_flights":["AA520"],"points":0.06},  # VIP
                {"passenger_id":"PAX-402","valid_flights":["AA520"],"points":0.05},  # Family
                {"passenger_id":"PAX-403","valid_flights":["UA621"],"points":0.04},
                {"passenger_id":"PAX-404","valid_flights":["UA621"],"points":0.07},  # Minor
                {"passenger_id":"PAX-405","valid_flights":["DL722"],"points":0.04},
                {"passenger_id":"PAX-406","valid_flights":["DL722"],"points":0.06},  # VIP
                {"passenger_id":"PAX-407","valid_flights":["SW925"],"points":0.05},  # Family
                {"passenger_id":"PAX-408","valid_flights":["SW925"],"points":0.03},
                {"passenger_id":"PAX-409","valid_flights":["AA823"],"points":0.03},
                {"passenger_id":"PAX-410","valid_flights":["AA823"],"points":0.05},  # Family
                {"passenger_id":"PAX-411","valid_flights":["UA024"],"points":0.04},
                {"passenger_id":"PAX-412","valid_flights":["UA024"],"points":0.06}], # VIP
            "crew_swaps":[
                {"crew_to_replace":"CRW-A0","flight":"AA511","valid_replacements":["CRW-C0","CRW-C1"],"points":0.06},
                {"crew_to_replace":"CRW-A1","flight":"UA612","valid_replacements":["CRW-C0","CRW-C1"],"points":0.06}],
            "cancellations_needed":[
                {"flight_id":"AA915","reason":"maintenance_ac950","points":0.07},
                {"flight_id":"UA016","reason":"maintenance_ac951","points":0.07}],
            "held_connections":[]},
    max_score=1.0,
    dynamic_events=[{"step":12,"type":"weather_update","severity":1,
                     "desc":"Rain clearing. Visibility improving."},
                    {"step":20,"type":"crew_fatigue","desc":"Ground crew shift change. Turnaround times +5 min."}],
)

# ================================================================
# TASK 6: Information Blackout (Expert+) -- 28 steps, 10 issues
# Comms failure, tarmac pressure, must discover info before acting
# ================================================================
TASK_BLACKOUT = dict(
    task_name="information_blackout", disruption_type="comms_system_failure",
    description=(
        "COMMS FAILURE: Airport communication system partially down. "
        "Two flights approaching FAA 3-hour tarmac limit. "
        "Use BROADCAST_DELAY for flights delayed >60min. "
        "Use ESCALATE_TO_SUPERVISOR if stuck."),
    max_steps=28, current_time="17:00", weather_severity=2, weather_desc="Overcast, gusty",
    flights=[
        _f("AA301","ORD","JFK","17:00","19:30","G1","delayed","CRW-50","AC-701",delay=150,pax=155),
        _f("UA402","SFO","JFK","17:15","19:45","G2","delayed","CRW-51","AC-702",delay=150,pax=140),
        _f("DL503","ATL","JFK","17:30","19:00","G3","delayed","CRW-52","AC-703",delay=90,pax=120),
        _f("AA604","JFK","LAX","18:00","18:00","G1","scheduled","CRW-53","AC-711",pax=135),
        _f("UA705","JFK","SEA","18:15","18:15","G2","scheduled","CRW-54","AC-712",pax=100),
        _f("DL806","JFK","MIA","18:30","18:30","G3","scheduled","CRW-55","AC-713",pax=115),
        _f("AA910","JFK","LAX","21:00","21:00","G7","scheduled","CRW-56","AC-721",pax=55),
        _f("UA011","JFK","SEA","21:30","21:30","G8","scheduled","CRW-57","AC-722",pax=45),
        _f("DL112","JFK","MIA","22:00","22:00","G9","scheduled","CRW-58","AC-723",pax=50),
    ],
    gates=[_g("G1","T1","occupied",flight="AA301"),_g("G2","T1","occupied",flight="UA402"),
           _g("G3","T1","occupied",flight="DL503"),_g("G4","T1","available"),
           _g("G5","T1","available"),_g("G6","T2","available"),
           _g("G7","T2","occupied",flight="AA910"),_g("G8","T2","occupied",flight="UA011"),
           _g("G9","T2","occupied",flight="DL112")],
    passengers=[
        _p("PAX-501","AA301",conn="AA604",priority="vip"),
        _p("PAX-502","AA301",conn="AA604"),
        _p("PAX-503","UA402",conn="UA705",priority="family"),
        _p("PAX-504","UA402",conn="UA705"),
        _p("PAX-505","DL503",conn="DL806"),
        _p("PAX-506","DL503",conn="DL806",priority="unaccompanied_minor"),
    ],
    crew=[_c("CRW-53","AA604",0.5,"at_limit"),_c("CRW-D0","RESERVE",8.0)],
    issues={"gate_conflicts":[
                {"flight_to_move":"AA604","blocking_flight":"AA301","points":0.08},
                {"flight_to_move":"UA705","blocking_flight":"UA402","points":0.08},
                {"flight_to_move":"DL806","blocking_flight":"DL503","points":0.08}],
            "passenger_rebookings":[
                {"passenger_id":"PAX-501","valid_flights":["AA910"],"points":0.12},
                {"passenger_id":"PAX-502","valid_flights":["AA910"],"points":0.10},
                {"passenger_id":"PAX-503","valid_flights":["UA011"],"points":0.10},
                {"passenger_id":"PAX-504","valid_flights":["UA011"],"points":0.10},
                {"passenger_id":"PAX-505","valid_flights":["DL112"],"points":0.10},
                {"passenger_id":"PAX-506","valid_flights":["DL112"],"points":0.10}],
            "crew_swaps":[{"crew_to_replace":"CRW-53","flight":"AA604","valid_replacements":["CRW-D0"],"points":0.14}],
            "cancellations_needed":[],"held_connections":[]},
    max_score=1.0,
    dynamic_events=[{"step":5,"type":"tarmac_warning","desc":"AA301 approaching 3h tarmac limit!"},
                    {"step":10,"type":"comms_partial","desc":"Partial comms restored."}],
)

SCENARIOS = {
    "single_delay": TASK_EASY,
    "cascading_delays": TASK_MEDIUM,
    "full_disruption": TASK_HARD,
    "international_hub": TASK_EXPERT,
    "overnight_recovery": TASK_NIGHTMARE,
    "information_blackout": TASK_BLACKOUT,
}
ALL_TASK_NAMES = list(SCENARIOS.keys())
