"""Tests de la configuration."""

from __future__ import annotations

import dataclasses
import logging
import sys
from pathlib import Path

import pytest

from news_property.config import (
    DATA_SUBDIRS,
    ConfigError,
    Settings,
    default_data_dir,
    ensure_data_dir,
    load_settings,
)
from news_property.logging_setup import configure_logging


def test_defaults(tmp_path: Path) -> None:
    env = {"LOCALAPPDATA": str(tmp_path), "XDG_DATA_HOME": str(tmp_path)}
    settings = load_settings(env)

    expected_name = "NewsProperty" if sys.platform == "win32" else "news-property"
    assert settings.data_dir == (tmp_path / expected_name).resolve()
    assert settings.host == "127.0.0.1"
    assert settings.port == 5000
    assert settings.debug is False
    assert settings.open_browser is True
    assert settings.log_level == "INFO"


def test_default_data_dir_windows(tmp_path: Path) -> None:
    env = {"LOCALAPPDATA": str(tmp_path)}
    assert default_data_dir(env, platform="win32") == tmp_path / "NewsProperty"


def test_default_data_dir_linux_without_xdg() -> None:
    expected = Path.home() / ".local" / "share" / "news-property"
    assert default_data_dir({}, platform="linux") == expected


def test_env_overrides(tmp_path: Path) -> None:
    env = {
        "CP_DATA_DIR": str(tmp_path / "custom"),
        "CP_PORT": "8123",
        "CP_DEBUG": "true",
        "CP_OPEN_BROWSER": "non",
        "CP_LOG_LEVEL": "debug",
    }
    settings = load_settings(env)

    assert settings.data_dir == (tmp_path / "custom").resolve()
    assert settings.port == 8123
    assert settings.debug is True
    assert settings.open_browser is False
    assert settings.log_level == "DEBUG"


def test_empty_values_use_defaults(tmp_path: Path) -> None:
    env = {"CP_DATA_DIR": str(tmp_path), "CP_PORT": "", "CP_DEBUG": "  "}
    settings = load_settings(env)
    assert settings.port == 5000
    assert settings.debug is False


@pytest.mark.parametrize("port", ["abc", "80", "70000", "5000.5"])
def test_invalid_port(tmp_path: Path, port: str) -> None:
    with pytest.raises(ValueError, match="CP_PORT"):
        load_settings({"CP_DATA_DIR": str(tmp_path), "CP_PORT": port})


@pytest.mark.parametrize("name", ["CP_DEBUG", "CP_OPEN_BROWSER"])
def test_invalid_bool(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError, match=name):
        load_settings({"CP_DATA_DIR": str(tmp_path), name: "peut-être"})


def test_invalid_log_level(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="CP_LOG_LEVEL"):
        load_settings({"CP_DATA_DIR": str(tmp_path), "CP_LOG_LEVEL": "BAVARD"})


def test_data_subdirs_created(tmp_path: Path) -> None:
    data_dir = tmp_path / "nouveau" / "dossier"
    load_settings({"CP_DATA_DIR": str(data_dir)})
    for name in DATA_SUBDIRS:
        assert (data_dir / name).is_dir()


def test_data_dir_not_writable(tmp_path: Path) -> None:
    # Un fichier à la place du dossier : la création des sous-dossiers échoue.
    blocker = tmp_path / "fichier"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(ConfigError, match="accessible en écriture"):
        ensure_data_dir(blocker)


def test_settings_immutable(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path)
    with pytest.raises(dataclasses.FrozenInstanceError):
        settings.port = 6000  # type: ignore[misc]


def test_host_is_always_localhost(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        Settings(data_dir=tmp_path, host="0.0.0.0")


def test_configure_logging_writes_in_data_dir(settings: Settings) -> None:
    log_path = configure_logging(settings)
    try:
        logging.getLogger("news_property.test").warning("\x1b[31mmessage de test\x1b[0m")
        for handler in logging.getLogger().handlers:
            handler.flush()
        content = log_path.read_text(encoding="utf-8")
        assert log_path.parent == settings.logs_dir
        assert "message de test" in content
        assert "\x1b[" not in content
    finally:
        root = logging.getLogger()
        for handler in list(root.handlers):
            if getattr(handler, "_news_property_handler", False):
                root.removeHandler(handler)
                handler.close()
