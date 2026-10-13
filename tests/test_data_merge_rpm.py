import math

import pytest

from data_merge.rpm_sync import RpmPoint, estimate_rpm_time_map


def _curve(time_s):
    return (
        8000 + 1800 * math.sin(time_s * 0.19)
        + 900 * math.sin(time_s * 0.047 + 0.4)
        + (1400 if 72 < time_s < 79 else 0)
        - (1800 if 166 < time_s < 171 else 0)
    )


def test_estimates_offset_and_drift_with_500_rpm_variability():
    scale, offset = 1.0002, 4.2
    aim = [RpmPoint(i / 20, _curve(i / 20)) for i in range(4801)]
    tire = [
        RpmPoint(i / 16, _curve(scale * (i / 16) + offset) + 350)
        for i in range(3601)
    ]

    report = estimate_rpm_time_map(aim, tire)

    assert report.time_map.scale == pytest.approx(scale, abs=.0003)
    assert report.time_map.offset_s == pytest.approx(offset, abs=.08)
    assert report.match_fraction > .95
    assert report.rpm_tolerance_rpm == 500


def test_absolute_tolerance_rejects_rpm_difference_over_500():
    aim = [RpmPoint(i / 20, _curve(i / 20)) for i in range(3001)]
    tire = [RpmPoint(i / 20, _curve(i / 20 + 3) + 750)
            for i in range(2801)]

    with pytest.raises(ValueError, match="RPM"):
        estimate_rpm_time_map(aim, tire, rpm_tolerance_rpm=500)
