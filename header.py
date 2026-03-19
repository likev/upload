from fastapi import Request
from fastapi.responses import JSONResponse


def _cors_headers(request: Request) -> dict:
    origin = request.headers.get("origin")
    headers = {
        "Content-Security-Policy": "default-src 'none'; connect-src 'self';",
        "Vary": "Origin",
    }
    if not origin:
        return headers

    headers["Access-Control-Allow-Origin"] = origin
    headers["Access-Control-Allow-Credentials"] = "true"
    headers["Access-Control-Allow-Methods"] = request.headers.get(
        "access-control-request-method", request.method
    )
    headers["Access-Control-Allow-Headers"] = request.headers.get(
        "access-control-request-headers", "*"
    )
    return headers


def build_header_response(request: Request) -> JSONResponse:
    payload = {
        "method": request.method,
        "headers": dict(request.headers),
    }
    return JSONResponse(payload, headers=_cors_headers(request))
