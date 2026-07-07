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
function appleLink(s) { return `https://maps.apple.com/?daddr=${s.lat},${s.lng}&dirflg=d`; }
function googleLink(s) { return `https://www.google.com/maps/dir/?api=1&destination=${s.lat},${s.lng}`; }

// ---- Datum/Uhrzeit-Helfer (datetime-local <-> gespeicherter ISO-Wert) --------
const toDTLocal = (v) => (v ? String(v).slice(0, 16) : "");            // fürs Eingabefeld
const fmtDT = (v) => (v ? String(v).slice(0, 16).replace("T", " ") : ""); // für die Anzeige

// von/bis-Felder nur zeigen, wenn "reserviert" angehakt ist
function toggleResDates() {
  document.getElementById("resDates").classList.toggle(
    "hidden", !document.getElementById("f_reserviert").checked);
}

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
    el.className = `marker ${s.status}`;
    const marker = new maplibregl.Marker({ element: el, anchor: "bottom" })
      .setLngLat([s.lng, s.lat])
      .setPopup(new maplibregl.Popup({ offset: 24 }).setDOMContent(stopPopupDOM(s)))
      .addTo(map);
    state.markers[s.id] = marker;
  });
}

// ---- Liste im Panel ----------------------------------------------------------
function renderList() {
  const ul = document.getElementById("stopList");
  ul.innerHTML = "";
  state.stops.forEach((s) => {
    const li = document.createElement("li");
    li.innerHTML = `<span>${escapeHtml(s.name)}</span><span class="badge ${s.status}">${s.status}</span>`;
    li.onclick = () => {
      map.flyTo({ center: [s.lng, s.lat], zoom: 11 });
      state.markers[s.id]?.togglePopup();
    };
    ul.appendChild(li);
  });
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
    state.tripId = state.trips[0].id;
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

// ---- Formular (Anlegen/Bearbeiten) ------------------------------------------
function openForm(stop) {
  state.editingId = stop ? stop.id : null;
  document.getElementById("formTitle").textContent = stop ? "Stopp bearbeiten" : "Neuer Stopp";
  document.getElementById("f_name").value = stop ? stop.name : "";
  document.getElementById("f_status").value = stop ? stop.status : "geplant";
  document.getElementById("f_datum").value = stop && stop.datum ? stop.datum : "";
  document.getElementById("f_notiz").value = stop && stop.notiz ? stop.notiz : "";
  document.getElementById("f_reserviert").checked = !!(stop && stop.reserviert);
  document.getElementById("f_res_von").value = stop ? toDTLocal(stop.reserviert_von) : "";
  document.getElementById("f_res_bis").value = stop ? toDTLocal(stop.reserviert_bis) : "";
  toggleResDates();
  const c = stop ? { lat: stop.lat, lng: stop.lng } : state.pendingCoords;
  document.getElementById("f_coords").textContent = c ? `${c.lat.toFixed(5)}, ${c.lng.toFixed(5)}` : "–";
  document.getElementById("stopForm").classList.remove("hidden");
}
function closeForm() {
  document.getElementById("stopForm").classList.add("hidden");
  state.editingId = null; state.pendingCoords = null;
}
async function saveForm() {
  const reserviert = document.getElementById("f_reserviert").checked;
  const payload = {
    name: document.getElementById("f_name").value.trim(),
    status: document.getElementById("f_status").value,
    datum: document.getElementById("f_datum").value || null,
    notiz: document.getElementById("f_notiz").value.trim() || null,
    reserviert: reserviert,
    reserviert_von: reserviert ? (document.getElementById("f_res_von").value || null) : null,
    reserviert_bis: reserviert ? (document.getElementById("f_res_bis").value || null) : null,
  };
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
document.getElementById("f_cancel").onclick = closeForm;
document.getElementById("f_save").onclick = saveForm;
document.getElementById("f_reserviert").onchange = toggleResDates;
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
