"""Tests for Settings loading and value provenance tracking."""

from pathlib import Path

import pytest

from newa.models.settings import Settings

CONFIG = """\
[testingfarm]
token = token-from-file

[jira]
url = https://jira.example.com
token = jira-token-from-file
"""


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    path = tmp_path / "newa.conf"
    path.write_text(CONFIG)
    return path


def test_config_file_value_and_source(config_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TESTING_FARM_API_TOKEN", raising=False)
    settings = Settings.load(config_file)
    assert settings.tf_token == "token-from-file"
    assert settings.describe_source("testingfarm/token") == "config file [testingfarm] token"


def test_env_var_overrides_config_file(config_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESTING_FARM_API_TOKEN", "token-from-env")
    settings = Settings.load(config_file)
    assert settings.tf_token == "token-from-env"
    assert settings.describe_source("testingfarm/token") == (
        "environment variable $TESTING_FARM_API_TOKEN"
        )


def test_empty_env_var_falls_back_to_config(
        config_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # An empty environment variable must not shadow the config file value.
    monkeypatch.setenv("TESTING_FARM_API_TOKEN", "")
    settings = Settings.load(config_file)
    assert settings.tf_token == "token-from-file"
    assert settings.describe_source("testingfarm/token") == "config file [testingfarm] token"


def test_built_in_default_source(config_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEWA_ROG_TOKEN", raising=False)
    settings = Settings.load(config_file)
    # rog/token is neither in the env nor the config file above.
    assert settings.rog_token == ""
    assert settings.describe_source("rog/token") == "built-in default"


def test_default_section_inheritance_source(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A value inherited from [DEFAULT] must be reported as [DEFAULT], not as
    # the service section (ConfigParser.has_option matches inherited defaults).
    monkeypatch.delenv("TESTING_FARM_API_TOKEN", raising=False)
    path = tmp_path / "newa.conf"
    path.write_text("[DEFAULT]\ntoken = shared-token\n[testingfarm]\n")
    settings = Settings.load(path)
    assert settings.tf_token == "shared-token"
    assert settings.describe_source("testingfarm/token") == "config file [DEFAULT] token"


def test_describe_source_unknown_path_defaults() -> None:
    # A directly-constructed Settings has no recorded provenance.
    assert Settings().describe_source("testingfarm/token") == "built-in default"
