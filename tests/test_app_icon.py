"""Anwendungssymbol und Windows-AppUserModelID."""

import sys

import pytest

from erd_editor import main


def test_icon_file_is_packaged_and_loads(qapp):
    assert main.APP_ICON.is_file()
    icon = main.app_icon()
    assert not icon.isNull()
    sizes = {s.width() for s in icon.availableSizes()}
    assert {16, 32, 256} <= sizes  # Taskleiste, Titelleiste, Explorer


def test_missing_icon_gives_empty_icon(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(main, "APP_ICON", tmp_path / "fehlt.ico")
    assert main.app_icon().isNull()


@pytest.mark.skipif(sys.platform != "win32", reason="nur unter Windows")
def test_app_user_model_id_is_set():
    import ctypes
    from ctypes import wintypes

    assert main.set_app_user_model_id()
    shell32 = ctypes.windll.shell32
    value = wintypes.LPWSTR()
    assert shell32.GetCurrentProcessExplicitAppUserModelID(ctypes.byref(value)) == 0
    try:
        assert value.value == main.APP_USER_MODEL_ID
    finally:
        ctypes.windll.ole32.CoTaskMemFree(value)


def test_other_platforms_do_nothing(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert main.set_app_user_model_id() is False
