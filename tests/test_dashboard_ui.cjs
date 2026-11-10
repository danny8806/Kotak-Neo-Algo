const {chromium}=require(process.env.PLAYWRIGHT_MODULE_PATH||'playwright');
const fs=require('node:fs'),http=require('node:http'),assert=require('node:assert/strict'),path=require('node:path'),os=require('node:os');
const root=path.resolve(__dirname,'../live_desk/static');
const screenshotDir=process.env.DASHBOARD_SCREENSHOT_DIR||os.tmpdir();
const marketTime=Date.parse('2026-11-05T09:18:00+05:30');
let revision=0,healthFailure=false;const mutations=[];
const journal={as_of:new Date().toISOString(),real_only:true,summary:{trade_count:1,closed_trade_count:1,open_trade_count:0,gross_realized_from_exits:25,gross_loss_total:0,closed_trade_return_pct:5,max_closed_trade_drawdown_gross:0,wins:1,losses:0,win_rate_pct:100,average_win:25,average_loss:null,net_after_fees:null,fees_status:'NOT AVAILABLE FROM CONFIRMED BROKER DATA',method:'Matched Kotak-confirmed entry and exit fills; descriptive only'},trades:[{session_date:'2026-11-05',symbol:'TESTA',direction:'LONG',status:'CLOSED',entry_quantity:5,exit_quantity:5,entry_average_price:100,exit_average_price:105,realized_gross:25}],events:[{observed_at:new Date().toISOString(),symbol:'TESTA',phase:'ENTRY',side:'BUY',state:'FILLED',details:'Kotak FILLED; filled 5/5'}],fills:[{first_seen_at:new Date().toISOString(),trading_symbol:'TESTA-EQ',side:'B',quantity:5,price:100,broker_fill_id:'F-TEST',broker_order_id:'O-TEST'}]};
function nginxRewrite(value){return value.toString().replaceAll('"/api/','"/kotak-live/api/').replaceAll('"/static/','"/kotak-live/static/').replaceAll('"/health','"/kotak-live/health').replaceAll('"/ready','"/kotak-live/ready').replaceAll('"/login','"/kotak-live/login').replaceAll('"/ws','"/kotak-live/ws').replaceAll('"/"','"/kotak-live/"');}
const state={mode:'LIVE_KOTAK_NEO_ONLY',server_time:new Date(marketTime).toISOString(),session_date:'2026-11-05',
 watchlist_locked:true,watchlist_eligible:true,closed_trades:[],
 signal_status:'READY',signals:[{symbol:'TESTA',entry_eligible:true,side:'LONG',rank:1,news_summary:'Synthetic research fixture A',reason:'Test evidence for the isolated UI runner.',first_available_at:'2026-11-05T08:00:00+05:30',sources:[{url:'https://www.nseindia.com/',title:'Test reference',published_at:'2026-11-05T08:00:00+05:30'}]},{symbol:'TESTB',entry_eligible:true,side:'SHORT',rank:1,news_summary:'Synthetic research fixture B',reason:'Second isolated test candidate.',sources:[]}],
 positions:[{symbol:'TESTA',side:'LONG',net_quantity:5,ltp:105,realized_gross:50,unrealized_gross:25,gross_mark_to_market:75,pnl_status:'LIVE'}],
 orders:Array.from({length:151},(_,i)=>({broker_order_id:'TEST-'+i,symbol:i%2?'TESTB':'TESTA',side:'BUY',order_quantity:'1',status:'complete',tag:i===150?'=test-formula':'test-tag'})),
 fills:Array.from({length:151},(_,i)=>({fill_id:'TEST-FILL-'+i,symbol:'TESTA',filled_quantity:'1',fill_price:100})),
 quotes:{TESTA:{fresh:true,ltp:105,open:100},TESTB:{fresh:true,ltp:95,open:100}},broker_connected:true,available_cash:'1000000',
 last_entry_quote_at:new Date(marketTime-1000).toISOString(),
 pnl:{gross_realized:50,gross_unrealized:25,gross_total:75,net_after_fees:null},broker_books_updated_at:new Date(marketTime).toISOString(),
 armed:false,readiness:false,orders_permitted_by_server:false,dhan_authentication:{state:'AUTHENTICATED',next_refresh:'2026-11-06T08:00:00+05:30'},kotak_authentication:{state:'AUTHENTICATED',next_refresh:'2026-11-06T08:00:00+05:30'},
 supervisor_status:'DISARMED',blocked_stage:'SERVER_ORDERS_DISABLED',readiness_blockers:['SERVER_ORDERS_DISABLED'],market_feed:{state:'CONNECTED'},
 strategy:{allocation_per_position:100000,max_positions:10,gross_cap:1000000,max_long:5,max_short:5,entry_cutoff:'09:30',exit_time:'15:15'},
 instrument_mappings:[{symbol:'TESTA',dhan_security_id:'123',kotak_trading_symbol:'TESTA-EQ',kotak_token:'456',status:'READY'},{symbol:'TESTB',dhan_security_id:'124',kotak_trading_symbol:'TESTB-EQ',kotak_token:'457',status:'READY'}],
 orb_monitor:{state:'MONITORING',order_submission_enabled:false,opening_ranges:[{symbol:'TESTA',high:100,low:95},{symbol:'TESTB',high:101,low:96}],breakouts:[]},intents:[],runtime_errors:[]};
