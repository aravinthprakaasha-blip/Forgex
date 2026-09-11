from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    print("Drift Watch ERROR: PyYAML is required. Install it with: pip install pyyaml")
    sys.exit(1)


TOOL_NAME = "Drift Watch"
STATE_VERSION = 1

PROJECT_ROOT = Path(__file__).resolve().parent
STATE_FILE = PROJECT_ROOT / ".drift_state.json"

CODE_EXTENSIONS = {".py", ".js", ".jsx", ".ts", ".tsx"}

ENV_FILE_PATTERN = re.compile(
    r"^\.env(?:\.(development|dev|staging|production|prod))?$",
    re.IGNORECASE,
)

PYTHON_PATTERNS = [
    re.compile(
        r"""os\.environ\s*\[\s*["']([A-Za-z_][A-Za-z0-9_]*)["']\s*\]"""
    ),
    re.compile(
        r"""os\.environ\.get\s*\(\s*["']([A-Za-z_][A-Za-z0-9_]*)["']"""
    ),
    re.compile(
        r"""os\.getenv\s*\(\s*["']([A-Za-z_][A-Za-z0-9_]*)["']"""
    ),
]

JS_PATTERNS = [
    re.compile(
        r"""process\.env\.([A-Za-z_][A-Za-z0-9_]*)"""
    ),
    re.compile(
        r"""process\.env\s*\[\s*["']([A-Za-z_][A-Za-z0-9_]*)["']\s*\]"""
    ),
]


def infer_string_type(value: str) -> str:
    text = value.strip()

    if text == "":
        return "string"

    lowered = text.lower()

    if lowered in {"true", "false"}:
        return "boolean"

    if re.fullmatch(r"[+-]?\d+", text):
        return "numeric"

    if re.fullmatch(r"[+-]?(?:\d+\.\d*|\.\d+)", text):
        return "numeric"

    if re.match(r"^https?://", text, re.IGNORECASE):
        return "url"

    return "string"


def infer_yaml_type(value: Any) -> str:
    if value is None:
        return "string"

    if isinstance(value, bool):
        return "boolean"

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return "numeric"

    if isinstance(value, str):
        return infer_string_type(value)

    return "string"


def strip_inline_env_comment(value: str) -> str:
    quote = None
    escaped = False

    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue

        if char == "\\":
            escaped = True
            continue

        if char in {"'", '"'}:
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            continue

        if char == "#" and quote is None:
            if index == 0 or value[index - 1].isspace():
                return value[:index].rstrip()

    return value.strip()


def parse_env_value(raw_value: str) -> str:
    value = strip_inline_env_comment(raw_value).strip()

    if len(value) >= 2:
        if value[0] == '"' and value[-1] == '"':
            return value[1:-1]

        if value[0] == "'" and value[-1] == "'":
            return value[1:-1]

    return value


def _has_closing_quote(value: str) -> bool:
    if not value.startswith('"'):
        return True

    escaped = False

    for index in range(1, len(value)):
        char = value[index]

        if escaped:
            escaped = False
            continue

        if char == "\\":
            escaped = True
            continue

        if char == '"':
            return True

    return False


def parse_env_file(path: Path) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"Unable to decode {path.name} as UTF-8"
        ) from exc
    except OSError as exc:
        raise ValueError(
            f"Unable to read {path}: {exc}"
        ) from exc

    index = 0

    while index < len(lines):
        original_line = lines[index]
        line_number = index + 1
        stripped = original_line.strip()

        if not stripped or stripped.startswith("#"):
            index += 1
            continue

        if stripped.startswith("export "):
            stripped = stripped[7:].lstrip()

        if "=" not in stripped:
            raise ValueError(
                f"Malformed .env syntax at {path.name}:{line_number}"
            )

        key, raw_value = stripped.split("=", 1)
        key = key.strip()

        if not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*",
            key,
        ):
            raise ValueError(
                f"Malformed environment key at {path.name}:{line_number}"
            )

        value_text = raw_value.strip()

        if value_text.startswith('"') and not _has_closing_quote(value_text):
            multiline_parts = [value_text]
            index += 1
            closed = False

            while index < len(lines):
                multiline_parts.append(lines[index])

                if _has_closing_quote(
                    "\n".join(multiline_parts)
                ):
                    closed = True
                    break

                index += 1

            if not closed:
                raise ValueError(
                    f"Malformed multiline value at {path.name}:{line_number}"
                )

            value_text = "\n".join(multiline_parts)

        parsed_value = parse_env_value(value_text)

        results[key] = {
            "type": infer_string_type(parsed_value),
            "file": path.name,
            "line": line_number,
        }

        index += 1

    return results


