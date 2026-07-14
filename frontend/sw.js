// Service Worker – Offline-Read-Cache (Ansehen offline, Bearbeiten nur online).
// Hinweis: Die Google-Maps-Karte selbst braucht Netz (kein Offline-Kartenbild).
const CACHE = "camper-v60";
const SHELL = [
  "/",
  "/index.html",
  "/style.css",
  "/app.js",
  "/manifest.webmanifest",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
  "/vendor/Sortable.min.js",   // S4: lokal statt unpkg-CDN
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

  // S8: Authentifizierte API-Antworten NIE cachen (sonst liest die nächste Person
  // auf einem geteilten Gerät gecachte Reise-/Kontodaten offline). /api/ läuft
  // ausschließlich über das Netz – keine CacheStorage-Beteiligung.
  if (url.pathname.startsWith("/api/")) return;

  // App-Shell: Netzwerk zuerst (immer die neueste Version wenn online), Cache nur
  // als Offline-Fallback. Verhindert, dass ein altes app.js "hängen bleibt".
  e.respondWith(
    fetch(req)
      .then((res) => {
        if (res.ok) caches.open(CACHE).then((c) => c.put(req, res.clone()));
        return res;
      })
      .catch(() => caches.match(req))
  );
});
