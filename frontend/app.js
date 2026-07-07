"use strict";

// ---- kleine API-Hilfe --------------------------------------------------------
const api = {
  async get(url) { return (await fetch(url)).json(); },
  async send(method, url, body) {
    const r = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!r.ok) throw new Error(`${method} ${url} -> ${r.status}`);
    return r.status === 204 ? null : r.json();
  },
};

const STATUS = ["geplant", "reserviert", "besucht"];

const state = {
  trips: [],
  tripId: null,
  stops: [],
  markers: {},        // stopId -> maplibregl.Marker
  editingId: null,    // Stopp-ID beim Bearbeiten, sonst null
  pendingCoords: null // Reserve für das Bearbeiten-Formular
};

// ---- Karte -------------------------------------------------------------------
const map = new maplibregl.Map({
  container: "map",
  style: "https://tiles.openfreemap.org/styles/bright",
  center: [10.0, 51.0], // Mitteleuropa
  zoom: 4,
});
map.addControl(new maplibregl.NavigationControl(), "bottom-right");
map.addControl(new maplibregl.GeolocateControl({ trackUserLocation: true }), "bottom-right");

// ---- Deep-Links --------------------------------------------------------------
// Orts-Pin (zentriert auf den gewählten Ort). "Route" ist in Apple Maps ein Tipp entfernt.
function appleLink(s) { return `https://maps.apple.com/?ll=${s.lat},${s.lng}&q=${encodeURIComponent(s.name)}`; }
function googleLink(s) { return `https://www.google.com/maps/dir/?api=1&destination=${s.lat},${s.lng}`; }

// ---- Datum/Uhrzeit-Helfer (datetime-local <-> gespeicherter ISO-Wert) --------
const toDTLocal = (v) => (v ? String(v).slice(0, 16) : "");            // fürs Eingabefeld
const fmtDT = (v) => (v ? String(v).slice(0, 16).replace("T", " ") : ""); // für die Anzeige

// ---- Popup-Inhalt eines Stopps ----------------------------------------------
function stopPopupDOM(s) {
  const el = document.createElement("div");
  el.className = "popup";
  const datum = s.datum ? ` · ${s.datum}` : "";
  const resInfo = s.reserviert
    ? `<div class="res-info">🔒 reserviert${s.reserviert_von ? " ab " + fmtDT(s.reserviert_von) : ""}${s.reserviert_bis ? " bis " + fmtDT(s.reserviert_bis) : ""}</div>`
    : "";
  el.innerHTML = `
    <h4>${escapeHtml(s.name)} <span class="badge ${s.status}">${s.status}</span></h4>
    <div>${escapeHtml(s.notiz || "")}${datum}</div>
    ${resInfo}
    <div class="nav-links">
      <a href="${appleLink(s)}" target="_blank" rel="noopener">Apple&nbsp;Maps</a>
      <a href="${googleLink(s)}" target="_blank" rel="noopener">Google&nbsp;Maps</a>
    </div>
    <div class="edit-links">
      <button data-act="edit">Bearbeiten</button>
      <button data-act="del">Löschen</button>
    </div>`;
  el.querySelector('[data-act="edit"]').onclick = () => openForm(s);
  el.querySelector('[data-act="del"]').onclick = () => deleteStop(s.id);
  return el;
}

// ---- Marker rendern ----------------------------------------------------------
function clearMarkers() {
  Object.values(state.markers).forEach((m) => m.remove());
  state.markers = {};
}
function renderMarkers() {
  clearMarkers();
  state.stops.forEach((s) => {
    const el = document.createElement("div");
    el.className = "marker-wrap";
    const pin = document.createElement("div");
    pin.className = `marker ${s.status}`;
    el.appendChild(pin);
    if (s.reserviert) {
      const lock = document.createElement("div");
      lock.className = "marker-lock";
      lock.textContent = "🔒";
      lock.title = "reserviert";
      el.appendChild(lock);
    }
    const marker = new maplibregl.Marker({ element: el, anchor: "bottom" })
      .setLngLat([s.lng, s.lat])
      .setPopup(new maplibregl.Popup({ offset: 24 }).setDOMContent(stopPopupDOM(s)))
      .addTo(map);
    state.markers[s.id] = marker;
  });
}

// ---- Liste im Panel (sortierbar + Straßen-km) -------------------------------
let sortable = null;

