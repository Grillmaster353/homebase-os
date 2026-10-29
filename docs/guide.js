// Copy code only after the reader explicitly clicks; never run commands.
for (const pre of document.querySelectorAll('.guide-content pre')) {
  const bar=document.createElement('div');bar.className='code-tools';
  const label=document.createElement('span');label.textContent='TERMINAL COMMANDS · CHECK WHICH COMPUTER THIS STEP USES';bar.append(label);
  if(navigator.clipboard?.writeText){
    const button=document.createElement('button');button.className='copy-code';button.type='button';button.textContent='Copy commands';
    button.addEventListener('click',async()=>{try{await navigator.clipboard.writeText(pre.textContent.trim());button.textContent='Copied';setTimeout(()=>button.textContent='Copy commands',1800);}catch{button.textContent='Select and copy manually';}});bar.append(button);
  }
  pre.before(bar);
}
