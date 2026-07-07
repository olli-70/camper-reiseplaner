// Service Worker – Offline-Read-Cache (Ansehen offline, Bearbeiten nur online).
const CACHE = "camper-v9";
const SHELL = [
  "/",
  "/index.html",
  "/style.css",
  "/app.js",
  "/manifest.webmanifest",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
  "https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js",
  "https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css",
  "https://unpkg.com/sortablejs@1.15.6/Sortable.min.js",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return; // Schreibzugriffe nur online

  const url = new URL(req.url);
  const isApi = url.pathname.startsWith("/api/");
  const isTiles = url.hostname.endsWith("openfreemap.org");

  if (isApi) {
    // Netzwerk zuerst, damit Daten frisch sind; offline aus Cache lesen.
    e.respondWith(
      fetch(req)
        .then((res) => { caches.open(CACHE).then((c) => c.put(req, res.clone())); return res; })
        .catch(() => caches.match(req))
    );
    return;
  }

  if (isTiles) {
    // Karten-Kacheln: Cache zuerst, im Hintergrund auffrischen (stale-while-revalidate).
    e.respondWith(
      caches.open(CACHE).then(async (c) => {
        const hit = await c.match(req);
        const net = fetch(req).then((res) => { c.put(req, res.clone()); return res; }).catch(() => hit);
        return hit || net;
      })
    );
    return;
  }

  // App-Shell: Cache zuerst, sonst Netzwerk.
  e.respondWith(caches.match(req).then((hit) => hit || fetch(req)));
});