def environment_from_filename(path: Path) -> str:
    name = path.name.lower()

    if name == ".env":
        return "development"

    if name in {".env.development", ".env.dev"}:
        return "development"

    if name == ".env.staging":
        return "staging"

    if name in {".env.production", ".env.prod"}:
        return "production"

    if "staging" in name:
        return "staging"

    if "production" in name or name.startswith("prod"):
        return "production"

    return "development"


def looks_like_compose_or_manifest(path: Path) -> bool:
    if path.suffix.lower() not in {".yml", ".yaml"}:
        return False

    name = path.name.lower()

    keywords = (
        "compose",
        "docker",
        "deployment",
        "deploy",
        "manifest",
        "config",
        "service",
        "k8s",
        "kubernetes",
    )

    return any(keyword in name for keyword in keywords)


def yaml_line_map(path: Path) -> dict[str, int]:
    result: dict[str, int] = {}

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return result

    for line_number, line in enumerate(lines, start=1):
        match = re.match(
            r"^\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*:",
            line,
        )

        if match:
            result[match.group(1)] = line_number

    return result


def extract_yaml_environment_blocks(
    data: Any,
    default_environment: str,
) -> list[tuple[str, dict[str, Any]]]:

    blocks: list[tuple[str, dict[str, Any]]] = []

    if isinstance(data, dict):

        if "environment" in data:
            environment = data["environment"]

            if isinstance(environment, dict):
                blocks.append(
                    (
                        default_environment,
                        environment,
                    )
                )

            elif isinstance(environment, list):

                mapping: dict[str, Any] = {}

                for item in environment:

                    if isinstance(item, str):

                        if "=" in item:
                            key, value = item.split("=", 1)
                            mapping[key.strip()] = value
                        else:
                            mapping[item.strip()] = ""

                    elif isinstance(item, dict):

                        for key, value in item.items():
                            mapping[str(key)] = value

                blocks.append(
                    (
                        default_environment,
                        mapping,
                    )
                )

        for key, value in data.items():

            child_environment = default_environment

            if isinstance(key, str):

                lowered = key.lower()

                if lowered in {"development", "dev"}:
                    child_environment = "development"

                elif lowered == "staging":
                    child_environment = "staging"

                elif lowered in {"production", "prod"}:
                    child_environment = "production"

            blocks.extend(
                extract_yaml_environment_blocks(
                    value,
                    child_environment,
                )
            )

    elif isinstance(data, list):

        for item in data:
            blocks.extend(
                extract_yaml_environment_blocks(
                    item,
                    default_environment,
                )
            )

    return blocks


def parse_yaml_file(
    path: Path,
) -> list[tuple[str, str, dict[str, Any]]]:

    results: list[
        tuple[str, str, dict[str, Any]]
    ] = []

    try:
        text = path.read_text(
            encoding="utf-8"
        )
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"Unable to decode {path.name} as UTF-8"
        ) from exc
    except OSError as exc:
        raise ValueError(
            f"Unable to read {path}: {exc}"
        ) from exc

    try:
        documents = list(
            yaml.safe_load_all(text)
        )
    except yaml.YAMLError as exc:
        raise ValueError(
            f"Malformed YAML syntax at {path.name}: {exc}"
        ) from exc

    line_map = yaml_line_map(path)

    default_environment = environment_from_filename(path)

    for document in documents:

        blocks = extract_yaml_environment_blocks(
            document,
            default_environment,
        )

        for environment, mapping in blocks:

            for key, raw_value in mapping.items():

                key = str(key)

                metadata = {
                    "type": infer_yaml_type(raw_value),
                    "file": path.name,
                    "line": line_map.get(key, 1),
                }

                results.append(
                    (
                        environment,
                        key,
                        metadata,
                    )
                )

    return results


