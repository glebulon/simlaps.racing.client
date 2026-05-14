"""Regression tests for HomePage UI interactions."""

from unittest.mock import MagicMock, PropertyMock, patch

import flet as ft

from src.ui.pages.home import HomePage, UPDATE_DOWNLOAD_URL
from src.utils.config import AppConfig


def _get_update_download_button(home_page: HomePage) -> ft.TextButton:
    for control in home_page._update_banner.content.controls:
        if isinstance(control, ft.TextButton) and control.content == "Download":
            return control

    raise AssertionError("Download button not found on update banner")


def test_update_banner_download_button_wired_to_open_update_url() -> None:
    with patch.object(HomePage, "_open_update_url") as open_update_url_mock:
        home_page = HomePage(AppConfig())
        download_button = _get_update_download_button(home_page)

        assert download_button.on_click is not None
        download_button.on_click(MagicMock())

    open_update_url_mock.assert_called_once()


def test_open_update_url_launches_expected_download_url() -> None:
    with patch.object(HomePage, "page", new_callable=PropertyMock) as page_prop:
        page_mock = MagicMock()
        page_prop.return_value = page_mock

        home_page = HomePage(AppConfig())
        home_page._open_update_url()

    page_mock.launch_url.assert_called_once_with(UPDATE_DOWNLOAD_URL)
