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
  stops: [],          // Übernachtungsplätze (kind="stop") – immer in Liste + Route
  pois: [],           // reine Punkte (kind="poi") – nur Karte, außer in_route=true
  route: [],          // geordnete Route = Stops + in_route-POIs (nach reihenfolge)
  markers: {},        // id -> google.maps.Marker (Stops UND POIs)
  editingId: null,    // Stopp-ID beim Bearbeiten, sonst null
  pendingCoords: null // Reserve für das Bearbeiten-Formular
};

// ---- Karte (Google Maps) ----------------------------------------------------
let map = null;
let infoWindow = null;
let infoOpenId = null;
let geomLib = null;       // Google "geometry"-Library (Polyline dekodieren)
let routePolylines = [];  // je Etappe eine eigene Linie (zwischen Übernachtungen)
let routeSegments = [];   // je Etappe { label, path } – für Etappen-Auswahl der Suche
let searchMarkers = [];   // temporäre Fund-Pins der Routensuche
const STATUS_COLORS = { geplant: "#2563eb", besucht: "#16a34a", reserviert: "#ea580c" };
// Etappen-Linien wechseln die Farbe (Tagesgrenzen sichtbar); kein Marker-Farbwert
const SEGMENT_COLORS = ["#0f766e", "#06b6d4"];

// Google Maps JS dynamisch mit Key aus /api/config laden (Key ist per Referrer
// auf die eigene Domain beschränkt -> in der Auslieferung ohnehin sichtbar).
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
    // Kartentyp-Auswahl (Karte / Satellit / Gelände) als kompaktes Dropdown
    mapTypeControl: true,
    mapTypeControlOptions: {
      style: google.maps.MapTypeControlStyle.DROPDOWN_MENU,
      position: google.maps.ControlPosition.TOP_RIGHT,
    },
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

// Routing läuft server-seitig (/api/directions). Client braucht die
// "geometry"-Library, um die vom Server gelieferte Polyline zu dekodieren.
async function ensureGeometry() {
  if (!geomLib) geomLib = await google.maps.importLibrary("geometry");
  return geomLib;
}

// Eine Etappe routen – rein server-seitig (/api/directions, Server-Key).
// Ergebnis normalisiert: { legs:[{distance(m),duration(s)}], path: LatLng[] } | null
async function fetchSegmentRoute(seg) {
  try {
    const r = await api.send("POST", "/api/directions", {
      origin: { lat: seg.from.lat, lng: seg.from.lng },
      destination: { lat: seg.to.lat, lng: seg.to.lng },
      waypoints: seg.waypoints.map((w) => ({ lat: w.lat, lng: w.lng })),
    });
    if (r && r.ok) {
      const { encoding } = await ensureGeometry();
      return { legs: r.legs, path: encoding.decodePath(r.polyline) };
    }
  } catch { /* Routing nicht verfügbar */ }
  return null;
}

// alle gezeichneten Etappen-Linien entfernen
function clearRoutes() {
  routePolylines.forEach((p) => p.setMap(null));
  routePolylines = [];
}

// Klick auf die Karte: Ort ermitteln -> nachfragen -> anlegen.
async function onMapClick(e) {
  if (!state.tripId) { alert("Bitte zuerst eine Reise anlegen (＋ Reise)."); return; }
  if (isLocked()) { lockAlert(); return; }
  const lat = e.latLng.lat();
  const lng = e.latLng.lng();
  const hintEl = document.getElementById("hint");
  hintEl.textContent = "Ort wird ermittelt …";
  const name = (await reverseGeocode(lat, lng)) || `Ort bei ${lat.toFixed(4)}, ${lng.toFixed(4)}`;
  hintEl.textContent = HINT;
  const chosen = await confirmAdd(name);
  if (!chosen) return;
  try {
    await api.send("POST", `/api/trips/${state.tripId}/stops`,
      { name: chosen.name, lat, lng, status: "geplant", kind: chosen.kind });
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
// "2026-07-20T08:00:00" -> "20.07 08:00"
const fmtDMHM = (v) => {
  const m = String(v || "").match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/);
  return m ? `${m[3]}.${m[2]} ${m[4]}:${m[5]}` : "";
};

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
  // Übernachtungsplätze: farbige Kreise nach Status, 🔒 bei Reservierung
  state.stops.forEach((s) => {
    const marker = new google.maps.Marker({
      position: { lat: s.lat, lng: s.lng }, map, draggable: !isLocked(), title: s.name,
      icon: {
        path: google.maps.SymbolPath.CIRCLE,
        fillColor: STATUS_COLORS[s.status] || STATUS_COLORS.geplant,
        fillOpacity: 1, strokeColor: "#fff", strokeWeight: 2, scale: 9,
      },
      label: { text: s.reserviert ? "✅" : "❓", fontSize: "11px" },
    });
    marker.addListener("click", () => openInfo(s));
    marker.addListener("dragend", async () => {
      const p = marker.getPosition();
      s.lat = p.lat(); s.lng = p.lng();
      try {
        await api.send("PATCH", `/api/stops/${s.id}`, { lat: s.lat, lng: s.lng });
      } catch (e) { alert("Verschieben fehlgeschlagen: " + e.message); return; }
      computeDistances();
      if (infoOpenId === s.id) openInfo(s);
    });
    state.markers[s.id] = marker;
  });
  // POIs: violette Punkte; in_route -> teal (liegt auf der Route). Klick -> Entfernungen
  state.pois.forEach((p) => {
    const marker = new google.maps.Marker({
      position: { lat: p.lat, lng: p.lng }, map, draggable: !isLocked(),
      title: p.in_route ? p.name + " (in Route)" : p.name,
      icon: {
        path: google.maps.SymbolPath.CIRCLE,
        fillColor: p.in_route ? "#0f766e" : "#7c3aed",
        fillOpacity: 1, strokeColor: "#fff", strokeWeight: 2, scale: p.in_route ? 7 : 6,
      },
    });
    marker.addListener("click", () => openPoiInfo(p));
    marker.addListener("dragend", async () => {
      const pos = marker.getPosition();
      p.lat = pos.lat(); p.lng = pos.lng();
      try {
        await api.send("PATCH", `/api/stops/${p.id}`, { lat: p.lat, lng: p.lng });
      } catch (e) { alert("Verschieben fehlgeschlagen: " + e.message); }
    });
    state.markers[p.id] = marker;
  });
}

