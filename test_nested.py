#!/usr/bin/python3
"""Integration checks inside a private DBus, PipeWire and headless KWin.

Never connects to the user's compositor or Sunshine service.
"""
import json
import multiprocessing
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time


def until(check, message, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.2)
    raise AssertionError(message)


def worker():
    import vdisplay as vd
    root = Path(os.environ["XDG_RUNTIME_DIR"])
    vd.LOG = root / "fake-sunshine.log"
    vd.LOG.touch()
    procs = []
    with (root / "nested.log").open("w") as log:
        try:
            procs.append(subprocess.Popen(["pipewire"], stdout=log, stderr=log))
            until(lambda: (root / "pipewire-0").exists(), "private PipeWire did not start")
            lock_flag = "--lockscreen" if os.environ.get("SUNSHINE_QOL_TEST_LOCKED") else "--no-lockscreen"
            procs.append(subprocess.Popen(["kwin_wayland", "--virtual", "--width", "1920", "--height", "1080",
                                           lock_flag, "--no-global-shortcuts", "--no-kactivities",
                                           "--socket", "sunshine-test"], stdout=log, stderr=log))
            until(lambda: (root / "sunshine-test").exists(), "private KWin did not start")
            until(lambda: len(vd.layout()) == 1, "private KWin baseline missing")
            if lock_flag == "--lockscreen":
                def is_locked():
                    result = subprocess.run(["gdbus", "call", "--session", "--dest", "org.freedesktop.ScreenSaver",
                                             "--object-path", "/ScreenSaver", "--method", "org.freedesktop.ScreenSaver.GetActive"],
                                            capture_output=True, text=True, timeout=3)
                    return result.returncode == 0 and result.stdout.strip() == "(true,)"
                until(is_locked, "private KWin did not start locked")
                print("PASS native KWin --lockscreen starts with locked session", flush=True)
            baseline = vd.layout()
            base_name = baseline[0]["name"]

            def physical_enabled():
                return next(o for o in vd.layout() if o["name"] == base_name)["enabled"]

            def virtual_exists():
                return any(o["name"] == vd.OUTPUT for o in vd.layout())

            def event(text):
                with vd.LOG.open("a") as f:
                    f.write("[test]: Info: " + text + "\n")

            native = os.environ.get("SUNSHINE_QOL_NATIVE")
            cases = [(1280, 720, "60")] if native else [(1280, 720, "60"), (1080, 1920, "90"), (1920, 1080, "59.94")]
            for w, h, fps in cases:
                req = vd.mode_request({"SUNSHINE_CLIENT_WIDTH": str(w), "SUNSHINE_CLIENT_HEIGHT": str(h),
                                       "SUNSHINE_CLIENT_FPS": fps})
                req.update(disable_physical=True, sunshine=vd.identity(os.getpid()))
                vd.atomic(vd.REQUEST, req)
                daemon = multiprocessing.get_context("fork").Process(target=vd.daemon)
                daemon.start()
                try:
                    until(lambda: vd.STATE.exists() and vd.read(vd.STATE).get("ready"), "display helper not ready")
                    assert not physical_enabled(), "physical baseline was not disabled"
                    # No VNC password: owned KRFB must have zero socket listeners.
                    vpid = vd.read(vd.STATE)["vmon"]["pid"]
                    sockets = subprocess.check_output(["ss", "-ltnp"], text=True)
                    assert f"pid={vpid}," not in sockets, "KRFB opened a TCP listener"
                    if native:
                        native_config = root / "native-sunshine.conf"
                        (root / "apps.json").write_text('{"apps": [{"name": "Test Desktop"}]}')
                        native_config.write_text(f"capture = kwin\noutput_name = {vd.OUTPUT}\n"
                                                 f"file_apps = {root / 'apps.json'}\n"
                                                 f"credentials_file = {root / 'test-credentials.json'}\n"
                                                 f"pkey = {root / 'test-key.pem'}\ncert = {root / 'test-cert.pem'}\n"
                                                 f"file_state = {root / 'test-state.json'}\n"
                                                 "port = 48989\nbind_address = 127.0.0.1\nupnp = disabled\n"
                                                 "controller = disabled\nkeyboard = disabled\nmouse = disabled\n"
                                                 "system_tray = disabled\n")
                        native_log = root / "native-sunshine.log"
                        with native_log.open("w") as capture:
                            native_proc = subprocess.Popen([native, str(native_config)], stdout=capture, stderr=capture)
                            procs.append(native_proc)
                            until(lambda: "Found HEVC encoder:" in native_log.read_text(), "native Sunshine KWin/HEVC probe failed", timeout=30)
                            text = native_log.read_text()
                            assert "zkde_screencast_unstable_v1 not found" not in text
                            assert "Screencasting with KWin ScreenCast" in text
                            assert vd.OUTPUT in text
                            print("PASS official native Sunshine: KWin virtual-display capture and HEVC encoder probe", flush=True)
                            native_proc.terminate()
                            native_proc.wait(timeout=10)
                            procs.remove(native_proc)
                    event("CLIENT CONNECTED")
                    time.sleep(0.4)
                    event("CLIENT DISCONNECTED")
                    until(physical_enabled, "physical output was not restored on disconnect")
                    assert virtual_exists(), "local disconnect destroyed resumable virtual display"
                    event("CLIENT CONNECTED")
                    until(lambda: not physical_enabled(), "reconnect did not restore exclusive streaming")
                    if fps == "59.94":
                        os.kill(daemon.pid, signal.SIGKILL)
                        daemon.join(5)
                        vd.recover()
                        print("PASS crash recovery after SIGKILL", flush=True)
                    else:
                        os.kill(daemon.pid, signal.SIGTERM)
                        daemon.join(10)
                        assert daemon.exitcode == 0, f"helper exited {daemon.exitcode}"
                    until(lambda: not virtual_exists(), "virtual display was not removed on app stop")
                    assert physical_enabled(), "physical output was not restored on app stop"
                    now = vd.layout()
                    assert now[0]["pos"] == baseline[0]["pos"]
                    assert now[0]["priority"] == baseline[0]["priority"]
                    assert now[0]["currentModeId"] == baseline[0]["currentModeId"]
                    print(f"PASS {w}x{h}@{fps}: create, exact mode, no VNC listener, disconnect, resume, cleanup", flush=True)
                finally:
                    if daemon.is_alive():
                        daemon.terminate()
                        daemon.join(10)
                    if vd.STATE.exists():
                        vd.recover()
        finally:
            for proc in reversed(procs):
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            print("Private compositor log:", root / "nested.log", flush=True)


