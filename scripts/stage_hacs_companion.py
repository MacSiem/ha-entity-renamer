"""Stage, never install or release, the Entity Renamer HACS integration companion.

The existing repository remains a Dashboard (plugin) repository.  Marketplace
and HACS install only one repository type at a time, so the custom integration
requires an independently versioned repository.  This script stages that
repository's *source layout* in a disposable directory for owner review.

No Home Assistant, HACS, GitHub or network access is performed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile


DOMAIN = "ha_entity_renamer"
REQUIRED = frozenset({
    "__init__.py", "config_flow.py", "const.py", "manifest.json",
    "websocket_api.py",
})
SUPPORTED_SUFFIXES = frozenset({".py", ".json"})
MIN_VERSION = re.compile(r"20\d{2}\.\d{1,2}\.\d+(?:b\d+)?\Z")
DEV_VERSION = re.compile(r"\d+\.\d+\.\d+\.dev\d+\Z")
README = """# Entity Renamer Companion (developer staging)

This is not a release or an installation instruction. It is a development-only
source package for independent owner review. The migration backend remains
incomplete and must not be advertised or installed as migration-ready.

The existing **Dashboard** card/plugin is managed separately. Installing this
integration does not install, replace, register or update the JavaScript card.

The owner must establish an independent integration repository, complete
backend mutation authorization and migration coverage, qualify HACS/Marketplace
fresh-install and upgrade in owned native HA, approve the version/tag and
receive an exact release grant before publishing.
"""


class PackagingError(ValueError):
    """Unsupported input, unsafe destination or incomplete source tree."""


def _load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PackagingError(f"invalid metadata: {path.name}") from error
    if not isinstance(value, dict):
        raise PackagingError(f"metadata not an object: {path.name}")
    return value


def _files(backend: Path) -> list[Path]:
    if not backend.is_dir() or backend.is_symlink():
        raise PackagingError("required backend directory missing or symlinked")
    result = []
    for path in sorted(backend.rglob("*")):
        relative = path.relative_to(backend)
        if "__pycache__" in relative.parts:
            continue
        if path.is_symlink():
            raise PackagingError("symlink in backend source is not permitted")
        if path.is_dir():
            continue
        if not path.is_file() or path.suffix not in SUPPORTED_SUFFIXES:
            raise PackagingError("unsupported backend runtime asset")
        result.append(relative)
    if not REQUIRED.issubset({p.as_posix() for p in result}):
        raise PackagingError("required backend source file missing")
    return result


def _reject_symlink_chain(path: Path) -> None:
    for candidate in (path, *path.parents):
        if candidate.is_symlink():
            raise PackagingError("symlink path or ancestor is not permitted")


def _destination(source: Path, output: Path) -> Path:
    requested = Path(output).expanduser().absolute()
    _reject_symlink_chain(requested)
    out = requested.resolve(strict=False)
    if out.exists() or out.is_symlink():
        raise PackagingError("destination already exists, will not overwrite")
    if source == out or source in out.parents or out in source.parents:
        raise PackagingError("source and destination must not overlap")
    if "custom_components" in out.parts or "www" in out.parts or out.name in ("config", "ha_entity_renamer"):
        raise PackagingError("destination resembles a live Home Assistant directory")
    if "stage" not in out.name.lower():
        raise PackagingError("destination must be explicitly named as staging")
    if any((parent / ".git").exists() for parent in out.parents):
        raise PackagingError("destination belongs to an existing Git repository")
    if not out.parent.is_dir():
        raise PackagingError("destination parent must already exist")
    return out


def build_companion(source: Path, output: Path) -> dict:
    """Create isolated integration repository layout; never overwrite any path.

    Non-development manifest versions are intentionally refused. Assembling a
    development package does not qualify its backend for publication or use.
    hide_default_branch is a HACS display preference, not a release guard.
    """
    requested_source = Path(source).expanduser().absolute()
    _reject_symlink_chain(requested_source)
    src = requested_source.resolve(strict=True)
    for relative in ("hacs.json", "ha-entity-renamer.js", "LICENSE", "custom_components/ha_entity_renamer"):
        _reject_symlink_chain(src / relative)
    out = _destination(src, Path(output))
    plugin = _load_json(src / "hacs.json")
    if plugin.get("filename") != "ha-entity-renamer.js" or plugin.get("content_in_root") is not True:
        raise PackagingError("existing Dashboard plugin metadata mismatch")
    if not (src / "ha-entity-renamer.js").is_file():
        raise PackagingError("required Dashboard JavaScript missing")
    minimum = plugin.get("homeassistant")
    if not isinstance(minimum, str) or not MIN_VERSION.fullmatch(minimum):
        raise PackagingError("Home Assistant minimum is missing or unsupported")
    backend = src / "custom_components" / DOMAIN
    paths = _files(backend)
    if Path("translations/en.json") not in paths:
        raise PackagingError("required complete English translation is missing")
    english = _load_json(backend / "translations" / "en.json")
    config = english.get("config")
    if not isinstance(config, dict):
        raise PackagingError("incomplete English translation for the setup flow")
    abort = config.get("abort", {})
    if (not isinstance(english.get("title"), str) or not english["title"].strip()
            or not isinstance(abort, dict) or not isinstance(abort.get("already_configured"), str)
            or not abort["already_configured"].strip()):
        raise PackagingError("incomplete English translation for the setup flow")
    manifest = _load_json(backend / "manifest.json")
    if manifest.get("domain") != DOMAIN:
        raise PackagingError("manifest domain does not match integration directory")
    if manifest.get("config_flow") is not True or "websocket_api" not in manifest.get("dependencies", []):
        raise PackagingError("required companion setup dependencies missing")
    for field in ("name", "documentation", "issue_tracker", "codeowners"):
        if not manifest.get(field):
            raise PackagingError(f"required integration manifest field: {field}")
    version = manifest.get("version")
    if not isinstance(version, str) or not DEV_VERSION.fullmatch(version):
        raise PackagingError("only development manifest versions may be staged")
    license_path = src / "LICENSE"
    if not license_path.is_file() or license_path.is_symlink():
        raise PackagingError("required license missing or symlinked")

    # Stage to a new sibling, then atomically rename only after byte readback.
    working = Path(tempfile.mkdtemp(prefix=f".{out.name}-tmp-", dir=out.parent))
    try:
        component_out = working / "custom_components" / DOMAIN
        component_out.mkdir(parents=True)
        copied_hashes = {}
        for relative in paths:
            original = backend / relative
            target = component_out / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            raw = original.read_bytes()
            target.write_bytes(raw)
            sha = hashlib.sha256(raw).hexdigest()
            if hashlib.sha256(target.read_bytes()).hexdigest() != sha:
                raise PackagingError("staged backend readback mismatch")
            copied_hashes[relative.as_posix()] = sha
        license_bytes = license_path.read_bytes()
        (working / "LICENSE").write_bytes(license_bytes)
        # No filename or content_in_root, those are the existing plugin's keys.
        (working / "hacs.json").write_text(json.dumps({
            "name": "Entity Renamer Companion",
            "homeassistant": minimum,
            "render_readme": True,
            "hide_default_branch": True,
        }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (working / "README.md").write_text(README, encoding="utf-8")
        # Detect source drift during staging: cannot mix different HEAD states.
        if _files(backend) != paths:
            raise PackagingError("backend inventory changed while staging")
        for relative, expected in copied_hashes.items():
            if hashlib.sha256((backend / relative).read_bytes()).hexdigest() != expected:
                raise PackagingError("backend source changed while staging")
        if license_path.read_bytes() != license_bytes:
            raise PackagingError("license changed while staging")
        if _load_json(src / "hacs.json") != plugin:
            raise PackagingError("plugin metadata changed while staging")
        if _load_json(backend / "manifest.json") != manifest:
            raise PackagingError("integration manifest changed while staging")
        if out.exists():
            raise PackagingError("destination appeared during staging")
        working.rename(out)
    except BaseException:
        shutil.rmtree(working, ignore_errors=True)
        raise
    return {
        "domain": DOMAIN,
        "version": version,
        "homeassistant": minimum,
        "files": len(paths),
        "mode": "source-only-development-stage",
        "mutation_ready": False,
        "released": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(build_companion(args.source, args.output), indent=2))
    except (PackagingError, OSError) as error:
        parser.exit(2, f"REFUSED: {error}\n")


if __name__ == "__main__":
    main()
