# Changelog

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
