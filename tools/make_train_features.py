"""Build the QuickML training file: 6 features + target."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pandas as pd
from features import build_features

df = pd.read_csv(os.path.join(ROOT, "train_telemetry.csv"))
out = build_features(df)
out["target_demand_kw"] = df["target_demand_kw"].values
path = os.path.join(ROOT, "train_features.csv")
out.to_csv(path, index=False)
print("wrote", path, out.shape, "columns:", list(out.columns))
