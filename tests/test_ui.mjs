// Offline contract tests. No browser, device discovery or network access occurs.
// A minimal DOM shell lets the real UI functions render controlled API fixtures.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import test from 'node:test';
import './test_theme.mjs';
import { BRAND_LOGOS, brandLogo } from '../web/assets/brands/manifest.js';
import { icon as realIcon } from '../web/icons.js';

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));

function harness(respond = () => ({})) {
  const elements = new Map(['#app', '#toast-root', '#modal-root'].map(key => [key, { innerHTML: '' }]));
  const requests = [];
  const handlers = {};
  const sandbox = {
    console,
    BRAND_LOGOS, brandLogo,
    location: { hash: '' },
    document: {
      activeElement: { tagName: 'BODY', focus() {} },
      querySelector: selector => elements.get(selector) || null,
      getElementById: id => elements.get('#'+id) || null,
      querySelectorAll: () => [],
      addEventListener(name,handler) { const previous=handlers[name];handlers[name]=previous?async(...args)=>{await previous(...args);await handler(...args);}:handler; },
    },
    window: { addEventListener() {}, scrollTo() {} },
    history: { replaceState() {} },
    requestAnimationFrame() {},
    setTimeout() { return 1; },
    clearTimeout() {},
    setInterval() {},
    async fetch(url, options) {
      const request = { url, ...options, json: options?.body ? JSON.parse(options.body) : undefined };
      requests.push(request);
      const result = respond(request);
      const status = result?._status || 200;
      return { ok: status < 400, status, json: async () => result?._body || result };
    },
  };
  let source = fs.readFileSync(path.join(root, 'web', 'app.js'), 'utf8');
  source = source.replace(/^import .*?;\s*/gm, '');
  source = 'const icon = () => "<svg></svg>";\n' + source;
  source = source.replace(/^await boot\(\);\s*$/m, '');
  source += '\nglobalThis.ui = { state, api, tvCard, smartTVsView, tvRemote, tvKeyboard, remoteCapabilities, loadEntities, automationsView, propose, date, deploymentSettings, camerasView, lanDetails, networkIdentityBadge, handleAction, shell, stats, securitySettings, helpModal, workspaceModal, connectionModal, pairingModal, devicesView, overview, prepareIntegrationValues, sendTVCommand, authView, render };';
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox, { filename: 'app.js', timeout: 1000 });
  sandbox.ui.state.status = { authenticated: true, csrfToken: 'fixture-csrf', user: 'fixture' };
  return { ui: sandbox.ui, requests, elements, handlers, browserWindow: sandbox.window };
}

test('Home Assistant entity API fixture renders usable, escaped controls', async () => {
  const { ui, requests } = harness(() => ({
    entities: [{ entityId: 'light.study', name: '<img src=x onerror=alert(1)>',
      attributes: { friendly_name: '<img src=x onerror=alert(1)>' },
      domain: 'light', state: 'on', integrationId: 'ha-fixture' }], errors: [],
  }));
  ui.state.overview.integrations = [{ id: 'ha-fixture', type: 'homeassistant' }];
  await ui.loadEntities();
  const html = ui.automationsView();
  assert.match(html, /data-entity="light\.study"/);
  assert.match(html, /data-integration="ha-fixture"/);
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.doesNotMatch(html, /<img src=x/);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, '/api/entities');
});

test('TV controls use commands accepted by the integration adapter', () => {
  const { ui } = harness();
  const html = ui.tvCard({ id: 'tv-fixture', type: 'samsung', name: 'Study TV', entityId: 'media_player.study', status: 'configured' });
  const commands = [...html.matchAll(/data-command="([^"]+)"/g)].map(match => match[1]);
  assert.deepEqual(commands, ['power_on', 'power_off', 'volume_down', 'play_pause', 'volume_up']);
});

test('camera discovery renders safe automatic address candidates without accessing a device', () => {
  const { ui, requests } = harness();
  ui.state.overview.network.discoveryStarted = true;
  ui.state.overview.devices = [
    { id: '192.168.1.20', type: 'camera', cameraCandidate: true, name: '<script>camera</script>',
      vendor: 'Hikvision', ip: '192.168.1.20', suggestedHost: 'http://192.168.1.20:80', suggestedIntegration: 'hikvision', source: 'ONVIF WS-Discovery' },
    { id: '192.168.1.21', type: 'camera', name: 'ONVIF camera', ip: '192.168.1.21', source: 'ONVIF WS-Discovery' },
  ];
  const html = ui.camerasView();
  assert.match(html, /data-camera-candidate="192\.168\.1\.20"/);
  assert.match(html, /http:\/\/192\.168\.1\.20:80/);
  assert.match(html, /data-device="192\.168\.1\.21"/);
  assert.match(html, /&lt;script&gt;camera&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>camera/);
  assert.match(html, /every 60 seconds/);
  assert.equal(requests.length, 0);
});

