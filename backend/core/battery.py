"""battery.py - Simulated state of charge. Spec gives no formula; numbers are editable.

SoC never changes the official grid mode.
Python 3.9 compatible (no dataclasses.kw_only).
"""


class BatteryParams(object):
    def __init__(
        self,
        capacity_kwh=2000.0,
        soc_start=0.70,
        soc_min=0.10,
        soc_max=0.95,
        p_discharge_max_kw=300.0,
        p_charge_max_kw=150.0,
        eff_charge=0.95,
        eff_discharge=0.95,
        pv_kwp=150.0,
        step_h=1.0,
    ):
        self.capacity_kwh = float(capacity_kwh)
        self.soc_start = float(soc_start)
        self.soc_min = float(soc_min)
        self.soc_max = float(soc_max)
        self.p_discharge_max_kw = float(p_discharge_max_kw)
        self.p_charge_max_kw = float(p_charge_max_kw)
        self.eff_charge = float(eff_charge)
        self.eff_discharge = float(eff_discharge)
        self.pv_kwp = float(pv_kwp)
        self.step_h = float(step_h)


def simulate(rows, solar, p=None):
    """rows: controller.evaluate output; solar: W/m2 per row."""
    if p is None:
        p = BatteryParams()
    e = p.soc_start * p.capacity_kwh
    out = []
    for r, g in zip(rows, solar):
        pv = p.pv_kwp * max(0.0, float(g)) / 1000.0
        d = r["demand_kw"]
        net = max(0.0, d - pv)
        surplus = max(0.0, pv - d)
        batt_to_load = 0.0
        grid_charge = 0.0
        pv_charge = 0.0
        room_kw = max(0.0, (p.soc_max * p.capacity_kwh - e) / (p.eff_charge * p.step_h))
        if r["mode"] == "RENEWABLE_STORAGE":
            avail_kw = max(0.0, (e - p.soc_min * p.capacity_kwh) * p.eff_discharge / p.step_h)
            batt_to_load = min(net, p.p_discharge_max_kw, avail_kw)
            e -= batt_to_load * p.step_h / p.eff_discharge
            pv_charge = min(surplus, p.p_charge_max_kw, room_kw)
        else:
            pv_charge = min(surplus, p.p_charge_max_kw, room_kw)
            grid_charge = max(0.0, min(p.p_charge_max_kw - pv_charge, room_kw - pv_charge))
        e += (pv_charge + grid_charge) * p.step_h * p.eff_charge
        shortfall = max(0.0, net - batt_to_load) if r["mode"] == "RENEWABLE_STORAGE" else 0.0
        soc = e / p.capacity_kwh
        rec = dict(r)
        rec.update(
            {
                "pv_kw": round(pv, 1),
                "soc_pct": round(100.0 * soc, 1),
                "battery_to_load_kw": round(batt_to_load, 1),
                "grid_charge_kw": round(grid_charge, 1),
                "pv_charge_kw": round(pv_charge, 1),
                "shortfall_kw": round(shortfall, 1),
                "low_soc_warning": bool(soc <= p.soc_min + 0.02),
            }
        )
        out.append(rec)
    return out
