"use strict";
const $=id=>document.getElementById(id);
const BASE_PATH=new URL(document.baseURI).pathname.replace(/\/$/,"");
const SEP=String.fromCharCode(47);
const route=path=>BASE_PATH&&path.startsWith(BASE_PATH+SEP)?path:(BASE_PATH||"")+path;
const fmt=(x,d=2)=>x===null||x===undefined||x===""||!Number.isFinite(Number(x))?"—":Number(x).toLocaleString("en-IN",{maximumFractionDigits:d,minimumFractionDigits:d});
const money=x=>x===null||x===undefined||x===""?"—":"₹"+fmt(x);
const safeText=v=>v===undefined||v===null||v===""?"—":String(v);
const human=v=>safeText(v).replaceAll("_"," ").toLowerCase().replace(/^./,c=>c.toUpperCase());
const VIEWS={overview:["Overview","Session overview","YOUR SESSION AT A GLANCE","Track live P&L, the opening workflow, and order readiness."],signals:["News signals","Verified news signals","RESEARCH WORKSPACE","Inspect the current watchlist and the evidence behind each candidate."],market:["Market monitor","Opening range & instruments","MARKET DATA","Follow the opening range, observed crossings, and exact broker mappings."],positions:["Positions & P&L","Positions & profit and loss","LIVE ACCOUNT","Broker-confirmed quantities, fresh valuations, and transparent gross P&L."],orders:["Order book","Broker order book","EXECUTION WORKSPACE","Search every reported order and inspect its broker status."],fills:["Executed fills","Executed fills","BROKER-CONFIRMED ACTIVITY","Actual reported executions, quantities, and fill prices."],journal:["Trade journal","Trade journal & performance","HISTORICAL PERFORMANCE","Review persisted fills, confirmed trade outcomes, and order lifecycle events."],connections:["Connections & checks","Connections & diagnostics","SYSTEM OPERATIONS","Inspect authentication, data freshness, and read-only connection tests."],settings:["Settings","Trading configuration","SESSION CONTROLS","Review sizing, position limits, session times, and order authorization."]};
const GATES={CURRENT_SESSION_SIGNALS:["Current-session research","signals"],KOTAK_AUTHENTICATION:["Kotak authentication","connections"],DHAN_AUTHENTICATION:["Dhan authentication","connections"],BROKER_BOOKS:["Fresh broker account books","connections"],INSTRUMENT_MAPPING:["Exact instrument mappings","market"],OPENING_RANGE:["Completed opening candle","market"],MARKET_FEED:["Market feed connection","market"],FRESH_QUOTES:["Fresh exchange prices","market"],MARKET_ENTRY_WINDOW:["Entry time window","settings"],FUNDS:["Funds and gross allocation","settings"],EXECUTION_RUNTIME:["Execution runtime","connections"],SUPERVISOR_ERROR:["Supervisor health","connections"],PRIOR_SESSION_RECOVERY:["Prior-session recovery","connections"],SERVER_ORDERS_DISABLED:["Server order permission","settings"]};
let current=null,selectedSymbol=null,ws=null,lastStateReceipt=0,lastServerTime=0,activeView="overview",settingsDirty=false,settingsSaving=false,stateStale=true,streamConnected=false,signalFilter="all",positionFilter="all",marketTab="orb",toastTimer,journalData=null,journalLoading=false,journalError=null;
const pages={positions:1,orders:1,fills:1},PAGE_SIZE=50,renderCache=new Map();
function text(id,value){const n=$(id);if(n){const next=safeText(value);if(n.textContent!==next)n.textContent=next;}}
function el(tag,value,cls){const n=document.createElement(tag);if(value!==undefined&&value!==null)n.textContent=String(value);if(cls)n.className=cls;return n;}
function pill(id,label,kind=""){const n=$(id);n.textContent=label;n.className="pill"+(kind?" "+kind:"");}
function td(value,cls){return el("td",value,cls);}
function row(...cells){const n=el("tr");n.append(...cells);return n;}
function emptyRow(cols,message){const r=el("tr"),c=el("td",message,"empty");c.colSpan=cols;r.append(c);return r;}
function stamp(raw){if(!raw)return "—";const d=new Date(raw);return Number.isNaN(d.getTime())?"—":d.toLocaleString("en-IN",{timeZone:"Asia/Kolkata",hour:"2-digit",minute:"2-digit",day:"2-digit",month:"short",hour12:false});}
function tone(value){return value===null||value===undefined?"":Number(value)>=0?"positive":"negative";}
function metric(id,value){
 const node=$(id);
 text(id,money(value));
 node.classList.remove("positive","negative");
 if(value!==null&&value!==undefined)node.classList.add(tone(value));
}
function changed(key,value){const signature=JSON.stringify(value);if(renderCache.get(key)===signature)return false;renderCache.set(key,signature);return true;}
function notify(message){text("toast",message);$("toast").hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$("toast").hidden=true,4200);}
function closeMenu(){$("sidebar").classList.remove("open");$("navScrim").hidden=true;$("menuToggle").setAttribute("aria-expanded","false");}
function showView(){
 let id=location.hash.slice(1);if(!VIEWS[id]){try{id=localStorage.getItem("kotak-desk-view");}catch{}if(!VIEWS[id])id="overview";history.replaceState(null,"","#"+id);}
 activeView=id;for(const name of Object.keys(VIEWS))$("view-"+name).hidden=name!==id;
 document.querySelectorAll("nav [data-view]").forEach(n=>{if(n.dataset.view===id)n.setAttribute("aria-current","page");else n.removeAttribute("aria-current");});
 const [title,heading,eyebrow,description]=VIEWS[id];text("viewTitle",title);text("pageTitle",heading);text("viewEyebrow",eyebrow);text("pageDescription",description);document.title=title+" · Kotak Neo Live Desk";
 try{localStorage.setItem("kotak-desk-view",id);}catch{}closeMenu();if(current)renderActive();if(id==="journal")loadJournal();
}
function gates(s,all=false){
 const blocked=new Set(s.readiness_blockers||[]),target=$(all?"allGates":"overviewGates");
 const ordered=[...blocked,...Object.keys(GATES).filter(key=>!blocked.has(key))];
 const keys=all?ordered:ordered.slice(0,5);
 if(!changed(target.id,[keys,[...blocked]]))return;const fragment=document.createDocumentFragment();
 for(const key of keys){const [label,view]=GATES[key]||[human(key),"connections"],isBlocked=blocked.has(key),link=el("a",null,"gate-row");link.href="#"+view;const name=el("span",null,"gate-name");name.append(el("i",isBlocked?"!":"✓","check-indicator"+(isBlocked?" blocked":"")),el("span",label));link.append(name,el("span",isBlocked?"Blocked":"Verified","gate-state"+(isBlocked?" blocked":"")));fragment.append(link);}target.replaceChildren(fragment);
}
function services(s){
 const dhan=s.dhan_authentication||{},kotak=s.kotak_authentication||{};
 return [{name:"Kotak Neo",mark:"KN",state:kotak.state||"NOT_STARTED",ok:kotak.state==="AUTHENTICATED",detail:"Next login: "+stamp(kotak.next_refresh)+" IST"},{name:"Dhan market data",mark:"DH",state:dhan.state||"NEEDS_SETUP",ok:dhan.state==="AUTHENTICATED",detail:"Next refresh: "+stamp(dhan.next_refresh)+" IST"},{name:"Account books",mark:"BK",state:s.broker_connected?"CONNECTED":"UNAVAILABLE",ok:s.broker_connected===true,detail:"Last sync: "+stamp(s.broker_books_updated_at)+" IST"},{name:"Dashboard stream",mark:"↔",state:streamConnected?"STREAMING":"POLLING",ok:lastStateReceipt>0&&Date.now()-lastStateReceipt<15000,detail:streamConnected?"Live WebSocket updates":"Read-only polling fallback"}];
}
function renderServices(s){
 const items=services(s);if(changed("overviewConnections",items)){const f=document.createDocumentFragment();for(const item of items.slice(0,3)){const r=el("div",null,"connection-row"),copy=el("div",null,"service-copy");copy.append(el("strong",item.name),el("small",item.detail));r.append(el("span",item.mark,"service-logo"),copy,el("span",human(item.state),"status-text"+(item.ok?" positive":"")));f.append(r);}$("overviewConnections").replaceChildren(f);}
 if(changed("connectionCards",items)){const f=document.createDocumentFragment();for(const item of items){const card=el("article",null,"connection-card");card.append(el("span",item.name),el("strong",human(item.state),item.ok?"positive":""),el("small",item.detail));f.append(card);}$("connectionCards").replaceChildren(f);}
}
const exchangeClock=new Intl.DateTimeFormat("en-GB",{timeZone:"Asia/Kolkata",hour:"2-digit",minute:"2-digit",second:"2-digit",hourCycle:"h23"});
function exchangeTime(raw){
 const at=Date.parse(raw);
 return Number.isFinite(at)?exchangeClock.format(at):null;
}
function sessionBanner(title,detail,tone,view,label){
 $("notice").dataset.tone=tone;
 text("noticeTitle",title);text("noticeDetail",detail);
 $("noticeAction").href="#"+view;
 text("noticeAction",label+" →");
}
function workflowStage(stage,label,status,tone=""){
 $(stage).dataset.tone=tone;
 text(label,status);
}
function renderOpeningWorkflow(s){
 const signals=(s.signals||[]).filter(row=>row.entry_eligible===true).length;
 const watchlist=s.watchlist_locked?(s.watchlist_eligible?signals+" locked":"No eligible stocks"):
  s.watchlist_eligible?signals+" selected":s.watchlist_locked===false?"Collecting":"Unavailable";
 workflowStage("stageWatchlist","workflowWatchlist",watchlist,s.watchlist_locked&&s.watchlist_eligible?"ready":"");
 const orb=s.orb_monitor||{};
 const ranges=(orb.opening_ranges||[]).length;
 const opening=ranges?ranges+" verified":orb.state==="WARMING"?"Feed warming":"Waiting";
 workflowStage("stageRange","workflowRange",opening,ranges?"ready":"");
 workflowStage("stageEntries","workflowEntries",
  s.readiness?(s.armed?"Armed":"Ready to arm"):"Blocked",s.readiness?(s.armed?"armed":"ready"):"blocked");
 const closed=Array.isArray(s.closed_trades)?s.closed_trades.length:null;
 workflowStage("stageExit","workflowExit",closed===null?"Unavailable":closed+" confirmed",closed?"ready":"");
 text("positionOutcome",closed===null?"Confirmed NSE cash positions":closed+" confirmed ORB "+(closed===1?"close":"closes")+" today");
}
function renderSessionStatus(s){
 const blockers=new Set(s.readiness_blockers||[]),strategy=s.strategy||{};
 const cutoff=strategy.entry_cutoff?.slice(0,5),squareOff=strategy.exit_time?.slice(0,5);
 const now=exchangeTime(s.server_time),pastCutoff=Boolean(now&&cutoff&&now.slice(0,5)>=cutoff);
 const primary=["PRIOR_SESSION_RECOVERY","SERVER_ORDERS_DISABLED","MARKET_ENTRY_WINDOW"]
  .find(key=>blockers.has(key))||[...blockers][0]||s.blocked_stage;
 const [reason,view]=GATES[primary]||[primary?human(primary):"Waiting for session checks","connections"];
 const entryState=!now||!cutoff?"Window unavailable":pastCutoff?"Cutoff passed":
  blockers.has("MARKET_ENTRY_WINDOW")?"Outside entry window":"Before entry cutoff";
 text("sessionCutoff",cutoff);text("squareOffTime",squareOff);
 text("tradingWindow",entryState);
 const bookAt=exchangeTime(s.broker_books_updated_at);
 text("bookFreshness",s.broker_connected?"Current":bookAt?"Stale":"Unavailable");
 text("bookLastSync",bookAt?"Last sync "+bookAt+" IST":"No broker sync yet");
 const tickAt=exchangeTime(s.last_entry_quote_at);
 const tickCurrent=s.market_feed?.state==="CONNECTED"&&!s.market_feed.error&&!blockers.has("FRESH_QUOTES")&&tickAt;
 text("tickFreshness",tickCurrent?"Current":tickAt?"Stale":"Unavailable");
 text("tickLastTrade",tickAt?"Last trade "+tickAt+" IST":"No exchange tick yet");
 text("risk",s.readiness?"READY":"BLOCKED");
 $("risk").className="value status-value "+(s.readiness?"positive":"");
 text("riskReason",s.readiness?"All entry prerequisites verified":reason);
 if(s.armed&&s.readiness){
  sessionBanner("Live entries armed","The terminal can send real Kotak orders when a breakout qualifies.","armed","settings","Order controls");
 }else if(s.readiness){
  sessionBanner("Ready to arm","Entry checks passed. Operator authorization is required to start live entries.","ready","settings","Order controls");
 }else{
  const notes={
   SERVER_ORDERS_DISABLED:"Server order permission is off.",
   MARKET_ENTRY_WINDOW:pastCutoff?"The "+cutoff+" IST entry cutoff has passed.":"The configured entry window is closed.",
   PRIOR_SESSION_RECOVERY:"Earlier broker orders or positions need reconciliation."
  };
  const count=blockers.size>1?" "+blockers.size+" checks are blocking entries.":"";
  const links={settings:"View settings",signals:"View signals",market:"View market data"};
  sessionBanner(s.armed?"Armed · entries blocked":"Live entries blocked",
   (notes[primary]||reason+".")+count,"blocked",view,links[view]||"View checks");
 }
}
function valuesForSettings(){const s=current?.strategy||{};return {settingAllocation:s.allocation_per_position,settingMaxPositions:s.max_positions,settingGross:s.gross_cap,settingMaxLong:s.max_long,settingMaxShort:s.max_short,settingCutoff:s.entry_cutoff,settingExit:s.exit_time};}
function canEditSettings(){
 return Boolean(current?.strategy&&!stateStale&&!current.armed&&!settingsSaving);
}
function settingsPreview(){
 const number=id=>Number($(id).value);
 const paise=id=>Math.round(number(id)*100);
 const perStock=paise("settingAllocation"), gross=paise("settingGross"), slots=number("settingMaxPositions");
 const validCapital=perStock>0&&gross>=perStock&&slots>0;
 const cash=stateStale?null:current?.available_cash;
 text("settingsPlanned",money(validCapital?Math.min(gross,slots*perStock)/100:null));
 text("settingsSlots",validCapital?Math.min(slots,Math.floor(gross/perStock)):null);
 text("settingsFunds",money(cash));
 text("settingsFundingNote",cash===null||cash===undefined?"Waiting for fresh Kotak funds.":
  gross>0&&gross>Math.round(Number(cash)*100)?"Available funds are below the gross cap. Entries stay blocked by the funds check.":
  "Full-size allocations use the entire per-stock budget. Actual quantities round down.");
 const price=paise("settingsPrice"),shares=price>0&&perStock>0&&$("settingsPrice").validity.valid?
  Math.floor(perStock/price):null;
 text("settingsShares",shares===null?"Enter a price to estimate whole shares per stock.":
  fmt(shares,0)+(shares===1?" share":" shares")+" · "+money(shares*price/100)+" reference notional");
 const seconds=id=>{
  const [hour,minute,second=0]=$(id).value.split(":").map(Number);
  return hour*3600+minute*60+second;
 };
 let issue="";
 if(gross>0&&gross<perStock)issue="Gross cap must cover at least one stock allocation.";
 else if(slots>number("settingMaxLong")+number("settingMaxShort"))issue="Maximum stocks cannot exceed the combined LONG and SHORT limits.";
 else if(seconds("settingExit")<=seconds("settingCutoff"))issue="Square-off must follow the entry cutoff.";
 text("settingsValidation",issue);
 $("settingsValidation").hidden=!issue;
 return !issue;
}
function syncSettings(){
 const fields=valuesForSettings(), allowed=canEditSettings();
 for(const [id,value] of Object.entries(fields)){
  if(!settingsDirty)$(id).value=value??"";
  $(id).disabled=!allowed;
 }
 const valid=settingsPreview()&&!$("settingsForm").querySelector("input:invalid");
 $("saveSettings").disabled=!allowed||!settingsDirty||!valid;
 $("resetSettings").disabled=!allowed||!settingsDirty;
 $("unsavedDot").hidden=!settingsDirty;
 pill("settingsState",settingsSaving?"SAVING":stateStale?"STATUS UNAVAILABLE":current?.armed?"ARMED · LOCKED":
  settingsDirty?"UNSAVED CHANGES":"DISARMED · EDITABLE",settingsDirty?"amber":"");
}
function render(s){
 const serverAt=Date.parse(s?.server_time);
 if(s?.mode!=="LIVE_KOTAK_NEO_ONLY"||!Number.isFinite(serverAt)||
    !["readiness","armed","orders_permitted_by_server"].every(key=>typeof s[key]==="boolean")||
    !Array.isArray(s.readiness_blockers)){
  disconnected("Unexpected service response. Controls are locked.");return;
 }
 if(serverAt<lastServerTime)return;lastServerTime=serverAt;lastStateReceipt=Date.now();current=s;stateStale=false;document.querySelector(".view-container").classList.remove("is-stale");
 const signals=s.signals||[],positions=s.positions||[],feed=s.market_feed?.state||"NOT_STARTED";
 metric("mtm",s.pnl?.gross_total??null);metric("realizedPnl",s.pnl?.gross_realized??null);metric("unrealizedPnl",s.pnl?.gross_unrealized??null);metric("netPnl",s.pnl?.net_after_fees??null);
 text("funds",money(s.available_cash));text("posCount",s.broker_connected?positions.filter(p=>Number(p.net_quantity)!==0).length:null);text("session",s.session_date);text("count",signals.length);text("navSignalCount",signals.length);
 const vix=s.quotes?.INDIAVIX||{};text("vix",vix.fresh?fmt(vix.ltp):null);text("vixAsOf",vix.fresh?"India VIX · Dhan index quote at "+stamp(vix.ohlc_at)+" IST":"India VIX is unavailable. It is context, not an entry trigger.");
 pill("mode",s.armed?"ORDERS ARMED":"ORDERS DISARMED",s.armed?"red":"");pill("source",human(s.signal_status||"UNAVAILABLE"),s.signal_status==="READY"?"green":"amber");pill("connection",s.broker_connected?"KOTAK CONNECTED":"KOTAK DISCONNECTED",s.broker_connected?"green":"red");pill("gate",s.armed?"ARMED":"DISARMED",s.armed?"red":"");pill("cutoff","Research cutoff: "+stamp(s.research_cutoff_at)+" IST");pill("feedBadge",human(feed),feed==="CONNECTED"?"green":"amber");
 const blockers=s.readiness_blockers||[],total=Object.keys(GATES).length,passed=total-new Set(blockers.filter(k=>GATES[k])).size;
 $("gateProgress").max=total;$("gateProgress").value=passed;
 text("gateSummary",passed+" of "+total+" prerequisites verified");text("allGateSummary",s.readiness?"All entry checks passed":blockers.length+" checks need attention before new entries");gates(s);renderSessionStatus(s);renderOpeningWorkflow(s);
 text("policy","09:15 one-minute opening range. New-entry cutoff "+safeText(s.strategy?.entry_cutoff)+"; square-off "+safeText(s.strategy?.exit_time)+" IST. Up to "+safeText(s.strategy?.max_long)+" LONG / "+safeText(s.strategy?.max_short)+" SHORT, "+safeText(s.strategy?.max_positions)+" total positions. Allocation "+money(s.strategy?.allocation_per_position)+" per stock; gross cap "+money(s.strategy?.gross_cap)+".");
 text("armNotice",!s.orders_permitted_by_server?"Server order permission is off. Account monitoring remains read-only.":s.armed?"This session is armed for actual Kotak orders.":s.readiness?"Prerequisites passed. Operator authorization and TOTP are required.":"Order authorization is locked until all prerequisites pass.");
 $("arm").disabled=!(s.orders_permitted_by_server===true&&s.readiness===true&&!s.armed);$("disarm").disabled=!s.armed;syncSettings();
 const dhan=s.dhan_authentication||{},kotak=s.kotak_authentication||{};text("diagnostics","Dhan auth: "+(dhan.state||"NEEDS_SETUP")+" · Kotak auth: "+(kotak.state||"NOT_STARTED")+" · Feed: "+feed+". Next refresh: Dhan "+stamp(dhan.next_refresh)+" / Kotak "+stamp(kotak.next_refresh)+" IST. Broker book: "+stamp(s.broker_books_updated_at)+" IST.");
 const errors=[s.error,dhan.error,kotak.error,s.position_feed_error,...(s.runtime_errors||[])].filter(Boolean);text("errors",errors.join(" | ")||"No runtime errors reported.");text("auditState",errors.length?"ATTENTION":"NO RUNTIME ERRORS");
 const intents=s.intents||[];text("intentInfo",intents.length+" durable order intents · "+intents.filter(x=>["RESERVED","SUBMISSION_UNKNOWN","PARTIAL","ACKNOWLEDGED","CANCEL_PENDING"].includes(x.state)).length+" unresolved. Intents are not executed fills.");
 text("updated","Updated "+stamp(s.server_time)+" IST");text("streamLabel",streamConnected?"Live state stream":"Live state · polling fallback");$("streamDot").className="status-dot connected";renderServices(s);renderActive();
}
function query(id){return $(id).value.trim().toLowerCase();}
function filtered(kind){
 const rows=current?.[kind]||[];
 if(kind==="positions"){const q=query("positionSearch");return rows.filter(r=>(r.symbol||"").toLowerCase().includes(q)&&(positionFilter==="all"||((Number(r.net_quantity)!==0)===(positionFilter==="open"))));}
 if(kind==="orders"){const q=query("orderSearch"),status=$("orderStatus").value;return [...rows].reverse().filter(r=>[r.symbol,r.broker_order_id,r.tag].join(" ").toLowerCase().includes(q)&&(status==="all"||(status==="complete"?/complete|filled/i.test(r.status):status==="rejected"?/reject|cancel/i.test(r.status):!/complete|filled|reject|cancel/i.test(r.status))));}
 if(kind==="fills"){const q=query("fillSearch");return [...rows].reverse().filter(r=>[r.symbol,r.fill_id,r.broker_order_id].join(" ").toLowerCase().includes(q));}
 if(kind==="mapping")return (current?.instrument_mappings||[]).filter(r=>r.symbol.toLowerCase().includes(query("marketSearch")));
 return rows;
}
function paginate(kind,rows){
 const max=Math.max(1,Math.ceil(rows.length/PAGE_SIZE));pages[kind]=Math.max(1,Math.min(pages[kind],max));const page=pages[kind],start=(page-1)*PAGE_SIZE,target=$(kind+"Pagination");if(!changed("pagination-"+kind,[page,rows.length]))return rows.slice(start,start+PAGE_SIZE);target.replaceChildren();target.append(el("span",rows.length?(start+1)+"–"+Math.min(start+PAGE_SIZE,rows.length)+" of "+rows.length:"No matching rows"));
 const controls=el("div",null,"pagination-controls");for(const [label,delta] of [["‹",-1],["›",1]]){const b=el("button",label);b.type="button";b.setAttribute("aria-label",delta<0?"Previous page":"Next page");b.disabled=delta<0?page===1:page===max;b.addEventListener("click",()=>{pages[kind]+=delta;renderActive();});if(delta===1)controls.append(el("span",page+" / "+max));controls.append(b);}target.append(controls);return rows.slice(start,start+PAGE_SIZE);
}
function detailButton(kind,item){const b=el("button","↗","row-open");b.type="button";b.setAttribute("aria-label","Open "+kind+" details");b.addEventListener("click",()=>openDetail(kind,item));return b;}
function openDetail(kind,item){
 text("detailEyebrow",kind.toUpperCase()+" · BROKER SNAPSHOT");text("detailTitle",item.symbol||item.broker_order_id||item.fill_id||"Details");
 const list=el("dl",null,"detail-list");for(const [key,value] of Object.entries({...item,snapshot_time:current?.server_time})){const group=el("div");group.append(el("dt",human(key)),el("dd",typeof value==="object"&&value!==null?JSON.stringify(value):safeText(value)));list.append(group);}$("detailContent").replaceChildren(list);$("detailDialog").showModal();
}
function renderPositions(){
 const pnl=current.pnl||{};
 metric("positionTotalPnl",pnl.gross_total??null);
 metric("positionRealizedPnl",pnl.gross_realized??null);
 metric("positionUnrealizedPnl",pnl.gross_unrealized??null);
 text("positionOpenCount",current.broker_connected?(current.positions||[]).filter(p=>Number(p.net_quantity)!==0).length:null);
 renderPositionClosures();
 const all=filtered("positions"),rows=paginate("positions",all);text("positionResultCount",all.length+" positions");text("positionsAsOf","Broker book: "+stamp(current.broker_books_updated_at)+" IST");
 if(!changed("positions",[rows,current.broker_books_updated_at]))return;const f=document.createDocumentFragment();
 if(!rows.length)f.append(emptyRow(8,current.broker_connected?"No NSE cash positions match this view.":"Live broker positions are unavailable."));
 for(const p of rows){const name=td(null),b=el("button",p.symbol,"stock-button");b.addEventListener("click",()=>openDetail("position",p));name.append(b,el("small",p.side||"FLAT","side "+(p.side==="LONG"?"long":"short")));const r=row(name,td(fmt(p.net_quantity,0),"numeric"),td(money(p.ltp),"numeric"),td(money(p.realized_gross),"numeric "+tone(p.realized_gross)),td(money(p.unrealized_gross),"numeric "+tone(p.unrealized_gross)),td(money(p.gross_mark_to_market),"numeric "+tone(p.gross_mark_to_market)),td(human(p.pnl_status)),td(null));r.classList.add("interactive-row");r.addEventListener("click",e=>{if(!e.target.closest("button,a"))openDetail("position",p);});r.lastChild.append(detailButton("position",p));f.append(r);}
 $("positionBody").replaceChildren(f);
}
function renderPositionClosures(){
 const trades=current.closed_trades||[];
 text("positionClosedAsOf",trades.length+" confirmed closed · updated "+stamp(current.server_time)+" IST");
 if(!changed("positionClosures",trades))return;
 const rows=document.createDocumentFragment();
 if(!trades.length)rows.append(emptyRow(6,"No fully closed ORB trades for this session."));
 for(const t of trades)rows.append(row(td(t.symbol,"code"),td(t.direction,"side "+(t.direction==="LONG"?"long":"short")),
  td(fmt(t.exit_quantity,0),"numeric"),td(money(t.entry_average_price),"numeric"),td(money(t.exit_average_price),"numeric"),td(money(t.realized_gross),"numeric "+tone(t.realized_gross))));
 $("positionClosedBody").replaceChildren(rows);
}
function renderBook(kind){
 const all=filtered(kind),rows=paginate(kind,all),orders=kind==="orders",target=$(orders?"orderBody":"fillBody");text(orders?"orderCount":"fillCount",all.length+" "+(orders?"broker orders":"confirmed fills"));if(!changed(kind,rows))return;
 const f=document.createDocumentFragment();if(!rows.length)f.append(emptyRow(6,"No "+(orders?"orders":"executed fills")+" match this view."));
 for(const item of rows){const r=orders?row(td(item.broker_order_id,"code"),td(item.symbol,"code"),td(item.side,"side "+(/^(B|BUY)$/i.test(item.side)?"long":"short")),td(item.order_quantity,"numeric"),td(human(item.status)),td(null)):row(td(item.fill_id,"code"),td(item.symbol,"code"),td(item.filled_quantity,"numeric"),td(money(item.fill_price),"numeric"),td(item.fill_time?stamp(item.fill_time):"—"),td(null));r.classList.add("interactive-row");r.addEventListener("click",e=>{if(!e.target.closest("button,a"))openDetail(orders?"order":"fill",item);});r.lastChild.append(detailButton(orders?"order":"fill",item));f.append(r);}target.replaceChildren(f);
}
function renderSignals(){
 const q=query("signalSearch"),signals=(current.signals||[]).filter(s=>s.symbol.toLowerCase().includes(q)&&(signalFilter==="all"||s.side===signalFilter)).sort((a,b)=>(a.side||"").localeCompare(b.side||"")||(a.rank||0)-(b.rank||0));
 text("signalResultCount",signals.length+" stocks");const data=signals.map(s=>[s,current.quotes?.[s.symbol]||{}]);
 if(changed("signals",data)){const f=document.createDocumentFragment();if(!signals.length)f.append(emptyRow(6,(current.signals||[]).length?"No candidates match these filters.":"No current-session verified news signals. New entries remain locked."));
 for(const s of signals){const quote=current.quotes?.[s.symbol]||{},price=quote.fresh?quote.ltp:null,opened=quote.open||null,change=price!==null&&opened?100*(price-opened)/opened:null,stock=td(null),b=el("button",s.symbol,"stock-button");b.addEventListener("click",()=>{selectedSymbol=s.symbol;renderEvidence();});stock.append(b);if(s.entry_eligible===false)stock.append(el("small","Research only","muted"));f.append(row(td(s.rank),stock,td(s.side,"side "+(s.side==="LONG"?"long":"short")),td(money(price),"numeric"),td(money(opened),"numeric"),td(change===null?"—":(change>=0?"+":"")+fmt(change)+"%","numeric "+tone(change))));}$("signalBody").replaceChildren(f);}
 if(!selectedSymbol||(current.signals||[]).every(s=>s.symbol!==selectedSymbol))selectedSymbol=signals[0]?.symbol||null;renderEvidence();
}
function renderEvidence(){
 const signal=(current.signals||[]).find(s=>s.symbol===selectedSymbol);if(!changed("evidence",signal||null))return;pill("selected",signal?signal.symbol+" · "+signal.side:"SELECT A STOCK");const target=$("evidence");target.replaceChildren();
 if(!signal){target.append(el("p","Select a verified stock to inspect its research, rationale, and original sources.","muted"));return;}
 target.append(el("b",signal.news_summary||"No summary provided"),el("p",signal.reason||"No rationale provided"),el("div","First available: "+stamp(signal.first_available_at)+" IST","meta"));
 const sources=el("div",null,"links");for(const ref of signal.sources||[]){try{const url=new URL(ref.url);if(url.protocol!=="https:")continue;const a=el("a",(ref.title||url.hostname)+" · "+stamp(ref.published_at)+" IST");a.href=url.href;a.target="_blank";a.rel="noopener noreferrer";sources.append(a);}catch{}}target.append(sources);
}
function renderMarket(){
 $("orbPanel").hidden=marketTab!=="orb";$("mappingPanel").hidden=marketTab!=="mapping";
 if(marketTab==="mapping"){const rows=filtered("mapping"),all=current.instrument_mappings||[];text("mappingInfo",all.length?all.filter(r=>r.dhan_security_id).length+" / "+all.length+" Dhan IDs · "+all.filter(r=>r.kotak_token&&r.kotak_trading_symbol).length+" / "+all.length+" Kotak mappings":"Waiting for today's verified research report.");if(!changed("mapping",rows))return;const f=document.createDocumentFragment();if(!rows.length)f.append(emptyRow(5,"Exact mappings appear after current-session candidates are verified."));for(const m of rows)f.append(row(td(m.symbol,"code"),td(m.dhan_security_id||"—","code"),td(m.kotak_trading_symbol||"—","code"),td(m.kotak_token||"—","code"),td(human(m.status))));$("mappingBody").replaceChildren(f);return;}
 const orb=current.orb_monitor||{},ranges=(orb.opening_ranges||[]).filter(r=>r.symbol.toLowerCase().includes(query("marketSearch"))),data=ranges.map(r=>[r,current.quotes?.[r.symbol],(orb.breakouts||[]).find(b=>b.symbol===r.symbol)]);
 text("orbInfo",orb.state==="MONITORING"?orb.order_submission_enabled?"Fresh ticks monitored · order gate armed separately":"Live tick monitoring · order gate remains disarmed":orb.state==="WARMING"?"Dhan feed warming · waiting for the 09:15–09:16 candle.":"Waiting for verified signals and a completed opening candle.");if(!changed("orb",data))return;const f=document.createDocumentFragment();if(!ranges.length)f.append(emptyRow(6,"No opening-range monitor matches this view."));
 for(const range of ranges){const signal=(current.signals||[]).find(s=>s.symbol===range.symbol)||{},quote=current.quotes?.[range.symbol]||{},event=(orb.breakouts||[]).find(e=>e.symbol===range.symbol);f.append(row(td(range.symbol,"code"),td(signal.side||"—"),td(range.high,"numeric"),td(range.low,"numeric"),td(quote.fresh?money(quote.ltp):"—","numeric"),td(event?"Observed · "+stamp(event.triggered_at):"Waiting for cross",event?"positive":"")));}$("orbBody").replaceChildren(f);
}
function renderActive(){if(!current||stateStale)return;if(activeView==="signals")renderSignals();else if(activeView==="positions")renderPositions();else if(activeView==="orders"||activeView==="fills")renderBook(activeView);else if(activeView==="journal")renderJournal();else if(activeView==="market")renderMarket();else if(activeView==="connections"){gates(current,true);renderServices(current);}}
async function loadJournal(){if(journalLoading||activeView!=="journal")return;journalLoading=true;try{const r=await fetch(route("/api/live/journal?limit=1000"),{credentials:"same-origin",cache:"no-store",signal:AbortSignal.timeout(10000)});if(r.status===401){location.assign(route("/login"));return;}if(!r.ok)throw Error("HTTP "+r.status);journalData=await r.json();journalError=null;renderJournal();}catch{journalError="Trade history is unavailable. Reconnecting…";renderJournal();}finally{journalLoading=false;}}
function renderJournal(){const data=journalData;if(!data){for(const id of ["journalClosed","journalGross","journalWinRate","journalOpen","journalLoss","journalReturn"])text(id,"—");text("journalAsOf",journalError||"Loading persisted trade history…");return;}
 const s=data.summary||{};text("journalClosed",fmt(s.closed_trade_count,0));text("journalGross",money(s.gross_realized_from_exits));$("journalGross").className="value "+tone(s.gross_realized_from_exits);text("journalWinRate",s.win_rate_pct===null?"—":fmt(s.win_rate_pct)+"%");text("journalOpen",fmt(s.open_trade_count,0));text("journalLoss",money(s.gross_loss_total));$("journalLoss").className="value "+tone(s.gross_loss_total);text("journalReturn",s.closed_trade_return_pct===null?"—":fmt(s.closed_trade_return_pct)+"%");$("journalReturn").className="value "+tone(s.closed_trade_return_pct);text("journalAsOf",(data.trades||[]).length+" persisted trade records · updated "+stamp(data.as_of)+" IST · "+(s.method||""));text("journalFees",s.fees_status||"FEES UNAVAILABLE");
 const trades=data.trades||[],tradeBody=$("journalTradeBody");if(changed("journalTrades",trades)){const f=document.createDocumentFragment();if(!trades.length)f.append(emptyRow(9,"No persisted trade intents yet. History starts with this version."));for(const t of trades)f.append(row(td(t.session_date),td(t.symbol,"code"),td(t.direction,"side "+(t.direction==="LONG"?"long":"short")),td(t.status),td(fmt(t.entry_quantity,0),"numeric"),td(fmt(t.exit_quantity,0),"numeric"),td(money(t.entry_average_price),"numeric"),td(money(t.exit_average_price),"numeric"),td(money(t.realized_gross),"numeric "+tone(t.realized_gross))));tradeBody.replaceChildren(f);}
 const events=data.events||[];if(changed("journalEvents",events)){const f=document.createDocumentFragment();if(!events.length)f.append(emptyRow(6,"No order lifecycle events recorded yet."));for(const e of events)f.append(row(td(stamp(e.observed_at)),td(e.symbol||"—","code"),td(e.phase||"—"),td(e.side||"—"),td(e.state||"—"),td(e.details||"—")));$("journalEventBody").replaceChildren(f);}
 const fills=data.fills||[];text("journalFillCount",fills.length+" persisted fills");if(changed("journalFills",fills)){const f=document.createDocumentFragment();if(!fills.length)f.append(emptyRow(7,"No broker fills have been persisted yet."));for(const fill of fills)f.append(row(td(stamp(fill.broker_time||fill.first_seen_at)),td(fill.trading_symbol||fill.symbol,"code"),td(fill.side||"—"),td(fmt(fill.quantity,0),"numeric"),td(money(fill.price),"numeric"),td(fill.broker_fill_id||"—","code"),td(fill.broker_order_id||"—","code")));$("journalFillBody").replaceChildren(f);}}
