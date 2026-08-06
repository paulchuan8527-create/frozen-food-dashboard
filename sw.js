// ===== 冻品情报站 Service Worker =====
// 版本号随每次部署更新，确保旧缓存自动清理
const CACHE_VERSION = '1786024111';
const CACHE_NAME = 'ff-dashboard-' + CACHE_VERSION;

// 页面和数据走网络优先（始终获取最新内容）
const NETWORK_FIRST = ['./', './index.html', './data.json'];
// 静态资源走缓存优先（变化频率低）
const CACHE_FIRST = ['./manifest.json'];

self.addEventListener('install', e => {
  console.log('[SW] Install v' + CACHE_VERSION);
  e.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      return cache.addAll(CACHE_FIRST);
    })
  );
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  console.log('[SW] Activate v' + CACHE_VERSION);
  e.waitUntil(
    caches.keys().then(keys => {
      return Promise.all(
        keys.filter(k => k !== CACHE_NAME).map(k => {
          console.log('[SW] Deleting old cache:', k);
          return caches.delete(k);
        })
      );
    })
  );
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  const path = url.pathname;

  // 跳过非 GET 请求
  if (e.request.method !== 'GET') return;

  // 跳过 Chrome 扩展请求
  if (e.request.url.startsWith('chrome-extension://')) return;

  e.respondWith(handleFetch(e.request, path));
});

async function handleFetch(request, path) {
  // 策略1: 页面和数据 → 网络优先（保证手机端始终最新）
  const isNetworkFirst = NETWORK_FIRST.some(p =>
    path.endsWith(p) || (p === './' && (path === '/' || path.endsWith('/index.html') || path === ''))
  );

  if (isNetworkFirst) {
    return networkFirst(request);
  }

  // 策略2: 其他资源 → 缓存优先，后台更新
  return staleWhileRevalidate(request);
}

// 网络优先：先尝试网络，失败后回退到缓存
async function networkFirst(request) {
  try {
    const resp = await fetch(request, { cache: 'no-cache' });
    // 更新缓存
    const cache = await caches.open(CACHE_NAME);
    cache.put(request, resp.clone());
    return resp;
  } catch (e) {
    const cached = await caches.match(request);
    if (cached) return cached;
    throw e;
  }
}

// 缓存优先，后台自动更新（stale-while-revalidate）
async function staleWhileRevalidate(request) {
  const cache = await caches.open(CACHE_NAME);
  const cached = await cache.match(request);

  const fetchPromise = fetch(request).then(resp => {
    if (resp.ok && resp.type === 'basic') {
      cache.put(request, resp.clone());
    }
    return resp;
  }).catch(() => null);

  return cached || fetchPromise;
}

// 监听更新消息
self.addEventListener('message', e => {
  if (e.data === 'SKIP_WAITING') {
    self.skipWaiting();
  }
  if (e.data === 'CHECK_UPDATE') {
    checkForUpdate(e.ports[0]);
  }
});

async function checkForUpdate(port) {
  try {
    const resp = await fetch('./data.json', { cache: 'no-cache' });
    const fresh = await resp.json();
    port.postMessage({ updated: fresh.meta.updated });
  } catch (e) {
    port.postMessage({ error: e.message });
  }
}
