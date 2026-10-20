#!/usr/bin/env python3
"""trueoffset-server-setup: set up a Raspberry Pi as a GNSS-disciplined NTP server.

Read this file before you run it. With no options it changes nothing: it looks the
machine over and prints what it found and what it would change. Changing the machine
(--apply) isn't built yet.

Python 3, standard library only. MIT license; see LICENSE.
"""

import argparse
import glob
import os
import re
import shutil
import subprocess
import sys

VERSION = "0.1.0"

# Debian releases this script supports, by codename.
SUPPORTED_RELEASES = {"bookworm": "Debian 12", "trixie": "Debian 13"}

# Boards that have been tested, by the start of /proc/device-tree/model.
TESTED_BOARDS = (
    "Raspberry Pi 3 Model B Plus",
    "Raspberry Pi 4 Model B",
    "Raspberry Pi Compute Module 4",
    "Raspberry Pi Compute Module 5",
)

# Clock chips by the name their kernel driver gives under /sys/bus/i2c/devices/*/name.
RTC_CHIPS = {"rv3028": "Micro Crystal RV-3028", "ds3231": "Maxim DS3231", "ds1307": "DS1307 family (DS3231 driver)"}

# Services a headless time server doesn't need. Each finding is shown only where the
# service is running or enabled, and nothing here is applied until it has a source.
UNNEEDED_SERVICES = {
    "triggerhappy.service": "listens for hotkeys from a keyboard",
    "ModemManager.service": "manages mobile-broadband modems",
    "bluetooth.service": "runs Bluetooth",
    "hciuart.service": "attaches the Bluetooth chip to a serial port the receiver may need",
    "avahi-daemon.service": "announces the machine on the local network (mDNS)",
    "dphys-swapfile.service": "keeps a swap file on the card, which adds writes",
    "wpa_supplicant.service": "runs Wi-Fi",
    "cups.service": "runs printing",
}


def run(args, timeout=20, any_status=False):
    """Runs a read-only command and returns its output, or None if it can't run or fails."""
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout if out.returncode == 0 or any_status else None


def read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


class Machine:
    """What the script finds, read-only."""

    def __init__(self):
        self.release = self._release()
        self.model = (read("/proc/device-tree/model") or "").rstrip("\0").strip()
        self.boot_dir = next((d for d in ("/boot/firmware", "/boot") if os.path.exists(d + "/config.txt")), None)
        self.config_txt = read(self.boot_dir + "/config.txt") if self.boot_dir else None
        self.cmdline = (read(self.boot_dir + "/cmdline.txt") or "").split() if self.boot_dir else []
        self.i2c = self._i2c()
        self.rtcs = self._rtcs()
        self.pps_devices = sorted(glob.glob("/dev/pps*"))
        self.pps_pin = self._pps_pin()
        self.serial = os.path.realpath("/dev/serial0") if os.path.exists("/dev/serial0") else None
        self.daemons = {name: self._daemon(name) for name in ("ntpd", "chronyd", "gpsd")}
        self.packages = self._packages(("ntpsec", "chrony", "gpsd", "gpsd-clients", "pps-tools", "certbot"))
        self.default_target = (run(["systemctl", "get-default"]) or "").strip()
        self.desktop = self._desktop()
        self.services = self._services(UNNEEDED_SERVICES)
        self.journal_storage = self._journal_storage()

    @staticmethod
    def _release():
        fields = {}
        for line in (read("/etc/os-release") or "").splitlines():
            key, _, value = line.partition("=")
            fields[key] = value.strip('"')
        return fields

    def config_lines(self):
        """config.txt's settings, without comments, in the [all] section and in this board's own."""
        return [line.split("#")[0].strip() for line in (self.config_txt or "").splitlines()
                if line.split("#")[0].strip()]

    @staticmethod
    def _i2c():
        found = {}
        for path in glob.glob("/sys/bus/i2c/devices/*-*/name"):
            address = path.split("/")[-2]
            if re.fullmatch(r"\d+-[0-9a-f]{4}", address):  # a chip, not a bus such as i2c-1
                found[address] = (read(path) or "").strip()
        return found

    @staticmethod
    def _rtcs():
        clocks = []
        for path in sorted(glob.glob("/sys/class/rtc/rtc*")):
            def attr(name):
                value = read(f"{path}/{name}")
                return value.strip() if value is not None else None
            clocks.append({"device": os.path.basename(path), "name": attr("name"), "hctosys": attr("hctosys"),
                           "battery_voltage": attr("battery_voltage")})
        return clocks

    def _pps_pin(self):
        for line in self.config_lines():
            m = re.match(r"dtoverlay=pps-gpio(?:,.*?)?(?:,gpiopin=(\d+))?(?:,|$)", line)
            if m:
                return int(m.group(1)) if m.group(1) else 18  # the overlay's default pin
        return None

    @staticmethod
    def _daemon(name):
        """The running copy's program file, and the package that owns it (None if built from source)."""
        for pid in filter(str.isdigit, os.listdir("/proc")):
            if (read(f"/proc/{pid}/comm") or "").strip() == name:
                try:
                    exe = os.path.realpath(f"/proc/{pid}/exe")
                except OSError:
                    exe = None
                if not exe or not os.path.exists(exe):
                    exe = shutil.which(name) or (f"/usr/local/sbin/{name}" if os.path.exists(f"/usr/local/sbin/{name}") else None)
                owner = run(["dpkg", "-S", exe]) if exe else None
                return {"running": True, "exe": exe, "package": owner.split(":")[0] if owner else None}
        return {"running": False, "exe": None, "package": None}

    @staticmethod
    def _packages(names):
        out = run(["dpkg-query", "-W", "-f", "${Package} ${Status} ${Version}\n", *names], any_status=True) or ""
        installed = {}
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[3] == "installed":
                installed[parts[0]] = parts[4]
        return installed

    def _desktop(self):
        return (self.default_target == "graphical.target"
                or run(["systemctl", "is-enabled", "display-manager.service"]) is not None)

    @staticmethod
    def _services(names):
        found = {}
        for name in names:
            active = (run(["systemctl", "is-active", name]) or "").strip() == "active"
            enabled = (run(["systemctl", "is-enabled", name]) or "").strip() == "enabled"
            if active or enabled:
                found[name] = "running" if active else "enabled, not running"
        return found

    @staticmethod
    def _journal_storage():
        storage = "auto"
        for path in ["/etc/systemd/journald.conf", *sorted(glob.glob("/etc/systemd/journald.conf.d/*.conf"))]:
            for line in (read(path) or "").splitlines():
                m = re.match(r"\s*Storage\s*=\s*(\S+)", line)
                if m:
                    storage = m.group(1)
        return storage