// POI-Klick: Entfernungen (Straße) zu ALLEN Übernachtungsplätzen via OSRM /table.
async function openPoiInfo(poi) {
  const el = document.createElement("div");
  el.className = "popup";
  const stops = state.stops;
  const noteHtml = poi.notiz ? `<div class="poi-note">${escapeHtml(poi.notiz)}</div>` : "";
  el.innerHTML =
    `<h4>${escapeHtml(poi.name)} <span class="badge poi">Punkt</span></h4>` + noteHtml +
    `<div class="edit-links"><button data-act="edit">Bearbeiten / Notiz</button><button data-act="del">Löschen</button></div>` +
    `<div class="poi-dist">${stops.length ? "Entfernungen werden berechnet …" : "Noch keine Übernachtungsplätze."}</div>`;
  el.querySelector('[data-act="edit"]').onclick = () => openForm(poi);
  el.querySelector('[data-act="del"]').onclick = () => deleteStop(poi.id);
  infoWindow.setContent(el);
  infoWindow.open(map, state.markers[poi.id]);
  infoOpenId = poi.id;
  if (!stops.length) return;
  const coords = [poi, ...stops].map((s) => `${s.lng},${s.lat}`).join(";");
  try {
    const r = await fetch(
      `https://router.project-osrm.org/table/v1/driving/${coords}?sources=0&annotations=distance,duration`);
    const d = await r.json();
    if (d.code !== "Ok") throw 0;
    const dist = d.distances[0], dur = d.durations[0];
    const rows = stops
      .map((s, i) => ({ name: s.name, km: dist[i + 1] / 1000, sec: dur[i + 1] }))
      .sort((a, b) => a.km - b.km)
      .map((x) => `<div>→ ${escapeHtml(x.name)}: <b>${Math.round(x.km)} km</b> (${fmtDur(x.sec)})</div>`)
      .join("");
    const box = el.querySelector(".poi-dist");
    if (box) box.innerHTML = `<div class="poi-dist-title">Entfernung zu Übernachtungsplätzen:</div>${rows}`;
  } catch {
    const box = el.querySelector(".poi-dist");
    if (box) box.textContent = "Entfernungen nicht verfügbar (offline?).";
  }
}

// ---- POI-Liste im Aufklappmenü ----------------------------------------------
function renderPoiList() {
  const ul = document.getElementById("poiList");
  const count = document.getElementById("poiCount");
  const section = document.getElementById("poiSection");
  if (!ul) return;
  ul.innerHTML = "";
  if (count) count.textContent = state.pois.length ? `(${state.pois.length})` : "";
  if (section) section.style.display = state.pois.length ? "" : "none";
  state.pois.forEach((p) => {
    const li = document.createElement("li");
    li.className = "poi-item";
    const note = p.notiz ? `<div class="poi-note">${escapeHtml(p.notiz)}</div>` : "";
    li.innerHTML =
      `<span class="poi-name">${p.in_route ? "🚏" : "📍"} ${escapeHtml(p.name)}</span>` +
      `<span class="poi-actions">` +
        `<button data-act="edit" title="Bearbeiten / Notiz">✏️</button>` +
        `<button data-act="del" title="Löschen">🗑</button>` +
      `</span>` +
      `<label class="poi-route" title="Punkt als Wegpunkt in die Route aufnehmen">` +
        `<input type="checkbox" data-act="route"${p.in_route ? " checked" : ""}` +
        `${isLocked() ? " disabled" : ""} /> in Route</label>` +
      note;
    li.querySelector(".poi-name").onclick = () => {
      map.panTo({ lat: p.lat, lng: p.lng });
      map.setZoom(Math.max(map.getZoom(), 11));
      openPoiInfo(p);
    };
    li.querySelector('[data-act="edit"]').onclick = () => openForm(p);
    li.querySelector('[data-act="del"]').onclick = () => deleteStop(p.id);
    li.querySelector('[data-act="route"]').onchange = async (ev) => {
      if (isLocked()) { ev.target.checked = p.in_route; lockAlert(); return; }
      try {
        await api.send("PATCH", `/api/stops/${p.id}`, { in_route: ev.target.checked });
        await loadStops();
      } catch (e) { alert("Konnte Route nicht ändern: " + e.message); }
    };
    ul.appendChild(li);
  });
}

// ---- Liste im Panel (sortierbar + Straßen-km) -------------------------------
let sortable = null;

