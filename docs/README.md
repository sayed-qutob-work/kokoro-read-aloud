# Engineering docs

These are working documents, not user documentation. If you want to install and
use the tool, the root [README.md](../README.md) is the right place.

They are published because they are the reason the code looks the way it does.
Almost every design decision here was made against a measurement, and several
were made *twice* because the first attempt trusted an estimate instead. The
logs record both.

| File | What it is |
|---|---|
| [AUDIT.md](AUDIT.md) | **The source of truth.** Measured facts, rejected options with the evidence that rejected them, operating procedures, and a dated log of every diagnosis. Read §4 (measured facts) and §6 (rejected options) before proposing anything. |
| [plan.md](plan.md) | The highlighter diagnosis and phased fix plan (2026-07-21). Still the open work item. |
| [RELEASE_PLAN.md](RELEASE_PLAN.md) | The current release plan: Windows beta, caption strip, and the Linux (Fedora) port. |

## Two things to know before reading

**The measurements are tied to specific hardware, and that hardware changed.**
Numbers in AUDIT §4 were taken on the original desktop (i5-12400F). The machine
became an i7-11370H laptop with a GTX 1650 on 2026-08-09, and the throughput,
synthesis-cost and latency rows do **not** transfer — the 2026-08-11 entry in §8
carries the current values. Machine specs are named throughout on purpose: a
throughput number without the machine that produced it is worse than no number,
because it invites exactly the false confidence these documents exist to
prevent.

**Some entries are wrong on purpose.** Superseded conclusions are marked and
kept rather than deleted, along with what replaced them. The GPU section in §6
is the clearest example: it argues against using a GPU, is preserved because
that verdict was correct *for the machine it was written about*, and is topped
with the 2026-08-11 measurement that reversed it on the current one. Read the
dates.
