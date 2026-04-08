"""
Airport Operations Recovery -- EnvClient
"""

from openenv.core.env_client import EnvClient
from openenv.core.client_types import StepResult
from models import AirportAction, AirportObservation, AirportState


class AirportRecoveryEnv(EnvClient[AirportAction, AirportObservation, AirportState]):

    def _step_payload(self, action: AirportAction) -> dict:
        return {"command": action.command}

    def _parse_result(self, payload: dict) -> StepResult:
        obs_data = payload.get("observation", {})
        return StepResult(
            observation=AirportObservation(
                done=payload.get("done", False),
                reward=payload.get("reward"),
                current_time=obs_data.get("current_time", ""),
                airport_status=obs_data.get("airport_status", ""),
                flights=obs_data.get("flights", []),
                gates=obs_data.get("gates", []),
                passenger_issues=obs_data.get("passenger_issues", []),
                crew_issues=obs_data.get("crew_issues", []),
                pending_issues_count=obs_data.get("pending_issues_count", 0),
                resolved_issues_count=obs_data.get("resolved_issues_count", 0),
                total_issues_count=obs_data.get("total_issues_count", 0),
                message=obs_data.get("message", ""),
                available_commands=obs_data.get("available_commands", []),
                score=obs_data.get("score", 0.0),
            ),
            reward=payload.get("reward"),
            done=payload.get("done", False),
        )

    def _parse_state(self, payload: dict) -> AirportState:
        return AirportState(
            episode_id=payload.get("episode_id"),
            step_count=payload.get("step_count", 0),
            task_name=payload.get("task_name", ""),
            disruption_type=payload.get("disruption_type", ""),
            total_flights=payload.get("total_flights", 0),
            resolved_issues=payload.get("resolved_issues", 0),
            total_issues=payload.get("total_issues", 0),
            max_steps=payload.get("max_steps", 0),
        )
