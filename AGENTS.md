# Job Search Skill development

Work only in this standalone repository. Use the configured Codex Python and Git Bash where available. Keep dependencies, browser profiles, test data, temporary renderings and private applicant records outside this repository and outside cloud-sync directories. Never copy the user's private profile, application records, credentials, or original resume into source or public fixtures.

The product targets Codex Desktop on Windows. Codex orchestrates the job-search workflow; a local service and web UI share one database. The applicant chooses jobs, reviews information and submits applications. Optional OTP providers do not grant application-submission permission.

Default job discovery and recommendations prioritize formal campus recruitment. Search for or recommend internships only when the applicant explicitly requests them within the current scope. Present unselected candidates in a separate "推荐岗位" area; only jobs the applicant selects for preparation or has actually applied to enter the corresponding application workflow. Preserve the distinction between applicant-reported and officially verified applications. Logging in, editing a company resume or finding a suitable job does not establish a job selection.

Stable resource and API contracts are in references/development-contract.md. Preserve existing user edits, unknown facts, source evidence, scope and original website status. Synthetic tests belong in tests; authorized personal acceptance data belongs in a separate local data directory. Report source tests, browser acceptance, real-site evidence and installed-plugin verification separately.

Do not publish a repository, create public uploads, reconfigure external servers or send messages during local development. Packaging a reviewable release and running local tests is in scope. Do not change existing recruiting plugins, the Career Harness checkout, or existing automations.
