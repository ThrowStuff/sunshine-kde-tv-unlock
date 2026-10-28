# Tested: Sunshine virtual display and TV unlock on Bazzite KDE Wayland

Tested 28 October 2026 on Bazzite 44.20261023.0 Kinoite, Plasma/KWin 6.7.5 Wayland, krfb 26.08.1, AMD RX 6750 XT, official standalone Sunshine 2026.914.233613 (commit 63d35f702ee9e362e43263742981836ec0710384), and a TV Artemis-family client with a DS4 controller.

The owner successfully locked the desktop, connected from the TV, and entered the account password using native Plasma Keyboard controlled from the TV. Host inspection afterward confirmed an unlocked session, virtual-output capture at 1920x1080 with 60 fps pacing, Vulkan HEVC, physical HDMI disabled, Opus audio initialization, and PS4 controller initialization. Sunshine had zero service restarts. Audio and game quality were not separately rated.

This is a report and adaptation guide for that tested stack. The companion files are sanitized exports of the working helper and services; their generic installation paths have not been exercised on a second machine. They are not a distribution-supported installer.

## What made it work

1. Use official standalone Sunshine for direct KWin capture. On this machine the same-version Flatpak failed at KWin permissions. Standalone capture worked without setting KWIN_WAYLAND_NO_PERMISSION_CHECKS globally.
2. Create an on-demand compositor output with krfb-virtualmonitor; select its stable name in Sunshine before Sunshine starts.
3. Use kscreen-doctor to validate the requested mode before disabling physical outputs. Set KSCREEN_BACKEND_INPROCESS=1 for each query/change to avoid stale shared-backend enumeration.
4. Restore physical outputs before stopping the virtual-monitor child. KillMode=mixed lets the helper handle SIGTERM first.
5. For access after boot, start the desktop through the login manager and start KWin with its native --lockscreen argument. This is an already-running locked session, not capture of the pre-login greeter.
6. Use Plasma Keyboard in mouse/touch mode for password entry. The client's own software keyboard did not work for this owner.

## Start with a working standalone host

Keep the current configuration, app list, pairing state, certificates and original launch service backed up privately. Do not publish them. Obtain the official build from https://github.com/LizardByte/Sunshine/releases and verify the release asset checksum. The tested deployment extracted the AppImage and launched its usr/bin/sunshine directly; it did not run the AppImage installer or add capabilities. GPU/controller permissions already worked here; that does not establish the requirements of a clean installation.

Place the extracted package at ~/sunshine-qol/native/squashfs-root and these companion files at ~/sunshine-qol. Use an existing, working standalone Sunshine configuration at ~/sunshine-qol/sunshine.conf. Replace any other Sunshine autostart so exactly one host runs, doing the switch while idle. The supplied service conflicts with one common Flatpak service name; other installations may use different names.

This installation deliberately kept native Sunshine using the original Flatpak data directory. Removing the Flatpak application did not remove its data. The helper's LOG constant defaults to ~/.var/app/dev.lizardbyte.app.Sunshine/config/sunshine/sunshine.log. For a different data location, change that constant to match Sunshine's log_path. Preserve existing file_apps, file_state, credentials_file, cert and pkey settings; do not replace pairing files or paste their contents into reports.

Dependencies on this host already included Python 3, krfb-virtualmonitor, kscreen-doctor, systemd user services and KDE Wayland. Plasma Keyboard was also installed. Confirm availability before proceeding. This guide does not require a dummy plug or EDID/boot changes, but this helper requires a connected, enabled physical output at first launch to retain a recoverable baseline.

## Virtual display configuration

In the existing Sunshine configuration set:

```ini
capture = kwin
output_name = Virtual-sunshine-qol
encoder = vulkan
system_tray = disabled
```

Vulkan was verified on the tested AMD card. Choose a supported encoder for other hardware. Disabling the tray avoided a packaged Qt plugin startup crash here.

Set global_prep_cmd to a JSON array containing a do/undo pair. Replace YOUR_HOME with the absolute home path; do not use an unexpanded tilde:

```ini
global_prep_cmd = [{"do": "/usr/bin/python3 YOUR_HOME/sunshine-qol/vdisplay.py start", "undo": "/usr/bin/python3 YOUR_HOME/sunshine-qol/vdisplay.py stop"}]
```

