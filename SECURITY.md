# Security policy

## Reporting a vulnerability

Please report security problems privately, not in a public issue:

1. Open the repository's **Security** tab.
2. Choose **Report a vulnerability**, or go straight to
   [the private report form](https://github.com/sayed-qutob-work/kokoro-read-aloud/security/advisories/new).

Only you and the repository's maintainers can see the report. The discussion
and any fix happen there, before anything is made public.

Please include:
- what an attacker can do, and what they need first (a web page you visit,
  a program running on your machine, a network position);
- the steps to reproduce it, with the platform and version (`kokoroctl
  status` on Linux, or the commit);
- any logs or request headers you captured.

When a fix is released, the advisory is published. You are credited if you
want to be.

## Supported versions

The latest release and the `main` branch.

## What counts

Kokoro Read-Aloud is a local tool. Its server listens on 127.0.0.1 and reads
aloud whatever text it is given.

**In scope,** for example:
- **Anything that lets a web page, or another machine,** make the server
  speak, change its settings, or read what it is reading. See
  [docs/security.md](docs/security.md) for the three attacks of this kind
  that were found and closed.
- **Anything that makes setup install something other than the pinned,
  hash-checked files.**
- **Selected text that reaches a shell or a file it shouldn't,** through the
  hotkey scripts.

**By design, and not a vulnerability:**
- **Any program running as your user can use the server.** It has no
  password, like most local services.
- **The Omarchy plugin runs unsandboxed in the shell,** like every Omarchy
  plugin. Omarchy warns about this when you add one.
- **`read_aloud.log` keeps a short excerpt of each selection,** on your
  machine, for diagnosing wrong reads.
