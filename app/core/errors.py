from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


@dataclass
class AppError(Exception):
    code: str
    message: str
    status_code: int = 400
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "error": {
                "code": self.code,
                "message": self.message,
            }
        }
        if self.details:
            payload["error"]["details"] = self.details
        return payload


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

    @app.exception_handler(Exception)
    async def handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        error = AppError(
            code="internal_error",
            message="Unexpected server error.",
            status_code=500,
            details={"type": exc.__class__.__name__},
        )
        return JSONResponse(status_code=500, content=error.to_dict())