Merge this with any existing preparation commands instead of silently replacing them. This setup expects the existing Desktop application to launch a desktop stream.

Install the companion user units, preserving any same-named units first:

```bash
mkdir -p ~/.config/systemd/user
cp ~/sunshine-qol/sunshine-qol-display.service ~/.config/systemd/user/
cp ~/sunshine-qol/sunshine-qol-host.service ~/.config/systemd/user/
cp ~/sunshine-qol/sunshine-qol-watchdog.service ~/.config/systemd/user/
cp ~/sunshine-qol/sunshine-qol-watchdog.timer ~/.config/systemd/user/
systemctl --user daemon-reload
```

The host unit assumes wayland-0, this extracted-binary location, and this configuration filename. Review these against your desktop before starting it. While idle, stop the old host, then enable/start the new host:

```bash
systemctl --user enable --now sunshine-qol-host.service
```

settings.json contains {"disable_physical": true}. A fresh Desktop launch uses the client's requested width/height/fps through validation. The final live stream was 1920x1080, with KScreen reporting approximately 59.96 Hz and Sunshine pacing at 60 fps. The helper checks that the requested mode became active before disabling physical monitors.

Without --password, the tested krfb version created its framebuffer without opening a VNC TCP listener. Source review and process socket checks confirmed that behavior for this version; recheck it after upgrades.

## Plasma Keyboard: the successful TV password-entry method

Back up ~/.config/kwinrc first. Check that /usr/share/applications/org.kde.plasma.keyboard.desktop exists. In a terminal belonging to the running KDE session:

```bash
kwriteconfig6 --notify --file kwinrc --group Wayland --key InputMethod /usr/share/applications/org.kde.plasma.keyboard.desktop
cp ~/sunshine-qol/sunshine-qol-lock-keyboard.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now sunshine-qol-lock-keyboard.service
gdbus call --session --dest org.kde.KWin --object-path /VirtualKeyboard --method org.freedesktop.DBus.Properties.GetAll org.kde.kwin.VirtualKeyboard
```

On the tested version mode 2 permits mouse/touch interaction and mode 0 is None. The companion lock_keyboard.py service (requires Python PyGObject/Gio) queries ScreenSaver.GetActive at startup, listens for lock changes, and reconciles every five seconds: mode 2 while locked, mode 0 after unlock. Verify available=true and mode=0 on the unlocked desktop; mode=2 is expected while locked. This leaves Plasma Keyboard selected as the input method but disables automatic display after unlock. The live unlocked state was verified as mode=0, visible=false; three simulated transition/failure tests passed. A live lock/unlock cycle with this new automation remains to be observed; the successful TV test used mode 2 set manually. visible=false is normal when the keyboard is not displayed. A sandboxed terminal/agent may need host execution to reach the desktop session bus; a bus-denied error does not show that the keyboard failed.

Lock with Meta+L. From the TV, connect to the existing host's Desktop, use controller mouse to select the password field and open the keyboard icon if needed. Enter the password on the TV, privately. This sequence succeeded for the owner. Successful capture alone is not proof of working password entry.

## Optional automatic login with native startup lock

Automatic login starts the user's session and services before the account password is entered at the lock screen. It does not provide the same boundary as a session that has never logged in. Disk-encryption prompts and KWallet unlocking are separate. Keep a local recovery route.

First verify remote manual-lock/unlock as above. Check the stock compositor command:

```bash
systemctl --user cat plasma-kwin_wayland.service
```

On this tested system the command was /usr/bin/kwin_wayland_wrapper --xwayland. A user drop-in at ~/.config/systemd/user/plasma-kwin_wayland.service.d/60-sunshine-start-locked.conf contains:

```ini
[Service]
ExecStart=
ExecStart=/usr/bin/kwin_wayland_wrapper --xwayland --lockscreen
```

Preserve your distribution's actual executable and existing arguments. The wrapper was checked to forward --lockscreen. Install the lock drop-in before enabling automatic login; daemon-reload does not restart the current compositor. Do not restart KWin in an active desktop to test it. The next graphical login takes the new command. This also affects manual logins and can require another unlock afterward.

