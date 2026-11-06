#!/usr/bin/env python3
"""Build the Anna distribution from HomeBase's reviewed classic web interface."""
from pathlib import Path
import argparse
import hashlib
import importlib.util
import json
import re
import shutil
import tarfile

ROOT = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source', type=Path, default=ROOT.parent,
                    help='HomeBase source checkout (defaults to parent directory).')
SOURCE = parser.parse_args().source.resolve()
if not (SOURCE/'web/index.html').is_file():
    parser.error('Point --source at a HomeBase checkout containing web/index.html.')
BUNDLE = ROOT/'bundle'
TOOL_ID = 'tool-grillmaster-homebase-local-bridge-vn4fj8eg'

for p in (SOURCE/'web').rglob('*'):
    if p.is_file():
        if p.name in {'anna-connect.html', 'anna-connect.js'}:
            continue  # Account entry is served only by the private HomeBase host.
        dest=BUNDLE/p.relative_to(SOURCE/'web')
        dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(p,dest)

index=(BUNDLE/'index.html').read_text()
index=re.sub(r'(src|href)="/(?!/)',r'\1="./',index)
(BUNDLE/'index.html').write_text(index)
brands=BUNDLE/'assets/brands/manifest.js'
brands.write_text(brands.read_text().replace('"/assets/','"./assets/'))
app=(BUNDLE/'app.js').read_text()
app="import {annaFetch,connectionInfo} from './anna.js';\n"+app
app=app.replace('await fetch(', 'await annaFetch(')
app=app.replace('No cloud required','Anna-connected · private home host')
app=app.replace('Private by design. Local by default.','Home server private. Connected through Anna.')
app=app.replace('Live telemetry · refreshes every 3s','Live telemetry · refreshes every 10s')
app=app.replace('},3000);','},10000);')
app=app.replace('fetch(`/api/cameras/', 'annaFetch(`/api/cameras/')

def replace_function(name,next_name,content):
    global app
    start=app.index('function '+name+'(')
    end=app.index('function '+next_name+'(',start)
    app=app[:start]+content+'\n'+app[end:]

auth='''function authView(){return `<main class="auth-screen" id="main"><header class="auth-header"><div class="brand"><div class="brand-mark">${icon('home')}</div><span>homebase<span style="color:var(--mint)">.</span></span></div><button class="icon-button" data-action="toggle-theme" data-theme-toggle aria-label="Switch theme">${icon('sun')}</button></header><div class="auth-layout"><section class="auth-story"><p class="eyebrow">HOMEBASE OS × ANNA</p><h1>Your home.<br>In your workspace.</h1><p class="auth-description">Your familiar command centre, connected to a real HomeBase installation. Live charts, supported remotes, cameras, servers and Home Assistant in one place.</p><div class="auth-visual" aria-hidden="true">${orb()}<div class="auth-visual-label">${icon('home')}HOMEBASE OS</div></div></section><section class="auth-card card"><div class="auth-card-mark">${icon('lock')}</div><p class="eyebrow">PRIVATE LOCAL CONNECTION</p><h2>Connect your home.</h2><p class="auth-description">${esc(state.status.bridgeError||'Start the HomeBase connector on your own Mac or Pi, then check the connection here.')}</p><ol class="bridge-steps"><li>Run HomeBase and an Anna Agent on the same home network.</li><li>Open <strong>Connect HomeBase to Anna.command</strong> from your HomeBase Anna App folder.</li><li>Approve the data connection and sign in locally. Your password and authenticator code stay on your device.</li><li>Select <strong>Check connection</strong> below.</li></ol><div class="note">${icon('info')}Home status and requested commands pass through Anna. The dashboard remains private. Camera images and command access each require an explicit local opt-in. A cloud-only Anna Agent cannot reach your home network.</div><p><button class="btn" data-bridge-retry>${icon('refresh')}Check connection</button></p><p><a class="btn secondary" href="https://localhost:8420" target="_blank" rel="noopener noreferrer">Open local HomeBase</a></p><p class="auth-description">Pair devices and edit credentials in local HomeBase. Sessions keep HomeBase’s normal expiry limits.</p></section></div><footer class="auth-footer"><span>HomeBase OS · Anna edition 0.1.0</span><span>No passwords stored in Anna.</span></footer></main>`;}'''
replace_function('authView','authModal',auth)
replace_function('authForm','authView',"function authForm(){return '';}")

