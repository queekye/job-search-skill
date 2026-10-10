# Company resource contract 1

A company pack is a versioned public resource, installed by the core plugin. Personal records, application identifiers, attachments, browser storage, raw DOM, credentials and private machine paths never belong here. The local database is the only applicant record authority.

## Fixed resources

`manifest.json`, `guide.md` and `fields.json` are required. `adapter.mjs` and supporting `.mjs` modules implement only declared capabilities. `references/` holds conditional procedures; `fixtures/` holds synthetic tests. No binaries, install hooks, npm dependencies, Python scripts or executable shell files are accepted. The manifest lists the SHA-256 of every resource except itself. Its canonical JSON digest identifies a complete version. IDs use lowercase letters, digits and hyphens; versions use `major.minor.patch` with an optional prerelease suffix.

The manifest's `origin` is the company's HTTPS origin, not an authority to contact it. `capabilities` is a mapping whose entries contain `status` (`executable`, `documentation`, `unavailable`) and `detail`. `verification` distinguishes source history, local synthetic checks and current live validation. Bundled source migration is not proof that today's website still matches. “Executable” means code exists for the described bounded operation, not automatic end-to-end application support.

`fields.json` has `{schema_version:1,fields:[...]}`. Each entry maps a local record field to a site label, method and supported scope. It is a mapping for the operator; it is not a general form language and does not execute selectors. The adapter owns the actual checked control logic. List unused mappings as unsupported instead of guessing code. Unknown user facts remain unknown.

## Local operations

- `resource.list` lists bundled and selected installed versions offline.
- `resource.install {id,version?}` installs only the bundled version, after member/hash validation. Installation never runs a script.
- `resource.read {id,path?}` returns an approved text resource, at most 16,000 characters. The default is `guide.md`. Installed path is returned only for an accepted installed version. Long code is reviewed through the local file viewer.
- `resource.export {id}` returns a deterministic ZIP of that selected pack only. It never reads profile, application, OTP or document storage. The public catalog download route always supplies `source:"bundled"`; this exports trusted repository content even when the user has installed a different local version. User-reviewed imports still need a fresh privacy review before sharing.
- `resource.import {filename,content_base64}` validates a ZIP into private review storage and returns its complete text members and a review candidate to the local UI. Code is not truncated in this review response. ZIP traversal, absolute paths, symlinks, Windows reparse metadata, duplicate/case-colliding paths, unexpected types, oversized resources, encrypted ZIPs, suspicious expansion ratios and hash mismatches are rejected. Limits: 2 MiB ZIP, 5 MiB expanded, 1 MiB/file, 160 files.
- `resource.approve {candidate_id}` is available to `actor=user` only, after the local UI displays the review. It revalidates the archived bytes, marks the version `user_reviewed`, and enables it. Community verification claims remain self-reported. Automated integrity checks do not constitute code or privacy review.
- `resource.rollback {id,version}` selects an already accepted version using its stored receipt; unaccepted or modified directories cannot be activated.
- `resource.refresh` reads a configured public HTTPS `index.json` without installing code; `resource.download` pins id/version/archive SHA-256 and sends the validated ZIP into the existing user review flow. The static [public catalog](public-resources.md) can use GitHub Pages or Cloudflare Pages; no private data or credentials are uploaded. Company packages, review candidates and version locks remain machine-local and do not enter personal WebDAV sync. A configured URL does not prove a site has been published or a package accepted.

Installed versions are immutable under `packages/<id>/<version>`. Publishing different content under an existing version is rejected. Operations pin the selected version for their lifetime. No code is executed by Python or by package installation. Codex may import an adapter only from the accepted version path returned by the local service, after checking trust and reading its guide. An executable community pack can access the browser privileges of its caller: hashes protect integrity, not safety. Do not auto-approve or execute a new community script on the model's own authority.

Package mutations use a process-visible exclusive lock and atomic lock-file replacement. A crashed mutation may leave `.packages-mutation.lock`; inspect its recorded PID and stop the former process before manually removing that file. Do not delete a live lock. Source changes or local user customizations become a new pack version rather than editing an installed version in place.

## Browser integration

Read the active CUA documentation first. Reuse the user's authorized existing tab and supported methods; the adapter does not launch browsers, read cookies or call private recruiting APIs. Import `scripts/browser-session.mjs` from the installed core plugin and `adapter.mjs` from the pinned pack. Create the named adapter exported in its guide, then call `createRecruitingSession({tab,adapter,authorization})`.

The common session exposes `inspect`, `act`, `readCapability` (`applications`/`resume` only), and `fillProfileField` where supported. The separate `scripts/profile-fill.mjs` exposes `fillResumeOnce` for a bounded general-profile page plan. It accepts facts only in caller memory, checks authorization, exact site route and current form, refuses conflicting existing values, fills supported text controls, reads them back and returns `saved:false`. A browser-protected value is marked `redacted_nonempty` after input or `masked_existing_review_required` on a later run; neither proves an exact match. It never saves or submits. Each plan lives in a versioned company adapter. Authorization uses `plan.consentKey` for sensitive fields when companies share an origin (for example, separate Moka tenants); otherwise it uses `plan.origin`. Origins and action permissions come from the user's actual task. `specificJobId` is the chosen official job identifier; do not invent one merely to unlock a helper.

A company resource only claims one-click filling for observed fields with executable code and a synthetic fixture. `fields.json` alone is documentation. Treat every unsupported section of a form as a separate gap and do not imply full resume completion from a partial plan. Save and refresh verification remain separate browser steps; personal values never enter exported packs.

Observe current structure before using migrated selectors. A `needs_discovery` or failed readback stops the affected operation; preserve prior application state. Login/OTP is optional and site-specific, never inferred from availability of a message source. A click or filled field is not a save; a save is not submission. The user performs final application submission.

## Contribution workflow

After a real task, extract only reusable official routes, field meanings, checked control logic and minimal synthetic evidence. Reuse this pack layout, increase its version, update hashes, run fixtures and export. Review every included file before sharing. A contribution may be documentation-only or cover one page. Never attach a private transcript or a “redacted” full personal page as a fixture.

The initial catalog supports browsing and downloading published resources. A contribution link may point to a structured GitHub issue; the contributor reviews and submits it themselves. Core implementation changes continue through PR review. The project does not create public posts, publish packs or deploy a website during ordinary form work.

## Migration evidence (2026-09-14)

Four packs adapt locally maintained source plugins: ByteDance 0.2.0, Tencent 0.1.0, Alibaba 0.1.0, Meituan 0.1.0, plus recruiting-browser session 0.2.0. Only script source, public rules and synthetic fixtures are migrated. The former independent discovery-file writer is omitted; updated shared resources now use versioned packs. Test imports are rebound to this repository, without depending on any previous plugin installation. Source historical observation dates are retained as history, not current live verification. The new pack format itself has local validation tests; current account and installed-plugin acceptance are separate work.

The repository now also includes a vivo current-page reader. Meituan, vivo and Xiaohongshu carry versioned [site-extension plans](site-extension.md) under their own `references/` resources. These describe observed surfaces and missing capabilities, not executable authorization. The release builder validates their schema, company identity and origin against the exact packaged snapshot. Synthetic fixtures and private live acceptance remain separate; installing a company resource does not install the native Codex plugin.

OPPO, Pinduoduo, DJI and Xiaohongshu are documentation starter packs. They have no executable filling or progress-reading claim. Generic lessons such as re-reading saved dropdowns or distinguishing parsed attachments from saved forms are guidance only, until a specific current surface is contributed and checked.
