const CACHE_NAME = "global-signal-scanner-shell-v4";
const SHELL = [
  "./",
  "./panel_escaner_vantage.html",
  "./manifest.webmanifest",
  "./icon-192.png",
  "./icon-512.png",
  "./global-signal-header.jpg",
  "./global_signal_scanner_footer.png"
];

self.addEventListener("install", event => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE_NAME);
    // Cachea cada recurso por separado: un archivo opcional ausente
    // no debe impedir la instalación completa del Service Worker.
    await Promise.all(SHELL.map(async path => {
      try {
        const response = await fetch(path, { cache: "reload" });
        if (response.ok) await cache.put(path, response);
        else console.warn("No se pudo precargar:", path, response.status);
      } catch (error) {
        console.warn("No se pudo precargar:", path, error);
      }
    }));
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", event => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(
      keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key))
    );
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", event => {
  const request = event.request;
  const url = new URL(request.url);

  if (request.method !== "GET" || url.origin !== self.location.origin) return;

  if (request.mode === "navigate" || url.pathname.endsWith(".html")) {
    event.respondWith((async () => {
      try {
        const response = await fetch(request);
        if (response.ok) {
          const cache = await caches.open(CACHE_NAME);
          await cache.put(request, response.clone());
        }
        return response;
      } catch {
        return (await caches.match(request))
          || (await caches.match("./panel_escaner_vantage.html"));
      }
    })());
    return;
  }

  event.respondWith((async () => {
    const cached = await caches.match(request);
    if (cached) return cached;

    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(CACHE_NAME);
      await cache.put(request, response.clone());
    }
    return response;
  })());
});
