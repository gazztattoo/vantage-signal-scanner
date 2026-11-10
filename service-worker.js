const CACHE_NAME = "global-signal-scanner-shell-v11";
const SHELL = [
  "./",
  "./panel_escaner_vantage.html",
  "./manifest.webmanifest",
  "./icon-192.png",
  "./icon-512.png",
  "./global-signal-header.jpg"
];

self.addEventListener("install", event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(SHELL))
  );
  self.skipWaiting();
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(
      keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key))
    ))
  );
  self.clients.claim();
});

// Recibe avisos enviados por el servidor mediante Web Push.
self.addEventListener("push", event => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch {
    payload = { body: event.data ? event.data.text() : "" };
  }

  const title = payload.title || "GLOBAL SIGNAL SCANNER";
  const options = {
    body: payload.body || "Hay una actualización de análisis disponible.",
    icon: "./icon-192.png",
    badge: "./icon-192.png",
    tag: payload.tag || "global-signal-scanner",
    renotify: Boolean(payload.renotify),
    data: {
      url: payload.url || "./panel_escaner_vantage.html"
    }
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", event => {
  event.notification.close();
  const targetUrl = new URL(
    event.notification.data?.url || "./panel_escaner_vantage.html",
    self.location.origin
  ).href;

  event.waitUntil((async () => {
    const targetOrigin = new URL(targetUrl).origin;
    if (targetOrigin !== self.location.origin) return;
    const clients = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const client of clients) {
      if (new URL(client.url).origin === self.location.origin && "focus" in client) {
        if (client.url !== targetUrl && "navigate" in client) await client.navigate(targetUrl);
        return client.focus();
      }
    }
    return self.clients.openWindow(targetUrl);
  })());
});

self.addEventListener("fetch", event => {
  const request = event.request;
  const url = new URL(request.url);

  if (request.method !== "GET" || url.origin !== self.location.origin) return;
  if (url.hostname.includes("onrender.com")) return;

  if (request.mode === "navigate" || url.pathname.endsWith(".html")) {
    event.respondWith(
      fetch(request).then(response => {
        const copy = response.clone();
        caches.open(CACHE_NAME).then(cache => cache.put(request, copy));
        return response;
      }).catch(() =>
        caches.match(request).then(
          cached => cached || caches.match("./panel_escaner_vantage.html")
        )
      )
    );
    return;
  }

  event.respondWith(
    caches.match(request).then(cached => cached || fetch(request).then(response => {
      if (response.ok) {
        const copy = response.clone();
        caches.open(CACHE_NAME).then(cache => cache.put(request, copy));
      }
      return response;
    }))
  );
});
