const __vite__mapDeps=(i,m=__vite__mapDeps,d=(m.f||(m.f=["assets/index-D7hWhKe6.js","assets/index-DiqwetGY.css"])))=>i.map(i=>d[i]);
(function(){const t=document.createElement("link").relList;if(t&&t.supports&&t.supports("modulepreload"))return;for(const a of document.querySelectorAll('link[rel="modulepreload"]'))r(a);new MutationObserver(a=>{for(const i of a)if(i.type==="childList")for(const o of i.addedNodes)o.tagName==="LINK"&&o.rel==="modulepreload"&&r(o)}).observe(document,{childList:!0,subtree:!0});function n(a){const i={};return a.integrity&&(i.integrity=a.integrity),a.referrerPolicy&&(i.referrerPolicy=a.referrerPolicy),a.crossOrigin==="use-credentials"?i.credentials="include":a.crossOrigin==="anonymous"?i.credentials="omit":i.credentials="same-origin",i}function r(a){if(a.ep)return;a.ep=!0;const i=n(a);fetch(a.href,i)}})();const qe="modulepreload",je=function(e){return"/portal/"+e},ie={},O=function(t,n,r){let a=Promise.resolve();if(n&&n.length>0){document.getElementsByTagName("link");const o=document.querySelector("meta[property=csp-nonce]"),c=(o==null?void 0:o.nonce)||(o==null?void 0:o.getAttribute("nonce"));a=Promise.allSettled(n.map(u=>{if(u=je(u),u in ie)return;ie[u]=!0;const d=u.endsWith(".css"),b=d?'[rel="stylesheet"]':"";if(document.querySelector(`link[href="${u}"]${b}`))return;const g=document.createElement("link");if(g.rel=d?"stylesheet":qe,d||(g.as="script"),g.crossOrigin="",g.href=u,c&&g.setAttribute("nonce",c),document.head.appendChild(g),d)return new Promise((E,W)=>{g.addEventListener("load",E),g.addEventListener("error",()=>W(new Error(`Unable to preload CSS for ${u}`)))})}))}function i(o){const c=new Event("vite:preloadError",{cancelable:!0});if(c.payload=o,window.dispatchEvent(c),!c.defaultPrevented)throw o}return a.then(o=>{for(const c of o||[])c.status==="rejected"&&i(c.reason);return t().catch(i)})},Z=50;function ye(e,t){const n=String(e.ts||"").localeCompare(String(t.ts||""));return n!==0?n:String(e.id||"").localeCompare(String(t.id||""))}function be(e,t,n){return t?String(e.ts||"")!==String(t)?String(e.ts||"")>String(t):n?String(e.id||"")>String(n):!1:!0}function De(e,t,n){return t?String(e.ts||"")!==String(t)?String(e.ts||"")<String(t):n?String(e.id||"")<String(n):!1:!1}function Ge(e,t){return!t||!t.ts?!0:be(e,t.ts,t.id||"")}function H(e,t){const n=new Map;for(const r of e||[])r&&r.id&&n.set(r.id,r);for(const r of t||[])!r||!r.id||n.set(r.id,{...n.get(r.id),...r});return[...n.values()].sort(ye)}function We(e,t={}){const n=[...e||[]].sort(ye),r=t.limit?Number(t.limit):n.length,a=Number.isFinite(r)&&r>0?r:n.length;if(t.before){const i=n.filter(o=>De(o,t.before,t.beforeId||""));return{messages:i.slice(-a),hasOlder:i.length>a,hasNewer:!0}}if(t.since){const i=n.filter(o=>be(o,t.since,t.sinceId||""));return{messages:i.slice(0,a),hasOlder:n.length>i.length,hasNewer:i.length>a}}return{messages:n.slice(-a),hasOlder:n.length>a,hasNewer:!1}}function oe(e,t={}){return Array.isArray(e)?We(e,t):e&&Array.isArray(e.messages)?{messages:e.messages,hasOlder:!!e.hasOlder,hasNewer:!!e.hasNewer}:{messages:[],hasOlder:!1,hasNewer:!1}}function Fe(e,t){return!Array.isArray(e)||e.length===0?-1:e.findIndex(n=>Ge(n,t))}function ve(e){const t=Number(e)||0;return t<=0?"":t===1?"1 new message":`${t} new messages`}function we(e){const t=Number(e)||0;return t<=0?"Latest":ve(t)}function Q(e,t=48){return e?e.scrollHeight-e.scrollTop-e.clientHeight<=t:!0}function $e(e,t=80){return e?e.scrollTop<=t:!1}function Ve(e,t,n){if(!e)return;const r=e.scrollHeight-t;e.scrollTop=n+r}function Se({hidden:e,pinned:t,hasMessages:n}={}){return!!(!e&&t&&n)}function ze(e,t="GSM Hub"){const n=Number(e)||0;return n<=0?t:`(${n}) ${t}`}function Je(e){const t=Number(e)||0;if(t<=0)return null;const r=`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="12" fill="#1a120c"/><circle cx="46" cy="18" r="16" fill="#c4a35a"/><text x="46" y="23" text-anchor="middle" font-size="16" font-family="sans-serif" font-weight="700" fill="#1a120c">${t>99?"99+":String(t)}</text></svg>`;return`data:image/svg+xml,${encodeURIComponent(r)}`}const Ye={},Ke=Ye||{},X=(Ke.VITE_HUB_ORIGIN||"").replace(/\/$/,""),C=`${X}/portal/api`;async function ke(e){const t=await fetch(e);if(!t.ok){const n=await t.text();throw new Error(n||`HTTP ${t.status}`)}return t.json()}async function ee(e,t){try{return{data:await ke(e),mock:!1}}catch{const n=await t();return{data:n.default??n,mock:!0}}}async function R(){return ee(`${X}/health`,()=>O(()=>import("./health-CSdgTVNW.js"),[]))}async function Ze(e){const t=Qe(e);return t?{data:t,mock:!1}:{data:(await O(()=>import("./usage-CNUK0FQm.js"),[])).default,mock:!0}}function Qe(e){return!e||typeof e!="object"?null:e.usage&&typeof e.usage=="object"?e.usage:e.tokens&&typeof e.tokens=="object"?e.tokens:e.openclaw_tokens&&typeof e.openclaw_tokens=="object"?e.openclaw_tokens:null}async function Te(e){const t=e?`?viewer=${encodeURIComponent(e)}`:"",{data:n}=await ee(`${C}/threads${t}`,()=>O(()=>import("./threads-DsUYIr5z.js"),[]));return n}async function te(e,t={}){const n=new URLSearchParams;n.set("peer",e),t.limit&&n.set("limit",String(t.limit)),t.before&&n.set("before",t.before),t.beforeId&&n.set("before_id",t.beforeId),t.since&&n.set("since",t.since),t.sinceId&&n.set("since_id",t.sinceId);try{return oe(await ke(`${C}/messages?${n}`),t)}catch{const a=(await O(()=>import("./messages-OYSZw2Be.js"),[])).default;return oe(a[e]||[],t)}}async function Xe({viewer:e,peer:t,ts:n,id:r}){const a=await fetch(`${C}/messages/read`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({viewer:e,peer:t,ts:n,id:r||""})});if(!a.ok){const i=await a.text();throw new Error(i||`HTTP ${a.status}`)}return a.json()}async function Ee(){const{data:e,mock:t}=await ee(`${C}/calls`,()=>O(()=>import("./calls-DVVb_JZE.js"),[]));return{data:e,mock:t}}function et(e){return`${C}/calls/${encodeURIComponent(e)}/recording`}async function tt(e,t){try{const n=await fetch(`${C}/messages/send`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({to:e,body:t})});if(!n.ok){const r=await n.text();throw new Error(r||`HTTP ${n.status}`)}return n.json()}catch(n){if(X)throw n;return{ok:!0,mock:!0,to:e,body:t}}}function nt(e){const t=new EventSource(`${C}/events`);let n=!1;t.onopen=()=>{n&&e("reconnect",{}),n=!0};for(const r of["message","call","outbox"])t.addEventListener(r,a=>{try{e(r,JSON.parse(a.data))}catch(i){console.warn("sse parse",i)}});return t.onerror=()=>{},t}const A={unread:{}},ce="gsm-portal-viewer";let B="";function Ae(e=A.unread){return Object.values(e||{}).reduce((t,n)=>t+(Number(n)||0),0)}function F(){var e;return(e=globalThis.crypto)!=null&&e.randomUUID?globalThis.crypto.randomUUID():`v-${Date.now().toString(36)}-${Math.random().toString(36).slice(2,10)}`}function st(){try{return globalThis.localStorage}catch{return null}}function ne(e){const t=st();if(!t)return B||(B=F()),B;try{let n=t.getItem(ce);return(!n||!/^[-A-Za-z0-9_]{1,80}$/.test(n))&&(n=F(),t.setItem(ce,n)),n}catch{return B||(B=F()),B}}function _e(e){const t={};for(const n of e||[]){const r=Number(n.unread)||0;r>0&&n.peer&&(t[n.peer]=r)}A.unread=t}async function rt(){const e=await Te(ne());return _e(e),k(),e}function at(e){e&&(A.unread[e]=(A.unread[e]||0)+1)}function Oe(e){e&&delete A.unread[e]}function k(){const e=Ae();if(typeof document>"u")return e;const t=document.getElementById("unread-badge");return t&&(t.hidden=e===0,t.textContent=String(e)),document.querySelectorAll("[data-peer]").forEach(n=>{var o;const r=n.getAttribute("data-peer"),a=A.unread[r]||0;let i=n.querySelector(".thread-unread");if(a<=0){i==null||i.remove();return}i||(i=document.createElement("span"),i.className="thread-unread",(o=n.querySelector(".row-head"))==null||o.appendChild(i)),i.textContent=String(a)}),it(e),e}function it(e){const t=document.documentElement.dataset.baseTitle||document.title||"GSM Hub";document.documentElement.dataset.baseTitle||(document.documentElement.dataset.baseTitle=String(t).replace(/^\(\d+\)\s+/,"")||"GSM Hub"),document.title=ze(e,document.documentElement.dataset.baseTitle);const n=document.querySelector('link[rel="icon"]');if(!n)return;n.dataset.baseHref||(n.dataset.baseHref=n.getAttribute("href")||"");const r=Je(e);n.setAttribute("href",r||n.dataset.baseHref)}function se(e=globalThis.isSecureContext,t=typeof Notification<"u"){return!!(e&&t)}function ot({isSecure:e=globalThis.isSecureContext,permission:t=typeof Notification<"u"?Notification.permission:"denied"}={}){return e&&t==="granted"?"os":"in-app"}function ct(e,{selectedPeer:t,documentHidden:n,route:r}={}){return!e||e.type!=="message"||e.direction==="out"&&e.status==="queued"?!1:!(r==="messaging"&&t&&t===e.peer&&!n)}function V(e,t=140){const n=String(e||"").replace(/\s+/g," ").trim();return n.length<=t?n:`${n.slice(0,t-1)}…`}function lt(e){const t=e.peer||"unknown";return e.direction==="out"?e.status==="failed"||e.status==="timeout"?{title:e.status==="timeout"?`SMS timed out to ${t}`:`SMS failed to ${t}`,body:V(e.error||e.body)}:{title:`SMS sent to ${t}`,body:V(e.body)}:{title:`SMS from ${t}`,body:V(e.body)}}async function ut(){if(!se())throw new Error("OS notifications need a secure context (HTTPS)");return Notification.requestPermission()}async function dt({title:e,body:t,peer:n,tag:r}){var o;const a={body:t,tag:r||`sms-${n||"desk"}`,data:{peer:n||""}},i=await((o=navigator.serviceWorker)==null?void 0:o.ready.catch(()=>null));return i&&typeof i.showNotification=="function"?(await i.showNotification(e,{...a,icon:"./icon-192.png"}),"sw"):(new Notification(e,a),"page")}function le({title:e,body:t,peer:n,onOpen:r}){const a=document.getElementById("toast-stack");if(!a)return;const i=document.createElement("button");i.type="button",i.className="toast",i.innerHTML=`<strong>${ue(e)}</strong><span>${ue(t||"")}</span>`;const o=()=>i.remove();i.addEventListener("click",()=>{r&&r(n),o()}),a.prepend(i),window.setTimeout(o,8e3)}function ue(e){return String(e).replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;")}let de=!1;function ft({onPermission:e}={}){const t=()=>{const n=document.getElementById("notify-btn"),r=document.getElementById("notify-hint");if(!n||!r)return;const a=se(),i=a?Notification.permission:"denied";if(!a){n.hidden=!0,r.hidden=!1,r.textContent="HTTP desk — in-app toasts only. OS alerts need HTTPS.";return}if(i==="granted"){n.hidden=!0,r.hidden=!0;return}if(i==="denied"){n.hidden=!0,r.hidden=!1,r.textContent="Alerts blocked in this browser. In-app toasts still fire.";return}n.hidden=!1,r.hidden=!0,n.textContent="Enable desk alerts"};return t(),de||(de=!0,document.addEventListener("click",async n=>{if(!n.target.closest("#notify-btn"))return;const a=document.getElementById("notify-hint");try{const i=await ut();t(),e&&e(i)}catch(i){a&&(a.hidden=!1,a.textContent=i.message||String(i))}})),t}async function pt(e,t){if(!ct(e,t))return!1;const n=lt(e);if(ot()==="os")try{return await dt({...n,peer:e.peer,tag:e.id}),"os"}catch{return le({...n,peer:e.peer,onOpen:t.onOpen}),"in-app"}return le({...n,peer:e.peer,onOpen:t.onOpen}),"in-app"}const mt={BASE_URL:"/portal/"},gt=mt||{},Ce=(gt.BASE_URL||"/").replace(/\/?$/,"/");function U(){return(location.hash||"").startsWith("#/")}function ht(){if(U()){const n=location.hash.slice(1);return _(n.split("?")[0])}let e=location.pathname||"/";const t=Ce.replace(/\/$/,"")||"";return t&&(e===t||e.startsWith(`${t}/`))&&(e=e.slice(t.length)||"/"),_(e)}function _(e){let t=e||"/";return t.startsWith("/")||(t=`/${t}`),t.length>1&&t.endsWith("/")&&(t=t.slice(0,-1)),t||"/"}function yt(e){const t=_(e);if(t==="/calls"||t.startsWith("/calls/"))return{page:"calls",path:t};if(t==="/messaging"||t.startsWith("/messaging/")){const n=t==="/messaging"?"/":t.slice(10);return{page:"messaging",path:t,rest:_(n)}}return t==="/routing"||t.startsWith("/routing/")?{page:"routing",path:t}:t==="/simulator"||t.startsWith("/simulator/")?{page:"simulator",path:t}:{page:"home",path:"/"}}function L(){return yt(ht())}function T(e){const t=_(e);if(U())return`#${t}`;const n=t==="/"?"":t.replace(/^\//,"");return`${Ce}${n}`}function bt(e){const t=_(e);if(U()){history.replaceState(null,"",`${location.pathname}${location.search}#${t}`);return}history.replaceState({},"",T(t))}function J(e){const t=_(e);if(U()){location.hash=t;return}const n=T(t);if(`${location.pathname}${location.search}`===n&&!location.hash){window.dispatchEvent(new PopStateEvent("popstate"));return}history.pushState({},"",n),window.dispatchEvent(new PopStateEvent("popstate"))}function vt(e){window.addEventListener("hashchange",e),window.addEventListener("popstate",e)}function wt(e){return e?'<span class="badge-mock">Local mock</span>':""}function $t(){const e=se(),t=e&&typeof Notification<"u"?Notification.permission:"denied";return e?t==="granted"?`
      <button id="notify-btn" type="button" class="ghost" hidden>Enable desk alerts</button>
      <p id="notify-hint" class="hint" hidden></p>
    `:t==="denied"?`
      <button id="notify-btn" type="button" class="ghost" hidden>Enable desk alerts</button>
      <p id="notify-hint" class="hint">Alerts blocked in this browser. In-app toasts still fire.</p>
    `:`
    <button id="notify-btn" type="button" class="ghost">Enable desk alerts</button>
    <p id="notify-hint" class="hint" hidden></p>
  `:`
      <button id="notify-btn" type="button" class="ghost" hidden>Enable desk alerts</button>
      <p id="notify-hint" class="hint">HTTP desk — in-app toasts only. OS alerts need HTTPS.</p>
    `}function q({page:e,mock:t,extra:n=""}){const r=Ae(),a=`<span id="unread-badge" class="nav-badge"${r?"":" hidden"}>${r}</span>`,i=(o,c,u)=>{const d=e===o?'aria-current="page"':"",b=o==="messaging"?a:"";return`<a class="cockpit-link${e===o?" on":""}" href="${T(c)}" ${d}>${u}${b}</a>`};return`
    <header class="cockpit-bar">
      <span class="cockpit-mark">GSM Hub</span>
      <nav class="cockpit-nav" aria-label="Operator">
        ${i("home","/","Overview")}
        ${i("messaging","/messaging","Messaging")}
        ${i("calls","/calls","Calls")}
        ${i("routing","/routing","Switchboard")}
        ${i("simulator","/simulator","Simulator")}
      </nav>
      <div class="cockpit-trail">
        ${$t()}
        ${wt(t)}
        ${n}
      </div>
    </header>
  `}function j(e){if(!e)return"";const t=String(e).trim(),n=t.replace(/\D/g,"");return n==="15550100"||n==="15555550100"||n==="5550100"||n==="15555550123"?"Mock +1 555 0100":/555/.test(n)&&n.length>=7?`Mock ${t}`:t}const Y=new Set;let D=!1,re=null,Be={seated:["gsm_bus","openclaw_bus"],echoGsm:!1,deafened:[]};function hn(e){return Y.add(e),()=>Y.delete(e)}function Ie(){const e=St();for(const t of Y)t(e)}function St(){return{active:D,path:re,snapshot:Be}}function yn({seated:e,echoGsm:t,deafened:n}){D||(Be={seated:[...e||[]],echoGsm:!!t,deafened:[...n||[]]})}function bn(e){return e==="loopback"?{seated:["gsm_bus"],echoGsm:!0,deafened:[],mode:"loopback",label:"GSM echo"}:{seated:["gsm_bus","openclaw_bus"],echoGsm:!1,deafened:[],mode:"openclaw",label:"Agent"}}function kt(e){D=!0,re=e,Ie()}function Tt(){D=!1,re=null,Ie()}function Et(){const e="".replace(/\/$/,"");return e||(typeof location<"u"?location.origin:"http://hub.mining-ling.ts.net:8787")}let w=null;function N(e,t){return`<div class="stereo-meter" id="${e}">
    <span class="meter-label">${t}</span>
    <div class="meter-channels">
      <div class="meter-channel" data-channel="l">
        <span class="channel-label">L</span>
        <div class="meter-track" role="meter" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0">
          <div class="meter-fill"></div>
        </div>
      </div>
      <div class="meter-channel" data-channel="r">
        <span class="channel-label">R</span>
        <div class="meter-track" role="meter" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0">
          <div class="meter-fill"></div>
        </div>
      </div>
    </div>
  </div>`}function At({agentOn:e=!0}={}){const t=e?"":"checked",n=e?"checked":"";return`
    <div class="call-sim">
      <header class="routing-head">
        <div>
          <h1>Call simulator</h1>
          <div class="sub">This is the fake handset. Same WebSocket the Pixel uses.</div>
        </div>
      </header>
      <section class="controls card">
        <label>
          Hub URL
          <input id="hub-url" type="url" value="${Et()}" />
        </label>
        <fieldset class="call-path" id="call-path">
          <legend>What the earpiece plays</legend>
          <div class="segmented" role="radiogroup" aria-label="Call path">
            <label class="segment">
              <input type="radio" name="call-path" value="loopback" ${t} />
              <span>GSM echo</span>
            </label>
            <label class="segment">
              <input type="radio" name="call-path" value="openclaw" ${n} />
              <span>Agent</span>
            </label>
          </div>
          <p class="call-path-hint">
            GSM echo is a wire test with nobody else on the line. Agent means OpenClaw is in the room. There is no echo-through-OpenClaw.
          </p>
        </fieldset>
        <label class="checkbox">
          <input id="tone-mode" type="checkbox" />
          Test tone instead of mic (440 Hz)
        </label>
        <div class="buttons">
          <button id="start-btn" type="button" class="primary">Start call</button>
          <button id="end-btn" type="button" disabled>End call</button>
        </div>
      </section>
      <section class="stats card">
        <div><span>Status</span><strong id="status">idle</strong></div>
        <div><span>Mic streaming</span><strong id="mic-status">no</strong></div>
        <div><span>Bytes sent</span><strong id="bytes-sent">0</strong></div>
        <div><span>Bytes received</span><strong id="bytes-recv">0</strong></div>
      </section>
      <section class="audio-meters card" aria-label="Audio level meters">
        <h2>Audio levels</h2>
        ${N("meter-mic","Mic uplink")}
        ${N("meter-tone","Test tone uplink")}
        ${N("meter-downlink","Earpiece (from hub)")}
        ${N("meter-sidetone","Local sidetone")}
      </section>
      <section class="card">
        <h2>Event log</h2>
        <pre id="log"></pre>
      </section>
    </div>
  `}async function _t(e){w==null||w(),w=null;const{mountCallSimulator:t}=await O(async()=>{const{mountCallSimulator:n}=await import("./main-r-Rna2Qa.js");return{mountCallSimulator:n}},[]);w=t(e,{onCallStart:n=>kt(n),onCallEnd:()=>Tt()})}async function Ot(e){const t=await R();e.innerHTML=`
    ${q({page:"simulator",mock:t.mock})}
    ${At({agentOn:!0})}
  `,await _t(e.querySelector(".call-sim"))}function Ct(){w==null||w(),w=null}async function Bt(e){const t=await R(),n=t.data||{},r=await Ee(),a=Array.isArray(r.data)?r.data:[],i=await Ze(t.mock?null:n),o=Lt(a,n.last_call_tap),c=!!(t.mock||r.mock),u=n.mode||"idle";e.innerHTML=`
    ${q({page:"home",mock:c})}
    <main class="dash">
      <div class="dash-col">
        <section class="card">
          <h2>Hub status</h2>
          ${It(n)}
        </section>
        <section class="card">
          <h2>Last call</h2>
          ${o?Mt(o):'<p class="muted">No calls recorded.</p>'}
        </section>
      </div>
      <div class="dash-col">
        <section class="card">
          <h2>Token / usage</h2>
          ${xt(i.data)}
        </section>
      </div>
      <section class="dash-strip">
        <div class="dash-strip-preset">
          <span class="lamp-label">Routing preset</span>
          <span class="lamp-value">${m(String(u).toUpperCase())}</span>
        </div>
        <pre class="teletype dash-teletype">${m(fe(u))}</pre>
        <div class="dash-strip-links">
          <a class="primary-link secondary" href="${T("/routing")}">Open switchboard</a>
          <a class="primary-link secondary" href="${T("/simulator")}">Call simulator</a>
        </div>
      </section>
      <p class="dash-tail">Last preset applied: ${m(String(u).toUpperCase())} · ${m(fe(u))}${o?` · last call ${m(Le(o.started||""))}`:""}</p>
    </main>
  `}function fe(e){return e==="openclaw"?"openclaw_bus:monitor_* |-> gsm_bus:playback_*":e==="loopback"?"(no pw-link; uplink → gsm_bus.monitor)":e==="conference"?"conference star: GSM and OpenClaw; WA/TG listen GSM only":e==="clear"?"(buses idle)":String(e)}function It(e){const t=!!e.ok,n=e.openclaw_talk||"unknown",r=e.mode||"idle";return`<div class="lamp-row">
    <div class="lamp-cell">
      <span class="lamp ${t?"good":"bad"}"></span>
      <span class="lamp-copy">
        <span class="lamp-label">Hub</span>
        <span class="lamp-value">${t?"OK":"Down"}</span>
      </span>
    </div>
    <div class="lamp-cell">
      <span class="lamp ${n==="webrtc-ui"?"good":"warn"}"></span>
      <span class="lamp-copy">
        <span class="lamp-label">Talk path</span>
        <span class="lamp-value">${m(n)}</span>
      </span>
    </div>
    <div class="lamp-cell">
      <span class="lamp preset-lamp"></span>
      <span class="lamp-copy">
        <span class="lamp-label">PipeWire</span>
        <span class="lamp-value">${m(String(r).toUpperCase())}</span>
      </span>
    </div>
  </div>`}function Lt(e,t){const n=e[0]||null,r=Nt(t),a=Pt(t);if(!n&&!t)return null;const i=(n==null?void 0:n.started_at)||(t==null?void 0:t.ended_at)||(t==null?void 0:t.started_at)||null,o=(n==null?void 0:n.duration_sec)??r??null,c=(n==null?void 0:n.number)||a||"";return{started:i,duration:o,number:c,fromTap:!!t&&!n}}function Nt(e){if(!e)return null;const t=e.streams||{};let n=0;for(const a of Object.values(t)){const i=Number(a==null?void 0:a.seconds);i>n&&(n=i)}if(n>0)return Math.round(n);const r=e.mixes||{};for(const a of Object.values(r)){const i=Number(a==null?void 0:a.seconds);i>n&&(n=i)}return n>0?Math.round(n):null}function Pt(e){if(!e)return"";const t=e.meta||{};return t.number||t.peer||t.from||""}function Mt(e){const t=e.started?Le(e.started):"time unknown",n=e.duration!=null?Ht(e.duration):"duration unknown",r=e.number?j(e.number):"number unknown";return`<p class="fixture-num">${m(r)}</p>
    <p class="muted last-call-meta">${m(n)}<br/>${m(t)}</p>`}function xt(e){if(!e)return'<p class="muted">No usage data.</p>';const t=e.label||"Usage",n=e.used==null?"n/a":String(e.used),r=e.limit==null?"n/a":String(e.limit),a=e.note?`<p class="muted">${m(e.note)}</p>`:"";return`<p>${m(t)}</p>
    <div class="kv">
      <span class="lamp-label">Used</span><span class="lamp-value">${m(n)}</span>
      <span class="lamp-label">Limit</span><span class="lamp-value">${m(r)}</span>
    </div>
    ${a}`}function Ht(e){const t=Number(e)||0,n=Math.floor(t/60),r=t%60;return n?`${n}m ${r}s`:`${r}s`}function Le(e){const t=new Date(e);if(Number.isNaN(t.getTime()))return e;const n=Date.now()-t.getTime(),r=Math.abs(n),a=new Intl.RelativeTimeFormat(void 0,{numeric:"auto"}),i=[{amount:60,unit:"second"},{amount:60,unit:"minute"},{amount:24,unit:"hour"},{amount:7,unit:"day"},{amount:4.34524,unit:"week"},{amount:12,unit:"month"},{amount:Number.POSITIVE_INFINITY,unit:"year"}];let o=r/1e3;for(const c of i){if(o<c.amount){const u=Math.round(o)*(n>=0?-1:1);return a.format(u,c.unit)}o/=c.amount}return t.toLocaleString()}function m(e){return String(e).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;")}const P="No OpenClaw recording for this call.",l={calls:[],error:"",mock:!1,playingId:null,playError:""};let f=null,K=null;function Rt(){l.playingId=null,l.playError="",f&&$(f)}function Ut(){l.playingId=null,l.playError=P,f&&$(f)}function qt(e){return K=e,e&&(e.preload="none",e.addEventListener("ended",Rt),e.addEventListener("error",Ut),e)}function jt(){return K||(typeof Audio>"u"?null:qt(new Audio))}function Dt(e,t=jt()){if(l.playError="",!e)return;if(l.playingId===e&&t&&!t.paused){t.pause(),l.playingId=null,f&&$(f);return}if(!t){l.playingId=null,l.playError=P,f&&$(f);return}l.playingId&&l.playingId!==e&&t.pause();const n=et(e);t.src=n;let r;try{r=t.play()}catch{l.playingId=null,l.playError=P,f&&$(f);return}l.playingId=e,f&&$(f),r&&typeof r.then=="function"&&r.catch(()=>{l.playingId===e&&(l.playingId=null,l.playError=P,f&&$(f))})}async function Ne(e){f=e;try{l.error="";const t=await R();l.mock=!!t.mock;const n=await Ee();l.calls=Array.isArray(n.data)?n.data:[],l.mock=l.mock||!!n.mock}catch(t){l.error=t.message||String(t)}$(e)}function Gt(e){f=e}async function Wt(e,t){const n=e||f;n&&t==="call"&&await Ne(n)}function $(e){e.innerHTML=`
    ${q({page:"calls",mock:l.mock})}
    ${l.error?`<div class="error">${y(l.error)}</div>`:""}
    <main>${Ft()}</main>
  `,k(),Vt(e)}function Ft(){return l.calls.length?`<div class="call-list">${l.calls.map(e=>{const t=e.direction!=="out",n=t?"Incoming":"Outgoing",r=e.switchboard_mode||"idle",a=e.path==="public"||e.path==="tailscale"?e.path:"",i=a?`<span class="preset-chip" data-entrance="${z(a)}">${y(a)}</span>`:"",o=l.playingId===e.id,c=e.session_id?`<div class="meta session-id">${y(e.session_id)}</div>`:"",u=pe(e.transcript)?`<div class="meta transcript" title="${z(e.transcript.path||"")}">${y(pe(e.transcript))}</div>`:"",d=o?"Pause":"Play",b=o?"❚❚":"▶";return`<button type="button" class="call-card${o?" playing":""}" data-call-id="${z(e.id)}" aria-pressed="${o?"true":"false"}" title="Play OpenClaw recording">
        <div class="call-card-head">
          <span class="lamp ${t?"good":"warn"}"></span>
          <h2>${y(n)}</h2>
          <span class="preset-chip">${y(r)}</span>
          ${i}
          <span class="call-play" aria-hidden="true">${b}</span>
          <span class="sr-only">${d} recording</span>
        </div>
        <p class="fixture-num">${y(j(e.number)||e.number||"")}</p>
        <div class="call-dur">${y(Jt(e.duration_sec))}</div>
        <div class="meta">${y(zt(e.started_at))}</div>
        ${c}
        ${u}
      </button>`}).join("")}</div>${l.playError?`<div class="call-play-empty">${y(l.playError)}</div>`:""}`:'<div class="empty">Mixer idle. No GSM or simulator legs bridged.</div>'}function Vt(e){e.querySelectorAll("[data-call-id]").forEach(t=>{t.addEventListener("click",()=>{const n=t.getAttribute("data-call-id");n&&Dt(n)})})}function pe(e){if(!e||!e.status)return"";const t=Number(e.turns),n=Number.isFinite(t)?`${t} turn${t===1?"":"s"}`:"no turns",r=e.file?` · ${e.file}`:"";return`transcript ${e.status} · ${n}${r}`}function zt(e){if(!e)return"";const t=new Date(e);return Number.isNaN(t.getTime())?e:t.toLocaleString([],{month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"})}function Jt(e){const t=Number(e)||0,n=Math.floor(t/60),r=t%60;return n?`${n}m ${r}s`:`${r}s`}function y(e){return String(e).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;")}function z(e){return y(e).replace(/'/g,"&#39;")}const s={peer:null,threads:[],messages:[],error:"",sending:!1,mock:!1,suppressAutoOpen:!1,hasOlder:!1,loadedPeer:null,stickToBottom:!0,loadingOlder:!1,boundary:null,dividerCount:0,pendingBelow:0,readToken:"",draft:""};let ae=null;function Yt(e){return!Array.isArray(e)||e.length===0?null:[...e].sort((n,r)=>String(r.lastAt||"").localeCompare(String(n.lastAt||"")))[0].peer}function Kt({peer:e,suppressAutoOpen:t,threads:n}){return e||(t?null:Yt(n))}function me(e){if(!e)return;const t=()=>{e.scrollTop=e.scrollHeight};t(),typeof requestAnimationFrame=="function"&&requestAnimationFrame(t)}function Zt(e){if(e.startsWith("/thread/")){s.peer=decodeURIComponent(e.slice(8)),s.suppressAutoOpen=!1;return}s.peer=null}function Qt(){return{peer:s.peer}}function Pe(){s.suppressAutoOpen=!1}function Me(e){e&&(s.suppressAutoOpen=!1,s.peer=e,J(`/messaging/thread/${encodeURIComponent(e)}`))}function ge(){s.peer?J(`/messaging/thread/${encodeURIComponent(s.peer)}`):J("/messaging")}function Xt(e){if(s.threads=Array.isArray(e)?e:[],_e(s.threads),s.readToken&&s.peer&&s.stickToBottom&&!document.hidden){Oe(s.peer);const t=s.threads.find(n=>n.peer===s.peer);t&&(t.unread=0)}}async function G(){const e=await Te(ne());Xt(e)}async function xe(e){const t=await te(e,{limit:Z});s.messages=t.messages,s.hasOlder=!!t.hasOlder,s.loadedPeer=e,s.stickToBottom=!0,s.pendingBelow=0,s.readToken="";const n=s.threads.find(r=>r.peer===e);s.dividerCount=Number(n==null?void 0:n.unread)||0,s.boundary=n!=null&&n.readTs?{ts:n.readTs,id:n.readId||""}:null}async function en(e){ae=e;try{s.error="";const t=await R();s.mock=!!t.mock,await G();const n=Kt({peer:s.peer,suppressAutoOpen:s.suppressAutoOpen,threads:s.threads});!s.peer&&n&&bt(`/messaging/thread/${encodeURIComponent(n)}`),s.peer=n,s.peer?await xe(s.peer):(s.messages=[],s.loadedPeer=null,s.hasOlder=!1)}catch(t){s.error=t.message||String(t)}v(e,{stick:!0}),await S()}function v(e,t={}){var o;const n=e.querySelector("#compose textarea");n&&(s.draft=n.value);const r=!!(n&&typeof document<"u"&&document.activeElement===n),a=r?[n.selectionStart,n.selectionEnd]:null;e.innerHTML=`
    ${q({page:"messaging",mock:s.mock})}
    ${s.error?`<div class="error">${p(s.error)}</div>`:""}
    <main>${tn()}</main>
  `,k(),e.querySelectorAll("[data-peer]").forEach(c=>{c.addEventListener("click",()=>{s.suppressAutoOpen=!1,s.peer=c.getAttribute("data-peer"),ge()})}),(o=e.querySelector("[data-threads-back]"))==null||o.addEventListener("click",c=>{c.preventDefault(),s.suppressAutoOpen=!0,s.peer=null,s.loadedPeer=null,ge()}),rn(e,t);const i=e.querySelector("#compose textarea");r&&i&&(i.focus(),a&&typeof i.setSelectionRange=="function"&&i.setSelectionRange(a[0],a[1]))}function tn(){const e=!!s.peer;return`<div class="msg-layout${e?" has-peer":""}">
    <div class="msg-list-pane">${nn()}</div>
    <div class="msg-detail-pane">${e?sn():'<div class="empty">Select a thread. Pixel forwarder idle until a peer is chosen.</div>'}</div>
  </div>`}function nn(){return s.threads.length?`<ul class="list">${s.threads.map(e=>{const t=e.peer===s.peer?" on":"",n=A.unread[e.peer]||0,r=n?`<span class="thread-unread">${n}</span>`:"";return`
      <li>
        <button class="row${t}" data-peer="${Re(e.peer)}">
          <span class="row-head"><strong>${p(j(e.peer)||e.peer)}</strong>${r}</span>
          <span>${p(e.lastBody||"")}</span>
          <span class="meta">${p(He(e.lastAt))}</span>
        </button>
      </li>`}).join("")}</ul>`:'<div class="empty">No SMS on the wire. Pixel forwarder idle.</div>'}function sn(){const e=s.dividerCount>0?Fe(s.messages,s.boundary):-1,t=s.messages.map((a,i)=>{const o=i===e?`<div class="unread-divider" role="separator"><span>${p(ve(s.dividerCount))}</span></div>`:"",c=a.direction==="out"?"out":"in",u=a.status&&a.status!=="received"&&a.status!=="sent"?`<span class="status-pill">${p(a.status)}${a.error?` ${p(a.error)}`:""}</span>`:"";return`${o}<div class="bubble ${c}" data-id="${Re(a.id||"")}"><span class="bubble-body">${p(a.body||"")}</span>${u}<time>${p(He(a.ts))}</time></div>`}).join(""),n=j(s.peer)||s.peer,r=we(s.pendingBelow);return`
    <div class="thread">
      <div class="thread-head">
        <a class="back" data-threads-back href="${T("/messaging")}">‹ Threads</a>
        <span class="thread-peer">${p(n)}</span>
      </div>
      ${s.error?`<div class="error">${p(s.error)}</div>`:""}
      <div class="messages" id="messages">${t||'<div class="empty">No messages in this thread.</div>'}</div>
      <button type="button" class="jump-latest" id="jump-latest" hidden>${p(r)}</button>
      <form class="compose" id="compose">
        <textarea name="body" rows="1" placeholder="SMS" required>${p(s.draft||"")}</textarea>
        <button type="submit" ${s.sending?"disabled":""}>Send</button>
      </form>
    </div>
  `}function rn(e,t){var a;const n=e.querySelector("#messages");t.anchor&&n?Ve(n,t.anchor.height,t.anchor.top):t.preserveTop!=null&&n?n.scrollTop=t.preserveTop:(t.stick||s.stickToBottom)&&me(n),M(n),n==null||n.addEventListener("scroll",()=>{const i=Q(n);s.stickToBottom=i,i&&(s.pendingBelow=0),M(n),i&&S(),!i&&$e(n)&&x(e)},{passive:!0}),(a=e.querySelector("#jump-latest"))==null||a.addEventListener("click",()=>{s.stickToBottom=!0,s.pendingBelow=0,me(n),M(n),S()});const r=e.querySelector("#compose");r&&(r.addEventListener("submit",async i=>{i.preventDefault();const o=i.target.querySelector("textarea"),c=i.target.querySelector("button"),u=o.value.trim();if(!(!u||s.sending)){s.sending=!0,s.draft="",c&&(c.disabled=!0);try{const d=await tt(s.peer,u),g=(Array.isArray(d==null?void 0:d.parts)?d.parts:[d]).map(E=>E==null?void 0:E.message).filter(Boolean);s.sending=!1,g.length?(s.messages=H(s.messages,g),s.stickToBottom=!0,s.pendingBelow=0,v(e,{stick:!0}),await S()):(await xe(s.peer),v(e,{stick:!0}),await S())}catch(d){s.sending=!1,s.draft=u,s.error=d.message||String(d),v(e,{stick:s.stickToBottom})}}}),s.stickToBottom&&s.hasOlder&&n&&n.scrollHeight<=n.clientHeight+8&&x(e))}function M(e){const t=document.getElementById("jump-latest");if(!t)return;const n=e?Q(e):s.stickToBottom;t.hidden=n,t.textContent=we(s.pendingBelow)}async function x(e,t=0){var o;if(s.loadingOlder||!s.hasOlder||!s.messages.length||!s.peer)return;s.loadingOlder=!0;const n=s.messages[0];let r=!1;try{const c=await te(s.peer,{limit:Z,before:n.ts,beforeId:n.id});if(!c.messages.filter(E=>!s.messages.some(W=>W.id===E.id)).length){s.hasOlder=!1;return}const d=e.querySelector("#messages"),b=s.stickToBottom,g=d?{height:d.scrollHeight,top:d.scrollTop}:null;s.messages=H(c.messages,s.messages),s.hasOlder=!!c.hasOlder,v(e,b?{stick:!0}:{anchor:g})}catch(c){r=!0,s.error=c.message||String(c),v(e,{stick:!1,preserveTop:(o=e.querySelector("#messages"))==null?void 0:o.scrollTop})}finally{s.loadingOlder=!1}if(r||t>=3||!s.hasOlder)return;const a=e.querySelector("#messages");if(!a)return;const i=a.scrollHeight<=a.clientHeight+8;s.stickToBottom&&i?await x(e,t+1):!s.stickToBottom&&$e(a)&&await x(e,t+1)}async function S(){const e=s.messages[s.messages.length-1];if(!Se({hidden:typeof document<"u"&&document.hidden,pinned:s.stickToBottom,hasMessages:!!(e&&s.peer)}))return!1;const t=`${s.peer}\0${e.ts}\0${e.id||""}`;if(s.readToken===t)return!0;s.readToken=t,s.pendingBelow=0,Oe(s.peer);const n=s.threads.find(r=>r.peer===s.peer);n&&(n.unread=0,n.readTs=e.ts,n.readId=e.id||""),k(),M(document.getElementById("messages"));try{await Xe({viewer:ne(),peer:s.peer,ts:e.ts,id:e.id||""})}catch(r){s.mock||(s.readToken="",console.warn(r))}return!0}async function an(e,t){const n=e.querySelector("#messages"),r=n?Q(n):s.stickToBottom;s.stickToBottom=r;const a=n?n.scrollTop:0,i=s.messages.some(c=>c.id===t.id);s.messages=H(s.messages,[t]);const o=Se({hidden:document.hidden,pinned:r,hasMessages:!0});!i&&t.direction==="in"&&!o&&(s.pendingBelow+=1);try{await G()}catch(c){console.warn(c)}v(e,r?{stick:!0}:{preserveTop:a}),o&&await S()}async function on(e){const t=e.querySelector("#messages"),n=t?t.scrollTop:0,r=s.stickToBottom;try{await G()}catch(o){console.warn(o)}if(!s.peer||!s.messages.length){v(e,{stick:r});return}let a=s.messages[s.messages.length-1],i=0;for(let o=0;o<20;o+=1){const c=await te(s.peer,{limit:Z,since:a.ts,sinceId:a.id});if(!c.messages.length)break;const u=c.messages.filter(d=>d.direction==="in"&&!s.messages.some(b=>b.id===d.id));if(s.messages=H(s.messages,c.messages),i+=u.length,a=s.messages[s.messages.length-1],!c.hasNewer)break}!r&&i&&(s.pendingBelow+=i),v(e,r?{stick:!0}:{preserveTop:n}),r&&await S()}async function cn(e,t,n){const r=e||ae;if(!r)return;if(t==="reconnect"){await on(r);return}if(t!=="message")return;const a=n&&typeof n=="object"?n:null;if(a&&s.peer&&a.peer===s.peer&&s.loadedPeer===s.peer){await an(r,a);return}try{await G()}catch(o){console.warn(o)}const i=r.querySelector("#messages");v(r,s.stickToBottom?{stick:!0}:{preserveTop:i?i.scrollTop:0})}function ln(e){ae=e}function He(e){if(!e)return"";const t=new Date(e);return Number.isNaN(t.getTime())?e:t.toLocaleString([],{month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"})}function p(e){return String(e).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;")}function Re(e){return p(e).replace(/'/g,"&#39;")}const h=document.getElementById("app"),un=Object.assign({"./routing/index.js":()=>O(()=>import("./index-D7hWhKe6.js"),__vite__mapDeps([0,1]))});let I=null,he=null;function dn(){return L().page}async function fn(e,t){const n=t&&typeof t=="object"?{...t,type:t.type||e}:{type:e};if(e==="message"){const r=Qt();await pt(n,{selectedPeer:r.peer,documentHidden:document.hidden,route:dn(),onOpen:a=>{a&&Me(a)}})}if(e==="reconnect"||e==="message"||e==="outbox"){if(L().page==="messaging")await cn(h,e,t);else if(e==="message")try{await rt()}catch(r){n.direction==="in"&&at(n.peer),k(),console.warn(r)}else k();return}L().page==="calls"?await Wt(h,e):k()}function pn(){he||(he=nt((e,t)=>{fn(e,t).catch(n=>console.warn(n))}))}function mn(){const e="/portal/sw.js".replace(/\/{2,}/g,"/");"serviceWorker"in navigator&&(navigator.serviceWorker.register(e).catch(t=>console.warn("sw",t)),navigator.serviceWorker.addEventListener("message",t=>{var n,r;((n=t.data)==null?void 0:n.type)==="open-peer"&&t.data.peer?Me(t.data.peer):((r=t.data)==null?void 0:r.type)==="open-peer"&&(Pe(),window.location.href=T("/messaging"))}))}async function Ue(){I==null||I(),I=null;const e=L();e.page!=="messaging"&&Pe(),e.page!=="simulator"&&e.page!=="routing"&&Ct(),e.page==="messaging"?(Zt(e.rest),ln(h),await en(h)):e.page==="calls"?(Gt(h),await Ne(h)):e.page==="routing"?await gn():e.page==="simulator"?await Ot(h):await Bt(h),ft(),k()}async function gn(){const e=Object.values(un);if(e.length){const t=await e[0]();if(typeof t.mount=="function"){I=await t.mount(h)||null;return}if(typeof t.render=="function"){await t.render(h);return}if(typeof t.default=="function"){await t.default(h);return}}h.innerHTML=`
    <header class="app-bar">
      <a class="back" href="${T("/")}">‹ Home</a>
      <h1>Routing</h1>
      <span></span>
    </header>
    <main class="dash">
      <p class="muted">Routing UI is not in this build yet. The hub switchboard still lives on the phone SMS commands.</p>
    </main>
  `}document.addEventListener("visibilitychange",()=>{document.hidden||L().page==="messaging"&&S().catch(e=>console.warn(e))});vt(()=>{Ue()});mn();pn();Ue();export{q as a,St as g,hn as s,yn as u,bn as v};
