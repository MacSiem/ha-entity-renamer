/* Real frontend observation in the isolated HA network namespace. */
const fs = require('node:fs');
const crypto = require('node:crypto');
const { chromium } = require('playwright');

(async () => {
  const request = JSON.parse(fs.readFileSync('/qa/render-request.json', 'utf8'));
  const token = fs.readFileSync('/qa/render-token', 'utf8');
  const origin = `http://127.0.0.1:${request.port}`;
  const browser = await chromium.launch({ headless: true,
    executablePath: '/ms-playwright/chromium-1219/chrome-linux/chrome', args: ['--no-sandbox'] });
  try {
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    await context.addInitScript(({ access, origin }) => localStorage.setItem('hassTokens', JSON.stringify({
      access_token: access, token_type: 'Bearer', expires_in: 1800,
      expires: Date.now() + 1700 * 1000, hassUrl: origin, clientId: origin + '/'
    })), { access: token, origin });
    const page = await context.newPage();
    const responses = [];
    page.on('response', response => {
      const url = new URL(response.url());
      if (url.origin === origin && responses.length < 80)
        responses.push({ path: url.pathname, status: response.status() });
    });
    await page.route('**/*', route => {
      const url = new URL(route.request().url());
      return url.origin === origin && !url.pathname.startsWith('/api/map_tiles/') ? route.continue() : route.abort();
    });
    const routes = [];
    for (const [path, type] of [['/r6-map/0', 'map'], ['/r8-map/0', 'entity']]) {
      await page.goto(origin + path, { waitUntil: 'domcontentloaded' });
      try {
        await page.locator(type === 'map' ? 'hui-map-card' : 'hui-entity-card').first()
          .waitFor({ state: 'attached', timeout: 30000 });
      } catch (error) {
        const diagnostic = await page.evaluate(() => {
          const names = [];
          function visit(root) { for (const el of root.querySelectorAll('*')) {
            if (el.localName.includes('-')) names.push(el.localName);
            if (el.shadowRoot) visit(el.shadowRoot);
          } }
          visit(document);
          return { path: location.pathname, title: document.title, tokenPresent: !!localStorage.getItem('hassTokens'),
            hassPresent: !!document.querySelector('home-assistant')?.hass,
            tags: [...new Set(names)].slice(0, 100) };
        });
        await page.screenshot({ path: `/qa/readback/render-${request.label}-${type}-diagnostic.png` });
        fs.writeFileSync(`/qa/readback/render-${request.label}-${type}-diagnostic.json`,
          JSON.stringify({ diagnostic, responses }, null, 2));
        throw error;
      }
      await page.waitForTimeout(2000);
      const state = await page.evaluate(({ source, type }) => {
        const nodes = [];
        function visit(root) { for (const el of root.querySelectorAll('*')) { nodes.push(el); if (el.shadowRoot) visit(el.shadowRoot); } }
        visit(document);
        const card = nodes.find(n => n.localName === (type === 'map' ? 'hui-map-card' : 'hui-entity-card'));
        const state = document.querySelector('home-assistant')?.hass?.states?.[source];
        const markers = nodes.filter(n => n.localName === 'ha-entity-marker' || n.classList?.contains('leaflet-marker-icon')).length;
        const config = card?._config || card?.config;
        const references = type === 'map' ? config?.entities : [config?.entity];
        return { present: !!card, visible: !!card && !!card.getBoundingClientRect().width,
          references, markers, state: state?.state, latitude: state?.attributes?.latitude, longitude: state?.attributes?.longitude };
      }, { source: request.source, type });
      const screenshot = `/qa/readback/render-${request.label}-${type}.png`;
      await page.screenshot({ path: screenshot });
      const digest = crypto.createHash('sha256').update(fs.readFileSync(screenshot)).digest('hex');
      routes.push({ path, type, ...state, screenshot_sha256: digest });
    }
    fs.writeFileSync('/qa/render-result.json', JSON.stringify({ label: request.label, source: request.source, routes }));
    await context.close();
  } finally { await browser.close(); }
})().catch(error => { process.stderr.write(String(error) + '\n'); process.exitCode = 1; });