function renderList() {
  const ul = document.getElementById("stopList");
  if (sortable) { sortable.destroy(); sortable = null; }
  ul.innerHTML = "";
  state.route.forEach((s, i) => {
    const isPoi = s.kind === "poi";
    const li = document.createElement("li");
    li.className = isPoi ? "stop route-poi" : "stop";
    li.dataset.id = s.id;
    li.dataset.kind = s.kind || "stop";
    const badge = isPoi
      ? `<span class="badge poi">Wegpunkt</span>`
      : `<span class="badge ${s.status}">${s.status}</span>`;
    const resLine = !isPoi && s.reserviert && (s.reserviert_von || s.reserviert_bis)
      ? `<span class="stop-res">An: ${fmtDMHM(s.reserviert_von) || "?"} · ab: ${fmtDMHM(s.reserviert_bis) || "?"}</span>`
      : "";
    const handle = isLocked() ? "" : `<span class="drag-handle" title="Ziehen zum Sortieren">⠿</span>`;
    li.innerHTML =
      handle +
      `<span class="stop-name">${isPoi ? "🚏 " : ""}${escapeHtml(s.name)}</span>` +
      badge +
      resLine +
      `<span class="leg-dist" data-leg="${i}"></span>` +
      `<span class="leg-warn" data-warn="${i}"></span>`;
    li.querySelector(".stop-name").onclick = () => {
      map.panTo({ lat: s.lat, lng: s.lng });
      map.setZoom(Math.max(map.getZoom(), 11));
      isPoi ? openPoiInfo(s) : openInfo(s);
    };
    ul.appendChild(li);
  });
  if (window.Sortable && !isLocked()) {
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
  const byId = Object.fromEntries(state.route.map((s) => [s.id, s]));
  state.route = ids.map((id) => byId[id]);
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

// Straßenroute + Etappen-km/Fahrzeit via Google Directions – ETAPPENWEISE:
// pro Abschnitt zwischen zwei Übernachtungs-Ankern (Start/Übernachtung/Ziel)
// eine eigene Directions-Anfrage + eine eigene, abwechselnd gefärbte Linie.
// In_route-POIs sind Wegpunkte INNERHALB ihrer Etappe. Vorteil: das 25-Wegpunkt-
// Limit greift nur pro Etappe (praktisch nie) und man sieht km/Zeit je Etappe.
//   Übernachtung-Zeile  -> Etappen-Summe (seit letzter Übernachtung/Start)
//   POI-Zeile           -> einzelner Wegpunkt-Sprung (Umweg)
async function computeDistances() {
  document.querySelectorAll("#stopList .leg-dist").forEach((e) => (e.textContent = ""));
  document.querySelectorAll("#stopList .leg-warn").forEach((e) => { e.textContent = ""; e.title = ""; });
  const total = document.getElementById("tripTotal");
  if (total) total.textContent = "";

  const t = currentTrip() || {};
  const hasStart = t.start_lat != null && t.start_lng != null;
  const hasEnd = t.end_lat != null && t.end_lng != null;

  // Geordnete Punktfolge: [Start?] + Route-Elemente + [Ziel?]. routeIdx zeigt auf
  // die Listenzeile (>=0), Start/Ziel haben keine Zeile (routeIdx=null).
  const seq = [];
  if (hasStart) seq.push({ lat: t.start_lat, lng: t.start_lng, anchor: true, routeIdx: null, name: "Start" });
  state.route.forEach((s, i) => seq.push({
    lat: s.lat, lng: s.lng, anchor: s.kind !== "poi", routeIdx: i, ref: s, name: s.name,
  }));
  if (hasEnd) seq.push({ lat: t.end_lat, lng: t.end_lng, anchor: true, routeIdx: null, name: "Ziel" });
  if (seq.length < 2) { clearRoutes(); routeSegments = []; renderLegSelector(); return; }

  // Anker = Segmentgrenzen: erster + letzter Punkt sowie jede Übernachtung.
  const anchorPos = [];
  seq.forEach((n, i) => {
    if (i === 0 || i === seq.length - 1 || n.anchor) anchorPos.push(i);
  });
  // Etappen zwischen aufeinanderfolgenden Ankern; POIs dazwischen = Wegpunkte.
  const segments = [];
  for (let k = 0; k < anchorPos.length - 1; k++) {
    const a = anchorPos[k], b = anchorPos[k + 1];
    segments.push({ from: seq[a], to: seq[b], waypoints: seq.slice(a + 1, b) });
  }
  if (!segments.length) { clearRoutes(); routeSegments = []; renderLegSelector(); return; }

  try {
    // Eine Etappe je Route-Anfrage (parallel); Backend bevorzugt, Client-Fallback.
    const routed = await Promise.all(segments.map((seg) => fetchSegmentRoute(seg)));

    clearRoutes();
    routeSegments = [];
    let grandM = 0, grandS = 0, backM = 0, backS = 0, anyFail = false;

    routed.forEach((res, si) => {
      const seg = segments[si];
      if (!res) { anyFail = true; return; }
      const legs = res.legs; // [{distance(m), duration(s)}] je Teilstück
      const path = res.path; // LatLng[] (Backend-Polyline dekodiert bzw. overview_path)
      // Etappen-Linie zeichnen (abwechselnde Farbe)
      routePolylines.push(new google.maps.Polyline({
        path, map,
        strokeColor: SEGMENT_COLORS[si % SEGMENT_COLORS.length],
        strokeWeight: 5, strokeOpacity: 0.85,
      }));
      // Etappe für die Etappen-Auswahl der Suche merken (Label + Straßenverlauf)
      routeSegments.push({
        label: `${seg.from.name} → ${seg.to.name}`,
        path: path.map((ll) => ({ lat: ll.lat(), lng: ll.lng() })),
      });
      // Wegpunkt-POIs: jeweils der einzelne eingehende Sprung
      seg.waypoints.forEach((wp, wi) => {
        const leg = legs[wi];
        if (leg && wp.routeIdx != null) setLeg(wp.routeIdx, leg.distance, leg.duration);
      });
      // Etappen-Summe (from -> to durch alle POIs)
      const segM = legs.reduce((s, l) => s + l.distance, 0);
      const segS = legs.reduce((s, l) => s + l.duration, 0);
      seg._m = segM; seg._s = segS;
      grandM += segM; grandS += segS;
      // Summe der Zeile des Ziel-Ankers (Übernachtung), falls es eine Liste-Zeile ist
      if (seg.to.routeIdx != null) setLeg(seg.to.routeIdx, segM, segS);
      // letzte Etappe -> Rückweg zum Ziel (nur wenn Ziel gesetzt)
      if (hasEnd && si === routed.length - 1) { backM = segM; backS = segS; }
    });

    // Reservierungs-Warnung zwischen aufeinanderfolgenden reservierten
    // Übernachtungen: Ende(Vorplatz) + Etappen-Fahrzeit > Beginn(nächster)?
    for (let si = 0; si < segments.length; si++) {
      const seg = segments[si];
      const a = seg.from.ref, b = seg.to.ref; // ref nur bei Listen-Elementen
      if (a && b && a.kind !== "poi" && b.kind !== "poi" && seg._s != null &&
          a.reserviert && a.reserviert_bis && b.reserviert && b.reserviert_von) {
        const arrival = new Date(a.reserviert_bis).getTime() + seg._s * 1000;
        if (arrival > new Date(b.reserviert_von).getTime()) {
          const w = document.querySelector(`#stopList .leg-warn[data-warn="${seg.to.routeIdx}"]`);
          if (w) {
            w.textContent = "⚠️ Reservierung knapp";
            w.title = `Abfahrt frühestens ${fmtDT(a.reserviert_bis)} + ${fmtDur(seg._s)} Fahrt`
              + ` = Ankunft nach Reservierungsbeginn (${fmtDT(b.reserviert_von)}).`;
          }
        }
      }
    }

    let txt = `Gesamtstrecke: ${Math.round(grandM / 1000)} km (${fmtDur(grandS)} Fahrzeit)`;
    if (hasEnd) txt += ` · Rückweg zum Ziel: ${Math.round(backM / 1000)} km (${fmtDur(backS)})`;
    if (anyFail) txt += " · ⚠️ eine Etappe konnte nicht berechnet werden";
    if (total) total.textContent = txt;
    renderLegSelector();
  } catch (err) {
    clearRoutes();
    routeSegments = [];
    renderLegSelector();
    console.warn("Directions fehlgeschlagen:", err && err.message);
  }
}

// km/Zeit in die Listenzeile i schreiben
function setLeg(i, meters, seconds) {
  const el = document.querySelector(`#stopList .leg-dist[data-leg="${i}"]`);
  if (el) el.textContent = `↓ ${Math.round(meters / 1000)} km (${fmtDur(seconds)})`;
}

// Etappen-Auswahl (Dropdown vor der Routensuche) aus routeSegments aufbauen
function renderLegSelector() {
  const sel = document.getElementById("routeLegSelect");
  if (!sel) return;
  const prev = sel.value;
  sel.innerHTML = "";
  const optAll = document.createElement("option");
  optAll.value = "all";
  optAll.textContent = routeSegments.length ? `Ganze Route (${routeSegments.length} Etappen)` : "— keine Route —";
  sel.appendChild(optAll);
  routeSegments.forEach((s, i) => {
    const o = document.createElement("option");
    o.value = String(i); o.textContent = s.label;
    sel.appendChild(o);
  });
  if ([...sel.options].some((o) => o.value === prev)) sel.value = prev;
  sel.disabled = routeSegments.length === 0;
}

// ---- Touren / Panel-Kopf -----------------------------------------------------
function currentTrip() {
  return state.trips.find((t) => t.id == state.tripId) || null;
}

// Reise gegen versehentliches Ändern gesperrt? (jederzeit im ⚙️ umschaltbar)
function isLocked() {
  const t = currentTrip();
  return !!(t && t.gesperrt);
}
function lockAlert() {
  alert("Diese Reise ist abgesperrt. Zum Ändern im Tour-Menü ⚙️ die Sperre lösen.");
}

function renderTourMenu() {
  const menu = document.getElementById("tourMenu");
  menu.innerHTML = "";
  state.trips.forEach((t) => {
    const b = document.createElement("button");
    b.className = "tour-menu-item" + (t.id === state.tripId ? " active" : "");
    b.textContent = t.name;
    b.onclick = () => { menu.classList.add("hidden"); selectTrip(t.id); };
    menu.appendChild(b);
  });
}

function updatePanelHeader() {
  const t = currentTrip();
  const lock = t && t.gesperrt ? "🔒 " : "";
  document.getElementById("tourNameLabel").textContent = t ? lock + t.name : "–";
  document.getElementById("tripTitle").textContent = t ? lock + t.name : "–";
  const dd = document.getElementById("tripDates");
  if (dd) {
    dd.textContent = t && (t.start_datum || t.end_datum)
      ? `🗓 Abfahrt ${t.start_datum || "?"} · Rückkehr ${t.end_datum || "?"}` : "";
  }
}

async function selectTrip(id) {
  state.tripId = id;
  await loadStops();
  renderTourMenu();
}

// ---- Daten laden -------------------------------------------------------------
async function loadTrips() {
  state.trips = await api.get("/api/trips");
  if (state.trips.length) {
    // Default: zuletzt beplante Reise (jüngstes updated_at)
    const latest = state.trips.reduce((a, b) =>
      (b.updated_at || "") > (a.updated_at || "") ? b : a);
    state.tripId = latest.id;
    await loadStops();
  } else {
    state.tripId = null;
    document.getElementById("tourNameLabel").textContent = "Keine Reise";
    document.getElementById("tripTitle").textContent = "Noch keine Reise – ＋";
  }
  renderTourMenu();
}

async function loadStops() {
  if (!state.tripId) return;
  const all = await api.get(`/api/trips/${state.tripId}/stops`);
  state.stops = all.filter((s) => (s.kind || "stop") === "stop"); // Übernachtungsplätze
  state.pois = all.filter((s) => s.kind === "poi");                // reine Punkte
  // Route = Übernachtungen + in_route-POIs, gemeinsam nach reihenfolge sortiert
  state.route = [...state.stops, ...state.pois.filter((p) => p.in_route)]
    .sort((a, b) => (a.reihenfolge - b.reihenfolge) || (a.id - b.id));
  updatePanelHeader();
  renderMarkers();
  renderList();
  renderPoiList();
  const pts = [...state.stops, ...state.pois];
  if (pts.length === 1) {
    map.setCenter({ lat: pts[0].lat, lng: pts[0].lng });
    map.setZoom(11);
  } else if (pts.length > 1) {
    const b = new google.maps.LatLngBounds();
    pts.forEach((s) => b.extend({ lat: s.lat, lng: s.lng }));
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
  { key: "name",           label: "Name",           type: "text",     required: true, poi: true },
  { key: "status",         label: "Status",         type: "select",
    options: ["geplant", "reserviert", "besucht"], default: "geplant" },
  { key: "datum",          label: "Datum",          type: "date" },
  { key: "notiz",          label: "Notiz",          type: "textarea", poi: true },
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
    row.dataset.key = f.key;
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
      document.getElementById(fieldId(f.key)).onchange = () => {
        applyShowIf(f.key);
        if (f.key === "reserviert") syncStatusFromReserviert();
      };
    });
}

// "reserviert"-Checkbox an -> Status auf "reserviert"; aus -> zurück auf
// "geplant" (nur wenn er auf reserviert stand, "besucht" bleibt erhalten).
function syncStatusFromReserviert() {
  const statusEl = document.getElementById("f_status");
  if (!statusEl) return;
  if (document.getElementById("f_reserviert").checked) {
    statusEl.value = "reserviert";
  } else if (statusEl.value === "reserviert") {
    statusEl.value = "geplant";
  }
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
  const isPoi = !!(stop && stop.kind === "poi");
  document.getElementById("formTitle").textContent =
    stop ? (isPoi ? "Punkt bearbeiten" : "Stopp bearbeiten") : "Neuer Stopp";
  STOP_FIELDS.forEach((f) => {
    const el = document.getElementById(fieldId(f.key));
    const val = stop ? stop[f.key] : undefined;
    if (f.type === "checkbox") el.checked = !!val;
    else if (f.type === "datetime") el.value = toDTLocal(val);
    else el.value = val ?? f.default ?? "";
    // Bei POIs nur POI-relevante Felder (Name, Notiz) zeigen
    const row = el.closest(".field-row");
    if (row) row.style.display = (isPoi && !f.poi) ? "none" : "";
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
  if (isLocked()) { lockAlert(); return; }
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
  // "ab" (Reservierungsende) darf nicht vor "an" (Reservierungsbeginn) liegen
  if (payload.reserviert && payload.reserviert_von && payload.reserviert_bis &&
      new Date(payload.reserviert_bis) < new Date(payload.reserviert_von)) {
    alert('Das "ab"-Datum/-Uhrzeit darf nicht vor dem "an"-Datum/-Uhrzeit liegen.');
    return;
  }
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
  if (isLocked()) { lockAlert(); return; }
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
    const kindSel = document.getElementById("c_kind");
    input.value = name;
    kindSel.value = "stop";
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
      done({ name: v, kind: kindSel.value });
    };
    cancel.onclick = () => done(null);
    input.onkeydown = (ev) => { if (ev.key === "Enter") { ev.preventDefault(); ok.onclick(); } };
    modal.classList.remove("hidden");
  });
}

