<!-- /sw.js -->
importScripts('https://storage.googleapis.com/workbox-cdn/releases/6.5.4/workbox-sw.js');
workbox.setConfig({ debug: false });
workbox.routing.registerRoute(
  /\.(?:png|webp|avif|jpg|jpeg|svg|ico)$/,
  new workbox.strategies.StaleWhileRevalidate({
    cacheName: 'images',
    plugins: [
      new workbox.expiration.ExpirationPlugin({ maxEntries: 50, maxAgeSeconds: 30 * 24 * 60 * 60 }),
    ],
  })
);
workbox.routing.registerRoute(
  /\.(?:js|css)$/,
  new workbox.strategies.StaleWhileRevalidate({ cacheName: 'static' })
);
workbox.routing.registerRoute(
  new RegExp('https://fonts.(?:googleapis|gstatic).com/.*'),
  new workbox.strategies.CacheFirst({
    cacheName: 'fonts',
    plugins: [
      new workbox.expiration.ExpirationPlugin({ maxEntries: 10, maxAgeSeconds: 60 * 60 * 24 * 365 }),
    ],
  })
);
workbox.routing.registerRoute(
  '/',
  new workbox.strategies.NetworkFirst({ cacheName: 'html' })
);
self.addEventListener('install', (e) => {
  // why: cache shell & critical assets for instant repeat visits
  e.waitUntil(
    caches.open('zenseek-v5').then(cache => cache.addAll([
      '/',       '/manifest.webmanifest',
      '/images/main.png',
      '/images/main.png'
    ])).catch(()=>{})
  );
});

self.addEventListener('activate', (e) => {
  // why: cleanup old caches and clear html cache to get fresh content
  e.waitUntil(
    caches.keys().then(keys => Promise.all(keys.map(k => caches.delete(k))))
  );
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  const url = new URL(req.url);

  // Network-first for HTML (fresh content)
  if (req.mode === 'navigate' || (req.headers.get('accept') || '').includes('text/html')) {
    e.respondWith(
      fetch(req).then(resp => {
        const copy = resp.clone();
        caches.open('zenseek-v5').then(c => c.put(req, copy));
        return resp;
      }).catch(() => caches.match(req))
    );
    return;
  }

  // Cache-first for same-origin static
  if (url.origin === location.origin) {
    e.respondWith(
      caches.match(req).then(cached => cached || fetch(req).then(resp => {
        const copy = resp.clone();
        caches.open('zenseek-v5').then(c => c.put(req, copy));
        return resp;
      }))
    );
  }
});