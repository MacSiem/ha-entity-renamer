# Changelog

## 4.6.1 (2026-10-29)

- Remove change-plan import and its registry-preview panel. Keep the existing device/entity browsing, editable names/IDs, queue, impact review, confirmation and history workflows.
- Show unavailable Template, Powercalc, automation, script and integration dependency updates as disabled, unchecked Coming soon options in the rename confirmation.
- Preserve the existing rename transaction and authorization behavior; no migration backend is included.

## 4.6.0 (2026-10-29)

- Import a JSON change plan, explicitly choose entity/device rows and preview the current Home Assistant registries without applying or queuing changes. No companion integration is required.
- Show current/proposed IDs and names, missing sources, occupied targets and changed device associations. Reject malformed or ambiguous input instead of inferring an already-applied rename.
- Clear imported plans after account, role or connection changes and discard results for an older file or selection. Keep input in memory only.
- Mark automatic dependency migration (including Template and Powercalc) as Coming soon. No companion integration or new migration/recovery API is shipped with this frontend release. Existing registry queue/history/export behavior remains available.

## 4.5.0 (2026-10-22)

- Keep confirmation button labels whole at narrow widths (GitHub #9), use theme-aware primary contrast and neutral removal actions.
- Keep tabs and filters in one horizontally scrollable row with keyboard navigation; use one expand/collapse control and avoid repeated New badges within new devices.
- Wrap entity IDs only at domain and underscore boundaries; increase small text and constrain wide content.
- Place friendly names above IDs on narrow cards and keep statistic values separate from their labels, including the New filter.
- Add account-scoped, collapsible help and visual configuration for default/visible tabs, help and support.
- Undo clearing a draft queue for eight seconds without overwriting newer proposals.
- Suggest area prefixes in the local queue, respecting entity area overrides, reading current assignments when requested and preserving existing proposals. Registry writes still require administrator review and confirmation.

## 4.4.1 (2026-10-17)

- Preserve list position, expanded groups and logical keyboard focus when queuing or removing Automatic ID proposals and expanding Devices. Filtered rows that disappear no longer pull the viewport toward their group header.
- Show queued entities with a readable, theme-aware text status and one Remove action. Use consistent device headers, entity counts and separators in Devices and Automatic IDs.
- Display history dates in the browser/system time zone by default, respecting explicit Home Assistant local/server, date language and 12/24-hour preferences. Refresh open history when those preferences change and retain raw timestamps in Time details.

## 4.4.0 (2026-10-16)

- Use one natural sort in the Home Assistant language for Devices, Automatic IDs and grouped Queue views.
- Search and filter Devices and Automatic IDs by all, to queue, queued, conflicts and new entries; queue-all actions include only visible matching proposals and show the count.
- Mark registry entries created within seven days; unknown creation times remain unmarked. Sort by name or newest and expand/collapse groups.
- Show queued status and per-device queue counts immediately. Keep the queue accessible from other tabs.
- Group proposals by device and label Automatic IDs, Manual or Prefix sources. Undo a removed proposal for eight seconds, preserving its exact contents and position; clearing, confirmation and authority changes invalidate undo.
- Manually replacing an automatic proposal removes its automatic validation flag. Home Assistant writes still require the existing review and confirmation.

## 4.3.1 (2026-10-16)

- Sort Automatic IDs device groups alphabetically by their displayed name.
- Keep Automatic IDs visible and device groups expanded after queuing an entity, device or all eligible changes.
- Mark queued entities and fully queued devices, prevent duplicate additions and restore available actions after removing proposals from the queue.

## 4.3.0 (2026-10-13)

- Offer native Save As where supported; confirm a save only after reading back identical file bytes. Keep download initiation unconfirmed in other browsers.
- Recover initial registry reads interrupted by HA card mounting, delayed account resolution or reconnecting. Reload permitted browsing after a recognized account or role changes without resuming old writes or consent.
- Make custom titles visible, preserve input/caret through editor configuration echoes and follow the HA editor language.
- Export completed rename history as JSON, CSV or TXT with an exact preview, filename choice and optional private entity-ID mapping. Acknowledgements without independent registry readback remain unconfirmed.
- Keep private rename history separate for each administrator on this browser origin. Preserve older history and offer an explicit local review with unknown account origin.
- Cancel confirmation and remaining writes after account, role, connection or browser availability changes; late replies cannot restore private results in another context. Preserve uncertain sent attempts in their original account history.
- Retain permitted household registry browsing and existing manual, automatic-ID and bulk-prefix tools.
- Distinguish unavailable and partially readable dashboards from known zero reference hits, including exported reports.
- Improve narrow layouts, accessible navigation, 44px controls, keyboard focus and editor drafts across ordinary updates.

## 4.2.12 (2026-10-11)

- Cancel pending rename confirmation with Escape without changing entities or discarding proposals. Focus starts on Cancel, stays on the confirmation actions with Tab, and returns to Apply Changes after cancellation.

## 4.2.11 (2026-10-09)

- Keep friendly names, unchanged-ID labels and blue badges readable in both Home Assistant light and dark themes.

- Count both device and entity proposals in the apply action, translate friendly-only queue labels, and give long target IDs their own row above the friendly name.
- Edit individual IDs and friendly names in an inline, labelled form with keyboard submit/cancel; preserve drafts across language changes and close them when the session changes. Registry writes still require confirmation.

- Translate the first-run introduction, its dismiss action and all queue removal accessible names per card in Polish and English, including ordinary language changes.

- Refresh open-card translations after ordinary Home Assistant language changes while preserving unqueued device/prefix drafts, focus and selection. Same-language state updates keep the live form. Losing administrator access closes pending confirmation; unresolved roles cannot confirm or send registry changes. Locale updates do not reload registries or apply drafts.

- Report affected references only for successful ID changes, including scenes; a rejected write does not claim references were changed.
- Send an entity ID and friendly name in one registry update; preserve failed items for safe retry. Freeze confirmed entity/device proposals and prevent duplicate apply operations.
- Cancel remaining writes after account, role or connection changes; discard late preview, registry and impact responses from the old session.
- Recheck stable source identity after impact analysis and immediately before each automatic write. Cancelled confirmations cannot authorize later changes; Clear removes both entity and device proposals.
- Require Home Assistant 2025.6 or newer, the first Core release with the automatic ID API.
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
