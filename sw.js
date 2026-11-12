const V = "daoyi-v4";
const SHELL = ["./", "index.html", "manifest.webmanifest", "topics.json", "icons/icon-180.png", "icons/icon-192.png", "icons/icon-512.png", "icons/icon-64.png"];
self.addEventListener("install", e => { e.waitUntil(caches.open(V).then(c => c.addAll(SHELL))); self.skipWaiting(); });
self.addEventListener("activate", e => { e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== V).map(k => caches.delete(k))))); self.clients.claim(); });
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET") return;
  if (url.pathname.includes("/audio/")) return; // let the browser stream audio (range requests)
  // stories & page: network first, fall back to cache (offline reading)
  if (url.origin === location.origin && (url.pathname.includes("/stories/") || url.pathname.endsWith("topics.json") || e.request.mode === "navigate")) {
    e.respondWith(fetch(e.request).then(r => { const cp = r.clone(); caches.open(V).then(c => c.put(e.request, cp)); return r; })
      .catch(() => caches.match(e.request, {ignoreSearch: true}).then(r => r || caches.match("index.html"))));
    return;
  }
  // everything else (icons, fonts): cache first
  e.respondWith(caches.match(e.request).then(r => r || fetch(e.request).then(res => {
    if (res.ok || res.type === "opaque") { const cp = res.clone(); caches.open(V).then(c => c.put(e.request, cp)); }
    return res;
  })));
});
