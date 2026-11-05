"use strict";
const $=id=>document.getElementById(id);
const fmt=(x,d=2)=>x===null||x===undefined||x===""||!Number.isFinite(Number(x))?"—":Number(x).toLocaleString("en-IN",{maximumFractionDigits:d,minimumFractionDigits:d});
const money=x=>x===null||x===undefined||x===""?"—":"₹"+fmt(x);
const safeText=v=>v===undefined||v===null||v===""?"—":String(v);
let current=null, selectedSymbol=null, ws=null;
function text(id,value){const node=$(id);if(node)node.textContent=safeText(value);}
function el(tag,value,cls){const item=document.createElement(tag);if(value!==undefined&&value!==null)item.textContent=String(value);if(cls)item.className=cls;return item;}
function pill(id,label,kind){const node=$(id);node.textContent=label;node.className="pill"+(kind?" "+kind:"");}
function emptyRow(cols,message){const row=el("tr"),cell=el("td",message,"empty");cell.colSpan=cols;row.append(cell);return row;}
function stamp(raw){if(!raw)return "—";const d=new Date(raw);return Number.isNaN(d.getTime())?"—":d.toLocaleString("en-IN",{timeZone:"Asia/Kolkata",hour:"2-digit",minute:"2-digit",day:"2-digit",month:"short",hour12:false});}
function td(value,cls){return el("td",value,cls);}
function row(...cells){const tr=el("tr");for(const cell of cells)tr.append(cell);return tr;}
function renderSignals(items,quotes){
 const body=$("signalBody");body.replaceChildren();
 if(!items.length){body.append(emptyRow(6,"No current-session verified news signals. New orders remain blocked."));return;}
 for(const s of [...items].sort((a,b)=>(a.side||"").localeCompare(b.side||"")||(a.rank||0)-(b.rank||0))){
  const q=quotes[s.symbol]||{};
  const price=q.fresh&&Number.isFinite(Number(q.ltp))?Number(q.ltp):null;
  const opened=q.open&&Number.isFinite(Number(q.open))?Number(q.open):null;
  const change=price!==null&&opened?100*(price-opened)/opened:null;
  const stock=td(s.symbol,"code"),button=el("button",s.symbol);
  button.type="button";button.addEventListener("click",()=>{selectedSymbol=s.symbol;renderEvidence();});
  stock.replaceChildren(button);
  body.append(row(td(s.rank),stock,td(s.side,"side "+(s.side==="LONG"?"long":"short")),
      td(money(price),"right-align"),td(money(opened),"right-align"),
      td(change===null?"—":(change>=0?"+":"")+fmt(change)+"%","right-align "+(change===null?"":change>=0?"positive":"negative"))));
 }
}
function renderEvidence(){
 const target=$("evidence");target.replaceChildren();
 const signal=(current?.signals||[]).find(s=>s.symbol===selectedSymbol);
 pill("selected",signal?signal.symbol+" · "+signal.side:"SELECT A STOCK");
 if(!signal){target.append(el("div","Select a verified stock to inspect supporting news.","muted"));return;}
 target.append(el("b",signal.news_summary||"No summary provided"));
 target.append(el("p",signal.reason||"No rationale provided"));
 target.append(el("div","First available: "+stamp(signal.first_available_at)+" IST","meta"));
 const sources=el("div",null,"links");
 for(const ref of signal.sources||[]){
  try{const u=new URL(ref.url);if(u.protocol!=="https:")continue;
   const a=el("a",(ref.title||u.hostname)+" · "+stamp(ref.published_at)+" IST");
   a.href=u.href;a.target="_blank";a.rel="noopener noreferrer";sources.append(a);
  }catch(_){}
 }
 target.append(sources);
}
function renderPositions(positions,asOf){
 const body=$("positionBody");body.replaceChildren();
 text("positionsAsOf","Broker book: "+stamp(asOf));
 if(!positions.length){body.append(emptyRow(5,"Kotak has reported no NSE cash positions."));return;}
 for(const p of positions){
  const n=Number(p.net_quantity||0),side=p.side||(n>0?"LONG":n<0?"SHORT":"FLAT");
  const pnl=p.gross_mark_to_market;
  body.append(row(td(p.symbol,"code"),td(side,"side "+(side==="LONG"?"long":side==="SHORT"?"short":"")),
      td(fmt(n,0),"right-align"),td(money(p.ltp),"right-align"),
      td(money(pnl),"right-align "+(pnl===null||pnl===undefined?"":Number(pnl)>=0?"positive":"negative"))));
 }
}
function renderBook(id,rows,kind){
 const body=$(id);body.replaceChildren();
 if(!rows.length){body.append(emptyRow(5,kind==="orders"?"No Kotak orders reported.":"No Kotak fills reported."));return;}
 for(const item of rows.slice(-100).reverse()){
  body.append(kind==="orders"?
    row(td(item.broker_order_id,"code"),td(item.symbol,"code"),td(item.side),td(item.order_quantity,"right-align"),td(item.status)):
    row(td(item.fill_id,"code"),td(item.symbol,"code"),td(item.filled_quantity,"right-align"),td(money(item.fill_price),"right-align"),td(stamp(item.fill_time))));
 }
}
function render(s){
 if(!s||s.mode!=="LIVE_KOTAK_NEO_ONLY"){text("notice","Unexpected live service response. Orders blocked.");$("arm").disabled=true;return;}
 current=s;
 const signals=Array.isArray(s.signals)?s.signals:[],positions=Array.isArray(s.positions)?s.positions:[];
 const orders=Array.isArray(s.orders)?s.orders:[],fills=Array.isArray(s.fills)?s.fills:[];
 const quotes=s.quotes||{},brokerReady=s.broker_connected===true;
 pill("source",s.signal_status||"UNAVAILABLE",s.signal_status==="READY"?"green":"red");
 pill("connection",brokerReady?"KOTAK CONNECTED":"KOTAK DISCONNECTED",brokerReady?"green":"red");
 pill("mode",s.armed?"REAL MONEY · ARMED":"REAL MONEY · DISARMED",s.armed?"green":"red");
 pill("gate",s.armed?"LIVE ARMED":"DISARMED",s.armed?"green":"red");
 pill("cutoff","Research cutoff: "+stamp(s.research_cutoff_at)+" IST");
 text("session",s.session_date);text("funds",money(s.available_cash));
 text("posCount",positions.filter(p=>Number(p.net_quantity)!==0).length);
 const active=positions.filter(p=>Number(p.net_quantity)!==0);
 const mtm=active.some(p=>p.gross_mark_to_market===null||p.gross_mark_to_market===undefined)?
   null:positions.reduce((acc,p)=>acc+Number(p.gross_mark_to_market||0),0);
 text("mtm",money(mtm));$("mtm").className="value "+(mtm===null?"":mtm>=0?"positive":"negative");
 text("count",signals.length);
 const vix=quotes.INDIAVIX||{};
 text("vix",vix.fresh?fmt(vix.ltp):"—");
 text("vixAsOf",vix.fresh?"Dhan index quote · "+stamp(vix.ohlc_at)+" IST":"Dhan index quote unavailable · not a trade trigger");
 text("risk",s.readiness?"READY":"BLOCKED");$("risk").className="value "+(s.readiness?"positive":"negative");
 text("riskReason",s.readiness?"Risk, broker and feed checks passed":
    "No new orders: "+safeText(s.supervisor_status||"verification required"));
 text("policy","09:15 one-minute opening-range breakout · cutoff "+safeText(s.strategy?.entry_cutoff)+
   " · exit "+safeText(s.strategy?.exit_time)+" · "+safeText(s.strategy?.max_long)+" LONG / "+
   safeText(s.strategy?.max_short)+" SHORT · max "+safeText(s.strategy?.max_positions)+" positions · ₹"+fmt(s.strategy?.allocation_per_position,0)+
   " per stock · gross cap ₹"+fmt(s.strategy?.gross_cap,0)+" · exit "+safeText(s.strategy?.exit_time)+" IST");
 const fields={settingAllocation:s.strategy?.allocation_per_position,settingMaxPositions:s.strategy?.max_positions,
   settingGross:s.strategy?.gross_cap,settingMaxLong:s.strategy?.max_long,settingMaxShort:s.strategy?.max_short,
   settingCutoff:s.strategy?.entry_cutoff,settingExit:s.strategy?.exit_time};
 if(!$("settingsForm").contains(document.activeElement))for(const [id,value] of Object.entries(fields))if(value!==undefined)$(id).value=value;
 const canEdit=!s.armed;$("saveSettings").disabled=!canEdit;
 pill("settingsState",canEdit?"DISARMED · EDITABLE":"ARMED · LOCKED",canEdit?"green":"red");
 const feed=s.market_feed?.state||"NOT_STARTED";
 const dhanAuth=s.dhan_authentication?.state||"NEEDS_SETUP";
 text("diagnostics","Dhan auth: "+dhanAuth+" · feed: "+feed+" · Kotak: "+(brokerReady?"connected":"disconnected")+
   " · Last broker refresh: "+stamp(s.broker_books_updated_at)+" IST");
 const mapped=s.instrument_mappings||[];
 text("mappingInfo",mapped.length?mapped.filter(x=>x.status==="READY").length+" / "+mapped.length+" signal instruments mapped in Dhan and Kotak":"Waiting for verified Dhan and Kotak instrument maps");
 const mappingBody=$("mappingBody");mappingBody.replaceChildren();
 if(!mapped.length)mappingBody.append(emptyRow(5,"Mappings appear after today’s signal report is verified."));
 for(const m of mapped)mappingBody.append(row(td(m.symbol,"code"),td(m.dhan_security_id||"—"),
   td(m.kotak_trading_symbol||"—","code"),td(m.kotak_token||"—","code"),
   td(m.status||"UNAVAILABLE","side "+(m.status==="READY"?"long":"short"))));
 const canArm=s.orders_permitted_by_server===true&&s.readiness===true&&!s.armed;
 $("arm").disabled=!canArm;$("disarm").disabled=!s.armed;
 text("armNotice",!s.orders_permitted_by_server?"Server-side LIVE_ORDERS_ALLOWED is OFF. Monitoring is read-only.":
    s.armed?"Actual Kotak orders are ARMED. Monitor your broker account.":s.readiness?
    "Operator may arm with authorization and TOTP.":"Trading gate blocked: verify signal, market data, broker and risk.");
 const notice=$("notice");notice.className="notice"+(s.armed?" error":s.readiness?" ok":"");
 notice.textContent=s.armed?"REAL MONEY ARMED: Actual Kotak orders can be sent. Review broker positions and risk immediately.":
   "LIVE MONITORING: Real orders DISARMED. Research: "+safeText(s.signal_status)+
   "; Dhan: "+feed+".";
 renderSignals(signals,quotes);
 if(!selectedSymbol||!signals.some(x=>x.symbol===selectedSymbol))selectedSymbol=signals[0]?.symbol||null;
 renderEvidence();renderPositions(positions,s.broker_books_updated_at);
 renderBook("orderBody",orders,"orders");renderBook("fillBody",fills,"fills");
 text("orderCount",orders.length+" broker orders");text("fillCount",fills.length+" fills");
 text("updated","Dashboard updated: "+stamp(s.server_time)+" IST");
 text("errors",[s.error,s.dhan_authentication?.error,...(s.runtime_errors||[])].filter(Boolean).join(" | ")||"No runtime errors reported.");
 const intents=s.intents||[];
 text("intentInfo","Durable order intents: "+intents.length+". Unreconciled: "+
   intents.filter(x=>["RESERVED","SUBMISSION_UNKNOWN","PARTIAL","ACKNOWLEDGED"].includes(x.state)).length+
   ". Intents are not broker fills.");
}
async function getState(){
 try{const r=await fetch("/api/state",{credentials:"same-origin",cache:"no-store"});
  if(r.status===401){location.assign("/login");return;}
  if(!r.ok)throw Error("HTTP "+r.status);
  render(await r.json());
 }catch(e){pill("connection","DISCONNECTED","red");$("arm").disabled=true;
  text("notice","Live data unavailable. Verify directly with Kotak Neo. "+e.message);}
}
function connect(){
 if(ws&&(ws.readyState===WebSocket.OPEN||ws.readyState===WebSocket.CONNECTING))return;
 const protocol=location.protocol==="https:"?"wss:":"ws:";
 ws=new WebSocket(protocol+"//"+location.host+"/ws");
 ws.onmessage=e=>{try{render(JSON.parse(e.data));}catch(_){$("arm").disabled=true;}};
 ws.onclose=e=>{if(e.code===4401){location.assign("/login");return;}setTimeout(connect,3000);};
 ws.onerror=()=>ws.close();
}
$("arm").addEventListener("click",()=>{$("armModal").classList.add("open");$("authorization").focus();$("armError").textContent="";});
$("cancelArm").addEventListener("click",()=>{$("armModal").classList.remove("open");$("otp").value="";});
$("armForm").addEventListener("submit",async e=>{
 e.preventDefault();
 try{const r=await fetch("/api/live/arm",{method:"POST",credentials:"same-origin",
  headers:{"Content-Type":"application/json"},
  body:JSON.stringify({acknowledgement:$("authorization").value,totp:$("otp").value})});
  if(!r.ok){const d=await r.json().catch(()=>({}));throw Error(typeof d.detail==="string"?d.detail:"Authorization denied");}
  $("armModal").classList.remove("open");$("authorization").value="";await getState();
 }catch(e){$("armError").textContent=e.message;}finally{$("otp").value="";}
});
$("disarm").addEventListener("click",async()=>{
 try{const r=await fetch("/api/live/disarm",{method:"POST",credentials:"same-origin"});
  if(!r.ok)throw Error("Disarm failed. Verify broker account directly.");await getState();}
 catch(e){text("notice",e.message);}
});
$("settingsForm").addEventListener("submit",async e=>{
 e.preventDefault();const button=$("saveSettings");button.disabled=true;
 const payload={per_position:$("settingAllocation").value,max_positions:$("settingMaxPositions").value,
  gross_cap:$("settingGross").value,max_long:$("settingMaxLong").value,max_short:$("settingMaxShort").value,
  entry_cutoff:$("settingCutoff").value,exit_time:$("settingExit").value};
 try{const r=await fetch("/api/live/settings",{method:"POST",credentials:"same-origin",
  headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
  const data=await r.json().catch(()=>({}));if(!r.ok)throw Error(data.detail||"Settings were not saved");
  text("settingsMessage","Saved on server. Review the amount, position limit, gross cap and session times before arming.");await getState();
 }catch(err){text("settingsMessage",err.message);}finally{button.disabled=current?.armed===true;}
});
$("logout").addEventListener("click",async()=>{try{await fetch("/api/auth/logout",{method:"POST",credentials:"same-origin"});}finally{location.assign("/login");}});
getState();connect();setInterval(getState,10000);
