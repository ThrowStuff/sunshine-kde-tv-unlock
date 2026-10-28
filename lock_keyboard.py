#!/usr/bin/python3
"""Enable Plasma Keyboard for lock-screen mouse input, disable it after unlock."""
from gi.repository import Gio, GLib

SCREENSAVER = "org.freedesktop.ScreenSaver"
KEYBOARD = "org.kde.kwin.VirtualKeyboard"
PROPERTIES = "org.freedesktop.DBus.Properties"


class LockKeyboard:
    def __init__(self, bus):
        self.bus = bus
        self.last_error = None

    def call(self, destination, path, interface, method, parameters=None):
        return self.bus.call_sync(destination, path, interface, method, parameters,
                                  None, Gio.DBusCallFlags.NONE, 3000, None)

    def sync(self, *_args):
        try:
            # Always query current state; queued signals must not restore stale state.
            locked = self.call(SCREENSAVER, "/ScreenSaver", SCREENSAVER,
                               "GetActive").unpack()[0]
            desired = 2 if locked else 0
            current = self.call("org.kde.KWin", "/VirtualKeyboard", PROPERTIES,
                                "Get", GLib.Variant("(ss)", (KEYBOARD, "mode"))).unpack()[0]
            if current != desired:
                self.call("org.kde.KWin", "/VirtualKeyboard", PROPERTIES, "Set",
                          GLib.Variant("(ssv)", (KEYBOARD, "mode", GLib.Variant("i", desired))))
                print(f"Keyboard mode={desired}; locked={locked}", flush=True)
            self.last_error = None
        except GLib.Error as error:
            # Retry at login / after bus-name changes without treating unknown as unlocked.
            message = str(error)
            if message != self.last_error:
                print(f"Waiting for KDE lock/keyboard interfaces: {message}", flush=True)
                self.last_error = message
        return GLib.SOURCE_CONTINUE


def main():
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    policy = LockKeyboard(bus)
    bus.signal_subscribe(SCREENSAVER, SCREENSAVER, "ActiveChanged", "/ScreenSaver",
                         None, Gio.DBusSignalFlags.NONE, policy.sync)
    bus.signal_subscribe("org.freedesktop.DBus", "org.freedesktop.DBus", "NameOwnerChanged",
                         "/org/freedesktop/DBus", "org.kde.KWin", Gio.DBusSignalFlags.NONE, policy.sync)
    # Also reconcile startup ordering and a missed signal or manual mode change.
    GLib.timeout_add_seconds(5, policy.sync)
    policy.sync()
    GLib.MainLoop().run()


if __name__ == "__main__":
    main()
