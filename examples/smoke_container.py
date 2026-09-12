"""Test a local image as non-root, including audit persistence after restart."""
from __future__ import annotations

import argparse
import os
import secrets
import socket
import subprocess
from pathlib import Path
from uuid import uuid4

import httpx
from smoke_runtime import exercise_runtime, wait_ready


def docker(*args: str, **kwargs) -> str:
    return subprocess.check_output(["docker", *args], text=True, **kwargs).strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--save", type=Path, help="Save the exact tested image as a tar artifact")
    args = parser.parse_args()
    docker("version", "--format", "{{.Server.Version}}", timeout=15)
    name = f"agent-plane-smoke-{uuid4().hex[:12]}"
    volume = f"{name}-audit"
    env = os.environ.copy()
    env.update({"ADMIN_TOKEN": secrets.token_urlsafe(32), "JWT_SECRET": secrets.token_urlsafe(32),
                "AUDIT_SIGNING_KEY": secrets.token_urlsafe(32)})
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    docker("volume", "create", volume, timeout=30)
    try:
        docker("run", "--detach", "--name", name, "-p", f"127.0.0.1:{port}:8000",
               "-e", "ADMIN_TOKEN", "-e", "JWT_SECRET", "-e", "AUDIT_SIGNING_KEY",
               "--mount", f"type=volume,source={volume},target=/data", args.image, env=env, timeout=60)
        assert docker("exec", name, "id", "-u", timeout=10) == "10001"
        evidence = exercise_runtime(url, env["ADMIN_TOKEN"], env["JWT_SECRET"])
        docker("restart", name, timeout=60)
        wait_ready(url)
        with httpx.Client(base_url=url, timeout=5, trust_env=False,
                          headers={"X-Admin-Token": env["ADMIN_TOKEN"]}) as client:
            records = client.get("/v1/audit").json()["events"]
            assert set(evidence["evidence_ids"]) <= {record["decision_id"] for record in records}
            # Audit and the runtime-issued lease both survive the restart (SQL authority store).
            lease = client.get(f"/v1/leases/{evidence['lease_id']}")
            assert lease.status_code == 200, lease.text
            assert lease.json()["id"] == evidence["lease_id"]
        if args.save:
            args.save.parent.mkdir(parents=True, exist_ok=True)
            docker("save", "--output", str(args.save), args.image, timeout=300)
        print("PASS: non-root image, writable SQLite volume, runtime decisions, audit + lease persistence")
    except Exception:
        subprocess.run(["docker", "logs", name], check=False, timeout=15)
        raise
    finally:
        subprocess.run(["docker", "rm", "--force", name], check=False, timeout=30)
        subprocess.run(["docker", "volume", "rm", volume], check=False, timeout=30)


if __name__ == "__main__":
    main()