class Plan:
    def __init__(self):
        self.stops = []     # reasons the script won't change anything here
        self.changes = []   # (what, why)
        self.untested = []  # found, not tested; changed only with the reader's go-ahead
        self.notes = []

    def stop(self, text):
        self.stops.append(text)

    def change(self, what, why):
        self.changes.append((what, why))


def make_plan(m, daemon):
    plan = Plan()

    codename = m.release.get("VERSION_CODENAME", "")
    if codename not in SUPPORTED_RELEASES:
        plan.stop(f"This is {m.release.get('PRETTY_NAME', 'an unknown system')}. The script supports "
                  "Raspberry Pi OS on Debian 12 (Bookworm) and 13 (Trixie) only.")
    if not m.model.startswith("Raspberry Pi"):
        plan.stop(f"This isn't a Raspberry Pi ({m.model or 'no board model found'}).")
    elif not m.model.startswith(TESTED_BOARDS):
        plan.untested.append(f"Board: {m.model}. Not tested; the script would treat it like the nearest tested board.")
    if not m.boot_dir:
        plan.stop("No config.txt was found in /boot/firmware or /boot.")

    # A time daemon or gpsd built from source: report it and stop, never convert it.
    for name, d in m.daemons.items():
        if d["running"] and not d["package"]:
            plan.stop(f"{name} here runs from {d['exe'] or 'an unknown file'}, which no package owns, so it was "
                      "built from source. This script manages only the distribution's packages, and it won't "
                      "replace a working build.")
    other = "chronyd" if daemon == "ntpsec" else "ntpd"
    if m.daemons[other]["running"] and m.daemons[other]["package"]:
        plan.stop(f"{other} is running from the {m.daemons[other]['package']} package, and you chose {daemon}. "
                  "Switching daemons isn't built yet.")

    # Notes hold even where the script stops: they're what a checkup would mention.
    for clock in m.rtcs:
        if clock["name"] == "rpi-rtc" and clock["battery_voltage"] in ("0", "0.000"):
            plan.notes.append(f"{clock['device']} (the board's own clock) reads 0 V: no backup cell, or a flat one.")
    for name, state in m.services.items():
        plan.notes.append(f"{name} is {state}: it {UNNEEDED_SERVICES[name]}. Proposed for turning off once its guidance is sourced.")
    if m.journal_storage != "volatile":
        plan.notes.append(f"The journal's Storage is {m.journal_storage}, so it writes to the card. Proposed: volatile, once sourced.")

    if plan.stops:
        return plan

    wanted = ["gpsd", "gpsd-clients", "pps-tools", daemon]
    missing = [p for p in wanted if p not in m.packages]
    if missing:
        plan.change(f"Install {', '.join(missing)} with apt.", "The distribution's own packages; nothing is built.")

    if m.pps_pin is None:
        plan.change("Add dtoverlay=pps-gpio,gpiopin=18 to config.txt.", "The receiver's pulse reaches the kernel through this overlay. Pin 18 is Uputronics' pin; John Miller's TimeHat uses pin 4. Check yours.")
    if "enable_uart=1" not in m.config_lines():
        plan.change("Add enable_uart=1 to config.txt.", "The receiver's serial line needs the UART.")
    if any(a.startswith(("console=serial0", "console=ttyAMA0", "console=ttyS0")) for a in m.cmdline):
        plan.change("Remove the serial console from cmdline.txt.", "The receiver needs that serial port.")

    for address, name in m.i2c.items():
        if name in RTC_CHIPS and not any(c["name"] and address in c["name"] for c in m.rtcs):
            plan.untested.append(f"A {RTC_CHIPS[name]} at {address} has no clock device. The line we'd add: dtoverlay=i2c-rtc,{name}")

    if m.desktop:
        plan.change("Boot to the console: systemctl set-default multi-user.target (undo: set-default graphical.target).",
                    "A desktop session uses cycles and wakes the processor while nobody is looking at it. The packages stay installed.")

    return plan


