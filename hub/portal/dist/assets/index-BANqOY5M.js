const __vite__mapDeps=(i,m=__vite__mapDeps,d=(m.f||(m.f=["assets/index-BgWxwkFG.js","assets/index-DiqwetGY.css"])))=>i.map(i=>d[i]);
(function(){const t=document.createElement("link").relList;if(t&&t.supports&&t.supports("modulepreload"))return;for(const a of document.querySelectorAll('link[rel="modulepreload"]'))s(a);new MutationObserver(a=>{for(const r of a)if(r.type==="childList")for(const i of r.addedNodes)i.tagName==="LINK"&&i.rel==="modulepreload"&&s(i)}).observe(document,{childList:!0,subtree:!0});function n(a){const r={};return a.integrity&&(r.integrity=a.integrity),a.referrerPolicy&&(r.referrerPolicy=a.referrerPolicy),a.crossOrigin==="use-credentials"?r.credentials="include":a.crossOrigin==="anonymous"?r.credentials="omit":r.credentials="same-origin",r}function s(a){if(a.ep)return;a.ep=!0;const r=n(a);fetch(a.href,r)}})();const he="modulepreload",ye=function(e){return"/portal/"+e},Y={},k=function(t,n,s){let a=Promise.resolve();if(n&&n.length>0){document.getElementsByTagName("link");const i=document.querySelector("meta[property=csp-nonce]"),c=(i==null?void 0:i.nonce)||(i==null?void 0:i.getAttribute("nonce"));a=Promise.allSettled(n.map(u=>{if(u=ye(u),u in Y)return;Y[u]=!0;const y=u.endsWith(".css"),R=y?'[rel="stylesheet"]':"";if(document.querySelector(`link[href="${u}"]${R}`))return;const b=document.createElement("link");if(b.rel=y?"stylesheet":he,y||(b.as="script"),b.crossOrigin="",b.href=u,c&&b.setAttribute("nonce",c),document.head.appendChild(b),y)return new Promise((me,ge)=>{b.addEventListener("load",me),b.addEventListener("error",()=>ge(new Error(`Unable to preload CSS for ${u}`)))})}))}function r(i){const c=new Event("vite:preloadError",{cancelable:!0});if(c.payload=i,window.dispatchEvent(c),!c.defaultPrevented)throw i}return a.then(i=>{for(const c of i||[])c.status==="rejected"&&r(c.reason);return t().catch(r)})},be={},ve=be||{},q=(ve.VITE_HUB_ORIGIN||"").replace(/\/$/,""),E=`${q}/portal/api`;async function te(e){const t=await fetch(e);if(!t.ok){const n=await t.text();throw new Error(n||`HTTP ${t.status}`)}return t.json()}async function j(e,t){try{return{data:await te(e),mock:!1}}catch{const n=await t();return{data:n.default??n,mock:!0}}}async function O(){return j(`${q}/health`,()=>k(()=>import("./health-CSdgTVNW.js"),[]))}async function $e(e){const t=we(e);return t?{data:t,mock:!1}:{data:(await k(()=>import("./usage-CNUK0FQm.js"),[])).default,mock:!0}}function we(e){return!e||typeof e!="object"?null:e.usage&&typeof e.usage=="object"?e.usage:e.tokens&&typeof e.tokens=="object"?e.tokens:e.openclaw_tokens&&typeof e.openclaw_tokens=="object"?e.openclaw_tokens:null}async function ke(){const{data:e}=await j(`${E}/threads`,()=>k(()=>import("./threads-DsUYIr5z.js"),[]));return e}async function Se(e){try{return await te(`${E}/messages?peer=${encodeURIComponent(e)}`)}catch{return(await k(()=>import("./messages-OYSZw2Be.js"),[])).default[e]||[]}}async function ne(){const{data:e,mock:t}=await j(`${E}/calls`,()=>k(()=>import("./calls-DVVb_JZE.js"),[]));return{data:e,mock:t}}function Ee(e){return`${E}/calls/${encodeURIComponent(e)}/recording`}async function _e(e,t){try{const n=await fetch(`${E}/messages/send`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({to:e,body:t})});if(!n.ok){const s=await n.text();throw new Error(s||`HTTP ${n.status}`)}return n.json()}catch(n){if(q)throw n;return{ok:!0,mock:!0,to:e,body:t}}}function Te(e){const t=new EventSource(`${E}/events`);for(const n of["message","call","outbox"])t.addEventListener(n,s=>{try{e(n,JSON.parse(s.data))}catch(a){console.warn("sse parse",a)}});return t.onerror=()=>{},t}const A={unread:{}};function se(e=A.unread){return Object.values(e||{}).reduce((t,n)=>t+(Number(n)||0),0)}function Ae(e){e&&(A.unread[e]=(A.unread[e]||0)+1)}function D(e){e&&delete A.unread[e]}function S(){const e=document.getElementById("unread-badge");if(!e)return;const t=se();e.hidden=t===0,e.textContent=String(t)}function W(e=globalThis.isSecureContext,t=typeof Notification<"u"){return!!(e&&t)}function Ce({isSecure:e=globalThis.isSecureContext,permission:t=typeof Notification<"u"?Notification.permission:"denied"}={}){return e&&t==="granted"?"os":"in-app"}function Le(e,{selectedPeer:t,documentHidden:n,route:s}={}){return!e||e.type!=="message"||e.direction==="out"&&e.status==="queued"?!1:!(s==="messaging"&&t&&t===e.peer&&!n)}function H(e,t=140){const n=String(e||"").replace(/\s+/g," ").trim();return n.length<=t?n:`${n.slice(0,t-1)}…`}function Oe(e){const t=e.peer||"unknown";return e.direction==="out"?e.status==="failed"||e.status==="timeout"?{title:e.status==="timeout"?`SMS timed out to ${t}`:`SMS failed to ${t}`,body:H(e.error||e.body)}:{title:`SMS sent to ${t}`,body:H(e.body)}:{title:`SMS from ${t}`,body:H(e.body)}}async function Pe(){if(!W())throw new Error("OS notifications need a secure context (HTTPS)");return Notification.requestPermission()}async function Ie({title:e,body:t,peer:n,tag:s}){var i;const a={body:t,tag:s||`sms-${n||"desk"}`,data:{peer:n||""}},r=await((i=navigator.serviceWorker)==null?void 0:i.ready.catch(()=>null));return r&&typeof r.showNotification=="function"?(await r.showNotification(e,{...a,icon:"./icon-192.png"}),"sw"):(new Notification(e,a),"page")}function z({title:e,body:t,peer:n,onOpen:s}){const a=document.getElementById("toast-stack");if(!a)return;const r=document.createElement("button");r.type="button",r.className="toast",r.innerHTML=`<strong>${J(e)}</strong><span>${J(t||"")}</span>`;const i=()=>r.remove();r.addEventListener("click",()=>{s&&s(n),i()}),a.prepend(r),window.setTimeout(i,8e3)}function J(e){return String(e).replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;")}let K=!1;function Ne({onPermission:e}={}){const t=()=>{const n=document.getElementById("notify-btn"),s=document.getElementById("notify-hint");if(!n||!s)return;const a=W(),r=a?Notification.permission:"denied";if(!a){n.hidden=!0,s.hidden=!1,s.textContent="HTTP desk — in-app toasts only. OS alerts need HTTPS.";return}if(r==="granted"){n.hidden=!0,s.hidden=!0;return}if(r==="denied"){n.hidden=!0,s.hidden=!1,s.textContent="Alerts blocked in this browser. In-app toasts still fire.";return}n.hidden=!1,s.hidden=!0,n.textContent="Enable desk alerts"};return t(),K||(K=!0,document.addEventListener("click",async n=>{if(!n.target.closest("#notify-btn"))return;const a=document.getElementById("notify-hint");try{const r=await Pe();t(),e&&e(r)}catch(r){a&&(a.hidden=!1,a.textContent=r.message||String(r))}})),t}async function Me(e,t){if(!Le(e,t))return!1;const n=Oe(e);if(Ce()==="os")try{return await Ie({...n,peer:e.peer,tag:e.id}),"os"}catch{return z({...n,peer:e.peer,onOpen:t.onOpen}),"in-app"}return z({...n,peer:e.peer,onOpen:t.onOpen}),"in-app"}const Re={BASE_URL:"/portal/"},He=Re||{},ae=(He.BASE_URL||"/").replace(/\/?$/,"/");function P(){return(location.hash||"").startsWith("#/")}function xe(){if(P()){const n=location.hash.slice(1);return w(n.split("?")[0])}let e=location.pathname||"/";const t=ae.replace(/\/$/,"")||"";return t&&(e===t||e.startsWith(`${t}/`))&&(e=e.slice(t.length)||"/"),w(e)}function w(e){let t=e||"/";return t.startsWith("/")||(t=`/${t}`),t.length>1&&t.endsWith("/")&&(t=t.slice(0,-1)),t||"/"}function Be(e){const t=w(e);if(t==="/calls"||t.startsWith("/calls/"))return{page:"calls",path:t};if(t==="/messaging"||t.startsWith("/messaging/")){const n=t==="/messaging"?"/":t.slice(10);return{page:"messaging",path:t,rest:w(n)}}return t==="/routing"||t.startsWith("/routing/")?{page:"routing",path:t}:t==="/simulator"||t.startsWith("/simulator/")?{page:"simulator",path:t}:{page:"home",path:"/"}}function T(){return Be(xe())}function $(e){const t=w(e);if(P())return`#${t}`;const n=t==="/"?"":t.replace(/^\//,"");return`${ae}${n}`}function Ue(e){const t=w(e);if(P()){history.replaceState(null,"",`${location.pathname}${location.search}#${t}`);return}history.replaceState({},"",$(t))}function x(e){const t=w(e);if(P()){location.hash=t;return}const n=$(t);if(`${location.pathname}${location.search}`===n&&!location.hash){window.dispatchEvent(new PopStateEvent("popstate"));return}history.pushState({},"",n),window.dispatchEvent(new PopStateEvent("popstate"))}function qe(e){window.addEventListener("hashchange",e),window.addEventListener("popstate",e)}function je(e){return e?'<span class="badge-mock">Local mock</span>':""}function De(){const e=W(),t=e&&typeof Notification<"u"?Notification.permission:"denied";return e?t==="granted"?`
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
    `}function I({page:e,mock:t,extra:n=""}){const s=se(),a=`<span id="unread-badge" class="nav-badge"${s?"":" hidden"}>${s}</span>`,r=(i,c,u)=>{const y=e===i?'aria-current="page"':"",R=i==="messaging"?a:"";return`<a class="cockpit-link${e===i?" on":""}" href="${$(c)}" ${y}>${u}${R}</a>`};return`
    <header class="cockpit-bar">
      <span class="cockpit-mark">GSM Hub</span>
      <nav class="cockpit-nav" aria-label="Operator">
        ${r("home","/","Overview")}
        ${r("messaging","/messaging","Messaging")}
        ${r("calls","/calls","Calls")}
        ${r("routing","/routing","Switchboard")}
        ${r("simulator","/simulator","Simulator")}
      </nav>
      <div class="cockpit-trail">
        ${De()}
        ${je(t)}
        ${n}
      </div>
    </header>
  `}function N(e){if(!e)return"";const t=String(e).trim(),n=t.replace(/\D/g,"");return n==="15550100"||n==="15555550100"||n==="5550100"||n==="15555550123"?"Mock +1 555 0100":/555/.test(n)&&n.length>=7?`Mock ${t}`:t}const B=new Set;let M=!1,G=null,re={seated:["gsm_bus","openclaw_bus"],echoGsm:!1,deafened:[]};function Mt(e){return B.add(e),()=>B.delete(e)}function ie(){const e=We();for(const t of B)t(e)}function We(){return{active:M,path:G,snapshot:re}}function Rt({seated:e,echoGsm:t,deafened:n}){M||(re={seated:[...e||[]],echoGsm:!!t,deafened:[...n||[]]})}function Ht(e){return e==="loopback"?{seated:["gsm_bus"],echoGsm:!0,deafened:[],mode:"loopback",label:"GSM echo"}:{seated:["gsm_bus","openclaw_bus"],echoGsm:!1,deafened:[],mode:"openclaw",label:"Agent"}}function Ge(e){M=!0,G=e,ie()}function Fe(){M=!1,G=null,ie()}function Ve(){const e="".replace(/\/$/,"");return e||(typeof location<"u"?location.origin:"http://hub.mining-ling.ts.net:8787")}let h=null;function C(e,t){return`<div class="stereo-meter" id="${e}">
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
  </div>`}function Ye({agentOn:e=!0}={}){const t=e?"":"checked",n=e?"checked":"";return`
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
          <input id="hub-url" type="url" value="${Ve()}" />
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
        ${C("meter-mic","Mic uplink")}
        ${C("meter-tone","Test tone uplink")}
        ${C("meter-downlink","Earpiece (from hub)")}
        ${C("meter-sidetone","Local sidetone")}
      </section>
      <section class="card">
        <h2>Event log</h2>
        <pre id="log"></pre>
      </section>
    </div>
  `}async function ze(e){h==null||h(),h=null;const{mountCallSimulator:t}=await k(async()=>{const{mountCallSimulator:n}=await import("./main-r-Rna2Qa.js");return{mountCallSimulator:n}},[]);h=t(e,{onCallStart:n=>Ge(n),onCallEnd:()=>Fe()})}async function Je(e){const t=await O();e.innerHTML=`
    ${I({page:"simulator",mock:t.mock})}
    ${Ye({agentOn:!0})}
  `,await ze(e.querySelector(".call-sim"))}function Ke(){h==null||h(),h=null}async function Qe(e){const t=await O(),n=t.data||{},s=await ne(),a=Array.isArray(s.data)?s.data:[],r=await $e(t.mock?null:n),i=Ze(a,n.last_call_tap),c=!!(t.mock||s.mock),u=n.mode||"idle";e.innerHTML=`
    ${I({page:"home",mock:c})}
    <main class="dash">
      <div class="dash-col">
        <section class="card">
          <h2>Hub status</h2>
          ${Xe(n)}
        </section>
        <section class="card">
          <h2>Last call</h2>
          ${i?nt(i):'<p class="muted">No calls recorded.</p>'}
        </section>
      </div>
      <div class="dash-col">
        <section class="card">
          <h2>Token / usage</h2>
          ${st(r.data)}
        </section>
      </div>
      <section class="dash-strip">
        <div class="dash-strip-preset">
          <span class="lamp-label">Routing preset</span>
          <span class="lamp-value">${p(String(u).toUpperCase())}</span>
        </div>
        <pre class="teletype dash-teletype">${p(Q(u))}</pre>
        <div class="dash-strip-links">
          <a class="primary-link secondary" href="${$("/routing")}">Open switchboard</a>
          <a class="primary-link secondary" href="${$("/simulator")}">Call simulator</a>
        </div>
      </section>
      <p class="dash-tail">Last preset applied: ${p(String(u).toUpperCase())} · ${p(Q(u))}${i?` · last call ${p(oe(i.started||""))}`:""}</p>
    </main>
  `}function Q(e){return e==="openclaw"?"openclaw_bus:monitor_* |-> gsm_bus:playback_*":e==="loopback"?"(no pw-link; uplink → gsm_bus.monitor)":e==="conference"?"conference star: GSM and OpenClaw; WA/TG listen GSM only":e==="clear"?"(buses idle)":String(e)}function Xe(e){const t=!!e.ok,n=e.openclaw_talk||"unknown",s=e.mode||"idle";return`<div class="lamp-row">
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
        <span class="lamp-value">${p(n)}</span>
      </span>
    </div>
    <div class="lamp-cell">
      <span class="lamp preset-lamp"></span>
      <span class="lamp-copy">
        <span class="lamp-label">PipeWire</span>
        <span class="lamp-value">${p(String(s).toUpperCase())}</span>
      </span>
    </div>
  </div>`}function Ze(e,t){const n=e[0]||null,s=et(t),a=tt(t);if(!n&&!t)return null;const r=(n==null?void 0:n.started_at)||(t==null?void 0:t.ended_at)||(t==null?void 0:t.started_at)||null,i=(n==null?void 0:n.duration_sec)??s??null,c=(n==null?void 0:n.number)||a||"";return{started:r,duration:i,number:c,fromTap:!!t&&!n}}function et(e){if(!e)return null;const t=e.streams||{};let n=0;for(const a of Object.values(t)){const r=Number(a==null?void 0:a.seconds);r>n&&(n=r)}if(n>0)return Math.round(n);const s=e.mixes||{};for(const a of Object.values(s)){const r=Number(a==null?void 0:a.seconds);r>n&&(n=r)}return n>0?Math.round(n):null}function tt(e){if(!e)return"";const t=e.meta||{};return t.number||t.peer||t.from||""}function nt(e){const t=e.started?oe(e.started):"time unknown",n=e.duration!=null?at(e.duration):"duration unknown",s=e.number?N(e.number):"number unknown";return`<p class="fixture-num">${p(s)}</p>
    <p class="muted last-call-meta">${p(n)}<br/>${p(t)}</p>`}function st(e){if(!e)return'<p class="muted">No usage data.</p>';const t=e.label||"Usage",n=e.used==null?"n/a":String(e.used),s=e.limit==null?"n/a":String(e.limit),a=e.note?`<p class="muted">${p(e.note)}</p>`:"";return`<p>${p(t)}</p>
    <div class="kv">
      <span class="lamp-label">Used</span><span class="lamp-value">${p(n)}</span>
      <span class="lamp-label">Limit</span><span class="lamp-value">${p(s)}</span>
    </div>
    ${a}`}function at(e){const t=Number(e)||0,n=Math.floor(t/60),s=t%60;return n?`${n}m ${s}s`:`${s}s`}function oe(e){const t=new Date(e);if(Number.isNaN(t.getTime()))return e;const n=Date.now()-t.getTime(),s=Math.abs(n),a=new Intl.RelativeTimeFormat(void 0,{numeric:"auto"}),r=[{amount:60,unit:"second"},{amount:60,unit:"minute"},{amount:24,unit:"hour"},{amount:7,unit:"day"},{amount:4.34524,unit:"week"},{amount:12,unit:"month"},{amount:Number.POSITIVE_INFINITY,unit:"year"}];let i=s/1e3;for(const c of r){if(i<c.amount){const u=Math.round(i)*(n>=0?-1:1);return a.format(u,c.unit)}i/=c.amount}return t.toLocaleString()}function p(e){return String(e).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;")}const L="No OpenClaw recording for this call.",l={calls:[],error:"",mock:!1,playingId:null,playError:""};let d=null,U=null;function rt(){l.playingId=null,l.playError="",d&&v(d)}function it(){l.playingId=null,l.playError=L,d&&v(d)}function ot(e){return U=e,e&&(e.preload="none",e.addEventListener("ended",rt),e.addEventListener("error",it),e)}function lt(){return U||(typeof Audio>"u"?null:ot(new Audio))}function ct(e,t=lt()){if(l.playError="",!e)return;if(l.playingId===e&&t&&!t.paused){t.pause(),l.playingId=null,d&&v(d);return}if(!t){l.playingId=null,l.playError=L,d&&v(d);return}l.playingId&&l.playingId!==e&&t.pause();const n=Ee(e);t.src=n;let s;try{s=t.play()}catch{l.playingId=null,l.playError=L,d&&v(d);return}l.playingId=e,d&&v(d),s&&typeof s.then=="function"&&s.catch(()=>{l.playingId===e&&(l.playingId=null,l.playError=L,d&&v(d))})}async function le(e){d=e;try{l.error="";const t=await O();l.mock=!!t.mock;const n=await ne();l.calls=Array.isArray(n.data)?n.data:[],l.mock=l.mock||!!n.mock}catch(t){l.error=t.message||String(t)}v(e)}function ut(e){d=e}async function dt(e,t){const n=e||d;n&&t==="call"&&await le(n)}function v(e){e.innerHTML=`
    ${I({page:"calls",mock:l.mock})}
    ${l.error?`<div class="error">${g(l.error)}</div>`:""}
    <main>${pt()}</main>
  `,S(),ft(e)}function pt(){return l.calls.length?`<div class="call-list">${l.calls.map(e=>{const t=e.direction!=="out",n=t?"Incoming":"Outgoing",s=e.switchboard_mode||"idle",a=e.path==="public"||e.path==="tailscale"?e.path:"",r=a?`<span class="preset-chip" data-entrance="${X(a)}">${g(a)}</span>`:"",i=l.playingId===e.id,c=e.session_id?`<div class="meta session-id">${g(e.session_id)}</div>`:"",u=i?"Pause":"Play",y=i?"❚❚":"▶";return`<button type="button" class="call-card${i?" playing":""}" data-call-id="${X(e.id)}" aria-pressed="${i?"true":"false"}" title="Play OpenClaw recording">
        <div class="call-card-head">
          <span class="lamp ${t?"good":"warn"}"></span>
          <h2>${g(n)}</h2>
          <span class="preset-chip">${g(s)}</span>
          ${r}
          <span class="call-play" aria-hidden="true">${y}</span>
          <span class="sr-only">${u} recording</span>
        </div>
        <p class="fixture-num">${g(N(e.number)||e.number||"")}</p>
        <div class="call-dur">${g(gt(e.duration_sec))}</div>
        <div class="meta">${g(mt(e.started_at))}</div>
        ${c}
      </button>`}).join("")}</div>${l.playError?`<div class="call-play-empty">${g(l.playError)}</div>`:""}`:'<div class="empty">Mixer idle. No GSM or simulator legs bridged.</div>'}function ft(e){e.querySelectorAll("[data-call-id]").forEach(t=>{t.addEventListener("click",()=>{const n=t.getAttribute("data-call-id");n&&ct(n)})})}function mt(e){if(!e)return"";const t=new Date(e);return Number.isNaN(t.getTime())?e:t.toLocaleString([],{month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"})}function gt(e){const t=Number(e)||0,n=Math.floor(t/60),s=t%60;return n?`${n}m ${s}s`:`${s}s`}function g(e){return String(e).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;")}function X(e){return g(e).replace(/'/g,"&#39;")}const o={peer:null,threads:[],messages:[],error:"",sending:!1,mock:!1,suppressAutoOpen:!1};let F=null;function ht(e){return!Array.isArray(e)||e.length===0?null:[...e].sort((n,s)=>String(s.lastAt||"").localeCompare(String(n.lastAt||"")))[0].peer}function yt({peer:e,suppressAutoOpen:t,threads:n}){return e||(t?null:ht(n))}function bt(e){if(!e)return;const t=()=>{e.scrollTop=e.scrollHeight};t(),typeof requestAnimationFrame=="function"&&requestAnimationFrame(t)}function vt(e){if(e.startsWith("/thread/")){o.peer=decodeURIComponent(e.slice(8)),o.suppressAutoOpen=!1;return}o.peer=null}function $t(){return{peer:o.peer}}function ce(){o.suppressAutoOpen=!1}function ue(e){e&&(o.suppressAutoOpen=!1,o.peer=e,D(e),S(),x(`/messaging/thread/${encodeURIComponent(e)}`))}function Z(){o.peer?x(`/messaging/thread/${encodeURIComponent(o.peer)}`):x("/messaging")}async function V(e){F=e;try{o.error="";const t=await O();o.mock=!!t.mock,o.threads=await ke();const n=yt({peer:o.peer,suppressAutoOpen:o.suppressAutoOpen,threads:o.threads});!o.peer&&n&&Ue(`/messaging/thread/${encodeURIComponent(n)}`),o.peer=n,o.peer?(D(o.peer),o.messages=await Se(o.peer)):o.messages=[]}catch(t){o.error=t.message||String(t)}de(e)}function de(e){var t;e.innerHTML=`
    ${I({page:"messaging",mock:o.mock})}
    ${o.error?`<div class="error">${m(o.error)}</div>`:""}
    <main>${wt()}</main>
  `,S(),e.querySelectorAll("[data-peer]").forEach(n=>{n.addEventListener("click",()=>{o.suppressAutoOpen=!1,o.peer=n.getAttribute("data-peer"),D(o.peer),Z()})}),(t=e.querySelector("[data-threads-back]"))==null||t.addEventListener("click",n=>{n.preventDefault(),o.suppressAutoOpen=!0,o.peer=null,Z()}),Et(e)}function wt(){const e=!!o.peer;return`<div class="msg-layout${e?" has-peer":""}">
    <div class="msg-list-pane">${kt()}</div>
    <div class="msg-detail-pane">${e?St():'<div class="empty">Select a thread. Pixel forwarder idle until a peer is chosen.</div>'}</div>
  </div>`}function kt(){return o.threads.length?`<ul class="list">${o.threads.map(e=>{const t=e.peer===o.peer?" on":"",n=A.unread[e.peer]||0,s=n?`<span class="thread-unread">${n}</span>`:"";return`
      <li>
        <button class="row${t}" data-peer="${At(e.peer)}">
          <span class="row-head"><strong>${m(N(e.peer)||e.peer)}</strong>${s}</span>
          <span>${m(e.lastBody||"")}</span>
          <span class="meta">${m(pe(e.lastAt))}</span>
        </button>
      </li>`}).join("")}</ul>`:'<div class="empty">No SMS on the wire. Pixel forwarder idle.</div>'}function St(){const e=o.messages.map(n=>{const s=n.direction==="out"?"out":"in",a=n.status&&n.status!=="received"&&n.status!=="sent"?`<span class="status-pill">${m(n.status)}${n.error?` ${m(n.error)}`:""}</span>`:"";return`<div class="bubble ${s}"><span class="bubble-body">${m(n.body||"")}</span>${a}<time>${m(pe(n.ts))}</time></div>`}).join(""),t=N(o.peer)||o.peer;return`
    <div class="thread">
      <div class="thread-head">
        <a class="back" data-threads-back href="${$("/messaging")}">‹ Threads</a>
        <span class="thread-peer">${m(t)}</span>
      </div>
      ${o.error?`<div class="error">${m(o.error)}</div>`:""}
      <div class="messages" id="messages">${e||'<div class="empty">No messages in this thread.</div>'}</div>
      <form class="compose" id="compose">
        <textarea name="body" rows="1" placeholder="SMS to mock peer" required></textarea>
        <button type="submit" ${o.sending?"disabled":""}>Send</button>
      </form>
    </div>
  `}function Et(e){bt(e.querySelector("#messages"));const t=e.querySelector("#compose");t&&t.addEventListener("submit",async n=>{n.preventDefault();const s=n.target.querySelector("textarea"),a=n.target.querySelector("button"),r=s.value.trim();if(!(!r||o.sending)){o.sending=!0,a&&(a.disabled=!0);try{await _e(o.peer,r),o.sending=!1,await V(e)}catch(i){o.sending=!1,o.error=i.message||String(i),de(e)}}})}async function _t(e,t){const n=e||F;n&&(t==="message"||t==="outbox")&&await V(n)}function Tt(e){F=e}function pe(e){if(!e)return"";const t=new Date(e);return Number.isNaN(t.getTime())?e:t.toLocaleString([],{month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"})}function m(e){return String(e).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;")}function At(e){return m(e).replace(/'/g,"&#39;")}const f=document.getElementById("app"),Ct=Object.assign({"./routing/index.js":()=>k(()=>import("./index-BgWxwkFG.js"),__vite__mapDeps([0,1]))});let _=null,ee=null;function Lt(){return T().page}async function Ot(e,t){const n=t&&typeof t=="object"?{...t,type:t.type||e}:{type:e};if(e==="message"){const s=$t(),a=await Me(n,{selectedPeer:s.peer,documentHidden:document.hidden,route:Lt(),onOpen:i=>{i&&ue(i)}}),r=T().page==="messaging"&&s.peer===n.peer&&!document.hidden;a&&n.peer&&!r&&(Ae(n.peer),S())}T().page==="messaging"?await _t(f,e):T().page==="calls"?await dt(f,e):S()}function Pt(){ee||(ee=Te((e,t)=>{Ot(e,t).catch(n=>console.warn(n))}))}function It(){const e="/portal/sw.js".replace(/\/{2,}/g,"/");"serviceWorker"in navigator&&(navigator.serviceWorker.register(e).catch(t=>console.warn("sw",t)),navigator.serviceWorker.addEventListener("message",t=>{var n,s;((n=t.data)==null?void 0:n.type)==="open-peer"&&t.data.peer?ue(t.data.peer):((s=t.data)==null?void 0:s.type)==="open-peer"&&(ce(),window.location.href=$("/messaging"))}))}async function fe(){_==null||_(),_=null;const e=T();e.page!=="messaging"&&ce(),e.page!=="simulator"&&e.page!=="routing"&&Ke(),e.page==="messaging"?(vt(e.rest),Tt(f),await V(f)):e.page==="calls"?(ut(f),await le(f)):e.page==="routing"?await Nt():e.page==="simulator"?await Je(f):await Qe(f),Ne(),S()}async function Nt(){const e=Object.values(Ct);if(e.length){const t=await e[0]();if(typeof t.mount=="function"){_=await t.mount(f)||null;return}if(typeof t.render=="function"){await t.render(f);return}if(typeof t.default=="function"){await t.default(f);return}}f.innerHTML=`
    <header class="app-bar">
      <a class="back" href="${$("/")}">‹ Home</a>
      <h1>Routing</h1>
      <span></span>
    </header>
    <main class="dash">
      <p class="muted">Routing UI is not in this build yet. The hub switchboard still lives on the phone SMS commands.</p>
    </main>
  `}qe(()=>{fe()});It();Pt();fe();export{I as a,We as g,Mt as s,Rt as u,Ht as v};
