"""The probe service and the platform agree on the return channel's limits.

The service runs on the probe as a heredoc inside the helper (ADR 0006: only
the helper is signed), so it cannot import anything from the backend; the
limits it enforces are written down twice. These tests read the service out
of the helper exactly as a probe gets it and compare the copies, so a change
on one side fails here instead of as records a probe refuses or the platform
rejects.
"""

from __future__ import annotations

import importlib.util
import re
from types import ModuleType

import pytest

from app.api.schemas.sensor_logs import MAX_BATCH_RECORDS, SensorLogRecordIn
from app.core.config import Settings
from app.services.return_channel import GAP_SENSOR, HEARTBEAT_INTERVAL
from app.services.sensor_logs import SENSOR_NAME_PATTERN
from tests.conftest import REPO_ROOT

HELPER = REPO_ROOT / "libexec" / "prtg-nats-probe-helper"
WLAN_AUTH = REPO_ROOT / "sensors" / "wlan-auth" / "privileged" / "prtg-sensor-wlan-auth"


@pytest.fixture(scope="module")
def service(tmp_path_factory: pytest.TempPathFactory) -> ModuleType:
    lines = HELPER.read_text(encoding="utf-8").splitlines(keepends=True)
    start = next(
        index
        for index, line in enumerate(lines)
        if line.rstrip().endswith("<<'CHANNELEOF'")
    )
    end = next(
        index
        for index in range(start + 1, len(lines))
        if lines[index].rstrip("\n") == "CHANNELEOF"
    )
    path = tmp_path_factory.mktemp("channel") / "prtg_nats_return_channel.py"
    path.write_text("".join(lines[start + 1 : end]), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("prtg_nats_return_channel", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _max_length(field: str) -> int:
    for item in SensorLogRecordIn.model_fields[field].metadata:
        if hasattr(item, "max_length"):
            return int(item.max_length)
    raise AssertionError(f"{field} has no length limit")


def test_both_sides_take_the_same_sensor_names(service: ModuleType) -> None:
    assert service.SENSOR_PATTERN.pattern == SENSOR_NAME_PATTERN.pattern


def test_both_sides_cut_the_fields_at_the_same_length(service: ModuleType) -> None:
    assert _max_length("job") == service.JOB_LENGTH
    assert _max_length("code") == service.CODE_LENGTH
    assert _max_length("summary") == service.SUMMARY_LENGTH


def test_a_full_batch_from_the_probe_is_one_the_platform_takes(
    service: ModuleType,
) -> None:
    assert service.BATCH_RECORDS == MAX_BATCH_RECORDS


def test_gap_records_carry_the_name_the_platform_counts(service: ModuleType) -> None:
    assert service.SELF_SENSOR == GAP_SENSOR


def test_the_heartbeat_the_silence_threshold_is_built_on(service: ModuleType) -> None:
    assert HEARTBEAT_INTERVAL.total_seconds() == service.DEFAULT_HEARTBEAT
    # Three missed heartbeats by default, so one late request is no alarm.
    default = Settings.model_fields["probe_channel_silence_minutes"].default
    assert default * 60 == 3 * service.DEFAULT_HEARTBEAT


def test_the_default_buffer_is_the_one_the_platform_sets(service: ModuleType) -> None:
    default = Settings.model_fields["probe_channel_buffer_mb"].default
    assert default * 1024 * 1024 == service.DEFAULT_LIMIT


def test_first_key_and_renewal_make_the_same_kind_of_key() -> None:
    """channel-csr in the helper and the renewal in the service both make an
    EC P-256 key under the same placeholder subject - the kind of CSR the
    signing tests feed the CA (tests/conftest.py probe_csr). The subject is
    ignored either way: the certificate is named after the probe identity."""
    source = HELPER.read_text(encoding="utf-8")
    assert source.count("ec_paramgen_curve:P-256") == 2
    assert source.count("/CN=prtg-nats-probe") == 2


def test_the_wlan_auth_helper_writes_to_the_service_socket(
    service: ModuleType,
) -> None:
    source = WLAN_AUTH.read_text(encoding="utf-8")
    socket = re.search(r'^RETURN_CHANNEL_SOCKET = "([^"]+)"', source, re.MULTILINE)
    assert socket is not None
    assert socket.group(1) == service.SOCKET_PATH
    config = re.search(r'^RETURN_CHANNEL_CONFIG = "([^"]+)"', source, re.MULTILINE)
    assert config is not None
    assert config.group(1) == service.CONFIG_PATH