This host uses Plasma Login Manager, not SDDM. Its root-owned mode-0644 file /etc/plasmalogin.conf.d/60-sunshine-autologin.conf is:

```ini
[Autologin]
User=YOUR_ACCOUNT_NAME
Session=plasma.desktop
Relogin=false
```

Use your account name. Do not apply that file to an SDDM installation. Reload user units after adding the KWin drop-in. Coordinate a reboot only when ready. After reboot inspect KWin's actual arguments, Sunshine health, and lock state. This host rebooted into KWin carrying --lockscreen, and post-boot streaming was recorded. Direct observation of the historical startup lock state was unavailable; the later manual lock-to-TV-unlock test was confirmed successful.

## Recovery and lifecycle

The host has Restart=always with a ten-second delay. The companion watchdog waits through a 90-second startup grace, checks loopback discovery and web endpoints every approximately 15 seconds, and queues a restart after three consecutive failures for the same host process. It respects an intentional Stop. Six watchdog behavior tests passed in the original setup. A rendering-only freeze with healthy control endpoints is outside its detection.

Prep/undo hooks in this Sunshine version run for application launch/termination. A local Disconnect is not equivalent to Quit Session, and resume does not rerun prep. The helper monitors connection log events: on local disconnect it restores physical outputs while retaining the virtual monitor for resume; on reconnect it switches back. Quit Session invokes undo and removes the output after restoring physical displays. Log rotation restores physical displays and suspends automatic reconnect switching until the app quits. Network-failure detection is best effort.

Isolated compositor tests passed lifecycle, reconnect, crash recovery, portrait and fractional-refresh cases. Real physical restoration was observed between sessions. The final successful TV-unlock stream was left running; its final Quit Session cleanup was not forcibly tested. Resolution matching is on fresh app launch, not a differently sized client resuming an old app session. HDR, multi-client behavior, unplugged-monitor boot and suspend/resume are not validated.

Read-only checks:

```bash
systemctl --user status sunshine-qol-host.service sunshine-qol-watchdog.timer
gdbus call --session --dest org.freedesktop.ScreenSaver --object-path /ScreenSaver --method org.freedesktop.ScreenSaver.GetActive
KSCREEN_BACKEND_INPROCESS=1 kscreen-doctor -o
journalctl --user -u sunshine-qol-host.service -n 100 --no-pager
```

## Rollback

Coordinate while idle: stopping the host interrupts a stream. Stop/disable the new host, stop its watchdog timer, then restore displays:

```bash
systemctl --user disable --now sunshine-qol-lock-keyboard.service
systemctl --user disable --now sunshine-qol-host.service
systemctl --user stop sunshine-qol-watchdog.timer
python3 ~/sunshine-qol/vdisplay.py stop
python3 ~/sunshine-qol/vdisplay.py recover
```

Restore your privately backed-up Sunshine configuration and previous launch mechanism. If you removed a Flatpak application, reinstall/unmask its service as applicable, retaining the original data. Do not delete ~/.var/app/dev.lizardbyte.app.Sunshine if the current host still uses it.

Remove only the auto-login and KWin startup-lock drop-ins created for this setup, after checking that neither contains unrelated changes. The login-manager file needs administrator access. Reload user units; the change takes effect at the next login. Restore the saved keyboard preferences if desired. Restore/remove only the newly installed user units and reload again. This does not require deleting games, pairing identities or account credentials.

## Credits and upstream context

The approach was informed by these sources; their installers were not used:

- https://github.com/jhonsnake/sunshine-kde-virtual-display
- https://github.com/ImStillBlue/sunshine-virtual-display (Sunveil)
- https://docs.bazzite.gg/Advanced/sunshine/
- https://github.com/LizardByte/Sunshine/issues/5716 (Flatpak/KWin capture problem)
- https://github.com/LizardByte/Sunshine/blob/63d35f702ee9e362e43263742981836ec0710384/src/process.cpp
- https://github.com/LizardByte/Sunshine/blob/63d35f702ee9e362e43263742981836ec0710384/src/nvhttp.cpp
- https://github.com/KDE/krfb/blob/v26.08.1/krfb/rfbserver.cpp

The KDE Connect/API bridge idea is deferred. The owner separately reported phone input while locked; pre-login input was not tested and is not claimed by this guide.
