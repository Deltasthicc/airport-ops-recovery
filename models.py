"""
Airport Operations Recovery -- Pydantic Type Definitions
"""
from typing import Dict, List, Optional
from openenv.core.env_server import Action, Observation, State


class AirportAction(Action):
    """Agent sends a text command."""
    command: str


class AirportObservation(Observation):
    """Full airport state. Inherits done: bool and reward: Optional[float]."""
    current_time: str = ""
    airport_status: str = ""
    weather_severity: int = 1
    weather_description: str = ""
    flights: List[Dict] = []
    gates: List[Dict] = []
    passenger_issues: List[Dict] = []
    crew_issues: List[Dict] = []
    pending_issues_count: int = 0
    resolved_issues_count: int = 0
    total_issues_count: int = 0
    compensation_cost_usd: float = 0.0
    tarmac_violations: int = 0
    message: str = ""
    available_commands: List[str] = []
    score: float = 0.0


class AirportState(State):
    """Episode metadata. Inherits episode_id + step_count."""
    task_name: str = ""
    disruption_type: str = ""
    total_flights: int = 0
    resolved_issues: int = 0
    total_issues: int = 0
    max_steps: int = 0
