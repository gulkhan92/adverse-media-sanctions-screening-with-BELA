"""RFC 7807 problem responses and the exception type used by handlers."""

from __future__ import annotations

from typing import Any

PROBLEM_TYPE = "urn:atlas:problem:"


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        slug: str,
        title: str,
        detail: str | None = None,
        headers: dict[str, str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(title)
        self.status = status
        self.slug = slug
        self.title = title
        self.detail = detail
        self.headers = headers or {}
        self.extra = extra or {}


def unauthorized(detail: str = "Authentication is required.") -> ApiError:
    return ApiError(401, "unauthorized", "Unauthorized", detail)


def forbidden(detail: str = "You do not have permission to perform this action.") -> ApiError:
    return ApiError(403, "forbidden", "Forbidden", detail)


def not_found(what: str = "Resource") -> ApiError:
    return ApiError(404, "not-found", "Not found", f"{what} was not found.")


def bad_request(detail: str, slug: str = "bad-request") -> ApiError:
    return ApiError(400, slug, "Bad request", detail)


def conflict(detail: str, slug: str = "conflict") -> ApiError:
    return ApiError(409, slug, "Conflict", detail)


def too_many(retry_after: int) -> ApiError:
    return ApiError(
        429,
        "rate-limited",
        "Too many requests",
        "The request rate limit was exceeded.",
        headers={"Retry-After": str(max(1, retry_after))},
    )


def problem_body(error: ApiError, path: str, request_id: str) -> dict[str, Any]:
    body: dict[str, Any] = {
        "type": PROBLEM_TYPE + error.slug,
        "title": error.title,
        "status": error.status,
        "instance": path,
        "request_id": request_id,
    }
    if error.detail:
        body["detail"] = error.detail
    body.update(error.extra)
    return body


PROBLEM_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"description": "Authentication required", "content": {"application/problem+json": {}}},
    403: {"description": "Permission denied", "content": {"application/problem+json": {}}},
    422: {"description": "Validation error", "content": {"application/problem+json": {}}},
    429: {"description": "Rate limit exceeded", "content": {"application/problem+json": {}}},
}