# Prevent credential-bearing forms from ever being rendered in the cloud app.
needle="function connectionModal(type,config={}){"
app=app.replace(needle,needle+" localSettingsNotice();return;")
app=app.replace("async function startPairing(el){","async function startPairing(el){localSettingsNotice();return;")
needle="async function handleAction(name,el){"
app=app.replace(needle,needle+"if(['mfa','mfa-disable','account','connect','add-device'].includes(name)){localSettingsNotice();return;}")

settings='''function settingsView(){return heading('Your home, your connection.','Connection and security administration stays in local HomeBase.','','HOMEBASE × ANNA')+`<section class="card" style="padding:28px"><h2>Connected through your local bridge.</h2><p>Use the full local dashboard to add integrations, pair TVs, save credentials, change AI providers or manage two-factor authentication. Those credentials are never submitted through Anna.</p><div class="security-feature">${icon('shield')}<div><strong>Command access</strong><p>${connectionInfo().commands?'Enabled by the local owner. HomeBase approval rules still apply.':'Read-only. Enable commands in the local connector to use the remotes and approvals.'}</p></div></div><div class="security-feature">${icon('camera')}<div><strong>Camera image sharing</strong><p>${connectionInfo().cameraImages?'Enabled locally. Snapshots you request pass through Anna.':'Off. No camera images are sent to Anna.'}</p></div></div><p><a class="btn" href="${esc(connectionInfo().url||'https://localhost:8420')}/#settings" target="_blank" rel="noopener noreferrer">Open local HomeBase settings</a></p><p>To revoke access, run the included disconnect command or sign out of the local connector session. A service restart and normal session expiry also end access.</p></section>`;}'''
replace_function('settingsView','integrationSettings',settings)
app=app.replace("async function boot(){try{", "async function boot(){try{")
start=app.index("catch(e){$('#app').innerHTML=`<div class=\"boot\"",app.index('async function boot()'))
end=app.index('\nawait boot();',start)
app=app[:start]+"catch(e){state.status={authenticated:false,bridgeError:e.message};render();}}"+app[end:]
extra='''
function localSettingsNotice(){openModal('Keep your credentials local.','Pair devices and manage accounts in your private HomeBase dashboard.',`<p>Use local HomeBase to complete this task, then return here and refresh. Your account password, device credentials and pairing codes are never sent through Anna.</p><p><a class="btn" href="${esc(connectionInfo().url||'https://localhost:8420')}/#settings" target="_blank" rel="noopener noreferrer">Open local HomeBase</a></p>`,{kind:'local-settings'});}
document.addEventListener('click',async event=>{if(event.target.closest('[data-bridge-retry]')){event.preventDefault();await boot();}});
'''
app=app.replace('\nawait boot();','\n'+extra+'\nawait boot();')
(BUNDLE/'app.js').write_text(app)
# Browser enrollment replaces the terminal-only first-run path. Keep the
# credential form exclusively on HomeBase's own HTTPS origin.
app=(BUNDLE/'app.js').read_text()
app=app.replace('Start the HomeBase connector on your own Mac or Pi, then check the connection here.','Open the local connection page below, sign into HomeBase and approve the connection. Then return here.')
app=app.replace('<li>Open <strong>Connect HomeBase to Anna.command</strong> from your HomeBase Anna App folder.</li>','<li>Select <strong>Connect in your browser</strong> below. No terminal commands are needed.</li>')
app=app.replace('<p><button class="btn" data-bridge-retry>', '<p><a class="btn" href="https://localhost:8420/anna-connect.html" target="_blank" rel="noopener noreferrer">Connect in your browser</a></p><p><button class="btn secondary" data-bridge-retry>')
app=app.replace('Anna edition 0.1.0','Anna edition 0.1.2')
app=app.replace('To revoke access, run the included disconnect command or sign out of the local connector session.','To revoke access, open the local connection page and select Disconnect and revoke access.')
app=app.replace('Open local HomeBase settings</a>', 'Open local HomeBase settings</a> <a class="btn secondary" href="https://localhost:8420/anna-connect.html" target="_blank" rel="noopener noreferrer">Manage Anna connection</a>')
(BUNDLE/'app.js').write_text(app)
(BUNDLE/'anna-layout.css').write_text('.bridge-steps{padding-left:20px;line-height:1.75;color:var(--text-muted)}.bridge-steps li{margin:10px 0}.auth-card .btn{white-space:normal;text-align:center}.auth-card .note{font-size:12px;line-height:1.7}@media(max-width:540px){.auth-card{padding:23px}.auth-layout{margin-top:16px}.auth-story h1{font-size:34px}.bridge-steps{font-size:13px}.auth-visual{display:none}}')
(BUNDLE/'index.html').write_text((BUNDLE/'index.html').read_text().replace('</head>','<link rel="stylesheet" href="./anna-layout.css">\n</head>'))
manifest={"schema":4,"required_executas":[{"tool_id":TOOL_ID}],"system_prompt_addendum":"You are HomeBase OS, a home command centre connected through the owner's local bridge. Open the HomeBase view for home-network questions. Call only the declared HomeBase request tool. Never ask for account passwords, credentials or pairing codes in Anna; those are entered in the local HomeBase dashboard. Read status before claiming connectivity. Never invent telemetry or claim a command succeeded without its actual result. Do not scan a network, control devices, approve proposals or change files unless the user explicitly requests that operation. Never automatically approve an action proposal. A cloud-only agent cannot reach a private home network.","tags":["home","network","local-first","smart-home"],"ui":{"bundle":{"format":"static-spa","entry":"index.html"},"views":[{"name":"homebase","title":"HomeBase OS","default":True,"min_size":{"w":320,"h":360},"default_size":{"w":1160,"h":760},"resizable":True,"movable":True,"single_instance":True,"icon":"favicon.svg"}],"form_factors":["desktop","mobile"],"host_api":{"tools":["required:*"]}}}
manifest['ui'].pop('host_api', None)  # schema 4 derives API access from declared dependencies
(ROOT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
spec=importlib.util.spec_from_file_location('bridge',ROOT/'bridge/homebase_bridge.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
(ROOT/'bridge/manifest.json').write_text(json.dumps(m.manifest(),indent=2)+'\n')
dist=ROOT/'dist';dist.mkdir(exist_ok=True)
exe=dist/'homebase-anna-bridge'
exe.write_text('''#!/bin/sh
# Resolve Anna's bin symlink before locating the sidecar. Isolate its Python bundle.
unset PYTHONHOME PYTHONPATH DYLD_LIBRARY_PATH DYLD_FRAMEWORK_PATH
exec /usr/bin/python3 -I -c 'import os,runpy,sys;p=os.path.join(os.path.dirname(os.path.realpath(sys.argv[1])),"homebase_bridge.py");sys.argv=[p]+sys.argv[2:];runpy.run_path(p,run_name="__main__")' "$0" "$@"
''')
exe.chmod(0o755)
shutil.copy2(ROOT/'bridge/homebase_bridge.py',dist/'homebase_bridge.py')
with tarfile.open(dist/'homebase-anna-bridge.tar.gz','w:gz') as tar:
    tar.add(exe,arcname=TOOL_ID)
    tar.add(dist/'homebase_bridge.py',arcname='homebase_bridge.py')
    tar.add(ROOT/'bridge/manifest.json',arcname='manifest.json')
files={str(p.relative_to(BUNDLE)):hashlib.sha256(p.read_bytes()).hexdigest() for p in BUNDLE.rglob('*') if p.is_file()}
(dist/'BUNDLE-MANIFEST.json').write_text(json.dumps(files,indent=2)+'\n')
# The web console accepts individual files. A flat, equivalent bundle avoids
# relying on a folder picker while retaining every relative asset reference.
upload=ROOT/'upload';upload.mkdir(exist_ok=True)
seen=set()
for rel in files:
    src=BUNDLE/rel
    name='CREDITS.txt' if src.name=='CREDITS.md' else src.name
    if name in seen: raise RuntimeError('Duplicate flat asset name: '+name)
    seen.add(name)
    content=src.read_bytes()
    if src.suffix in {'.js','.html','.css'}:
        content=content.decode().replace('./assets/brands/manifest.js','./manifest.js').replace('./assets/brands/','./').encode()
    (upload/name).write_bytes(content)
(dist/'UPLOAD-MANIFEST.json').write_text(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in upload.iterdir() if p.is_file()},indent=2)+'\n')
print('Built',len(files),'static files; no runtime state, credentials or private certificates included.')
