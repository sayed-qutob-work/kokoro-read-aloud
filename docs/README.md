# Documentation

## Guides

For installing and using Kokoro Read-Aloud. Start with the root
[README.md](../README.md); these pages hold the details it links to.

| Guide | What it covers |
|---|---|
| [install.md](install.md) | Every platform in detail: what setup does, download and disk sizes, where files go, updating, removing, optional extras |
| [omarchy.md](omarchy.md) | The Omarchy bar widget, what each mode costs, and `kokoroctl` |
| [windows.md](windows.md) | In-place highlighting per app, the Chromium extension, known issues |
| [settings.md](settings.md) | The Settings panel, `settings.json`, the 28 voices and their grades, the caption strip |
| [troubleshooting.md](troubleshooting.md) | The logs, common problems, reporting a bug |
| [how-it-works.md](how-it-works.md) | The pieces, what happens on a read, speed and hardware, what gets read, security, pinning |
| [licenses.md](licenses.md) | Credits, third-party licenses, and what redistributing a bundle would require |
| [windows-test-checklist.md](windows-test-checklist.md) | Step-by-step retest of the current version on Windows 11: install, hotkeys, request checks, settings, tray, caption strip, highlighting, autostart, uninstall |
| [security.md](security.md) | Three web-page attacks on the local server, measured in a real browser before and after the 2026-10-18 fix. Reporting a vulnerability: [SECURITY.md](../SECURITY.md) |

## Engineering docs

These are working documents, not user documentation. They are published because they are the reason the code looks the way it does.
Almost every design decision here was made against a measurement, and several
were made *twice* because the first attempt trusted an estimate instead. The
logs record both.

| File | What it is |
|---|---|
| [AUDIT.md](AUDIT.md) | **The source of truth.** Measured facts, rejected options with the evidence that rejected them, operating procedures, and a dated log of every diagnosis. Read §4 (measured facts) and §6 (rejected options) before proposing anything. |
| [plan.md](plan.md) | The highlighter diagnosis and phased fix plan (2026-07-21). Still the open work item. |
| [RELEASE_PLAN.md](RELEASE_PLAN.md) | The current release plan: Windows beta, caption strip, and the Linux (Fedora) port. |

### Two things to know before reading

**The measurements are tied to specific hardware, and that hardware changed.**
Numbers in AUDIT §4 were taken on the original desktop's CPU, under Windows
(i5-12400F). Since then the project has run on an i7-11370H laptop with a
GTX 1650 (Windows, then Fedora, from 2026-08-09) and on an i5-12400F + RTX 4060
Ti desktop running Omarchy (from 2026-10-06), both on the GPU. The throughput,
synthesis-cost and latency rows in §4 do **not** transfer to either. Each
machine's own values are in §8: 2026-08-11 and 2026-08-18 for the laptop,
2026-10-06/07 for the Omarchy desktop. Machine specs are named throughout on
purpose: a throughput number without the machine that produced it is worse
than no number, because it invites exactly the false confidence these
documents exist to prevent.

**Some entries are wrong on purpose.** Superseded conclusions are marked and
kept rather than deleted, along with what replaced them. The GPU section in §6
is the clearest example: it argues against using a GPU, is preserved because
that verdict was correct *for the machine it was written about*, and is topped
with the 2026-08-11 measurement that reversed it on the laptop. Read the
dates.