def report(m, plan, daemon):
    print(f"trueoffset-server-setup {VERSION}, dry run: nothing has been changed.\n")
    print("Found")
    print(f"  System:   {m.release.get('PRETTY_NAME', 'unknown')}")
    print(f"  Board:    {m.model or 'unknown'}")
    print(f"  Boot:     {m.boot_dir or 'not found'}; cmdline {'has nohz=off' if 'nohz=off' in m.cmdline else 'no nohz setting'}")
    print(f"  Pulse:    {', '.join(m.pps_devices) or 'no /dev/pps device'}" + (f"; overlay on pin {m.pps_pin}" if m.pps_pin is not None else ""))
    print(f"  Serial:   /dev/serial0 -> {m.serial}" if m.serial else "  Serial:   no /dev/serial0")
    for address, name in sorted(m.i2c.items()):
        print(f"  I2C:      {address} {name}")
    for c in m.rtcs:
        battery = f", battery {c['battery_voltage']}" if c["battery_voltage"] is not None else ", battery not reported"
        print(f"  Clock:    {c['device']} {c['name']}, {'sets' if c['hctosys'] == '1' else 'does not set'} the time at start{battery}")
    if not m.rtcs:
        print("  Clock:    none")
    for name, d in m.daemons.items():
        if d["running"]:
            print(f"  Running:  {name} from {d['exe']}, " + (f"package {d['package']}" if d["package"] else "no package (built from source)"))
    for name, version in sorted(m.packages.items()):
        print(f"  Package:  {name} {version}")
    print(f"  Boots to: {m.default_target or 'unknown'}" + (" (a desktop)" if m.desktop else ""))
    print(f"  Journal:  Storage={m.journal_storage}")
    print(f"  Daemon:   {daemon} (chosen with --daemon)\n")

    if plan.stops:
        print("Stopped. The script won't change this machine:")
        for s in plan.stops:
            print(f"  - {s}")
    elif plan.changes:
        print("It would change:")
        for what, why in plan.changes:
            print(f"  - {what}\n    Why: {why}")
    else:
        print("Nothing to change.")
    if plan.untested:
        print("\nFound, not tested (changed only with your go-ahead):")
        for u in plan.untested:
            print(f"  - {u}")
    if plan.notes:
        print("\nNotes (not changed):")
        for n in plan.notes:
            print(f"  - {n}")


def main():
    parser = argparse.ArgumentParser(description="Set up a Raspberry Pi as a GNSS-disciplined NTP server. "
                                     "With no options, it changes nothing and prints what it would do.")
    parser.add_argument("--daemon", choices=("ntpsec", "chrony"), default="ntpsec", help="the time daemon to set up (default ntpsec)")
    parser.add_argument("--apply", action="store_true", help="make the changes (not built yet)")
    parser.add_argument("--version", action="version", version=VERSION)
    args = parser.parse_args()

    if args.apply:
        sys.exit("--apply isn't built yet. Run without it to see what the script would change.")

    machine = Machine()
    report(machine, make_plan(machine, args.daemon), args.daemon)


if __name__ == "__main__":
    main()