function renderList() {
  const ul = document.getElementById("stopList");
  if (sortable) { sortable.destroy(); sortable = null; }
  ul.innerHTML = "";
  state.stops.forEach((s, i) => {
    const li = document.createElement("li");
    li.className = "stop";
    li.dataset.id = s.id;
    const lock = s.reserviert ? "🔒 " : "";
    li.innerHTML =
      `<span class="drag-handle" title="Ziehen zum Sortieren">⠿</span>` +
      `<span class="stop-name">${lock}${escapeHtml(s.name)}</span>` +
      `<span class="badge ${s.status}">${s.status}</span>` +
      `<span class="leg-dist" data-leg="${i}"></span>`;
    li.querySelector(".stop-name").onclick = () => {
      map.flyTo({ center: [s.lng, s.lat], zoom: 11 });
      state.markers[s.id]?.togglePopup();
    };
    ul.appendChild(li);
  });
  if (window.Sortable) {
    sortable = window.Sortable.create(ul, {
      handle: ".drag-handle",
      animation: 150,
      onEnd: onReorder,
    });
  }
  computeDistances();
}

// Reihenfolge nach dem Ziehen übernehmen: Zustand + Backend + km aktualisieren
async function onReorder() {
  const ul = document.getElementById("stopList");
  const ids = [...ul.querySelectorAll("li.stop")].map((li) => Number(li.dataset.id));
  const byId = Object.fromEntries(state.stops.map((s) => [s.id, s]));
  state.stops = ids.map((id) => byId[id]);
  try {
    await api.send("PUT", `/api/trips/${state.tripId}/stops/order`, { order: ids });
  } catch (e) {
    alert("Reihenfolge speichern fehlgeschlagen: " + e.message);
  }
  setTimeout(renderList, 0); // neu aufbauen -> km passend zur neuen Reihenfolge
}

// Straßen-Distanzen (km) zwischen aufeinanderfolgenden Stopps via OSRM.
// Ein /route-Call liefert alle Etappen (legs). Offline/Fehler -> keine km.
async function computeDistances() {
  document.querySelectorAll("#stopList .leg-dist").forEach((e) => (e.textContent = ""));
  const total = document.getElementById("tripTotal");
  if (total) total.textContent = "";
  if (state.stops.length < 2) return;
  const coords = state.stops.map((s) => `${s.lng},${s.lat}`).join(";");
  try {
    const r = await fetch(
      `https://router.project-osrm.org/route/v1/driving/${coords}?overview=false`);
    const d = await r.json();
    if (!r.ok || d.code !== "Ok" || !d.routes[0]) return;
    d.routes[0].legs.forEach((leg, i) => {
      const el = document.querySelector(`#stopList .leg-dist[data-leg="${i}"]`);
      if (el) el.textContent = `↓ ${Math.round(leg.distance / 1000)} km`;
    });
    if (total) {
      total.textContent = `Gesamtstrecke: ${Math.round(d.routes[0].distance / 1000)} km (Straße)`;
    }
  } catch (_) {
    /* offline / Routing-Dienst nicht erreichbar -> ohne km */
  }
}

// ---- Daten laden -------------------------------------------------------------
async function loadTrips() {
  state.trips = await api.get("/api/trips");
  const sel = document.getElementById("tripSelect");
  sel.innerHTML = "";
  state.trips.forEach((t) => {
    const o = document.createElement("option");
    o.value = t.id; o.textContent = t.name;
    sel.appendChild(o);
  });
  if (state.trips.length) {
    // Default: zuletzt beplante Reise (jüngstes updated_at)
    const latest = state.trips.reduce((a, b) =>
      (b.updated_at || "") > (a.updated_at || "") ? b : a);
    state.tripId = latest.id;
    sel.value = state.tripId;
    await loadStops();
  } else {
    document.getElementById("tripTitle").textContent = "Noch keine Reise – ＋ Reise";
  }
}

async function loadStops() {
  if (!state.tripId) return;
  state.stops = await api.get(`/api/trips/${state.tripId}/stops`);
  const trip = state.trips.find((t) => t.id == state.tripId);
  document.getElementById("tripTitle").textContent = trip ? trip.name : "–";
  renderMarkers();
  renderList();
  if (state.stops.length) {
    const b = new maplibregl.LngLatBounds();
    state.stops.forEach((s) => b.extend([s.lng, s.lat]));
    map.fitBounds(b, { padding: 60, maxZoom: 11, duration: 600 });
  }
}

