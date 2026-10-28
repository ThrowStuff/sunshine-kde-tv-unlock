#!/usr/bin/env bash
set -euo pipefail
kwriteconfig6 --notify --file kwinrc --group Wayland --key InputMethod /usr/share/applications/org.kde.plasma.keyboard.desktop
systemctl --user enable --now sunshine-qol-lock-keyboard.service
gdbus call --session --dest org.kde.KWin --object-path /VirtualKeyboard --method org.freedesktop.DBus.Properties.GetAll org.kde.kwin.VirtualKeyboard
