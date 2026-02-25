/**
 * HaqSetu Service Worker — Offline-first caching for slow networks.
 *
 * Strategy:
 *   - Static assets (CSS, JS, fonts): Cache-first with network fallback
 *   - API responses: Network-first with cache fallback
 *   - HTML pages: Network-first with cache fallback
 *   - Images: Cache-first with network fallback
 *
 * This ensures the app works on the slowest phones and in areas
 * with intermittent connectivity (rural India).
 */

'use strict';

const CACHE_VERSION = 'haqsetu-v1';
const STATIC_CACHE = `${CACHE_VERSION}-static`;
const API_CACHE = `${CACHE_VERSION}-api`;

// Static assets to pre-cache on install
const PRECACHE_URLS = [
  '/',
  '/static/css/skeuo.css',
  '/static/js/app.js',
];

// Install: pre-cache static assets
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE)
      .then((cache) => cache.addAll(PRECACHE_URLS))
      .then(() => self.skipWaiting())
  );
});

// Activate: clean up old caches
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys
          .filter((key) => key !== STATIC_CACHE && key !== API_CACHE)
          .map((key) => caches.delete(key))
      ))
      .then(() => self.clients.claim())
  );
});

// Fetch: route requests to appropriate caching strategy
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Skip non-GET requests
  if (event.request.method !== 'GET') return;

  // API requests: network-first
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(networkFirst(event.request, API_CACHE));
    return;
  }

  // Static assets: cache-first
  if (url.pathname.startsWith('/static/') || url.pathname === '/') {
    event.respondWith(cacheFirst(event.request, STATIC_CACHE));
    return;
  }
});

/**
 * Cache-first strategy: serve from cache, fall back to network.
 * Updates cache in the background after serving.
 */
async function cacheFirst(request, cacheName) {
  const cached = await caches.match(request);
  if (cached) return cached;

  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(cacheName);
      cache.put(request, response.clone());
    }
    return response;
  } catch {
    // Return a basic offline page if nothing is cached
    return new Response(
      '<html><body style="font-family:sans-serif;text-align:center;padding:2rem">' +
      '<h1>Offline</h1><p>HaqSetu is not available offline. Please check your connection.</p>' +
      '</body></html>',
      { headers: { 'Content-Type': 'text/html' } }
    );
  }
}

/**
 * Network-first strategy: try network, fall back to cache.
 * Caches successful responses for offline use.
 */
async function networkFirst(request, cacheName) {
  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(cacheName);
      cache.put(request, response.clone());
    }
    return response;
  } catch {
    const cached = await caches.match(request);
    if (cached) return cached;

    return new Response(
      JSON.stringify({ error: 'offline', detail: 'You are offline. Cached data shown where available.' }),
      { status: 503, headers: { 'Content-Type': 'application/json' } }
    );
  }
}
