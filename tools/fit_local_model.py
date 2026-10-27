"""Local linear-model check. Expect RMSE about 8.5 and coefficients from the plan."""
from __future__ import print_function

import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np
import pandas as pd

from features import FEATURE_COLS, build_features, clean_inputs

PLAN_COEF = {
    "intercept": 143.82,
    "b_night": -53.91,
    "b_ramp6": -11.00,
    "b_morning": 29.90,
    "b_evening": 54.23,
    "b_h23": -33.86,
    "cdd": 8.59,
}


def ols_fit(X, y):
    xtx = X.T.dot(X)
    xty = X.T.dot(y)
    beta = np.linalg.solve(xtx, xty)
    return beta


def rmse(y, yhat):
    return float(np.sqrt(np.mean((y - yhat) ** 2)))


def main():
    train = pd.read_csv(os.path.join(ROOT, "train_telemetry.csv"))
    train, _ = clean_inputs(train, max_rows=None)
    feats = build_features(train)
    y = train["target_demand_kw"].astype(float).values
    X = np.column_stack([np.ones(len(feats)), feats[FEATURE_COLS].values])
    beta = ols_fit(X, y)
    names = ["intercept"] + FEATURE_COLS
    coef = dict((n, round(float(b), 4)) for n, b in zip(names, beta))
    yhat = X.dot(beta)
    in_rmse = rmse(y, yhat)

    last800 = slice(-800, None)
    Xtr, ytr = X[:-800], y[:-800]
    beta800 = ols_fit(Xtr, ytr)
    hold_rmse = rmse(y[last800], X[last800].dot(beta800))

    cool = train["ambient_temp_c"] <= 28.0
    hot = train["ambient_temp_c"] > 28.0
    beta_heat = ols_fit(X[cool.values], y[cool.values])
    heat_rmse = rmse(y[hot.values], X[hot.values].dot(beta_heat))

    plan_vec = np.array([PLAN_COEF[n] for n in names], dtype=float)
    plan_rmse = rmse(y, X.dot(plan_vec))

    report = {
        "n_train": int(len(train)),
        "in_sample_rmse": round(in_rmse, 3),
        "plan_coef_rmse": round(plan_rmse, 3),
        "holdout_last800_rmse": round(hold_rmse, 3),
        "heat_stress_rmse": round(heat_rmse, 3),
        "fitted_coefficients": coef,
        "plan_coefficients": PLAN_COEF,
        "r2": round(1.0 - float(np.sum((y - yhat) ** 2) / np.sum((y - y.mean()) ** 2)), 4),
        "pass_rmse_le_9": in_rmse <= 9.0,
    }
    out_path = os.path.join(ROOT, "docs", "local_model_metrics.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2)
    print(json.dumps(report, indent=2))
    if in_rmse > 9.0:
        sys.exit("FAIL: in-sample RMSE %.3f > 9.0" % in_rmse)
    print("PASS local linear model")


if __name__ == "__main__":
    main()