function bump(){state.server_time=new Date(marketTime+(++revision)*1000).toISOString();}
(async()=>{
 const server=http.createServer((req,res)=>{
  const requestPath=(req.url||'/').replace(/^\/kotak-live(?=\/|$)/,'')||'/';
  if(req.method==='POST'&&requestPath==='/api/auth/login'){res.setHeader('Content-Type','application/json');res.end(JSON.stringify({authenticated:true}));return;}
  if(req.method==='POST'){mutations.push(requestPath);if(requestPath!=='/api/live/settings'){res.writeHead(405);res.end();return;}let body='';req.on('data',chunk=>body+=chunk);req.on('end',()=>{const data=JSON.parse(body);const {per_position,...limits}=data;Object.assign(state.strategy,limits,{allocation_per_position:per_position});bump();res.setHeader('Content-Type','application/json');res.end(JSON.stringify({saved:true,strategy:state.strategy}));});return;}
  if(requestPath.startsWith('/api/')||requestPath==='/health'||requestPath==='/ready'){res.setHeader('Content-Type','application/json');res.statusCode=requestPath==='/ready'?503:requestPath==='/health'&&healthFailure?503:200;res.end(JSON.stringify(requestPath==='/api/state'?state:requestPath.startsWith('/api/live/journal')?journal:{status:'test-response'}));return;}
  const name=requestPath==='/'?'index.html':requestPath==='/login'?'login.html':requestPath.replace('/static/','');
  if(!['index.html','login.html','app.js','styles.css'].includes(name)){res.writeHead(404);res.end();return;}
  res.setHeader('Content-Type',name.endsWith('.js')?'application/javascript':name.endsWith('.css')?'text/css':'text/html');const body=fs.readFileSync(path.join(root,name));const filled=name.endsWith('.html')?body.toString().replaceAll('__DASHBOARD_BASE_PATH__','/kotak-live'):body;res.end(nginxRewrite(filled));
 });
 await new Promise(r=>server.listen(0,'127.0.0.1',r));let browser;
 try{
  browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.CHROMIUM_EXECUTABLE_PATH}:{})});const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));page.on('requestfailed',r=>console.log('request_failed',r.url(),r.failure()?.errorText));
  await page.clock.setFixedTime(marketTime);
  await page.addInitScript(()=>{window.WebSocket=class {static OPEN=1;static CONNECTING=0;constructor(){this.readyState=1;window._testSocket=this;}close(){this.readyState=3;}};});
  const origin='http://127.0.0.1:'+server.address().port;const loginPage=await browser.newPage();await loginPage.goto(origin+'/kotak-live/login');await loginPage.locator('#password').fill('Synthetic-test-password');await loginPage.locator('button.primary').click();await loginPage.waitForURL('**/kotak-live/');await loginPage.close();
  await page.goto(origin+'/kotak-live/');await page.waitForFunction(()=>document.getElementById('mtm').textContent==='₹75.00');
  const go=async name=>{await page.locator('nav [data-view="'+name+'"]').click();await page.locator('#view-'+name).waitFor({state:'visible'});assert.equal(await page.locator('.view:visible').count(),1);};
  const push=async()=>{bump();await page.evaluate(s=>window._testSocket.onmessage({data:JSON.stringify(s)}),state);};
  assert.equal(await page.locator('#realizedPnl').textContent(),'₹50.00');assert.equal(await page.locator('#unrealizedPnl').textContent(),'₹25.00');assert.equal(await page.locator('#netPnl').textContent(),'—');
  assert.equal(await page.locator('#noticeTitle').textContent(),'Live entries blocked');
  assert.match(await page.locator('#noticeDetail').textContent(),/Server order permission is off/);
  assert.equal(await page.locator('#noticeAction').getAttribute('href'),'#settings');
  assert.equal(await page.locator('#sessionCutoff').textContent(),'09:30');
  assert.equal(await page.locator('#squareOffTime').textContent(),'15:15');
  assert.equal(await page.locator('#bookFreshness').textContent(),'Current');
  assert.equal(await page.locator('#tickFreshness').textContent(),'Current');
  assert.equal(await page.locator('#workflowWatchlist').textContent(),'2 locked');
  assert.equal(await page.locator('#workflowRange').textContent(),'2 verified');
  assert.equal(await page.locator('#workflowEntries').textContent(),'Blocked');
  assert.equal(await page.locator('#workflowExit').textContent(),'0 confirmed');
  assert.equal(await page.locator('#gateProgress').evaluate(n=>n.value),13);
  assert.match(await page.locator('#overviewGates .gate-row').first().textContent(),/Server order permission.*Blocked/);
  assert.equal(await page.evaluate(()=>document.body.scrollHeight<=innerHeight+1),true,'The workspace must not become a long document');
  const overviewSize=await page.locator('#view-overview').evaluate(n=>({height:n.clientHeight,content:n.scrollHeight}));
  assert.ok(overviewSize.content<=overviewSize.height+1,`Desktop overview height ${overviewSize.content}px exceeds ${overviewSize.height}px`);
  for(const selector of ['.pnl-panel','.readiness-panel'])assert.equal(await page.locator(selector).evaluate(n=>n.scrollHeight<=n.clientHeight+1),true,'Overview cards must not clip their contents');
  await page.screenshot({path:path.join(screenshotDir,'kotak-dashboard-preview-desktop.png'),fullPage:true,animations:'disabled'});
  await page.setViewportSize({width:1280,height:720});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.locator('#overviewConnections').scrollIntoViewIfNeeded();
  await page.setViewportSize({width:1440,height:1000});
  await go('signals');assert.equal(await page.locator('#signalBody tr').count(),2);
  await page.locator('#signalSearch').fill('TESTB');assert.equal(await page.locator('#signalBody tr').count(),1);await page.locator('#signalBody button').click();assert.match(await page.locator('#evidence').textContent(),/fixture B/);
  await page.locator('#signalSearch').fill('');await page.locator('#signalFilters [data-filter="LONG"]').click();assert.equal(await page.locator('#signalBody tr').count(),1);assert.match(await page.locator('#signalBody').textContent(),/TESTA/);
  await go('market');assert.equal(await page.locator('#orbBody tr').count(),2);
  state.orb_monitor.state='ENTRY_WINDOW_CLOSED';state.signals[0].entry_status='EXPIRED';await push();
  assert.match(await page.locator('#orbInfo').textContent(),/Entry monitoring ended/);
  assert.match(await page.locator('#orbBody').textContent(),/Expired · no trigger/);
  await go('signals');assert.match(await page.locator('#signalBody').textContent(),/Expired · no trigger/);
  state.orb_monitor.state='MONITORING';delete state.signals[0].entry_status;await push();
  await go('market');await page.locator('[data-market-tab="mapping"]').click();assert.equal(await page.locator('#mappingBody tr').count(),2);await page.locator('#marketSearch').fill('TESTA');assert.equal(await page.locator('#mappingBody tr').count(),1);
  await go('positions');assert.equal(await page.locator('#positionBody td').count(),8);await page.locator('#positionBody .row-open').click();await page.locator('#detailDialog').waitFor({state:'visible'});assert.match(await page.locator('#detailContent').textContent(),/105/);await page.keyboard.press('Escape');await page.locator('#detailDialog').waitFor({state:'hidden'});
  assert.equal(await page.locator('#positionTotalPnl').textContent(),'₹75.00');
  assert.equal(await page.locator('#positionOpenCount').textContent(),'1');
  assert.match(await page.locator('#positionClosedBody').textContent(),/No fully closed ORB trades/);
  state.closed_trades=journal.trades;await push();
  assert.match(await page.locator('#positionClosedBody').textContent(),/TESTA.*₹25.00/);
  await page.locator('#positionFilters [data-filter="closed"]').click();assert.match(await page.locator('#positionBody').textContent(),/No NSE/);await page.locator('#positionFilters [data-filter="open"]').click();assert.equal(await page.locator('#positionBody .row-open').count(),1);
  await go('orders');assert.equal(await page.locator('#orderBody tr').count(),50);assert.match(await page.locator('#ordersPagination').textContent(),/151/);
  await page.locator('#ordersPagination [aria-label="Next page"]').focus();await push();assert.equal(await page.locator('#ordersPagination [aria-label="Next page"]').evaluate(n=>n===document.activeElement),true,'Live updates must preserve pagination focus');
  for(let i=0;i<3;i++)await page.locator('#ordersPagination [aria-label="Next page"]').click();assert.equal(await page.locator('#orderBody tr').count(),1);assert.match(await page.locator('#orderBody').textContent(),/TEST-0/);
  await page.locator('#orderSearch').fill('TEST-150');assert.equal(await page.locator('#orderBody tr').count(),1);await page.locator('#orderBody tr td:first-child').click();assert.match(await page.locator('#detailContent').textContent(),/TEST-150/);await page.locator('#closeDetail').click();
  const downloadPromise=page.waitForEvent('download');await page.locator('[data-export="orders"]').click();const download=await downloadPromise;const csv=fs.readFileSync(await download.path(),'utf8');assert.match(csv,/'=test-formula/);assert.match(csv,/TEST-150/);
  await page.locator('#orderSearch').fill('');await page.locator('#orderStatus').selectOption('rejected');assert.match(await page.locator('#orderBody').textContent(),/No orders/);await page.locator('#orderStatus').selectOption('complete');assert.equal(await page.locator('#orderBody tr').count(),50);
  await go('fills');assert.equal(await page.locator('#fillBody tr').count(),50);await page.locator('#fillSearch').fill('TEST-FILL-0');assert.equal(await page.locator('#fillBody tr').count(),1);
  await go('journal');await page.waitForFunction(()=>document.getElementById('journalClosed').textContent==='1');assert.equal(await page.locator('#journalTradeBody tr').count(),1);assert.match(await page.locator('#journalTradeBody').textContent(),/TESTA/);assert.equal(await page.locator('#journalGross').textContent(),'₹25.00');assert.equal(await page.locator('#journalLoss').textContent(),'₹0.00');assert.equal(await page.locator('#journalReturn').textContent(),'5.00%');assert.match(await page.locator('#journalEventBody').textContent(),/FILLED/);assert.match(await page.locator('#journalFillBody').textContent(),/F-TEST/);assert.match(await page.locator('#journalFees').textContent(),/NOT AVAILABLE/);
  await go('settings');assert.equal(await page.locator('#saveSettings').isDisabled(),true);await page.locator('#settingAllocation').fill('125000');await page.locator('#settingGross').focus();await push();assert.equal(await page.locator('#settingAllocation').inputValue(),'125000');
  await go('overview');await go('settings');assert.equal(await page.locator('#settingAllocation').inputValue(),'125000');await page.locator('#resetSettings').click();assert.equal(await page.locator('#settingAllocation').inputValue(),'100000');
  await page.locator('#settingsPrice').fill('320.50');
  assert.match(await page.locator('#settingsShares').textContent(),/^312 shares/);
  assert.equal(await page.locator('#saveSettings').isDisabled(),true,'Calculator does not edit settings');
  const edits={settingAllocation:'125500.25',settingGross:'300000.25',settingMaxPositions:'3',
    settingMaxLong:'2',settingMaxShort:'1',settingCutoff:'09:27',settingExit:'15:10'};
  for(const [id,value] of Object.entries(edits))await page.locator('#'+id).fill(value);
  assert.equal(await page.locator('#settingsPlanned').textContent(),'₹3,00,000.25');
  assert.equal(await page.locator('#settingsSlots').textContent(),'2');
  assert.equal(await page.locator('#settingsShares').textContent(),'391 shares · ₹1,25,315.50 reference notional');
  await page.locator('#settingsPrice').fill('125500.26');
  assert.match(await page.locator('#settingsShares').textContent(),/^0 shares/);
  await page.locator('#settingGross').fill('125500.24');
  assert.match(await page.locator('#settingsValidation').textContent(),/Gross cap must cover/);
  assert.equal(await page.locator('#saveSettings').isDisabled(),true);
  await page.locator('#settingGross').fill(edits.settingGross);
  await page.locator('#settingMaxPositions').fill('4');
  assert.match(await page.locator('#settingsValidation').textContent(),/combined LONG and SHORT/);
  assert.equal(await page.locator('#saveSettings').isDisabled(),true);
  await page.locator('#settingMaxPositions').fill(edits.settingMaxPositions);
  await page.locator('#settingExit').fill('09:27');
  assert.match(await page.locator('#settingsValidation').textContent(),/must follow/);
  assert.equal(await page.locator('#saveSettings').isDisabled(),true);
  await page.locator('#settingExit').fill(edits.settingExit);
  await page.locator('#settingCutoff').fill('09:31');
  assert.equal(await page.locator('#saveSettings').isDisabled(),true);
  await page.locator('#settingCutoff').fill(edits.settingCutoff);
  await page.locator('#saveSettings').click();
  await page.waitForFunction(()=>document.getElementById('settingsMessage').textContent.includes('Configuration saved'));
  await page.reload();await page.waitForFunction(()=>document.getElementById('settingAllocation').value==='125500.25');
  for(const [id,value] of Object.entries(edits))assert.equal(await page.locator('#'+id).inputValue(),value);
  assert.equal(await page.locator('#saveSettings').isDisabled(),true);
  state.armed=true;await push();assert.equal(await page.locator('#settingAllocation').isDisabled(),true);assert.equal(await page.locator('#saveSettings').isDisabled(),true);state.armed=false;await push();assert.equal(await page.locator('#arm').isDisabled(),true);
  state.readiness_blockers=['FRESH_QUOTES','SERVER_ORDERS_DISABLED'];await push();
  assert.equal(await page.locator('#tickFreshness').textContent(),'Stale');
  state.orders_permitted_by_server=true;state.readiness=true;state.readiness_blockers=[];await push();
  assert.equal(await page.locator('#noticeTitle').textContent(),'Ready to arm');
  assert.equal(await page.locator('#workflowEntries').textContent(),'Ready to arm');
  await page.evaluate(()=>window._testSocket.onmessage({data:'{invalid'}));
  assert.equal(await page.locator('#risk').textContent(),'UNKNOWN');
  assert.equal(await page.locator('#workflowEntries').textContent(),'Unavailable');
  assert.equal(await page.locator('#gateProgress').getAttribute('value'),null);
  assert.match(await page.locator('#overviewGates').textContent(),/Waiting for live status/);
  assert.equal(await page.locator('#arm').isDisabled(),true);
  await push();
  assert.equal(await page.locator('#risk').textContent(),'READY');
  await page.locator('#arm').click();await page.locator('#armModal').waitFor({state:'visible'});
  await page.locator('#authorization').fill('TEST ONLY - DO NOT SUBMIT');await page.locator('#otp').fill('000000');await page.keyboard.press('Escape');
  await page.locator('#armModal').waitFor({state:'hidden'});await page.waitForFunction(()=>document.getElementById('otp').value==='');assert.equal(await page.locator('#otp').inputValue(),'');assert.equal(await page.locator('#authorization').inputValue(),'');
  state.orders_permitted_by_server=false;state.readiness=false;state.readiness_blockers=['SERVER_ORDERS_DISABLED'];await push();
  await go('connections');assert.equal(await page.locator('#connectionCards .connection-card').count(),4);await page.locator('#runChecks').click();await page.waitForFunction(()=>document.querySelectorAll('#checkResults .check-row').length===7);assert.match(await page.locator('#checkResults').textContent(),/Blocked · HTTP 503/);assert.equal(await page.locator('#checkResults .negative').count(),0);
  healthFailure=true;await page.locator('#runChecks').click();await page.waitForFunction(()=>document.querySelectorAll('#checkResults .negative').length===1);healthFailure=false;
  await go('overview');await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'Mobile page overflows');
  await page.waitForFunction(()=>document.getElementById('sidebar').getBoundingClientRect().right<=0);await page.locator('#toast').waitFor({state:'hidden'});
  await page.locator('#view-overview').evaluate(n=>{n.scrollTop=0;});
  await page.screenshot({path:path.join(screenshotDir,'kotak-dashboard-preview-mobile.png'),fullPage:true,animations:'disabled'});
  await page.locator('#menuToggle').click();assert.equal(await page.locator('#menuToggle').getAttribute('aria-expanded'),'true');await go('orders');assert.equal(await page.locator('#menuToggle').getAttribute('aria-expanded'),'false');assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.reload();await page.waitForFunction(()=>document.getElementById('view-orders').hidden===false);await page.waitForFunction(()=>document.getElementById('connection').textContent==='KOTAK CONNECTED');
  await page.evaluate(()=>window._testSocket.onmessage({data:'{invalid'}));for(const id of ['funds','posCount','mtm','realizedPnl','unrealizedPnl','netPnl'])assert.equal(await page.locator('#'+id).textContent(),'—');assert.equal(await page.locator('#arm').isDisabled(),true);
  assert.equal(await page.locator('#noticeTitle').textContent(),'Live status unavailable');
  assert.equal(await page.locator('#bookFreshness').textContent(),'Unavailable');
  await page.locator('#menuToggle').click();await go('positions');assert.match(await page.locator('#positionBody').textContent(),/unavailable/);
  await push();assert.equal(await page.locator('#positionBody .row-open').count(),1);
  assert.deepEqual(mutations,['/api/live/settings'],'Read-only checks and navigation must never mutate broker/signal state');assert.deepEqual(errors,[]);
  console.log(JSON.stringify({desktop:'passed',mobile:'passed',login_and_api_paths_under_https_prefix:'passed',nine_separate_views:'passed',trade_journal_and_analytics:'passed',search_and_filters:'passed',full_dataset_pagination:'passed',csv_export_sanitized:'passed',detail_drawers:'passed',unsaved_settings_preserved:'passed',configuration_save_and_lock:'passed',readonly_connection_checks:'passed',stale_navigation_and_recovery:'passed',javascript_errors:0}));
 }finally{if(browser)await browser.close();server.closeAllConnections();await new Promise(r=>server.close(r));}
})().catch(e=>{console.error(e.stack||e.message);process.exitCode=1;});