// ---- Formular: zentrale Feld-Konfiguration ----------------------------------
// NEUES FELD? -> hier EINE Zeile ergänzen (+ passendes Feld im Backend-Modell
// models.py). Formular-Aufbau, Vorbelegung und Speichern laufen generisch
// über diese Liste; die DB-Spalte entsteht automatisch (Auto-Migration).
//   type: text | textarea | select | date | datetime | checkbox
//   options: nur bei select   default: Vorbelegung bei neuem Stopp
//   showIf: Schlüssel eines Checkbox-Feldes -> nur sichtbar, wenn dieses an ist
const STOP_FIELDS = [
  { key: "name",           label: "Name",           type: "text",     required: true },
  { key: "status",         label: "Status",         type: "select",
    options: ["geplant", "reserviert", "besucht"], default: "geplant" },
  { key: "datum",          label: "Datum",          type: "date" },
  { key: "notiz",          label: "Notiz",          type: "textarea" },
  { key: "reserviert",     label: "reserviert",     type: "checkbox" },
  { key: "reserviert_von", label: "Reserviert von", type: "datetime", showIf: "reserviert" },
  { key: "reserviert_bis", label: "Reserviert bis", type: "datetime", showIf: "reserviert" },
];

const fieldId = (key) => "f_" + key;

// Formular einmalig aus der Konfiguration erzeugen.
function buildForm() {
  const box = document.getElementById("formFields");
  box.innerHTML = "";
  STOP_FIELDS.forEach((f) => {
    const row = document.createElement("div");
    row.className = "field-row";
    if (f.showIf) row.dataset.showif = f.showIf;
    const id = fieldId(f.key);
    if (f.type === "select") {
      const opts = f.options.map((o) => `<option value="${o}">${o}</option>`).join("");
      row.innerHTML = `<label>${f.label} <select id="${id}">${opts}</select></label>`;
    } else if (f.type === "textarea") {
      row.innerHTML = `<label>${f.label} <textarea id="${id}" rows="3"></textarea></label>`;
    } else if (f.type === "checkbox") {
      row.innerHTML = `<label class="check"><input id="${id}" type="checkbox" /> ${f.label}</label>`;
    } else {
      const t = f.type === "datetime" ? "datetime-local" : f.type; // text | date | datetime-local
      row.innerHTML = `<label>${f.label} <input id="${id}" type="${t}" /></label>`;
    }
    box.appendChild(row);
  });
  // Checkbox-Felder, von denen andere abhängen, verdrahten
  STOP_FIELDS
    .filter((f) => f.type === "checkbox" && STOP_FIELDS.some((x) => x.showIf === f.key))
    .forEach((f) => {
      document.getElementById(fieldId(f.key)).onchange = () => applyShowIf(f.key);
    });
}

// abhängige (showIf-)Felder ein-/ausblenden
function applyShowIf(controlKey) {
  const on = document.getElementById(fieldId(controlKey)).checked;
  document.querySelectorAll(`.field-row[data-showif="${controlKey}"]`)
    .forEach((r) => r.classList.toggle("hidden", !on));
}

// ---- Formular öffnen / schließen / speichern --------------------------------
function openForm(stop) {
  state.editingId = stop ? stop.id : null;
  document.getElementById("formTitle").textContent = stop ? "Stopp bearbeiten" : "Neuer Stopp";
  STOP_FIELDS.forEach((f) => {
    const el = document.getElementById(fieldId(f.key));
    const val = stop ? stop[f.key] : undefined;
    if (f.type === "checkbox") el.checked = !!val;
    else if (f.type === "datetime") el.value = toDTLocal(val);
    else el.value = val ?? f.default ?? "";
  });
  STOP_FIELDS.filter((f) => f.type === "checkbox").forEach((f) => applyShowIf(f.key));
  const c = stop ? { lat: stop.lat, lng: stop.lng } : state.pendingCoords;
  document.getElementById("f_coords").textContent = c ? `${c.lat.toFixed(5)}, ${c.lng.toFixed(5)}` : "–";
  document.getElementById("stopForm").classList.remove("hidden");
}

function closeForm() {
  document.getElementById("stopForm").classList.add("hidden");
  state.editingId = null; state.pendingCoords = null;
}

async function saveForm() {
  const payload = {};
  STOP_FIELDS.forEach((f) => {
    const el = document.getElementById(fieldId(f.key));
    if (f.type === "checkbox") {
      payload[f.key] = el.checked;
    } else {
      const v = typeof el.value === "string" ? el.value.trim() : el.value;
      payload[f.key] = v || null;
    }
  });
  // abhängige Felder leeren, wenn ihr Steuerfeld aus ist
  STOP_FIELDS.forEach((f) => { if (f.showIf && !payload[f.showIf]) payload[f.key] = null; });
  if (!payload.name) { alert("Bitte einen Namen eingeben."); return; }
  try {
    if (state.editingId) {
      await api.send("PATCH", `/api/stops/${state.editingId}`, payload);
    } else {
      const c = state.pendingCoords;
      await api.send("POST", `/api/trips/${state.tripId}/stops`, { ...payload, lat: c.lat, lng: c.lng });
    }
    closeForm();
    await loadStops();
  } catch (e) { alert("Speichern fehlgeschlagen: " + e.message); }
}

