// The offline copy. After one visit, the page opens even when the server or the internet is down.
// Only the page itself is kept. Requests to /api/ are never touched.
const C='aypr-v1';
self.addEventListener('install',e=>{self.skipWaiting();e.waitUntil(caches.open(C).then(c=>c.add(new Request('./',{cache:'reload'}))).catch(()=>{}))});
self.addEventListener('activate',e=>e.waitUntil(self.clients.claim()));
self.addEventListener('fetch',e=>{const u=new URL(e.request.url);
 if(e.request.method!=='GET'||e.request.mode!=='navigate'||u.origin!==location.origin||u.pathname.includes('/api/'))return;
 e.respondWith((async()=>{const c=await caches.open(C),ctl=new AbortController(),t=setTimeout(()=>ctl.abort(),4000);
  try{const r=await fetch(e.request,{signal:ctl.signal});if(r.ok)c.put('./',r.clone());return r}
  catch(x){return(await c.match('./'))||Response.error()}finally{clearTimeout(t)}})())});