def scan_code(
    repository: Path,
) -> dict[str, list[dict[str, Any]]]:

    references: dict[
        str,
        list[dict[str, Any]]
    ] = {}

    for path in repository.rglob("*"):

        if not path.is_file():
            continue

        if path.suffix.lower() not in CODE_EXTENSIONS:
            continue

        if any(
            part in {
                ".git",
                "node_modules",
                "__pycache__",
                ".venv",
                "venv",
            }
            for part in path.parts
        ):
            continue

        try:
            lines = path.read_text(
                encoding="utf-8"
            ).splitlines()
        except (
            UnicodeDecodeError,
            OSError,
        ):
            continue

        if path.suffix.lower() == ".py":
            patterns = PYTHON_PATTERNS
        else:
            patterns = JS_PATTERNS

        for line_number, line in enumerate(
            lines,
            start=1,
        ):

            for pattern in patterns:

                for match in pattern.finditer(line):

                    key = match.group(1)

                    references.setdefault(
                        key,
                        [],
                    ).append(
                        {
                            "file": path.relative_to(
                                repository
                            ).as_posix(),
                            "line": line_number,
                        }
                    )

    return references


def unique_references(
    references: list[dict[str, Any]],
) -> list[dict[str, Any]]:

    seen: set[tuple[str, int]] = set()
    result: list[dict[str, Any]] = []

    for reference in references:

        identifier = (
            reference["file"],
            reference["line"],
        )

        if identifier in seen:
            continue

        seen.add(identifier)
        result.append(reference)

    return result


def scan_configs(
    repository: Path,
) -> dict[str, dict[str, dict[str, Any]]]:

    configs: dict[
        str,
        dict[str, dict[str, Any]]
    ] = {}

    # ---------------------------------------------------------
    # Scan .env files
    # ---------------------------------------------------------

    for path in repository.rglob("*"):

        if not path.is_file():
            continue

        if any(
            part in {
                ".git",
                "node_modules",
                "__pycache__",
                ".venv",
                "venv",
            }
            for part in path.parts
        ):
            continue

        if ENV_FILE_PATTERN.match(path.name):

            environment = environment_from_filename(
                path
            )

            parsed = parse_env_file(path)

            for key, metadata in parsed.items():

                configs.setdefault(
                    environment,
                    {}
                )[key] = {
                    "type": metadata["type"],
                    "file": metadata["file"],
                    "line": metadata["line"],
                }

    # ---------------------------------------------------------
    # Scan YAML files separately.
    #
    # IMPORTANT:
    # YAML values are merged with .env values only when the
    # YAML source provides an additional configuration source.
    # Existing .env metadata is NOT replaced.
    # ---------------------------------------------------------

    for path in repository.rglob("*"):

        if not path.is_file():
            continue

        if any(
            part in {
                ".git",
                "node_modules",
                "__pycache__",
                ".venv",
                "venv",
            }
            for part in path.parts
        ):
            continue

        if not looks_like_compose_or_manifest(path):
            continue

        yaml_entries = parse_yaml_file(path)

        for environment, key, metadata in yaml_entries:

            environment_config = configs.setdefault(
                environment,
                {}
            )

            # If the key already exists in an .env file,
            # preserve that source as the primary environment
            # configuration for type comparison.
            #
            # Otherwise add the YAML configuration.
            if key not in environment_config:

                environment_config[key] = {
                    "type": metadata["type"],
                    "file": metadata["file"],
                    "line": metadata["line"],
                }

    return configs


