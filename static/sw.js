// Makes the app installable and gives a friendly page when there's no
// connection. It deliberately caches NOTHING else: this is a live ops tool
// (battery levels, router status, who was texted), so a stale screen or stale
// API answer is worse than an error. Every request goes to the network; only
// a failed page navigation falls back to the offline page.
const CACHE = "jasiri-ops-shell-v1";
const OFFLINE_URL = "/static/offline.html";

self.addEventListener("install", (event) => {
    event.waitUntil(caches.open(CACHE).then((c) => c.add(OFFLINE_URL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
    event.waitUntil(
        caches.keys()
            .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
            .then(() => self.clients.claim())
    );
});

self.addEventListener("fetch", (event) => {
    if (event.request.mode !== "navigate") return;
    event.respondWith(fetch(event.request).catch(() => caches.match(OFFLINE_URL)));
});
