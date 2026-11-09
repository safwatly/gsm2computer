const __vite__mapDeps=(i,m=__vite__mapDeps,d=(m.f||(m.f=["assets/index-CsfvnLur.js","assets/index-DiqwetGY.css"])))=>i.map(i=>d[i]);
(function(){const t=document.createElement("link").relList;if(t&&t.supports&&t.supports("modulepreload"))return;for(const a of document.querySelectorAll('link[rel="modulepreload"]'))s(a);new MutationObserver(a=>{for(const r of a)if(r.type==="childList")for(const i of r.addedNodes)i.tagName==="LINK"&&i.rel==="modulepreload"&&s(i)}).observe(document,{childList:!0,subtree:!0});function n(a){const r={};return a.integrity&&(r.integrity=a.integrity),a.referrerPolicy&&(r.referrerPolicy=a.referrerPolicy),a.crossOrigin==="use-credentials"?r.credentials="include":a.crossOrigin==="anonymous"?r.credentials="omit":r.credentials="same-origin",r}function s(a){if(a.ep)return;a.ep=!0;const r=n(a);fetch(a.href,r)}})();const ye="modulepreload",be=function(e){return"/portal/"+e},z={},k=function(t,n,s){let a=Promise.resolve();if(n&&n.length>0){document.getElementsByTagName("link");const i=document.querySelector("meta[property=csp-nonce]"),c=(i==null?void 0:i.nonce)||(i==null?void 0:i.getAttribute("nonce"));a=Promise.allSettled(n.map(u=>{if(u=be(u),u in z)return;z[u]=!0;const y=u.endsWith(".css"),_=y?'[rel="stylesheet"]':"";if(document.querySelector(`link[href="${u}"]${_}`))return;const b=document.createElement("link");if(b.rel=y?"stylesheet":ye,y||(b.as="script"),b.crossOrigin="",b.href=u,c&&b.setAttribute("nonce",c),document.head.appendChild(b),y)return new Promise((ge,he)=>{b.addEventListener("load",ge),b.addEventListener("error",()=>he(new Error(`Unable to preload CSS for ${u}`)))})}))}function r(i){const c=new Event("vite:preloadError",{cancelable:!0});if(c.payload=i,window.dispatchEvent(c),!c.defaultPrevented)throw i}return a.then(i=>{for(const c of i||[])c.status==="rejected"&&r(c.reason);return t().catch(r)})},ve={},$e=ve||{},j=($e.VITE_HUB_ORIGIN||"").replace(/\/$/,""),E=`${j}/portal/api`;async function ne(e){const t=await fetch(e);if(!t.ok){const n=await t.text();throw new Error(n||`HTTP ${t.status}`)}return t.json()}async function D(e,t){try{return{data:await ne(e),mock:!1}}catch{const n=await t();return{data:n.default??n,mock:!0}}}async function P(){return D(`${j}/health`,()=>k(()=>import("./health-CSdgTVNW.js"),[]))}async function we(e){const t=ke(e);return t?{data:t,mock:!1}:{data:(await k(()=>import("./usage-CNUK0FQm.js"),[])).default,mock:!0}}function ke(e){return!e||typeof e!="object"?null:e.usage&&typeof e.usage=="object"?e.usage:e.tokens&&typeof e.tokens=="object"?e.tokens:e.openclaw_tokens&&typeof e.openclaw_tokens=="object"?e.openclaw_tokens:null}async function Se(){const{data:e}=await D(`${E}/threads`,()=>k(()=>import("./threads-DsUYIr5z.js"),[]));return e}async function Ee(e){try{return await ne(`${E}/messages?peer=${encodeURIComponent(e)}`)}catch{return(await k(()=>import("./messages-OYSZw2Be.js"),[])).default[e]||[]}}async function se(){const{data:e,mock:t}=await D(`${E}/calls`,()=>k(()=>import("./calls-DVVb_JZE.js"),[]));return{data:e,mock:t}}function _e(e){return`${E}/calls/${encodeURIComponent(e)}/recording`}async function Te(e,t){try{const n=await fetch(`${E}/messages/send`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({to:e,body:t})});if(!n.ok){const s=await n.text();throw new Error(s||`HTTP ${n.status}`)}return n.json()}catch(n){if(j)throw n;return{ok:!0,mock:!0,to:e,body:t}}}function Ae(e){const t=new EventSource(`${E}/events`);for(const n of["message","call","outbox"])t.addEventListener(n,s=>{try{e(n,JSON.parse(s.data))}catch(a){console.warn("sse parse",a)}});return t.onerror=()=>{},t}const C={unread:{}};function ae(e=C.unread){return Object.values(e||{}).reduce((t,n)=>t+(Number(n)||0),0)}function Ce(e){e&&(C.unread[e]=(C.unread[e]||0)+1)}function W(e){e&&delete C.unread[e]}function S(){const e=document.getElementById("unread-badge");if(!e)return;const t=ae();e.hidden=t===0,e.textContent=String(t)}function G(e=globalThis.isSecureContext,t=typeof Notification<"u"){return!!(e&&t)}function Le({isSecure:e=globalThis.isSecureContext,permission:t=typeof Notification<"u"?Notification.permission:"denied"}={}){return e&&t==="granted"?"os":"in-app"}function Oe(e,{selectedPeer:t,documentHidden:n,route:s}={}){return!e||e.type!=="message"||e.direction==="out"&&e.status==="queued"?!1:!(s==="messaging"&&t&&t===e.peer&&!n)}function H(e,t=140){const n=String(e||"").replace(/\s+/g," ").trim();return n.length<=t?n:`${n.slice(0,t-1)}…`}function Pe(e){const t=e.peer||"unknown";return e.direction==="out"?e.status==="failed"||e.status==="timeout"?{title:e.status==="timeout"?`SMS timed out to ${t}`:`SMS failed to ${t}`,body:H(e.error||e.body)}:{title:`SMS sent to ${t}`,body:H(e.body)}:{title:`SMS from ${t}`,body:H(e.body)}}async function Ne(){if(!G())throw new Error("OS notifications need a secure context (HTTPS)");return Notification.requestPermission()}async function Ie({title:e,body:t,peer:n,tag:s}){var i;const a={body:t,tag:s||`sms-${n||"desk"}`,data:{peer:n||""}},r=await((i=navigator.serviceWorker)==null?void 0:i.ready.catch(()=>null));return r&&typeof r.showNotification=="function"?(await r.showNotification(e,{...a,icon:"./icon-192.png"}),"sw"):(new Notification(e,a),"page")}function J({title:e,body:t,peer:n,onOpen:s}){const a=document.getElementById("toast-stack");if(!a)return;const r=document.createElement("button");r.type="button",r.className="toast",r.innerHTML=`<strong>${K(e)}</strong><span>${K(t||"")}</span>`;const i=()=>r.remove();r.addEventListener("click",()=>{s&&s(n),i()}),a.prepend(r),window.setTimeout(i,8e3)}function K(e){return String(e).replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;")}let Q=!1;function Me({onPermission:e}={}){const t=()=>{const n=document.getElementById("notify-btn"),s=document.getElementById("notify-hint");if(!n||!s)return;const a=G(),r=a?Notification.permission:"denied";if(!a){n.hidden=!0,s.hidden=!1,s.textContent="HTTP desk — in-app toasts only. OS alerts need HTTPS.";return}if(r==="granted"){n.hidden=!0,s.hidden=!0;return}if(r==="denied"){n.hidden=!0,s.hidden=!1,s.textContent="Alerts blocked in this browser. In-app toasts still fire.";return}n.hidden=!1,s.hidden=!0,n.textContent="Enable desk alerts"};return t(),Q||(Q=!0,document.addEventListener("click",async n=>{if(!n.target.closest("#notify-btn"))return;const a=document.getElementById("notify-hint");try{const r=await Ne();t(),e&&e(r)}catch(r){a&&(a.hidden=!1,a.textContent=r.message||String(r))}})),t}async function Re(e,t){if(!Oe(e,t))return!1;const n=Pe(e);if(Le()==="os")try{return await Ie({...n,peer:e.peer,tag:e.id}),"os"}catch{return J({...n,peer:e.peer,onOpen:t.onOpen}),"in-app"}return J({...n,peer:e.peer,onOpen:t.onOpen}),"in-app"}const He={BASE_URL:"/portal/"},xe=He||{},re=(xe.BASE_URL||"/").replace(/\/?$/,"/");function N(){return(location.hash||"").startsWith("#/")}function Be(){if(N()){const n=location.hash.slice(1);return w(n.split("?")[0])}let e=location.pathname||"/";const t=re.replace(/\/$/,"")||"";return t&&(e===t||e.startsWith(`${t}/`))&&(e=e.slice(t.length)||"/"),w(e)}function w(e){let t=e||"/";return t.startsWith("/")||(t=`/${t}`),t.length>1&&t.endsWith("/")&&(t=t.slice(0,-1)),t||"/"}function Ue(e){const t=w(e);if(t==="/calls"||t.startsWith("/calls/"))return{page:"calls",path:t};if(t==="/messaging"||t.startsWith("/messaging/")){const n=t==="/messaging"?"/":t.slice(10);return{page:"messaging",path:t,rest:w(n)}}return t==="/routing"||t.startsWith("/routing/")?{page:"routing",path:t}:t==="/simulator"||t.startsWith("/simulator/")?{page:"simulator",path:t}:{page:"home",path:"/"}}function A(){return Ue(Be())}function $(e){const t=w(e);if(N())return`#${t}`;const n=t==="/"?"":t.replace(/^\//,"");return`${re}${n}`}function qe(e){const t=w(e);if(N()){history.replaceState(null,"",`${location.pathname}${location.search}#${t}`);return}history.replaceState({},"",$(t))}function B(e){const t=w(e);if(N()){location.hash=t;return}const n=$(t);if(`${location.pathname}${location.search}`===n&&!location.hash){window.dispatchEvent(new PopStateEvent("popstate"));return}history.pushState({},"",n),window.dispatchEvent(new PopStateEvent("popstate"))}function je(e){window.addEventListener("hashchange",e),window.addEventListener("popstate",e)}function De(e){return e?'<span class="badge-mock">Local mock</span>':""}function We(){const e=G(),t=e&&typeof Notification<"u"?Notification.permission:"denied";return e?t==="granted"?`
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
    `}function I({page:e,mock:t,extra:n=""}){const s=ae(),a=`<span id="unread-badge" class="nav-badge"${s?"":" hidden"}>${s}</span>`,r=(i,c,u)=>{const y=e===i?'aria-current="page"':"",_=i==="messaging"?a:"";return`<a class="cockpit-link${e===i?" on":""}" href="${$(c)}" ${y}>${u}${_}</a>`};return`
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
        ${We()}
        ${De(t)}
        ${n}
      </div>
    </header>
  `}function M(e){if(!e)return"";const t=String(e).trim(),n=t.replace(/\D/g,"");return n==="15550100"||n==="15555550100"||n==="5550100"||n==="15555550123"?"Mock +1 555 0100":/555/.test(n)&&n.length>=7?`Mock ${t}`:t}const U=new Set;let R=!1,F=null,ie={seated:["gsm_bus","openclaw_bus"],echoGsm:!1,deafened:[]};function Rt(e){return U.add(e),()=>U.delete(e)}function oe(){const e=Ge();for(const t of U)t(e)}function Ge(){return{active:R,path:F,snapshot:ie}}function Ht({seated:e,echoGsm:t,deafened:n}){R||(ie={seated:[...e||[]],echoGsm:!!t,deafened:[...n||[]]})}function xt(e){return e==="loopback"?{seated:["gsm_bus"],echoGsm:!0,deafened:[],mode:"loopback",label:"GSM echo"}:{seated:["gsm_bus","openclaw_bus"],echoGsm:!1,deafened:[],mode:"openclaw",label:"Agent"}}function Fe(e){R=!0,F=e,oe()}function Ve(){R=!1,F=null,oe()}function Ye(){const e="".replace(/\/$/,"");return e||(typeof location<"u"?location.origin:"http://hub.mining-ling.ts.net:8787")}let h=null;function L(e,t){return`<div class="stereo-meter" id="${e}">
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
  </div>`}function ze({agentOn:e=!0}={}){const t=e?"":"checked",n=e?"checked":"";return`
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
          <input id="hub-url" type="url" value="${Ye()}" />
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
        ${L("meter-mic","Mic uplink")}
        ${L("meter-tone","Test tone uplink")}
        ${L("meter-downlink","Earpiece (from hub)")}
        ${L("meter-sidetone","Local sidetone")}
      </section>
      <section class="card">
        <h2>Event log</h2>
        <pre id="log"></pre>
      </section>
    </div>
  `}async function Je(e){h==null||h(),h=null;const{mountCallSimulator:t}=await k(async()=>{const{mountCallSimulator:n}=await import("./main-r-Rna2Qa.js");return{mountCallSimulator:n}},[]);h=t(e,{onCallStart:n=>Fe(n),onCallEnd:()=>Ve()})}async function Ke(e){const t=await P();e.innerHTML=`
    ${I({page:"simulator",mock:t.mock})}
    ${ze({agentOn:!0})}
  `,await Je(e.querySelector(".call-sim"))}function Qe(){h==null||h(),h=null}async function Xe(e){const t=await P(),n=t.data||{},s=await se(),a=Array.isArray(s.data)?s.data:[],r=await we(t.mock?null:n),i=et(a,n.last_call_tap),c=!!(t.mock||s.mock),u=n.mode||"idle";e.innerHTML=`
    ${I({page:"home",mock:c})}
    <main class="dash">
      <div class="dash-col">
        <section class="card">
          <h2>Hub status</h2>
          ${Ze(n)}
        </section>
        <section class="card">
          <h2>Last call</h2>
          ${i?st(i):'<p class="muted">No calls recorded.</p>'}
        </section>
      </div>
      <div class="dash-col">
        <section class="card">
          <h2>Token / usage</h2>
          ${at(r.data)}
        </section>
      </div>
      <section class="dash-strip">
        <div class="dash-strip-preset">
          <span class="lamp-label">Routing preset</span>
          <span class="lamp-value">${p(String(u).toUpperCase())}</span>
        </div>
        <pre class="teletype dash-teletype">${p(X(u))}</pre>
        <div class="dash-strip-links">
          <a class="primary-link secondary" href="${$("/routing")}">Open switchboard</a>
          <a class="primary-link secondary" href="${$("/simulator")}">Call simulator</a>
        </div>
      </section>
      <p class="dash-tail">Last preset applied: ${p(String(u).toUpperCase())} · ${p(X(u))}${i?` · last call ${p(le(i.started||""))}`:""}</p>
    </main>
  `}function X(e){return e==="openclaw"?"openclaw_bus:monitor_* |-> gsm_bus:playback_*":e==="loopback"?"(no pw-link; uplink → gsm_bus.monitor)":e==="conference"?"conference star: GSM and OpenClaw; WA/TG listen GSM only":e==="clear"?"(buses idle)":String(e)}function Ze(e){const t=!!e.ok,n=e.openclaw_talk||"unknown",s=e.mode||"idle";return`<div class="lamp-row">
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
  </div>`}function et(e,t){const n=e[0]||null,s=tt(t),a=nt(t);if(!n&&!t)return null;const r=(n==null?void 0:n.started_at)||(t==null?void 0:t.ended_at)||(t==null?void 0:t.started_at)||null,i=(n==null?void 0:n.duration_sec)??s??null,c=(n==null?void 0:n.number)||a||"";return{started:r,duration:i,number:c,fromTap:!!t&&!n}}function tt(e){if(!e)return null;const t=e.streams||{};let n=0;for(const a of Object.values(t)){const r=Number(a==null?void 0:a.seconds);r>n&&(n=r)}if(n>0)return Math.round(n);const s=e.mixes||{};for(const a of Object.values(s)){const r=Number(a==null?void 0:a.seconds);r>n&&(n=r)}return n>0?Math.round(n):null}function nt(e){if(!e)return"";const t=e.meta||{};return t.number||t.peer||t.from||""}function st(e){const t=e.started?le(e.started):"time unknown",n=e.duration!=null?rt(e.duration):"duration unknown",s=e.number?M(e.number):"number unknown";return`<p class="fixture-num">${p(s)}</p>
    <p class="muted last-call-meta">${p(n)}<br/>${p(t)}</p>`}function at(e){if(!e)return'<p class="muted">No usage data.</p>';const t=e.label||"Usage",n=e.used==null?"n/a":String(e.used),s=e.limit==null?"n/a":String(e.limit),a=e.note?`<p class="muted">${p(e.note)}</p>`:"";return`<p>${p(t)}</p>
    <div class="kv">
      <span class="lamp-label">Used</span><span class="lamp-value">${p(n)}</span>
      <span class="lamp-label">Limit</span><span class="lamp-value">${p(s)}</span>
    </div>
    ${a}`}function rt(e){const t=Number(e)||0,n=Math.floor(t/60),s=t%60;return n?`${n}m ${s}s`:`${s}s`}function le(e){const t=new Date(e);if(Number.isNaN(t.getTime()))return e;const n=Date.now()-t.getTime(),s=Math.abs(n),a=new Intl.RelativeTimeFormat(void 0,{numeric:"auto"}),r=[{amount:60,unit:"second"},{amount:60,unit:"minute"},{amount:24,unit:"hour"},{amount:7,unit:"day"},{amount:4.34524,unit:"week"},{amount:12,unit:"month"},{amount:Number.POSITIVE_INFINITY,unit:"year"}];let i=s/1e3;for(const c of r){if(i<c.amount){const u=Math.round(i)*(n>=0?-1:1);return a.format(u,c.unit)}i/=c.amount}return t.toLocaleString()}function p(e){return String(e).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;")}const O="No OpenClaw recording for this call.",l={calls:[],error:"",mock:!1,playingId:null,playError:""};let d=null,q=null;function it(){l.playingId=null,l.playError="",d&&v(d)}function ot(){l.playingId=null,l.playError=O,d&&v(d)}function lt(e){return q=e,e&&(e.preload="none",e.addEventListener("ended",it),e.addEventListener("error",ot),e)}function ct(){return q||(typeof Audio>"u"?null:lt(new Audio))}function ut(e,t=ct()){if(l.playError="",!e)return;if(l.playingId===e&&t&&!t.paused){t.pause(),l.playingId=null,d&&v(d);return}if(!t){l.playingId=null,l.playError=O,d&&v(d);return}l.playingId&&l.playingId!==e&&t.pause();const n=_e(e);t.src=n;let s;try{s=t.play()}catch{l.playingId=null,l.playError=O,d&&v(d);return}l.playingId=e,d&&v(d),s&&typeof s.then=="function"&&s.catch(()=>{l.playingId===e&&(l.playingId=null,l.playError=O,d&&v(d))})}async function ce(e){d=e;try{l.error="";const t=await P();l.mock=!!t.mock;const n=await se();l.calls=Array.isArray(n.data)?n.data:[],l.mock=l.mock||!!n.mock}catch(t){l.error=t.message||String(t)}v(e)}function dt(e){d=e}async function pt(e,t){const n=e||d;n&&t==="call"&&await ce(n)}function v(e){e.innerHTML=`
    ${I({page:"calls",mock:l.mock})}
    ${l.error?`<div class="error">${m(l.error)}</div>`:""}
    <main>${ft()}</main>
  `,S(),mt(e)}function ft(){return l.calls.length?`<div class="call-list">${l.calls.map(e=>{const t=e.direction!=="out",n=t?"Incoming":"Outgoing",s=e.switchboard_mode||"idle",a=e.path==="public"||e.path==="tailscale"?e.path:"",r=a?`<span class="preset-chip" data-entrance="${x(a)}">${m(a)}</span>`:"",i=l.playingId===e.id,c=e.session_id?`<div class="meta session-id">${m(e.session_id)}</div>`:"",u=Z(e.transcript)?`<div class="meta transcript" title="${x(e.transcript.path||"")}">${m(Z(e.transcript))}</div>`:"",y=i?"Pause":"Play",_=i?"❚❚":"▶";return`<button type="button" class="call-card${i?" playing":""}" data-call-id="${x(e.id)}" aria-pressed="${i?"true":"false"}" title="Play OpenClaw recording">
        <div class="call-card-head">
          <span class="lamp ${t?"good":"warn"}"></span>
          <h2>${m(n)}</h2>
          <span class="preset-chip">${m(s)}</span>
          ${r}
          <span class="call-play" aria-hidden="true">${_}</span>
          <span class="sr-only">${y} recording</span>
        </div>
        <p class="fixture-num">${m(M(e.number)||e.number||"")}</p>
        <div class="call-dur">${m(ht(e.duration_sec))}</div>
        <div class="meta">${m(gt(e.started_at))}</div>
        ${c}
        ${u}
      </button>`}).join("")}</div>${l.playError?`<div class="call-play-empty">${m(l.playError)}</div>`:""}`:'<div class="empty">Mixer idle. No GSM or simulator legs bridged.</div>'}function mt(e){e.querySelectorAll("[data-call-id]").forEach(t=>{t.addEventListener("click",()=>{const n=t.getAttribute("data-call-id");n&&ut(n)})})}function Z(e){if(!e||!e.status)return"";const t=Number(e.turns),n=Number.isFinite(t)?`${t} turn${t===1?"":"s"}`:"no turns",s=e.file?` · ${e.file}`:"";return`transcript ${e.status} · ${n}${s}`}function gt(e){if(!e)return"";const t=new Date(e);return Number.isNaN(t.getTime())?e:t.toLocaleString([],{month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"})}function ht(e){const t=Number(e)||0,n=Math.floor(t/60),s=t%60;return n?`${n}m ${s}s`:`${s}s`}function m(e){return String(e).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;")}function x(e){return m(e).replace(/'/g,"&#39;")}const o={peer:null,threads:[],messages:[],error:"",sending:!1,mock:!1,suppressAutoOpen:!1};let V=null;function yt(e){return!Array.isArray(e)||e.length===0?null:[...e].sort((n,s)=>String(s.lastAt||"").localeCompare(String(n.lastAt||"")))[0].peer}function bt({peer:e,suppressAutoOpen:t,threads:n}){return e||(t?null:yt(n))}function vt(e){if(!e)return;const t=()=>{e.scrollTop=e.scrollHeight};t(),typeof requestAnimationFrame=="function"&&requestAnimationFrame(t)}function $t(e){if(e.startsWith("/thread/")){o.peer=decodeURIComponent(e.slice(8)),o.suppressAutoOpen=!1;return}o.peer=null}function wt(){return{peer:o.peer}}function ue(){o.suppressAutoOpen=!1}function de(e){e&&(o.suppressAutoOpen=!1,o.peer=e,W(e),S(),B(`/messaging/thread/${encodeURIComponent(e)}`))}function ee(){o.peer?B(`/messaging/thread/${encodeURIComponent(o.peer)}`):B("/messaging")}async function Y(e){V=e;try{o.error="";const t=await P();o.mock=!!t.mock,o.threads=await Se();const n=bt({peer:o.peer,suppressAutoOpen:o.suppressAutoOpen,threads:o.threads});!o.peer&&n&&qe(`/messaging/thread/${encodeURIComponent(n)}`),o.peer=n,o.peer?(W(o.peer),o.messages=await Ee(o.peer)):o.messages=[]}catch(t){o.error=t.message||String(t)}pe(e)}function pe(e){var t;e.innerHTML=`
    ${I({page:"messaging",mock:o.mock})}
    ${o.error?`<div class="error">${g(o.error)}</div>`:""}
    <main>${kt()}</main>
  `,S(),e.querySelectorAll("[data-peer]").forEach(n=>{n.addEventListener("click",()=>{o.suppressAutoOpen=!1,o.peer=n.getAttribute("data-peer"),W(o.peer),ee()})}),(t=e.querySelector("[data-threads-back]"))==null||t.addEventListener("click",n=>{n.preventDefault(),o.suppressAutoOpen=!0,o.peer=null,ee()}),_t(e)}function kt(){const e=!!o.peer;return`<div class="msg-layout${e?" has-peer":""}">
    <div class="msg-list-pane">${St()}</div>
    <div class="msg-detail-pane">${e?Et():'<div class="empty">Select a thread. Pixel forwarder idle until a peer is chosen.</div>'}</div>
  </div>`}function St(){return o.threads.length?`<ul class="list">${o.threads.map(e=>{const t=e.peer===o.peer?" on":"",n=C.unread[e.peer]||0,s=n?`<span class="thread-unread">${n}</span>`:"";return`
      <li>
        <button class="row${t}" data-peer="${Ct(e.peer)}">
          <span class="row-head"><strong>${g(M(e.peer)||e.peer)}</strong>${s}</span>
          <span>${g(e.lastBody||"")}</span>
          <span class="meta">${g(fe(e.lastAt))}</span>
        </button>
      </li>`}).join("")}</ul>`:'<div class="empty">No SMS on the wire. Pixel forwarder idle.</div>'}function Et(){const e=o.messages.map(n=>{const s=n.direction==="out"?"out":"in",a=n.status&&n.status!=="received"&&n.status!=="sent"?`<span class="status-pill">${g(n.status)}${n.error?` ${g(n.error)}`:""}</span>`:"";return`<div class="bubble ${s}"><span class="bubble-body">${g(n.body||"")}</span>${a}<time>${g(fe(n.ts))}</time></div>`}).join(""),t=M(o.peer)||o.peer;return`
    <div class="thread">
      <div class="thread-head">
        <a class="back" data-threads-back href="${$("/messaging")}">‹ Threads</a>
        <span class="thread-peer">${g(t)}</span>
      </div>
      ${o.error?`<div class="error">${g(o.error)}</div>`:""}
      <div class="messages" id="messages">${e||'<div class="empty">No messages in this thread.</div>'}</div>
      <form class="compose" id="compose">
        <textarea name="body" rows="1" placeholder="SMS to mock peer" required></textarea>
        <button type="submit" ${o.sending?"disabled":""}>Send</button>
      </form>
    </div>
  `}function _t(e){vt(e.querySelector("#messages"));const t=e.querySelector("#compose");t&&t.addEventListener("submit",async n=>{n.preventDefault();const s=n.target.querySelector("textarea"),a=n.target.querySelector("button"),r=s.value.trim();if(!(!r||o.sending)){o.sending=!0,a&&(a.disabled=!0);try{await Te(o.peer,r),o.sending=!1,await Y(e)}catch(i){o.sending=!1,o.error=i.message||String(i),pe(e)}}})}async function Tt(e,t){const n=e||V;n&&(t==="message"||t==="outbox")&&await Y(n)}function At(e){V=e}function fe(e){if(!e)return"";const t=new Date(e);return Number.isNaN(t.getTime())?e:t.toLocaleString([],{month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"})}function g(e){return String(e).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;")}function Ct(e){return g(e).replace(/'/g,"&#39;")}const f=document.getElementById("app"),Lt=Object.assign({"./routing/index.js":()=>k(()=>import("./index-CsfvnLur.js"),__vite__mapDeps([0,1]))});let T=null,te=null;function Ot(){return A().page}async function Pt(e,t){const n=t&&typeof t=="object"?{...t,type:t.type||e}:{type:e};if(e==="message"){const s=wt(),a=await Re(n,{selectedPeer:s.peer,documentHidden:document.hidden,route:Ot(),onOpen:i=>{i&&de(i)}}),r=A().page==="messaging"&&s.peer===n.peer&&!document.hidden;a&&n.peer&&!r&&(Ce(n.peer),S())}A().page==="messaging"?await Tt(f,e):A().page==="calls"?await pt(f,e):S()}function Nt(){te||(te=Ae((e,t)=>{Pt(e,t).catch(n=>console.warn(n))}))}function It(){const e="/portal/sw.js".replace(/\/{2,}/g,"/");"serviceWorker"in navigator&&(navigator.serviceWorker.register(e).catch(t=>console.warn("sw",t)),navigator.serviceWorker.addEventListener("message",t=>{var n,s;((n=t.data)==null?void 0:n.type)==="open-peer"&&t.data.peer?de(t.data.peer):((s=t.data)==null?void 0:s.type)==="open-peer"&&(ue(),window.location.href=$("/messaging"))}))}async function me(){T==null||T(),T=null;const e=A();e.page!=="messaging"&&ue(),e.page!=="simulator"&&e.page!=="routing"&&Qe(),e.page==="messaging"?($t(e.rest),At(f),await Y(f)):e.page==="calls"?(dt(f),await ce(f)):e.page==="routing"?await Mt():e.page==="simulator"?await Ke(f):await Xe(f),Me(),S()}async function Mt(){const e=Object.values(Lt);if(e.length){const t=await e[0]();if(typeof t.mount=="function"){T=await t.mount(f)||null;return}if(typeof t.render=="function"){await t.render(f);return}if(typeof t.default=="function"){await t.default(f);return}}f.innerHTML=`
    <header class="app-bar">
      <a class="back" href="${$("/")}">‹ Home</a>
      <h1>Routing</h1>
      <span></span>
    </header>
    <main class="dash">
      <p class="muted">Routing UI is not in this build yet. The hub switchboard still lives on the phone SMS commands.</p>
    </main>
  `}je(()=>{me()});It();Nt();me();export{I as a,Ge as g,Rt as s,Ht as u,xt as v};
