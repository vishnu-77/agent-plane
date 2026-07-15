"""Issue and manage agent delegation credentials (Ed25519).

Operator tooling for ``identity_mode=delegation``. The issuer holds the private
key; the control plane is configured with the matching public key and verifies
every delegation. Agents never mint their own scope.

Examples
--------
Generate an issuer keypair::

    python -m agent_plane.identity_cli keygen --out-dir keys

Issue a scoped, short-lived delegation for an agent::

    python -m agent_plane.identity_cli issue --key keys/delegation_private.pem \\
        --sub alice --app crm-app --agent sales-assistant \\
        --tools search,draft_email --clearance internal --ttl 3600

Point the control plane at the public key::

    IDENTITY_MODE=delegation
    DELEGATION_PUBLIC_KEY=keys/delegation_public.pem
"""
from __future__ import annotations

import argparse
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt


def _ensure_crypto():
    """Delegation tooling needs the optional ``[delegation]`` extra."""
    try:
        from cryptography.hazmat.primitives import serialization  # noqa: F401
        from cryptography.hazmat.primitives.asymmetric import ed25519  # noqa: F401
    except ImportError:  # pragma: no cover - exercised only without the extra
        sys.exit("This command needs cryptography. Install:  pip install 'agent-plane[delegation]'")
    return serialization, ed25519


def _keygen(args: argparse.Namespace) -> None:
    serialization, ed25519 = _ensure_crypto()
    private_key = ed25519.Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = (
        private_key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    if args.out_dir:
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "delegation_private.pem").write_text(private_pem, encoding="utf-8")
        (out / "delegation_public.pem").write_text(public_pem, encoding="utf-8")
        print(f"wrote {out / 'delegation_private.pem'}")
        print(f"wrote {out / 'delegation_public.pem'}  <- DELEGATION_PUBLIC_KEY")
    else:
        print(private_pem)
        print(public_pem)


def _issue(args: argparse.Namespace) -> None:
    _ensure_crypto()  # jwt.encode(EdDSA) needs cryptography
    private_pem = Path(args.key).read_text(encoding="utf-8")
    now = datetime.now(UTC)
    payload: dict = {
        "sub": args.sub,
        "app": args.app,
        "agent": args.agent,
        "tenant": args.tenant,
        "department": args.department,
        "scope": {
            "tools": [t for t in args.tools.split(",") if t.strip()] if args.tools else [],
            "clearance": args.clearance,
        },
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=args.ttl)).timestamp()),
        "jti": args.jti or uuid.uuid4().hex,
    }
    if args.issuer:
        payload["iss"] = args.issuer
    if args.audience:
        payload["aud"] = args.audience
    print(jwt.encode(payload, private_pem, algorithm="EdDSA"))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="identity_cli")
    sub = parser.add_subparsers(dest="cmd", required=True)

    keygen = sub.add_parser("keygen", help="generate an issuer Ed25519 keypair")
    keygen.add_argument("--out-dir", help="write keys here instead of stdout")
    keygen.set_defaults(func=_keygen)

    issue = sub.add_parser("issue", help="issue a signed agent delegation")
    issue.add_argument("--key", required=True, help="issuer private key (PEM path)")
    issue.add_argument("--sub", required=True, help="user the agent acts for")
    issue.add_argument("--app", help="application identity")
    issue.add_argument("--agent", help="agent identity")
    issue.add_argument("--tenant", default="default")
    issue.add_argument("--department")
    issue.add_argument("--tools", default="", help="comma-separated granted tools")
    issue.add_argument("--clearance", default="internal")
    issue.add_argument("--ttl", type=int, default=3600, help="lifetime in seconds")
    issue.add_argument("--jti", help="explicit id (default: random)")
    issue.add_argument("--issuer")
    issue.add_argument("--audience")
    issue.set_defaults(func=_issue)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
