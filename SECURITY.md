# Security policy

This server hands captured on-screen activity to whatever agent host spawns it. Its safety rests on three properties:

- **Read-only.** It opens the engine's SQLite tiers in read-only mode and never writes to them.
- **Unprivileged.** It requests no macOS permissions and has no capture capability of its own. The engine daemon is the only process that holds the Accessibility / Input Monitoring grants.
- **Local.** It speaks stdio to the process that launched it and makes no network calls.

The agent host is the trust boundary: it decides which model sees the tool results. Anything that weakens the three properties above is a security issue even when intentional and must be discussed in the open first.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting on this repository (**Security** tab → **Report a vulnerability**). Do not open a public issue for anything that could expose captured data. You will get an acknowledgement within a few days. There is no bug bounty.

Issues in the capture daemon or its on-disk formats belong to the engine repository: https://github.com/OpenRhyme/OpenRhyme/security
