"""Single sign-on for the console, over plain OpenID Connect.

This is the *portal* door, and only that. A human signs in with their identity
provider and receives the same signed session cookie a password login issues;
everything downstream is unchanged. Agents keep authenticating with a Project
API Key, so an identity provider outage can never stop an agent being
governed, and a leaked session is never an agent credential.

There is no provider-specific code here on purpose. The issuer's discovery
document supplies the endpoints, which is what makes Auth0, Okta, Google,
Entra and Keycloak the same three settings rather than four integrations.

The flow, once:

    /v1/auth/oidc/start     state + nonce + PKCE verifier -> a short-lived
                            signed cookie, then redirect to the provider
    /v1/auth/oidc/callback  check state, exchange the code with the verifier,
                            verify the id_token against the provider's JWKS,
                            then link or create the account

What this refuses, deliberately:

* an id_token whose email is not verified - that is the classic path to taking
  over someone else's account by registering their address elsewhere;
* an issuer or audience that is not the configured one;
* a reused or missing state, and a nonce that does not match the one issued.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWKSet

from agent_plane.config import Settings

# The handshake cookie only has to survive a redirect to the provider and back.
HANDSHAKE_COOKIE = "ap_oidc"
HANDSHAKE_TTL_SECONDS = 600
DISCOVERY_TTL_SECONDS = 3600
SCOPE = "openid email profile"


class OidcError(Exception):
    """Sign-in failed. The message is safe to show a human."""


@dataclass(frozen=True)
class Identity:
    """What the provider vouched for, once verified."""

    issuer: str
    subject: str
    email: str
    name: str = ""


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


# --------------------------------------------------------------------------- #
# discovery
# --------------------------------------------------------------------------- #
class Discovery:
    """The provider's published endpoints, fetched once and cached.

    Cached because it is fetched on every sign-in attempt otherwise, and an
    unauthenticated endpoint that makes an outbound request per call is a way
    to have someone else's rate limit become your outage.
    """

    def __init__(self, client: httpx.Client | None = None, ttl: int = DISCOVERY_TTL_SECONDS):
        self._client = client
        self._ttl = ttl
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}

    def document(self, issuer: str) -> dict[str, Any]:
        cached = self._cache.get(issuer)
        if cached and cached[0] > time.time():
            return cached[1]
        url = issuer.rstrip("/") + "/.well-known/openid-configuration"
        try:
            client = self._client or httpx.Client(timeout=10.0)
            response = client.get(url)
            response.raise_for_status()
            document = response.json()
        except Exception as exc:  # noqa: BLE001 - any failure here is "provider unreachable"
            raise OidcError("The identity provider could not be reached") from exc
        for field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            if not document.get(field):
                raise OidcError(f"The provider's discovery document has no {field}")
        self._cache[issuer] = (time.time() + self._ttl, document)
        return document


# --------------------------------------------------------------------------- #
# the two halves of the handshake
# --------------------------------------------------------------------------- #
def begin(settings: Settings, discovery: Discovery, *, redirect_uri: str,
          next_path: str = "/") -> tuple[str, dict[str, str]]:
    """The provider URL to send the browser to, and the handshake to remember.

    PKCE is used even though this is a confidential client: it costs one hash
    and removes the value of an intercepted authorization code.
    """
    document = discovery.document(settings.oidc_issuer)
    verifier = _b64u(secrets.token_bytes(48))
    handshake = {
        "state": _b64u(secrets.token_bytes(24)),
        "nonce": _b64u(secrets.token_bytes(24)),
        "verifier": verifier,
        "redirect_uri": redirect_uri,
        "next": next_path,
    }
    query = {
        "response_type": "code",
        "client_id": settings.oidc_client_id,
        "redirect_uri": redirect_uri,
        "scope": SCOPE,
        "state": handshake["state"],
        "nonce": handshake["nonce"],
        "code_challenge": _b64u(hashlib.sha256(verifier.encode()).digest()),
        "code_challenge_method": "S256",
    }
    return f"{document['authorization_endpoint']}?{urlencode(query)}", handshake


def complete(settings: Settings, discovery: Discovery, *, code: str, state: str,
             handshake: dict[str, Any] | None,
             client: httpx.Client | None = None) -> Identity:
    """Exchange the code and return the identity the provider vouched for."""
    if not handshake:
        raise OidcError("This sign-in took too long. Start again.")
    if not state or not secrets.compare_digest(str(handshake.get("state", "")), state):
        raise OidcError("This sign-in could not be verified. Start again.")

    document = discovery.document(settings.oidc_issuer)
    http = client or httpx.Client(timeout=10.0)
    try:
        response = http.post(document["token_endpoint"], data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": handshake.get("redirect_uri", ""),
            "client_id": settings.oidc_client_id,
            "client_secret": settings.oidc_client_secret,
            "code_verifier": handshake.get("verifier", ""),
        }, headers={"Accept": "application/json"})
    except Exception as exc:  # noqa: BLE001
        raise OidcError("The identity provider could not be reached") from exc
    if response.status_code >= 400:
        # The provider's own error text can carry the code and the client id.
        raise OidcError("The identity provider rejected this sign-in")
    token = response.json().get("id_token")
    if not token:
        raise OidcError("The identity provider returned no id_token")

    claims = _verify(settings, document, token, handshake, http)
    return _identity(settings, claims)


def _signing_key(document: dict[str, Any], token: str, client: httpx.Client) -> Any:
    """The provider's public key for this token.

    Fetched with the same HTTP client as everything else rather than through
    PyJWKClient, which carries its own network stack: one client means one
    timeout, one proxy configuration, and one thing to intercept in a test.
    """
    response = client.get(document["jwks_uri"])
    response.raise_for_status()
    keys = PyJWKSet.from_dict(response.json())
    kid = jwt.get_unverified_header(token).get("kid")
    if kid:
        return keys[kid].key
    usable = [k for k in keys.keys if k.public_key_use in ("sig", None)]
    if len(usable) != 1:
        raise OidcError("The provider published several keys and the token names none")
    return usable[0].key


def _verify(settings: Settings, document: dict[str, Any], token: str,
            handshake: dict[str, Any], client: httpx.Client) -> dict[str, Any]:
    try:
        claims = jwt.decode(
            token,
            _signing_key(document, token, client),
            algorithms=[jwt.get_unverified_header(token).get("alg", "RS256")],
            audience=settings.oidc_client_id,
            issuer=document.get("issuer") or settings.oidc_issuer,
            options={"require": ["exp", "iat", "sub"]},
        )
    except Exception as exc:  # noqa: BLE001 - a bad token is a bad token
        raise OidcError("The identity provider's response could not be verified") from exc

    nonce = handshake.get("nonce")
    if nonce and claims.get("nonce") != nonce:
        raise OidcError("The identity provider's response could not be verified")
    return claims


def _identity(settings: Settings, claims: dict[str, Any]) -> Identity:
    email = str(claims.get("email") or "").strip().lower()
    if not email:
        raise OidcError("Your identity provider did not share an email address")
    # An unverified address is an address someone else may own.
    if claims.get("email_verified") is False:
        raise OidcError("Your email address is not verified with your identity provider")
    domains = settings.oidc_domains
    if domains and email.rsplit("@", 1)[-1] not in domains:
        raise OidcError("That email domain is not allowed to sign in here")
    return Identity(issuer=str(claims.get("iss") or settings.oidc_issuer),
                    subject=str(claims["sub"]),
                    email=email,
                    name=str(claims.get("name") or "").strip())