def create_findings(
    code_refs: dict[str, list[dict[str, Any]]],
    configs: dict[str, dict[str, dict[str, Any]]],
) -> list[dict[str, Any]]:

    findings: list[dict[str, Any]] = []

    environments = [
        "development",
        "staging",
        "production",
    ]

    # ---------------------------------------------------------
    # MISSING
    # ---------------------------------------------------------

    for key, references in sorted(
        code_refs.items()
    ):

        references = unique_references(
            references
        )

        for environment in environments:

            if key not in configs.get(
                environment,
                {},
            ):

                findings.append(
                    {
                        "key": key,
                        "category": "Missing",
                        "severity": "Critical",
                        "environment": environment,
                        "references": references,
                        "config_references": [],
                        "message": (
                            f"{key} is referenced by source code at "
                            f"{', '.join(_format_reference(r) for r in references)} "
                            f"but is not defined in the "
                            f"{environment} configuration."
                        ),
                    }
                )

    # ---------------------------------------------------------
    # ORPHANED
    # ---------------------------------------------------------

    for environment, environment_config in sorted(
        configs.items()
    ):

        for key, metadata in sorted(
            environment_config.items()
        ):

            if key not in code_refs:

                findings.append(
                    {
                        "key": key,
                        "category": "Orphaned",
                        "severity": "Info",
                        "environment": environment,
                        "references": [],
                        "config_references": [
                            {
                                "file": metadata["file"],
                                "line": metadata["line"],
                            }
                        ],
                        "message": (
                            f"{key} is defined in the "
                            f"{environment} configuration at "
                            f"{metadata['file']}:{metadata['line']}, "
                            f"but no supported source code access "
                            f"was found."
                        ),
                    }
                )

    # ---------------------------------------------------------
    # TYPE MISMATCH
    # ---------------------------------------------------------

    all_keys: set[str] = set()

    for environment_config in configs.values():
        all_keys.update(
            environment_config.keys()
        )

    for key in sorted(all_keys):

        types_by_environment: dict[
            str,
            str
        ] = {}

        references: list[
            dict[str, Any]
        ] = []

        for environment in environments:

            metadata = configs.get(
                environment,
                {},
            ).get(key)

            if metadata:

                types_by_environment[
                    environment
                ] = metadata["type"]

                references.append(
                    {
                        "file": metadata["file"],
                        "line": metadata["line"],
                    }
                )

        distinct_types = set(
            types_by_environment.values()
        )

        if len(distinct_types) > 1:

            findings.append(
                {
                    "key": key,
                    "category": "Type-Mismatch",
                    "severity": "Warning",
                    "environment": "cross-environment",
                    "references": [],
                    "config_references": unique_references(
                        references
                    ),
                    "message": (
                        f"{key} has inconsistent inferred types "
                        f"across environments ("
                        + ", ".join(
                            f"{environment}="
                            f"{types_by_environment[environment]}"
                            for environment in environments
                            if environment
                            in types_by_environment
                        )
                        + ")."
                    ),
                }
            )

    findings.sort(
        key=lambda finding: (
            finding["key"],
            finding["category"],
            finding["environment"],
        )
    )

    return findings


def _format_reference(
    reference: dict[str, Any],
) -> str:

    return (
        f"{reference['file']}:"
        f"{reference['line']}"
    )


def finding_signature(
    finding: dict[str, Any],
) -> str:

    return (
        f"{finding['key']}::"
        f"{finding['category']}"
    )


def load_state() -> dict[str, Any]:

    if not STATE_FILE.exists():

        return {
            "version": STATE_VERSION,
            "known_findings": [],
        }

    try:

        data = json.loads(
            STATE_FILE.read_text(
                encoding="utf-8"
            )
        )

    except (
        OSError,
        json.JSONDecodeError,
    ) as exc:

        raise ValueError(
            f"Unable to read state file "
            f"{STATE_FILE}: {exc}"
        ) from exc

    if not isinstance(data, dict):
        raise ValueError(
            "Invalid .drift_state.json format."
        )

    known = data.get(
        "known_findings",
        [],
    )

    if not isinstance(
        known,
        list,
    ):
        raise ValueError(
            "Invalid .drift_state.json: "
            "known_findings must be a list."
        )

    deduplicated = sorted(
        {
            str(signature)
            for signature in known
            if isinstance(
                signature,
                str,
            )
        }
    )

    return {
        "version": STATE_VERSION,
        "known_findings": deduplicated,
    }


