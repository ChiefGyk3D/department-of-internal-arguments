# Security Policy

## Supported Versions

This repository ships agent role and pack text, the scripts that generate and install it, and its own CI callers. Only the latest release (the tip of `main`) is supported.

## Reporting a Vulnerability

Please report security issues **privately**, not in a public issue.

- Preferred: open a
  [GitHub Security Advisory](https://github.com/ChiefGyk3D/department-of-internal-arguments/security/advisories/new)
  for this repository. This notifies the maintainer directly and keeps the
  report private until a fix is available.
- If you cannot use GitHub Security Advisories, contact the maintainer through
  the profile at [github.com/ChiefGyk3D](https://github.com/ChiefGyk3D).

Please include:

- The affected role, pack, policy, script or workflow (file and, if applicable, line)
- A description of the issue and its potential impact
- Steps to reproduce, or a minimal example if practical

## What to Expect

This is a small, community-maintained project. There is no formal SLA, but
reports are triaged as soon as practical and a fix or mitigation is
prioritized for anything that could affect a calling repository's secrets,
supply chain, or CI environment, or that makes an installed persona less safe than documented. Target timelines: an
acknowledgement within 14 days, and public disclosure once a fix is released or
120 days after the report, whichever comes first, coordinated with the reporter.
Credit is given in the fix's changelog entry
or commit message unless you ask not to be named.

## Scope

In scope: `scripts/` (the installer, exporter, generator and validator), the generated adapters, and `.github/workflows/`. Out of scope: how a particular model behaves when given these prompts. Prompts guide behavior and are not access control; see `policies/security-boundaries.md`.
