"""Install release wheels into a fresh venv, outside the checkout, and test them."""
from __future__ import annotations

import argparse
import os
import secrets
import socket
import subprocess
import tempfile
import venv
from pathlib import Path


def run(command: list[str], **kwargs) -> None:
    subprocess.run(command, check=True, **kwargs)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", type=Path, required=True)
    args = parser.parse_args()
    wheels = sorted(args.dist_dir.resolve().rglob("*.whl"))
    if len(wheels) != 2 or sum(p.name.startswith("agent_plane_sdk-") for p in wheels) != 1:
        parser.error("Expected exactly one server wheel and one SDK wheel")
    root = Path(__file__).resolve().parents[1]
    smoke_script = root / "examples/smoke_runtime.py"
    with tempfile.TemporaryDirectory(prefix="agent-plane-distribution-") as temporary:
        work = Path(temporary)
        environment = work / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env.update({
            "ENVIRONMENT": "development", "IDENTITY_MODE": "jwt_claims",
            "STORAGE_BACKEND": "local", "SQLITE_PATH": str(work / "audit.db"),
            "ADMIN_TOKEN": secrets.token_urlsafe(32), "JWT_SECRET": secrets.token_urlsafe(32),
            "AUDIT_SIGNING_KEY": secrets.token_urlsafe(32), "RATE_LIMIT_PER_MINUTE": "10000",
            "POLICY_DIR": str(work / "absent-policies"),
        })
        for key in ("LEASES_FILE", "MODELS_FILE", "TOOLS_FILE", "KNOWLEDGE_FILE", "PRICING_FILE"):
            env.pop(key, None)
        run([str(python), "-m", "pip", "install", "--disable-pip-version-check", *map(str, wheels)], cwd=work, env=env)
        run([str(python), "-m", "pip", "check"], cwd=work, env=env)
        run([str(python), "-c", "import agent_plane, agentplane, sys; from pathlib import Path; "
             "assert all(Path(m.__file__).is_relative_to(Path(sys.prefix)) for m in (agent_plane, agentplane))"], cwd=work, env=env)
        run([str(python), "-m", "agent_plane.cli", "version"], cwd=work, env=env)
        run([str(python), "-m", "agent_plane.cli", "init", "--dir", str(work / "scaffold")], cwd=work, env=env)
        assert (work / "scaffold/config/leases.yaml").is_file()
        assert (work / "scaffold/policies/pii-redaction-required.yaml").is_file()
        with socket.socket() as available:
            available.bind(("127.0.0.1", 0))
            port = available.getsockname()[1]
        with (work / "server.log").open("w+", encoding="utf-8") as log:
            server = subprocess.Popen(
                [str(python), "-m", "agent_plane.cli", "serve", "--host", "127.0.0.1", "--port", str(port)],
                cwd=work, env=env, stdout=log, stderr=subprocess.STDOUT,
            )
            try:
                run([str(python), str(smoke_script), "--url", f"http://127.0.0.1:{port}"], cwd=work, env=env, timeout=90)
            except Exception:
                log.flush()
                print((work / "server.log").read_text(encoding="utf-8"))
                raise
            finally:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()
        print("PASS: both wheels install and run without checkout imports or external databases")


if __name__ == "__main__":
    main()
