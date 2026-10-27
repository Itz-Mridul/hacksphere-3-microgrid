# HackSphere 3.0 — Smart Energy Microgrid & Demand Forecasting Engine

Hourly demand forecast (linear model: hour-of-day blocks + cooling hinge at 24 °C), PDF section-6 grid transition controller, battery SoC simulation, and a live dashboard.

**Live Catalyst URL:** https://microgrid-api-50047163722.development.catalystappsail.in

The service record exists in Catalyst, but the previously uploaded revision reported an
AppSail startup/port error. Redeploy the refreshed `backend-appsail.zip` before treating the
URL as operational.

## What works today (local)

- Shared features, controller, battery, `predict.py` CLI, `test_eval.py`
- Flask API: `/api/forecast`, `/api/predict`, `/api/upload`, `/api/presets`, `/api/history`, `/api/health`
- Dashboard: presets, CSV upload + cleaning report, charts, grouped overload events, SoC, logs, history
- AppSail proxy configured as the default endpoint for `predict.py`; responses report `source=quickml`, `cache`, `mixed`, or the flagged local `fallback`

## Architecture

| Piece | Role |
| --- | --- |
| `features.py` | Clean + 6 numeric features used everywhere |
| `predict.py` | Judge contract → QuickML or fallback |
| Flask AppSail (`backend/`) | Forecast, controller, SoC, cache, SQLite |
| `frontend/` | Single-page dashboard (Chart.js) |
| Zoho QuickML | Published Linear Regression endpoint called through the AppSail proxy |

## Model (short)

`demand ≈ 143.82 + hour-block offsets + 8.59 × max(0, temp−24)`

Humidity/solar/weekday/lags add nothing on this synthetic set. Trees lose in the hotter test period. Expected RMSE floor ≈ **8.5 kW**.

## Controller (PDF §6)

- **Renewable Storage** if demand **> 160 kW** OR hour in **18–22** (inclusive)
- **Main Grid** otherwise
- **Critical Overload** if demand **≥ 200 kW** (independent of mode)
- Battery SoC is advisory only. It never changes the official mode.

Battery defaults (assumptions, not in the PDF): 2000 kWh, start SoC 70%, 10–95% limits, 300 kW discharge, 150 kW charge, 95% efficiency, PV = 150 kWp × irradiance/1000.

## Run locally

```bash
python -m pip install -r requirements.txt
python tools/make_train_features.py
python tools/fit_local_model.py
python tools/build_presets.py
python test_controller.py
python test_features.py
python predict.py --input sample_test_input.csv --output predictions.csv
python test_eval.py
python backend/main.py
```

Open http://127.0.0.1:8080

Messy CSV for the demo: `docs/messy_sample.csv`.

## Environment variable names (values stay in `.env`, never in git)

See `.env.example`. Names: `QUICKML_ENDPOINT_URL`, `QUICKML_AUTH_MODE`, `QUICKML_ENDPOINT_KEY`, `CATALYST_ORG`, `QUICKML_ENV`, `ZOHO_CLIENT_ID`, `ZOHO_CLIENT_SECRET`, `ZOHO_REFRESH_TOKEN`, `ZOHO_ACCOUNTS_URL`, `QUICKML_PAYLOAD_MODE`, `QUICKML_STRICT`, `CORS_ALLOW_ORIGIN`, `MAX_ROWS`, `DAILY_QUICKML_BUDGET`.

Honesty rule: every response shows `source` = `quickml` | `cache` | `mixed` | `fallback`.

## Catalyst deploy

1. Catalyst project in your data centre; AppSail Python + optional Slate
2. QuickML dataset = `train_features.csv`; Linear Regression; publish endpoint
3. Set env vars on AppSail; listen on `X_ZOHO_CATALYST_LISTEN_PORT` (already in `backend/main.py`)
4. The checked-in `predict.py` defaults to the public `/api/predict` proxy (judges need no Zoho secrets); override it with `QUICKML_ENDPOINT_URL` when needed
5. Incognito + phone-data check of the public URL

## Limits

- Free QuickML: 500 prediction calls / month — cache + 24-row tests
- Fallback exists if the endpoint is down; a STRICT run (`QUICKML_STRICT=1`) disables it
- No Stratus / Catalyst Data Store / Job Scheduling yet (local SQLite instead)
