import unittest
from unittest.mock import Mock
from gi.repository import GLib
from lock_keyboard import LockKeyboard


class KeyboardPolicyTests(unittest.TestCase):
    def test_lock_then_unlock(self):
        bus = Mock()
        policy = LockKeyboard(bus)
        for locked, current, expected in [(True, 0, 2), (False, 2, 0)]:
            bus.reset_mock()
            bus.call_sync.side_effect = [GLib.Variant("(b)", (locked,)),
                                        GLib.Variant("(v)", (GLib.Variant("i", current),)), None]
            policy.sync()
            self.assertEqual(bus.call_sync.call_count, 3)
            self.assertEqual(bus.call_sync.call_args.args[4].unpack()[2], expected)

    def test_unchanged_does_not_write(self):
        bus = Mock()
        bus.call_sync.side_effect = [GLib.Variant("(b)", (False,)),
                                    GLib.Variant("(v)", (GLib.Variant("i", 0),))]
        LockKeyboard(bus).sync()
        self.assertEqual(bus.call_sync.call_count, 2)

    def test_unavailable_lock_state_never_enables_keyboard(self):
        bus = Mock()
        bus.call_sync.side_effect = GLib.Error("unavailable")
        LockKeyboard(bus).sync()
        self.assertEqual(bus.call_sync.call_count, 1)


if __name__ == "__main__":
    unittest.main()
