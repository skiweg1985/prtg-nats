"""The error model of the API.

The rule this module exists to enforce: **the backend never returns translated
prose.** An error carries a stable ``code``, a ``message_key`` and structured
``params``; the browser turns that into a sentence in the operator's language.
``details`` stays untranslated on purpose - it is the technical output an
administrator needs verbatim.

Anything user-visible that is not a translation key belongs in ``details``.
"""

from __future__ import annotations

from typing import Any, Self

from fastapi import Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.logging import get_correlation_id
from app.core.redaction import redact

# Namespace of every translation key this module emits. The frontend has a
# matching file per namespace, and a test asserts both sides agree.
ERROR_KEY_PREFIX = "errors"


class AppError(Exception):
    """Base class for every failure the API reports deliberately.

    Subclasses set ``code`` and ``http_status``; the translation key is derived
    from the code so the two cannot drift apart.
    """

    code: str = "internal.unexpected"
    http_status: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    retryable: bool = False

    def __init__(
        self,
        *,
        params: dict[str, Any] | None = None,
        details: str | None = None,
        fields: list[str] | None = None,
        retryable: bool | None = None,
    ) -> None:
        self.params = redact(params or {})
        self.details = details
        self.fields = fields or []
        if retryable is not None:
            self.retryable = retryable
        super().__init__(self.code)

    @property
    def message_key(self) -> str:
        return f"{ERROR_KEY_PREFIX}.{self.code}"

    def to_payload(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message_key": self.message_key,
                "params": self.params,
                "fields": self.fields,
                "details": self.details,
                "correlation_id": get_correlation_id(),
                "retryable": self.retryable,
            }
        }

    def to_response(self) -> JSONResponse:
        return JSONResponse(
            status_code=self.http_status,
            content=jsonable_encoder(self.to_payload()),
        )


# --- Client errors ----------------------------------------------------------


class NotFoundError(AppError):
    code = "common.not_found"
    http_status = status.HTTP_404_NOT_FOUND

    @classmethod
    def of(cls, resource: str, identifier: str) -> Self:
        return cls(params={"resource": resource, "id": identifier})


class ValidationFailedError(AppError):
    code = "common.validation_failed"
    # 422 by its current Starlette name; the older constant is deprecated.
    http_status = 422


class ConflictError(AppError):
    code = "common.conflict"
    http_status = status.HTTP_409_CONFLICT


class AuthenticationRequiredError(AppError):
    code = "auth.authentication_required"
    http_status = status.HTTP_401_UNAUTHORIZED


class InvalidCredentialsError(AppError):
    code = "auth.invalid_credentials"
    http_status = status.HTTP_401_UNAUTHORIZED


class AccountLockedError(AppError):
    code = "auth.account_locked"
    http_status = status.HTTP_429_TOO_MANY_REQUESTS
    retryable = True


class PermissionDeniedError(AppError):
    code = "auth.permission_denied"
    http_status = status.HTTP_403_FORBIDDEN

    @classmethod
    def of(cls, permission: str) -> Self:
        return cls(params={"permission": permission})


class SetupRequiredError(AppError):
    """No administrator exists yet; the first-run wizard has to run."""

    code = "auth.setup_required"
    http_status = status.HTTP_409_CONFLICT


# --- Infrastructure errors --------------------------------------------------


class ProbeUnreachableError(AppError):
    code = "probe.unreachable"
    http_status = status.HTTP_502_BAD_GATEWAY
    retryable = True

    @classmethod
    def of(cls, probe: str, *, details: str | None = None) -> Self:
        return cls(params={"probe": probe}, details=details)


class ProbeProtocolError(AppError):
    """The probe answered, but not the way its helper protocol prescribes."""

    code = "probe.protocol_error"
    http_status = status.HTTP_502_BAD_GATEWAY


class ProbeRejectedError(AppError):
    """The probe helper refused the request with its own message."""

    code = "probe.request_rejected"
    http_status = status.HTTP_502_BAD_GATEWAY


