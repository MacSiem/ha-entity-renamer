/* Real bundled card in the isolated native HA frontend. */
const fs = require('node:fs');
const { chromium } = require('playwright');
const dir = '/qa';
const request = JSON.parse(fs.readFileSync(dir + '/request.json', 'utf8'));
const access = fs.readFileSync(dir + '/render-token', 'utf8');
const origin = `http://127.0.0.1:${request.port}`;
const write = (name, value) => {
  const temp = `${dir}/${name}.tmp`;
  fs.writeFileSync(temp, JSON.stringify(value), {mode: 0o600});
  fs.renameSync(temp, `${dir}/${name}.json`);
};
const wait = async name => {
  const until = Date.now() + 2400000;
  while (!fs.existsSync(`${dir}/${name}.go`)) {
    if (Date.now() > until) throw Error('fixture handoff timeout: ' + name);
    await new Promise(resolve => setTimeout(resolve, 100));
  }
};
(async () => {
  const browser = await chromium.launch({headless:true, executablePath:'/ms-playwright/chromium-1219/chrome-linux/chrome', args:['--no-sandbox']});
  let page, card;
  const wsErrors = [];
  const connectionEvents = [];
  try {
    const member = await browser.newContext({viewport:{width:1280,height:900}});
    const memberAccess = fs.readFileSync(dir + '/render-user-token', 'utf8');
    await member.addInitScript(({access,origin}) => localStorage.setItem('hassTokens', JSON.stringify({
      access_token:access, token_type:'Bearer', expires_in:18000, expires:Date.now()+17900000,
      hassUrl:origin, clientId:origin+'/'})), {access:memberAccess, origin});
    const memberPage = await member.newPage();
    await memberPage.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    await memberPage.goto(origin + '/p4-ui/0', {waitUntil:'domcontentloaded'});
    const memberCard = memberPage.locator('ha-entity-renamer');
    await memberCard.waitFor({state:'visible', timeout:30000});
    await memberPage.waitForFunction(() => document.querySelector('home-assistant')?.hass?.user?.is_admin === false);
    const memberCanWrite = await memberCard.evaluate(el => el._renameAuthorityAvailable());
    if (memberCanWrite) throw Error('non-admin card has write authority');
    await member.close();
    const context = await browser.newContext({viewport:{width:1280,height:900}});
    await context.addInitScript(({access,origin}) => localStorage.setItem('hassTokens', JSON.stringify({
      access_token:access, token_type:'Bearer', expires_in:18000, expires:Date.now()+17900000,
      hassUrl:origin, clientId:origin+'/'})), {access, origin});
    page = await context.newPage();
    page.on('framenavigated', frame => { if (frame === page.mainFrame()) connectionEvents.push({event:'navigation', url:frame.url(), at:Date.now()}); });
    page.on('websocket', socket => {
      socket.on('close', () => connectionEvents.push({event:'websocket-close', at:Date.now()}));
      socket.on('socketerror', error => connectionEvents.push({event:'websocket-error', error:String(error), at:Date.now()}));
      socket.on('framereceived', event => {
      try {
        const message = JSON.parse(event.payload);
        if (message.type === 'result' && message.success === false)
          wsErrors.push({id:message.id, error:message.error});
      } catch (_) { /* Ignore non-JSON websocket frames. */ }
      });
    });
    const snapshot = async label => {
      try {
        const state = await card.evaluate(el => ({
          connected:el.isConnected, authority:el._renameAuthorityAvailable(),
          hassConnected:el.hass?.connected ?? null,
          migration:el._migration ? {stage:el._migration.stage, busy:el._migration.busy,
            review:el._migration.review, error:el._migration.error,
            operation_id:el._migration.operation_id, applyState:el._migration.applyState,
            inspection:el._migration.inspection} : null,
          text:(el.shadowRoot?.innerText || '').slice(0, 30000),
          applyPresent:!!el.shadowRoot?.querySelector('#applyMigration')
        }));
        write(label, {state, url:page.url(), wsErrors, connectionEvents});
        await page.screenshot({path:dir + '/' + label + '.png'});
        fs.chmodSync(dir + '/' + label + '.png', 0o600);
      } catch (error) { write(label, {snapshot_error:String(error), wsErrors, connectionEvents}); }
    };
    await page.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    await page.goto(origin + '/p4-ui/0', {waitUntil:'domcontentloaded'});
    card = page.locator('ha-entity-renamer');
    await card.waitFor({state:'visible', timeout:30000});
    await page.waitForFunction(() => document.querySelector('home-assistant')?.hass?.user?.is_admin === true);
    const queue = async (entities, deviceName) => {
    for (const row of entities) {
      const device = row.device || '__renamer_unassigned__';
      const target = card.locator(`.device-header[data-device-id="${device}"]`);
      if (await target.getAttribute('aria-expanded') !== 'true') await target.click();
      await card.locator(`[data-add-single="${row.old}"]`).click();
      await card.locator('#entityEditObjectId').fill(row.new.split('.')[1]);
      if (row.name) await card.locator('#entityEditName').fill(row.name);
      await card.locator('#queueEntityEdit').click();
    }
    const deviceHeader = card.locator(`.device-header[data-device-id="${request.device}"]`);
    if (await deviceHeader.getAttribute('aria-expanded') === 'true') await deviceHeader.click();
    await deviceHeader.click();
    await card.locator('#deviceName').fill(deviceName);
    await card.locator('#applyDeviceName').click();
    await card.locator('[data-tab="queue"]').click();
    await card.locator('#executeRenames').click();
    await card.locator('#prepareMigration').click();
    await card.locator('#applyMigration').waitFor({state:'visible', timeout:2400000});
    };
    await queue(request.entities, request.device_name);
    const reviewed = await card.evaluate(el => ({operation_id:el._migration.operation_id,
      fingerprint:el._migration.fingerprint, changes:el._migration.review.changes.length,
      domains:el._migration.review.dependencies.domains}));
    reviewed.nonadmin_card_denied = !memberCanWrite;
    if (reviewed.changes !== request.entities.length + 1) throw Error('incomplete server review');
    for (const domain of ['powercalc','energy','template','automation','script','dashboard','homekit','utility_meter'])
      if (!reviewed.domains.includes(domain)) throw Error('missing reviewed dependency: ' + domain);
    await page.screenshot({path:dir + '/ui-review.png'});
    fs.chmodSync(dir + '/ui-review.png', 0o600);
    write('review', reviewed);
    await wait('apply');
    await snapshot('pre-apply-witness');
    try { await card.locator('#applyMigration').click(); }
    catch (error) { await snapshot('apply-failure-witness'); throw error; }
    await card.locator('#continueWriteWarning').click();
    await page.waitForFunction(() => {
      const c=document.querySelector('home-assistant')?.shadowRoot;
      function find(root) { for (const el of root?.querySelectorAll('*') || []) {
        if (el.localName === 'ha-entity-renamer') return el;
        const nested = el.shadowRoot && find(el.shadowRoot); if (nested) return nested;
      }}
      const m=find(c)?._migration; return m && !m.busy && ['applied','unknown','refused'].includes(m.stage);
    }, null, {timeout:2400000});
    const applied = await card.evaluate(el => ({state:el._migration.stage, inspection:el._migration.inspection?.state,
      applyState:el._migration.applyState, registry:el._migration.registryMatches,
      progress:el._migration.journal?.progress || null}));
    if (applied.state !== 'applied' || applied.inspection !== 'applied' || !applied.registry) {
      write('applied', {...applied, wsErrors, error:'UI apply not independently confirmed'});
      throw Error('UI apply not independently confirmed');
    }
    await page.screenshot({path:dir + '/ui-applied.png'});
    fs.chmodSync(dir + '/ui-applied.png', 0o600);
    write('applied', applied);
    await wait('restore');
    // External HomeKit/Utility lifecycle requires a fresh reverse operation;
    // it does not grant recovery rollback for the previous operation.
    await card.locator('#finishMigration').click();
    await card.locator('[data-tab="devices"]').click();
    const reverse = request.entities.map(row => ({...row, old:row.new, new:row.old,
      ...(row.name ? {name:request.ordinary_original_name} : {})}));
    await queue(reverse, request.original_device_name);
    const inverse = await card.evaluate(el => ({operation_id:el._migration.operation_id,
      fingerprint:el._migration.fingerprint, changes:el._migration.review.changes.length,
      domains:el._migration.review.dependencies.domains}));
    if (inverse.operation_id === reviewed.operation_id || inverse.changes !== request.entities.length + 1)
      throw Error('fresh inverse review missing');
    for (const domain of reviewed.domains)
      if (!inverse.domains.includes(domain)) throw Error('missing inverse dependency: ' + domain);
    await card.locator('#applyMigration').click();
    await card.locator('#continueWriteWarning').click();
    await page.waitForFunction(() => {
      function find(root) { for (const el of root.querySelectorAll('*')) {
        if (el.localName === 'ha-entity-renamer') return el;
        const nested = el.shadowRoot && find(el.shadowRoot); if (nested) return nested;
      }}
      const m=find(document)?._migration; return m && !m.busy && ['applied','unknown','refused'].includes(m.stage);
    }, null, {timeout:2400000});
    const restored = await card.evaluate(el => ({state:el._migration.stage, kind:'fresh_inverse',
      inspection:el._migration.inspection?.state, registry:el._migration.registryMatches,
      operation_id:el._migration.operation_id}));
    if (restored.state !== 'applied' || restored.inspection !== 'applied' || !restored.registry
        || restored.operation_id !== inverse.operation_id)
      throw Error('UI restore not independently confirmed');
    await page.screenshot({path:dir + '/ui-restored.png'});
    fs.chmodSync(dir + '/ui-restored.png', 0o600);
    write('restored', restored);
    await context.close();
  } finally {
    let migration = null;
    try {
      if (card && await card.count()) migration = await card.evaluate(el => ({
        stage:el._migration?.stage, busy:el._migration?.busy,
        inspection:el._migration?.inspection?.state, applyState:el._migration?.applyState,
        registry:el._migration?.registryMatches, progress:el._migration?.journal?.progress || null
      }));
    } catch (error) { migration = {snapshot_error:String(error)}; }
    write('terminal-witness', {migration, wsErrors, connectionEvents});
    await browser.close();
  }
})().catch(error => {
  for (const label of ['review','applied','restored']) if (!fs.existsSync(`${dir}/${label}.json`)) write(label, {error:String(error)});
  process.exitCode=1;
});
