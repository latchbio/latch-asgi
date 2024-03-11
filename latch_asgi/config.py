from dataclasses import dataclass

from latch_config.config import read_config


@dataclass(frozen=True)
class AuthConfig:
    audience: str
    self_signed_jwk: str
    cross_server_token: str = ""
    allow_spoofing: bool = False


config = read_config(AuthConfig, "auth_")
