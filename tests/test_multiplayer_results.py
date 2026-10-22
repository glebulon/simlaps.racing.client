import json
from unittest.mock import Mock

from src.ui.app import AppPage, SimLapsApp
from src.ui.pages.home import HomePage
from src.ui.pages.multiplayer_results import MultiplayerResultsPage, load_results
from src.utils.config import AppConfig


def _save_result(folder, name, **overrides):
    data = {
        "season": "Test server",
        "track": "Nurburgring",
        "layout": "Touristenfahrten Time Attack",
        "session": "Practice",
        "drivers": [
            {
                "position": 0,
                "driver_name": "Glebulon",
                "driver_surname": " ",
                "car_name": "Porsche 911 GT3 RS",
                "laps": 3,
                "best_lap": "07:10.275",
                "diff_first": "",
            },
            {
                "position": 1,
                "driver_name": "Other",
                "driver_surname": "Driver",
                "car_name": "BMW M4",
                "laps": 2,
                "best_lap": "",
                "diff_first": "",
            },
        ],
    }
    data.update(overrides)
    (folder / name).write_text(json.dumps(data), encoding="utf-8")


def test_load_results_newest_first_skips_invalid_files(tmp_path):
    _save_result(tmp_path, "261011-2047_MP_Practice.sessionresults.json")
    _save_result(tmp_path, "261012-0010_MP_Practice.sessionresults.json")
    (tmp_path / "261013-0000_MP_Practice.sessionresults.json").write_text("{", encoding="utf-8")
    (tmp_path / "other.json").write_text("{}", encoding="utf-8")

    sessions = load_results(tmp_path)

    assert len(sessions) == 2
    assert sessions[0].date == "Oct 12, 2026 00:10"
    assert sessions[0].drivers[0]["driver_name"] == "Glebulon"


def test_results_page_displays_no_valid_lap_and_preserves_names(tmp_path):
    _save_result(tmp_path, "261012-0010_MP_Practice.sessionresults.json")
    page = MultiplayerResultsPage(results_dir=tmp_path)
    page.refresh()
    page._select_session(0)

    rows = page._list_view.controls
    assert rows[0].content.controls[0].value == "1"
    assert rows[0].content.controls[1].controls[0].value == "Glebulon"
    assert rows[1].content.controls[1].controls[0].value == "Other Driver"
    assert rows[1].content.controls[2].controls[0].value == "No valid lap"
    assert rows[1].content.controls[1].controls[1].value == "BMW M4 · 2 laps"


def test_refresh_replaces_cached_results(tmp_path):
    _save_result(tmp_path, "261011-2047_MP_Practice.sessionresults.json")
    page = MultiplayerResultsPage(results_dir=tmp_path)
    page.refresh()
    assert len(page._sessions) == 1

    _save_result(tmp_path, "261012-0010_MP_Practice.sessionresults.json")
    assert len(page._sessions) == 1
    page.refresh()
    assert len(page._sessions) == 2


def test_results_page_navigation_loads_once_and_reuses_in_memory_data(tmp_path):
    _save_result(tmp_path, "261012-0010_MP_Practice.sessionresults.json")
    app = SimLapsApp.__new__(SimLapsApp)
    app.page = Mock()
    app._results_page = MultiplayerResultsPage(results_dir=tmp_path)

    app._show_page(AppPage.MULTIPLAYER_RESULTS)
    (tmp_path / "261012-0010_MP_Practice.sessionresults.json").write_text("{", encoding="utf-8")
    app._show_page(AppPage.MULTIPLAYER_RESULTS)

    assert len(app._results_page._sessions) == 1
    assert app.page.add.call_count == 2


def test_home_results_button_uses_callback():
    callback = Mock()
    home = HomePage(AppConfig(), on_results_click=callback)
    home._handle_results_click(None)
    callback.assert_called_once_with()