test('LAN settings expose only the HTTPS address and real advertisement state', () => {
  const { ui } = harness();
  ui.state.overview.lan = { url: 'https://homebase-1234abcd.local:8420', advertisementState: 'advertising', autoDiscover: true };
  const html = ui.lanDetails();
  assert.match(html, /https:\/\/homebase-1234abcd\.local:8420/);
  assert.match(html, /advertising/);
  assert.match(html, /homebase-rootCA\.crt/);
  assert.doesNotMatch(html, /rootCA\.key/);
});

test('overview network branding uses a local transparent router logo and real provider name', () => {
  const { ui, requests } = harness();
  ui.state.overview.networkIdentity = { isp: { id:'telstra', name:'Telstra', source:'ipwho.is' }, router:{ id:'tplink', name:'TP-Link' } };
  const html=ui.networkIdentityBadge();
  assert.match(html,/Telstra/);
  assert.match(html,/TP-Link/);
  assert.match(html,/\/assets\/brands\/tplink\.mono\.svg/);
  assert.match(html,/<strong><img[^>]+src="\/assets\/brands\/telstra\.svg"[^>]+alt="Telstra — internet provider"/);
  assert.doesNotMatch(html,/https?:\/\//);
  assert.equal(requests.length,0);
});

test('unlisted ISP and router names stay supported and escaped without a false logo', () => {
  const { ui }=harness();
  ui.state.overview.networkIdentity={isp:{id:'regional-fibre',name:'Regional <Fibre>'},router:{id:'unknown-oem',name:'OEM <Router>'}};
  const html=ui.networkIdentityBadge();
  assert.match(html,/Regional &lt;Fibre&gt;/);
  assert.match(html,/OEM &lt;Router&gt;/);
  assert.doesNotMatch(html,/<img/);
});

test('Exetel gets a readable local logo when the router brand is unknown', () => {
  const { ui }=harness();
  ui.state.overview.networkIdentity={isp:{id:'exetel',name:'Exetel',source:'manual'},router:{id:'unknown'}};
  const html=ui.networkIdentityBadge();
  assert.match(html,/\/assets\/brands\/exetel\.mono\.svg/);
  assert.match(html,/Exetel — internet provider/);
  assert.match(html,/ISP \/ EXIT/);
  assert.match(html,/Manual provider/);
});

test('Telstra automatic and manual badges load bundled artwork without a known router', () => {
  const {ui,requests}=harness();
  for(const source of ['ipwho.is','manual']){
    ui.state.overview.networkIdentity={isp:{id:'telstra',name:'Telstra',source},router:{id:'unknown'}};
    const html=ui.networkIdentityBadge();
    assert.match(html,/src="\/assets\/brands\/telstra\.svg"/);
    assert.match(html,/alt="Telstra — internet provider"/);
    assert.match(html,/ISP \/ EXIT/);
    assert.doesNotMatch(html,/https?:\/\//);
  }
  assert.equal(requests.length,0);
});

test('auto-detect requests a fresh result and renders Automatic mode instead of the saved override', async () => {
  const identity={isp:{id:'cloudflare-inc',name:'Cloudflare, Inc.',source:'ipwho.is',status:'detected'},detectedIsp:{id:'cloudflare-inc',name:'Cloudflare, Inc.',status:'detected'},router:{id:'unknown'}};
  const {ui,requests,elements}=harness(request=>request.url==='/api/settings'?{networkIdentity:{ispName:'',routerName:'',allowExternalLookup:true}}:identity);
  ui.state.overview.networkIdentity={isp:{id:'exetel',name:'Exetel',source:'manual'},router:{id:'unknown'}};
  await ui.handleAction('detect-isp',{disabled:false,innerHTML:'Auto-detect ISP now'});
  const request=requests.find(r=>r.url==='/api/network-identity/detect');
  assert.deepEqual(request.json,{allowExternal:true,forceRefresh:true,useDetected:true});
  assert.equal(ui.state.overview.networkIdentity.isp.name,'Cloudflare, Inc.');
  assert.match(elements.get('#modal-root').innerHTML,/<option value="automatic" selected>/);
  assert.match(elements.get('#modal-root').innerHTML,/id="manual-isp-field" hidden/);
  await ui.handleAction('close',{});
  assert.match(elements.get('#app').innerHTML,/Cloudflare, Inc\./);
  assert.doesNotMatch(elements.get('#app').innerHTML,/>Exetel</);
});

test('a failed logo request switches to a neutral graphic rather than a broken image', () => {
  const {handlers}=harness();
  const fallback={hidden:true};
  const image={tagName:'IMG',hidden:false,classList:{contains:name=>name==='brand-logo-image'},parentElement:{querySelector:()=>fallback}};
  handlers.error({target:image});
  assert.equal(image.hidden,true);
  assert.equal(fallback.hidden,false);
});

test('bundled brand artwork is inert SVG or a transparent WebP', () => {
  for(const brand of Object.values(BRAND_LOGOS)){
    for(const name of ['logo','monoLogo']){
      const file=path.join(root,'web',brand[name].slice(1));
      if(file.endsWith('.png')){const bytes=fs.readFileSync(file);assert.equal(bytes.subarray(0,8).toString('hex'),'89504e470d0a1a0a');assert.ok([4,6].includes(bytes[25]),'brand PNG must contain alpha');continue;}
      if(file.endsWith('.webp')){const bytes=fs.readFileSync(file);assert.equal(bytes.toString('ascii',0,4),'RIFF');assert.equal(bytes.toString('ascii',8,12),'WEBP');assert.equal(bytes.toString('ascii',12,16),'VP8X');assert.ok(bytes[20]&16,'brand WebP must contain alpha');continue;}
      const svg=fs.readFileSync(file,'utf8');
      assert.match(svg,/<svg\b/);
      assert.doesNotMatch(svg,/<(?:script|foreignObject|image)\b|\son\w+\s*=|(?:href|src)\s*=\s*["']https?:/i);
    }
  }
});

test('navigation controls require a configured Home Assistant remote entity', () => {
  const { ui } = harness();
  const config = { id: 'tv-fixture', type: 'samsung', name: 'Study TV', entityId: 'media_player.study', status: 'configured' };
  const withoutRemote = ui.tvCard(config);
  assert.doesNotMatch(withoutRemote, /data-command="(?:up|down|left|right|select|home|back)"/);
  const withRemote = ui.tvCard({ ...config, remoteEntityId: 'remote.study_tv' });
  for (const command of ['up', 'down', 'left', 'right', 'select', 'home', 'back']) {
    assert.ok(withRemote.includes(`data-command="${command}"`), `${command} control is missing`);
  }
});

test('requesting a TV action proposes with CSRF and never approves automatically', async () => {
  const overview = { host: {}, network: { history: [] }, devices: [], integrations: [], events: [], actions: [] };
  const { ui, requests } = harness(request => request.url === '/api/overview' ? overview : { action: { id: 'pending-fixture', status: 'pending' } });
  await ui.propose('tv.command', 'tv-fixture', { command: 'power_on' });
  assert.equal(requests[0].url, '/api/actions');
  assert.equal(requests[0].headers['X-CSRF-Token'], 'fixture-csrf');
  assert.deepEqual(requests[0].json, { type: 'tv.command', target: 'tv-fixture', payload: { command: 'power_on' } });
  assert.ok(requests.every(request => !request.url.includes('/approve')));
});

test('unknown timestamps remain unknown and deployment uses generic Pi instructions', () => {
  const { ui } = harness();
  assert.equal(ui.date(null), '—');
  assert.equal(ui.date(undefined), '—');
  assert.match(ui.deploymentSettings(), /sudo \.\/scripts\/setup-pi\.sh/);
  assert.doesNotMatch(ui.deploymentSettings(), /your Mac|this Mac/);
});

test('an expired session clears all previously loaded private UI data', async () => {
  const { ui, elements } = harness(() => ({ _status: 401, _body: { message: 'Sign in again.' } }));
  ui.state.overview.host = { name: 'private-host-fixture' };
  ui.state.overview.integrations = [{ id: 'private-integration-fixture', type: 'homeassistant' }];
  ui.state.settings = { workspace: '/private/workspace-fixture' };
  ui.state.messages = [{ role: 'user', content: 'private-chat-fixture' }];
  ui.state.entities = [{ entityId: 'light.private-fixture' }];
  ui.state.servers = [{ id: 'private-server-fixture' }];
  await assert.rejects(ui.api('/overview'), /Sign in again/);
  assert.equal(ui.state.status.authenticated, false);
  assert.equal(ui.state.status.csrfToken, undefined);
  assert.equal(ui.state.status.user, undefined);
  assert.equal(Object.keys(ui.state.overview.host).length, 0);
  assert.equal(ui.state.overview.integrations.length, 0);
  assert.equal(Object.keys(ui.state.settings).length, 0);
  assert.equal(ui.state.messages.length, 0);
  assert.equal(ui.state.entities.length, 0);
  assert.equal(ui.state.servers.length, 0);
  assert.doesNotMatch(elements.get('#app').innerHTML, /private-(?:host|integration|chat|server)-fixture/);
});


test('workspace menu is interactive and escapes the installation display name', () => {
  const {ui,elements}=harness();
  ui.state.settings.workspaceName='Family <home>';
  const html=ui.shell('');
  assert.match(html,/<button class="home-switch" data-action="workspace"/);
  assert.match(html,/Family &lt;home&gt;/);
  assert.match(html,/HomeBase OS/);
  assert.match(html,/v1.0.0/);
  ui.workspaceModal();
  assert.match(elements.get('#modal-root').innerHTML,/id="workspace-form"/);
  assert.match(elements.get('#modal-root').innerHTML,/name="workspaceName"/);
});

test('security overview describes application controls without claiming whole-home protection', () => {
  const {ui,elements}=harness();
  ui.state.settings.security={mfaEnabled:true};
  const html=ui.stats();
  assert.match(html,/App security/);
  assert.match(html,/2FA enabled/);
  assert.doesNotMatch(html,/>Protected</);
  ui.handleAction('security-details',{});
  assert.match(elements.get('#modal-root').innerHTML,/does not assess your router/);
  assert.match(elements.get('#modal-root').innerHTML,/Not verified/);
});

test('security settings report missing verification rather than always Active', () => {
  const {ui}=harness();
  ui.state.settings.security={accountConfigured:true,encryptedStorage:false,keyPermissionsRestricted:false,approvalRequired:true};
  const html=ui.securitySettings();
  assert.match(html,/Needs attention/);
  assert.match(html,/Not verified/);
  assert.match(html,/Configured/);
});

test('live receive card keeps its graph and has no bandwidth test or help link', () => {
  const {ui,requests}=harness();
  ui.state.overview.network.rxBps=46250;
  ui.state.overview.network.history=[{rxBps:20000},{rxBps:46250}];
  const html=ui.stats();
  assert.match(html,/Live receive traffic/);
  assert.match(html,/0.37/);
  assert.match(html,/traffic-spark/);
  assert.match(html,/<svg class="stat-spark"/);
  assert.doesNotMatch(html,/traffic-info|speedtest|What’s this|Host traffic · About/);
  assert.equal(requests.length,0);
});

test('getting started removes personal migration steps and mandatory Home Assistant TV pairing', () => {
  const {ui,elements}=harness();
  ui.helpModal();
  const html=elements.get('#modal-root').innerHTML;
  assert.doesNotMatch(html,/once you’re home|your Mac|TV controls require a supported device paired in Home Assistant/);
  assert.match(html,/without Home Assistant/);
  assert.match(html,/pairing code/);
});

test('direct TV setup needs no Home Assistant token and starts no pairing implicitly', () => {
  const {ui,elements,requests}=harness();
  ui.connectionModal('androidtv',{name:'Living room',host:'192.168.1.50',connectionMode:'direct'});
  let html=elements.get('#modal-root').innerHTML;
  assert.match(html,/Direct · no Home Assistant needed/);
  assert.match(html,/id="tv-ha-fields" hidden disabled/);
  assert.match(html,/name="host" value="192.168.1.50"/);
  ui.pairingModal({id:'tv-fixture',name:'Living room',protocol:'androidtv',host:'192.168.1.50'});
  html=elements.get('#modal-root').innerHTML;
  assert.match(html,/Approve & start pairing/);
  assert.equal(requests.length,0);
});

test('direct remote controls require pairing and exclude unsupported LG toggle semantics', () => {
  const {ui}=harness();
  const config={id:'direct-fixture',type:'webos',protocol:'webos',connectionMode:'direct',name:'Living room',status:'configured'};
  let html=ui.tvCard(config);
  assert.match(html,/Pair this TV to enable supported controls/);
  assert.match(html,/data-command="home"[^>]*disabled/);
  assert.doesNotMatch(html,/data-command="play_pause"/);
  assert.match(html,/data-command="play"/);
  assert.match(html,/data-command="pause"/);
  html=ui.tvCard({...config,hasTvCredentials:true,status:'paired'});
  assert.doesNotMatch(html,/data-command="home"[^>]*disabled/);
  assert.match(html,/Pair again/);
});

test('ambiguous Hisense setup requires an explicit platform and never silently selects Android', () => {
  const {ui,elements,requests}=harness();
  ui.connectionModal('hisense',{name:'A TV',host:'192.168.1.50'});
  const html=elements.get('#modal-root').innerHTML;
  assert.match(html,/<select name="protocol" id="tv-protocol" required>/);
  assert.match(html,/<option value="" selected disabled>Choose the TV’s operating system/);
  assert.doesNotMatch(html,/<option value="androidtv" selected/);
  assert.match(html,/<option value="vidaa"/);
  assert.throws(()=>ui.prepareIntegrationValues({connectionMode:'direct',protocol:''}),/operating system/);
  assert.equal(requests.length,0);
});

test('VIDAA setup preserves saved profile privacy and does not overwrite it with a blank form', () => {
  const {ui,elements,requests}=harness();
  ui.connectionModal('hisense',{id:'vidaa-fixture',name:'A TV',host:'192.168.1.50',connectionMode:'direct',protocol:'vidaa',hasVidaaProfile:true,vidaaProfile:'private-profile-fixture'});
  const html=elements.get('#modal-root').innerHTML;
  assert.match(html,/<option value="vidaa" selected>/);
  assert.match(html,/id="tv-vidaa-fields" >/);
  assert.match(html,/<textarea name="vidaaProfile"[^>]*><\/textarea>/);
  assert.match(html,/An encrypted profile is saved/);
  assert.doesNotMatch(html,/private-profile-fixture/);
  const values=ui.prepareIntegrationValues({connectionMode:'direct',protocol:'vidaa',vidaaProfile:'  '});
  assert.equal(Object.hasOwn(values,'vidaaProfile'),false);
  assert.equal(requests.length,0);
});

test('VIDAA identity form validates JSON and excludes it from other protocols', () => {
  const {ui}=harness();
  for(const invalid of ['not JSON','[]','null','123'])assert.throws(()=>ui.prepareIntegrationValues({connectionMode:'direct',protocol:'vidaa',vidaaProfile:invalid}),/VIDAA credential profile/);
  const profile=JSON.stringify({clientId:'fixture',password:'private-fixture'});
  assert.equal(ui.prepareIntegrationValues({connectionMode:'direct',protocol:'vidaa',vidaaProfile:profile}).vidaaProfile,profile);
  assert.equal(Object.hasOwn(ui.prepareIntegrationValues({connectionMode:'direct',protocol:'androidtv',vidaaProfile:profile}),'vidaaProfile'),false);
  assert.equal(Object.hasOwn(ui.prepareIntegrationValues({connectionMode:'homeassistant',vidaaProfile:profile}),'vidaaProfile'),false);
});

test('VIDAA pairing requires its identity and omits unsupported media toggle controls', () => {
  const {ui,elements,requests}=harness();
  const config={id:'vidaa-fixture',type:'hisense',protocol:'vidaa',connectionMode:'direct',name:'A TV',host:'192.168.1.50'};
  ui.pairingModal(config);
  let html=elements.get('#modal-root').innerHTML;
  assert.match(html,/Configure VIDAA identity/);
  assert.doesNotMatch(html,/data-action="pair-start"/);
  assert.match(html,/PIN alone cannot authenticate/);
  ui.pairingModal({...config,hasVidaaProfile:true});
  html=elements.get('#modal-root').innerHTML;
  assert.match(html,/Approve & start pairing/);
  const remote=ui.tvCard({...config,hasTvCredentials:true});
  assert.doesNotMatch(remote,/data-command="(?:play_pause|power_on|power_off)"/);
  assert.match(remote,/data-command="play"/);
  assert.match(remote,/data-command="pause"/);
  assert.match(ui.tvCard(config),/data-command="home"[^>]*disabled/);
  assert.equal(requests.length,0);
});

test('manual TV permission is explicit, reversible, and scoped to a direct connection', () => {
  const {ui,elements}=harness();
  const base={connectionMode:'direct',protocol:'vidaa'};
  assert.equal(ui.prepareIntegrationValues(base).manualControlEnabled,false);
  assert.equal(ui.prepareIntegrationValues({...base,manualControlEnabled:'on'}).manualControlEnabled,true);
  assert.equal(ui.prepareIntegrationValues({...base,manualControlEnabled:true}).manualControlEnabled,true);
  assert.equal(ui.prepareIntegrationValues({...base,manualControlEnabled:'true'}).manualControlEnabled,false);
  assert.equal(ui.prepareIntegrationValues({connectionMode:'homeassistant',manualControlEnabled:'on'}).manualControlEnabled,false);
  ui.connectionModal('hisense',{...base});
  assert.match(elements.get('#modal-root').innerHTML,/<input type="checkbox" name="manualControlEnabled" >/);
  ui.connectionModal('hisense',{...base,manualControlEnabled:true});
  assert.match(elements.get('#modal-root').innerHTML,/<input type="checkbox" name="manualControlEnabled" checked>/);
});

test('VIDAA remote exposes real power toggle and channel keys with honest labels', () => {
  const {ui}=harness();
  const config={id:'vidaa',type:'hisense',protocol:'vidaa',connectionMode:'direct',name:'A TV',hasTvCredentials:true,manualControlEnabled:true};
  const html=ui.tvCard(config);
  for(const command of ['power_toggle','channel_up','channel_down','play','pause','up','down'])assert.ok(html.includes(`data-command="${command}"`),command);
  assert.match(html,/aria-label="Power"/);
  assert.match(html,/title="Toggle power/);
  assert.match(html,/>CH \+</);
  assert.match(html,/Remote buttons act immediately/);
  assert.doesNotMatch(html,/data-command="power_(?:on|off)"/);
  assert.doesNotMatch(ui.tvCard({...config,type:'samsung',protocol:'samsung'}),/data-command="(?:power_toggle|channel_up|channel_down)"/);
});

test('enabled manual remote sends one authenticated CSRF-protected command without proposing or approving', async () => {
  const {ui,requests,elements}=harness(()=>({message:'Key delivery acknowledged; TV state unverified.'}));
  ui.state.overview.integrations=[{id:'manual-fixture',type:'hisense',protocol:'vidaa',connectionMode:'direct',hasTvCredentials:true,manualControlEnabled:true}];
  const status={textContent:''};elements.set('#tv-status-manual-fixture',status);
  const button={dataset:{tv:'manual-fixture',command:'channel_up'},disabled:false};
  await ui.sendTVCommand(button);
  assert.equal(requests.length,1);
  assert.equal(requests[0].url,'/api/tvs/manual-fixture/command');
  assert.deepEqual(requests[0].json,{command:'channel_up'});
  assert.equal(requests[0].headers['X-CSRF-Token'],'fixture-csrf');
  assert.equal(requests[0].credentials,'same-origin');
  assert.ok(requests.every(r=>!r.url.includes('/actions')));
  assert.equal(button.disabled,false);
  assert.match(status.textContent,/acknowledged/);
  assert.equal(elements.get('#toast-root').innerHTML,'');
});

test('a pending manual key blocks duplicate requests to the same TV and restores controls', async () => {
  let release;
  const {ui,requests}=harness(()=>new Promise(resolve=>release=resolve));
  ui.state.overview.integrations=[{id:'busy-tv',type:'hisense',protocol:'vidaa',connectionMode:'direct',hasTvCredentials:true,manualControlEnabled:true}];
  const button={dataset:{tv:'busy-tv',command:'volume_up'},disabled:false};
  const first=ui.sendTVCommand(button);
  assert.equal(button.disabled,true);
  await ui.sendTVCommand({dataset:{tv:'busy-tv',command:'home'},disabled:false});
  assert.equal(requests.length,1);
  assert.match(ui.tvCard({...ui.state.overview.integrations[0],name:'TV'}),/data-command="home"[^>]*disabled/);
  release({message:'Key delivered.'});await first;
  assert.equal(button.disabled,false);
});

test('manual remote remains paired and opt-in guarded, with ordinary TV actions still proposed', async () => {
  const overview={host:{},network:{history:[]},devices:[],integrations:[],events:[],actions:[]};
  const {ui,requests}=harness(r=>r.url==='/api/overview'?overview:{action:{id:'pending',status:'pending'}});
  const config={id:'tv-fixture',type:'hisense',protocol:'vidaa',connectionMode:'direct',hasTvCredentials:false,manualControlEnabled:true};
  ui.state.overview.integrations=[config];
  const button={dataset:{tv:config.id,command:'home'},disabled:false};
  await assert.rejects(ui.sendTVCommand(button),/Pair this TV/);
  assert.equal(requests.length,0);
  ui.state.overview.integrations=[{...config,hasTvCredentials:true,manualControlEnabled:false}];
  await ui.sendTVCommand(button);
  assert.equal(requests[0].url,'/api/actions');
  assert.deepEqual(requests[0].json,{type:'tv.command',target:'tv-fixture',payload:{command:'home'}});
  assert.ok(requests.every(r=>!r.url.includes('/command')&&!r.url.includes('/approve')));
});

test('play uses its own triangle icon instead of the missing-icon box fallback', () => {
  assert.notEqual(realIcon('play'),realIcon('box'));
  assert.match(realIcon('play'),/m8 4 12 8-12 8Z/);
  assert.notEqual(realIcon('pause'),realIcon('box'));
});

test('TV candidates expose a direct connect action without a Home Assistant integration', () => {
  const {ui}=harness();
  ui.state.overview.devices=[{id:'tv-candidate',name:'Living room',type:'tv',ip:'192.168.1.50',tvCandidate:true,suggestedProtocol:'androidtv'}];
  const html=ui.devicesView();
  assert.match(html,/data-tv-candidate="tv-candidate"/);
  assert.match(html,/Connect TV/);
});

test('Broadband Solutions and Cloudflare show their provider logos when router branding is unknown', () => {
  const {ui}=harness();
  for(const [id,name] of [['broadband-solutions','Broadband Solutions'],['cloudflare-inc','Cloudflare']]){
    ui.state.overview.networkIdentity={isp:{id,name,source:'ipwho.is'},router:{id:'unknown'}};
    const html=ui.networkIdentityBadge();
    assert.ok(html.includes(`/assets/brands/${id}.svg`));
    assert.match(html,/internet provider/);
    assert.match(html,/ISP \/ EXIT/);
  }
});

test('TV removal is visible from both its card and saved connection settings', () => {
  const {ui,elements,requests}=harness();
  const config={id:'removable-tv',type:'androidtv',name:'Living room',host:'192.168.1.50',connectionMode:'direct',protocol:'androidtv'};
  assert.match(ui.tvCard(config),/data-delete="removable-tv"[^>]*[^<]*>.*Remove TV/);
  ui.connectionModal('androidtv',config);
  assert.match(elements.get('#modal-root').innerHTML,/data-delete="removable-tv"/);
  assert.match(elements.get('#modal-root').innerHTML,/Remove TV/);
  assert.equal(requests.length,0);
});

test('published documentation derives working repository and source download links locally', () => {
  const source=fs.readFileSync(path.join(root,'docs/repository.js'),'utf8');
  for(const configured of ['', 'https://github.com/example/homebase-os']){
    const links=[{dataset:{repositoryLink:'source'},hidden:true},{dataset:{repositoryLink:'download'},hidden:true}];
    const context={URL,location:{hostname:configured?'docs.example.test':'example.github.io',pathname:'/homebase-os/'},document:{querySelector:()=>({content:configured}),querySelectorAll:()=>links}};
    vm.runInNewContext(source,context);
    assert.equal(links[0].href,'https://github.com/example/homebase-os');
    assert.equal(links[1].href,'https://github.com/example/homebase-os/archive/refs/heads/main.zip');
    assert.ok(links.every(link=>!link.hidden));
  }
});

test('documentation preview and invalid repository configuration make no external link assumptions', () => {
  const source=fs.readFileSync(path.join(root,'docs/repository.js'),'utf8');
  for(const configured of ['', 'https://attacker.example/fake/repo']){
    const links=[{dataset:{repositoryLink:'source'},hidden:true}];
    vm.runInNewContext(source,{URL,location:{hostname:'localhost',pathname:'/'},document:{querySelector:()=>({content:configured}),querySelectorAll:()=>links}});
    assert.equal(links[0].href,undefined);
    assert.equal(links[0].hidden,true);
  }
});


test('secure LG pairing uses a visible saved fingerprint without cleartext consent', () => {
  const {ui,elements}=harness();
  const config={id:'lg-fixture',type:'webos',protocol:'webos',connectionMode:'direct',name:'LG',host:'https://192.168.1.20:3001',serverFingerprint:'a'.repeat(64)};
  ui.connectionModal('webos',config);
  let html=elements.get('#modal-root').innerHTML;
  assert.match(html,/Read TV certificate/);assert.match(html,/name="serverFingerprint"/);assert.match(html,/https:\/\/TV-address:3001/);
  ui.pairingModal(config);html=elements.get('#modal-root').innerHTML;
  assert.doesNotMatch(html,/pair-insecure-consent/);
  ui.pairingModal({...config,host:'http://192.168.1.20:3000'});
  assert.match(elements.get('#modal-root').innerHTML,/pair-insecure-consent/);
});

test('reading an LG certificate populates public trust metadata without saving or pairing', async () => {
  const {ui,elements,requests}=harness(()=>({address:'192.168.1.20',port:3001,fingerprint:'b'.repeat(64)}));
  elements.set('#integration-form [name="host"]',{value:'https://192.168.1.20:3001'});
  elements.set('#integration-form [name="serverFingerprint"]',{value:''});
  elements.set('#lg-certificate-status',{textContent:''});
  await ui.handleAction('read-lg-certificate',{disabled:false});
  assert.equal(requests.length,1);assert.equal(requests[0].url,'/api/tvs/certificate');
  assert.deepEqual(requests[0].json,{host:'https://192.168.1.20:3001'});
  assert.equal(elements.get('#integration-form [name="serverFingerprint"]').value,'b'.repeat(64));
});

test('Smart TVs has a dedicated navigation destination and Devices contains no remotes', () => {
  const {ui}=harness();ui.state.overview.integrations=[{id:'paired',type:'hisense',protocol:'vidaa',connectionMode:'direct',name:'Living TV',hasTvCredentials:true,manualControlEnabled:true}];
  assert.match(ui.shell(''),/data-nav="tvs"[^>]*[\s\S]*Smart TVs/);
  assert.doesNotMatch(ui.devicesView(),/data-command=/);
  assert.match(ui.smartTVsView(),/Living TV/);
  assert.match(ui.smartTVsView(),/remote-dpad/);
});

test('new remote renders only the platform capabilities and keeps number controls accessible', () => {
  const {ui}=harness();const tv={id:'tv-fixture',name:'TV <unsafe>',type:'hisense',protocol:'vidaa',connectionMode:'direct',hasTvCredentials:true,manualControlEnabled:true,supportedCommands:['power_toggle','channel_up','channel_down','select','home','text','digit_1']};
  const html=ui.tvRemote(tv);
  for(const c of ['power_toggle','channel_up','channel_down','select','home','digit_1'])assert.ok(html.includes(`data-command="${c}"`));
  assert.doesNotMatch(html,/data-command="(?:power_on|volume_up|play_pause)"/);
  assert.match(html,/TV &lt;unsafe&gt;/);assert.match(html,/123 &amp; more|123 & more/);
});

test('digital keyboard drafts are private until explicit send and disappear after sign-out', async () => {
  const {ui,handlers,requests}=harness();ui.state.selectedTV='fixture';
  await handlers.input({target:{id:'tv-text-input',value:'local search draft'}});
  assert.equal(ui.state.tvDrafts.fixture,'local search draft');assert.equal(requests.length,0);
  await ui.api('/protected');
  const denied=harness(()=>({_status:401,_body:{message:'Sign in'}}));denied.ui.state.tvDrafts={fixture:'do not retain'};
  await assert.rejects(denied.ui.api('/protected'));assert.equal(Object.keys(denied.ui.state.tvDrafts).length,0);
});

test('Optus, Vocus and Flip use local provider artwork beside an explicitly branded router', () => {
  const {ui,requests}=harness();
  for(const id of ['optus','vocus','flip']){ui.state.overview.networkIdentity={isp:{id,name:id,source:'ipwho.is',status:'detected'},router:{id:'telstra',name:'Telstra',source:'manual'}};const html=ui.networkIdentityBadge();assert.ok(html.includes(BRAND_LOGOS[id].darkLogo));assert.match(html,/telstra\.svg/);}
  assert.equal(requests.length,0);
});

test('restyled overview retains every existing section, statistic and navigation control', () => {
  const {ui,requests}=harness();
  const html=ui.overview();
  for(const label of ['Network devices','Live receive traffic','Network latency','App security','Network activity','Your home, a little smarter.','Connected devices','Quick actions','Your integrations','System health']) assert.ok(html.includes(label),label);
  for(const action of ['network-identity','add','security-details','connect-ha','approvals']) assert.ok(html.includes(`data-action="${action}"`),action);
  const shell=ui.shell(html);
  for(const route of ['overview','devices','network','cameras','automations','assistant','settings']) assert.ok(shell.includes(`data-nav="${route}"`),route);
  for(const action of ['workspace','search','activity','account','help','toggle-theme']) assert.ok(shell.includes(`data-action="${action}"`),action);
  assert.equal(requests.length,0);
});

test('theme action changes appearance without API calls or resetting active application state', async () => {
  const {ui,requests,elements,browserWindow}=harness();
  let toggles=0;browserWindow.HomeBaseTheme={toggle(){toggles++;}};
  ui.state.page='settings';ui.state.tab='ai';ui.state.modal='pairing';
  ui.state.messages=[{role:'user',content:'Keep this message'}];
  ui.state.overview.actions=[{id:'pending-fixture',status:'pending'}];
  elements.get('#app').innerHTML='Existing page and typed values';
  elements.get('#modal-root').innerHTML='Existing pairing form';
  const before=JSON.stringify(ui.state);
  await ui.handleAction('toggle-theme',{});
  assert.equal(toggles,1);assert.equal(JSON.stringify(ui.state),before);
  assert.equal(elements.get('#app').innerHTML,'Existing page and typed values');
  assert.equal(elements.get('#modal-root').innerHTML,'Existing pairing form');
  assert.equal(requests.length,0);
});


test('signed-out rendering is a full-screen account entry, without dashboard controls', () => {
  const { ui, elements } = harness();
  ui.state.status = { authenticated: false, setupRequired: false };
  ui.render();
  const html = elements.get('#app').innerHTML;
  assert.match(html, /class="auth-screen"/);
  assert.match(html, /id="auth-form"/);
  assert.match(html, /autocomplete="current-password"/);
  assert.match(html, /one-time-code/);
  assert.doesNotMatch(html, /class="sidebar|Live receive traffic|class="setup-banner/);
  assert.match(html, /data-theme-toggle/);
  assert.match(html, /Additional user accounts and public registration are not supported/);
});

test('first account screen permits local setup and explains terminal setup to remote visitors', () => {
  const { ui } = harness();
  ui.state.status = { setupRequired: true, setupLocalAllowed: true };
  assert.match(ui.authView(), /Confirm password/);
  assert.match(ui.authView(), /Create secure account/);
  ui.state.status.setupLocalAllowed = false;
  assert.doesNotMatch(ui.authView(), /id="auth-form"/);
  assert.match(ui.authView(), /Remote account creation is disabled/);
});

test('disabling 2FA asks for password and a fresh code without disabling anything on open', async () => {
  const { ui, requests, elements } = harness();
  ui.state.settings.security = { mfaEnabled: true };
  assert.match(ui.securitySettings(), /data-action="mfa-disable"/);
  await ui.handleAction('mfa-disable', {});
  const html = elements.get('#modal-root').innerHTML;
  assert.match(html, /id="mfa-disable-form"/);
  assert.match(html, /autocomplete="current-password"/);
  assert.match(html, /Fresh authenticator code/);
  assert.match(html, /All sessions will be signed out/);
  assert.equal(requests.length, 0);
});
