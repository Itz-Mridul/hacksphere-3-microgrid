"""Feature + ingest checks."""
import pandas as pd

from features import FEATURE_COLS, build_features, clean_inputs


def test_known_feature_row():
    df = pd.DataFrame(
        [
            {
                "timestamp": "2015-06-16 18:00:00",
                "ambient_temp_c": 35.4,
                "humidity_pct": 50,
                "solar_irradiance_wm2": 10,
            }
        ]
    )
    f = build_features(df).iloc[0]
    assert list(build_features(df).columns) == FEATURE_COLS
    assert f["b_evening"] == 1.0
    assert abs(f["cdd"] - 11.4) < 1e-9
    assert f["b_night"] == 0.0


def test_clean_clips_and_fills():
    df = pd.DataFrame(
        [
            {
                "timestamp": "2015-06-16 15:00:00",
                "ambient_temp_c": 999,
                "humidity_pct": "abc",
                "solar_irradiance_wm2": 10,
            },
            {
                "timestamp": "2015-06-16 16:00:00",
                "ambient_temp_c": 30,
                "humidity_pct": 50,
                "solar_irradiance_wm2": 10,
            },
        ]
    )
    out, rep = clean_inputs(df)
    assert len(out) == 2
    assert out["ambient_temp_c"].max() <= 45
    assert "humidity_pct" in rep["filled"]
    assert "ambient_temp_c" in rep["clipped"]


def test_header_case():
    df = pd.DataFrame(
        [
            {
                " Timestamp ": "2015-06-16 15:00:00",
                "AMBIENT_TEMP_C": 20,
                "humidity_pct": 40,
                "solar_irradiance_wm2": 0,
            }
        ]
    )
    out, _ = clean_inputs(df)
    assert "ambient_temp_c" in out.columns


if __name__ == "__main__":
    test_known_feature_row()
    test_clean_clips_and_fills()
    test_header_case()
    print("PASS features")
