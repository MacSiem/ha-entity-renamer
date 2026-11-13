/* Real frontend and locally installed public custom-card assets, isolated HA. */
const fs = require('node:fs');
const { chromium } = require('playwright');
(async () => {
  const req = JSON.parse(fs.readFileSync('/qa/request.json', 'utf8'));
  const access = fs.readFileSync('/qa/render-token', 'utf8');
  const origin = `http://127.0.0.1:${req.port}`;
  const browser = await chromium.launch({headless:true, executablePath:'/ms-playwright/chromium-1219/chrome-linux/chrome', args:['--no-sandbox']});
  try {
    const context = await browser.newContext({viewport:{width:1280,height:900}});
    await context.addInitScript(({access,origin}) => localStorage.setItem('hassTokens', JSON.stringify({
      access_token:access, token_type:'Bearer', expires_in:1800, expires:Date.now()+1700000,
      hassUrl:origin, clientId:origin+'/'})), {access, origin});
    const page = await context.newPage();
    await page.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    await page.goto(origin+'/lovelace/0', {waitUntil:'domcontentloaded'});
    await page.locator('hui-button-card').first().waitFor({state:'attached',timeout:30000});
    await page.locator('button-card').first().waitFor({state:'attached',timeout:30000});
    await page.locator('bubble-card').first().waitFor({state:'attached',timeout:30000});
    await page.waitForFunction(source => document.querySelector('home-assistant')?.hass?.states?.[source],req.source);
    const before = await page.evaluate(() => {
      function find(root, tag) { for (const el of root.querySelectorAll('*')) {
        if (el.localName === tag) return el;
        const nested = el.shadowRoot && find(el.shadowRoot, tag); if (nested) return nested;
      }}
      const cards = ['hui-button-card','button-card','bubble-card'].map(x => find(document,x));
      return {visible: cards.every(x => x && x.getBoundingClientRect().width > 0),
              refs:cards.map(x => (x._config || x.config)?.entity)};
    });
    await page.locator('button-card').first().locator('ha-card').first().click();
    await page.waitForFunction(source => document.querySelector('home-assistant')?.hass?.states?.[source]?.state === 'on', req.source, {timeout:10000});
    await page.locator('bubble-card').first().locator('.bubble-button-card-container').first().click();
    await page.waitForFunction(source => document.querySelector('home-assistant')?.hass?.states?.[source]?.state === 'off', req.source, {timeout:10000});
    fs.writeFileSync('/qa/result.json', JSON.stringify({...before, button_toggled:true, bubble_toggled:true}));
    await context.close();
  } finally { await browser.close(); }
})().catch(error => { fs.writeFileSync('/qa/result.json', JSON.stringify({error:String(error)})); process.exitCode=1; });
