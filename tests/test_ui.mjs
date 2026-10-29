// Offline contract tests. No browser, device discovery or network access occurs.
// A minimal DOM shell lets the real UI functions render controlled API fixtures.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import test from 'node:test';
import { BRAND_LOGOS, brandLogo } from '../web/assets/brands/manifest.js';

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
      querySelectorAll: () => [],
      addEventListener(name,handler) { handlers[name]=handler; },
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
  source += '\nglobalThis.ui = { state, api, tvCard, loadEntities, automationsView, propose, date, deploymentSettings, camerasView, lanDetails, networkIdentityBadge, handleAction, shell, stats, securitySettings, helpModal, workspaceModal, connectionModal, pairingModal, devicesView };';
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox, { filename: 'app.js', timeout: 1000 });
  sandbox.ui.state.status = { authenticated: true, csrfToken: 'fixture-csrf', user: 'fixture' };
  return { ui: sandbox.ui, requests, elements, handlers };
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

test('bundled brand vectors have no executable or external content', () => {
  for(const brand of Object.values(BRAND_LOGOS)){
    for(const name of ['logo','monoLogo']){
      const svg=fs.readFileSync(path.join(root,'web',brand[name].slice(1)),'utf8');
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
