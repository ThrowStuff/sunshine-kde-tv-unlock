#!/usr/bin/python3
"""KDE virtual display hooks for a standalone Sunshine host.

The log path defaults to the preserved Flatpak data directory. No credentials are read.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from decimal import Decimal

NAME = "sunshine-qol"
OUTPUT = "Virtual-" + NAME
UNIT = "sunshine-qol-display.service"
ROOT = Path(__file__).resolve().parent
RUNTIME = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / NAME
STATE = RUNTIME / "state.json"
REQUEST = RUNTIME / "request.json"
LOG = Path.home() / ".var/app/dev.lizardbyte.app.Sunshine/config/sunshine/sunshine.log"


def run(*args):
    env = dict(os.environ)
    if args[0] == "kscreen-doctor":
        # The shared backend launcher can retain an incomplete output list after
        # disabling a connector. Query KWin directly on each invocation instead.
        env["KSCREEN_BACKEND_INPROCESS"] = "1"
        env.pop("KSCREEN_BACKEND", None)
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT, timeout=12, env=env)


def layout():
    return json.loads(run("kscreen-doctor", "-j"))["outputs"]


def doctor(args):
    if args:
        run("kscreen-doctor", *args)


def atomic(path, obj):
    RUNTIME.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=2) + "\n")
    tmp.chmod(0o600)
    tmp.replace(path)


def read(path):
    return json.loads(path.read_text())


def mode_request(env):
    w = int(env.get("SUNSHINE_CLIENT_WIDTH", "1920"))
    h = int(env.get("SUNSHINE_CLIENT_HEIGHT", "1080"))
    fps = Decimal(env.get("SUNSHINE_CLIENT_FPS", "60"))
    if not (320 <= w <= 8192 and 200 <= h <= 8192 and w * h <= 33554432):
        raise ValueError("Unsupported client dimensions")
    if not fps.is_finite() or not 10 <= fps <= 240:
        raise ValueError("Unsupported client refresh rate")
    return {"width": w, "height": h, "mhz": int(fps * 1000)}


def identity(pid):
    try:
        p = Path(f"/proc/{pid}")
        # Field 22 (start time) prevents acting on a reused PID.
        fields = p.joinpath("stat").read_text().rsplit(")", 1)[1].split()
        if fields[0] == "Z":
            return None
        start = fields[19]
        return {"pid": int(pid), "start": start, "comm": p.joinpath("comm").read_text().strip()}
    except (OSError, IndexError):
        return None


def alive(token):
    return token is not None and identity(token["pid"]) == token


def host_identity():
    found = []
    for pid in run("pgrep", "-x", "sunshine").split():
        token = identity(int(pid))
        if token and Path(f"/proc/{pid}").stat().st_uid == os.getuid():
            found.append(token)
    if len(found) != 1:
        raise RuntimeError("Expected exactly one Sunshine process owned by this user")
    return found[0]


def restore_args(saved, current):
    """Restore only outputs touched by us, retaining initially disabled outputs."""
    by_name = {o["name"]: o for o in current if o["connected"]}
    args = []
    for old in saved:
        name = old["name"]
        if name not in by_name:
            continue
        prefix = f"output.{name}."
        if not old["enabled"]:
            args.append(prefix + "disable")
            continue
        args.append(prefix + "enable")
        # We never change physical modes/scales, but KWin may reposition them.
        args.append(prefix + f"position.{old['pos']['x']},{old['pos']['y']}")
        args.append(prefix + f"priority.{old['priority']}")
    return args


def restore(saved):
    current = layout()
    doctor(restore_args(saved, current))
    enabled = [o for o in layout() if o["enabled"] and o["connected"] and o["name"] != OUTPUT]
    if not enabled:
        raise RuntimeError("No physical output restored; leaving virtual monitor for recovery")


def exclusive(saved):
    current = {o["name"]: o for o in layout()}
    vm = current.get(OUTPUT)
    if not vm or not vm["enabled"]:
        raise RuntimeError("Virtual output unavailable; refusing physical-monitor shutdown")
    args = [f"output.{OUTPUT}.priority.1"]
    args += [f"output.{o['name']}.disable" for o in saved
             if o["enabled"] and o["name"] in current and current[o["name"]]["connected"]]
    doctor(args)


def stop_owned(token):
    if alive(token) and token["comm"] == "krfb-virtualmonitor"[:15]:
        os.kill(token["pid"], signal.SIGTERM)
        for _ in range(30):
            if not alive(token):
                return
            time.sleep(0.1)
        if alive(token):
            raise RuntimeError("Owned virtual monitor did not exit; retained state for recovery")


def recover():
    if not STATE.exists():
        return
    state = read(STATE)
    restore(state["outputs"])
    stop_owned(state.get("vmon"))
    STATE.unlink(missing_ok=True)


def notify(message):
    address = os.environ.get("NOTIFY_SOCKET")
    if address:
        if address.startswith("@"):
            address = "\0" + address[1:]
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(address)
            sock.sendall(message.encode())


def daemon():
    req = read(REQUEST)
    if not alive(req["sunshine"]):
        raise RuntimeError("Sunshine exited before virtual-display preparation")
    if STATE.exists():
        recover()
    before = layout()
    if any(o["name"] == OUTPUT for o in before):
        raise RuntimeError("Unexpected existing virtual output; refusing to claim it")
    saved = [o for o in before if o["name"] != OUTPUT]
    if not any(o["connected"] and o["enabled"] for o in saved):
        raise RuntimeError("Need a connected physical monitor for a recoverable baseline")
    state = {"outputs": saved, "request": req, "vmon": None}
    atomic(STATE, state)
    quitting = False

    def quit_signal(*_):
        nonlocal quitting
        quitting = True

    signal.signal(signal.SIGTERM, quit_signal)
    signal.signal(signal.SIGINT, quit_signal)
    process = None
    try:
        # Without --password KRFB initializes the framebuffer but does not open
        # its IPv4/IPv6 VNC listener (verified in KDE krfb v26.08.1 source).
        # stdout/stderr go to the service journal, never through a prep-hook pipe.
        process = subprocess.Popen(["krfb-virtualmonitor", "--resolution",
                                    f"{req['width']}x{req['height']}", "--name", NAME,
                                    "--scale", "1"])
        for _ in range(60):
            if quitting or process.poll() is not None:
                raise RuntimeError("Virtual monitor terminated during setup")
            vm = next((o for o in layout() if o["name"] == OUTPUT), None)
            if vm:
                state["vmon"] = identity(process.pid)
                atomic(STATE, state)
                break
            time.sleep(0.2)
        else:
            raise RuntimeError("Virtual output never appeared")
        doctor([f"output.{OUTPUT}.addCustomMode.{req['width']}.{req['height']}.{req['mhz']}.full"])
        vm = next(o for o in layout() if o["name"] == OUTPUT)
        modes = [m for m in vm["modes"] if m["size"] == {"width": req["width"], "height": req["height"]}
                 and abs(m["refreshRate"] * 1000 - req["mhz"]) < 100]
        if not modes:
            raise RuntimeError("KWin did not expose the requested resolution/refresh")
        doctor([f"output.{OUTPUT}.enable", f"output.{OUTPUT}.mode.{modes[-1]['id']}",
                f"output.{OUTPUT}.scale.1", f"output.{OUTPUT}.priority.1"])
        actual = next(o for o in layout() if o["name"] == OUTPUT)
        active_mode = next(m for m in actual["modes"] if m["id"] == actual["currentModeId"])
        if active_mode not in modes:
            raise RuntimeError("Requested virtual display mode did not become active")
        # Open log BEFORE releasing the prep hook, so no connection event is lost.
        with LOG.open() as logfile:
            logfile.seek(0, 2)
            if req["disable_physical"]:
                exclusive(saved)
            atomic(STATE, dict(state, ready=True))
            notify("READY=1")
            print("Virtual display ready; retaining it across local disconnect/resume", flush=True)
            connections = 0
            last_connection = time.monotonic()
            restored = not req["disable_physical"]
            monitoring = True
            while not quitting and alive(req["sunshine"]) and process.poll() is None:
                lines = logfile.readlines() if monitoring else []
                for line in lines:
                    if "]: Info: CLIENT CONNECTED" in line:
                        connections += 1
                        last_connection = time.monotonic()
                        if req["disable_physical"] and restored:
                            exclusive(saved)
                            restored = False
                    elif "]: Info: CLIENT DISCONNECTED" in line:
                        connections = max(0, connections - 1)
                        if connections == 0 and not restored:
                            restore(saved)
                            restored = True
                # Failed launch or unseen disconnect: restore after initial
                # grace period, keeping the virtual display available for resume.
                if connections == 0 and not restored and time.monotonic() - last_connection > 30:
                    restore(saved)
                    restored = True
                if monitoring and (LOG.stat().st_ino != os.fstat(logfile.fileno()).st_ino or LOG.stat().st_size < logfile.tell()):
                    # Log rotation makes connection count uncertain. Fail safely:
                    # restore physical outputs, retain virtual until app quits.
                    restore(saved)
                    restored = True
                    monitoring = False
                    print("Log rotated; physical displays restored, virtual retained until app quit", flush=True)
                time.sleep(0.25)
    finally:
        restore(saved)
        if process and process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        STATE.unlink(missing_ok=True)


def start():
    if subprocess.run(["systemctl", "--user", "is-active", "--quiet", UNIT]).returncode == 0:
        raise RuntimeError("Virtual display is already active; refusing a second request")
    settings = read(ROOT / "settings.json")
    requested = dict(os.environ)
    if resolution := settings.get("resolution"):
        requested["SUNSHINE_CLIENT_WIDTH"] = str(resolution["width"])
        requested["SUNSHINE_CLIENT_HEIGHT"] = str(resolution["height"])
    req = mode_request(requested)
    if os.environ.get("SUNSHINE_CLIENT_HDR", "false").lower() == "true":
        raise RuntimeError("This virtual-display profile supports SDR only")
    req["disable_physical"] = settings["disable_physical"]
    req["sunshine"] = host_identity()
    atomic(REQUEST, req)
    run("systemctl", "--user", "start", UNIT)
    if not STATE.exists() or not read(STATE).get("ready"):
        raise RuntimeError("Virtual display service did not report readiness")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "stop", "daemon", "recover", "inspect"])
    args = parser.parse_args()
    if args.action == "inspect":
        print(json.dumps({"outputs": [{k: o[k] for k in ("name", "connected", "enabled", "currentModeId", "pos", "priority")}
                                      for o in layout()], "saved_state": STATE.exists()}, indent=2))
        return
    RUNTIME.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Do not hold this lock while starting/stopping systemd: the daemon and
    # ExecStopPost must be able to acquire it while those commands wait.
    if args.action == "start":
        with (RUNTIME / "request.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            start()
    elif args.action == "stop":
        run("systemctl", "--user", "stop", UNIT)
    else:
        with (RUNTIME / "display.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            {"daemon": daemon, "recover": recover}[args.action]()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"sunshine-qol: {error}", file=sys.stderr)
        sys.exit(1)
