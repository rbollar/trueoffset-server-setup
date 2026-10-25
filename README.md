# trueoffset-server-setup

A script that sets up a Raspberry Pi as a GNSS-disciplined NTP server. Download it, read it, then run it. Afterward, the [TrueOffset](https://trueoffset.app) Mac app's checkup can look the server over.

**Not ready yet.** Version 0.1.0 only looks: run it with no options and it prints what it found and what it would change, and changes nothing. `--apply` isn't built.

```
python3 setup.py                  # dry run, NTPsec
python3 setup.py --daemon chrony  # dry run, chrony
```

If it finds NTPsec, chrony or gpsd built from source by something other than this script, as the 2018 HOWTO's steps did, it reports that and stops. A later version will replace such a build only if you ask, and back it up first.

## What it will do

- **Supported:** Raspberry Pi OS on Debian 13 (Trixie) and 12 (Bookworm). Older releases get a plain refusal.
- **Boards and receivers:** supported means tested. That's the Pi 4B, Pi 3B+, CM4 and CM5, with u-blox M8, M8T and F9T receivers on Uputronics boards, and on a custom TimeHat with an LEA-M8T timing receiver. That board grew out of John Miller's [TimeHat](https://millerjs.org/?action=page&url=timehat) (a DS3231 clock on I²C, pulse on GPIO 4), whose standard version carries a MAX-M8Q and hasn't been tested. Neither is the Raspberry Pi 5 TimeHAT that Jeff Geerling has written about.
  - It probes for clock chips, the board, the receiver and the pulse pin.
  - Anything it finds that hasn't been tested is reported, with the line it would add, and changed only with your go-ahead.
- **Your choice of time daemon:** NTPsec or chrony, with gpsd.
- **Upstream builds, recommended:** the distribution's packages lag behind the projects' own releases, and some lack security fixes the projects have already released (CVEs). So the script recommends building each program from its latest tagged release. For each one, the dry run lists how far the package lags, its open CVEs (from Debian's security tracker) and what it's missing, and you choose. The distribution's packages stay a choice. Unattended, it needs `--upstream` or `--distro`.
  - Sources are checked against their signature or checksum before building, and a mismatch is refused.
  - It keeps a list of what it built, so it can upgrade those builds later, and it removes the tools needed only for building.
- **NTS (optional):** a Let's Encrypt certificate through certbot's DNS check (Cloudflare first), or a certificate you supply. Renewal reloads the daemon.
- **Safe to re-run:** it changes only what's out of line, and a dry run prints every change first.
- **Records versions:** it writes its own version, and the version of every package and build, on the server.
- **Public-server defaults:** rate limits, no management queries, and the NTP Pool's operator guidance cited. It links to joining the Pool but never registers you.
- **A desktop image:** it offers to boot to the console instead, explains why, and leaves the packages installed. Raspberry Pi OS Lite is recommended.
- **A timing receiver:** it explains the day-long survey of the antenna's position and asks before starting it.

It's one Python 3 file, standard library only.

## Why these choices

Each choice comes from something measured on our own servers, a cited source, or plain reasoning, and we say which. The full account, with the numbers and a read-only check for each, is on [Tuning a time server](https://trueoffset.app/tuning/#why).

- **Raspberry Pi OS Lite, booting to the console.** A desktop uses cycles and wakes the processor while nobody is looking. That's reasoning; we haven't timed the difference.
- **Upstream builds recommended.** Distribution packages lag behind security fixes. NTPsec 1.2.6 (2026-10-20) fixed two remote crashes of an NTS server, CVE-2026-102486 and CVE-2026-102483. Builds are checked against their signature or checksum. A build is yours to keep up to date, so the packages stay a choice.
- **NTPsec or chrony.** On one Pi 3B+, switching between them changed the served time's daily spread by a few microseconds. Its USB Ethernet cost more than that. Pick the one you know.
- **The pulse on a GPIO pin, and gpsd started at boot.** The time in the receiver's sentences arrived about 166 ms late, so the pulse sets the second. Debian's gpsd starts only when a client opens its socket, and the daemons' shared-memory drivers never do. So `gpsd.service` is enabled and runs with `-n`, reading the receiver without waiting for a client.
- **The receiver's serial port for the receiver only.** The serial console comes off it. With `enable_uart=1` the port was a kernel console anyway, and the receiver's bytes reached the kernel as SysRq commands. At 9,600 baud a line was 83–88% full and the sentences were 286 ms late; at 115,200 it was about 5%.
- **A full-day survey for a timing receiver.** A 9.6-hour single-band survey read 3.45 m low; at 24 hours it was within 0.08 m. Each meter is about 3.3 ns in the pulse.
- **Public-server defaults.** Rate limits and no management queries, following the NTP Pool's operator guidance. On our own network, the default limits refused a client polling every second, so we lifted them for the LAN. What the script does about that isn't decided.
- **Services flagged but not yet turned off** (ModemManager, Bluetooth, swap, and others): each waits for a source. ModemManager has evidence already: it probes serial ports with AT commands, and it ran on all four of our Pis until we masked it.
- **The journal.** Undecided. Keeping it in memory saves the card, but after a hang it left us nothing to read.

## Credit

Inspired by Eric S. Raymond's [Stratum-1 Microserver HOWTO](https://www.ntpsec.org/white-papers/stratum-1-microserver-howto/) for NTPsec (2018). This script is written fresh, and none of the HOWTO's text or scripts is copied. Where the HOWTO built gpsd and NTPsec from git for Raspbian Stretch, this will build tagged, verified releases, with the distribution's packages as the other choice, and adds chrony and the Compute Modules.

## License

MIT. See [LICENSE](LICENSE).