function disconnected(message){
 stateStale=true;document.querySelector(".view-container").classList.add("is-stale");
 pill("connection","DISCONNECTED","red");pill("mode","ORDER STATE UNKNOWN","amber");pill("gate","UNKNOWN","amber");pill("source","UNAVAILABLE","amber");
 $("arm").disabled=true;$("disarm").disabled=true;$("saveSettings").disabled=true;for(const id of ["funds","posCount","mtm","realizedPnl","unrealizedPnl","netPnl","positionTotalPnl","positionRealizedPnl","positionUnrealizedPnl","positionOpenCount"])text(id,null);
 $("positionBody").replaceChildren(emptyRow(8,"Live account data unavailable. Reconnecting."));
 text("positionClosedAsOf","Trade status unavailable");
 $("positionClosedBody").replaceChildren(emptyRow(6,"Waiting for the session state to reconnect."));
 renderCache.clear();
 text("risk","UNKNOWN");$("risk").className="value status-value";
 text("riskReason","Waiting for live status");
 text("gateSummary","Live checks unavailable");text("allGateSummary","Live checks unavailable");
 $("gateProgress").removeAttribute("value");
 for(const id of ["overviewGates","allGates","overviewConnections","connectionCards"])
  $(id).replaceChildren(el("p","Waiting for live status.","status-placeholder"));
 for(const [stage,label] of [["stageWatchlist","workflowWatchlist"],["stageRange","workflowRange"],
  ["stageEntries","workflowEntries"],["stageExit","workflowExit"]])workflowStage(stage,label,"Unavailable");
 for(const id of ["count","vix","navSignalCount"])text(id,null);
 text("positionOutcome","Waiting for live status");text("vixAsOf","Dhan index quote unavailable");
 syncSettings();
 sessionBanner("Live status unavailable",message,"blocked","connections","View checks");
 text("tradingWindow","Status unavailable");
 text("bookFreshness","Unavailable");text("bookLastSync","Waiting for sync");
 text("tickFreshness","Unavailable");text("tickLastTrade","Waiting for feed");
 text("streamLabel","Connection interrupted");$("streamDot").className="status-dot";
}
let fetchInFlight=false;
async function getState(){if(fetchInFlight)return;fetchInFlight=true;try{const r=await fetch(route("/api/state"),{credentials:"same-origin",cache:"no-store",signal:AbortSignal.timeout(5000)});if(r.status===401){location.assign(route("/login"));return;}if(!r.ok)throw Error("HTTP "+r.status);render(await r.json());}catch{if(Date.now()-lastStateReceipt>5000)disconnected("Live data is unavailable. Reconnecting; verify your account directly with Kotak.");}finally{fetchInFlight=false;}}
function connect(){
 if(ws&&(ws.readyState===WebSocket.OPEN||ws.readyState===WebSocket.CONNECTING))return;ws=new WebSocket((location.protocol==="https:"?"wss:":"ws:")+"//"+location.host+route("/ws"));
 ws.onopen=()=>{streamConnected=true;if(current&&!stateStale)renderServices(current);};
 ws.onmessage=e=>{try{render(JSON.parse(e.data));}catch{disconnected("Dashboard stream is invalid; reconnecting.");}};
 ws.onclose=e=>{streamConnected=false;if(e.code===4401){location.assign(route("/login"));return;}if(current&&!stateStale)renderServices(current);setTimeout(connect,3000);};ws.onerror=()=>ws.close();
}
async function runChecks(){
 const button=$("runChecks");button.disabled=true;button.textContent="Checking…";const paths=[["Service health","/health"],["Trading readiness","/ready"],["Current live state","/api/state"],["Broker positions","/api/live/positions"],["Broker orders","/api/live/orders"],["Executed fills","/api/live/fills"]];$("checkResults").replaceChildren(el("p","Running read-only connection checks…","detail"));
 try{const results=await Promise.all(paths.map(async([name,path])=>{const start=performance.now();try{const r=await fetch(route(path),{credentials:"same-origin",cache:"no-store",signal:AbortSignal.timeout(5000)});return {name,path,status:r.status,ms:Math.round(performance.now()-start),expected:r.ok||(path==="/ready"&&r.status===503)};}catch{return {name,path,status:null,expected:false};}}));const f=document.createDocumentFragment();
 for(const result of results){const r=el("div",null,"check-row"),label=el("div",result.name);label.append(el("small",result.path));const status=result.path==="/ready"&&result.status===503?"Blocked · HTTP 503":result.status?"HTTP "+result.status+" · "+result.ms+" ms":"Unavailable";r.append(label,el("strong",status,result.expected&&result.status!==503?"positive":result.expected?"":"negative"));f.append(r);}
 const stream=el("div",null,"check-row");stream.append(el("span","Dashboard transport"),el("strong",streamConnected?"WebSocket connected":"Polling fallback",streamConnected?"positive":""));f.append(stream);$("checkResults").replaceChildren(f);notify("Read-only checks completed. Readiness may remain blocked by session prerequisites.");
 }finally{button.disabled=false;button.textContent="Run checks ↗";}
}
function exportCsv(kind){
 const rows=filtered(kind);if(!rows.length){notify("No matching live rows to export.");return;}const keys=[...new Set(rows.flatMap(r=>Object.keys(r)))];
 const cell=value=>{let raw=value===null||value===undefined?"":typeof value==="object"?JSON.stringify(value):String(value);if(typeof value==="string"&&/^[=+@\t\r-]/.test(raw))raw="'"+raw;return '"'+raw.replaceAll('"','""')+'"';};
 const data=[keys.map(cell).join(","),...rows.map(r=>keys.map(k=>cell(r[k])).join(","))].join("\r\n"),blob=new Blob(["\uFEFF",data],{type:"text/csv;charset=utf-8"}),url=URL.createObjectURL(blob),a=el("a");a.href=url;a.download="kotak-"+kind+"-"+(current?.session_date||"session")+".csv";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);notify(rows.length+" live rows exported.");
}
window.addEventListener("hashchange",showView);
$("menuToggle").addEventListener("click",()=>{const open=!$("sidebar").classList.contains("open");$("sidebar").classList.toggle("open",open);$("navScrim").hidden=!open;$("menuToggle").setAttribute("aria-expanded",String(open));});
$("navScrim").addEventListener("click",closeMenu);
$("refreshState").addEventListener("click",async()=>{const b=$("refreshState");b.disabled=true;await getState();b.disabled=false;});
$("runChecks").addEventListener("click",runChecks);
for(const [id,kind] of [["positionSearch","positions"],["orderSearch","orders"],["fillSearch","fills"],["signalSearch","signals"],["marketSearch","market"]])$(id).addEventListener("input",()=>{if(pages[kind])pages[kind]=1;renderActive();});
$("orderStatus").addEventListener("change",()=>{pages.orders=1;renderActive();});
for(const [id,kind] of [["signalFilters","signal"],["positionFilters","position"]])$(id).addEventListener("click",event=>{const b=event.target.closest("[data-filter]");if(!b)return;$(id).querySelectorAll("button").forEach(n=>n.setAttribute("aria-pressed",String(n===b)));if(kind==="signal")signalFilter=b.dataset.filter;else{positionFilter=b.dataset.filter;pages.positions=1;}renderActive();});
$("marketTabs").addEventListener("click",event=>{const b=event.target.closest("[data-market-tab]");if(!b)return;marketTab=b.dataset.marketTab;$("marketTabs").querySelectorAll("button").forEach(n=>n.setAttribute("aria-pressed",String(n===b)));renderMarket();});
document.querySelectorAll("[data-export]").forEach(b=>b.addEventListener("click",()=>exportCsv(b.dataset.export)));
$("closeDetail").addEventListener("click",()=>$("detailDialog").close());
$("arm").addEventListener("click",()=>{$("armError").textContent="";$("armModal").showModal();$("authorization").focus();});
$("cancelArm").addEventListener("click",()=>$("armModal").close());
$("armModal").addEventListener("close",()=>{$("otp").value="";$("authorization").value="";});
$("armForm").addEventListener("submit",async event=>{
 event.preventDefault();try{const r=await fetch(route("/api/live/arm"),{method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json"},body:JSON.stringify({acknowledgement:$("authorization").value,totp:$("otp").value})});if(!r.ok){const d=await r.json().catch(()=>({}));throw Error(typeof d.detail==="string"?d.detail:"Authorization denied");}$("armModal").close();await getState();}catch(e){text("armError",e.message);}finally{$("otp").value="";}
});
$("disarm").addEventListener("click",async()=>{try{const r=await fetch(route("/api/live/disarm"),{method:"POST",credentials:"same-origin"});if(!r.ok)throw Error("Disarm failed. Verify the broker account directly.");await getState();notify("New entries disarmed.");}catch(e){notify(e.message);}});
$("settingsPrice").addEventListener("input",settingsPreview);
$("settingsForm").addEventListener("input",()=>{
 if(!canEditSettings())return;
 settingsDirty=true;syncSettings();text("settingsMessage","Unsaved edits. Live updates will preserve your changes.");
});
$("resetSettings").addEventListener("click",()=>{
 if(!canEditSettings())return;
 settingsDirty=false;syncSettings();text("settingsMessage","Edits discarded. Showing the saved live configuration.");
});
$("settingsForm").addEventListener("submit",async event=>{
 event.preventDefault();
 if(!canEditSettings()||!settingsDirty||$("saveSettings").disabled)return;
 const payload={per_position:$("settingAllocation").value,max_positions:$("settingMaxPositions").value,
  gross_cap:$("settingGross").value,max_long:$("settingMaxLong").value,max_short:$("settingMaxShort").value,
  entry_cutoff:$("settingCutoff").value,exit_time:$("settingExit").value};
 settingsSaving=true;syncSettings();
 try{
  const r=await fetch(route("/api/live/settings"),{method:"POST",credentials:"same-origin",
   headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
  const data=await r.json().catch(()=>({}));
  if(!r.ok||data.saved!==true)throw Error(typeof data.detail==="string"?data.detail:"Configuration could not be saved");
  if(data.strategy)current={...current,strategy:{...current.strategy,...data.strategy}};
  settingsDirty=false;text("settingsMessage","Configuration saved to the server.");
  await getState();notify("Trading configuration saved.");
 }catch(e){text("settingsMessage",e.message);}
 finally{settingsSaving=false;syncSettings();}
});
$("logout").addEventListener("click",async()=>{try{await fetch(route("/api/auth/logout"),{method:"POST",credentials:"same-origin"});}finally{location.assign(route("/login"));}});
document.addEventListener("keydown",event=>{if(event.key==="Escape")closeMenu();if(event.key==="/"&&!/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)){const search=document.querySelector('[data-search="'+activeView+'"]');if(search){event.preventDefault();search.focus();}}});
function clock(){text("liveClock",new Date().toLocaleTimeString("en-GB",{timeZone:"Asia/Kolkata",hour12:false})+" IST");}
showView();clock();setInterval(clock,1000);getState();connect();
setInterval(()=>{if(!streamConnected||Date.now()-lastStateReceipt>8000)getState();},10000);
setInterval(()=>{if(lastStateReceipt&&Date.now()-lastStateReceipt>15000)disconnected("Dashboard state is stale. Reconnecting; verify directly with Kotak.");},3000);
setInterval(()=>{if(activeView==="journal")loadJournal();},15000);
