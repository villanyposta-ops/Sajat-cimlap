// A Kezdőlap – háttérszkript: a címlapot mindig a hálózatról kéri, de elmenti, hogy offline is megnyíljon
const TAR = 'kezdolap-v2';
const ALAP = ['./', 'manifest.webmanifest', 'ikon-hirlap-192.png', 'ikon-hirlap-512.png', 'ikon-hirlap-maskable.png'];
self.addEventListener('install', e => {
  e.waitUntil(caches.open(TAR).then(c => c.addAll(ALAP)).catch(() => {}));
  self.skipWaiting();
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(k => Promise.all(k.filter(n => n !== TAR).map(n => caches.delete(n))))
    .then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const r = e.request;
  if (r.method !== 'GET') return;
  const u = new URL(r.url);
  if (u.origin === location.origin) {
    if (u.pathname.endsWith('frissites.json')) return;            // mindig a hálózatról
    if (r.mode === 'navigate' || u.pathname.endsWith('/') || u.pathname.endsWith('.html')) {
      e.respondWith(fetch(r).then(v => {                          // előbb a friss változat…
        const m = v.clone(); caches.open(TAR).then(c => c.put('./', m)); return v;
      }).catch(() => caches.match('./')));                         // …offline az elmentett
      return;
    }
    e.respondWith(caches.match(r).then(m => m || fetch(r)));
    return;
  }
  if (u.hostname === 'fonts.googleapis.com' || u.hostname === 'fonts.gstatic.com') {
    e.respondWith(caches.open(TAR).then(c => c.match(r).then(m => {
      const h = fetch(r).then(v => { if (v.ok) c.put(r, v.clone()); return v; }).catch(() => m);
      return m || h;
    })));
  }
});