// ---- Tour-Einstellungen (Start-/Zieladresse + Datum) ------------------------
function openTourForm() {
  const t = currentTrip();
  if (!t) { alert("Bitte zuerst eine Reise anlegen (＋)."); return; }
  document.getElementById("t_name").value = t.name || "";
  document.getElementById("t_start").value = t.start_address || "";
  document.getElementById("t_end").value = t.end_address || "";
  document.getElementById("t_abfahrt").value = t.start_datum || "";
  document.getElementById("t_rueckkehr").value = t.end_datum || "";
  document.getElementById("t_gesperrt").checked = !!t.gesperrt;
  document.getElementById("t_status").textContent = "";
  document.getElementById("tourForm").classList.remove("hidden");
}
function closeTourForm() { document.getElementById("tourForm").classList.add("hidden"); }

// Google-Geocoder server-seitig (/api/geocode). null -> Fallback (OSM).
async function googleGeocode(query) {
  try {
    const d = await api.get("/api/geocode?q=" + encodeURIComponent(query));
    const res = (d && d.results) || [];
    return res.length ? res : null;
  } catch { return null; }
}

// Google Places Text-Suche server-seitig (/api/places). Firmen/Campingplätze + Adressen.
async function googlePlacesSearch(query) {
  try {
    const d = await api.send("POST", "/api/places", { textQuery: query, maxResultCount: 6 });
    return (d.places || []).map((p) => ({
      display: (p.name ? p.name + " · " : "") + (p.address || ""),
      name: p.name || String(p.address || "").split(",")[0],
      lat: p.lat, lng: p.lng,
    }));
  } catch { return []; }
}

