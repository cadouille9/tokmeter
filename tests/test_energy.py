from tokmeter import energy


def test_merged_seconds_disjoint():
    assert energy.merged_seconds([(0, 10), (20, 30)]) == 20


def test_merged_seconds_overlapping_and_nested():
    # (0,10) and (5,15) overlap -> 15; (16,20) nested in (16,20) stays 4
    assert energy.merged_seconds([(0, 10), (5, 15), (16, 20), (17, 19)]) == 19


def test_merged_seconds_adjacent_touching():
    # touching endpoints merge without double counting
    assert energy.merged_seconds([(0, 10), (10, 20)]) == 20


def test_merged_seconds_empty():
    assert energy.merged_seconds([]) == 0


def test_intervals_from_rows_ts_is_end():
    # ts is the request END; a 2000ms request ending at :10 spans :08 -> :10
    rows = [("m", "2026-07-07T12:00:10+00:00", 2000)]
    ivals = energy.intervals_from_rows(rows)
    (start, end), = ivals["m"]
    assert end - start == 2.0


def test_intervals_from_rows_drops_zero_and_none_duration():
    rows = [
        ("m", "2026-07-07T12:00:10+00:00", 0),
        ("m", "2026-07-07T12:00:11+00:00", None),
    ]
    assert energy.intervals_from_rows(rows) == {}


def test_energy_kwh_merges_within_model_adds_across_models():
    # model a: two fully-overlapping 1h requests at 100W -> 1h, 0.1 kWh
    # model b: one 1h request at 300W, concurrent with a -> adds 0.3 kWh
    rows = [
        ("a", "2026-07-07T13:00:00+00:00", 3_600_000),
        ("a", "2026-07-07T13:00:00+00:00", 3_600_000),
        ("b", "2026-07-07T13:00:00+00:00", 3_600_000),
    ]
    hours, kwh = energy.energy_kwh(rows, watts_for=lambda m: {"a": 100, "b": 300}[m])
    assert hours == 2.0  # per-model active hours summed
    assert abs(kwh - 0.4) < 1e-9
