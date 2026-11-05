"""One-time local setup for pinned SSH and GitHub Actions deploy secrets.

Run on your OWN trusted computer, not GitHub Actions. Rotate any exposed
VPS passwords first. Requires: python -m pip install paramiko cryptography
and GitHub CLI authenticated with 'gh auth login'.

Get the VPS ED25519 host key fingerprint from the provider console:
  ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub -E sha256

Then run:
  python ops/bootstrap_deploy_access.py --trusted-host-fingerprint SHA256:...

This never sends the VPS password to GitHub and never prints credentials.
"""
from __future__ import annotations
import argparse
import base64
import getpass
import hashlib
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

DEFAULT_ENV = Path.home() / "Desktop" / "MCX-TRADER-LIVE" / "mcx-trader.env"
DEFAULT_KEY = Path.home() / ".ssh" / "kotak_news_actions_ed25519"
REPOSITORY = "danny8806/Indian-Market-Intelligence-AI"


def fingerprint(key_bytes: bytes) -> str:
    return "SHA256:" + base64.b64encode(hashlib.sha256(key_bytes).digest()).decode().rstrip("=")


def local_password(env_file: Path) -> str:
    if os.environ.get("VPS_PASS"):
        return os.environ["VPS_PASS"]
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("VPS_PASS="):
                return line.partition("=")[2].strip().strip("'\"")
    return getpass.getpass("Authorized (rotated) VPS password, hidden: ")


def key_pair(path: Path) -> tuple[bytes, str]:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding, PrivateFormat, PublicFormat, NoEncryption
    )
    public = Path(str(path) + ".pub")
    if path.exists() != public.exists():
        raise RuntimeError("Incomplete SSH key pair; check files before retrying")
    if path.exists():
        return path.read_bytes(), public.read_text(encoding="ascii").strip()
    private = Ed25519PrivateKey.generate()
    raw = private.private_bytes(Encoding.PEM, PrivateFormat.OpenSSH, NoEncryption())
    pub = private.public_key().public_bytes(Encoding.OpenSSH, PublicFormat.OpenSSH)
    line = pub.decode("ascii") + " github-kotak-news-deploy"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as out:
        out.write(raw)
    os.chmod(path, 0o600)
    with public.open("x", encoding="ascii") as out:
        out.write(line + "\n")
    return raw, line


def gh_secret(name: str, value: bytes, repo: str) -> None:
    result = subprocess.run(
        ["gh", "secret", "set", name, "--repo", repo],
        input=value, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=30, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(f"GitHub secret {name} not configured; check gh auth permissions")


def run(host: str, user: str, trusted: str, repo: str, env_file: Path, key_file: Path):
    if not re.fullmatch(r"SHA256:[A-Za-z0-9+/]{32,44}={0,2}", trusted):
        raise RuntimeError("Provide an ED25519 host fingerprint verified in the VPS provider console")
    if not re.fullmatch(r"[a-zA-Z0-9.-]+", host) or not re.fullmatch(r"[a-z_][a-z0-9_-]*", user):
        raise RuntimeError("Invalid SSH host or user")
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise RuntimeError("Invalid GitHub repository")
    if not shutil.which("gh") or subprocess.run(
        ["gh", "auth", "status"], stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, check=False
    ).returncode:
        raise RuntimeError("Install GitHub CLI and complete gh auth login first")

    import paramiko
    transport = paramiko.Transport((host, 22))
    try:
        transport.start_client(timeout=15)
        server_key = transport.get_remote_server_key()
        if server_key.get_name() != "ssh-ed25519" or fingerprint(server_key.asbytes()) != trusted:
            raise RuntimeError("VPS host key differs from independently verified fingerprint")
    finally:
        transport.close()

    raw, public_line = key_pair(key_file)
    password = local_password(env_file)
    if not password:
        raise RuntimeError("No authorized VPS password available")
    client = paramiko.SSHClient()
    client.get_host_keys().add(host, server_key.get_name(), server_key)
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    try:
        client.connect(hostname=host, username=user, password=password,
                       look_for_keys=False, allow_agent=False, timeout=15)
        quoted = shlex.quote(public_line)
        command = (
            'umask 077; mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh" && '
            'touch "$HOME/.ssh/authorized_keys" && chmod 600 "$HOME/.ssh/authorized_keys" && '
            f'(grep -Fqx -- {quoted} "$HOME/.ssh/authorized_keys" || '
            f'printf "%s\\n" {quoted} >> "$HOME/.ssh/authorized_keys")'
        )
        _, out, err = client.exec_command(command, timeout=20)
        if out.channel.recv_exit_status():
            raise RuntimeError("Could not install new public key on VPS")
    finally:
        client.close()

    # Do not upload a key that cannot actually log in.
    key = paramiko.Ed25519Key.from_private_key_file(str(key_file))
    verifier = paramiko.SSHClient()
    verifier.get_host_keys().add(host, server_key.get_name(), server_key)
    verifier.set_missing_host_key_policy(paramiko.RejectPolicy())
    try:
        verifier.connect(hostname=host, username=user, pkey=key,
                         look_for_keys=False, allow_agent=False, timeout=15)
        _, out, err = verifier.exec_command('printf "SSH_OK\\n"', timeout=10)
        if out.channel.recv_exit_status() or out.read().decode().strip() != "SSH_OK":
            raise RuntimeError("New SSH private key login verification failed")
    finally:
        verifier.close()

    known_hosts = f"{host} {server_key.get_name()} {base64.b64encode(server_key.asbytes()).decode('ascii')}\n"
    gh_secret("VPS_SSH_PRIVATE_KEY", raw, repo)
    gh_secret("VPS_SSH_KNOWN_HOSTS", known_hosts.encode("ascii"), repo)
    print("PASS: Pinned VPS SSH public-key authentication verified")
    print("PASS: GitHub Actions deployment secrets configured without disclosing them")
    print("NEXT: Configure private VPS config/live.env and run the GitHub deploy workflow")


def main():
    p = argparse.ArgumentParser(description="One-time local VPS→GitHub Actions SSH setup")
    p.add_argument("--trusted-host-fingerprint", required=True)
    p.add_argument("--host", default="200.234.44.93")
    p.add_argument("--user", default="root")
    p.add_argument("--repo", default=REPOSITORY)
    p.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    p.add_argument("--key-file", type=Path, default=DEFAULT_KEY)
    args = p.parse_args()
    try:
        run(args.host, args.user, args.trusted_host_fingerprint,
            args.repo, args.env_file, args.key_file)
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