// OpenStreetMap/Nominatim-Suche (gratis, Fallback).
async function nominatimSearch(query) {
  try {
    const url = "https://nominatim.openstreetmap.org/search?format=jsonv2&limit=5&accept-language=de&q="
      + encodeURIComponent(query);
    const r = await fetch(url, { headers: { Accept: "application/json" } });
    const res = r.ok ? await r.json() : [];
    return res.map((x) => ({
      display: x.display_name,
      name: x.name || String(x.display_name || "").split(",")[0],
      lat: parseFloat(x.lat), lng: parseFloat(x.lon),
    }));
  } catch { return []; }
}

// Ortssuche: Places (Namen+Adressen) -> Geocoder (Adressen) -> OSM (Fallback).
async function searchPlaces(query) {
  let r = await googlePlacesSearch(query);
  if (r.length) return r;
  r = await googleGeocode(query);
  if (r && r.length) return r;
  return await nominatimSearch(query);
}

// Adresse -> {lat,lng} (für Tour-Start/Ziel), gleiche Google-zuerst-Logik.
async function forwardGeocode(address) {
  const list = await searchPlaces(address);
  return list.length ? { lat: list[0].lat, lng: list[0].lng } : null;
}

// ---- Suche ENTLANG DER ROUTE (Stellplätze/Campingplätze) --------------------
// Quellen: OpenStreetMap/Overpass (gratis, camper-spezifisch) + Google Places
// searchAlongRoute (nach Umweg sortiert). Ergebnisse als temporäre Pins.
function clearSearchMarkers() {
  searchMarkers.forEach((m) => m.setMap(null));
  searchMarkers = [];
}

