// Zahlentabelle fuer die Monitoring-Detailseite: zeigt zu jedem Observable des laufenden
// Experiments den zuletzt empfangenen Wert als Zahl - Ergaenzung zu den Kurven aus plots.js
// (Anforderung aus LABS-Backend/docs/daheim_entwickeln_ohne_pi.md: "Zahlentabelle neben den
// Kurven auf der Monitoring-Seite").
//
// Datenquelle ist derselbe SSE-Strom wie fuer die Plots: Plot_manager.on_source_event_function
// (plots.js) feuert nach jedem Update ein "monitoring-update"-CustomEvent mit den Rohdaten.
// Dadurch gibt es KEINE zweite EventSource-Verbindung - das Backend wird weiterhin nur einmal
// pro Sekunde abgefragt.
//
// Datenformat (identisch zu plots.js):
//   data.current_experiment: Name des laufenden Experiments
//   data.updates: { device: { observable: [[timestamp_s, value], ...] } }
// Die Listen enthalten nur die NEUEN Punkte seit der letzten Abfrage; eine leere Liste heisst
// "kein neuer Wert" - der zuletzt angezeigte Wert bleibt dann stehen.

export class LiveValues {
  constructor(anchor_id) {
    this.anchor = document.getElementById(anchor_id);
    this.current_experiment = null;
    this.rows = new Map(); // "device/observable" -> { value_cell, time_cell }
    this.table_body = null;
    this.start_timestamp = null; // aeltester Datenpunkt des laufenden Experiments (~ Startzeit)
    this.elapsed_span = null;
    this.elapsed_timer = null;
  }

  handle_update(data) {
    // Experimentwechsel: alte Zeilen gehoeren zum alten Experiment -> Tabelle leeren
    // (gleiches Verhalten wie Plot_manager mit seinen Charts).
    if (data.current_experiment !== this.current_experiment) {
      this.current_experiment = data.current_experiment;
      this.reset();
    }
    for (const [device, device_values] of Object.entries(data.updates || {})) {
      for (const [observable, points] of Object.entries(device_values)) {
        if (!points || points.length === 0) {
          continue; // kein neuer Wert -> letzten stehen lassen
        }
        // Versuchszeit: die erste get_updates-Antwort enthaelt die komplette Historie seit
        // Experimentstart, der aelteste Punkt darin ist also ~ die Startzeit.
        if (this.start_timestamp === null || points[0][0] < this.start_timestamp) {
          this.start_timestamp = points[0][0];
        }
        const [timestamp, value] = points[points.length - 1];
        this.set_value(device, observable, value, timestamp);
      }
    }
  }

  reset() {
    this.rows.clear();
    this.table_body = null;
    this.start_timestamp = null;
    this.elapsed_span = null;
    if (this.elapsed_timer !== null) {
      clearInterval(this.elapsed_timer);
      this.elapsed_timer = null;
    }
    if (this.anchor) {
      this.anchor.innerHTML = "";
    }
  }

  ensure_table() {
    if (this.table_body || !this.anchor) {
      return;
    }
    const heading = document.createElement("h2");
    heading.innerText = "Current values";
    // Versuchszeit neben der Ueberschrift, tickt sekuendlich weiter (auch zwischen zwei Updates)
    this.elapsed_span = document.createElement("span");
    this.elapsed_span.style.fontSize = "60%";
    this.elapsed_span.style.fontWeight = "normal";
    this.elapsed_span.style.marginLeft = "1em";
    this.elapsed_span.style.fontVariantNumeric = "tabular-nums";
    heading.appendChild(this.elapsed_span);
    this.elapsed_timer = setInterval(() => this.update_elapsed(), 1000);
    const table = document.createElement("table");
    table.className = "table table-sm table-striped";
    table.style.width = "auto";
    table.innerHTML =
      "<thead><tr><th>Device</th><th>Observable</th><th>Value</th><th>Updated</th></tr></thead>";
    this.table_body = document.createElement("tbody");
    table.appendChild(this.table_body);
    this.anchor.appendChild(heading);
    this.anchor.appendChild(table);
  }

  set_value(device, observable, value, timestamp) {
    this.ensure_table();
    const key = `${device}/${observable}`;
    let row = this.rows.get(key);
    if (!row) {
      const tr = document.createElement("tr");
      const device_cell = document.createElement("td");
      device_cell.innerText = device;
      const observable_cell = document.createElement("td");
      observable_cell.innerText = observable;
      const value_cell = document.createElement("td");
      value_cell.style.fontWeight = "600";
      value_cell.style.textAlign = "right";
      value_cell.style.fontVariantNumeric = "tabular-nums";
      const time_cell = document.createElement("td");
      tr.append(device_cell, observable_cell, value_cell, time_cell);
      this.table_body.appendChild(tr);
      row = { value_cell: value_cell, time_cell: time_cell, last_timestamp: null };
      this.rows.set(key, row);
    }
    row.value_cell.innerText = this.format_value(value);
    row.last_timestamp = timestamp;
    this.render_age(row);
  }

  // "Updated" zeigt das ALTER des Werts, nicht die Uhrzeit: solange alle Geraete liefern,
  // steht ueberall dasselbe - informativ wird die Spalte genau dann, wenn ein Wert NICHT
  // mehr nachkommt (Polling gestoppt, Geraet haengt). Deshalb: >5 s ohne Update -> rot.
  render_age(row) {
    if (row.last_timestamp === null) {
      return;
    }
    const age = Math.max(0, Math.round(Date.now() / 1000 - row.last_timestamp));
    row.time_cell.innerText = age <= 1 ? "now" : `${age} s ago`;
    const stale = age > 5;
    row.time_cell.style.color = stale ? "#c00" : "#888";
    row.time_cell.style.fontWeight = stale ? "600" : "normal";
  }

  update_elapsed() {
    for (const row of this.rows.values()) {
      this.render_age(row);
    }
    if (!this.elapsed_span || this.start_timestamp === null) {
      return;
    }
    let seconds = Math.max(0, Math.floor(Date.now() / 1000 - this.start_timestamp));
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    seconds = seconds % 60;
    const mmss = `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
    this.elapsed_span.innerText = `running for ${hours > 0 ? hours + ":" : ""}${mmss}`;
  }

  format_value(value) {
    if (typeof value === "number" && Number.isFinite(value)) {
      // auf 3 Nachkommastellen runden, ohne nachlaufende Nullen anzuzeigen
      return String(Math.round(value * 1000) / 1000);
    }
    return String(value); // Booleans (running/clockwise) und Strings unveraendert
  }
}
