# Security Policy

## Supported versions

Only the latest released version receives security fixes.

## Reporting a vulnerability

Report suspected vulnerabilities privately through the repository's GitHub Security Advisory feature. Do not open a public issue for an unpatched vulnerability.

Include the affected version, platform, reproduction steps, impact, and any proposed mitigation. Reports are evaluated based on reproducibility, severity, and practical exploitability.

## Security boundaries

commands-wrapper intentionally executes shell commands defined by the user. Command files must be treated as executable code and should only be accepted from trusted sources. The project does not sandbox configured commands.


Custom update and installer sources are disabled by default. Only use the source override environment variables with infrastructure you control, prefer HTTPS, and provide the documented SHA-256 value whenever a custom archive is used. Local command promotion is also opt-in because project command files have the same trust level as executable code.
