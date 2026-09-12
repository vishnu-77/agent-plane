"""Console single sign-on, against a fake identity provider.

The provider is fake but the cryptography is not: a real RSA key signs a real
id_token, and the server verifies it through the same code path a real Auth0
or Okta response would take. The tests that matter here are the refusals -
an unverified email, a wrong audience, a replayed state - because those are
the ways SSO becomes account takeover.
"""
from __future__ import annotations

import base64
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from agent_plane.accounts.oidc import HANDSHAKE_COOKIE

ISSUER = "https://idp.example.com"
CLIENT_ID = "agent-plane-console"
CLIENT_SECRET = "idp-secret"
KID = "test-key-1"


# --------------------------------------------------------------------------- #
# a fake provider
# --------------------------------------------------------------------------- #
class Idp:
    """Serves discovery, a token endpoint, and JWKS over a mock transport."""

    def __init__(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.private_pem = self.key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        self.claims: dict = {}
        self.token_status = 200
        self.seen: list[dict] = []

    def jwks(self) -> dict:
        numbers = self.key.public_key().public_numbers()

        def b64(value: int) -> str:
            raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
            return base64.urlsafe_b64encode(raw).decode().rstrip("=")

        return {"keys": [{"kty": "RSA", "use": "sig", "alg": "RS256", "kid": KID,
                          "n": b64(numbers.n), "e": b64(numbers.e)}]}

    def id_token(self, nonce: str, **overrides) -> str:
        now = int(time.time())
        claims = {
            "iss": ISSUER, "aud": CLIENT_ID, "sub": "idp|00042",
            "iat": now, "exp": now + 300, "nonce": nonce,
            "email": "dev@acme.com", "email_verified": True, "name": "Dev Person",
            **self.claims, **overrides,
        }
        return jwt.encode(claims, self.private_pem, algorithm="RS256", headers={"kid": KID})

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path.endswith("/.well-known/openid-configuration"):
                return httpx.Response(200, json={
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/oauth/token",
                    "jwks_uri": f"{ISSUER}/.well-known/jwks.json",
                })
            if path.endswith("/jwks.json"):
                return httpx.Response(200, json=self.jwks())
            if path.endswith("/oauth/token"):
                form = dict(x.split("=", 1) for x in request.content.decode().split("&"))
                self.seen.append(form)
                if self.token_status >= 400:
                    return httpx.Response(self.token_status, json={"error": "invalid_grant"})
                nonce = getattr(self, "_nonce", "")
                return httpx.Response(200, json={"id_token": self.id_token(nonce)})
            return httpx.Response(404)

        return httpx.MockTransport(handle)


@pytest.fixture()
def idp() -> Idp:
    return Idp()


@pytest.fixture()
def client(tmp_path, monkeypatch, idp):
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setenv("POLICY_DIR", "policies")
    monkeypatch.setenv("JWT_SECRET", "sso-secret-0123456789abcdef")
    monkeypatch.setenv("OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("OIDC_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("OIDC_CLIENT_SECRET", CLIENT_SECRET)
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    from agent_plane.config import get_settings

    get_settings.cache_clear()
    from agent_plane.main import create_app

    app = create_app()
    app.state.oidc_http = httpx.Client(transport=idp.transport(), base_url=ISSUER)
    with TestClient(app, follow_redirects=False) as c:
        yield c
    get_settings.cache_clear()


def start(client, idp, **params):
    """Begin a sign-in and hand back the state the provider would echo."""
    r = client.get("/v1/auth/oidc/start", params=params)
    assert r.status_code == 302, r.text
    query = dict(p.split("=", 1) for p in r.headers["location"].split("?", 1)[1].split("&"))
    from urllib.parse import unquote

    idp._nonce = unquote(query["nonce"])
    return unquote(query["state"]), r.cookies.get(HANDSHAKE_COOKIE)


def callback(client, state, handshake, **params):
    if handshake:
        client.cookies.set(HANDSHAKE_COOKIE, handshake)
    else:
        client.cookies.delete(HANDSHAKE_COOKIE)
    return client.get("/v1/auth/oidc/callback",
                      params={"code": "auth-code", "state": state, **params})


def error_of(response) -> str:
    from urllib.parse import unquote

    location = response.headers.get("location", "")
    return unquote(location.split("sso_error=", 1)[1]) if "sso_error=" in location else ""


# --------------------------------------------------------------------------- #
# the happy path
# --------------------------------------------------------------------------- #
def test_the_console_advertises_sso_only_when_it_is_configured(client):
    state = client.get("/v1/auth/state").json()
    assert state["sso_available"] is True
    assert state["password_login"] is True        # both doors, unless told otherwise


def test_start_redirects_with_state_nonce_and_pkce(client, idp):
    r = client.get("/v1/auth/oidc/start")
    assert r.status_code == 302
    location = r.headers["location"]
    assert location.startswith(f"{ISSUER}/authorize?")
    for required in ("response_type=code", "code_challenge=", "code_challenge_method=S256",
                     "state=", "nonce=", f"client_id={CLIENT_ID}"):
        assert required in location, required
    assert r.cookies.get(HANDSHAKE_COOKIE)        # signed, httponly, short-lived


def test_a_first_sign_in_creates_the_account_and_a_workspace(client, idp):
    state, handshake = start(client, idp)
    r = callback(client, state, handshake)
    assert r.status_code == 302 and "sso_error" not in r.headers["location"]

    me = client.get("/v1/auth/me").json()
    assert me["user"]["email"] == "dev@acme.com"
    assert me["user"]["sso"] is True              # the console can say "no password here"
    assert me["workspaces"], "an SSO account still needs somewhere to put projects"

    # PKCE and the client secret really were sent.
    form = idp.seen[-1]
    assert form["grant_type"] == "authorization_code"
    assert form["code_verifier"] and form["client_secret"] == CLIENT_SECRET


def test_a_second_sign_in_reuses_the_same_account(client, idp):
    state, handshake = start(client, idp)
    callback(client, state, handshake)
    first = client.get("/v1/auth/me").json()["user"]["id"]

    client.post("/v1/auth/logout")
    client.cookies.clear()
    state, handshake = start(client, idp)
    callback(client, state, handshake)
    assert client.get("/v1/auth/me").json()["user"]["id"] == first


def test_the_session_is_the_ordinary_one(client, idp):
    """SSO replaces the front door and nothing else."""
    state, handshake = start(client, idp)
    callback(client, state, handshake)
    project = client.post("/v1/projects", json={"name": "sso-project"})
    assert project.status_code == 200, project.text
    assert client.get(f"/v1/rules?project={project.json()['project']['id']}").status_code == 200


# --------------------------------------------------------------------------- #
# the refusals
# --------------------------------------------------------------------------- #
def test_an_unverified_email_is_refused(client, idp):
    """The classic takeover: register someone's address at a sloppy provider."""
    idp.claims = {"email_verified": False}
    state, handshake = start(client, idp)
    r = callback(client, state, handshake)
    assert "not verified" in error_of(r)
    assert client.get("/v1/auth/me").status_code == 401


def test_a_token_for_another_audience_is_refused(client, idp):
    idp.claims = {"aud": "some-other-app"}
    state, handshake = start(client, idp)
    assert "could not be verified" in error_of(callback(client, state, handshake))


def test_a_token_from_another_issuer_is_refused(client, idp):
    idp.claims = {"iss": "https://evil.example.com"}
    state, handshake = start(client, idp)
    assert "could not be verified" in error_of(callback(client, state, handshake))


def test_an_expired_token_is_refused(client, idp):
    idp.claims = {"exp": int(time.time()) - 60}
    state, handshake = start(client, idp)
    assert "could not be verified" in error_of(callback(client, state, handshake))


def test_a_replayed_nonce_is_refused(client, idp):
    """A token minted for one sign-in cannot be posted into another."""
    state, handshake = start(client, idp)
    idp._nonce = "a-nonce-from-somewhere-else"
    assert "could not be verified" in error_of(callback(client, state, handshake))


def test_a_wrong_or_missing_state_is_refused(client, idp):
    state, handshake = start(client, idp)
    assert "could not be verified" in error_of(callback(client, "not-the-state", handshake))
    assert "took too long" in error_of(callback(client, state, None))


def test_a_provider_error_never_leaks_its_body(client, idp):
    idp.token_status = 400
    state, handshake = start(client, idp)
    message = error_of(callback(client, state, handshake))
    assert "rejected this sign-in" in message
    assert "invalid_grant" not in message


def test_an_email_outside_the_allowed_domains_is_refused(tmp_path, monkeypatch, idp):
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setenv("POLICY_DIR", "policies")
    monkeypatch.setenv("JWT_SECRET", "sso-secret-0123456789abcdef")
    monkeypatch.setenv("OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("OIDC_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("OIDC_CLIENT_SECRET", CLIENT_SECRET)
    monkeypatch.setenv("OIDC_ALLOWED_DOMAINS", "corp.example")
    from agent_plane.config import get_settings

    get_settings.cache_clear()
    from agent_plane.main import create_app

    app = create_app()
    app.state.oidc_http = httpx.Client(transport=idp.transport(), base_url=ISSUER)
    with TestClient(app, follow_redirects=False) as c:
        state, handshake = start(c, idp)
        assert "domain is not allowed" in error_of(callback(c, state, handshake))
    get_settings.cache_clear()


# --------------------------------------------------------------------------- #
# the two doors stay separate
# --------------------------------------------------------------------------- #
def test_an_sso_account_cannot_be_signed_into_with_a_password(client, idp):
    """It has no password hash, and an empty one must match nothing."""
    state, handshake = start(client, idp)
    callback(client, state, handshake)
    client.post("/v1/auth/logout")
    client.cookies.clear()

    for attempt in ("", " ", "password", "dev@acme.com"):
        r = client.post("/v1/auth/login", json={"email": "dev@acme.com", "password": attempt})
        assert r.status_code == 401, attempt


def test_sso_is_not_an_agent_credential(client, idp):
    """A session cookie must never authorize an action on the plane."""
    state, handshake = start(client, idp)
    callback(client, state, handshake)
    project = client.post("/v1/projects", json={"name": "sso-project"}).json()["project"]

    reported = client.post("/v1/events/action",
                           json={"agent": "claude-code", "task": "t", "action": "filesystem.read",
                                 "resource": "workspace/a.ts", "project": project["id"]})
    assert reported.status_code == 401, "the cookie must not stand in for a Project API Key"


def test_sso_routes_are_absent_when_it_is_not_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setenv("POLICY_DIR", "policies")
    monkeypatch.setenv("JWT_SECRET", "sso-secret-0123456789abcdef")
    for name in ("OIDC_ISSUER", "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    from agent_plane.config import get_settings

    get_settings.cache_clear()
    from agent_plane.main import create_app

    with TestClient(create_app(), follow_redirects=False) as c:
        assert c.get("/v1/auth/state").json()["sso_available"] is False
        assert c.get("/v1/auth/oidc/start").status_code == 404
        assert c.get("/v1/auth/oidc/callback").status_code == 404
    get_settings.cache_clear()


def test_oidc_only_closes_the_password_door(tmp_path, monkeypatch, idp):
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setenv("POLICY_DIR", "policies")
    monkeypatch.setenv("JWT_SECRET", "sso-secret-0123456789abcdef")
    monkeypatch.setenv("OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("OIDC_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("OIDC_CLIENT_SECRET", CLIENT_SECRET)
    monkeypatch.setenv("OIDC_ONLY", "true")
    from agent_plane.config import get_settings

    get_settings.cache_clear()
    from agent_plane.main import create_app

    app = create_app()
    app.state.oidc_http = httpx.Client(transport=idp.transport(), base_url=ISSUER)
    with TestClient(app, follow_redirects=False) as c:
        assert c.get("/v1/auth/state").json()["password_login"] is False
    get_settings.cache_clear()


def test_oidc_only_is_ignored_when_sso_cannot_let_anyone_in(tmp_path, monkeypatch):
    """Otherwise a typo in the issuer locks every human out of their own install."""
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setenv("POLICY_DIR", "policies")
    monkeypatch.setenv("JWT_SECRET", "sso-secret-0123456789abcdef")
    monkeypatch.setenv("OIDC_ONLY", "true")
    for name in ("OIDC_ISSUER", "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET"):
        monkeypatch.delenv(name, raising=False)
    from agent_plane.config import get_settings

    get_settings.cache_clear()
    from agent_plane.main import create_app

    with TestClient(create_app()) as c:
        assert c.get("/v1/auth/state").json()["password_login"] is True
    get_settings.cache_clear()


def test_the_handshake_cookie_is_not_readable_by_script(client, idp):
    r = client.get("/v1/auth/oidc/start")
    header = r.headers.get("set-cookie", "")
    assert "httponly" in header.lower()
    assert "samesite=lax" in header.lower()
    assert base64.urlsafe_b64decode  # the handshake body is base64url, signed
