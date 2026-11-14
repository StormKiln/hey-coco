import os
from pathlib import Path

import pytest

from coco_hub.settings import Settings

TEST_TOKEN = "test-token-0123456789abcdef"


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep a developer's real config and COCO_* env out of every test."""
    for key in list(os.environ):
        if key.startswith("COCO_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("COCO_CONFIG", str(tmp_path / "absent.toml"))


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        token=TEST_TOKEN,
        data_dir=tmp_path / "data",
        discovery={"enabled": False},
    )


@pytest.fixture
def anyio_backend() -> str:
    """Run ``@pytest.mark.anyio`` tests on asyncio only."""
    return "asyncio"
