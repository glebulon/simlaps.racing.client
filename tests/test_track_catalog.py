"""
Tests for track catalog module.

Tests track profile selection, alias matching, and config selection.
"""

import pytest

from src.core.track_catalog import (
    TRACK_CATALOG,
    build_track_profile,
    find_track_by_name,
    select_track_profile,
)


class TestSelectTrackProfile:
    """Test track profile selection."""

    def test_select_by_track_name_direct(self):
        """Test selecting track by direct name match."""
        # Use actual track name from catalog
        track_key, profile = select_track_profile(track_name="brands_hatch")

        assert track_key is not None
        assert profile is not None

    def test_select_by_track_name_alias(self):
        """Test selecting track by alias."""
        track_key, profile = select_track_profile(track_name="brands")

        assert track_key is not None
        assert profile is not None

    def test_select_by_track_name_with_config(self):
        """Test selecting track with specific config."""
        track_key, profile = select_track_profile(track_name="brands_hatch", config_name="gp")

        assert track_key is not None
        assert profile is not None

    def test_select_by_track_name_case_insensitive(self):
        """Test case-insensitive track name matching."""
        track_key, profile = select_track_profile(track_name="BRANDS")

        assert track_key is not None
        assert profile is not None

    def test_select_by_track_name_not_found(self):
        """Test selecting non-existent track."""
        track_key, profile = select_track_profile(track_name="nonexistent_track")

        assert track_key is None
        assert profile is None

    def test_select_by_path(self):
        """Test selecting track by path."""
        track_key, profile = select_track_profile(path="brands_hatch")

        assert track_key is not None
        assert profile is not None

    def test_select_by_path_not_found(self):
        """Test selecting by non-existent path."""
        track_key, profile = select_track_profile(path="nonexistent_path")

        assert track_key is None
        assert profile is None

    def test_select_none_inputs(self):
        """Test select with None inputs."""
        track_key, profile = select_track_profile(track_name=None, path=None)

        assert track_key is None
        assert profile is None

    def test_monza_v04_windows_match_ac_evo_progress_clusters(self):
        """Monza profile should align first chicane and Curva Grande to decoded progress."""
        track_key, profile = select_track_profile(track_name="monza")

        assert track_key == "monza"
        corners = {corner["id"]: corner for corner in profile["corners"]}
        assert corners[1]["start"] == pytest.approx(0.145)
        assert corners[2]["end"] == pytest.approx(0.195)
        assert corners[3]["start"] >= 0.220
        assert corners[11]["start"] == pytest.approx(0.875)


class TestFindTrackByName:
    """Test find_track_by_name function."""

    def test_find_by_name_direct(self):
        """Test finding track by direct key."""
        track_key, profile = find_track_by_name("brands_hatch")

        assert track_key == "brands_hatch"
        assert profile is not None

    def test_find_by_name_alias(self):
        """Test finding track by alias."""
        track_key, profile = find_track_by_name("brands")

        assert track_key is not None
        assert profile is not None

    def test_find_by_name_case_insensitive(self):
        """Test case-insensitive matching."""
        track_key, profile = find_track_by_name("BRANDS")

        assert track_key is not None
        assert profile is not None

    def test_find_by_name_with_spaces(self):
        """Test matching with spaces replaced by dashes."""
        track_key, profile = find_track_by_name("brands hatch")

        assert track_key is not None
        assert profile is not None

    def test_find_by_name_with_underscores(self):
        """Test matching with underscores replaced by dashes."""
        track_key, profile = find_track_by_name("brands_hatch_gp")

        assert track_key is not None
        assert profile is not None

    def test_find_by_name_prefix_match(self):
        """Test prefix matching on alias."""
        track_key, profile = find_track_by_name("brands")

        assert track_key is not None
        assert profile is not None

    def test_find_by_name_not_found(self):
        """Test finding non-existent track."""
        track_key, profile = find_track_by_name("nonexistent_track")

        assert track_key is None
        assert profile is None

    def test_find_by_name_none(self):
        """Test find with None input."""
        track_key, profile = find_track_by_name(None)

        assert track_key is None
        assert profile is None


class TestBuildTrackProfile:
    """Test build_track_profile function."""

    def test_build_profile_basic(self):
        """Test basic profile building."""
        profile = build_track_profile("brands_hatch", "gp")

        assert profile is not None
        assert "corners" in profile

    def test_build_profile_with_corners(self):
        """Test profile with corner data."""
        profile = build_track_profile("brands_hatch", "gp")

        assert profile["corners"] is not None
        assert isinstance(profile["corners"], list)

    def test_build_profile_confidence_profiled(self):
        """Profile with no estimated corners reports profiled confidence."""
        profile = build_track_profile("monza", "v0_4")

        assert profile["confidence"] == "profiled"

    def test_build_profile_confidence_estimated(self):
        """Profile with any estimated corner reports estimated confidence."""
        profile = build_track_profile("silverstone", "gp")

        assert profile["confidence"] == "estimated"


class TestTrackCatalog:
    """Test TRACK_CATALOG structure."""

    def test_catalog_not_empty(self):
        """Test that catalog has entries."""
        assert len(TRACK_CATALOG) > 0

    def test_catalog_has_required_fields(self):
        """Test catalog entries have required fields."""
        for _track_key, track_data in TRACK_CATALOG.items():
            assert "name" in track_data or "display_name" in track_data
            assert "configs" in track_data
            assert "default_config" in track_data
            assert "aliases" in track_data


class TestNordschleifeConfigSelection:
    """Layout-aware profile selection for the Nürburgring Nordschleife."""

    def test_static_nordschleife_selects_nordschleife_config(self):
        """Static SHM config "Nordschleife" resolves to the plain layout."""
        track_key, profile = select_track_profile(track_name="Nurburgring", config_name="Nordschleife")

        assert track_key == "nurburgring_nordschleife"
        assert profile["config_key"] == "nordschleife"
        assert profile["config_name"] == "Nordschleife"
        assert len(profile["corners"]) == 53

    def test_static_gp_selects_gp_config(self):
        """Static SHM config "GP" skips the Nordschleife entry and finds GP."""
        track_key, profile = select_track_profile(track_name="Nurburgring", config_name="GP")

        assert track_key == "nurburgring_gp"
        assert profile["config_key"] == "gp"

    def test_no_config_keeps_default_24h(self):
        """Without a static config the 24h default is preserved."""
        track_key, profile = select_track_profile(track_name="Nurburgring")

        assert track_key == "nurburgring_nordschleife"
        assert profile["config_key"] == "24h"

    def test_touristenfahrten_alias_moved_to_nordschleife(self):
        """touristenfahrten (= plain tourist layout) must not alias 24h."""
        entry = TRACK_CATALOG["nurburgring_nordschleife"]
        assert "touristenfahrten" not in entry["configs"]["24h"]["aliases"]
        assert "touristenfahrten" in entry["configs"]["nordschleife"]["aliases"]
        assert entry["default_config"] == "24h"

    def test_nordschleife_corners_span_full_layout(self):
        """The plain profile spans ~0.007-0.852, not the 24h's 0.01-0.40."""
        profile = build_track_profile("nurburgring_nordschleife", "nordschleife")
        starts = [corner["start"] for corner in profile["corners"]]
        ends = [corner["end"] for corner in profile["corners"]]

        assert min(starts) == pytest.approx(0.0073)
        assert max(ends) == pytest.approx(0.852)
        assert max(ends) > 0.85
        assert profile["confidence"] == "estimated"
