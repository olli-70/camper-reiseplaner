// Service Worker – Offline-Read-Cache (Ansehen offline, Bearbeiten nur online).
// Hinweis: Die Google-Maps-Karte selbst braucht Netz (kein Offline-Kartenbild).
const CACHE = "camper-v38";
const SHELL = [
  "/",
  "/index.html",
  "/style.css",
  "/app.js",
  "/manifest.webmanifest",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
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

  if (url.pathname.startsWith("/api/")) {
    // Netzwerk zuerst, damit Daten frisch sind; offline aus Cache lesen.
    e.respondWith(
      fetch(req)
        .then((res) => { caches.open(CACHE).then((c) => c.put(req, res.clone())); return res; })
        .catch(() => caches.match(req))
    );
    return;
  }

  // App-Shell: Cache zuerst, sonst Netzwerk. Karten-Requests (Google, cross-origin)
  // sind nicht im Cache -> laufen direkt ins Netz, werden nicht zwischengespeichert.
  e.respondWith(caches.match(req).then((hit) => hit || fetch(req)));
});
