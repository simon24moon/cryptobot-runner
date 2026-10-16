const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
let playwright;
try { playwright = require('playwright'); } catch { if (!process.env.PLAYWRIGHT_MODULE) throw Error('Install Playwright or set PLAYWRIGHT_MODULE'); playwright = require(process.env.PLAYWRIGHT_MODULE); }
const root=__dirname;
const config=JSON.parse(fs.readFileSync(path.join(root,fs.existsSync(path.join(root,'config.local.json'))?'config.local.json':'config.example.json')));
const job=process.env.CAPTURE_JOB ? JSON.parse(fs.readFileSync(process.env.CAPTURE_JOB)) : {};
const categories=JSON.parse(fs.readFileSync(path.join(root,'bubble_categories.json')));
const categoryMode=job.captureKind==='categories';
if(job.period) config.chartPeriod=job.period;
if(!['1H','1D','1W'].includes(config.chartPeriod)) throw Error('Unsupported period');
const run=job.run || new Date().toISOString().replace(/[:.]/g,'-');
if(!/^[a-zA-Z0-9_-]+$/.test(run)) throw Error('Invalid run ID');
const dir=path.join(root,'captures',run);
const existedBeforeRun=fs.existsSync(path.join(dir,'manifest.json'));
const manifest={run,kind:categoryMode?'categories':'ranks',startedAt:new Date().toISOString(),period:config.chartPeriod,channel:config.slackChannelId,thread_ts:job.thread_ts||null,requestId:job.requestId||run,files:[],failed:[],status:'capturing'};
const save=()=>fs.writeFileSync(path.join(dir,'manifest.json'),JSON.stringify(manifest,null,2));
async function selectPeriod(page){
 const button=page.getByRole('button',{name:config.chartPeriod,exact:true});
 await button.waitFor();
 const selected=await button.evaluate(e=>e.classList.contains('selected'));
 if(!selected) await button.click();
 await page.waitForFunction(period=>Array.from(document.querySelectorAll('button')).some(e=>e.textContent.trim()===period && e.classList.contains('selected')),config.chartPeriod);
}
(async()=>{
 fs.mkdirSync(dir,{recursive:true}); if(fs.existsSync(path.join(dir,'manifest.json'))) throw Error('Run already exists'); save();
 const browser=await playwright.chromium.launch({...(process.env.CHROME_CHANNEL ? {channel:process.env.CHROME_CHANNEL} : {}),headless:true});
 try {
 const page=await browser.newPage({viewport:config.viewport,locale:'en-US',timezoneId:config.timezone});
 page.setDefaultTimeout(25000);
 let reloadRequired=true;
 const targets=categoryMode?categories:config.rankRanges.map(([start,end])=>({start,end,name:`${start} - ${end}`}));
 for(const [index,target] of targets.entries()){
  const {start,end}=target;const label=target.name; let success=false;
  for(let attempt=1;attempt<=config.captureAttemptsPerPage;attempt++){
   try{
    if(reloadRequired){
      await page.goto(config.url,{waitUntil:'domcontentloaded',timeout:60000});
      await selectPeriod(page);
      reloadRequired=false;
    }
    const rangeButton=page.locator('button.filter-select');
    if((await rangeButton.innerText()).trim()!==label){
      await rangeButton.click();
      await page.getByText(label,{exact:true}).click();
    }
    await page.getByRole('button',{name:label,exact:true}).waitFor();
    if(!categoryMode) await page.waitForFunction(({start,end})=>{
      const ranks=Array.from(document.querySelectorAll('table .currency-rank')).map(e=>Number((e.innerText.match(/\d+/g)||[]).at(-1)));
      return ranks.length===100 && new Set(ranks).size===100 && ranks.every(n=>n>=start&&n<=end);
    },{start,end},{timeout:25000});
    else await page.waitForFunction(label=>{
      const ranks=Array.from(document.querySelectorAll('table .currency-rank'));
      return document.querySelector('button.filter-select')?.textContent.trim()===label && ranks.length>0;
    },label,{timeout:25000});
    await page.waitForTimeout(2200);
    await page.waitForFunction(()=>{
      const c=document.querySelector('canvas'); if(!c||c.width<500)return false;
      const d=c.getContext('2d').getImageData(0,0,c.width,c.height).data;
      let colored=0;for(let i=0;i<d.length;i+=64){const r=d[i],g=d[i+1],b=d[i+2];if(Math.max(r,g)>60&&Math.abs(r-g)>25&&Math.max(r,g)>b*1.25)colored++;}
      return colored>500;
    });
    await page.mouse.move(0,0);
    const filename=categoryMode?`${String(index+1).padStart(2,'0')}-${target.slug}-${config.chartPeriod}.png`:`${String(start).padStart(4,'0')}-${String(end).padStart(4,'0')}-${config.chartPeriod}.png`;
    const file=path.join(dir,filename);
    await page.screenshot({path:file,fullPage:false});
    const sha256=crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
    if(manifest.files.some(f=>f.sha256===sha256))throw Error('Duplicate image');
    const item=categoryMode?{category:label,slug:target.slug,itemCount:await page.locator('table .currency-rank').count()}:{start,end};
    manifest.files.push({...item,filename,sha256,capturedAt:new Date().toISOString()});save();
    console.log(`Captured ${label}`);success=true;break;
   }catch(e){console.log(`Retry ${label} attempt ${attempt}: capture step failed`); reloadRequired=true; await page.screenshot({path:path.join(dir,"error.png"),timeout:5000}).catch(()=>{});}
  }
  if(!success){manifest.failed.push(label);save();break;}
 }
 manifest.endedAt=new Date().toISOString(); manifest.status=manifest.failed.length?'partial':'captured';save();console.log(`Manifest: ${path.join(dir,'manifest.json')}`);
 if(manifest.failed.length)process.exitCode=1;
 }finally{await browser.close();}
})().catch(e=>{if(!existedBeforeRun){manifest.status='capture_error';save();}console.error('Capture stopped; review private job status');process.exitCode=1});
