const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const {fork} = require('child_process');
const {chromium} = require('playwright');

const root = __dirname;
const sha = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const port = 43000 + Math.floor(Math.random() * 1000);
const start = () => new Promise((resolve, reject) => {
  const child = fork(path.join(root, 'server.js'), [], {env:{...process.env, CP_HOST_PORT:String(port)}, silent:true});
  const timer = setTimeout(() => reject(new Error('host start timeout')), 10000);
  child.on('message', message => { if(message.ready){clearTimeout(timer);resolve(child);} });
  child.on('exit', code => reject(new Error(`host exited ${code}`)));
});
const snapshot = async page => ({
  ticket: await page.getByRole('heading', {name:'QCU-217'}).textContent().catch(()=>null),
  assignee: await page.getByLabel('Assignee').inputValue().catch(()=>null),
  priority: await page.getByLabel('Priority').inputValue().catch(()=>null),
  note: await page.getByLabel('Release note').inputValue().catch(()=>null),
  status: await page.locator('#status').textContent().catch(()=>null),
});
async function main(){
  const command=process.argv[2]; const server=await start(); const browser=await chromium.launch({headless:true});
  try {
    const page=await browser.newPage(); await page.goto(`http://127.0.0.1:${port}`);
    if(command==='run'){
      const statePath=path.join(root,'state.json'); const initialState=fs.readFileSync(statePath);
      const actions=JSON.parse(fs.readFileSync(path.resolve(process.argv[3]),'utf8'));
      try {
        for(const action of actions){
          if(action.action==='fill') await page.getByLabel(action.label).fill(action.value);
          else if(action.action==='select') await page.getByLabel(action.label).selectOption({label:action.value});
          else if(action.action==='press') await page.getByLabel(action.label).press(action.key);
          else if(action.action==='click') await page.getByRole('button',{name:action.name,exact:true}).click();
          else throw new Error(`unsupported action ${action.action}`);
        }
        await page.getByText('Ready', {exact:true}).waitFor({timeout:5000});
      } catch(error) {
        fs.writeFileSync(statePath, initialState);
        throw error;
      }
      const visible=await snapshot(page); const screenshot=path.join(root,'final.png'); await page.screenshot({path:screenshot,fullPage:true});
      const receipt={schemaVersion:1,host:'chromium-public-surface',actions,visible,screenshot:'final.png',screenshotSha256:sha(screenshot)};
      const receiptPath=path.join(root,'host-receipt.json'); fs.writeFileSync(receiptPath,JSON.stringify(receipt,null,2));
      console.log(JSON.stringify({receipt:'host-receipt.json',receiptSha256:sha(receiptPath),screenshotSha256:receipt.screenshotSha256}));
    } else if(command==='observe'){
      await page.getByText('Ready', {exact:true}).waitFor({timeout:5000});
      const visible=await snapshot(page); const state=JSON.parse(fs.readFileSync(path.join(root,'state.json'),'utf8'));
      const receiptPath=path.join(root,'host-receipt.json');
      const receipt={schemaVersion:1,host:'fresh-chromium-public-surface',visible,events:state.events,actionReceiptSha256:sha(receiptPath)};
      const observerPath=path.join(root,'observer-receipt.json'); fs.writeFileSync(observerPath,JSON.stringify(receipt,null,2));
      console.log(JSON.stringify({observer:'observer-receipt.json',observerSha256:sha(observerPath),...receipt}));
    } else throw new Error('usage: node host_driver.js run ACTIONS.json | observe');
  } finally { await browser.close(); server.kill(); }
}
main().catch(error=>{console.error(error.stack||error);process.exitCode=1;});
