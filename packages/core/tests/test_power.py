"""Reading battery power on Linux and macOS (`cherrypick.core.power`).

The power watch alerts every channel while the machine runs on battery (2026-10-08 outage). Windows
was the first cut; these pin the Linux sysfs and macOS pmset readings, including the cases that must
never alert (a desktop or server with no battery) and the one that must never read as plugged in (no
power information at all).
"""

from __future__ import annotations

from cherrypick.core import power


def _supply(root, name, **files):
    d = root / name
    d.mkdir(parents=True)
    for k, v in files.items():
        (d / k).write_text(f"{v}\n", encoding="utf-8")


def test_a_laptop_on_ac_is_not_on_battery(tmp_path):
    _supply(tmp_path, "AC", type="Mains", online=1)
    _supply(tmp_path, "BAT0", type="Battery", status="Charging", capacity=88)
    assert power.read_linux(str(tmp_path)) == {
        "known": True,
        "on_battery": False,
        "percent": 88,
        "seconds_left": None,
    }


def test_a_laptop_unplugged_is_on_battery_with_time_left(tmp_path):
    _supply(tmp_path, "AC", type="Mains", online=0)
    _supply(
        tmp_path,
        "BAT0",
        type="Battery",
        status="Discharging",
        capacity=64,
        energy_now=30_000_000,
        power_now=10_000_000,
    )
    assert power.read_linux(str(tmp_path)) == {
        "known": True,
        "on_battery": True,
        "percent": 64,
        "seconds_left": 3 * 3600,
    }


def test_no_mains_entry_falls_back_to_the_battery_status(tmp_path):
    _supply(
        tmp_path, "BAT1", type="Battery", status="Discharging", capacity=40, charge_now=2000, current_now=1000
    )
    out = power.read_linux(str(tmp_path))
    assert out["on_battery"] is True and out["seconds_left"] == 2 * 3600


def test_a_desktop_or_server_with_no_battery_never_alerts(tmp_path):
    _supply(tmp_path, "ucsi-source-psy-1", type="USB", online=1)
    assert power.read_linux(str(tmp_path))["on_battery"] is False
    empty = tmp_path / "server"
    empty.mkdir()
    _supply(empty, "hidpp_battery_0", type="Battery", scope="Device", status="Discharging", capacity=20)
    assert power.read_linux(str(empty)) == {
        "known": True,
        "on_battery": False,
        "percent": None,
        "seconds_left": None,
    }


def test_no_power_information_is_cannot_tell(tmp_path):
    assert power.read_linux(str(tmp_path / "absent")) == power.UNKNOWN
    (tmp_path / "empty").mkdir()
    assert power.read_linux(str(tmp_path / "empty"))["known"] is False


def test_macos_pmset_on_battery_and_on_ac():
    on_battery = (
        "Now drawing from 'Battery Power'\n"
        " -InternalBattery-0 (id=4653155)\t64%; discharging; 3:12 remaining present: true\n"
    )
    assert power.parse_pmset(on_battery) == {
        "known": True,
        "on_battery": True,
        "percent": 64,
        "seconds_left": 3 * 3600 + 12 * 60,
    }
    on_ac = (
        "Now drawing from 'AC Power'\n"
        " -InternalBattery-0 (id=1)\t100%; charged; 0:00 remaining present: true\n"
    )
    assert power.parse_pmset(on_ac)["on_battery"] is False
    assert power.parse_pmset("")["known"] is False
