"""FastAPI Agent Service Application Entrypoint and Runner."""

import uvicorn
from fastapi import FastAPI

from chappie.api import create_app

app: FastAPI = create_app()


def run() -> None:
    """Run the Agent FastAPI service with Uvicorn."""
    uvicorn.run(
        "services.agent.main:app",
        host="0.0.0.0",
        port=8001,
        reload=False,
    )


if __name__ == "__main__":
    run()
