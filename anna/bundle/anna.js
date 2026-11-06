import {AnnaAppRuntime} from '/static/anna-apps/_sdk/latest/index.js';
import {createTransport} from './transport.js';
let runtime, transport, connectionError;
try {
  runtime = await AnnaAppRuntime.connect();
  transport = createTransport(runtime);
  runtime.onThemeChange?.(({theme})=>{const t=window.HomeBaseTheme;if(t && ['dark','light'].includes(theme) && t.current!==theme)t.toggle();});
} catch(error) { connectionError=error; }
export function connectionInfo(){return transport?.bridge || {};}
export async function annaFetch(path,options={}) {
  if (!transport) throw Error(connectionError?.message || 'Open HomeBase OS from its installed Anna app window.');
  return transport.fetch(path,options);
}
