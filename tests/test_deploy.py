"""The server option: what can be checked without a server.

The files are read, not run: nothing here starts a container.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import desk

REPO = Path(__file__).resolve().parent.parent
D = REPO / "deploy"


def test_only_the_https_front_publishes_ports():
    compose = (D / "docker-compose.yml").read_text(encoding="utf-8")
    blocks = re.split(r"\n  (?=[a-z0-9-]+:\n)", compose.split("\nservices:\n")[1].split("\nnetworks:\n")[0])
    with_ports = [b.split(":")[0].strip() for b in blocks
                  if re.search(r"^\s+ports:", b, re.M)]
    assert with_ports == ["caddy"]
    assert "--pass-user-headers=true" in compose and "--upstream=http://desk:8765" in compose
    assert "--skip-auth-route=POST=^/webhook/ashby$" in compose
    assert "--authenticated-emails-file" in compose


def test_the_access_section_is_one_the_desk_accepts():
    raw = tomllib.loads((D / "desk-access.toml").read_text(encoding="utf-8"))
    acc = raw["access"]
    assert acc["mode"] == "header" and acc["header"] == "X-Forwarded-Email"
    compose = (D / "docker-compose.yml").read_text(encoding="utf-8")
    assert acc["trusted_proxies"] == ["172.28.0.0/24"] and "subnet: 172.28.0.0/24" in compose
    assert list(acc["emails"].values()) == ["Recruiter"]


def test_secrets_and_data_never_reach_git_or_the_image():
    ignored = (REPO / ".gitignore").read_text(encoding="utf-8")
    docker = (REPO / ".dockerignore").read_text(encoding="utf-8")
    for p in ("deploy/.env", "deploy/emails.txt", "deploy/data/", "deploy/backups/"):
        assert p in ignored and p in docker
    for p in ("runs/", "local/", ".git/"):
        assert p in docker