if __name__ == "__main__":
    if "--worker" in sys.argv:
        worker()
    else:
        # Keep test evidence in /tmp; never read/write the live display config.
        temp = Path(tempfile.mkdtemp(prefix="sunshine-qol-test-"))
        env = dict(os.environ)
        for name in ("DBUS_SESSION_BUS_ADDRESS", "DISPLAY", "NOTIFY_SOCKET"):
            env.pop(name, None)
        env.update(XDG_RUNTIME_DIR=str(temp), XDG_CONFIG_HOME=str(temp / "config"),
                   XDG_DATA_HOME=str(temp / "data"), XDG_CACHE_HOME=str(temp / "cache"),
                   WAYLAND_DISPLAY="sunshine-test", QT_QPA_PLATFORM="wayland",
                   XDG_SESSION_TYPE="wayland", XDG_CURRENT_DESKTOP="KDE", LIBGL_ALWAYS_SOFTWARE="1",
                   QT_FORCE_STDERR_LOGGING="1", QT_LOGGING_RULES="krfb.*.debug=true;kwin_screencast.debug=true")
        env["DBUS_SYSTEM_BUS_ADDRESS"] = "unix:path=" + str(temp / "no-system-bus")
        result = subprocess.run(["dbus-run-session", "--", sys.executable, __file__, "--worker"], env=env)
        print("Test evidence:", temp)
        sys.exit(result.returncode)
