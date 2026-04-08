"""
Airport Operations Recovery -- FastAPI Server
"""

from openenv.core.env_server import create_fastapi_app

from models import AirportAction, AirportObservation
from server.environment import AirportRecoveryEnvironment

app = create_fastapi_app(AirportRecoveryEnvironment, AirportAction, AirportObservation)


def main():
    """Entry point for `uv run server` / pyproject.toml [project.scripts]."""
    import uvicorn
    uvicorn.run("server.app:app", host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()