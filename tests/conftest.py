import copy
import json
from pathlib import Path

import pytest

from record_mcp.catalog import load_catalog
from record_mcp.engine import Engine

EXAMPLES = Path(__file__).parent / "fixtures" / "examples"
_CACHE = {p.name: json.loads(p.read_text()) for p in EXAMPLES.glob("*.json")}


def example(name: str) -> dict:
    """A fresh deep copy of a record-spec example trace."""
    return copy.deepcopy(_CACHE[name])


def rule_ids(violations) -> set[str]:
    return {v.rule_id for v in violations}


@pytest.fixture(scope="session")
def catalog():
    return load_catalog()


@pytest.fixture(scope="session")
def engine(catalog):
    return Engine(catalog)


@pytest.fixture
def aml() -> dict:
    return example("aml_triage.json")


@pytest.fixture
def anyio_backend():
    return "asyncio"
