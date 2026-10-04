import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import synthetic_da  # noqa: E402


@pytest.fixture(scope="session")
def synthetic_workbook(tmp_path_factory):
    return synthetic_da.build(str(tmp_path_factory.mktemp("da") / "synthetic_da.xlsx"))


@pytest.fixture(scope="session")
def generated(synthetic_workbook, tmp_path_factory):
    """Metric and US databases generated from the synthetic Digital Annex."""
    pytest.importorskip("pretty_j1939")
    import j1939db_tools as tools
    out = tmp_path_factory.mktemp("generated")
    return tools.generate([synthetic_workbook], str(out), log=lambda *_: None)