// Luftlinie zweier Punkte in km (Dedup/Filter)
function haversine(a, b) {
  const R = 6371, toRad = (x) => (x * Math.PI) / 180;
  const dLat = toRad(b.lat - a.lat), dLng = toRad(b.lng - a.lng);
  const s = Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(a.lat)) * Math.cos(toRad(b.lat)) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(s));
}

// Punktfolge entlang der Route: bevorzugt die gezeichneten Etappen-Linien
// (echter Straßenverlauf), sonst Luftlinie über Start/Stopps/Ziel. Downsampling.
function getRoutePath() {
  let pts = [];
  if (routePolylines.length) {
    routePolylines.forEach((pl) =>
      pl.getPath().forEach((ll) => pts.push({ lat: ll.lat(), lng: ll.lng() })));
  } else {
    const t = currentTrip() || {};
    if (t.start_lat != null) pts.push({ lat: t.start_lat, lng: t.start_lng });
    state.route.forEach((s) => pts.push({ lat: s.lat, lng: s.lng }));
    if (t.end_lat != null) pts.push({ lat: t.end_lat, lng: t.end_lng });
  }
  return downsample(pts);
}

// Punktzahl auf ~max begrenzen (Overpass/encodePath schlank halten)
function downsample(pts, max = 60) {
  if (pts.length <= max) return pts;
  const step = Math.ceil(pts.length / max);
  return pts.filter((_, i) => i % step === 0 || i === pts.length - 1);
}

// Suchpfad: gewählte Etappe (routeLegSelect) ODER ganze Route
function getSearchPath() {
  const sel = document.getElementById("routeLegSelect");
  const v = sel ? sel.value : "all";
  if (v !== "all" && routeSegments[+v]) return downsample(routeSegments[+v].path.slice());
  return getRoutePath();
}

// OSM/Overpass: Camp-/Wohnmobilplätze im Korridor um die Route (around: Polyline)
async function overpassCampsites(points) {
  if (points.length < 2) return [];
  const around = "around:8000," +
    points.map((p) => `${p.lat.toFixed(5)},${p.lng.toFixed(5)}`).join(",");
  // Camping-/Wohnmobilplätze inkl. der deutschen Variante "amenity=parking +
  // caravans/motorhome" (viele Stellplätze sind nicht als caravan_site getaggt).
  const q = `[out:json][timeout:25];(`
    + `nwr["tourism"~"^(camp_site|caravan_site)$"](${around});`
    + `nwr["amenity"="parking"]["caravans"="yes"](${around});`
    + `nwr["amenity"="parking"]["motorhome"~"^(yes|designated)$"](${around});`
    + `);out center 80;`;
  // Mehrere Overpass-Instanzen probieren (die Haupt-Instanz liefert oft 504/429).
  const endpoints = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
  ];
  let d = null;
  for (const url of endpoints) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 12000); // öffentl. Overpass ist oft träge; nicht-blockierend (Google zeigt zuerst)
    try {
      const r = await fetch(url, {
        method: "POST", body: "data=" + encodeURIComponent(q),
        headers: { "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json" },
        signal: ctrl.signal,
      });
      if (r.ok) { d = await r.json(); clearTimeout(timer); break; }
    } catch { /* Timeout/Fehler -> nächste Instanz */ }
    clearTimeout(timer);
  }
  if (!d) return [];
  try {
    return (d.elements || []).map((e) => {
      const lat = e.lat ?? (e.center && e.center.lat);
      const lng = e.lon ?? (e.center && e.center.lon);
      if (lat == null || lng == null) return null;
      const tags = e.tags || {};
      const info = tags.tourism === "caravan_site" ? "Wohnmobil-Stellplatz"
        : tags.tourism === "camp_site" ? "Campingplatz"
        : "Wohnmobil-Parkplatz";
      return { name: tags.name || info, lat, lng, source: "osm", info };
    }).filter(Boolean);
  } catch { return []; }
}

