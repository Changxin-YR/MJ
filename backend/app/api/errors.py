from uuid import uuid4

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class APIError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, details: dict | None = None):
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}


def trace_id(request: Request) -> str:
    return getattr(request.state, "trace_id", str(uuid4()))


def ok(request: Request, data):
    return {"success": True, "data": data, "trace_id": trace_id(request)}


async def api_error_handler(request: Request, exc: APIError):
    return JSONResponse(
        {"success": False, "error": {"code": exc.code, "message": exc.message, "details": exc.details}, "trace_id": trace_id(request)},
        status_code=exc.status,
    )


async def validation_error_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        {"success": False, "error": {"code": "INVALID_PARAMETER", "message": "Invalid request", "details": {"fields": [{"loc": list(e["loc"]), "type": e["type"]} for e in exc.errors()]}}, "trace_id": trace_id(request)},
        status_code=422,
    )
