// viARMS Progressive Web App Service Worker
const CACHE_NAME = 'viarms-pwa-v2';

// Essential assets to cache for offline usage
const PRECACHE_ASSETS = [
  '/',
  '/pos/',
  '/static/manifest.json',
  '/static/css/styles.css?v=6',
  '/static/js/i18n.js',
  '/static/js/pos.js?v=5',
  '/static/icons/icon.svg',
  'https://unpkg.com/dexie@3.2.4/dist/dexie.min.js',
  'https://unpkg.com/lucide@latest',
  'https://unpkg.com/html5-qrcode'
];

// Install Event - Precache critical shell assets & skip waiting immediately
self.addEventListener('install', (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return Promise.allSettled(
        PRECACHE_ASSETS.map((url) =>
          cache.add(url).catch((err) => {
            console.warn('[SW] Could not precache:', url, err);
          })
        )
      );
    })
  );
});

// Activate Event - Clean up all previous caches and immediately take control
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames.map((cache) => {
          if (cache !== CACHE_NAME) {
            console.log('[SW] Deleting old cache:', cache);
            return caches.delete(cache);
          }
        })
      );
    }).then(() => self.clients.claim())
  );
});

// Fetch Event - Network-first for dynamic navigation/APIs, Cache-first fallback
self.addEventListener('fetch', (event) => {
  const request = event.request;

  // Don't intercept non-GET requests (e.g. POST /pos/checkout/)
  if (request.method !== 'GET') {
    return;
  }

  const url = new URL(request.url);

  // For static assets (CSS, JS, Fonts, Images): Cache-first with background network revalidation
  if (
    url.pathname.startsWith('/static/') ||
    url.hostname.includes('unpkg.com') ||
    url.hostname.includes('cdn.jsdelivr.net')
  ) {
    event.respondWith(
      caches.match(request).then((cachedResponse) => {
        const fetchPromise = fetch(request).then((networkResponse) => {
          if (networkResponse && networkResponse.status === 200) {
            caches.open(CACHE_NAME).then((cache) => cache.put(request, networkResponse.clone()));
          }
          return networkResponse;
        }).catch(() => null);

        // If cached asset exists, return it immediately while fetching fresh in background
        return cachedResponse || fetchPromise;
      })
    );
    return;
  }

  // For Navigation requests (HTML pages like /pos/, /, /dashboard/): Network-first with cache fallback
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request).then((networkResponse) => {
        if (networkResponse && networkResponse.status === 200) {
          const responseClone = networkResponse.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(request, responseClone));
        }
        return networkResponse;
      }).catch(async () => {
        const cached = await caches.match(request);
        if (cached) return cached;
        const posFallback = await caches.match('/pos/');
        if (posFallback) return posFallback;
        return new Response(
          `<!DOCTYPE html><html><head><meta charset="utf-8"><title>viARMS Offline</title><style>body{background:#0a0f1d;color:#fff;font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;text-align:center;}h1{color:#38bdf8;}button{background:#0284c7;color:#fff;border:none;padding:12px 24px;border-radius:8px;font-size:16px;cursor:pointer;margin-top:16px;}</style></head><body><div><h1>You are currently offline</h1><p>viARMS POS is cached and ready. Click below to open POS:</p><button onclick="window.location.href='/pos/'">Open POS Checkout</button></div></body></html>`,
          { headers: { 'Content-Type': 'text/html' } }
        );
      })
    );
    return;
  }

  // Default fetch
  event.respondWith(
    caches.match(request).then((cachedResponse) => cachedResponse || fetch(request))
  );
});

// Background Sync Event (Triggered when network is restored)
self.addEventListener('sync', (event) => {
  if (event.tag === 'viarms-sync-sales') {
    event.waitUntil(
      self.clients.matchAll().then((clients) => {
        clients.forEach((client) => {
          client.postMessage({ type: 'TRIGGER_BACKGROUND_SYNC' });
        });
      })
    );
  }
});
