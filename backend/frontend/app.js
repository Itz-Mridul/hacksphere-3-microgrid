(function () {
  var API = "";
  var last = null;
  var liveTimer = null;
  var liveOffset = 0;
  var chartLoad = null;
  var chartSoc = null;
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
    document.getElementById("src-pill").textContent = "Model: " + (model && model.version ? model.version + " / " : "") + src;
    document.getElementById("src-pill").className = "pill " + sourceClass(src);
    if (model) document.getElementById("run-pill").textContent = "Run " + (model.latency_ms || 0) + " ms";
    var used = health ? health.quickml_calls_today : 0;
    var budget = health ? health.daily_budget : 300;
    document.getElementById("call-pill").textContent = "QuickML calls today " + used + "/" + budget;
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
      return "Critical overload " + prettyTs(e.start) + "–" + hourFromTs(e.end) + ":00 (" + e.hours + " h, peak " + e.peak_kw.toFixed(1) + " kW)";
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
      return r.critical_overload ? "#ff5d6c" : (r.mode === "MAIN_GRID" ? "#3dd68c" : "#5aa8ff");
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
          { label: "p95 high", data: hi, borderColor: "rgba(255,255,255,0.15)", pointRadius: 0, fill: "+1" },
          { label: "p95 low", data: lo, borderColor: "rgba(255,255,255,0.15)", pointRadius: 0, fill: false },
          {
            label: "Demand kW",
            data: y,
            borderColor: "#9ecbff",
            pointBackgroundColor: chartColors(rows),
            pointRadius: 4,
            tension: 0.15
          }
        ]
      },
      options: {
        responsive: true,
        plugins: {
          legend: { labels: { color: "#93a0bb" } },
          annotation: undefined
        },
        scales: {
          x: { ticks: { color: "#93a0bb" }, grid: { color: "#243049" } },
          y: { ticks: { color: "#93a0bb" }, grid: { color: "#243049" } }
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
            label: "SoC %",
            data: rows.map(function (r) { return r.soc_pct; }),
            borderColor: "#f5c542",
            yAxisID: "y"
          },
          {
            label: "Battery to load kW",
            data: rows.map(function (r) { return r.battery_to_load_kw; }),
            backgroundColor: "rgba(90,168,255,0.45)",
            yAxisID: "y1"
          }
        ]
      },
      options: {
        responsive: true,
        plugins: { legend: { labels: { color: "#93a0bb" } } },
        scales: {
          x: { ticks: { color: "#93a0bb" }, grid: { color: "#243049" } },
          y: { min: 0, max: 100, ticks: { color: "#93a0bb" }, grid: { color: "#243049" } },
          y1: { position: "right", ticks: { color: "#93a0bb" }, grid: { display: false } }
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

  function renderLogs(data) {
    var lines = [];
    var q = data.quality || {};
    lines.push("INGEST rows=" + q.rows + " filled=" + JSON.stringify(q.filled || {}) + " clipped=" + JSON.stringify(q.clipped || {}) + " bad_ts=" + (q.bad_timestamp || 0));
    (q.warnings || []).forEach(function (w) { lines.push("WARN " + w); });
    var m = data.model || {};
    lines.push("MODEL source=" + m.source + " latency=" + m.latency_ms + "ms calls=" + m.quickml_calls + " cache_hits=" + m.cache_hits);
    (data.transitions || []).forEach(function (t) { lines.push(t.message); });
    document.getElementById("logs").textContent = lines.join("\n");
  }

  function renderAll(data) {
    last = data;
    renderHeadline(data);
    renderBanner(data.events);
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
          li.textContent = run.run_id + " · " + run.n_rows + " rows · peak " + run.peak_kw + " · " + run.model_source;
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
    var header = ["timestamp","ambient_temp_c","demand_kw","mode","critical_overload","soc_pct","reason"];
    var lines = [header.join(",")];
    last.rows.forEach(function (r) {
      lines.push([r.timestamp, r.ambient_temp_c, r.demand_kw, r.mode, r.critical_overload, r.soc_pct, r.reason].map(csvSafe).join(","));
    });
    var blob = new Blob([lines.join("\n")], { type: "text/csv" });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "forecast.csv";
    a.click();
  };

  Promise.all([loadPresets(), refreshHealth(), loadHistory()]).catch(function (e) {
    document.getElementById("logs").textContent = "Could not reach API. Start backend with: python backend/main.py\n" + e;
  });
})();
