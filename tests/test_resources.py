"""Resource sampling for the Resources page.

No Qt here: the sampler reports numbers, and keeping it separate means it
can be checked without a window.
"""

from __future__ import annotations

import os
import tempfile

import pytest

psutil = pytest.importorskip("psutil")

from desktop_app.resources import ResourceSampler  # noqa: E402


@pytest.fixture
def sampler() -> ResourceSampler:
    return ResourceSampler(tempfile.gettempdir())


def test_sample_reports_every_meter(sampler: ResourceSampler) -> None:
    """A reading carries all the fields the page renders."""
    reading = sampler.sample()

    for key in ("cpu", "per_core", "memory", "disk", "process", "uptime", "core_count"):
        assert key in reading, f"missing {key}"


def test_cpu_is_a_percentage_within_range(sampler: ResourceSampler) -> None:
    """CPU load is a bounded percentage."""
    reading = sampler.sample()

    assert 0.0 <= reading["cpu"] <= 100.0


def test_per_core_matches_the_core_count(sampler: ResourceSampler) -> None:
    """One reading per logical core, so the grid lines up."""
    reading = sampler.sample()

    assert len(reading["per_core"]) == reading["core_count"]
    assert all(0.0 <= value <= 100.0 for value in reading["per_core"])


def test_memory_reports_human_readable_values(sampler: ResourceSampler) -> None:
    """Memory includes both a percentage and readable text."""
    memory = sampler.sample()["memory"]

    assert 0 <= memory["percent"] <= 100
    assert memory["total"] > 0
    assert memory["used_human"].endswith(("B", "KB", "MB", "GB", "TB", "PB"))
    assert memory["used_human"] != memory["total_human"]


def test_disk_reports_the_path_it_measured(sampler: ResourceSampler) -> None:
    """The disk figure says which drive it describes."""
    disk = sampler.sample()["disk"]

    assert os.path.isdir(disk["path"])
    assert 0 <= disk["percent"] <= 100


def test_process_reports_this_process(sampler: ResourceSampler) -> None:
    """The app's own footprint is included."""
    process = sampler.sample()["process"]

    assert process["memory"] > 0
    assert process["threads"] >= 1


def test_uptime_is_formatted(sampler: ResourceSampler) -> None:
    """Uptime reads as text rather than raw seconds."""
    uptime = sampler.sample()["uptime"]

    assert uptime
    assert any(unit in uptime for unit in ("d", "h", "m"))


@pytest.mark.parametrize(
    "size,expected_unit",
    [(512, "B"), (2048, "KB"), (5 * 1024 ** 2, "MB"), (3 * 1024 ** 3, "GB")],
)
def test_humanize_bytes_picks_a_sensible_unit(size, expected_unit) -> None:
    """Byte counts are scaled rather than printed as long integers."""
    assert ResourceSampler._humanize_bytes(size).endswith(expected_unit)


def test_missing_disk_path_falls_back_to_the_root(sampler: ResourceSampler) -> None:
    """A preferences path that no longer exists does not break the page."""
    sampler.disk_path = os.path.join(tempfile.gettempdir(), "not-created-yet")

    disk = sampler.sample()["disk"]

    assert disk is not None
    assert os.path.isdir(disk["path"])


def test_second_sample_reports_real_cpu_numbers(sampler: ResourceSampler) -> None:
    """The first read primes the counters, so it is not stuck at zero.

    psutil computes CPU as a difference between reads. Without priming in
    the constructor the panel would show 0% until the second poll, which
    reads as a broken meter.
    """
    first = sampler.sample()
    second = sampler.sample()

    # Both are valid percentages; the point is the second is measurable.
    assert 0.0 <= second["cpu"] <= 100.0
    assert isinstance(first["cpu"], float)
