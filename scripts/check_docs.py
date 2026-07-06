"""Portfolio documentation consistency and leak checks."""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_SRC = ROOT / "apps" / "api"

REQUIRED_DOCS = (
    ROOT / "README.md",
    ROOT / "docs" / "architecture" / "README.md",
    ROOT / "docs" / "architecture" / "system-overview.md",
    ROOT / "docs" / "architecture" / "data-model.md",
    ROOT / "docs" / "architecture" / "api-contracts.md",
    ROOT / "docs" / "architecture" / "model-routing.md",
    ROOT / "docs" / "architecture" / "evaluation.md",
    ROOT / "docs" / "privacy" / "README.md",
    ROOT / "docs" / "privacy" / "data-flow.md",
    ROOT / "docs" / "safety" / "README.md",
    ROOT / "docs" / "safety" / "failure-modes.md",
    ROOT / "docs" / "demo-walkthrough.md",
    ROOT / "docs" / "docs-consistency-checklist.md",
)

REQUIRED_PHRASES = (
    "wellness decision support",
    "SQL",
    "RAG",
    "deterministic",
    "LLM",
    "evidence",
    "confidence",
    "uncertainty",
    "safety",
    "synthetic",
)

PRIVATE_DATA_PATTERNS = (
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b",
        r"\bsk-[a-z0-9][a-z0-9_-]{8,}\b",
        r"\bAKIA[0-9A-Z]{16}\b",
        r"BEGIN [A-Z ]*PRIVATE KEY",
        r"api[_-]?key\s*[:=]",
        r"\bsecret(?:\s+(?:token|key|value|credential|password)|\s*[:=])",
        r"raw_prompt\s*[:=]",
        r"prompt_payload\s*[:=]",
    )
)

MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
BASELINE_MODULE_RE = re.compile(r"\bbaseline_api(?:\.[A-Za-z_][A-Za-z0-9_]*)+")
ENDPOINT_RE = re.compile(r"`(?:GET|POST|PUT|DELETE|PATCH)?\s*(/(?:health|metrics|v1)[^` ]*)`")


def main() -> int:
    failures: list[str] = []
    docs = _docs_to_scan()

    _check_required_docs(failures)
    _check_markdown_links(docs, failures)
    _check_required_phrases(docs, failures)
    _check_private_data(docs, failures)
    _check_baseline_modules(docs, failures)
    _check_endpoint_claims(docs, failures)
    _check_eval_inventory(failures)

    if failures:
        print("Documentation check failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print(f"Documentation check passed for {len(docs)} Markdown files.")
    return 0


def _docs_to_scan() -> list[Path]:
    docs = [ROOT / "README.md"]
    docs.extend(sorted((ROOT / "docs").rglob("*.md")))
    return docs


def _check_required_docs(failures: list[str]) -> None:
    for path in REQUIRED_DOCS:
        if not path.exists():
            failures.append(f"required doc missing: {_display(path)}")


def _check_markdown_links(docs: list[Path], failures: list[str]) -> None:
    for doc in docs:
        text = doc.read_text(encoding="utf-8")
        for raw_target in MARKDOWN_LINK_RE.findall(text):
            target = raw_target.strip().split()[0]
            if _is_external_or_anchor(target):
                continue
            path_part = target.split("#", 1)[0]
            if not path_part:
                continue
            resolved = (doc.parent / path_part).resolve()
            if not _is_inside_root(resolved):
                failures.append(f"{_display(doc)} links outside repository: {target}")
                continue
            if not resolved.exists():
                failures.append(f"{_display(doc)} has broken link: {target}")


def _check_required_phrases(docs: list[Path], failures: list[str]) -> None:
    combined = "\n".join(doc.read_text(encoding="utf-8") for doc in docs)
    for phrase in REQUIRED_PHRASES:
        if phrase not in combined:
            failures.append(f"required portfolio phrase missing from docs: {phrase}")


def _check_private_data(docs: list[Path], failures: list[str]) -> None:
    for doc in docs:
        text = doc.read_text(encoding="utf-8")
        for pattern in PRIVATE_DATA_PATTERNS:
            match = pattern.search(text)
            if match is not None:
                failures.append(
                    f"{_display(doc)} matches private-data pattern {pattern.pattern!r}: "
                    f"{match.group(0)!r}"
                )


def _check_baseline_modules(docs: list[Path], failures: list[str]) -> None:
    for doc in docs:
        text = doc.read_text(encoding="utf-8")
        for module_name in sorted(set(BASELINE_MODULE_RE.findall(text))):
            if not _baseline_module_exists(module_name):
                failures.append(f"{_display(doc)} references missing module: {module_name}")


def _baseline_module_exists(module_name: str) -> bool:
    parts = module_name.split(".")
    while len(parts) > 1:
        rel_parts = parts[1:]
        candidate = API_SRC / "baseline_api" / Path(*rel_parts)
        if (candidate.with_suffix(".py")).exists() or (candidate / "__init__.py").exists():
            return True
        parts.pop()
    return False


def _check_endpoint_claims(docs: list[Path], failures: list[str]) -> None:
    route_paths = _route_paths()
    for doc in docs:
        text = doc.read_text(encoding="utf-8")
        for endpoint in sorted(set(ENDPOINT_RE.findall(text))):
            endpoint = endpoint.strip()
            if "*" in endpoint:
                continue
            if endpoint not in route_paths:
                failures.append(f"{_display(doc)} references unknown endpoint: {endpoint}")


def _route_paths() -> set[str]:
    sys.path.insert(0, str(API_SRC))
    from baseline_api.app import create_app

    app = create_app()
    paths: set[str] = set()
    for route in app.routes:
        path = getattr(route, "path_format", None) or getattr(route, "path", None)
        if path is not None:
            paths.add(str(path))
        original_router = getattr(route, "original_router", None)
        if original_router is None:
            continue
        for child_route in getattr(original_router, "routes", ()):
            child_path = getattr(child_route, "path_format", None) or getattr(
                child_route,
                "path",
                None,
            )
            if child_path is not None:
                paths.add(str(child_path))
    return paths


def _check_eval_inventory(failures: list[str]) -> None:
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(API_SRC))
    from packages.eval.suites import build_default_registry

    suites = build_default_registry().selected()
    counts = Counter(suite.eval_type.value for suite in suites)
    expected = {
        "reasoning": 31,
        "safety": 14,
        "privacy": 6,
        "retrieval": 4,
        "regression": 1,
        "deterministic": 1,
        "llm_property": 1,
    }
    if len(suites) != 58 or counts != expected:
        failures.append(f"eval inventory drifted: total={len(suites)}, counts={dict(counts)}")

    report = (ROOT / "docs" / "architecture" / "evaluation.md").read_text(encoding="utf-8")
    if "58 suites" not in report or "| `reasoning` | 31 |" not in report:
        failures.append("evaluation report does not reflect the current default suite inventory")
    if "Total pass rate: 58/58 suites passed (100%)" not in report:
        failures.append("evaluation report does not include the current eval pass rate")
    if "| `safety` | 14/14 passed |" not in report or "| `privacy` | 6/6 passed |" not in report:
        failures.append("evaluation report does not include current safety/privacy results")


def _is_external_or_anchor(target: str) -> bool:
    return (
        target.startswith("#")
        or target.startswith("http://")
        or target.startswith("https://")
        or target.startswith("mailto:")
    )


def _is_inside_root(path: Path) -> bool:
    try:
        path.relative_to(ROOT)
    except ValueError:
        return False
    return True


def _display(path: Path) -> str:
    return str(path.relative_to(ROOT))


if __name__ == "__main__":
    raise SystemExit(main())
