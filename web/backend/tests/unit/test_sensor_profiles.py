"""The variant store: profiles, their files, and who is meant to hold them.

Two bars to clear. The probe helper accepts a profile only as comments and
upper-case ``KEY=VALUE`` lines, so anything else has to be refused here, where
a field name can still be attached to the refusal. And ``./prtg-nats sensor
profile`` has to be able to deploy again what the interface wrote, which means
the same path and the same format the shell tooling has always used.
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.errors import NotFoundError, RuntimeStateError
from app.infrastructure.probe_helper.protocol import probe_profile_file_path
from app.infrastructure.runtime_files import RuntimeFileStore
from app.infrastructure.sensor_catalog import (
    ParameterField,
    SensorSchema,
    profile_parameter_line,
)


def mode_of(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def enrol(store: RuntimeFileStore, username: str, host: str) -> None:
    store.write_probe_inventory(
        nats_username=username,
        ssh_host=host,
        ssh_port=22,
        probe_id=f"{username}-id",
        access_key="k" * 32,
        probe_name=username,
    )


def test_a_profile_lands_where_the_shell_tooling_looks_for_it(
    settings: Settings,
) -> None:
    store = RuntimeFileStore(settings)
    store.write_sensor_profile(
        "wlan-auth", "standort-nord", {"SSID": "Corporate", "AUTH": "peap"}
    )

    path = settings.sensor_profile_dir / "wlan-auth" / "standort-nord.env"
    assert path.is_file()
    assert store.read_sensor_profile("wlan-auth", "standort-nord") == {
        "AUTH": "peap",
        "SSID": "Corporate",
    }


def test_the_profile_and_its_directories_are_readable_by_nobody_else(
    settings: Settings,
) -> None:
    store = RuntimeFileStore(settings)
    store.write_sensor_profile("wlan-auth", "standort-nord", {"PASSWORD": "s3cret"})

    path = settings.sensor_profile_dir / "wlan-auth" / "standort-nord.env"
    assert mode_of(path) == 0o600
    # The directory too: the file mode alone would still let anything list
    # which variants exist and for which sites.
    assert mode_of(path.parent) == 0o700
    assert mode_of(settings.sensor_profile_dir) == 0o700


def test_a_value_with_a_line_break_is_refused_before_the_probe_sees_it(
    settings: Settings,
) -> None:
    store = RuntimeFileStore(settings)
    with pytest.raises(RuntimeStateError):
        store.write_sensor_profile("wlan-auth", "nord", {"SSID": "Corp\nPASSWORD=x"})


def test_a_lower_case_key_is_refused_because_the_helper_rejects_it(
    settings: Settings,
) -> None:
    store = RuntimeFileStore(settings)
    with pytest.raises(RuntimeStateError):
        store.write_sensor_profile("wlan-auth", "nord", {"ssid": "Corporate"})


def test_the_written_file_is_comments_and_key_value_lines_only(
    settings: Settings,
) -> None:
    store = RuntimeFileStore(settings)
    store.write_sensor_profile("wlan-auth", "nord", {"SSID": "Corp", "AUTH": "psk"})

    content = store.sensor_profile_content("wlan-auth", "nord")
    for line in content.splitlines():
        assert line.startswith("#") or "=" in line
    assert "SSID=Corp" in content


def test_a_file_replaces_the_one_before_it_extension_included(
    settings: Settings,
) -> None:
    store = RuntimeFileStore(settings)
    store.write_sensor_profile_file(
        "wlan-auth", "nord", "CA_CERT", "CA_CERT.pem", b"first"
    )
    store.write_sensor_profile_file(
        "wlan-auth", "nord", "CA_CERT", "CA_CERT.crt", b"second"
    )

    files = store.list_sensor_profile_files("wlan-auth", "nord")
    # Two files for one key would leave the profile pointing at the one that
    # is no longer meant.
    assert [entry.filename for entry in files] == ["CA_CERT.crt"]
    assert store.read_sensor_profile_file("wlan-auth", "nord", "CA_CERT") == b"second"


def test_a_deployed_file_is_as_protected_as_the_profile(settings: Settings) -> None:
    store = RuntimeFileStore(settings)
    store.write_sensor_profile_file(
        "wlan-auth", "nord", "PRIVATE_KEY", "PRIVATE_KEY.pem", b"-----BEGIN"
    )
    path = (
        settings.sensor_profile_dir / "wlan-auth" / "files" / "nord" / "PRIVATE_KEY.pem"
    )
    assert mode_of(path) == 0o600
    assert mode_of(path.parent) == 0o700


def test_the_path_written_into_the_profile_is_the_one_on_the_probe() -> None:
    # What the sensor script reads out of the profile, and what the helper
    # builds from its own tokens. The two have to agree or the script gets a
    # path to a file that is not there.
    assert (
        probe_profile_file_path("wlan-auth", "nord", "CA_CERT.pem")
        == "/etc/prtg-nats/sensors/wlan-auth/files/nord/CA_CERT.pem"
    )


def test_removing_a_variant_takes_its_files_with_it(settings: Settings) -> None:
    store = RuntimeFileStore(settings)
    store.write_sensor_profile("wlan-auth", "nord", {"SSID": "Corp"})
    store.write_sensor_profile_file(
        "wlan-auth", "nord", "CA_CERT", "CA_CERT.pem", b"cert"
    )

    store.remove_sensor_profile("wlan-auth", "nord")
    assert not store.sensor_profile_exists("wlan-auth", "nord")
    assert store.list_sensor_profile_files("wlan-auth", "nord") == []


def test_an_unknown_variant_is_not_found_rather_than_empty(settings: Settings) -> None:
    store = RuntimeFileStore(settings)
    with pytest.raises(NotFoundError):
        store.read_sensor_profile("wlan-auth", "never-written")


def test_a_variant_knows_which_probes_are_meant_to_hold_it(
    settings: Settings,
) -> None:
    store = RuntimeFileStore(settings)
    enrol(store, "mpp-nord", "192.0.2.10")
    enrol(store, "mpp-sued", "192.0.2.11")
    store.write_sensor_profile("wlan-auth", "gaeste", {"SSID": "Guest"})
    store.assign_profile("mpp-nord", "wlan-auth", "gaeste")

    records = store.list_sensor_profiles("wlan-auth")
    assert [record.name for record in records] == ["gaeste"]
    # Assignment is desired state: the south site is deliberately not on it.
    assert records[0].probes == ("mpp-nord",)
    assert store.assigned_profiles("mpp-nord", "wlan-auth") == ("gaeste",)
    assert store.assigned_profiles("mpp-sued", "wlan-auth") == ()


def test_an_assignment_for_another_sensor_does_not_leak_into_this_one(
    settings: Settings,
) -> None:
    store = RuntimeFileStore(settings)
    enrol(store, "mpp-nord", "192.0.2.10")
    store.assign_profile("mpp-nord", "wlan-auth", "gaeste")
    store.assign_profile("mpp-nord", "iperf-throughput", "berlin")

    assert store.assigned_profiles("mpp-nord", "wlan-auth") == ("gaeste",)
    assert store.assigned_profiles("mpp-nord") == ("gaeste", "berlin")

    store.unassign_profile("mpp-nord", "wlan-auth", "gaeste")
    assert store.assigned_profiles("mpp-nord") == ("berlin",)


def test_the_profile_option_comes_from_the_declaration() -> None:
    """Which option selects a profile is the sensor's to say, not ours.

    Every sensor that reads one calls it --profile today. The lookup exists so
    that a sensor calling it something else stays selectable without anyone
    editing the platform, and this pins that the declaration is what decides.
    """
    profile = ParameterField(name="--profile", type="string")
    variant = ParameterField(name="--variant", type="string")
    unrelated = ParameterField(name="--server", type="string")

    assert (
        profile_parameter_line(SensorSchema(parameters=(unrelated, profile)), "berlin")
        == "--profile berlin"
    )
    assert (
        profile_parameter_line(SensorSchema(parameters=(unrelated, variant)), "berlin")
        == "--variant berlin"
    )
    # A sensor that declares neither still reads "default" on the probe, so the
    # answer is the option it would listen to rather than nothing at all.
    assert (
        profile_parameter_line(SensorSchema(parameters=(unrelated,)), "berlin")
        == "--profile berlin"
    )
    # And one with no declaration at all - iperf-throughput carries only
    # parameters, so its schema is the same shape a caller may not have.
    assert profile_parameter_line(None, "berlin") == "--profile berlin"
