"""Build frontend/presets.json from train + sample CSVs."""
import json
import os

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COLS = ["timestamp", "ambient_temp_c", "humidity_pct", "solar_irradiance_wm2"]


def recs(df):
    return json.loads(df[COLS].to_json(orient="records"))


def main():
    sample = pd.read_csv(os.path.join(ROOT, "sample_test_input.csv"))
    train = pd.read_csv(os.path.join(ROOT, "train_telemetry.csv"))
    heat = sample.iloc[:24]
    winter = train.iloc[200:224]
    spring = train.iloc[1800:1824]
    presets = {
        "presets": [
            {
                "id": "heatwave",
                "label": "Heat wave (sample test, 24 h)",
                "rows": recs(heat),
            },
            {
                "id": "winter",
                "label": "Winter day (train rows 200-223)",
                "rows": recs(winter),
            },
            {
                "id": "spring",
                "label": "Mild spring day (train)",
                "rows": recs(spring),
            },
        ]
    }
    out = os.path.join(ROOT, "frontend", "presets.json")
    with open(out, "w") as f:
        json.dump(presets, f, indent=2)
    print("wrote", out, "heat", len(heat), "winter", len(winter), "spring", len(spring))


if __name__ == "__main__":
    main()
