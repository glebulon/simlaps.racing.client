import json
from pathlib import Path

import pytest

from src.core.analyzer.ai_prompt import generate_ai_prompt

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.asyncio
async def test_full_prompt_matches_frozen_fixture_exactly(tmp_path):
    data = json.loads((FIXTURES / "ai_prompt_full_input.json").read_text(encoding="utf-8"))
    expected = json.loads((FIXTURES / "ai_prompt_full_expected.json").read_text(encoding="utf-8"))

    path = await generate_ai_prompt(data, str(tmp_path), "full-fixture")

    assert Path(path).read_text(encoding="utf-8") == expected
