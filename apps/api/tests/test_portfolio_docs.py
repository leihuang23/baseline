"""Portfolio documentation consistency checks."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_check_docs_main():
    script_path = Path("scripts/check_docs.py")
    spec = importlib.util.spec_from_file_location("check_docs", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.main


def test_portfolio_docs_consistency_and_leak_check() -> None:
    """P6-02 docs must stay linked, accurate to key code surfaces, and leak-free."""

    assert _load_check_docs_main()() == 0
