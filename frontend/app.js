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
  markers: {},        // stopId -> google.maps.Marker
  editingId: null,    // Stopp-ID beim Bearbeiten, sonst null
  pendingCoords: null // Reserve für das Bearbeiten-Formular
};

// ---- Karte (Google Maps) ----------------------------------------------------
let map = null;
let infoWindow = null;
let infoOpenId = null;
const STATUS_COLORS = { geplant: "#2563eb", besucht: "#16a34a", reserviert: "#ea580c" };

// Google Maps JS dynamisch mit Key aus /api/config laden (Key ist per Referrer
// auf camper.dorf27.com beschränkt -> in der Auslieferung ohnehin sichtbar).
async function loadGoogleMaps() {
  const cfg = await api.get("/api/config");
  if (!cfg.googleMapsApiKey) throw new Error("Kein Google-Maps-Key konfiguriert");
  await new Promise((resolve, reject) => {
    window.__gmapsReady = resolve;
    const sc = document.createElement("script");
    sc.src = "https://maps.googleapis.com/maps/api/js?key="
      + encodeURIComponent(cfg.googleMapsApiKey) + "&loading=async&callback=__gmapsReady";
    sc.async = true;
    sc.onerror = () => reject(new Error("Google Maps konnte nicht geladen werden"));
    document.head.appendChild(sc);
  });
}

function initMap() {
  map = new google.maps.Map(document.getElementById("map"), {
    center: { lat: 51, lng: 10 }, // Mitteleuropa
    zoom: 4,
    mapTypeControl: false,
    streetViewControl: false,
    fullscreenControl: false,
    clickableIcons: false,
  });
  infoWindow = new google.maps.InfoWindow();
  google.maps.event.addListener(infoWindow, "closeclick", () => { infoOpenId = null; });
  addLocateControl();
  map.addListener("click", onMapClick);
}

// "Mein Standort"-Button als eigenes Karten-Control (unten rechts)
function addLocateControl() {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "locate-btn";
  btn.title = "Mein Standort";
  btn.textContent = "◎";
  btn.onclick = () => {
    if (!navigator.geolocation) return;
    navigator.geolocation.getCurrentPosition((pos) => {
      map.panTo({ lat: pos.coords.latitude, lng: pos.coords.longitude });
      map.setZoom(12);
    });
  };
  map.controls[google.maps.ControlPosition.RIGHT_BOTTOM].push(btn);
}

// Klick auf die Karte: Ort ermitteln -> nachfragen -> anlegen.
async function onMapClick(e) {
  if (!state.tripId) { alert("Bitte zuerst eine Reise anlegen (＋ Reise)."); return; }
  const lat = e.latLng.lat();
  const lng = e.latLng.lng();
  const hintEl = document.getElementById("hint");
  hintEl.textContent = "Ort wird ermittelt …";
  const name = (await reverseGeocode(lat, lng)) || `Ort bei ${lat.toFixed(4)}, ${lng.toFixed(4)}`;
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
}

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

// ---- Marker rendern (Google Maps) -------------------------------------------
function clearMarkers() {
  Object.values(state.markers).forEach((m) => m.setMap(null));
  state.markers = {};
}

// InfoWindow (Popup) eines Stopps öffnen – nutzt denselben DOM-Bauer wie zuvor.
function openInfo(s) {
  const marker = state.markers[s.id];
  if (!marker) return;
  infoWindow.setContent(stopPopupDOM(s));
  infoWindow.open(map, marker);
  infoOpenId = s.id;
}

function renderMarkers() {
  clearMarkers();
  state.stops.forEach((s) => {
    const marker = new google.maps.Marker({
      position: { lat: s.lat, lng: s.lng },
      map,
      draggable: true,
      title: s.name,
      icon: {
        path: google.maps.SymbolPath.CIRCLE,
        fillColor: STATUS_COLORS[s.status] || STATUS_COLORS.geplant,
        fillOpacity: 1,
        strokeColor: "#fff",
        strokeWeight: 2,
        scale: 9,
      },
      label: s.reserviert ? { text: "🔒", fontSize: "11px" } : undefined,
    });
    marker.addListener("click", () => openInfo(s));
    // Ort per Marker-Ziehen verschieben -> Koordinaten speichern + km neu
    marker.addListener("dragend", async () => {
      const p = marker.getPosition();
      s.lat = p.lat();
      s.lng = p.lng();
      try {
        await api.send("PATCH", `/api/stops/${s.id}`, { lat: s.lat, lng: s.lng });
      } catch (e) {
        alert("Verschieben fehlgeschlagen: " + e.message);
        return;
      }
      computeDistances(); // km/Fahrzeit an neue Position anpassen
      if (infoOpenId === s.id) openInfo(s); // Deep-Links aktualisieren
    });
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
      map.panTo({ lat: s.lat, lng: s.lng });
      map.setZoom(Math.max(map.getZoom(), 11));
      openInfo(s);
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

// Sekunden -> "1 h 49 min" (bzw. "49 min" / "2 h")
function fmtDur(sec) {
  const totalMin = Math.round((sec || 0) / 60);
  const h = Math.floor(totalMin / 60);
  const m = totalMin % 60;
  if (h && m) return `${h} h ${m} min`;
  if (h) return `${h} h`;
  return `${m} min`;
}

// Straßen-Distanzen (km) + Fahrzeit (HH:MM) zwischen aufeinanderfolgenden
// Stopps via OSRM. Ein /route-Call liefert Etappen (legs) mit distance+duration.
// Offline/Fehler -> keine Angaben.
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
      if (el) el.textContent = `↓ ${Math.round(leg.distance / 1000)} km (${fmtDur(leg.duration)})`;
    });
    if (total) {
      const r = d.routes[0];
      total.textContent =
        `Gesamtstrecke: ${Math.round(r.distance / 1000)} km (${fmtDur(r.duration)} Fahrzeit)`;
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
  if (state.stops.length === 1) {
    map.setCenter({ lat: state.stops[0].lat, lng: state.stops[0].lng });
    map.setZoom(11);
  } else if (state.stops.length > 1) {
    const b = new google.maps.LatLngBounds();
    state.stops.forEach((s) => b.extend({ lat: s.lat, lng: s.lng }));
    map.fitBounds(b, 60);
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

// ---- Service Worker (Offline-Read-Cache + nahtloses Auto-Update) -------------
if ("serviceWorker" in navigator) {
  // War die Seite beim Laden schon von einem SW kontrolliert? Dann ist ein
  // späterer Controller-Wechsel ein *Update* -> einmal automatisch neu laden.
  const hadController = !!navigator.serviceWorker.controller;
  let refreshing = false;
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    if (refreshing || !hadController) return; // Erstinstallation nicht neu laden
    refreshing = true;
    window.location.reload();
  });
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
}

document.getElementById("hint").textContent = HINT;

// ---- Start: Google Maps laden -> Karte init -> Daten laden -------------------
(async () => {
  try {
    await loadGoogleMaps();
    initMap();
    await loadTrips();
  } catch (e) {
    document.getElementById("hint").textContent = "Karte konnte nicht geladen werden: " + e.message;
    document.getElementById("tripTitle").textContent = "⚠️ Karte nicht verfügbar";
  }
})();