def save_state(
    signatures: set[str],
) -> None:

    state = {
        "known_findings": sorted(
            signatures
        ),
        "version": STATE_VERSION,
    }

    temporary = STATE_FILE.with_suffix(
        ".tmp"
    )

    try:

        temporary.write_text(
            json.dumps(
                state,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        temporary.replace(
            STATE_FILE
        )

    except OSError as exc:

        raise ValueError(
            f"Unable to save state file "
            f"{STATE_FILE}: {exc}"
        ) from exc


def format_finding(
    finding: dict[str, Any],
) -> str:

    key = finding["key"]
    category = finding["category"]
    severity = finding["severity"]
    environment = finding["environment"]

    if finding["references"]:

        references = ", ".join(
            _format_reference(reference)
            for reference in finding[
                "references"
            ]
        )

    else:

        references = ", ".join(
            _format_reference(reference)
            for reference in finding[
                "config_references"
            ]
        )

    return (
        f"- **{key}** — `{category}` "
        f"(`{severity}`) — `{environment}`\n"
        f"  - Reference: {references}\n"
        f"  - {finding['message']}"
    )


def generate_report(
    repository: Path,
    findings: list[dict[str, Any]],
    new_findings: list[dict[str, Any]],
    unresolved_findings: list[dict[str, Any]],
) -> str:

    critical = sum(
        finding["severity"] == "Critical"
        for finding in findings
    )

    warning = sum(
        finding["severity"] == "Warning"
        for finding in findings
    )

    info = sum(
        finding["severity"] == "Info"
        for finding in findings
    )

    lines: list[str] = []

    lines.append(
        "# Drift Watch Report"
    )

    lines.append("")

    lines.append(
        f"Repository: `{repository}`"
    )

    lines.append("")

    lines.append(
        "Configuration values are intentionally "
        "excluded from this report."
    )

    lines.append("")

    lines.append(
        "## Summary"
    )

    lines.append("")

    lines.append(
        f"- Total findings: **{len(findings)}**"
    )

    lines.append(
        f"- New since last run: "
        f"**{len(new_findings)}**"
    )

    lines.append(
        f"- Still unresolved: "
        f"**{len(unresolved_findings)}**"
    )

    lines.append(
        f"- Critical: **{critical}**"
    )

    lines.append(
        f"- Warning: **{warning}**"
    )

    lines.append(
        f"- Info: **{info}**"
    )

    lines.append("")

    lines.append(
        "## New Since Last Run"
    )

    lines.append("")

    if new_findings:

        for finding in new_findings:
            lines.append(
                format_finding(finding)
            )

    else:

        lines.append(
            "No new drift findings."
        )

    lines.append("")

    lines.append(
        "## Still Unresolved"
    )

    lines.append("")

    if unresolved_findings:

        for finding in unresolved_findings:
            lines.append(
                format_finding(finding)
            )

    else:

        lines.append(
            "No unresolved drift findings."
        )

    lines.append("")

    lines.append(
        "## Status"
    )

    lines.append("")

    if critical > 0:

        lines.append(
            "❌ **DRIFT DETECTED — "
            "critical configuration is missing.**"
        )

    elif warning > 0:

        lines.append(
            "⚠️ **DRIFT DETECTED — "
            "configuration warnings require review.**"
        )

    elif info > 0:

        lines.append(
            "ℹ️ **DRIFT DETECTED — "
            "informational configuration drift found.**"
        )

    else:

        lines.append(
            "✅ **NO DRIFT DETECTED.**"
        )

    return "\n".join(lines)


def validate_repository(
    repository: Path,
) -> None:

    if not repository.exists():

        raise ValueError(
            f"Target repository does not exist: "
            f"{repository}"
        )

    if not repository.is_dir():

        raise ValueError(
            f"Target path is not a directory: "
            f"{repository}"
        )


def run(
    repository: Path,
) -> int:

    validate_repository(
        repository
    )

    code_references = scan_code(
        repository
    )

    configurations = scan_configs(
        repository
    )

    findings = create_findings(
        code_references,
        configurations,
    )

    state = load_state()

    previous_signatures = set(
        state["known_findings"]
    )

    current_signatures = {
        finding_signature(finding)
        for finding in findings
    }

    new_signatures = (
        current_signatures
        - previous_signatures
    )

    new_findings = [
        finding
        for finding in findings
        if finding_signature(finding)
        in new_signatures
    ]

    unresolved_findings = list(
        findings
    )

    save_state(
        current_signatures
    )

    report = generate_report(
        repository,
        findings,
        new_findings,
        unresolved_findings,
    )

    print(report)

    if any(
        finding["severity"]
        in {"Critical", "Warning"}
        for finding in findings
    ):

        return 2

    return 0


def build_argument_parser() -> argparse.ArgumentParser:

    parser = argparse.ArgumentParser(
        prog="drift_watch.py",
        description=(
            "Detect configuration and secrets "
            "drift between source code and "
            "deployment environments."
        ),
    )

    parser.add_argument(
        "path",
        help="Target repository path to scan.",
    )

    return parser


def main() -> int:

    parser = build_argument_parser()

    args = parser.parse_args()

    repository = Path(
        args.path
    ).resolve()

    try:

        return run(
            repository
        )

    except KeyboardInterrupt:

        print(
            "\nDrift Watch interrupted."
        )

        return 130

    except Exception as exc:

        print(
            f"Drift Watch ERROR: {exc}"
        )

        return 1


if __name__ == "__main__":
    sys.exit(main())
