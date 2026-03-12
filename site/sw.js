// A simple service worker to pass PWA install requirements
self.addEventListener('fetch', function(event) {
  // We aren't doing offline caching yet, just passing requests through
  event.respondWith(fetch(event.request));
});