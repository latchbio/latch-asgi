# todo(maximsmol): move JWT stuff into envoy

import re
from dataclasses import dataclass
from http import HTTPStatus
from typing import Literal, Self

import jwt
from jwt import PyJWKClient
from latch_o11y.o11y import app_tracer, trace_app_function

from .config import config
from .framework.http import HTTPErrorResponse

jwk_client = PyJWKClient("https://latchai.us.auth0.com/.well-known/jwks.json")
debug_authentication_header_regex = re.compile(
    r"""
    ^(
        Latch-Spoof-Sub \s+ (?P<oauth_sub>.*)
    )$
    """,
    re.IGNORECASE | re.VERBOSE,
)
authentication_header_regex = re.compile(
    r"""
    ^(
        Bearer \s+ (?P<oauth_token>.*) |
        Latch-Execution-Token \s+ (?P<execution_token>.*) |
        Latch-SDK-Token \s+ (?P<sdk_token>.*)
    )$
    """,
    re.IGNORECASE | re.VERBOSE,
)


class _HTTPUnauthorized(HTTPErrorResponse):
    """WARNING: HTTPForbidden is the correct error to use in virtually all cases"""

    def __init__(
        self: Self,
        error_description: str,
        error: (Literal["invalid_request", "invalid_token", "insufficient_scope"]),
    ) -> None:
        escaped_description = error_description.replace('"', '\\"')
        super().__init__(
            HTTPStatus.UNAUTHORIZED,
            error_description,
            headers={
                "WWW-Authenticate": (
                    f'Bearer error="{error}", error_description="{escaped_description}"'
                )
            },
        )


@dataclass(frozen=True, kw_only=True)
class Authorization:
    oauth_sub: str | None = None
    execution_token: str | None = None
    sdk_token: str | None = None

    def unauthorized_if_none(self: Self) -> None:
        if self.oauth_sub is not None:
            return
        if self.execution_token is not None:
            return
        if self.sdk_token is not None:
            return

        raise _HTTPUnauthorized("Authenticaton required", error="invalid_request")


@trace_app_function
def get_signer_sub(auth_header: str) -> Authorization:
    if auth_header is None:
        return Authorization()

    # todo(maximsmol): allow spoofing in prod if using a secret key
    # todo(maximsmol): allow spoofing using account ID instead of auth0 sub
    if config.allow_spoofing:
        auth_match = debug_authentication_header_regex.match(auth_header)
        if auth_match is not None:
            return Authorization(oauth_sub=auth_match.group("oauth_sub"))

    with app_tracer.start_as_current_span("match regex"):
        auth_match = authentication_header_regex.match(auth_header)
        if auth_match is None:
            raise _HTTPUnauthorized(
                error_description=(
                    "The `Authorization` header did not match the expected format."
                    " Accepted schemes: `Bearer` and `Latch-Execution-Token`"
                ),
                error="invalid_token",
            )

        execution_token = auth_match.group("execution_token")
        if execution_token is not None:
            return Authorization(execution_token=execution_token)

        sdk_token = auth_match.group("sdk_token")
        if sdk_token is not None:
            return Authorization(sdk_token=sdk_token)

        oauth_token = auth_match.group("oauth_token")

        if oauth_token is None:
            raise _HTTPUnauthorized(
                error_description="Could not parse the OAuth token",
                error="invalid_token",
            )

    with app_tracer.start_as_current_span("fetch jwk"):
        try:
            jwt_key = jwk_client.get_signing_key_from_jwt(oauth_token).key
        except jwt.exceptions.InvalidTokenError as e:
            raise _HTTPUnauthorized(
                error_description="JWT decoding failed", error="invalid_token"
            ) from e
        except jwt.exceptions.PyJWKClientError:
            # fixme(maximsmol): gut this abomination
            jwt_key = config.self_signed_jwk
            # raise _HTTPUnauthorized(
            #     error_description="No matching JWK found",
            #     error="invalid_token",
            # ) from e

    with app_tracer.start_as_current_span("decode jwt"):
        try:
            jwt_data: dict[str, str] = jwt.decode(
                oauth_token,
                key=jwt_key,
                algorithms=["RS256", "HS256"],
                # fixme(maximsmol): gut this abomination
                audience=(
                    config.audience if jwt_key != config.self_signed_jwk else None
                ),
            )
        except jwt.exceptions.InvalidTokenError as e:
            # todo(maximsmol): filter out scope failures and include the correct error code
            raise _HTTPUnauthorized(
                error_description="The JWT failed signature verification",
                error="invalid_token",
            ) from e

    return Authorization(oauth_sub=jwt_data["sub"])
