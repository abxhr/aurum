/* Minimal PWA shell cache. Never caches /api/* — financial data must always be fresh. */
const CACHE = 'wealth-shell-v11';
const SHELL = ['/static/style.css', '/static/app.js', '/static/charts.js',
  '/static/icons/icon-192.png', '/static/icons/icon-512.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).catch(() => {}));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
  );
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (!url.pathname.startsWith('/static/')) return; // only static files are ever cached; pages and data always come from the network
  if (e.request.method !== 'GET') return;
  // network first so a new version shows up immediately; the cache is only the offline fallback
  e.respondWith(
    fetch(e.request).then(resp => {
      if (resp && resp.ok) { const copy = resp.clone(); caches.open(CACHE).then(c => c.put(e.request, copy)); }
      return resp;
    }).catch(() => caches.match(e.request))
  );
});

/* Phone alerts: only ever sent by the server for new statements or a rare market moment. */
self.addEventListener('push', e => {
  let d = {}; try { d = e.data.json(); } catch (_) {}
  e.waitUntil(self.registration.showNotification(d.title || 'Aurum', { body: d.body || '', tag: d.tag, icon: '/static/icons/icon-192.png', data: { url: d.url || '/' } }));
});
self.addEventListener('notificationclick', e => {
  e.notification.close();
  e.waitUntil(clients.matchAll({ type: 'window' }).then(l => l.length ? l[0].focus() : clients.openWindow((e.notification.data || {}).url || '/')));
});
