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
  addMode: false,
  editingId: null,    // Stopp-ID beim Bearbeiten, sonst null
  pendingCoords: null // {lat,lng} beim Neuanlegen
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

// ---- Popup-Inhalt eines Stopps ----------------------------------------------
function stopPopupDOM(s) {
  const el = document.createElement("div");
  el.className = "popup";
  const datum = s.datum ? ` · ${s.datum}` : "";
  el.innerHTML = `
    <h4>${escapeHtml(s.name)} <span class="badge ${s.status}">${s.status}</span></h4>
    <div>${escapeHtml(s.notiz || "")}${datum}</div>
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
  const c = stop ? { lat: stop.lat, lng: stop.lng } : state.pendingCoords;
  document.getElementById("f_coords").textContent = c ? `${c.lat.toFixed(5)}, ${c.lng.toFixed(5)}` : "–";
  document.getElementById("stopForm").classList.remove("hidden");
}
function closeForm() {
  document.getElementById("stopForm").classList.add("hidden");
  state.editingId = null; state.pendingCoords = null;
}
async function saveForm() {
  const payload = {
    name: document.getElementById("f_name").value.trim(),
    status: document.getElementById("f_status").value,
    datum: document.getElementById("f_datum").value || null,
    notiz: document.getElementById("f_notiz").value.trim() || null,
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

// ---- Add-Mode: nächster Kartenklick legt Stopp an ---------------------------
function setAddMode(on) {
  state.addMode = on;
  document.getElementById("addStopBtn").classList.toggle("active", on);
  document.getElementById("hint").textContent = on ? "Auf die Karte tippen, um den Stopp zu setzen" : "";
  map.getCanvas().style.cursor = on ? "crosshair" : "";
}
map.on("click", (e) => {
  if (!state.addMode) return;
  if (!state.tripId) { alert("Bitte zuerst eine Reise anlegen."); setAddMode(false); return; }
  state.pendingCoords = { lat: e.lngLat.lat, lng: e.lngLat.lng };
  setAddMode(false);
  openForm(null);
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
document.getElementById("addStopBtn").onclick = () => setAddMode(!state.addMode);
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

map.on("load", loadTrips);