async function deleteStop(id) {
  if (!confirm("Diesen Stopp löschen?")) return;
  await api.send("DELETE", `/api/stops/${id}`);
  await loadStops();
}

// ---- Ort per Kartenklick hinzufügen (Reverse-Geocoding + Rückfrage) ----------
const HINT = "📍 Tippe auf die Karte, um einen Ort hinzuzufügen";

// Kostenloses Reverse-Geocoding via OpenStreetMap Nominatim (kein API-Key).
// Bei Fehler/offline: null -> Aufrufer fällt auf Koordinaten zurück.
async function reverseGeocode(lat, lng) {
  try {
    const url = "https://nominatim.openstreetmap.org/reverse?format=jsonv2"
      + `&lat=${lat}&lon=${lng}&zoom=14&accept-language=de`;
    const r = await fetch(url, { headers: { Accept: "application/json" } });
    if (!r.ok) return null;
    const d = await r.json();
    const a = d.address || {};
    return (
      d.name || a.tourism || a.attraction || a.camp_site || a.village ||
      a.town || a.city || a.municipality || a.hamlet || a.suburb ||
      a.road || (d.display_name ? d.display_name.split(",")[0] : null) || null
    );
  } catch {
    return null;
  }
}

// Rückfrage-Dialog mit editierbarem Namen -> Promise<string|null>
// Liefert den (ggf. korrigierten) Namen bei "Ja", sonst null bei Abbruch.
function confirmAdd(name) {
  return new Promise((resolve) => {
    const modal = document.getElementById("confirmModal");
    const input = document.getElementById("c_name");
    input.value = name;
    const ok = document.getElementById("c_ok");
    const cancel = document.getElementById("c_cancel");
    const done = (val) => {
      modal.classList.add("hidden");
      ok.onclick = null; cancel.onclick = null; input.onkeydown = null;
      resolve(val);
    };
    ok.onclick = () => {
      const v = input.value.trim();
      if (!v) { input.focus(); return; } // leerer Name nicht erlaubt
      done(v);
    };
    cancel.onclick = () => done(null);
    input.onkeydown = (ev) => { if (ev.key === "Enter") { ev.preventDefault(); ok.onclick(); } };
    modal.classList.remove("hidden");
  });
}

// Klick auf die Karte: Ort ermitteln -> nachfragen -> anlegen.
map.on("click", async (e) => {
  if (!state.tripId) { alert("Bitte zuerst eine Reise anlegen (＋ Reise)."); return; }
  const { lat, lng } = e.lngLat;
  const hintEl = document.getElementById("hint");
  hintEl.textContent = "Ort wird ermittelt …";
  const name =
    (await reverseGeocode(lat, lng)) ||
    `Ort bei ${lat.toFixed(4)}, ${lng.toFixed(4)}`;
  hintEl.textContent = HINT;
  const chosenName = await confirmAdd(name);
  if (!chosenName) return;
  try {
    await api.send("POST", `/api/trips/${state.tripId}/stops`,
      { name: chosenName, lat, lng, status: "geplant" });
    await loadStops();
  } catch (err) {
    alert("Hinzufügen fehlgeschlagen: " + err.message);
  }
});

// ---- UI-Verdrahtung ----------------------------------------------------------
document.getElementById("tripSelect").onchange = async (e) => {
  state.tripId = Number(e.target.value);
  await loadStops();
};
document.getElementById("newTripBtn").onclick = async () => {
  const name = prompt("Name der neuen Reise?");
  if (!name) return;
  const trip = await api.send("POST", "/api/trips", { name: name.trim() });
  await loadTrips();
  document.getElementById("tripSelect").value = trip.id;
  state.tripId = trip.id;
  await loadStops();
};
buildForm(); // Formularfelder aus STOP_FIELDS erzeugen
document.getElementById("f_cancel").onclick = closeForm;
document.getElementById("f_save").onclick = saveForm;
document.getElementById("panelToggle").onclick = () =>
  document.getElementById("panel").classList.toggle("collapsed");

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---- Service Worker (Offline-Read-Cache) ------------------------------------
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
}

document.getElementById("hint").textContent = HINT;
map.on("load", loadTrips);
