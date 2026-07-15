from fastapi import FastAPI
from fastapi.responses import PlainTextResponse


def create_app() -> FastAPI:
    app = FastAPI(title="WB Task Bot — веб-админка")

    @app.get("/healthz", response_class=PlainTextResponse)
    async def healthz() -> str:
        return "ok"

    return app


app = create_app()