// Google Places-Suche entlang der Route – server-seitig (/api/places mit points).
async function googleAlongRoute(points) {
  if (points.length < 2) return [];
  try {
    const d = await api.send("POST", "/api/places", {
      textQuery: "Wohnmobilstellplatz Campingplatz", points,
    });
    return (d.places || []).map((p) => ({
      name: p.name || "Campingplatz", lat: p.lat, lng: p.lng,
      source: "google", info: p.address || "",
    }));
  } catch { return []; }
}

async function searchAlongRoute() {
  const box = document.getElementById("searchResults");
  const sel = document.getElementById("routeLegSelect");
  const legLabel = (sel && sel.value !== "all" && routeSegments[+sel.value])
    ? `Etappe „${routeSegments[+sel.value].label}"` : "der Route";
  const path = getSearchPath();
  if (path.length < 2) {
    alert("Bitte zuerst eine Route anlegen (Start/Ziel + mindestens ein Stopp).");
    return;
  }
  box.innerHTML = `<div class="search-hint">Suche Stellplätze entlang ${escapeHtml(legLabel)} …</div>`;
  clearSearchMarkers();
  const dedupe = (arr) => {
    const out = [];
    for (const c of arr) if (!out.some((m) => haversine(m, c) < 0.2)) out.push(c);
    return out;
  };
  // OSM (kann langsam sein) im Hintergrund; Google zuerst sofort anzeigen.
  const osmPromise = overpassCampsites(path);
  const goog = await googleAlongRoute(path);
  if (goog.length) renderCampResults(dedupe(goog), legLabel);
  else box.innerHTML = `<div class="search-hint">Suche Stellplätze (OpenStreetMap) …</div>`;
  const osm = await osmPromise;
  const full = dedupe([...goog, ...osm]);
  clearSearchMarkers();
  if (!full.length) {
    box.innerHTML = `<div class="search-hint">Keine Stellplätze entlang ${escapeHtml(legLabel)} gefunden.</div>`;
    return;
  }
  renderCampResults(full, legLabel);
}

function renderCampResults(list, label) {
  const box = document.getElementById("searchResults");
  // Ein-/Ausklapp-Zustand über das Google->OSM-Nachladen hinweg beibehalten.
  const prev = box.querySelector("details.camp-results");
  const wasOpen = prev ? prev.open : true;
  box.innerHTML = "";

  const det = document.createElement("details");
  det.className = "camp-results";
  det.open = wasOpen;
  const sum = document.createElement("summary");
  sum.innerHTML =
    `<span class="camp-sum-text">🏕 ${list.length} Stellplätze entlang ${escapeHtml(label || "der Route")}</span>` +
    `<button class="camp-clear" title="Ergebnisse & Pins entfernen">✕</button>`;
  det.appendChild(sum);

  const wrap = document.createElement("div");
  wrap.className = "camp-list";
  wrap.innerHTML = `<div class="search-hint">Als Übernachtung 🛏 oder Punkt 📍 übernehmen:</div>`;
  list.forEach((res) => {
    const marker = new google.maps.Marker({
      position: { lat: res.lat, lng: res.lng }, map, title: res.name,
      icon: {
        path: google.maps.SymbolPath.CIRCLE, fillColor: "#d97706",
        fillOpacity: 1, strokeColor: "#fff", strokeWeight: 2, scale: 6,
      },
    });
    marker.addListener("click", () => map.panTo({ lat: res.lat, lng: res.lng }));
    searchMarkers.push(marker);
    const row = document.createElement("div");
    row.className = "search-result";
    const info = res.info ? ` · <span class="sr-info">${escapeHtml(res.info)}</span>` : "";
    row.innerHTML =
      `<div class="sr-name">🏕 ${escapeHtml(res.name)}${info}</div>` +
      `<div class="sr-actions">` +
        `<button data-k="stop" title="Als Übernachtungsplatz">🛏</button>` +
        `<button data-k="poi" title="Als Punkt (POI)">📍</button>` +
      `</div>`;
    row.querySelector(".sr-name").onclick = () => {
      map.panTo({ lat: res.lat, lng: res.lng }); map.setZoom(Math.max(map.getZoom(), 12));
    };
    row.querySelector('[data-k="stop"]').onclick = () => addSearchResult(res.name, res.lat, res.lng, "stop");
    row.querySelector('[data-k="poi"]').onclick = () => addSearchResult(res.name, res.lat, res.lng, "poi");
    wrap.appendChild(row);
  });
  det.appendChild(wrap);
  box.appendChild(det);

  sum.querySelector(".camp-clear").onclick = (e) => {
    e.preventDefault(); e.stopPropagation();  // nicht auf-/zuklappen, sondern wegräumen
    clearSearchMarkers();
    box.innerHTML = "";
  };
}

