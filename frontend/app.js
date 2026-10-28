(function () {
  var API = "";
  var last = null;
  var liveTimer = null;
  var liveOffset = 0;
  var chartLoad = null;
  var chartSoc = null;
  var chartCost = null;
  var presets = [];

  function hourFromTs(ts) {
    var s = String(ts || "");
    if (s.length >= 13) return s.slice(11, 13);
    return "--";
  }

  function prettyTs(ts) {
    var s = String(ts || "").replace("T", " ");
    if (s.length < 16) return s;
    var months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
    var m = parseInt(s.slice(5, 7), 10);
    var d = s.slice(8, 10);
    var hm = s.slice(11, 16);
    var mon = months[m - 1] || s.slice(5, 7);
    return d + " " + mon + " " + hm;
  }

  function sourceClass(src) {
    if (src === "quickml") return "green";
    if (src === "cache" || src === "mixed") return "blue";
    return "amber";
  }

  function setPills(health, model) {
    var src = (model && model.source) || (health && health.quickml_configured ? "quickml" : "fallback");
    var srcPill = document.getElementById("src-pill");
    srcPill.innerHTML = '<span class="material-symbols-outlined" style="font-size:15px;">memory</span> Model: ' + (model && model.version ? model.version + " / " : "") + src;
    srcPill.className = "pill " + sourceClass(src);
    if (model) {
      document.getElementById("run-pill").innerHTML = '<span class="material-symbols-outlined" style="font-size:15px;">speed</span> Latency: ' + (model.latency_ms || 0) + " ms";
    }
    var used = health ? health.quickml_calls_today : 0;
    var budget = health ? health.daily_budget : 300;
    document.getElementById("call-pill").innerHTML = '<span class="material-symbols-outlined" style="font-size:15px;">analytics</span> API Quota: ' + used + "/" + budget;
  }

  function modeLabel(mode) {
    return mode === "MAIN_GRID" ? "MAIN GRID" : "RENEWABLE STORAGE";
  }

  function renderHeadline(data) {
    var empty = document.getElementById("empty-state");
    var box = document.getElementById("headline");
    empty.hidden = true;
    box.hidden = false;
    var first = data.rows[0];
    var el = document.getElementById("first-mode");
    el.textContent = modeLabel(first.mode);
    el.className = "state " + first.mode;
    var k = data.kpis;
    document.getElementById("kpi-peak").textContent = k.peak_kw + " kW at " + prettyTs(k.peak_at);
    document.getElementById("kpi-ov").textContent = k.overload_hours + " h in " + k.overload_events + " event(s)";
    document.getElementById("kpi-soc").textContent = k.min_soc_pct + "% at " + prettyTs(k.min_soc_at);
    document.getElementById("kpi-sw").textContent = String(k.mode_switches);
  }

  function renderBanner(events) {
    var el = document.getElementById("banner");
    if (!events || !events.length) {
      el.hidden = true;
      el.textContent = "";
      return;
    }
    var top = events.slice().sort(function (a, b) { return b.peak_kw - a.peak_kw; });
    var shown = top.slice(0, 3);
    var extra = top.length - shown.length;
    var parts = shown.map(function (e) {
      return "⚠️ LEVEL 3 CONTINGENCY: Critical Overload Active " + prettyTs(e.start) + "–" + hourFromTs(e.end) + ":00 (" + e.hours + " hrs, Peak " + e.peak_kw.toFixed(1) + " kW ≥ 200 kW Limit)";
    });
    if (extra > 0) parts.push("+" + extra + " more");
    el.textContent = parts.join("  ·  ");
    el.hidden = false;
  }

  function renderTimeline(rows) {
    var box = document.getElementById("timeline");
    box.innerHTML = "";
    var view = rows.slice(0, 24);
    view.forEach(function (r) {
      var d = document.createElement("div");
      d.className = "cell " + r.mode + (r.critical_overload ? " ov" : "");
      d.title = prettyTs(r.timestamp) + " " + r.demand_kw + " kW";
      d.textContent = hourFromTs(r.timestamp);
      box.appendChild(d);
    });
  }

  function chartColors(rows) {
    return rows.map(function (r) {
      return r.critical_overload ? "#DC2626" : (r.mode === "MAIN_GRID" ? "#0F172A" : "#16A34A");
    });
  }

  function drawLoad(rows) {
    var ctx = document.getElementById("chart-load");
    var labels = rows.map(function (r) { return hourFromTs(r.timestamp); });
    var y = rows.map(function (r) { return r.demand_kw; });
    var lo = rows.map(function (r) { return r.p95_low; });
    var hi = rows.map(function (r) { return r.p95_high; });
    if (chartLoad) chartLoad.destroy();
    chartLoad = new Chart(ctx, {
      type: "line",
      data: {
        labels: labels,
        datasets: [
          { label: "95% Upper CI", data: hi, borderColor: "rgba(22, 163, 74, 0.25)", backgroundColor: "rgba(22, 163, 74, 0.05)", pointRadius: 0, fill: "+1" },
          { label: "95% Lower CI", data: lo, borderColor: "rgba(22, 163, 74, 0.25)", pointRadius: 0, fill: false },
          {
            label: "Predicted Demand (kW)",
            data: y,
            borderColor: "#16A34A",
            backgroundColor: "rgba(22, 163, 74, 0.12)",
            pointBackgroundColor: chartColors(rows),
            pointBorderColor: "#FFFFFF",
            pointBorderWidth: 2,
            pointRadius: 5,
            tension: 0.2,
            fill: true
          }
        ]
      },
      options: {
        responsive: true,
        plugins: {
          legend: { labels: { color: "#14532D", font: { family: "'Inter', sans-serif", weight: "600" } } }
        },
        scales: {
          x: { ticks: { color: "#166534", font: { family: "'JetBrains Mono', monospace" } }, grid: { color: "rgba(209, 250, 229, 0.7)" } },
          y: { ticks: { color: "#166534", font: { family: "'JetBrains Mono', monospace" } }, grid: { color: "rgba(209, 250, 229, 0.7)" } }
        }
      }
    });
  }

  function drawSoc(rows) {
    var ctx = document.getElementById("chart-soc");
    var labels = rows.map(function (r) { return hourFromTs(r.timestamp); });
    if (chartSoc) chartSoc.destroy();
    chartSoc = new Chart(ctx, {
      type: "bar",
      data: {
        labels: labels,
        datasets: [
          {
            type: "line",
            label: "Battery SoC %",
            data: rows.map(function (r) { return r.soc_pct; }),
            borderColor: "#CA8A04",
            backgroundColor: "rgba(250, 204, 21, 0.15)",
            borderWidth: 2.5,
            pointBackgroundColor: "#FACC15",
            pointBorderColor: "#0F172A",
            pointRadius: 4,
            tension: 0.2,
            yAxisID: "y"
          },
          {
            label: "Battery to Load (kW)",
            data: rows.map(function (r) { return r.battery_to_load_kw; }),
            backgroundColor: "rgba(22, 163, 74, 0.35)",
            borderColor: "#16A34A",
            borderWidth: 1.5,
            borderRadius: 6,
            yAxisID: "y1"
          }
        ]
      },
      options: {
        responsive: true,
        plugins: { legend: { labels: { color: "#14532D", font: { family: "'Inter', sans-serif", weight: "600" } } } },
        scales: {
          x: { ticks: { color: "#166534", font: { family: "'JetBrains Mono', monospace" } }, grid: { color: "rgba(209, 250, 229, 0.7)" } },
          y: { min: 0, max: 100, ticks: { color: "#166534", font: { family: "'JetBrains Mono', monospace" } }, grid: { color: "rgba(209, 250, 229, 0.7)" } },
          y1: { position: "right", ticks: { color: "#166534", font: { family: "'JetBrains Mono', monospace" } }, grid: { display: false } }
        }
      }
    });
  }

  function renderTable(rows) {
    var tb = document.querySelector("#tbl tbody");
    tb.innerHTML = "";
    rows.forEach(function (r) {
      var tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" + prettyTs(r.timestamp) + "</td>" +
        "<td>" + r.ambient_temp_c + "</td>" +
        "<td>" + r.demand_kw + "</td>" +
        "<td>" + modeLabel(r.mode) + "</td>" +
        "<td>" + (r.critical_overload ? "YES" : "") + "</td>" +
        "<td>" + r.soc_pct + "%</td>" +
        "<td>" + r.reason + "</td>";
      tb.appendChild(tr);
    });
  }

  function renderAlerts(alerts, summary) {
    var empty = document.getElementById("alerts-empty");
    var container = document.getElementById("alerts-container");
    var badge = document.getElementById("alerts-count-badge");
    if (!container) return;

    if (empty) empty.hidden = true;
    container.hidden = false;
    container.innerHTML = "";

    var count = alerts ? alerts.length : 0;
    if (badge) {
      badge.hidden = false;
      var hasCrit = alerts && alerts.some(function (a) { return a.severity === "critical"; });
      badge.textContent = count === 0 ? "0 risks" : (count + (count === 1 ? " alert" : " alerts"));
      badge.className = "badge-tag " + (count === 0 ? "badge-info" : (hasCrit ? "badge-critical" : "badge-warning"));
    }

    if (!alerts || alerts.length === 0) {
      var okDiv = document.createElement("div");
      okDiv.className = "alert-item info alert-clear";
      okDiv.innerHTML =
        '<div class="alert-item-header">' +
          '<span class="alert-badge info">INFO</span>' +
          '<span class="alert-time">Next 24 Hours</span>' +
          '<span class="alert-val">Nominal Grid</span>' +
        '</div>' +
        '<div class="alert-msg">No risks detected in next 24 hours. Grid load remains within operational limits.</div>' +
        '<div class="alert-action">' +
          '<span class="material-symbols-outlined">verified</span>' +
          '<span><strong>Suggested Action:</strong> Maintain current storage dispatch profile</span>' +
        '</div>';
      container.appendChild(okDiv);
      return;
    }

    alerts.forEach(function (a) {
      var item = document.createElement("div");
      var sev = a.severity || "warning";
      item.className = "alert-item " + sev;
      var icon = sev === "critical" ? "bolt" : "lightbulb";

      item.innerHTML =
        '<div class="alert-item-header">' +
          '<span class="alert-badge ' + sev + '">' + sev.toUpperCase() + '</span>' +
          '<span class="alert-time">' + (a.time || "--") + '</span>' +
          (a.predicted_val ? '<span class="alert-val">' + a.predicted_val + '</span>' : '') +
        '</div>' +
        '<div class="alert-msg">' + (a.message || "") + '</div>' +
        '<div class="alert-action">' +
          '<span class="material-symbols-outlined">' + icon + '</span>' +
          '<span><strong>Suggested Action:</strong> ' + (a.action || "Shift loads") + '</span>' +
        '</div>';
      container.appendChild(item);
    });
  }

  function drawCost(cc) {
    var ctx = document.getElementById("chart-cost");
    if (!ctx || !cc) return;
    if (chartCost) chartCost.destroy();

    chartCost = new Chart(ctx, {
      type: "bar",
      data: {
        labels: ["Baseline (Grid-only)", "Smart Dispatch"],
        datasets: [{
          label: "Electricity Cost (₹)",
          data: [cc.baseline_cost_inr || 0, cc.smart_cost_inr || 0],
          backgroundColor: ["#0F172A", "#16A34A"],
          borderColor: ["#0F172A", "#15803D"],
          borderWidth: 1.5,
          borderRadius: 6,
          barPercentage: 0.55
        }]
      },
      options: {
        indexAxis: "y",
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: function (ctx) {
                var v = Number(ctx.raw || 0).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
                return "Cost: ₹ " + v;
              }
            }
          }
        },
        scales: {
          x: {
            ticks: {
              color: "#166534",
              font: { family: "'JetBrains Mono', monospace" },
              callback: function (val) { return "₹" + Number(val).toLocaleString("en-IN"); }
            },
            grid: { color: "rgba(209, 250, 229, 0.7)" }
          },
          y: {
            ticks: {
              color: "#0F172A",
              font: { family: "'Inter', sans-serif", weight: "600" }
            },
            grid: { display: false }
          }
        }
      }
    });
  }

  function renderCostCarbon(cc) {
    var empty = document.getElementById("cost-empty");
    var content = document.getElementById("cost-content");
    if (!content || !cc) return;

    if (empty) empty.hidden = true;
    content.hidden = false;

    var costSavedEl = document.getElementById("kpi-cost-saved");
    var savingsPctEl = document.getElementById("kpi-savings-pct");
    var co2AvoidedEl = document.getElementById("kpi-co2-avoided");
    var renewShareEl = document.getElementById("kpi-renew-share");

    if (costSavedEl) costSavedEl.textContent = "₹ " + Number(cc.savings_inr || 0).toLocaleString("en-IN", { minimumFractionDigits: 0, maximumFractionDigits: 0 });
    if (savingsPctEl) savingsPctEl.textContent = (cc.savings_pct || 0) + "%";
    if (co2AvoidedEl) co2AvoidedEl.textContent = Number(cc.co2_avoided_kg || 0).toFixed(1) + " kg";
    if (renewShareEl) renewShareEl.textContent = (cc.renewable_share_pct || 0) + "%";

    drawCost(cc);
  }

  function renderLogs(data) {
    var lines = [];
    var q = data.quality || {};
    lines.push("INGEST rows=" + q.rows + " filled=" + JSON.stringify(q.filled || {}) + " clipped=" + JSON.stringify(q.clipped || {}) + " bad_ts=" + (q.bad_timestamp || 0));
    (q.warnings || []).forEach(function (w) { lines.push("WARN " + w); });
    var m = data.model || {};
    lines.push("MODEL source=" + m.source + " latency=" + m.latency_ms + "ms calls=" + m.quickml_calls + " cache_hits=" + m.cache_hits);
    if (data.alert_summary) {
      lines.push(data.alert_summary);
    }
    if (data.cost_carbon) {
      var cc = data.cost_carbon;
      lines.push("COST & CARBON: Baseline ₹" + cc.baseline_cost_inr.toFixed(0) + " -> Smart ₹" + cc.smart_cost_inr.toFixed(0) + " | Saved ₹" + cc.savings_inr.toFixed(0) + " (" + cc.savings_pct + "%) | Avoided " + cc.co2_avoided_kg.toFixed(1) + "kg CO2 | Renewable " + cc.renewable_share_pct + "%");
    }
    (data.transitions || []).forEach(function (t) { lines.push(t.message); });
    document.getElementById("logs").textContent = lines.join("\n");
  }

  function renderAll(data) {
    last = data;
    renderHeadline(data);
    renderBanner(data.events);
    renderAlerts(data.alerts, data.alert_summary);
    renderCostCarbon(data.cost_carbon);
    renderTimeline(data.rows);
    drawLoad(data.rows);
    drawSoc(data.rows);
    renderTable(data.rows);
    renderLogs(data);
    setPills(window._health, data.model);
  }

  function selectedRows() {
    var id = document.getElementById("preset").value;
    var p = presets.find(function (x) { return x.id === id; });
    return p ? p.rows : [];
  }

  function batteryBody() {
    return {
      capacity_kwh: Number(document.getElementById("cap").value || 2000),
      soc_start: Number(document.getElementById("soc0").value || 0.7)
    };
  }

  function runForecast(rows) {
    var btn = document.getElementById("btn-run");
    btn.disabled = true;
    return fetch(API + "/api/forecast", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rows: rows, battery: batteryBody() })
    })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (x) {
        if (!x.ok) throw new Error((x.j && x.j.error) || "Forecast failed");
        renderAll(x.j);
        loadHistory();
        refreshHealth();
      })
      .catch(function (e) {
        document.getElementById("logs").textContent = String(e.message || e);
        alert(e.message || e);
      })
      .finally(function () { btn.disabled = false; });
  }

  function loadPresets() {
    return fetch(API + "/api/presets")
      .then(function (r) { return r.json(); })
      .then(function (j) {
        presets = j.presets || [];
        var sel = document.getElementById("preset");
        sel.innerHTML = "";
        presets.forEach(function (p) {
          var o = document.createElement("option");
          o.value = p.id;
          o.textContent = p.label;
          sel.appendChild(o);
        });
      });
  }

  function refreshHealth() {
    return fetch(API + "/api/health")
      .then(function (r) { return r.json(); })
      .then(function (h) {
        window._health = h;
        setPills(h, last && last.model);
      });
  }

  function loadHistory() {
    fetch(API + "/api/history")
      .then(function (r) { return r.json(); })
      .then(function (j) {
        var ul = document.getElementById("history");
        ul.innerHTML = "";
        (j.runs || []).forEach(function (run) {
          var li = document.createElement("li");
          var extra = "";
          if (run.savings_inr != null) {
            extra = " · Saved ₹" + Math.round(run.savings_inr) + " (" + run.savings_pct + "%)";
          }
          li.textContent = run.run_id + " · " + run.n_rows + " rows · peak " + run.peak_kw + " kW" + extra + " · " + run.model_source;
          li.onclick = function () {
            fetch(API + "/api/history/" + run.run_id)
              .then(function (r) { return r.json(); })
              .then(renderAll);
          };
          ul.appendChild(li);
        });
      });
  }

  function csvSafe(v) {
    var s = String(v == null ? "" : v);
    if (/^[=+\-@]/.test(s)) s = "'" + s;
    if (/[",\n]/.test(s)) s = '"' + s.replace(/"/g, '""') + '"';
    return s;
  }

  document.getElementById("btn-run").onclick = function () {
    var rows = selectedRows();
    if (!rows.length) {
      alert("No preset rows loaded.");
      return;
    }
    liveOffset = 0;
    runForecast(rows);
  };

  document.getElementById("btn-live").onclick = function () {
    if (liveTimer) {
      clearInterval(liveTimer);
      liveTimer = null;
      this.textContent = "Live feed";
      return;
    }
    var rows = selectedRows();
    if (rows.length < 24) {
      alert("Need a 24+ hour preset for live feed.");
      return;
    }
    this.textContent = "Stop live";
    liveTimer = setInterval(function () {
      liveOffset = (liveOffset + 1) % Math.max(1, rows.length - 23);
      var windowRows = rows.slice(liveOffset, liveOffset + 24);
      runForecast(windowRows);
    }, 3000);
  };

  document.getElementById("csv").onchange = function (ev) {
    var f = ev.target.files[0];
    if (!f) return;
    var fd = new FormData();
    fd.append("file", f);
    fetch(API + "/api/upload", { method: "POST", body: fd })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (x) {
        if (!x.ok) throw new Error((x.j && x.j.error) || "Upload failed");
        var q = document.getElementById("quality");
        q.hidden = false;
        q.textContent = "Cleaned " + x.j.quality.rows + " rows. " + (x.j.quality.warnings || []).join(" ");
        presets = presets.filter(function (p) { return p.id !== "upload"; });
        presets.unshift({ id: "upload", label: "Uploaded CSV (" + x.j.rows.length + " rows)", rows: x.j.rows });
        var sel = document.getElementById("preset");
        var o = document.createElement("option");
        o.value = "upload";
        o.textContent = "Uploaded CSV (" + x.j.rows.length + " rows)";
        sel.insertBefore(o, sel.firstChild);
        sel.value = "upload";
      })
      .catch(function (e) { alert(e.message || e); });
  };

  document.getElementById("btn-csv").onclick = function () {
    if (!last) return;
    var header = [
      "timestamp",
      "ambient_temp_c",
      "demand_kw",
      "mode",
      "critical_overload",
      "soc_pct",
      "battery_to_load_kw",
      "pv_kw",
      "grid_energy_kw",
      "baseline_cost_inr",
      "smart_cost_inr",
      "co2_avoided_kg",
      "reason"
    ];
    var lines = [header.join(",")];
    last.rows.forEach(function (r) {
      lines.push([
        r.timestamp,
        r.ambient_temp_c,
        r.demand_kw,
        r.mode,
        r.critical_overload ? "YES" : "NO",
        r.soc_pct,
        r.battery_to_load_kw != null ? r.battery_to_load_kw : 0,
        r.pv_kw != null ? r.pv_kw : 0,
        r.grid_energy_kw != null ? r.grid_energy_kw : 0,
        r.baseline_cost_inr != null ? r.baseline_cost_inr : 0,
        r.smart_cost_inr != null ? r.smart_cost_inr : 0,
        r.co2_avoided_kg != null ? r.co2_avoided_kg : 0,
        r.reason
      ].map(csvSafe).join(","));
    });
    var blob = new Blob([lines.join("\n")], { type: "text/csv" });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "forecast_dispatch_cost_carbon.csv";
    a.click();
  };

  Promise.all([loadPresets(), refreshHealth(), loadHistory()]).catch(function (e) {
    document.getElementById("logs").textContent = "Could not reach API. Start backend with: python backend/main.py\n" + e;
  });
})();
