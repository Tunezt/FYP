#!/data/data/com.termux/files/usr/bin/sh
# Android (Termux) autostart. Copy to ~/.termux/boot/poernama-bridge.sh and
# `chmod +x` it. Needs the Termux and Termux:Boot apps (from F-Droid, not the
# Play Store build), and battery optimisation switched OFF for both apps.
# The loop restarts the bridge if it ever exits; the wake lock stops Android
# from putting the CPU to sleep while the screen is off.
termux-wake-lock
cd "$HOME/poernama-bridge" || exit 1
while true; do
  python print_bridge.py --config bridge-config.json >> bridge.log 2>&1
  sleep 5
done
