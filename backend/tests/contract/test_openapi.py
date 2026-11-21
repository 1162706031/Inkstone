import json
from pathlib import Path

from ai_platform.api.main import app


def test_export_matches_runtime() -> None:
    contract = Path(__file__).resolve().parents[3] / "contracts/openapi.json"
    assert json.loads(contract.read_text(encoding="utf-8")) == app.openapi()