// ---- Ort-Suche (Nominatim) -> als Übernachtungsplatz oder POI hinzufügen -----
async function doSearch() {
  const q = document.getElementById("searchInput").value.trim();
  const box = document.getElementById("searchResults");
  clearSearchMarkers();
  if (!q) { box.innerHTML = ""; return; }
  box.innerHTML = `<div class="search-hint">Suche …</div>`;
  const results = await searchPlaces(q);
  if (!results.length) { box.innerHTML = `<div class="search-hint">Nichts gefunden.</div>`; return; }
  box.innerHTML = "";
  results.forEach((res) => {
    const row = document.createElement("div");
    row.className = "search-result";
    row.innerHTML =
      `<div class="sr-name">${escapeHtml(res.display)}</div>` +
      `<div class="sr-actions">` +
        `<button data-k="stop" title="Als Übernachtungsplatz hinzufügen">🛏</button>` +
        `<button data-k="poi" title="Als Punkt (POI) hinzufügen">📍</button>` +
      `</div>`;
    row.querySelector(".sr-name").onclick = () => {
      map.panTo({ lat: res.lat, lng: res.lng }); map.setZoom(Math.max(map.getZoom(), 12));
    };
    row.querySelector('[data-k="stop"]').onclick = () => addSearchResult(res.name, res.lat, res.lng, "stop");
    row.querySelector('[data-k="poi"]').onclick = () => addSearchResult(res.name, res.lat, res.lng, "poi");
    box.appendChild(row);
  });
}

async function addSearchResult(name, lat, lng, kind) {
  if (!state.tripId) { alert("Bitte zuerst eine Reise anlegen (＋)."); return; }
  if (isLocked()) { lockAlert(); return; }
  try {
    await api.send("POST", `/api/trips/${state.tripId}/stops`,
      { name, lat, lng, status: "geplant", kind });
    clearSearchMarkers();
    document.getElementById("searchResults").innerHTML = "";
    document.getElementById("searchInput").value = "";
    await loadStops();
  } catch (e) {
    alert("Hinzufügen fehlgeschlagen: " + e.message);
  }
}

async function saveTourForm() {
  const t = currentTrip();
  if (!t) return;
  const name = document.getElementById("t_name").value.trim();
  if (!name) { alert("Bitte einen Namen eingeben."); return; }
  const startAddr = document.getElementById("t_start").value.trim();
  let endAddr = document.getElementById("t_end").value.trim();
  if (!endAddr && startAddr) endAddr = startAddr; // leer = Rundreise (Ziel = Start)
  const statusEl = document.getElementById("t_status");
  const payload = {
    name,
    start_datum: document.getElementById("t_abfahrt").value || null,
    end_datum: document.getElementById("t_rueckkehr").value || null,
    start_address: startAddr || null,
    end_address: endAddr || null,
    start_lat: null, start_lng: null, end_lat: null, end_lng: null,
    gesperrt: document.getElementById("t_gesperrt").checked,
  };
  // Geocodieren; unveränderte Adressen behalten ihre vorhandenen Koordinaten.
  if (startAddr) {
    let g = (startAddr === t.start_address && t.start_lat != null)
      ? { lat: t.start_lat, lng: t.start_lng } : null;
    if (!g) { statusEl.textContent = "Startadresse wird gesucht …"; g = await forwardGeocode(startAddr); }
    if (!g) { statusEl.textContent = "⚠️ Startadresse nicht gefunden"; return; }
    payload.start_lat = g.lat; payload.start_lng = g.lng;
  }
  if (endAddr) {
    let g = (endAddr === t.end_address && t.end_lat != null) ? { lat: t.end_lat, lng: t.end_lng }
      : (endAddr === startAddr && payload.start_lat != null) ? { lat: payload.start_lat, lng: payload.start_lng }
      : null;
    if (!g) { statusEl.textContent = "Zieladresse wird gesucht …"; g = await forwardGeocode(endAddr); }
    if (!g) { statusEl.textContent = "⚠️ Zieladresse nicht gefunden"; return; }
    payload.end_lat = g.lat; payload.end_lng = g.lng;
  }
  try {
    await api.send("PATCH", `/api/trips/${t.id}`, payload);
    Object.assign(t, payload); // lokalen Trip-Cache aktualisieren
    closeTourForm();
    updatePanelHeader();
    renderTourMenu();
    renderMarkers();  // Sperre wirkt sofort (Marker (nicht) ziehbar)
    renderList();     // Drag-Handle/Sortable an Sperre anpassen
    computeDistances();
  } catch (e) { statusEl.textContent = "Speichern fehlgeschlagen: " + e.message; }
}

// ---- UI-Verdrahtung ----------------------------------------------------------
document.getElementById("tourMenuBtn").onclick = (e) => {
  e.stopPropagation();
  document.getElementById("tourMenu").classList.toggle("hidden");
};
document.addEventListener("click", (e) => {
  const menu = document.getElementById("tourMenu");
  const btn = document.getElementById("tourMenuBtn");
  if (!menu.classList.contains("hidden") && !menu.contains(e.target) && !btn.contains(e.target)) {
    menu.classList.add("hidden");
  }
});
document.getElementById("tourEditBtn").onclick = openTourForm;
document.getElementById("t_cancel").onclick = closeTourForm;
document.getElementById("t_save").onclick = saveTourForm;
document.getElementById("searchBtn").onclick = doSearch;
document.getElementById("searchInput").onkeydown = (e) => {
  if (e.key === "Enter") { e.preventDefault(); doSearch(); }
};
document.getElementById("routeSearchBtn").onclick = searchAlongRoute;
document.getElementById("newTripBtn").onclick = async () => {
  const name = prompt("Name der neuen Reise?");
  if (!name) return;
  await api.send("POST", "/api/trips", { name: name.trim() });
  await loadTrips(); // lädt neu, wählt die neueste (= neue) Reise
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