class ProbeHelperOutdatedError(AppError):
    """The probe carries a helper that predates the request it was asked.

    Separated from the general refusal because the cause and the way out are
    entirely different: nothing about the request was wrong, and the fix is to
    renew the helper rather than to correct anything.
    """

    code = "probe.helper_outdated"
    http_status = status.HTTP_502_BAD_GATEWAY


class ProbePackageMissingError(AppError):
    """The probe carries no MPP package, so there is nothing to configure.

    Separated from the general refusal because the probe refuses nothing: it
    has no prtg.mpprobe.service, so every configuration transaction dies in
    the activate step with a message about a missing systemd unit - true, and
    no help at all to whoever has to fix it. Enrolment never installs the
    package; only the bootstrap script does.
    """

    code = "probe.package_missing"
    http_status = status.HTTP_409_CONFLICT


class DockerUnavailableError(AppError):
    """The Docker socket is not mounted, so server lifecycle actions are off."""

    code = "docker.unavailable"
    http_status = status.HTTP_503_SERVICE_UNAVAILABLE


class RuntimeStateError(AppError):
    """runtime/ is missing or incomplete - the stack was never set up here."""

    code = "runtime.incomplete"
    http_status = status.HTTP_503_SERVICE_UNAVAILABLE


class EnrollmentTokenInvalidError(AppError):
    """One answer for unknown, expired, revoked and already used.

    Deliberately undifferentiated: the caller is unauthenticated, and a
    distinct "expired" would confirm that the token existed. There is nothing
    an honest host does with the difference that it cannot do with a retry.
    """

    code = "enrollment.token_invalid"
    http_status = status.HTTP_404_NOT_FOUND


class NatsReloadRefusedError(AppError):
    """NATS took the signal and kept its old configuration.

    Not reloadable means not reloadable: a changed server name, port or TLS
    setting needs a restart. Raised rather than logged because the caller
    just wrote an account nobody can use yet.
    """

    code = "nats.reload_refused"
    http_status = status.HTTP_409_CONFLICT


class HostAlreadyEnrolledError(AppError):
    """Another probe already claims this address.

    The management access on a host belongs to the host, not to an inventory
    entry. A second entry for the same address shares it, and retiring either
    one revokes it for both - leaving a probe that is connected to NATS and
    unreachable at the same time.
    """

    code = "probe.host_already_enrolled"
    http_status = status.HTTP_409_CONFLICT


class HostKeyMismatchError(AppError):
    """A pinned host presented a different key than the one on record.

    Never resolved by overwriting. Either the host was rebuilt - then the old
    entry is removed deliberately - or something is answering in its place.
    """

    code = "probe.host_key_mismatch"
    http_status = status.HTTP_409_CONFLICT


class StackUpdateUnavailableError(AppError):
    """This installation cannot update itself, and the reason is structural.

    No Docker socket, no Compose labels to find the checkout by, or no updater
    image yet. None of these is fixed by trying again, so the interface hides
    the action and shows what is missing instead.
    """

    code = "stack.update_unavailable"
    http_status = status.HTTP_503_SERVICE_UNAVAILABLE


class StackUpdateBlockedError(AppError):
    """The installation could update itself, but not right now.

    A modified checkout, another job still running, a branch that has diverged.
    Each of these is a thing an operator can resolve, which is why they are
    refused up front rather than discovered halfway through a rollout.
    """

    code = "stack.update_blocked"
    http_status = status.HTTP_409_CONFLICT


async def app_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)  # noqa: S101 - handler is registered for it
    return exc.to_response()


async def validation_error_handler(_: Request, exc: Exception) -> JSONResponse:
    """Translate FastAPI's own validation errors into our envelope."""
    assert isinstance(exc, RequestValidationError)  # noqa: S101
    fields = [
        ".".join(str(part) for part in error["loc"] if part != "body")
        for error in exc.errors()
    ]
    error = ValidationFailedError(
        fields=[field for field in fields if field],
        details="; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
        ),
    )
    return error.to_response()


async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
    """Last resort: never leak a traceback, always leave a correlation id."""
    return AppError(details=type(exc).__name__).to_response()
