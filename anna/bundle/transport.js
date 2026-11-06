// Pure, testable Anna transport. No HomeBase credentials enter this module.
export const TOOL_ID = 'tool-grillmaster-homebase-local-bridge-vn4fj8eg';
const sensitive = new Set(['password','confirm','totp','secret','mfasecret','apikey','api_key','token','accesstoken','access_token','refreshtoken','refresh_token','authorization','cookie','pin','code','username','privatekey','clientkey']);
export function containsSecret(value) {
  if (Array.isArray(value)) return value.some(containsSecret);
  return value && typeof value === 'object' && Object.entries(value).some(([k,v]) => sensitive.has(k.toLowerCase().replaceAll('-','_')) || containsSecret(v));
}
export function createTransport(anna) {
  let bridge = {connected:false, commands:false, cameraImages:false};
  let pendingRefresh;
  const invoke = async (path, options) => {
    const method = options.method || 'GET';
    const body = options.body ? JSON.parse(options.body) : undefined;
    if (!/^\/api\/[A-Za-z0-9_/-]+$/.test(path) || path.includes('..') || path.includes('//')) throw Error('Unsupported HomeBase route.');
    if (path.startsWith('/api/auth/') && path!=='/api/auth/logout' || path.includes('/pair/') || path==='/api/tvs/certificate' || path==='/api/integrations' && method !== 'GET' || method==='DELETE') throw Error('Manage accounts, credentials and TV pairing directly in local HomeBase.');
    if (containsSecret(body)) throw Error('Credentials are kept local. Use the local HomeBase settings to save them.');
    if (path==='/api/settings' && method!=='GET' && Object.keys(body||{}).some(k=>k!=='workspaceName')) throw Error('Manage connection settings directly in local HomeBase.');
    if (method!=='GET' && path!=='/api/auth/logout' && !bridge.commands) throw Error('The local connector is read-only. Enable command access locally first.');
    if (path.endsWith('/snapshot') && !bridge.cameraImages) throw Error('Camera image sharing is off. Enable it explicitly in the local connector.');
    // Never pass the dashboard's request headers or browser cookies to Anna.
    const response = await anna.tools.invoke({tool_id:TOOL_ID,method:'request',args:{path,method,...(body!==undefined?{body}:{})},timeoutMs:70000}, {timeoutMs:75000});
    let payload = response;
    for (let i=0;i<3;i++) {
      if (payload && Number.isInteger(payload.status) && payload.data) break;
      if (payload?.success===false || payload?.ok===false) throw Error(payload.error?.message || payload.error || 'HomeBase bridge failed.');
      payload = payload?.result ?? payload?.data ?? payload;
    }
    if (!Number.isInteger(payload?.status) || !payload?.data) throw Error('The HomeBase bridge returned an invalid response.');
    if (path==='/api/status') bridge = {...bridge,...payload.data.bridge};
    const {data,status} = payload;
    const image = data.base64 && data.contentType==='image/jpeg';
    const content = image ? Uint8Array.from(atob(data.base64), c=>c.charCodeAt(0)) : JSON.stringify(data);
    return new Response(content,{status,headers:{'Content-Type':image?'image/jpeg':'application/json'}});
  };
  return {
    get bridge(){return {...bridge};},
    async fetch(path,options={}) {
      // Coalesce overlapping polling, never coalesce commands or retry writes.
      if (path==='/api/overview' && (!options.method || options.method==='GET')) {
        if (!pendingRefresh) pendingRefresh=invoke(path,options).finally(()=>pendingRefresh=null);
        return (await pendingRefresh).clone();
      }
      return invoke(path,options);
    }
  };
}
