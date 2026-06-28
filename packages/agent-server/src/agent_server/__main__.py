"""Entry point: python -m agent_server"""

import uvicorn


def main() -> None:
    uvicorn.run(
        "agent_server.app:app",
        host="0.0.0.0",
        port=8123,
        reload=True,
    )


if __name__ == "__main__":
    main()
