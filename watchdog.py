#!/usr/bin/python3
"""Restart the managed Sunshine host after repeated control-endpoint failures.

Never starts an intentionally stopped host and never reads pairing credentials.
"""
import fcntl
import http.client
import json
import os
from pathlib import Path
import ssl
import subprocess
import time
import xml.etree.ElementTree as ET

HOST = "sunshine-qol-host.service"
GRACE_SECONDS = 90
FAILURE_LIMIT = 3
RUNTIME = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "sunshine-qol-watchdog"


def host_state():
    result = subprocess.run(["systemctl", "--user", "show", HOST,
                             "-p", "ActiveState", "-p", "SubState", "-p", "MainPID",
                             "-p", "ActiveEnterTimestampMonotonic"], check=True,
                            capture_output=True, text=True, timeout=5)
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)


def identity(state):
    return state.get("MainPID"), state.get("ActiveEnterTimestampMonotonic")


def running(state):
    return state.get("ActiveState") == "active" and state.get("SubState") == "running" and state.get("MainPID") not in (None, "0")


def healthy():
    connection = http.client.HTTPConnection("127.0.0.1", 47989, timeout=4)
    try:
        connection.request("GET", "/serverinfo")
        response = connection.getresponse()
        if response.status != 200:
            return False, f"discovery HTTP {response.status}"
        root = ET.fromstring(response.read(65536))
        if root.findtext("state") not in ("SUNSHINE_SERVER_FREE", "SUNSHINE_SERVER_BUSY"):
            return False, "invalid discovery response"
    except (OSError, http.client.HTTPException, ET.ParseError) as error:
        return False, f"discovery: {type(error).__name__}"
    finally:
        connection.close()
    # This probe sends no credentials to the self-signed, loopback-only URL.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    connection = http.client.HTTPSConnection("127.0.0.1", 47990, timeout=4, context=context)
    try:
        connection.request("GET", "/")
        response = connection.getresponse()
        if response.status not in (200, 301, 302, 303, 401):
            return False, f"web UI HTTP {response.status}"
    except (OSError, http.client.HTTPException) as error:
        return False, f"web UI: {type(error).__name__}"
    finally:
        connection.close()
    return True, "discovery and web UI responsive"


def save(path, state):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state) + "\n")
    temp.chmod(0o600)
    temp.replace(path)


def check(state_path, now=None):
    before = host_state()
    now = time.monotonic() if now is None else now
    current = {"identity": list(identity(before)), "failures": 0}
    # Respect manual Stop and allow a newly started service to settle.
    if not running(before):
        save(state_path, current)
        return "host not running; no action"
    age = now - int(before.get("ActiveEnterTimestampMonotonic", "0")) / 1_000_000
    if age < GRACE_SECONDS:
        save(state_path, current)
        return "startup grace period; no action"
    try:
        previous = json.loads(state_path.read_text())
    except (OSError, ValueError):
        previous = {}
    ok, reason = healthy()
    after = host_state()
    if not running(after) or identity(after) != identity(before):
        save(state_path, {"identity": list(identity(after)), "failures": 0})
        return "host state changed during probe; no action"
    if ok:
        save(state_path, current)
        return reason
    if previous.get("identity") == current["identity"]:
        current["failures"] = int(previous.get("failures", 0))
    current["failures"] += 1
    if current["failures"] >= FAILURE_LIMIT:
        # Queue rather than wait: restarting the host also restarts its timer.
        subprocess.run(["systemctl", "--user", "--no-block", "restart", HOST],
                       check=True, capture_output=True, text=True, timeout=5)
        save(state_path, dict(current, failures=0))
        return f"RESTART QUEUED after {FAILURE_LIMIT} failures: {reason}"
    save(state_path, current)
    return f"health failure {current['failures']}/{FAILURE_LIMIT}: {reason}"


def main():
    RUNTIME.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (RUNTIME / "lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        print(check(RUNTIME / "state.json"), flush=True)


if __name__ == "__main__":
    main()
