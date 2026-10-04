# Changelog

- Translate the first-run introduction, its dismiss action and all queue removal accessible names per card in Polish and English, including ordinary language changes.

- Refresh open-card translations after ordinary Home Assistant language changes while preserving unqueued device/prefix drafts, focus and selection. Same-language state updates keep the live form. Losing administrator access closes pending confirmation; unresolved roles cannot confirm or send registry changes. Locale updates do not reload registries or apply drafts.

## 4.2.11 (2026-09-29)

- Correct first-run wording to describe the available controls and manual reference review.

- Keep long old/new entity IDs wrapped inside narrow queue rows and keep each Remove button visible.

- Place whole-queue controls before the proposed changes so large queues can be reviewed or cancelled without hundreds of keyboard stops.
- Report successful entity-registry updates without requesting an unnecessary Core restart; direct users to review affected references instead.

- Add a read-only Automatic IDs view using HA's `config/entity_registry/get_automatic_entity_ids`, grouped by device with per-entity, per-device and all-at-once queueing. Null and target collisions are kept separate.
- Revalidate automatic proposals and source identity immediately before apply; stale proposals fail without writes and failed items remain queued.
- Bound related-entity impact requests to batches of 20 and avoid silently treating failed related lookups as no impact.
- Clarify impact coverage for unreadable dashboards/YAML references.

## 4.2.10 (2026-08-28)

- Isolation: Bento CSS is component-local and cannot be captured from `window.HAToolsBentoCSS` by load order.
- Isolation: persistence is now card-local, removing `window._haToolsPersistence` load-order coupling while retaining the legacy `ha-tools-entity-renamer-history` key.
- Security: remove the suite-wide DOM/shadow-root injector; intro and support UI now render only inside this card.
- Security: normalize non-string values before both local and inherited HTML escaping.
- Fix: reload persisted rename history synchronously and tolerate malformed persisted impact arrays.
- Test: extend runtime coverage with foreign-card isolation, no-document-observer, persisted dismiss and hostile arrays.

## 4.2.9 (2026-08-20)

- Security: escape Home Assistant names, entity IDs, impact results, and persisted rename-log values before inserting them into card HTML or data attributes.
- Test: add a dependency-free regression check covering every HTML sink identified during HACS review.

## 4.2.8 (2026-07-18)

- Fix: "Apply Changes" no longer blanks the card. The rename confirmation dialog was referenced by render() but never implemented, so it threw a TypeError and wiped the card. The dialog now lists the queued entity and device renames with Rename / Cancel.

## 4.2.7 (2026-07-18)

- Fix (UI): the small accent dot before section titles no longer detaches from the title text (it was pushed to the opposite edge by the header's flex space-between); it is now pinned next to the title.

## 4.2.6 (2026-07-17)

- Fix (UI): responsive tab bar — tabs stretch to fill the card width and wrap on narrow layouts instead of being pinned to content width and clipped (shared HA Tools tab styling).

## 4.2.6 (2026-07-17)

- Fix (UI): responsive tab bar — tabs stretch to fill the card width and wrap on narrow layouts instead of being pinned to content width and clipped (shared HA Tools tab styling).

## [4.2.4] - 2026-06-15

- Theme: dark/light now follows the active Home Assistant theme (luminance of --card-background-color) instead of OS prefers-color-scheme.
- Fix: restore missing _renderApplyResult() that blanked the card.


## [4.2.3] - 2026-06-15

- Theme: dark/light now follows the active Home Assistant theme (luminance of --card-background-color) instead of OS prefers-color-scheme.
- Fix: restore missing _renderApplyResult() that blanked the card.

## [4.2.2] - 2026-06-13

### Fixed
- **Card no longer renders blank.** render() called an undefined _renderApplyResult(), throwing "is not a function" and blanking the entire panel (regression since v4.2.0). Added a no-op stub; rename feedback is shown via the status message as before.

# Changelog — Entity Renamer

## [4.1.3] - 2026-05-12

### Fixed
- Removed Google Fonts CDN @import (1 occurrence(s)); now uses system font stack with Inter as the preferred locally-installed face.
- Normalized bare `font-family: "Inter", sans-serif` declarations to a complete cross-platform system stack.
- Privacy section in README: claim now matches behaviour (no CDN dependencies).

All notable changes to **Entity Renamer** are documented here.

## [4.0.0] - 2026-05-10

### Major
- **Split from `MacSiem/ha-tools` monorepo** into a dedicated standalone HACS plugin.
- Bundled Bento Design System CSS inline — no shared dependency required.
- Inlined `_haToolsEsc` XSS sanitizer.
- Persistence keys migrated to per-tool namespace `ha-entity-renamer-…` (clean break — old data under `ha-tools-…` is **not** migrated automatically).
- Donation/support footer added to the panel.
- Cross-tool discovery banner removed; each tool stands on its own.

### Compatibility

- Home Assistant ≥ 2024.1.0
