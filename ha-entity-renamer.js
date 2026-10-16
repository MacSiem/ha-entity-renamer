/* HA Tools split — ha-entity-renamer v4.4.0 (2026-10-16) — single-tool standalone repo */
(function() {
'use strict';

// BEGIN HA TOOLS UI V3 source-sha256=029c06206e55abad9aa09dfcf65bb13027ec24ce6a0bfacc45a938891b27254e
// HA Tools v3: vendored inside each consumer's own closure, never a window API.
function createUiV3({ language = 'en' } = {}) {
  const pl = String(language).startsWith('pl');
  const states = {
    success: ['Success', 'Sukces', '✓'],
    partial: ['Partial', 'Częściowo', '½'],
    unconfirmed: ['Unconfirmed', 'Niepotwierdzony', '?'],
    error: ['Error', 'Błąd', '✕'],
    not_checked: ['Not checked', 'Nie sprawdzono', '–'],
    no_data: ['No data', 'Brak danych', '∅'],
  };
  const status = (value) => {
    const code = typeof value === 'string' && Object.hasOwn(states, value) ? value : 'not_checked';
    const row = states[code];
    return { code, label: row[pl ? 1 : 0], icon: row[2], countsAsSuccess: code === 'success' };
  };
  const statusHtml = (value) => {
    const result = status(value);
    return `<span class="ht-result-state ht-result-state--${result.code}" data-result-state="${result.code}"><span aria-hidden="true">${result.icon}</span> ${result.label}</span>`;
  };
  const css = `
.ht-result-state { display:inline-flex;align-items:center;gap:4px;padding:3px 8px;border:1px solid currentColor;border-radius:6px;font-size:12px;line-height:1.4;white-space:normal; }
.ht-result-state--success { color:#237b3d;background:rgba(35,123,61,.10); }
.ht-result-state--partial { color:#966400;background:rgba(150,100,0,.10); }
.ht-result-state--unconfirmed { color:#ad5200;background:rgba(173,82,0,.10); }
.ht-result-state--error { color:#c32b2b;background:rgba(195,43,43,.10); }
.ht-result-state--not_checked,.ht-result-state--no_data { color:var(--secondary-text-color,#666);background:transparent; }
.ht-result-state--no_data { border-style:dashed; }
:host(.bento-dark) .ht-result-state--success { color:#81cf99; }
:host(.bento-dark) .ht-result-state--partial { color:#efd080; }
:host(.bento-dark) .ht-result-state--unconfirmed { color:#ffb97b; }
:host(.bento-dark) .ht-result-state--error { color:#ff9999; }
`;
  return { status, statusHtml, css };
}
// END HA TOOLS UI V3

// BEGIN HA TOOLS EXPORT V3 source-sha256=1f9e9c5444ec3b6de44eec136f14ae9e7d89dae1002650b33f746ab9633982f1
/**
 * W6 export dialog. Pure ES module; vendor this factory INSIDE each consumer's
 * own IIFE (remove only the `export` keyword). Never attach it to window.
 * Construction is inert. open() appends only its own container to root.
 *
 * Required dependency injection:
 * root: Element/ShadowRoot; getLanguage(): 'pl'|'en'; createUiV3: existing W4 factory.
 * getContext(): {role,userId,connectionId,datasetId,revision,connected,canExport,
 *                sourceConfirmed}. Identities/revision are nonempty scalar values;
 * bump revision monotonically for source/filter/selection/identity changes,
 * including a restored source; never reuse an old revision. Never return tokens or data.
 * policy: {id,allowlistedFields:string[],neverInclude:[tokens,users,addresses,raw_config]}.
 * formats: explicit subset of ['json','csv','txt']; defaults must be explicit valid choices.
 * buildPayload({context,draft,policy,signal}): read-only, Promise or sync result:
 * {serialized:string,confirmed:true,sourceConfirmed:true,status:'success'|'partial',
 *  included:string[],excluded:string[],sourceLimit:string}.
 * Included/Excluded/sourceLimit describe the REVIEWED CONSUMER policy and actual
 * source limitations in the current language. The consumer MUST explicitly select
 * allowed fields, exclude tokens/users/addresses/raw configuration under EVERY
 * private opt-in, and sanitize CSV formula cells where applicable. A generic
 * recursive export/redactor of arbitrary raw state/config is NOT a valid consumer.
 * This factory cannot inspect or truthfully sanitize an unknown arbitrary schema.
 * For partial reports the consumer also encodes the partial status, inclusion
 * scope and source limitations IN its allowlisted file schema, not only UI text.
 * performDownload({serialized,blob,filename,format,context,signal}): explicit Save
 * only. Return {outcome:'initiated'|'saved'|'unknown'}; 'saved' requires actual
 * adapter confirmation, never infer disk persistence from an anchor click.
 * Optional clock(): monotonic milliseconds; setTimer(fn,ms), clearTimer(handle).
 *
 * Returned API: open({opener}), cancel(), contextChanged(), destroy(). Every open
 * resets private consent. contextChanged() discards bytes/consent immediately;
 * owner also calls cancel/destroy on disconnect. Save ALWAYS rechecks context.
 * One preview on open, explicit Refresh thereafter; bounded 10s, no automatic
 * retries/downloads/persistence. AbortSignal is advisory; late results are ignored.
 */
function createExportDialog(options) {
  const { root, getContext, getLanguage, createUiV3, buildPayload, performDownload,
    clock = () => globalThis.performance.now(), setTimer = (fn, ms) => globalThis.setTimeout(fn, ms),
    clearTimer = id => globalThis.clearTimeout(id), defaultFormat, defaultFilename } = options || {};
  const allowedFormats = ['json', 'csv', 'txt', 'yaml'];
  const excludedCategories = ['tokens', 'users', 'addresses', 'raw_config'];
  const strings = value => Array.isArray(value) && value.length > 0 && value.every(v => typeof v === 'string' && v.trim());
  if (!root?.append || !root.ownerDocument || [getContext, getLanguage, createUiV3, buildPayload, performDownload, clock, setTimer, clearTimer].some(fn => typeof fn !== 'function')) {
    throw new TypeError('root and injected getContext/getLanguage/createUiV3/buildPayload/performDownload/clock/timers are required');
  }
  const suppliedPolicy = options.policy;
  if (!suppliedPolicy || typeof suppliedPolicy.id !== 'string' || !suppliedPolicy.id.trim() || !strings(suppliedPolicy.allowlistedFields) || !strings(suppliedPolicy.neverInclude) || excludedCategories.some(key => !suppliedPolicy.neverInclude.includes(key))) {
    throw new TypeError('Explicit reviewed consumer policy with allowlistedFields and neverInclude is required');
  }
  const policy = Object.freeze({ id: suppliedPolicy.id, allowlistedFields: Object.freeze([...suppliedPolicy.allowlistedFields]), neverInclude: Object.freeze([...suppliedPolicy.neverInclude]) });
  if (!strings(options.formats) || options.formats.some(f => !allowedFormats.includes(f)) || new Set(options.formats).size !== options.formats.length || !options.formats.includes(defaultFormat) || typeof defaultFilename !== 'string') {
    throw new TypeError('Explicit formats/defaultFormat/defaultFilename are required');
  }
  const formats = Object.freeze([...options.formats]);
  const doc = root.ownerDocument;
  const mime = { json: 'application/json;charset=utf-8', csv: 'text/csv;charset=utf-8', txt: 'text/plain;charset=utf-8', yaml: 'application/yaml;charset=utf-8' };
  const copy = {
    en: { title: 'Export', filename: 'Filename', format: 'Format', private: 'Include reviewed private fields (this export only)', included: 'Included in the file', excluded: 'Excluded from the file', source: 'Source and limits', preview: 'Exact file content', save: 'Save', refresh: 'Refresh preview', cancel: 'Cancel', busy: 'Building preview… Save waits for confirmed bytes.', changed: 'Choices changed. Refresh preview before Save.', context: 'Source or access changed. Review the source and refresh preview.', unavailable: 'Source unavailable or unconfirmed. Reconnect or confirm the source, then refresh.', invalidFormat: 'Choose an allowed format: JSON, CSV, TXT or YAML as offered.', invalidName: 'Use a basename of 1–128 characters: no paths, control characters, reserved names or leading/trailing spaces.', extension: 'Filename must end with the selected extension. Edit the filename, then refresh.', timeout: 'Preview exceeded 10 seconds. Check the source and explicitly refresh.', buildError: 'Could not build a confirmed preview. Check the source and refresh.', unconfirmed: 'Bytes or source metadata are unconfirmed. Confirm the source and refresh.', noData: 'No confirmed file content. Select available data and refresh.', saving: 'Handing off the confirmed preview… Wait for the adapter result.', initiated: 'Download initiated. Saving to the filesystem is not confirmed. Refresh only for a new export intent.', saved: 'Save confirmed by the download adapter.', unknown: 'Download result unconfirmed. Check the previous attempt before explicitly refreshing for a new export intent.' },
    pl: { title: 'Eksport', filename: 'Nazwa pliku', format: 'Format', private: 'Dołącz sprawdzone pola prywatne (tylko ten eksport)', included: 'W pliku jest', excluded: 'W pliku nie ma', source: 'Źródło i ograniczenia', preview: 'Dokładna zawartość pliku', save: 'Zapisz', refresh: 'Odśwież podgląd', cancel: 'Anuluj', busy: 'Przygotowywanie podglądu… Zapis wymaga potwierdzonej zawartości.', changed: 'Wybór zmieniony. Odśwież podgląd przed zapisem.', context: 'Źródło lub uprawnienia zmienione. Sprawdź źródło i odśwież podgląd.', unavailable: 'Źródło niedostępne lub niepotwierdzone. Połącz lub potwierdź źródło i odśwież.', invalidFormat: 'Wybierz dostępny format: JSON, CSV, TXT lub YAML z listy.', invalidName: 'Użyj nazwy pliku o długości 1–128 znaków: bez ścieżek, znaków sterujących, nazw zastrzeżonych i skrajnych spacji.', extension: 'Nazwa pliku musi mieć rozszerzenie wybranego formatu. Popraw nazwę i odśwież.', timeout: 'Podgląd przekroczył 10 sekund. Sprawdź źródło i jawnie odśwież.', buildError: 'Nie udało się przygotować potwierdzonego podglądu. Sprawdź źródło i odśwież.', unconfirmed: 'Zawartość lub opis źródła niepotwierdzony. Potwierdź źródło i odśwież.', noData: 'Brak potwierdzonej zawartości pliku. Wybierz dostępne dane i odśwież.', saving: 'Przekazywanie potwierdzonego podglądu… Poczekaj na wynik zapisu.', initiated: 'Pobieranie rozpoczęte. Zapis na dysku nie jest potwierdzony. Odśwież tylko dla nowej próby eksportu.', saved: 'Adapter pobierania potwierdził zapis.', unknown: 'Wynik pobierania niepotwierdzony. Sprawdź poprzednią próbę przed jawnym odświeżeniem dla nowego eksportu.' },
  };
  const css = `
[data-ha-tools-export] { container-type:inline-size;min-width:0;max-width:100%;color:var(--primary-text-color,inherit);font:inherit; }
[data-ha-tools-export] * { box-sizing:border-box; }
[data-ha-tools-export] [role=dialog] { width:100%;min-width:0;border:1px solid var(--divider-color,#aaa);border-radius:12px;background:var(--card-background-color,#fff);padding:16px; }
[data-ha-tools-export] h2,[data-ha-tools-export] h3,[data-ha-tools-export] p,[data-ha-tools-export] li { overflow-wrap:anywhere;white-space:normal; }
[data-ha-tools-export] h2 { margin:0 0 12px;font-size:1.25em; }
[data-ha-tools-export] h3 { margin:12px 0 6px;font-size:1em; }
[data-ha-tools-export] .ht-export-fields { display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:12px; }
[data-ha-tools-export] label { display:flex;flex-direction:column;gap:4px;min-width:0;overflow-wrap:anywhere; }
[data-ha-tools-export] .ht-export-private { flex-direction:row;align-items:center;min-height:44px;margin-top:12px; }
[data-ha-tools-export] input:not([type=checkbox]),[data-ha-tools-export] select,[data-ha-tools-export] button { font:inherit;min-width:44px;min-height:44px;max-width:100%; }
[data-ha-tools-export] input:not([type=checkbox]),[data-ha-tools-export] select { width:100%;color:inherit;background:var(--secondary-background-color,var(--card-background-color,#f5f5f5));border:1px solid var(--divider-color,#aaa);border-radius:6px;padding:8px; }
[data-ha-tools-export] input[type=checkbox] { width:24px;height:24px;flex:none;margin:10px; }
[data-ha-tools-export] button { padding:8px 12px;white-space:normal;overflow-wrap:anywhere;cursor:pointer;border:1px solid var(--divider-color,#aaa);border-radius:6px;color:inherit;background:var(--secondary-background-color,var(--card-background-color,#f5f5f5)); }
[data-ha-tools-export] :focus-visible { outline:3px solid var(--primary-color,#1769aa);outline-offset:2px; }
[data-ha-tools-export] button:disabled { cursor:default;opacity:.6; }
[data-ha-tools-export] .ht-export-actions { display:flex;flex-wrap:wrap;gap:8px;margin-top:16px; }
[data-ha-tools-export] pre { max-width:100%;min-width:0;max-height:320px;overflow:auto;white-space:pre;margin:0;padding:12px;border:1px solid var(--divider-color,#aaa);background:var(--secondary-background-color,var(--card-background-color,#f5f5f5)); }
[data-ha-tools-export] ul { padding-inline-start:22px;margin:0; }
@container (max-width:499px) { [data-ha-tools-export] .ht-export-fields { grid-template-columns:minmax(0,1fr); } }
@container (max-width:319px) { [data-ha-tools-export] [role=dialog] { padding:10px; } [data-ha-tools-export] .ht-export-actions button { flex:1 1 100%; } }
`;
  let container, nodes, L, ui, opener, alive = false, destroyed = false, serial = 0;
  let pending = null, handoff = null, ready = null, observed = null;
  const listeners = [];
  const on = (target, event, handler, capture = false) => {
    target.addEventListener(event, handler, capture);
    listeners.push(() => target.removeEventListener(event, handler, capture));
  };
  const contextKeys = ['role', 'userId', 'connectionId', 'datasetId', 'revision'];
  function readContext() {
    try {
      const value = getContext();
      if (!value || contextKeys.some(k => !['string', 'number'].includes(typeof value[k]) || String(value[k]).trim() === '' || (typeof value[k] === 'number' && !Number.isFinite(value[k])))) return null;
      return Object.freeze(Object.fromEntries([...contextKeys, 'connected', 'canExport', 'sourceConfirmed'].map(k => [k, value[k]])));
    } catch { return null; }
  }
  const same = (a, b) => !!a && !!b && [...contextKeys, 'connected', 'canExport', 'sourceConfirmed'].every(k => a[k] === b[k]);
  const available = c => !!c && c.connected === true && c.canExport === true && c.sourceConfirmed === true;
  const draft = () => Object.freeze({ filename: nodes.filename.value, format: nodes.format.value, includePrivate: nodes.private.checked });
  const sameDraft = (a, b) => a.filename === b.filename && a.format === b.format && a.includePrivate === b.includePrivate;
  function validation(value) {
    if (!formats.includes(value.format)) return 'invalidFormat';
    const name = value.filename;
    if (!name || [...name].length > 128 || name !== name.trim() || /[\p{Cc}\p{Cf}/\\<>:"|?*]/u.test(name) || /[. ]$/.test(name) || /^(con|prn|aux|nul|com[0-9¹²³]|lpt[0-9¹²³])(?:\.|$)/i.test(name)) return 'invalidName';
    if (!name.endsWith(`.${value.format}`) || name.length <= value.format.length + 1) return 'extension';
    return null;
  }
  function status(code, message) {
    if (!alive) return;
    const result = ui.status(code); // exact existing W4 labels/icons/codes
    nodes.status.className = `ht-result-state ht-result-state--${result.code}`;
    nodes.status.dataset.resultState = result.code;
    nodes.status.replaceChildren();
    const icon = doc.createElement('span'); icon.setAttribute('aria-hidden', 'true'); icon.textContent = result.icon;
    nodes.status.append(icon, doc.createTextNode(` ${result.label}`));
    nodes.message.textContent = ` ${L[message]}`;
    nodes.save.disabled = !ready || !!pending || !!handoff;
    nodes.refresh.disabled = !!pending || !!handoff;
  }
  function stop(job) {
    if (!job) return;
    if (job.timer !== null) clearTimer(job.timer);
    job.timer = null; job.controller.abort();
  }
  function discard(resetPrivate = false) {
    serial++; stop(pending); pending = null; ready = null;
    if (alive) {
      nodes.preview.textContent = ''; nodes.included.replaceChildren(); nodes.excluded.replaceChildren(); nodes['source-limit'].textContent = '';
      if (resetPrivate) nodes.private.checked = false;
    }
  }
  function contextChanged() {
    if (!alive) return;
    observed = readContext(); discard(true);
    // Once a handoff began we cannot retract a browser download. Ignore its late
    // callback and retain the prohibition on a second write until it settles.
    if (handoff) handoff.controller.abort();
    status(root.isConnected && available(observed) ? 'unconfirmed' : 'no_data', 'context');
  }
  function validContext(original) {
    const current = readContext();
    if (!root.isConnected || !same(original, current) || !available(current)) { contextChanged(); return false; }
    return true;
  }
  function changeDraft() {
    if (!alive) return;
    if (!same(observed, readContext())) { contextChanged(); return; }
    discard();
    const error = validation(draft()); status(error ? 'error' : 'not_checked', error || 'changed');
  }
  function fillList(node, items) {
    node.replaceChildren(...items.map(text => { const li = doc.createElement('li'); li.textContent = text; return li; }));
  }
  async function refresh() {
    if (!alive || pending || handoff) return;
    const current = readContext();
    if (!same(observed, current)) { observed = current; discard(true); }
    const value = draft(), error = validation(value);
    discard();
    if (error) { status('error', error); return; }
    if (!root.isConnected || !available(current)) { status(!root.isConnected || current?.connected === false ? 'no_data' : 'unconfirmed', 'unavailable'); return; }
    observed = current;
    const job = { id: serial, context: current, draft: value, started: clock(), controller: new globalThis.AbortController(), timer: null };
    pending = job; status('not_checked', 'busy');
    const timeout = () => {
      if (!alive || pending !== job) return;
      discard(); status('unconfirmed', 'timeout');
    };
    job.timer = setTimer(timeout, 10000);
    try {
      const payload = await buildPayload(Object.freeze({ context: current, draft: value, policy, signal: job.controller.signal }));
      if (!alive || pending !== job || job.id !== serial) return;
      if (clock() - job.started >= 10000) { timeout(); return; }
      if (!validContext(current)) return;
      clearTimer(job.timer); job.timer = null;
      if (!sameDraft(value, draft())) { changeDraft(); return; }
      const { serialized, confirmed, sourceConfirmed, status: resultState, included, excluded, sourceLimit } = payload || {};
      if (resultState === 'no_data' || serialized === '') { pending = null; status('no_data', 'noData'); return; }
      if (confirmed !== true || sourceConfirmed !== true || !['success', 'partial'].includes(resultState) || typeof serialized !== 'string' || !strings(included) || !strings(excluded) || typeof sourceLimit !== 'string' || !sourceLimit.trim()) {
        pending = null; status('unconfirmed', 'unconfirmed'); return;
      }
      // Freeze the only serialized bytes and build the only Blob NOW; Save never
      // re-runs serialization or builds a second payload from potentially newer data.
      ready = Object.freeze({ serialized, blob: new globalThis.Blob([serialized], { type: mime[value.format] }), context: current, draft: value });
      nodes.preview.textContent = ready.serialized;
      fillList(nodes.included, [...included]); fillList(nodes.excluded, [...excluded]);
      nodes['source-limit'].textContent = sourceLimit;
      pending = null; status(resultState, 'changed'); nodes.message.textContent = '';
    } catch {
      if (!alive || pending !== job || job.id !== serial) return;
      if (clock() - job.started >= 10000) { timeout(); return; }
      if (!validContext(current)) return;
      discard(); status('error', 'buildError');
    }
  }
  async function save() {
    if (!alive || !ready || pending || handoff) return;
    if (!validContext(ready.context)) return;
    const value = draft(), error = validation(value);
    if (error || !sameDraft(value, ready.draft) || nodes.preview.textContent !== ready.serialized) {
      discard(); status(error ? 'error' : 'unconfirmed', error || 'changed'); return;
    }
    const snapshot = ready; ready = null; // consume intent BEFORE invoking adapter
    const job = { id: serial, context: snapshot.context, controller: new globalThis.AbortController() };
    handoff = job; status('unconfirmed', 'saving');
    // choices cannot initiate another preview while the adapter result is unknown
    for (const key of ['filename', 'format', 'private']) nodes[key].disabled = true;
    try {
      const result = await performDownload(Object.freeze({ serialized: snapshot.serialized, blob: snapshot.blob, filename: snapshot.draft.filename, format: snapshot.draft.format, context: snapshot.context, signal: job.controller.signal }));
      if (!alive || handoff !== job || job.id !== serial || !validContext(job.context)) return;
      status(result?.outcome === 'saved' ? 'success' : 'unconfirmed', result?.outcome === 'saved' ? 'saved' : result?.outcome === 'initiated' ? 'initiated' : 'unknown');
    } catch {
      if (alive && handoff === job && job.id === serial && validContext(job.context)) status('unconfirmed', 'unknown');
    } finally {
      if (handoff === job) {
        handoff = null;
        if (alive) {
          for (const key of ['filename', 'format', 'private']) nodes[key].disabled = false;
          nodes.refresh.disabled = false; nodes.save.disabled = true;
          if (job.id !== serial) status('unconfirmed', 'unknown');
        }
      }
    }
  }
  function focusable() {
    return Array.from(nodes.dialog.querySelectorAll('input,select,button,[tabindex="0"]')).filter(el => !el.disabled);
  }
  function keydown(event) {
    if (!alive) return;
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); cancel(); }
    if (event.key === 'Tab') {
      const items = focusable(), first = items[0], last = items.at(-1);
      const active = nodes.dialog.getRootNode().activeElement || root.activeElement || doc.activeElement;
      if (event.shiftKey && (active === first || !nodes.dialog.contains(active))) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && (active === last || !nodes.dialog.contains(active))) { event.preventDefault(); first?.focus(); }
    }
  }
  function cancel() {
    if (!alive) return;
    discard(true); if (handoff) handoff.controller.abort();
    alive = false; listeners.splice(0).forEach(off => off()); container.remove();
    container = null; nodes = null; observed = null;
    // A pending handoff remains latched until settled: reopening never repeats it.
    if (opener?.isConnected && typeof opener.focus === 'function') opener.focus();
    opener = null;
  }
  function open({ opener: target } = {}) {
    if (destroyed) throw new Error('Export dialog destroyed');
    if (alive) return;
    const language = String(getLanguage());
    L = copy[language.startsWith('pl') ? 'pl' : 'en'];
    ui = createUiV3({ language });
    opener = target || root.activeElement || doc.activeElement;
    container = doc.createElement('div'); container.setAttribute('data-ha-tools-export', '');
    // The own shadow boundary contains CSS and local IDs; adjacent W4 status
    // elements retain their existing styling. Consumers can query this open root.
    const shadow = container.attachShadow({ mode: 'open' });
    if (root.host?.classList.contains('bento-dark')) container.classList.add('bento-dark');
    const style = doc.createElement('style'); style.textContent = ':host{display:block;min-width:0;max-width:100%;}' + css + '\n' + ui.css;
    shadow.append(style);
    const shell = doc.createElement('div'); shell.setAttribute('data-ha-tools-export', ''); shadow.append(shell);
    nodes = {};
    const make = (tag, key, parent, text) => { const node = doc.createElement(tag); if (key) { node.dataset.export = key; nodes[key] = node; } if (text) node.textContent = text; parent.append(node); return node; };
    const dialog = make('section', 'dialog', shell); dialog.setAttribute('role', 'dialog'); dialog.setAttribute('aria-modal', 'true');
    const title = make('h2', 'title', dialog, L.title);
    // Locally unique IDs, no random or global counters/persistent state.
    const prefix = `ht-export-${String(clock()).replace(/\W/g, '')}`;
    title.id = prefix + '-title'; dialog.setAttribute('aria-labelledby', title.id);
    const fields = make('div', null, dialog); fields.className = 'ht-export-fields';
    const filenameLabel = make('label', null, fields); make('span', null, filenameLabel, L.filename);
    const filename = make('input', 'filename', filenameLabel); filename.type = 'text'; filename.value = defaultFilename; filename.autocomplete = 'off';
    const formatLabel = make('label', null, fields); make('span', null, formatLabel, L.format);
    const format = make('select', 'format', formatLabel);
    formats.forEach(value => { const option = make('option', null, format, value.toUpperCase()); option.value = value; }); format.value = defaultFormat;
    const privateLabel = make('label', null, dialog); privateLabel.className = 'ht-export-private';
    const privateInput = make('input', 'private', privateLabel); privateInput.type = 'checkbox'; privateInput.checked = false; make('span', null, privateLabel, L.private);
    const statusLine = make('p', null, dialog); statusLine.setAttribute('role', 'status'); statusLine.setAttribute('aria-live', 'polite');
    make('span', 'status', statusLine); make('span', 'message', statusLine);
    for (const key of ['included', 'excluded']) { make('h3', null, dialog, L[key]); make('ul', key, dialog); }
    make('h3', null, dialog, L.source); make('p', 'source-limit', dialog);
    const previewHeading = make('h3', null, dialog, L.preview); previewHeading.id = prefix + '-preview';
    const preview = make('pre', 'preview', dialog); preview.tabIndex = 0; preview.setAttribute('aria-labelledby', previewHeading.id);
    const actions = make('div', null, dialog); actions.className = 'ht-export-actions';
    for (const key of ['refresh', 'save', 'cancel']) { const button = make('button', key, actions, L[key]); button.type = 'button'; }
    root.append(container); alive = true; observed = readContext();
    for (const [key, event] of [['filename', 'input'], ['format', 'change'], ['private', 'change']]) on(nodes[key], event, changeDraft);
    on(nodes.refresh, 'click', refresh); on(nodes.save, 'click', save); on(nodes.cancel, 'click', cancel);
    on(doc, 'keydown', keydown, true);
    on(doc, 'focusin', event => {
      if (alive && !event.composedPath().includes(nodes.dialog)) focusable()[0]?.focus();
    }, true);
    // The consumer supplies exact opener even when its shadow DOM hides activeElement.
    filename.focus();
    if (handoff) {
      for (const key of ['filename', 'format', 'private']) nodes[key].disabled = true;
      preview.focus();
      status('unconfirmed', 'saving');
    } else { status('not_checked', 'changed'); void refresh(); }
  }
  function destroy() { cancel(); destroyed = true; }
  return Object.freeze({ open, cancel, contextChanged, destroy });
}
// END HA TOOLS EXPORT V3

// BEGIN HA RENAMER REPORTS V1 source-sha256=8eb67bff7938388cd42213168e8ae56b91a355f647f5becb2d5e972fb424d7e6
// Reviewed completed-history projection. Never serialize arbitrary log/state/config.
function createRenameReports() {
  const entityId = value => typeof value === 'string' && value.length <= 255 && /^[a-z][a-z0-9_]*\.[a-z0-9_]+$/.test(value) ? value : null;
  const timestamp = row => {
    for (const key of ['completedAt', 'attemptedAt']) {
      if (typeof row[key] !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(row[key])) continue;
      const date = new Date(row[key]);
      if (Number.isFinite(date.getTime()) && date.toISOString() === row[key]) return row[key];
    }
    return typeof row.time === 'string' && /^(?:[01]?\d|2[0-3]):[0-5]\d:[0-5]\d(?: (?:AM|PM))?$/.test(row.time) ? row.time : null;
  };
  const referenceTypes = ['automations', 'scripts', 'scenes', 'dashboards'];
  function snapshot(log, includePrivate = false) {
    const rows = [], aliases = new Map();
    const alias = (value, fallback) => {
      if (!value) return fallback;
      if (!aliases.has(value)) aliases.set(value, `entity-${aliases.size + 1}`);
      return aliases.get(value);
    };
    let omitted = 0;
    for (const entry of Array.isArray(log) ? log : []) {
      if (!entry || typeof entry !== 'object' || !['ok', 'error'].includes(entry.status)
        || typeof entry.oldId !== 'string' || typeof entry.newId !== 'string') { omitted++; continue; }
      const old = entityId(entry.oldId), next = entityId(entry.targetEntityId) || entityId(entry.newId);
      const phase = ['not_started', 'attempted', 'acknowledged', 'readback_confirmed'].includes(entry.writePhase) ? entry.writePhase : null;
      // ACK is never a readback. Legacy error text cannot prove rejection before a write.
      const verified = entry.status === 'ok' && phase === 'readback_confirmed' && entry.verification === 'independent_registry_list_exact_fields';
      const status = verified ? 'applied' : entry.status === 'error' && phase === 'not_started' ? 'failed' : 'unknown';
      const reason = verified ? 'registry_readback_confirmed' : status === 'failed' ? 'preflight_rejected' : phase === 'attempted' ? 'write_outcome_unconfirmed' : entry.status === 'ok' ? 'ack_without_readback' : 'legacy_error_without_phase';
      const references = Object.fromEntries(referenceTypes.map(type => [type, Array.isArray(entry.impact?.[type]) ? entry.impact[type].length : null]));
      const dashboardCoverage = ['complete', 'partial', 'unavailable'].includes(entry.impact?.dashboardCoverage) ? entry.impact.dashboardCoverage : 'unknown';
      if (dashboardCoverage === 'unavailable' || dashboardCoverage === 'partial' && references.dashboards === 0) references.dashboards = null;
      const hasKnownReferences = Object.values(references).some(count => typeof count === 'number' && count > 0);
      rows.push({index: rows.length + 1, timestamp: timestamp(entry), timestampScope: typeof entry.completedAt === 'string' && timestamp(entry) === entry.completedAt ? 'utc_completion' : typeof entry.attemptedAt === 'string' && timestamp(entry) === entry.attemptedAt ? 'utc_attempt_intent' : timestamp(entry) ? 'local_time_date_unknown' : 'unknown',
        kind: entry.operationKind === 'device' ? 'device' : old && next ? 'entity' : 'unreviewed_identifier',
        old: includePrivate ? old : alias(old, `record-${rows.length + 1}-source`),
        new: includePrivate ? next : alias(next, `record-${rows.length + 1}-target`), status, reason,
        references, dashboardCoverage, referenceCoverage: 'known_readable_hits_only_completeness_unknown',
        manualRepair: status === 'failed' ? 'no_change_confirmed_no_repair_claim' : old && next && old !== next && hasKnownReferences ? 'verify_registry_then_review_known_references' : 'verify_registry_no_reference_coverage_claim'});
    }
    return {rows, omitted};
  }
  function build({log, format, includePrivate = false, language = 'en'}) {
    const {rows, omitted} = snapshot(log, includePrivate), pl = language === 'pl';
    if (!rows.length) return {serialized: '', status: 'no_data', confirmed: true, sourceConfirmed: true};
    const limits = pl
      ? 'Tylko ukończone wpisy lokalnej historii. ACK nie potwierdza zmiany. Czas UTC oznacza obserwowany koniec próby albo jej początkową intencję; brak obserwowanego końca pozostaje nieznany. Applied wymaga zapisanego, niezależnego odczytu rejestru ze zgodną stałą tożsamością i wszystkimi żądanymi polami. Starsze błędy bez fazy zapisu są unknown. Pochodzenie konta, pełna data starszych wpisów i kompletność odwołań nie są potwierdzone. Liczby obejmują tylko zapisane trafienia z search/related i czytelnych dashboardów Lovelace; null oznacza brak danych, 0 zapisany brak trafień. Eksport nie wykonuje odczytów ani zmian HA.'
      : 'Completed entries in local history only. ACK alone does not confirm a change. UTC time identifies observed attempt completion or attempt intent; an unobserved completion remains unknown. Applied requires recorded independent registry readback with matching stable identity and all requested fields. Legacy errors without a write phase are unknown. Account origin, full dates of legacy entries and reference completeness are unconfirmed. Counts cover recorded search/related and readable Lovelace hits only; null means unavailable, 0 recorded no hits. Export performs no HA reads or changes.';
    const instructions = pl
      ? 'Dla unknown sprawdź aktualny rejestr przed kolejną zmianą. Dla zapisanych trafień po potwierdzeniu zmiany ID ręcznie sprawdź automatyzacje, skrypty, sceny i dashboardy w HA; popraw stare odwołania i przetestuj. Failed dotyczy wyłącznie odrzuconej próby przed zapisem. Raport nie uruchamia naprawy, undo ani kolejnej zmiany nazw.'
      : 'For unknown, verify the current registry before another change. For recorded hits, after confirming an ID change manually inspect automations, scripts, scenes and dashboards in HA, update old references and test. Failed covers only rejection before a write. This report performs no repair, undo or repeat rename.';
    const included = [pl ? 'Liczby applied/failed/unknown, bezpieczny czas/status/powód, liczby znanych odwołań i ręczne kroki.' : 'Applied/failed/unknown counts, reviewed time/status/reason, known reference counts and manual steps.',
      includePrivate ? (pl ? 'Tylko ścisłe identyfikatory old/new entity_id; pozostałe identyfikatory jako null.' : 'Strict old/new entity_id only; other identifiers are null.') : (pl ? 'Powiązane pseudonimy old → new; bez prawdziwych ID.' : 'Paired old → new pseudonyms; no real IDs.')];
    const excluded = [pl ? 'Tokeny, użytkownicy, adresy, nazwy, surowe błędy/wiadomości, konfiguracja, YAML i szablony.' : 'Tokens, users, addresses, names, raw errors/messages, configuration, YAML and templates.', pl ? 'Propozycje w kolejce, niepotwierdzony sukces i automatyczne naprawy.' : 'Queued proposals, unconfirmed success and automatic repairs.'];
    const report = {schema: 'ha-entity-renamer.completed-history.v1', status: 'partial', source: 'native_completed_local_history', method: 'recorded_registry_attempts_with_optional_verified_readback',
      privateIds: includePrivate === true, counts: {total: rows.length, applied: rows.filter(row => row.status === 'applied').length, failed: rows.filter(row => row.status === 'failed').length, unknown: rows.filter(row => row.status === 'unknown').length, omitted}, included, excluded, limits, instructions, rows};
    let serialized;
    if (format === 'json') serialized = JSON.stringify(report, null, 2) + '\n';
    else if (format === 'csv') {
      const cell = value => {
        let text = value === null ? 'null' : String(value);
        if (/^[\s]*[=+\-@]/.test(text) || /^[\t\r\n]/.test(text)) text = "'" + text;
        return '"' + text.replace(/"/g, '""') + '"';
      };
      const records = [['record', 'index', 'timestamp', 'timestamp_scope', 'kind', 'old', 'new', 'status', 'reason', ...referenceTypes, 'dashboard_coverage', 'reference_coverage', 'manual_repair']];
      for (const key of ['schema', 'status', 'source', 'method', 'privateIds', 'limits', 'instructions']) records.push(['meta', key, report[key]]);
      for (const [key, value] of Object.entries(report.counts)) records.push(['count', key, value]);
      for (const key of ['included', 'excluded']) report[key].forEach(value => records.push([key, value]));
      rows.forEach(row => records.push(['row', row.index, row.timestamp, row.timestampScope, row.kind, row.old, row.new, row.status, row.reason, ...referenceTypes.map(type => row.references[type]), row.dashboardCoverage, row.referenceCoverage, row.manualRepair]));
      serialized = records.map(values => values.map(cell).join(',')).join('\r\n') + '\r\n';
    } else if (format === 'txt') {
      serialized = [report.schema, `status: ${report.status}`, `source: ${report.source}`, `method: ${report.method}`, `privateIds: ${report.privateIds}`, `counts: ${JSON.stringify(report.counts)}`, ...included.map(text => 'included: ' + text), ...excluded.map(text => 'excluded: ' + text), `limits: ${limits}`, `instructions: ${instructions}`,
        ...rows.map(row => JSON.stringify(row))].join('\n') + '\n';
    } else throw new TypeError('Unreviewed report format');
    return {serialized, confirmed: true, sourceConfirmed: true, status: 'partial', included, excluded, sourceLimit: limits};
  }
  return Object.freeze({snapshot, build});
}
// END HA RENAMER REPORTS V1
const renameReports = createRenameReports();

// Component-local persistence: never reads from or publishes a global helper.
const haToolsPersistence = {
  setHass() {},
  async save(key, data) {
    try { localStorage.setItem(`ha-tools-${key}`, JSON.stringify(data)); } catch (e) { console.debug('[ha-entity-renamer] caught:', e); }
  },
  loadSync(key) {
    try { const value = localStorage.getItem(`ha-tools-${key}`); return value ? JSON.parse(value) : null; } catch (e) { return null; }
  },
};
const _esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]);

/**
 * HA Entity Renamer – Device & Entity Rename Tool
 * Renames devices/entities and scans readable resources for references requiring manual updates.
 * Part of HA Tools suite.
 */
/* ===== HA Tools split — inline shared infrastructure ===== */
// Bento Design System CSS (inline copy — keeps tool standalone)
const HA_ENTITY_RENAMER_BENTO_CSS = `
/* ═══════════════════════════════════════════════
   HA Tools — Bento Design System v2.0 (Premium)
   ═══════════════════════════════════════════════ */

/* keyboard a11y */
:focus-visible { outline: 2px solid var(--bento-primary, #6366f1); outline-offset: 2px; border-radius: 3px; }

:host {
  /* Brand palette — diamond top, gradient-friendly */
  --bento-primary: #6366f1;
  --bento-primary-2: #8b5cf6;
  --bento-primary-3: #ec4899;
  --bento-primary-hover: #4f46e5;
  --bento-primary-light: rgba(99, 102, 241, 0.08);
  --bento-primary-glow: rgba(99, 102, 241, 0.35);
  --bento-success: #10B981;
  --bento-success-light: rgba(16, 185, 129, 0.10);
  --bento-success-border: rgba(16, 185, 129, 0.25);
  --bento-error: #EF4444;
  --bento-error-light: rgba(239, 68, 68, 0.10);
  --bento-error-border: rgba(239, 68, 68, 0.25);
  --bento-warning: #F59E0B;
  --bento-warning-light: rgba(245, 158, 11, 0.10);
  --bento-warning-border: rgba(245, 158, 11, 0.25);
  --bento-info: #06b6d4;
  --bento-info-light: rgba(6, 182, 212, 0.10);
  --bento-info-border: rgba(6, 182, 212, 0.25);

  /* Theme */
  --bento-bg:     var(--primary-background-color, #fafaf9);
  --bento-bg-2:   var(--card-background-color, #f5f5f4);
  --bento-card:   var(--card-background-color, #ffffff);
  --bento-glass:  rgba(255, 255, 255, 0.7);
  --bento-border: var(--divider-color, #e7e5e4);
  --bento-border-strong: rgba(0, 0, 0, 0.08);
  --bento-text:           var(--primary-text-color,   #0c0a09);
  --bento-text-secondary: var(--secondary-text-color, #57534e);
  --bento-text-muted:     var(--disabled-text-color,  #a8a29e);

  /* Radii */
  --bento-radius-xs: 8px;
  --bento-radius-sm: 12px;
  --bento-radius-md: 18px;
  --bento-radius-lg: 24px;
  --bento-radius-pill: 999px;

  /* Shadows — modern, layered */
  --bento-shadow-sm: 0 1px 2px rgba(0,0,0,0.04), 0 1px 3px rgba(0,0,0,0.02);
  --bento-shadow-md: 0 4px 12px rgba(0,0,0,0.05), 0 2px 6px rgba(0,0,0,0.03);
  --bento-shadow-lg: 0 24px 48px -12px rgba(0,0,0,0.10), 0 12px 24px -8px rgba(0,0,0,0.05);
  --bento-shadow-glow: 0 0 0 1px rgba(99,102,241,0.15), 0 8px 32px -8px rgba(99,102,241,0.25);

  /* Gradients */
  --bento-grad-primary: linear-gradient(135deg, #6366f1, #8b5cf6);
  --bento-grad-rainbow: linear-gradient(135deg, #6366f1 0%, #8b5cf6 50%, #ec4899 100%);
  --bento-grad-success: linear-gradient(135deg, #10b981, #34d399);
  --bento-grad-error:   linear-gradient(135deg, #ef4444, #f87171);
  --bento-grad-warning: linear-gradient(135deg, #f59e0b, #fbbf24);

  /* Motion */
  --bento-trans-fast: 0.15s cubic-bezier(0.4, 0, 0.2, 1);
  --bento-trans:      0.25s cubic-bezier(0.4, 0, 0.2, 1);
  --bento-trans-slow: 0.4s cubic-bezier(0.4, 0, 0.2, 1);

  /* Typography */
  font-family: "Inter", -apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI", system-ui, sans-serif;
  font-feature-settings: "cv11" 1, "ss01" 1;
  letter-spacing: -0.01em;
  display: block;
  color: var(--bento-text);
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

/* ── Dark mode ───────────────────────────────── */
:host(.bento-dark) {
    --bento-bg:     var(--primary-background-color, #0a0a0f);
    --bento-bg-2:   var(--card-background-color,    #111119);
    --bento-card:   var(--card-background-color,    #16161f);
    --bento-glass:  rgba(22, 22, 31, 0.7);
    --bento-border: var(--divider-color,            #27272f);
    --bento-border-strong: rgba(255, 255, 255, 0.08);
    --bento-text:           var(--primary-text-color,   #fafaf9);
    --bento-text-secondary: var(--secondary-text-color, #d6d3d1);
    --bento-text-muted:     var(--disabled-text-color,  #78716c);
    --bento-primary:        #818cf8;
    --bento-primary-2:      #a78bfa;
    --bento-primary-3:      #f472b6;
    --bento-primary-light:  rgba(129, 140, 248, 0.12);
    --bento-primary-glow:   rgba(129, 140, 248, 0.45);
    --bento-success: #34d399;
    --bento-success-light:  rgba(52, 211, 153, 0.12);
    --bento-success-border: rgba(52, 211, 153, 0.30);
    --bento-error:   #f87171;
    --bento-error-light:    rgba(248, 113, 113, 0.12);
    --bento-error-border:   rgba(248, 113, 113, 0.30);
    --bento-warning: #fbbf24;
    --bento-warning-light:  rgba(251, 191, 36, 0.12);
    --bento-warning-border: rgba(251, 191, 36, 0.30);
    --bento-info:    #22d3ee;
    --bento-info-light:     rgba(34, 211, 238, 0.12);
    --bento-info-border:    rgba(34, 211, 238, 0.30);
    --bento-shadow-sm: 0 1px 2px rgba(0,0,0,0.4);
    --bento-shadow-md: 0 4px 12px rgba(0,0,0,0.4), 0 2px 6px rgba(0,0,0,0.2);
    --bento-shadow-lg: 0 24px 48px -12px rgba(0,0,0,0.6), 0 12px 24px -8px rgba(0,0,0,0.3);
    --bento-shadow-glow: 0 0 0 1px rgba(129,140,248,0.2), 0 8px 32px -8px rgba(129,140,248,0.5);
    --bento-grad-primary: linear-gradient(135deg, #818cf8, #a78bfa);
    --bento-grad-rainbow: linear-gradient(135deg, #818cf8, #a78bfa 50%, #f472b6);
    color-scheme: dark !important;
  }
:host(.bento-dark) .card, :host(.bento-dark) .card-container, :host(.bento-dark) .main-card, :host(.bento-dark) .panel-card {
    background: var(--bento-card) !important; color: var(--bento-text) !important; border-color: var(--bento-border) !important;
  }
:host(.bento-dark) input, :host(.bento-dark) select, :host(.bento-dark) textarea { background: var(--bento-bg-2); color: var(--bento-text); border-color: var(--bento-border); }
:host(.bento-dark) table th { background: var(--bento-bg-2); color: var(--bento-text-secondary); border-color: var(--bento-border); }
:host(.bento-dark) table td { color: var(--bento-text); border-color: var(--bento-border); }
:host(.bento-dark) pre, :host(.bento-dark) code { background: #1e1e2e !important; color: #e2e8f0 !important; }

/* ── Reset & motion preferences ──────────────── */
* { box-sizing: border-box; }
@media (prefers-reduced-motion: reduce) { * { animation-duration: 0s !important; transition-duration: 0s !important; } }

/* ── Main Card Wrapper ───────────────────────── */
.card {
  background: var(--bento-card);
  border: 1px solid var(--bento-border);
  border-radius: var(--bento-radius-md);
  box-shadow: var(--bento-shadow-md);
  color: var(--bento-text);
  font-family: "Inter", -apple-system, BlinkMacSystemFont, sans-serif;
  position: relative;
  transition: box-shadow var(--bento-trans), border-color var(--bento-trans);
}

/* ── Header ──────────────────────────────────── */
.header {
  padding: 20px 24px 0;
  display: flex; align-items: center; gap: 12px;
}
.header-icon { font-size: 24px; }
.header-title {
  font-size: 18px; font-weight: 700; letter-spacing: -0.02em;
  color: var(--bento-text);
}
.header-badge {
  margin-left: auto;
  background: var(--bento-grad-primary); color: #fff;
  font-size: 11px; padding: 4px 10px; border-radius: var(--bento-radius-pill);
  font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase;
  box-shadow: 0 4px 14px -2px var(--bento-primary-glow);
}
.content { padding: 20px 24px 24px; }

/* ── Tabs (modern pill style) ────────────────── */
.tabs, .tab-bar, .tab-nav, .tab-header {
  display: flex !important; gap: 4px !important;
  padding: 4px !important;
  background: var(--bento-bg-2) !important;
  border-radius: var(--bento-radius-pill) !important;
  margin-bottom: 20px !important;
  overflow: visible !important;
  -webkit-overflow-scrolling: touch !important;
  flex-wrap: wrap !important; border-bottom: 0 !important;
  width: 100%; max-width: 100%; box-sizing: border-box;
}
.tab, .tab-btn, .tab-button, .dtab {
  padding: 8px 16px !important;
  border: none !important; background: transparent !important; cursor: pointer !important;
  font-size: 13px !important; font-weight: 600 !important;
  font-family: "Inter", -apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI", Roboto, system-ui, sans-serif !important;
  color: var(--bento-text-secondary) !important;
  border-radius: var(--bento-radius-pill) !important;
  margin-bottom: 0 !important;
  transition: all var(--bento-trans) !important;
  white-space: nowrap !important; flex: 1 1 auto !important; text-align: center !important; min-height: 40px !important;
  letter-spacing: -0.005em !important;
}
.tab:hover, .tab-btn:hover, .tab-button:hover, .dtab:hover {
  color: var(--bento-text) !important;
  background: var(--bento-card) !important;
}
.tab.active, .tab-btn.active, .tab-button.active, .dtab.active {
  background: var(--bento-card) !important;
  color: var(--bento-primary) !important;
  box-shadow: var(--bento-shadow-sm) !important;
  font-weight: 700 !important;
}
.tab-content { display: block; }
.tab-content.active { animation: bentoFadeIn 0.35s cubic-bezier(0.4, 0, 0.2, 1); }
@keyframes bentoFadeIn {
  from { opacity: 0; transform: translateY(8px); }
  to   { opacity: 1; transform: translateY(0); }
}

/* ── Stat / KPI cards (premium) ──────────────── */
.stat-card, .stat-item, .metric-card, .kpi-card {
  background: var(--bento-bg-2) !important;
  border: 1px solid var(--bento-border) !important;
  border-radius: var(--bento-radius-sm) !important;
  padding: 18px !important;
  text-align: left !important;
  transition: transform var(--bento-trans), box-shadow var(--bento-trans), border-color var(--bento-trans);
  position: relative; overflow: hidden;
}
.stat-card::before, .metric-card::before, .kpi-card::before {
  content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 3px;
  background: var(--bento-grad-primary);
  opacity: 0; transition: opacity var(--bento-trans);
}
.stat-card:hover, .stat-item:hover, .metric-card:hover, .kpi-card:hover {
  transform: translateY(-2px); box-shadow: var(--bento-shadow-lg); border-color: var(--bento-primary-light);
}
.stat-card:hover::before, .metric-card:hover::before, .kpi-card:hover::before { opacity: 1; }
.stat-icon { font-size: 22px; margin-bottom: 6px; opacity: 0.85; }
.stat-value, .stat-val, .metric-value, .kpi-val {
  font-size: 26px; font-weight: 800; line-height: 1.1;
  letter-spacing: -0.02em; color: var(--bento-text);
  font-feature-settings: "tnum" 1;
}
.stat-label, .stat-lbl, .metric-label, .kpi-lbl {
  font-size: 11px; color: var(--bento-text-secondary);
  margin-top: 4px; text-transform: uppercase; letter-spacing: 0.06em; font-weight: 600;
}
.stat-num {
  font-size: 24px; font-weight: 800; color: var(--bento-primary);
  font-feature-settings: "tnum" 1; letter-spacing: -0.02em;
}
.stat-sub { font-size: 12px; color: var(--bento-text-muted); font-weight: 500; }

/* ── Overview grid ───────────────────────────── */
.overview-grid, .stats-grid, .summary-grid, .stat-cards, .kpi-grid, .metrics-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 12px; margin-bottom: 20px;
}

/* ── Section headers ─────────────────────────── */
.section-header, .section-title {
  display: flex; align-items: center; justify-content: space-between;
  position: relative; padding-left: 12px;
  font-size: 12px; font-weight: 700; color: var(--bento-text-secondary);
  text-transform: uppercase; letter-spacing: 0.08em;
  margin: 16px 0 10px;
}
.section-header::before, .section-title::before {
  content: ""; width: 4px; height: 4px; border-radius: 50%; background: var(--bento-primary);
  position: absolute; left: 0; top: 50%; transform: translateY(-50%); flex-shrink: 0;
}

/* ── Loading / Empty / Info ──────────────────── */
.loading-bar {
  height: 3px; border-radius: var(--bento-radius-pill);
  background: linear-gradient(90deg, var(--bento-primary), var(--bento-primary-2), transparent);
  background-size: 200% 100%;
  animation: bentoLoad 1.5s linear infinite; margin-bottom: 12px;
}
@keyframes bentoLoad { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }

.empty-state, .no-data, .no-results {
  text-align: center; color: var(--bento-text-secondary);
  padding: 40px 20px; font-size: 14px;
  background: var(--bento-bg-2); border-radius: var(--bento-radius-md);
  border: 1px dashed var(--bento-border);
}
.info-note, .tip-box {
  font-size: 13px; color: var(--bento-text-secondary);
  background: var(--bento-primary-light);
  border-radius: var(--bento-radius-sm); padding: 12px 14px;
  border-left: 3px solid var(--bento-primary); margin-top: 12px;
  line-height: 1.55;
}
.last-updated {
  font-size: 11px; color: var(--bento-text-muted);
  text-align: right; margin-top: 12px; font-feature-settings: "tnum" 1;
}

/* ── Buttons (premium) ───────────────────────── */
.refresh-btn {
  background: var(--bento-bg-2); border: 1px solid var(--bento-border);
  border-radius: var(--bento-radius-pill); padding: 6px 14px;
  font-size: 12px; color: var(--bento-text-secondary);
  cursor: pointer; font-weight: 600; transition: all var(--bento-trans);
  font-family: "Inter", -apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI", Roboto, system-ui, sans-serif;
}
.refresh-btn:hover {
  background: var(--bento-card); color: var(--bento-primary);
  border-color: var(--bento-primary); transform: translateY(-1px);
  box-shadow: var(--bento-shadow-sm);
}
.toggle-btn, .action-btn {
  background: var(--bento-grad-primary); border: none;
  border-radius: var(--bento-radius-xs); padding: 8px 16px;
  font-size: 13px; color: #fff; cursor: pointer; font-weight: 600;
  transition: all var(--bento-trans); font-family: "Inter", -apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI", Roboto, system-ui, sans-serif;
  letter-spacing: -0.005em;
  box-shadow: 0 4px 12px -2px var(--bento-primary-glow);
}
.toggle-btn:hover, .action-btn:hover {
  transform: translateY(-1px);
  box-shadow: 0 8px 20px -4px var(--bento-primary-glow);
}
.send-btn, .btn-primary {
  width: 100%;
  background: var(--bento-grad-primary); color: #fff;
  border: none; border-radius: var(--bento-radius-sm);
  padding: 12px 20px; font-size: 14px; font-weight: 700;
  cursor: pointer; font-family: "Inter", -apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI", Roboto, system-ui, sans-serif;
  letter-spacing: -0.01em;
  transition: all var(--bento-trans);
  box-shadow: 0 4px 14px -2px var(--bento-primary-glow);
}
.send-btn:hover, .btn-primary:hover {
  transform: translateY(-2px);
  box-shadow: 0 12px 28px -6px var(--bento-primary-glow);
}
.send-btn:active, .btn-primary:active { transform: translateY(0); }
.send-btn:disabled, .btn-primary:disabled {
  opacity: 0.5; cursor: not-allowed; transform: none; box-shadow: none;
}

/* ── Badges / Status (modern pill) ───────────── */
.badge, .status-badge, .tag, .chip {
  padding: 4px 12px; border-radius: var(--bento-radius-pill);
  font-size: 11px; font-weight: 700; display: inline-flex; align-items: center; gap: 5px;
  letter-spacing: 0.04em; text-transform: uppercase;
  border: 1px solid;
}
.badge-ok, .badge-success { background: var(--bento-success-light); color: var(--bento-success); border-color: var(--bento-success-border); }
.badge-er, .badge-error   { background: var(--bento-error-light);   color: var(--bento-error);   border-color: var(--bento-error-border); }
.badge-warn, .badge-warning { background: var(--bento-warning-light); color: var(--bento-warning); border-color: var(--bento-warning-border); }
.badge-info { background: var(--bento-info-light); color: var(--bento-info); border-color: var(--bento-info-border); }

.count-badge {
  font-size: 11px; font-weight: 700; padding: 3px 10px;
  border-radius: var(--bento-radius-pill); display: inline-flex; align-items: center;
  font-feature-settings: "tnum" 1;
}
.error-badge { background: var(--bento-error-light); color: var(--bento-error); border: 1px solid var(--bento-error-border); }
.warn-badge  { background: var(--bento-warning-light); color: var(--bento-warning); border: 1px solid var(--bento-warning-border); }
.info-badge  { background: var(--bento-primary-light); color: var(--bento-primary); border: 1px solid var(--bento-border); }
.ok-badge    { background: var(--bento-success-light); color: var(--bento-success); border: 1px solid var(--bento-success-border); }

/* ── Tables (modern) ─────────────────────────── */
table { width: 100%; border-collapse: separate; border-spacing: 0; }
th {
  background: var(--bento-bg-2); color: var(--bento-text-secondary);
  font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em;
  padding: 12px 16px; text-align: left;
  border-bottom: 1px solid var(--bento-border);
}
th:first-child { border-top-left-radius: var(--bento-radius-sm); }
th:last-child  { border-top-right-radius: var(--bento-radius-sm); }
td {
  padding: 14px 16px; border-bottom: 1px solid var(--bento-border);
  color: var(--bento-text); font-size: 13px;
}
tr { transition: background var(--bento-trans-fast); }
tr:hover td { background: var(--bento-primary-light); }
tr:last-child td { border-bottom: 0; }

/* ── Forms / Inputs ──────────────────────────── */
input, select, textarea {
  padding: 10px 14px; border: 1.5px solid var(--bento-border);
  border-radius: var(--bento-radius-xs);
  background: var(--bento-card); color: var(--bento-text);
  font-size: 14px; font-family: "Inter", -apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI", Roboto, system-ui, sans-serif;
  transition: all var(--bento-trans); outline: none;
  letter-spacing: -0.005em;
}
input:focus, select:focus, textarea:focus {
  border-color: var(--bento-primary);
  box-shadow: 0 0 0 4px var(--bento-primary-light);
}
input::placeholder, textarea::placeholder { color: var(--bento-text-muted); }

/* ── Code blocks ─────────────────────────────── */
code {
  background: var(--bento-bg-2); padding: 2px 6px;
  border-radius: 4px; font-size: 12px;
  font-family: "JetBrains Mono", ui-monospace, SFMono-Regular, monospace;
  border: 1px solid var(--bento-border);
}
pre {
  background: #1e1e2e; color: #e2e8f0;
  padding: 16px; border-radius: var(--bento-radius-sm);
  font-size: 12.5px; overflow-x: auto; line-height: 1.65;
  white-space: pre-wrap; word-break: break-word;
  font-family: "JetBrains Mono", ui-monospace, monospace;
  box-shadow: var(--bento-shadow-md);
}

/* ── Grid layouts ────────────────────────────── */
.schedule-grid, .send-grid {
  display: grid; grid-template-columns: 1fr 1fr; gap: 12px;
}
.schedule-card, .send-card, .info-card {
  background: var(--bento-bg-2); border: 1px solid var(--bento-border);
  border-radius: var(--bento-radius-sm); padding: 16px;
  transition: all var(--bento-trans);
}
.schedule-card:hover, .send-card:hover, .info-card:hover {
  border-color: var(--bento-primary-light); transform: translateY(-1px);
  box-shadow: var(--bento-shadow-md);
}

/* ── Log entries ─────────────────────────────── */
.log-entry {
  display: flex; flex-wrap: wrap; align-items: flex-start;
  gap: 4px 8px; padding: 10px 12px;
  border-radius: var(--bento-radius-sm); margin-bottom: 6px;
  font-size: 12.5px; min-width: 0; overflow: hidden;
  border: 1px solid transparent; transition: all var(--bento-trans-fast);
}
.error-entry { background: var(--bento-error-light); border-color: var(--bento-error-border); }
.warn-entry  { background: var(--bento-warning-light); border-color: var(--bento-warning-border); }
.log-time { color: var(--bento-text-muted); font-feature-settings: "tnum" 1; flex-shrink: 0; font-family: "JetBrains Mono", monospace; }
.log-domain {
  font-weight: 700; flex-shrink: 1; min-width: 0; max-width: 100%;
  overflow: hidden; text-overflow: ellipsis; word-break: break-all;
}
.error-domain { color: var(--bento-error); }
.warn-domain  { color: var(--bento-warning); }
.log-msg {
  color: var(--bento-text-secondary); flex-basis: 100%;
  word-break: break-word; overflow-wrap: anywhere;
  white-space: pre-wrap; min-width: 0; line-height: 1.55;
}

/* ── Send status ─────────────────────────────── */
.send-status {
  padding: 12px 16px; border-radius: var(--bento-radius-sm);
  margin-top: 14px; font-size: 13px; font-weight: 600;
  text-align: center; letter-spacing: -0.005em;
  border: 1px solid;
}
.send-status.sending { background: var(--bento-primary-light); color: var(--bento-primary); border-color: var(--bento-border); }
.send-status.success { background: var(--bento-success-light); color: var(--bento-success); border-color: var(--bento-success-border); }
.send-status.error   { background: var(--bento-error-light);   color: var(--bento-error);   border-color: var(--bento-error-border); }

/* ── Scrollbar ───────────────────────────────── */
::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--bento-border); border-radius: var(--bento-radius-pill); border: 2px solid transparent; background-clip: content-box; }
::-webkit-scrollbar-thumb:hover { background: var(--bento-text-muted); background-clip: content-box; }

/* ── Animations ──────────────────────────────── */
@keyframes bentoSpin  { to { transform: rotate(360deg); } }
@keyframes bentoPulse { 0%,100% { opacity: 1; } 50% { opacity: .5; } }
@keyframes bentoSlideIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
@keyframes bentoStaggerIn { from { opacity: 0; transform: translateY(12px) scale(0.98); } to { opacity: 1; transform: translateY(0) scale(1); } }

/* Apply stagger to grids of stat-cards */
.stats-grid > *, .overview-grid > *, .summary-grid > * {
  animation: bentoStaggerIn 0.35s cubic-bezier(0.4, 0, 0.2, 1) both;
}
.stats-grid > *:nth-child(1)  { animation-delay: 0.02s; }
.stats-grid > *:nth-child(2)  { animation-delay: 0.06s; }
.stats-grid > *:nth-child(3)  { animation-delay: 0.10s; }
.stats-grid > *:nth-child(4)  { animation-delay: 0.14s; }
.stats-grid > *:nth-child(5)  { animation-delay: 0.18s; }
.stats-grid > *:nth-child(6)  { animation-delay: 0.22s; }

/* ── Mobile — 768 px ─────────────────────────── */
@media (max-width: 768px) {
  .content { padding: 16px; }
  .header { padding: 16px 16px 0; }
  .tabs { gap: 2px !important; padding: 3px !important; }
  .tab, .tab-button, .tab-btn { padding: 6px 12px !important; font-size: 12px !important; }
  .overview-grid, .stats-grid, .summary-grid, .stat-cards, .kpi-grid, .metrics-grid {
    grid-template-columns: repeat(2, 1fr); gap: 10px;
  }
  .stat-value, .stat-val, .kpi-val, .metric-val { font-size: 22px; }
  .stat-label, .stat-lbl, .kpi-lbl, .metric-lbl { font-size: 10px; }
  .send-grid, .schedule-grid { grid-template-columns: 1fr; }
  .log-entry { flex-wrap: wrap; gap: 2px 6px; padding: 8px 10px; }
  .log-domain { max-width: 60%; font-size: 11.5px; }
  .log-msg { flex-basis: 100%; max-width: 100%; font-size: 11.5px; }
  pre { padding: 12px; font-size: 11.5px; }
  h2 { font-size: 18px; }
  h3 { font-size: 15px; }
  table { font-size: 12.5px; }
  th, td { padding: 10px 12px; }
}
@media (max-width: 480px) {
  .tabs { gap: 1px !important; padding: 2px !important; }
  .tab, .tab-button, .tab-btn { padding: 5px 10px !important; font-size: 11px !important; }
  .overview-grid, .stats-grid, .summary-grid { grid-template-columns: 1fr 1fr; }
  .stat-value, .stat-val, .kpi-val { font-size: 18px; }
}
`;
// Card-owned support footer. Never mutates document or foreign cards.
const _LOCAL_INTRO_KEY = 'ha-intro-dismissed-ha-entity-renamer';
const _LOCAL_INTRO = {
  headline: "Bulk-rename HA entities + friendly names.",
  steps: ["Pick an entity and preview its new ID or friendly name.","Bulk pattern: sensor.old_* → sensor.new_*.","Review affected references and update dashboards or automations manually."]
};
const _LOCAL_INTRO_PL = {
  headline: 'Masowa zmiana identyfikatorów encji i ich nazw.',
  steps: ['Wybierz encję i ustaw nowy identyfikator.', 'Zmiana prefiksu: sensor.old_* → sensor.new_*.', 'Sprawdź i w razie potrzeby popraw odwołania w dashboardach Lovelace.']
};
const _LOCAL_SUPPORT_KEY = 'ha-entity-renamer-support-dismissed';
const _LOCAL_DONATE_HTML = '<div class="donate-section" data-source="own-card" style="margin:8px 0 0;padding:4px 0;background:none;border:0;box-shadow:none;min-height:0;display:flex;gap:8px;align-items:center;flex-wrap:wrap;flex-direction:row;justify-content:flex-start;text-align:left"><a href="https://buymeacoffee.com/macsiem" target="_blank" rel="noopener noreferrer" style="font-size:11px;color:var(--secondary-text-color,#64748b);font-weight:400;text-decoration:underline">Optional support for HA Tools</a><button type="button" class="support-dismiss" aria-label="Dismiss support link" style="margin-left:auto;padding:2px 6px;min-height:0;line-height:1;border:0;background:none;color:var(--secondary-text-color,#64748b);cursor:pointer">×</button></div>';
function _localSupportDismissed() { try { return localStorage.getItem(_LOCAL_SUPPORT_KEY) === '1'; } catch (_) { return false; } }
function _bindLocalSupportDismiss(root) { root.querySelector('.support-dismiss')?.addEventListener('click', () => { try { localStorage.setItem(_LOCAL_SUPPORT_KEY, '1'); } catch (_) {} root.querySelector('.donate-section[data-source="own-card"]')?.remove(); }); }
function _localIntroDismissed() {
  try { return localStorage.getItem(_LOCAL_INTRO_KEY) === '1'; } catch(e) { return false; }
}
function _renderLocalIntro(language = 'en') {
  if (_localIntroDismissed()) return '';
  const intro = language === 'pl' ? _LOCAL_INTRO_PL : _LOCAL_INTRO;
  const dismiss = language === 'pl' ? 'Ukryj instrukcję' : 'Dismiss instructions';
  const steps = intro.steps.map(step => '<li>' + _esc(step) + '</li>').join('');
  return '<div class="intro-banner" data-intro="ha-entity-renamer">'
    + '<button class="intro-dismiss" type="button" title="' + _esc(dismiss) + '" aria-label="' + _esc(dismiss) + '">✕</button>'
    + '<div class="intro-headline">💡 ' + _esc(intro.headline) + '</div>'
    + '<ol class="intro-steps">' + steps + '</ol>'
    + '</div>';
}
function _bindLocalIntroDismiss(root) {
  const button = root && root.querySelector('.intro-banner[data-intro="ha-entity-renamer"] .intro-dismiss');
  if (!button) return;
  button.addEventListener('click', event => {
    event.stopPropagation();
    try { localStorage.setItem(_LOCAL_INTRO_KEY, '1'); } catch(e) {}
    button.closest('.intro-banner')?.remove();
  });
}
/* ============================================================ */


// BEGIN HA TOOLS FORMAT V4 source-sha256=a2f51d3a0dde1a000934d8d1c4f115a40dec00c0726e2ae35bdfd3bcd240f71c
// Localized presentation; raw values remain available to callers for details/export.
function createFormatUi(language = 'en', timeZone = 'UTC') {
  const numbers = new Intl.NumberFormat(language);
  const plurals = new Intl.PluralRules(language);
  const count = (value, forms) => `${numbers.format(value)} ${forms[plurals.select(value)] || forms.other}`;
  const dateTime = value => {
    if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T/.test(value)) return null;
    const time = Date.parse(value);
    if (!Number.isFinite(time)) return null;
    try { return new Intl.DateTimeFormat(language, {timeZone, dateStyle:'medium', timeStyle:'short'}).format(new Date(time)); }
    catch (_) { return null; }
  };
  return {number:value=>numbers.format(value), count, dateTime};
}
// END HA TOOLS FORMAT V4

// BEGIN HA TOOLS LISTS V4 source-sha256=f669abcd1c151f99ff2bee1ba93ba508aef1ce93b1fcb7b1cb3a91cf7973296d
// Shared list presentation. No registry writes, storage or global state.
function createListUi(language = 'en') {
  const collator = new Intl.Collator(language, {numeric:true, sensitivity:'base'});
  const compare = (a,b) => collator.compare(String(a ?? ''), String(b ?? ''));
  const createdAt = value => {
    if (typeof value === 'number') return Number.isFinite(value) && value > 0 ? value * 1000 : 0;
    if (typeof value !== 'string' || !value.trim() || /^0(?:\.0+)?$/.test(value.trim())) return 0;
    const time = Date.parse(value);
    return Number.isFinite(time) && time > 0 ? time : 0;
  };
  const isNew = (value, now = Date.now()) => {
    const time = createdAt(value);
    return time > 0 && time <= now && now - time < 7 * 86400000;
  };
  return {compare, createdAt, isNew};
}
// END HA TOOLS LISTS V4

const FAMILY_NAV='.tabs',FAMILY_ATTR='data-tab',FAMILY_ACTIVE='active';

// Adapted from Config/YAML family15: options come only from current native buttons.
// Observe the actual navigation parent's content box, not the global viewport.
function _rememberFamilyFocus(card) {
  const active = card.shadowRoot?.activeElement;
  const nav = card.shadowRoot?.querySelector(FAMILY_NAV);
  if (active?.id === 'ha-tools-tab-picker') return { picker:true };
  if (nav?.contains(active)) return { tab:active.getAttribute(FAMILY_ATTR) };
  return null;
}
function _disconnectFamilyNavigation(card) {
  card._familyNavObserver?.disconnect();card._familyNavObserver=null;card._familyNavTarget=null;
}
function _bindFamilyNavigation(card, restoreFocus=null) {
  const root=card.shadowRoot, nav=root?.querySelector(FAMILY_NAV);
  if (!nav) { _disconnectFamilyNavigation(card);return; }
  let label=root.querySelector('.ht-tab-picker');
  if (!label) {
    label=card.ownerDocument.createElement('div');label.className='ht-tab-picker';
    const text=card.ownerDocument.createElement('label');text.htmlFor='ha-tools-tab-picker';label.append(text);
    const select=card.ownerDocument.createElement('select');select.id='ha-tools-tab-picker';label.append(select);nav.before(label);
    select.addEventListener('change',()=>{
      const button=Array.from(root.querySelectorAll(FAMILY_NAV+' button['+FAMILY_ATTR+']')).find(item=>item.getAttribute(FAMILY_ATTR)===select.value&&!item.disabled&&!item.hidden);
      if(button)button.click(); // The original handler owns navigation, history and guards.
      _bindFamilyNavigation(card);
    });
  }
  label.firstElementChild.textContent=card._lang==='pl'?'Zakładka':'Tab';
  const select=label.querySelector('select');
  const buttons=Array.from(nav.querySelectorAll('button['+FAMILY_ATTR+']')).filter(button=>!button.hidden&&!button.disabled);
  const options=Array.from(select.options);
  if(options.length!==buttons.length||buttons.some((button,index)=>options[index]?.value!==button.getAttribute(FAMILY_ATTR))) {
    select.replaceChildren(...buttons.map(button=>{const option=card.ownerDocument.createElement('option');option.value=button.getAttribute(FAMILY_ATTR);return option;}));
  }
  buttons.forEach((button,index)=>{select.options[index].textContent=button.getAttribute('aria-label')||button.textContent.trim();});
  const selected=buttons.find(button=>button.classList.contains(FAMILY_ACTIVE));select.value=selected?.getAttribute(FAMILY_ATTR)||'';
  const target=nav.parentElement;
  const applyWidth=width=>{
    const currentNav=root.querySelector(FAMILY_NAV),currentLabel=root.querySelector('.ht-tab-picker');if(!currentNav||!currentLabel)return;
    const input=currentLabel.querySelector('select'),active=root.activeElement;
    const style=getComputedStyle(currentNav);
    const available=width-(parseFloat(style.paddingLeft)||0)-(parseFloat(style.paddingRight)||0);
    const narrow=available>0&&available<280,fromTab=currentNav.contains(active),fromPicker=active===input;
    currentNav.hidden=narrow;currentLabel.hidden=!narrow;
    if(narrow&&fromTab)input.focus({preventScroll:true});
    else if(!narrow&&fromPicker)currentNav.querySelector('button.'+FAMILY_ACTIVE)?.focus({preventScroll:true});
  };
  if(card._familyNavTarget!==target) {
    _disconnectFamilyNavigation(card);
    const style=getComputedStyle(target);
    const measured=target.getBoundingClientRect().width-(parseFloat(style.paddingLeft)||0)-(parseFloat(style.paddingRight)||0)-(parseFloat(style.borderLeftWidth)||0)-(parseFloat(style.borderRightWidth)||0);
    if(measured>0)card._familyNavWidth=measured;
    if(typeof ResizeObserver==='function'&&card.isConnected) {
      card._familyNavTarget=target;
      card._familyNavObserver=new ResizeObserver(entries=>{
        if(!card.isConnected||card._familyNavTarget!==target)return;
        const entry=entries.find(item=>item.target===target);if(!entry)return;
        card._familyNavWidth=entry.contentRect.width;applyWidth(card._familyNavWidth);
      });
      card._familyNavObserver.observe(target);
    }
  }
  applyWidth(card._familyNavWidth||900);
  // If ResizeObserver is unavailable, all native tabs and the complete picker stay usable.
  if(typeof ResizeObserver!=='function') { nav.hidden=false;label.hidden=false; }
  if(restoreFocus) {
    const button=buttons.find(item=>item.getAttribute(FAMILY_ATTR)===restoreFocus.tab)||selected;
    if(nav.hidden)select.focus({preventScroll:true});else button?.focus({preventScroll:true});
  }
}

function _rememberFamilyDrafts(card) {
  const root=card.shadowRoot, active=root?.activeElement;
  return Array.from(root?.querySelectorAll('input[id]:not([type=checkbox]):not([type=radio]),textarea[id]')||[],field=>({
    id:field.id,context:field.getAttribute('data-family-draft-context'),value:field.value,focused:field===active,start:field.selectionStart,end:field.selectionEnd,direction:field.selectionDirection,scrollTop:field.scrollTop,
  }));
}
function _restoreFamilyDrafts(card, fields) {
  for(const saved of fields) {
    const field=card.shadowRoot.getElementById(saved.id);if(!field||field.disabled||field.getAttribute('data-family-draft-context')!==saved.context)continue;
    field.value=saved.value;field.scrollTop=saved.scrollTop;
    if(saved.focused){field.focus({preventScroll:true});if(typeof saved.start==='number'&&typeof field.setSelectionRange==='function')field.setSelectionRange(saved.start,saved.end,saved.direction||'none');}
  }
}

const _FAMILY_CONTROL_CSS = `
/* Family15: native touch targets; checkbox indicators remain small inside labels. */
:host button,:host summary,:host a[href],:host [role=button],
:host input:not([type=checkbox]):not([type=radio]),:host select,:host textarea {
  box-sizing:border-box;min-height:44px !important;min-width:44px !important;max-width:100%;
}
:host button,:host summary,:host a[href] { white-space:normal !important;overflow-wrap:anywhere; }
:host a[href] { display:inline-flex;align-items:center; }
:host label:has(input[type=checkbox]) { min-height:44px !important;min-width:44px;display:flex;align-items:center;gap:8px;cursor:pointer; }
:host input[type=checkbox] { width:18px;height:18px;flex:none; }
:host button:focus-visible,:host summary:focus-visible,:host a:focus-visible,:host input:focus-visible,:host select:focus-visible,:host textarea:focus-visible,:host [role=button]:focus-visible {
  outline:3px solid var(--primary-color,#1769aa) !important;outline-offset:2px;
}
.intro-banner { padding-right:64px !important; }
.ht-tab-picker { min-width:0;display:flex;flex-direction:column;gap:6px;padding:8px;margin:0 0 8px; }
.ht-tab-picker select { width:100%;font:inherit;color:var(--primary-text-color,inherit);background:var(--card-background-color,transparent);padding:8px;border:1px solid var(--divider-color,#aaa);border-radius:8px; }
.ht-tab-picker[hidden],.tabs[hidden],.det-tabs[hidden] { display:none !important; }
.tabs,.det-tabs { display:flex;flex-wrap:wrap !important;overflow:visible !important;min-width:0;max-width:100%; }
:host .tabs button,:host .det-tabs button { min-height:44px !important;min-width:44px !important;white-space:normal !important;overflow-wrap:anywhere; }
.device-header { width:100%; border:0; background:transparent; color:inherit; font:inherit; text-align:left; }
`;


// Forward native key activation to the original action, without repeat or shortcut clicks.
function _bindFamilyRowKeys(root, selector) {
  root.querySelectorAll(selector).forEach(row => row.addEventListener('keydown', event => {
    if (event.target !== row || !['Enter', ' '].includes(event.key) || event.altKey || event.ctrlKey || event.metaKey) return;
    event.preventDefault();
    if (!event.repeat) row.click();
  }));
}
function _rememberFamilyRowFocus(card, kinds) {
  const active = card.shadowRoot.activeElement;
  const kind = active && kinds.find(([selector]) => active.matches(selector));
  return kind ? { selector:kind[0], attr:kind[1], key:active.getAttribute(kind[1]) } : null;
}
function _restoreFamilyRowFocus(card, saved) {
  if (!saved) return;
  Array.from(card.shadowRoot.querySelectorAll(saved.selector))
    .find(row => row.getAttribute(saved.attr) === saved.key)?.focus({preventScroll:true});
}

class HAEntityRenamer extends HTMLElement {
  static getConfigElement() { return document.createElement('ha-entity-renamer-editor'); }
  getCardSize() { return 8; }

  getGridOptions() { return { columns: 12, min_rows: 3, min_columns: 6 }; }

  static getStubConfig() { return { type: 'custom:ha-entity-renamer', title: 'Entity Renamer' }; }
  setConfig(config) { this._config = config || {}; if (this._hass) this.render(); }
  constructor() {
    super();
    this._toolId = this.tagName.toLowerCase().replace('ha-', '');
    this.attachShadow({ mode: 'open' });
    this._lang = 'en';
    this._hass = null;
    this._devices = [];
    this._entities = [];
    this._selectedDevice = null;
    this._searchQuery = '';
    this._automaticSearch = '';
    this._listFilters = {devices:'all', automatic:'all'};
    this._listSort = 'name';
    this._queueUndo = null;
    this._renameQueue = []; // {oldId, newId, newName?, entityObj}
    this._previewMode = false;
    this._deviceRenameQueue = {}; // deviceId -> newName
    this._impactResults = null;
    this._loading = false;
    this._registryRefreshPending = false;
    this._message = null;
    this._confirmDialogOpen = false;
    this._confirmation = null;
    this._entityEditor = null;
    this._applyOperation = null;
    this._readEpoch = 0;
    this._authorityEpoch = 0;
    this._historyScope = null;
    this._legacyReviewLog = [];
    this._lastApplyResult = null;
    this._activeTab = 'devices'; // devices | queue | log
    this._automaticRows = [];
    this._automaticNull = [];
    this._automaticLoaded = false;
    this._renameLog = [];
    this._exportDialog = null;
    this._exportRevision = 0;
    this._exportFingerprint = null;
    this._exportSourceArray = null;
    this._exportConnections = new WeakMap();
    this._exportConnectionSerial = 0;
    this._exportEventCleanups = [];
    this._exportBoundConnection = null;
    this._exportConnectionOnline = false;
    this._exportBrowserOnline = navigator.onLine === true;
    this._expandedDevices = new Set();
    this._loadHistoryFromStorage();
  }

  _historyKey(hass = this._hass) {
    const id = hass?.user?.id;
    return hass?.user?.is_admin === true && typeof id === 'string' && id.trim()
      ? 'entity-renamer-history-v2-' + encodeURIComponent(JSON.stringify([location.origin, id])) : null;
  }

  _loadHistoryFromStorage() {
    this._historyScope = this._historyKey();
    this._legacyReviewLog = [];
    this._renameLog = [];
    // Old history has no proven account origin. Preserve it in place, never
    // attribute it to the first administrator to open this upgraded card.
    this._legacyHistoryPresent = Array.isArray(haToolsPersistence.loadSync('entity-renamer-history'));
    if (!this._historyScope) return;
    const stored = haToolsPersistence.loadSync(this._historyScope);
    if (Array.isArray(stored)) this._renameLog = stored;
  }

  _saveHistoryToStorage(scope = this._historyKey(), log = this._renameLog) {
    if (!scope) return;
    if (log === this._renameLog && scope === this._historyKey()) {
      this._historyScope = scope;
      this._invalidateRenameExport();
    }
    // Captured original scope is also used for uncertain in-flight writes;
    // they never enter the newly authenticated account's array or storage.
    haToolsPersistence.save(scope, log);
  }

  _renameAuthorityAvailable() {
    return this.isConnected && this._hass?.user?.is_admin === true
      && !!this._historyKey() && this._hass?.connection?.connected === true
      && this._exportConnectionOnline === true && this._exportBrowserOnline === true
      && navigator.onLine === true;
  }

  _cancelRenameAuthority() {
    this._clearQueueUndo();
    if (this._loading && !this._applyOperation) this._registryRefreshPending = true;
    this._legacyReviewLog = [];
    this._authorityEpoch++;
    this._readEpoch++;
    if (this._applyOperation) this._applyOperation.cancelled = true;
    else this._loading = false;
    this._confirmation = null;
    this._confirmDialogOpen = false;
    this._entityEditor = null;
    this._lastApplyResult = null;
    this._message = null;
    this._invalidateRenameExport();
  }

  get _t() {
    const T = {
      pl: {
        deviceEntityRenamer: 'Device & Entity Renamer',
        devices: 'Urządzenia',
        entities: 'Encje',
        queue: 'Kolejka',
        log: 'Historia',
        inQueue: 'W kolejce',
        noResults: 'Brak wyników',
        searchPlaceholder: 'Szukaj urządzeń lub encji...',
        newDeviceName: 'Nowa nazwa urządzenia',
        changeName: 'Zmień nazwę',
        prefixChange: 'Zmiana prefiksu entity_id dla wszystkich encji',
        oldPrefix: 'Stary prefiks',
        newPrefix: 'Nowy prefiks',
        apply: 'Zastosuj',
        queueEmpty: 'Kolejka jest pusta.\nDodaj encje z zakładki Urządzenia.',
        devicesToRename: 'Urządzenia do zmiany nazwy:',
        clear: 'Wyczyść',
        analyzeImpact: 'Analizuj wpływ',
        executeRenames: 'Wykonaj zmiany',
        errorLoadingData: 'Błąd ładowania danych: ',
        analyzing: 'Analizuję wpływ zmian...',
        analyzing2: 'Analizuję wpływ i zmieniam nazwy...',
        renameSuccess: 'Zmieniono {ok} encji{devCount}{fail}{impact}. Sprawdź powiązania zmienionych encji.',
        editEntity: 'Zmień encję', queueEdit: 'Dodaj do kolejki', noIdChange: 'Bez zmiany entity_id',
        newEntity: 'Nowy entity_id (object_id) – zostaw bez zmian jeśli chcesz zmienić tylko friendly name:',
        newFriendly: 'Nowy friendly name (zostaw puste = bez zmian):',
        confirmRename: 'Czy na pewno chcesz zmienić nazwy {count} encji? Ta operacja jest nieodwracalna.',
        confirmRenameTitle: 'Potwierdź zmiany',
        confirmRenameBody: 'Zmiany w rejestrze encji zostaną zapisane w Home Assistant dopiero po kliknięciu „Zmień nazwy”.',
        confirmRenameCount: 'Encje do zmiany: {count}',
        confirmDeviceCount: 'Urządzenia do zmiany: {count}',
        confirmNoEntityChanges: 'Brak zmian entity_id w kolejce.',
        confirmMore: '…i jeszcze {count}',
        cancel: 'Anuluj',
        rename: 'Zmień nazwy',
        resultTitle: 'Wynik zmian',
        resultSucceeded: 'Udane zapisy: {ok} z {total}',
        resultFailures: 'Błędy:',
        noHistory: 'Brak historii zmian.\nWykonaj zmiany z zakładki Kolejka.',
        usedIn: 'Używane w:',
        notUsed: 'Nie znaleziono powiązań w dostępnych zasobach',
        deviceAdded: 'Nazwa urządzenia "{name}" dodana do kolejki.',
        automatic: 'Automatyczne ID', loadAutomatic: 'Porównaj z Recreate entity IDs',
        automaticInfo: 'Podgląd jest tylko do odczytu. HA wylicza ID według bieżącego języka i formatu. Powiązań w automatyzacjach ani dashboardach karta nie zmienia.',
        automaticUnknown: 'Brak automatycznego ID', addAllAutomatic: 'Dodaj wszystkie', addDeviceAutomatic: 'Dodaj urządzenie',
        automaticEmpty: 'Brak różnic do dodania.', automaticConflict: 'Kolizja docelowego ID',
        adminRequired: 'Zmiany rejestru wymagają konta administratora HA.',
        removeFromQueue: 'Usuń z kolejki',
      },
      en: {
        deviceEntityRenamer: 'Device & Entity Renamer',
        devices: 'Devices',
        entities: 'Entities',
        queue: 'Queue',
        log: 'Log',
        inQueue: 'In Queue',
        noResults: 'No results',
        searchPlaceholder: 'Search devices or entities...',
        newDeviceName: 'New device name',
        changeName: 'Rename',
        prefixChange: 'Change entity_id prefix for all entities',
        oldPrefix: 'Old prefix',
        newPrefix: 'New prefix',
        apply: 'Apply',
        queueEmpty: 'Queue is empty.\nAdd entities from the Devices tab.',
        devicesToRename: 'Devices to rename:',
        clear: 'Clear',
        analyzeImpact: 'Analyze Impact',
        executeRenames: 'Apply Changes',
        errorLoadingData: 'Error loading data: ',
        analyzing: 'Analyzing impact...',
        analyzing2: 'Analyzing impact and changing names...',
        renameSuccess: 'Renamed {ok} entities{devCount}{fail}{impact}. Review any affected references.',
        editEntity: 'Edit entity', queueEdit: 'Add to queue', noIdChange: 'No entity_id change',
        newEntity: 'New entity_id (object_id) – leave unchanged if you only want to change the friendly name:',
        newFriendly: 'New friendly name (leave empty = no change):',
        confirmRename: 'Are you sure you want to rename {count} entities? This operation is irreversible.',
        confirmRenameTitle: 'Confirm rename',
        confirmRenameBody: 'Entity registry changes are written to Home Assistant only after you click Rename.',
        confirmRenameCount: 'Entities to rename: {count}',
        confirmDeviceCount: 'Devices to rename: {count}',
        confirmNoEntityChanges: 'No entity_id changes queued.',
        confirmMore: '…and {count} more',
        cancel: 'Cancel',
        rename: 'Rename',
        resultTitle: 'Rename result',
        resultSucceeded: 'Succeeded writes: {ok} of {total}',
        resultFailures: 'Failed writes:',
        noHistory: 'No rename history.\nMake changes from the Queue tab.',
        usedIn: 'Used in:',
        notUsed: 'No references found in readable resources',
        deviceAdded: 'Device name "{name}" added to queue.',
        automatic: 'Automatic IDs', loadAutomatic: 'Compare with Recreate entity IDs',
        automaticInfo: 'Preview is read-only. HA calculates IDs using the current language and format. This card does not rewrite automation or dashboard references.',
        automaticUnknown: 'No automatic ID', addAllAutomatic: 'Queue all', addDeviceAutomatic: 'Queue device',
        automaticEmpty: 'No differences to queue.', automaticConflict: 'Target ID collision',
        adminRequired: 'Registry changes require a Home Assistant administrator account.',
        removeFromQueue: 'Remove from queue',
      }
    };
    return T[this._lang] || T.en;
  }

  connectedCallback() {
    this._bindRenameExportEvents();
    this._lang = (this._hass?.language || navigator.language || '').startsWith('pl') ? 'pl' : 'en';
    _bindFamilyNavigation(this);
    // HA may detach/reparent a card while its initial registry read is pending.
    // Retry only reads; a cancelled rename or export never resumes here.
    if (this._hass && !this._applyOperation && (this._loading || (!this._entities.length && !this._devices.length))) this._registryRefreshPending = true;
    this._resumePermittedRegistryRead();
  }

  _invalidateRenameExport() {
    this._exportRevision++;
    this._exportDialog?.contextChanged();
  }

  _bindRenameExportEvents() {
    const connection = this._hass?.connection;
    if (this._exportEventCleanups.length && connection === this._exportBoundConnection) return;
    this._exportEventCleanups.splice(0).forEach(off => off());
    this._exportBoundConnection = connection;
    this._exportConnectionOnline = connection?.connected === true;
    const listen = (target, event, handler) => {
      if (typeof target?.addEventListener !== 'function' || typeof target?.removeEventListener !== 'function') return;
      target.addEventListener(event, handler);
      this._exportEventCleanups.push(() => target.removeEventListener(event, handler));
    };
    listen(window, 'offline', () => { this._exportBrowserOnline = false; this._cancelRenameAuthority(); this.render(); });
    listen(window, 'online', () => { this._exportBrowserOnline = navigator.onLine === true; this._invalidateRenameExport(); this._resumePermittedRegistryRead(); });
    listen(connection, 'ready', () => { this._exportConnectionOnline = true; this._invalidateRenameExport(); this._resumePermittedRegistryRead(); });
    listen(connection, 'disconnected', () => { this._exportConnectionOnline = false; this._cancelRenameAuthority(); this.render(); });
  }

  _renameExportContext() {
    // Changes to reviewed source fields are checked at refresh AND at Save.
    // No registry, reference scan or mutation is triggered by this projection.
    const fingerprint = JSON.stringify(renameReports.snapshot(this._visibleRenameHistory(), true));
    if (this._exportSourceArray !== this._renameLog || fingerprint !== this._exportFingerprint) {
      this._exportSourceArray = this._renameLog; this._exportFingerprint = fingerprint; this._exportRevision++;
    }
    const connection = this._hass?.connection;
    const objectConnection = connection !== null && (typeof connection === 'object' || typeof connection === 'function');
    if (objectConnection && !this._exportConnections.has(connection)) this._exportConnections.set(connection, ++this._exportConnectionSerial);
    const admin = this._hass?.user?.is_admin;
    return {role: admin === true ? 'admin' : admin === false ? 'member' : 'unresolved',
      userId: typeof this._hass?.user?.id === 'string' ? this._hass.user.id : '',
      connectionId: objectConnection ? this._exportConnections.get(connection) : 'unresolved',
      datasetId: 'native-completed-local-rename-history', revision: this._exportRevision,
      connected: this.isConnected && objectConnection && connection.connected === true && this._exportConnectionOnline === true && this._exportBrowserOnline === true && navigator.onLine === true,
      canExport: admin === true && !this._loading && !this._applyOperation,
      sourceConfirmed: Array.isArray(this._renameLog)};
  }

  _visibleRenameHistory() {
    return this._legacyReviewLog?.length ? this._renameLog.concat(this._legacyReviewLog) : this._renameLog;
  }

  _reviewLegacyHistory() {
    if (!this._renameAuthorityAvailable() || this._applyOperation) return;
    if (this._legacyReviewLog.length) this._legacyReviewLog = [];
    else {
      const legacy = haToolsPersistence.loadSync('entity-renamer-history');
      this._legacyReviewLog = Array.isArray(legacy) ? legacy : [];
    }
    this._invalidateRenameExport();
    this.render();
    this.shadowRoot.getElementById('reviewLegacyHistory')?.focus();
  }

  _buildRenameReport({draft}) {
    return renameReports.build({log: this._visibleRenameHistory(), format: draft.format, includePrivate: draft.includePrivate === true, language: this._lang});
  }

  async _downloadRenameReport({blob, filename, signal, format}) {
    if (signal.aborted) return {outcome: 'unknown'};
    if (typeof window.showSaveFilePicker === 'function') {
      let writable;
      try {
        // Invoke within the Save gesture, before any asynchronous processing.
        const mime = {json: 'application/json', csv: 'text/csv', txt: 'text/plain'};
        const handle = await window.showSaveFilePicker({suggestedName: filename,
          types: [{description: format.toUpperCase(), accept: {[mime[format]]: ['.' + format]}}]});
        if (signal.aborted || handle.name.normalize('NFC') !== filename.normalize('NFC')) return {outcome: 'unknown'};
        writable = await handle.createWritable();
        if (signal.aborted) { await writable.abort(); writable = null; return {outcome: 'unknown'}; }
        await writable.write(blob);
        if (signal.aborted) { await writable.abort(); writable = null; return {outcome: 'unknown'}; }
        await writable.close(); writable = null;
        if (signal.aborted) return {outcome: 'unknown'};
        const file = await handle.getFile();
        const [expected, actual] = await Promise.all([blob.arrayBuffer(), file.arrayBuffer()]);
        const left = new Uint8Array(expected), right = new Uint8Array(actual);
        if (signal.aborted || left.length !== right.length || !left.every((byte, i) => byte === right[i])) return {outcome: 'unknown'};
        return {outcome: 'saved'};
      } catch {
        if (writable) { try { await writable.abort(); } catch {} }
        // A cancelled or uncertain save must never trigger a second download.
        return {outcome: 'unknown'};
      }
    }
    const url = URL.createObjectURL(blob), anchor = document.createElement('a');
    anchor.href = url; anchor.download = filename; anchor.hidden = true;
    try {
      document.body.append(anchor); anchor.click();
      // The browser only confirms initiating a download, never a disk write.
      return {outcome: 'initiated'};
    } finally { anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
  }

  _openRenameExport(opener) {
    this._bindRenameExportEvents();
    if (!this._exportDialog) this._exportDialog = createExportDialog({
      root: this.shadowRoot, getContext: () => this._renameExportContext(), getLanguage: () => this._lang, createUiV3,
      formats: ['json', 'csv', 'txt'], defaultFormat: 'json', defaultFilename: 'rename-history.json',
      policy: {id: 'renamer.completed-history.v1', allowlistedFields: ['counts', 'timestamp', 'status', 'reason', 'paired_pseudonyms', 'strict_entity_ids_opt_in', 'reference_type_counts', 'manual_repair_enum'],
        neverInclude: ['tokens', 'users', 'addresses', 'raw_config', 'names', 'raw_errors', 'yaml', 'templates']},
      buildPayload: request => this._buildRenameReport(request), performDownload: request => this._downloadRenameReport(request),
    });
    this._exportDialog.open({opener});
  }

  _sanitize(str) {
    if (!str) return str;
    try { return decodeURIComponent(escape(str)); } catch(e) { return str; }
  }

  // Session identity follows the tested Device Health / Network Map pattern.
  _readSession(hass = this._hass) {
    return { connection: hass?.connection, userId: hass?.user?.id, admin: hass?.user?.is_admin };
  }

  _sameReadSession(a, b) {
    return a.connection === b.connection && a.userId === b.userId && a.admin === b.admin;
  }

  _captureRead() {
    const hass = this._hass;
    const session = this._readSession(hass);
    const epoch = this._readEpoch;
    return { hass, current: () => epoch === this._readEpoch && this._sameReadSession(session, this._readSession()) };
  }

  _sourceIdentity(entry) {
    return entry && JSON.stringify([entry.id, entry.platform, entry.unique_id, entry.device_id]);
  }

  set hass(hass) {
    const previousLanguage = this._lang;
    const previousAdmin = this._hass?.user?.is_admin === true;
    const previousSession = this._lastSession || this._readSession();
    const nextSession = this._readSession(hass);
    const sessionChanged = this._hass && !this._sameReadSession(previousSession, nextSession);
    this._lastSession = nextSession;
    if (sessionChanged) {
      this._cancelRenameAuthority();
      if (!this._applyOperation) this._loading = false;
      if (this._applyOperation) this._applyOperation.cancelled = true;
      this._confirmation = null;
      this._entityEditor = null;
      this._confirmDialogOpen = false;
      this._automaticLoaded = false;
      this._automaticRows = [];
      this._automaticNull = [];
      this._impactResults = null;
      // A connection replacement alone cancels consent; account/role changes
      // additionally remove every retained private projection from this UI.
      if (previousSession.userId !== nextSession.userId || previousSession.admin !== nextSession.admin) {
        this._entities = []; this._devices = []; this._deviceEntities = {};
        this._renameQueue = []; this._deviceRenameQueue = {};
        this._renameLog = []; this._historyScope = null;
        this._expandedDevices.clear(); this._selectedDevice = null;
        this._searchQuery = ''; this._automaticSearch = ''; this._listFilters = {devices:'all',automatic:'all'}; this._prefixOld = ''; this._prefixNew = '';
        this._exportDialog?.cancel();
        this.shadowRoot.replaceChildren(); // do not carry private form drafts into the next authority
      }
    }
    try {
      var _bg = (getComputedStyle(this).getPropertyValue('--card-background-color') || getComputedStyle(this).getPropertyValue('--primary-background-color') || '').trim();
      var _d = false;
      if (_bg) {
        var _h, _r, _g, _b, _m;
        if (_bg.charAt(0) === '#') { _h = _bg.slice(1); if (_h.length === 3) _h = _h.replace(/(.)/g, '$1$1'); _r = parseInt(_h.slice(0,2),16); _g = parseInt(_h.slice(2,4),16); _b = parseInt(_h.slice(4,6),16); }
        else { _m = _bg.match(/[\d.]+/g); if (_m) { _r = +_m[0]; _g = +_m[1]; _b = +_m[2]; } }
        if (_r != null) _d = (0.2126*_r + 0.7152*_g + 0.0722*_b) / 255 < 0.5;
      } else if (hass && hass.themes) { _d = !!hass.themes.darkMode; }
      this.classList.toggle('bento-dark', _d);
    } catch (e) {}
    const first = !this._hass;
    this._hass = hass;
    if (first || (sessionChanged && (previousSession.userId !== nextSession.userId || previousSession.admin !== nextSession.admin))) this._loadHistoryFromStorage();
    this._bindRenameExportEvents();
    if (sessionChanged) this._invalidateRenameExport();
    if (hass?.language) this._lang = hass.language.startsWith('pl') ? 'pl' : 'en';
    if (previousLanguage !== this._lang) { this._invalidateRenameExport(); this._exportDialog?.cancel(); }
    if (!hass) return;
    if (first) this._init();
    else if (sessionChanged || previousAdmin !== (hass.user?.is_admin === true)) {
      const identityChanged = previousSession.userId !== nextSession.userId || previousSession.admin !== nextSession.admin;
      if (identityChanged) this._registryRefreshPending = [true, false].includes(nextSession.admin);
      this._confirmDialogOpen = false;
      this.render();
      this._resumePermittedRegistryRead();
    } else if (previousLanguage !== this._lang) this._renderLocalePreservingDrafts();
  }

  _renderLocalePreservingDrafts() {
    const fields = Array.from(this.shadowRoot.querySelectorAll('input[id],textarea[id],select[id]'), field => ({
      id: field.id, value: field.value, checked: field.checked, scrollTop: field.scrollTop,
    }));
    const active = this.shadowRoot.activeElement;
    const focus = active?.id ? { id: active.id, start: active.selectionStart, end: active.selectionEnd, direction: active.selectionDirection } : null;
    this.render();
    for (const saved of fields) {
      const field = this.shadowRoot.getElementById(saved.id);
      if (!field) continue;
      field.value = saved.value;
      if (typeof saved.checked === 'boolean') field.checked = saved.checked;
      field.scrollTop = saved.scrollTop;
    }
    const current = focus && this.shadowRoot.getElementById(focus.id);
    if (current) {
      current.focus({ preventScroll: true });
      if (typeof focus.start === 'number' && typeof current.setSelectionRange === 'function') {
        current.setSelectionRange(focus.start, focus.end, focus.direction || 'none');
      }
    }
  }

  _resumePermittedRegistryRead() {
    if (!this._registryRefreshPending || this._applyOperation || !this.isConnected
      || ![true, false].includes(this._hass?.user?.is_admin)
      || this._exportConnectionOnline !== true || this._exportBrowserOnline !== true) return;
    this._registryRefreshPending = false;
    this._init(); // Only registry reads; no rename, old consent or impact retry.
  }

  async _init() {
    const { current } = this._captureRead();
    this.render();
    await this._loadData();
    if (current()) this.render();
  }

  async _loadData() {
    // Keep authorized read-only browsing; unresolved authority cannot fetch.
    if (![true, false].includes(this._hass?.user?.is_admin)) return null;
    const { hass, current } = this._captureRead();
    let receipt = null;
    this._loading = true;
    this.render();
    try {
      const [devResult, entResult] = await Promise.all([
        hass.callWS({ type: 'config/device_registry/list' }),
        hass.callWS({ type: 'config/entity_registry/list' }),
      ]);
      if (!current()) return;
      if (!Array.isArray(devResult) || !Array.isArray(entResult)) throw new Error('Malformed registry read');
      this._devices = [...devResult].sort((a,b) => this._compareDevices(a,b));
      this._entities = entResult;
      // Build device->entities map
      this._deviceEntities = Object.create(null);
      for (const ent of this._entities) {
        const did = ent.device_id;
        if (!did) continue;
        if (!this._deviceEntities[did]) this._deviceEntities[did] = [];
        this._deviceEntities[did].push(ent);
      }
      // Sort entities within each device
      for (const did of Object.keys(this._deviceEntities)) {
        this._deviceEntities[did].sort((a, b) => this._listUi.compare(a.entity_id,b.entity_id));
      }
      receipt = {devices: devResult, entities: entResult, session: this._readSession(hass)};
    } catch (e) {
      if (!current()) return;
      this._message = { type: 'error', text: this._t.errorLoadingData + e.message };
    }
    if (current()) this._loading = false;
    return receipt;
  }

  async _loadAutomaticIds() {
    if (this._loading || ![true, false].includes(this._hass?.user?.is_admin)) return;
    const { hass, current } = this._captureRead();
    const entities = this._entities.map(entry => ({ ...entry }));
    this._loading = true;
    this._automaticLoaded = false;
    this._message = null;
    this.render();
    try {
      const ids = entities.filter(ent => ent.device_id).map(ent => ent.entity_id);
      const mapping = {};
      for (let i = 0; i < ids.length; i += 100) {
        const batch = ids.slice(i, i + 100);
        const result = await hass.callWS({ type: 'config/entity_registry/get_automatic_entity_ids', entity_ids: batch });
        if (!current()) return;
        for (const id of batch) {
          if (!Object.prototype.hasOwnProperty.call(result, id)) throw new Error('Incomplete HA automatic ID response');
          mapping[id] = result[id];
        }
      }
      const occupied = new Set(entities.map(ent => ent.entity_id));
      const seenTargets = new Set();
      const rows = [];
      const noId = [];
      for (const ent of entities.filter(entry => entry.device_id)) {
        const target = mapping[ent.entity_id];
        if (target === null) { noId.push(ent); continue; }
        if (!target || target === ent.entity_id) continue;
        const conflict = occupied.has(target) || seenTargets.has(target);
        seenTargets.add(target);
        rows.push({ oldId: ent.entity_id, newId: target, deviceId: ent.device_id, uniqueId: ent.unique_id, sourceIdentity: this._sourceIdentity(ent), conflict });
      }
      this._automaticRows = rows;
      this._automaticNull = noId;
      this._automaticLoaded = true;
    } catch (error) {
      if (!current()) return;
      this._automaticRows = [];
      this._automaticNull = [];
      this._message = { type: 'error', text: this._formatError(error) };
    } finally {
      if (current()) { this._loading = false; this.render(); }
    }
  }

  _queueAutomaticIds(deviceId = null, oldId = null) {
    if (this._loading) return;
    const focusedDevice = this.shadowRoot.activeElement?.closest('[data-auto-group]')?.dataset.autoGroup;
    const queued = new Set(this._renameQueue.map(row => row.oldId));
    for (const row of this._visibleAutomaticRows()) {
      if (row.conflict || queued.has(row.oldId)) continue;
      if (deviceId && row.deviceId !== deviceId) continue;
      if (oldId && row.oldId !== oldId) continue;
      this._renameQueue.push({ ...row, automatic: true, source: 'automatic', newName: null });
      queued.add(row.oldId);
    }
    this._confirmation = null;
    this._confirmDialogOpen = false;
    this._impactResults = null;
    this.render();
    if (focusedDevice) Array.from(this.shadowRoot.querySelectorAll('[data-auto-group]'))
      .find(group => group.dataset.autoGroup === focusedDevice)?.querySelector('summary')?.focus({preventScroll: true});
  }


  _getDeviceName(d) { return d.name_by_user || d.name || d.id; }

  get _listUi() {
    const language = this._hass?.language || this._lang;
    if (this._listUiLanguage !== language) {
      this._listUiLanguage = language;
      this._cachedListUi = createListUi(language);
    }
    return this._cachedListUi;
  }

  get _formatUi() { return createFormatUi(this._hass?.language||this._lang,this._hass?.config?.time_zone||'UTC'); }

  _registryCount(value,kind) {
    const pl=this._lang==='pl';
    const forms=kind==='device' ? (pl?{one:'urządzenie',few:'urządzenia',many:'urządzeń',other:'urządzenia'}:{one:'device',other:'devices'}) : (pl?{one:'encja',few:'encje',many:'encji',other:'encji'}:{one:'entity',other:'entities'});
    return this._formatUi.count(value,forms);
  }

  _compareDevices(a,b) {
    const natural = this._listUi.compare(this._getDeviceName(a),this._getDeviceName(b)) || this._listUi.compare(a.id,b.id);
    if (this._listSort === 'newest') return this._listUi.createdAt(b.created_at)-this._listUi.createdAt(a.created_at) || natural;
    return this._listSort === 'reverse' ? -natural : natural;
  }

  _entityMatches(entity, filter) {
    const queued = this._renameQueue.some(row=>row.oldId===entity.entity_id);
    const automatic = this._automaticRows.find(row=>row.oldId===entity.entity_id);
    if (filter === 'queued') return queued;
    if (filter === 'available') return !queued && !automatic?.conflict;
    if (filter === 'conflicts') return automatic?.conflict === true;
    if (filter === 'new') return this._listUi.isNew(entity.created_at);
    return true;
  }

  _deviceRows(device, filter=this._listFilters.devices) {
    const query = this._searchQuery.trim().toLocaleLowerCase(this._hass?.language || this._lang);
    const matchesDevice = this._getDeviceName(device).toLocaleLowerCase(this._hass?.language || this._lang).includes(query);
    return (this._deviceEntities[device.id] || []).filter(entity =>
      (matchesDevice || [entity.entity_id,entity.name,entity.original_name].some(value=>String(value||'').toLocaleLowerCase(this._hass?.language || this._lang).includes(query))) &&
      (filter === 'new' && this._listUi.isNew(device.created_at) || this._entityMatches(entity,filter))
    ).sort((a,b)=>this._listUi.compare(a.entity_id,b.entity_id));
  }

  _getFilteredDevices() {
    return this._devices.filter(device=>this._deviceRows(device).length).sort((a,b)=>this._compareDevices(a,b));
  }

  _visibleAutomaticRows(filter=this._listFilters.automatic) {
    const query = this._automaticSearch.trim().toLocaleLowerCase(this._hass?.language || this._lang);
    return this._automaticRows.filter(row=> {
      const device = this._devices.find(device=>device.id===row.deviceId) || {id:row.deviceId};
      const entity = this._entities.find(entity=>entity.entity_id===row.oldId) || {entity_id:row.oldId};
      return [this._getDeviceName(device),row.oldId,row.newId].some(value=>String(value||'').toLocaleLowerCase(this._hass?.language || this._lang).includes(query)) &&
        (filter === 'new' && this._listUi.isNew(device.created_at) || this._entityMatches(entity,filter));
    });
  }

  _visibleAutomaticNull() {
    const filter=this._listFilters.automatic;
    if (filter==='available'||filter==='conflicts') return [];
    const query=this._automaticSearch.trim().toLocaleLowerCase(this._hass?.language||this._lang);
    return this._automaticNull.filter(entity=>{
      const device=this._devices.find(d=>d.id===entity.device_id)||{id:entity.device_id||''};
      return [entity.entity_id,entity.name,this._getDeviceName(device)].some(value=>String(value||'').toLocaleLowerCase(this._hass?.language||this._lang).includes(query)) &&
        (filter==='new'&&this._listUi.isNew(device.created_at)||this._entityMatches(entity,filter));
    });
  }

  _newBadge(value) {
    return this._listUi.isNew(value) ? `<span class="new-badge">${this._lang==='pl'?'Nowe':'New'}</span>` : '';
  }

  _renderListControls(automatic=false) {
    const pl=this._lang==='pl',scope=automatic?'automatic':'devices';
    const keys=['all','available','queued','conflicts','new'];
    const labels=pl?['Wszystkie','Do dodania','W kolejce','Konflikty','Nowe']:['All','To queue','In queue','Conflicts','New'];
    const count=filter=>automatic?this._visibleAutomaticRows(filter).length:this._devices.reduce((n,d)=>n+this._deviceRows(d,filter).length,0);
    return `<div class="list-toolbar"><input type="search" id="${automatic?'automaticSearch':'searchInput'}" aria-label="${_esc(this._t.searchPlaceholder)}" placeholder="🔍 ${_esc(this._t.searchPlaceholder)}" value="${_esc(automatic?this._automaticSearch:this._searchQuery)}">
      <label>${pl?'Sortowanie':'Sort'} <select id="listSort">${[['name',pl?'Nazwa A–Z':'Name A–Z'],['reverse',pl?'Nazwa Z–A':'Name Z–A'],['newest',pl?'Najnowsze':'Newest']].map(([key,label])=>`<option value="${key}" ${this._listSort===key?'selected':''}>${label}</option>`).join('')}</select></label></div>
      <div class="list-chips" role="group" aria-label="${pl?'Filtry encji':'Entity filters'}">${keys.map((key,i)=>`<button type="button" class="btn btn-sm btn-outline" data-list-filter="${key}" data-list-scope="${scope}" aria-pressed="${this._listFilters[scope]===key}">${labels[i]} (${count(key)})</button>`).join('')}</div>
      <div class="list-expand"><button class="btn btn-sm btn-outline" data-expand-list="true">${pl?'Rozwiń wszystkie':'Expand all'}</button><button class="btn btn-sm btn-outline" data-expand-list="false">${pl?'Zwiń wszystkie':'Collapse all'}</button></div>`;
  }

  _clearQueueUndo() {
    clearTimeout(this._queueUndoTimer);
    this._queueUndoTimer = null;
    this._queueUndo = null;
  }

  _rememberQueueRemoval(value) {
    this._clearQueueUndo();
    this._queueUndo={...value,until:Date.now()+8000};
    this._queueUndoTimer=setTimeout(()=>{this._clearQueueUndo();this.shadowRoot.querySelector('.queue-undo')?.remove();},8000);
  }

  _undoQueueRemoval() {
    const undo = this._queueUndo;
    if (this._loading || !undo) return;
    if (Date.now() < undo.until) {
      if (undo.kind==='device') {
        if(!Object.hasOwn(this._deviceRenameQueue,undo.row.oldId)) {
          const entries=Object.entries(this._deviceRenameQueue);
          entries.splice(Math.min(undo.index,entries.length),0,[undo.row.oldId,undo.row.newId]);
          this._deviceRenameQueue=Object.fromEntries(entries);
        }
      } else if (!this._renameQueue.some(row=>row.oldId===undo.row.oldId)) this._renameQueue.splice(Math.min(undo.index,this._renameQueue.length),0,undo.row);
      this._confirmation=null; this._confirmDialogOpen=false; this._impactResults=null;
    }
    this._clearQueueUndo(); this.render();
  }

  _removeDeviceFromQueue(id) {
    if(this._loading || !Object.hasOwn(this._deviceRenameQueue,id)) return;
    this._rememberQueueRemoval({kind:'device',row:{oldId:id,newId:this._deviceRenameQueue[id]},index:Object.keys(this._deviceRenameQueue).indexOf(id)});
    delete this._deviceRenameQueue[id];
    this._confirmation=null;this._confirmDialogOpen=false;this._lastApplyResult=null;this._impactResults=null;
    this.render();
  }

  _queueGroups() {
    const groups=new Map();
    const group=id=>{
      if(!groups.has(id)) groups.set(id,{device:this._devices.find(d=>d.id===id)||{id,name:id|| (this._lang==='pl'?'Bez urządzenia':'No device')},rows:[]});
      return groups.get(id);
    };
    for(const [id,name] of Object.entries(this._deviceRenameQueue)) group(id).nameChange=name;
    for (const row of this._renameQueue) {
      const id=row.deviceId || row.entity?.device_id || this._entities.find(e=>e.entity_id===row.oldId)?.device_id || '';
      group(id).rows.push(row);
    }
    return [...groups.values()].sort((a,b)=>this._listUi.compare(this._getDeviceName(a.device),this._getDeviceName(b.device))||this._listUi.compare(a.device.id,b.device.id));
  }

  _findCommonPrefix(entityIds) {
    if (!entityIds.length) return '';
    // Strip domain prefix (e.g. sensor.) and find common prefix of object_ids
    const objectIds = entityIds.map(id => id.split('.')[1] || id);
    if (objectIds.length === 1) return objectIds[0];
    let prefix = objectIds[0];
    for (let i = 1; i < objectIds.length; i++) {
      while (objectIds[i].indexOf(prefix) !== 0) {
        prefix = prefix.substring(0, prefix.length - 1);
        if (!prefix) return '';
      }
    }
    // Trim trailing _ or .
    return prefix.replace(/[_.]$/, '');
  }

  _selectDevice(deviceId) {
    this._selectedDevice = deviceId === this._selectedDevice ? null : deviceId;
    this._prefixOld = '';
    this._prefixNew = '';
    if (this._selectedDevice) {
      const ents = this._deviceEntities[this._selectedDevice] || [];
      this._prefixOld = this._findCommonPrefix(ents.map(e => e.entity_id));
    }
    this.render();
  }


  _addToQueue(oldId, newId, newName, source='manual') {
    if (this._loading) return;
    // Allow adding if EITHER entity_id changed OR friendly_name/alias changed
    if (oldId === newId && !newName) return;
    this._confirmDialogOpen = false;
    this._confirmation = null;
    this._lastApplyResult = null;
    if (this._renameQueue.some(r => r.oldId === oldId)) {
      this._renameQueue = this._renameQueue.map(r => r.oldId === oldId ? { ...r, newId, automatic:false, source, ...(newName !== undefined ? { newName } : {}) } : r);
    } else {
      const ent = this._entities.find(e => e.entity_id === oldId);
      this._renameQueue.push({ oldId, newId, newName: newName || null, automatic:false, source, entity: ent, sourceIdentity: this._sourceIdentity(ent) });
    }
    this.render();
  }

  _removeFromQueue(oldId) {
    if (this._loading) return;
    this._confirmDialogOpen = false;
    this._confirmation = null;
    this._lastApplyResult = null;
    const index=this._renameQueue.findIndex(row=>row.oldId===oldId);
    if(index>=0) {
      this._rememberQueueRemoval({row:this._renameQueue[index],index});
      this._renameQueue.splice(index,1);
      this._impactResults=null;
    }
    this.render();
  }

  _clearQueue() {
    if (this._loading) return;
    this._clearQueueUndo();
    this._confirmDialogOpen = false;
    this._confirmation = null;
    this._lastApplyResult = null;
    this._renameQueue = [];
    this._deviceRenameQueue = {};
    this._impactResults = null;
    this.render();
  }

  _addPrefixRename() {
    if (this._loading) return;
    if (!this._prefixOld || !this._prefixNew || this._prefixOld === this._prefixNew) return;
    const ents = this._deviceEntities[this._selectedDevice] || [];
    for (const ent of ents) {
      const objId = ent.entity_id.split('.')[1] || '';
      if (objId.startsWith(this._prefixOld)) {
        const domain = ent.entity_id.split('.')[0];
        const newObjId = this._prefixNew + objId.substring(this._prefixOld.length);
        const newId = domain + '.' + newObjId;
        this._addToQueue(ent.entity_id, newId, undefined, 'prefix');
      }
    }
    this.render();
  }


  async _analyzeImpact() {
    if (![true, false].includes(this._hass?.user?.is_admin)) return;
    if (this._loading || !this._renameQueue.length) return;
    const { hass, current } = this._captureRead();
    const queue = this._renameQueue.map(row => ({ ...row }));
    this._loading = true;
    this._message = { type: 'info', text: this._t.analyzing };
    this.render();

    const impact = {};
    // 1. Use search/related WS API for each entity (automations, scripts, scenes, areas)
    let searchResults;
    try { searchResults = await this._loadRelatedResults(queue, hass); }
    catch (error) {
      if (!current()) return;
      this._loading = false;
      this._message = { type: 'error', text: 'Impact lookup failed: ' + this._formatError(error) };
      this.render();
      return;
    }

    // Build friendly names lookup for automations/scripts
    if (!current()) return;
    for (const { oldId, result } of searchResults) {
      const hits = { automations: [], scripts: [], dashboards: [], scenes: [] };
      // Automations
      if (result.automation) {
        for (const autoId of result.automation) {
          const state = hass.states[autoId];
          hits.automations.push(state ? state.attributes.friendly_name : autoId.replace('automation.', ''));
        }
      }
      // Scripts
      if (result.script) {
        for (const scriptId of result.script) {
          const state = hass.states[scriptId];
          hits.scripts.push(state ? state.attributes.friendly_name : scriptId.replace('script.', ''));
        }
      }
      // Scenes
      if (result.scene) {
        for (const sceneId of result.scene) {
          const state = hass.states[sceneId];
          hits.scenes.push(state ? state.attributes.friendly_name : sceneId.replace('scene.', ''));
        }
      }
      impact[oldId] = hits;
    }

    // 2. Also scan dashboards (search/related doesn't cover lovelace)
    try {
      const lovelaceConfig = await this._loadLovelaceConfigs(hass);
      const dashboardText = lovelaceConfig.map(dash => ({ title: dash.title || dash.url_path || 'default', text: JSON.stringify(dash.config || {}) }));
      if (!current()) return;
      for (const rename of queue) {
        if (!impact[rename.oldId]) continue;
        impact[rename.oldId].dashboardCoverage = lovelaceConfig.dashboardCoverage || 'unknown';
        for (const dash of dashboardText) {
          if (dash.text.includes(rename.oldId)) {
            impact[rename.oldId].dashboards.push(dash.title);
          }
        }
      }
    } catch(e) { console.debug('[ha-entity-renamer] caught:', e); }

    if (!current()) return;
    this._impactResults = impact;
    this._loading = false;
    this._message = null;
    this._activeTab = 'queue';
    this.render();
  }

  async _loadRelatedResults(rows, hass = this._hass, current = () => true) {
    const results = [];
    for (let i = 0; i < rows.length; i += 20) {
      if (!current()) return [];
      const batch = rows.slice(i, i + 20);
      const related = await Promise.all(batch.map(async row => ({
        oldId: row.oldId,
        result: await hass.callWS({ type: 'search/related', item_type: 'entity', item_id: row.oldId })
      })));
      if (!current()) return [];
      results.push(...related);
    }
    return results;
  }

  async _loadLovelaceConfigs(hass = this._hass) {
    const configs = [];
    configs.dashboardCoverage = 'unavailable';
    let dashboards;
    try {
      dashboards = await hass.callWS({ type: 'lovelace/dashboards/list' });
      if (!Array.isArray(dashboards)) return configs;
    } catch (_) { return configs; }
    let failed = 0;
    const read = async (request, meta) => {
      try {
        const config = await hass.callWS(request);
        if (!config || typeof config !== 'object' || !Array.isArray(config.views)) { failed++; return; }
        configs.push({ ...meta, config });
      } catch (_) { failed++; }
    };
    await read({ type: 'lovelace/config', force: false }, { url_path: 'default', title: 'Default' });
    for (const dash of dashboards) {
      if (!dash || typeof dash.url_path !== 'string') { failed++; continue; }
      await read({ type: 'lovelace/config', url_path: dash.url_path }, { url_path: dash.url_path, title: dash.title || dash.url_path });
    }
    configs.dashboardCoverage = configs.length === 0 ? 'unavailable' : failed > 0 ? 'partial' : 'complete';
    return configs;
  }

  _dashboardCoverageHtml(impact) {
    if (!impact || !['partial', 'unavailable'].includes(impact.dashboardCoverage)) return '';
    const partial = impact.dashboardCoverage === 'partial', pl = this._lang === 'pl';
    const text = partial ? (pl ? 'Dashboardy: częściowy odczyt. Zapisano tylko trafienia w dostępnych widokach.' : 'Dashboards: partial read. Only hits in readable views are recorded.')
      : (pl ? 'Dashboardy: odczyt niedostępny. Liczba odwołań nie jest znana.' : 'Dashboards: read unavailable. Reference count is unknown.');
    return `<p class="dashboard-coverage" style="font-size:14px;line-height:1.5;overflow-wrap:anywhere;">${_esc(text)}</p>`;
  }

  _formatError(e) {
    if (!e) return 'Unknown error';
    if (typeof e === 'string') return e;
    if (e.message) return e.message;
    try { return JSON.stringify(e); } catch(err) { return String(e); }
  }

  _confirmRenameReadback(completed, snapshot) {
    let confirmed = 0;
    for (const target of completed) {
      let matches = false;
      if (target.deviceId) matches = snapshot.devices.some(row => row?.id === target.deviceId && row.name_by_user === target.name);
      else {
        let identity;
        try { identity = JSON.parse(target.identity); } catch (_) { continue; }
        if (!Array.isArray(identity) || typeof identity[0] !== 'string' || !identity[0]) continue;
        const rows = snapshot.entities.filter(row => row?.id === identity[0]);
        matches = rows.length === 1 && rows[0].entity_id === target.entityId && this._sourceIdentity(rows[0]) === target.identity
          && (target.name === null || rows[0].name === target.name);
      }
      if (!matches || !this._renameLog.includes(target.entry)) continue;
      target.entry.writePhase = 'readback_confirmed';
      target.entry.verification = 'independent_registry_list_exact_fields';
      confirmed++;
    }
    return confirmed;
  }

  async _applyEntityRename(rename, hass = this._hass) {
    const update = { type: 'config/entity_registry/update', entity_id: rename.oldId };
    if (rename.newId && rename.newId !== rename.oldId) update.new_entity_id = rename.newId;
    if (rename.newName) update.name = rename.newName;
    // HA applies both properties to the same registry entry in one transaction.
    await hass.callWS(update);
  }

  async _revalidateAutomaticQueue(rows = this._renameQueue, hass = this._hass, isCurrent = () => true) {
    const automatic = rows.filter(row => row.automatic);
    const failures = new Map();
    if (!automatic.length) return failures;
    try {
      const current = await hass.callWS({ type: 'config/entity_registry/list' });
      if (!isCurrent()) return failures;
      const byId = new Map(current.map(row => [row.entity_id, row]));
      const ids = automatic.map(row => row.oldId);
      const targets = {};
      for (let i = 0; i < ids.length; i += 100) {
        Object.assign(targets, await hass.callWS({
          type: 'config/entity_registry/get_automatic_entity_ids', entity_ids: ids.slice(i, i + 100)
        }));
        if (!isCurrent()) return failures;
      }
      const reserved = new Set();
      for (const row of automatic) {
        const entry = byId.get(row.oldId);
        if (!entry || entry.unique_id !== row.uniqueId || entry.device_id !== row.deviceId || (row.sourceIdentity && this._sourceIdentity(entry) !== row.sourceIdentity)) {
          failures.set(row.oldId, 'Source entity changed since preview');
        } else if (targets[row.oldId] !== row.newId) {
          failures.set(row.oldId, 'Automatic ID changed since preview');
        } else if (byId.has(row.newId) || reserved.has(row.newId)) {
          failures.set(row.oldId, 'Target ID collision');
        } else {
          reserved.add(row.newId);
        }
      }
    } catch (error) {
      for (const row of automatic) failures.set(row.oldId, 'Could not revalidate automatic ID: ' + this._formatError(error));
    }
    return failures;
  }

  _showRenameConfirmation() {
    this._clearQueueUndo();
    if (!this._renameAuthorityAvailable()) {
      this._confirmDialogOpen = false;
      this._message = { type: 'error', text: this._t.adminRequired };
      this.render();
      return;
    }
    if (this._loading || (!this._renameQueue.length && !Object.keys(this._deviceRenameQueue || {}).length)) return;
    this._confirmation = {
      session: this._readSession(),
      epoch: this._authorityEpoch,
      entities: this._renameQueue.map(row => ({ ...row })),
      devices: { ...this._deviceRenameQueue },
    };
    this._confirmDialogOpen = true;
    this._lastApplyResult = null;
    this._activeTab = 'queue';
    this.render();
    this.shadowRoot.getElementById('cancelRenameDialog')?.focus();
  }

  _cancelRenameConfirmation() {
    this._confirmation = null;
    this._confirmDialogOpen = false;
    this.render();
    this.shadowRoot.getElementById('executeRenames')?.focus();
  }

  async _executeRenames(confirmed = false) {
    if (this._loading || this._applyOperation) return;
    if (!this._renameQueue.length && !Object.keys(this._deviceRenameQueue).length) return;
    if (!this._renameAuthorityAvailable()) {
      this._confirmDialogOpen = false;
      this._message = { type: 'error', text: this._t.adminRequired };
      this.render();
      return;
    }
    if (!confirmed) {
      this._showRenameConfirmation();
      return;
    }
    const confirmation = this._confirmation;
    if (!confirmation || confirmation.epoch !== this._authorityEpoch || !this._sameReadSession(confirmation.session, this._readSession())) return;
    const hass = this._hass;
    const queue = confirmation.entities;
    const devices = confirmation.devices;
    const operation = { session: confirmation.session, epoch: confirmation.epoch, cancelled: false,
      scope: this._historyKey(hass), log: this._renameLog };
    this._applyOperation = operation;
    const isCurrent = () => !operation.cancelled && this._applyOperation === operation
      && operation.epoch === this._authorityEpoch && this._renameAuthorityAvailable()
      && this._sameReadSession(operation.session, this._readSession());
    this._confirmation = null;
    this._confirmDialogOpen = false;
    this._lastApplyResult = null;
    this._loading = true;
    this._message = { type: 'info', text: this._t.analyzing2 };
    this.render();
    try {
      let automaticFailures = await this._revalidateAutomaticQueue(queue, hass, isCurrent);
      if (!isCurrent()) return;

      // Auto-run impact analysis before execution using search/related
      let impact = {};
      try {
        const searchResults = await this._loadRelatedResults(queue.filter(row => !automaticFailures.has(row.oldId)), hass, isCurrent);
        if (!isCurrent()) return;
        for (const { oldId, result } of searchResults) {
          const hits = { automations: [], scripts: [], dashboards: [], scenes: [] };
          if (result.automation) {
            for (const aId of result.automation) {
              const st = hass.states[aId];
              hits.automations.push(st ? st.attributes.friendly_name : aId.replace('automation.', ''));
            }
          }
          if (result.script) {
            for (const sId of result.script) {
              const st = hass.states[sId];
              hits.scripts.push(st ? st.attributes.friendly_name : sId.replace('script.', ''));
            }
          }
          if (result.scene) {
            for (const scId of result.scene) {
              const st = hass.states[scId];
              hits.scenes.push(st ? st.attributes.friendly_name : scId.replace('scene.', ''));
            }
          }
          impact[oldId] = hits;
        }
        // Also scan dashboards
        const lovelaceConfig = await this._loadLovelaceConfigs(hass);
        if (!isCurrent()) return;
        const dashboardText = lovelaceConfig.map(dash => ({ title: dash.title || dash.url_path || 'default', text: JSON.stringify(dash.config || {}) }));
        for (const rename of queue) {
          if (!impact[rename.oldId]) continue;
          impact[rename.oldId].dashboardCoverage = lovelaceConfig.dashboardCoverage || 'unknown';
          for (const dash of dashboardText) {
            if (dash.text.includes(rename.oldId)) {
              impact[rename.oldId].dashboards.push(dash.title);
            }
          }
        }
      } catch(e) {
        if (!isCurrent()) return;
        this._loading = false;
        this._applyOperation = null;
        this._message = { type: 'error', text: 'Impact lookup failed; no changes applied: ' + this._formatError(e) };
        this.render();
        return;
      }

      automaticFailures = await this._revalidateAutomaticQueue(queue, hass, isCurrent);
      if (!isCurrent()) return;
      const results = [];
      const historyBefore = new Set(this._renameLog);
      const completedEntries = [];
      const deviceResults = [];
      const ts = new Date().toLocaleTimeString();

      // 1. Rename devices first
      for (const [devId, newName] of Object.entries(devices)) {
        let writePhase = 'not_started';
        try {
          if (!isCurrent()) throw new Error('Account, role or connection changed; remaining changes cancelled');
          writePhase = 'attempted';
          const pendingEntry = {time: ts, oldId: '📱 ' + devId.substring(0, 8) + '...', newId: '📱 ' + newName,
            status: 'ok', impact: null, operationKind: 'device', writePhase, attemptedAt: new Date().toISOString()};
          operation.log.unshift(pendingEntry);
          this._saveHistoryToStorage(operation.scope, operation.log);
          await hass.callWS({ type: 'config/device_registry/update', device_id: devId, name_by_user: newName });
          if (!isCurrent()) return;
          operation.log.shift();
          writePhase = 'acknowledged';
          deviceResults.push({ devId, newName, status: 'ok' });
          operation.log.unshift({ time: ts, oldId: '📱 ' + devId.substring(0, 8) + '...', newId: '📱 ' + newName, status: 'ok', impact: null, operationKind: 'device', writePhase, completedAt: new Date().toISOString() });
          completedEntries.push({entry: operation.log[0], deviceId: devId, name: newName});
        } catch (e) {
          if (!isCurrent()) return;
          if (writePhase === 'attempted') operation.log.shift();
          const error = this._formatError(e);
          deviceResults.push({ devId, newName, status: 'error', error });
          operation.log.unshift({ time: ts, oldId: '📱 device', newId: '📱 ' + newName, status: 'error', error, impact: null, operationKind: 'device', writePhase, completedAt: new Date().toISOString() });
        }
        this._saveHistoryToStorage(operation.scope, operation.log);
      }

      // 2. Rename entities (entity_id + optional friendly name)
      for (const rename of queue) {
        const expectedIdentity = rename.sourceIdentity || this._sourceIdentity(rename.entity || this._entities.find(row => row.entity_id === rename.oldId));
        const imp = impact[rename.oldId] || { automations: [], scripts: [], dashboards: [] };
        let writePhase = 'not_started';
        try {
          if (automaticFailures.has(rename.oldId)) throw new Error(automaticFailures.get(rename.oldId));
          if (!isCurrent()) throw new Error('Account, role or connection changed; remaining changes cancelled');
          if (rename.automatic) {
            const latest = await this._revalidateAutomaticQueue([rename], hass, isCurrent);
            if (!isCurrent()) return;
            if (latest.has(rename.oldId)) throw new Error(latest.get(rename.oldId));
          } else if (rename.sourceIdentity) {
            const current = await hass.callWS({ type: 'config/entity_registry/list' });
            if (!isCurrent()) return;
            if (this._sourceIdentity(current.find(entry => entry.entity_id === rename.oldId)) !== rename.sourceIdentity) {
              throw new Error('Source entity changed since queueing');
            }
          }
          if (!isCurrent()) throw new Error('Account, role or connection changed; remaining changes cancelled');
          writePhase = 'attempted';
          const pendingEntry = {time: ts, oldId: rename.oldId, newId: rename.newId || rename.oldId,
            targetEntityId: rename.newId || rename.oldId, status: 'ok', impact: imp,
            operationKind: 'entity', writePhase, attemptedAt: new Date().toISOString()};
          operation.log.unshift(pendingEntry);
          this._saveHistoryToStorage(operation.scope, operation.log);
          await this._applyEntityRename(rename, hass);
          if (!isCurrent()) return;
          operation.log.shift();
          writePhase = 'acknowledged';
          results.push({ ...rename, status: 'ok' });
          operation.log.unshift({
            time: ts,
            oldId: rename.oldId,
            newId: (rename.newId !== rename.oldId ? rename.newId : rename.oldId) + (rename.newName ? ' (' + rename.newName + ')' : ''),
            status: 'ok',
            impact: imp,
            targetEntityId: rename.newId || rename.oldId, operationKind: 'entity', writePhase, completedAt: new Date().toISOString(),
          });
          completedEntries.push({entry: operation.log[0], entityId: rename.newId || rename.oldId, identity: expectedIdentity, name: rename.newName || null});
        } catch (e) {
          if (!isCurrent()) return;
          if (writePhase === 'attempted') operation.log.shift();
          const error = this._formatError(e);
          results.push({ ...rename, status: 'error', error });
          operation.log.unshift({ time: ts, oldId: rename.oldId, newId: rename.newId, status: 'error', error, impact: imp, operationKind: 'entity', writePhase, completedAt: new Date().toISOString() });
        }
        this._saveHistoryToStorage(operation.scope, operation.log);
      }

      if (!isCurrent()) return;
      const ok = results.filter(r => r.status === 'ok').length;
      const fail = results.filter(r => r.status === 'error').length;
      const devOk = deviceResults.filter(r => r.status === 'ok').length;
      const devFail = deviceResults.filter(r => r.status === 'error').length;
      const devCount = deviceResults.length;
      const totalImpact = results.filter(row => row.status === 'ok' && row.newId !== row.oldId).reduce((count, row) => {
        const hits = impact[row.oldId];
        return count + (hits ? hits.automations.length + hits.scripts.length + hits.dashboards.length + (hits.scenes || []).length : 0);
      }, 0);

      let msg = this._t.renameSuccess.replace('{ok}', ok);
      msg = msg.replace('{devCount}', devOk ? ', ' + devOk + ' ' + (this._lang === 'pl' ? 'urządzeń' : 'devices') : '');
      msg = msg.replace('{fail}', fail + devFail > 0 ? `, ${fail + devFail} ${this._lang === 'pl' ? 'błędów' : 'errors'}` : '');
      msg = msg.replace('{impact}', totalImpact > 0 ? ` ⚠️ ${totalImpact} ${this._lang === 'pl' ? 'miejsc wymaga sprawdzenia (szczegóły w historii)' : 'places require review (details in history)'}` : '');

      const deviceFailures = deviceResults.filter(r => r.status === 'error').map(r => ({
        oldId: 'device ' + r.devId,
        newId: r.newName,
        error: r.error,
      }));
      const entityFailures = results.filter(r => r.status === 'error').map(r => ({
        oldId: r.oldId,
        newId: r.newId,
        error: r.error,
      }));
      this._lastApplyResult = {
        ok: ok + devOk,
        total: results.length + deviceResults.length,
        failures: [...entityFailures, ...deviceFailures],
      };
      this._message = {
        type: fail + devFail > 0 ? 'warning' : 'success',
        text: msg,
      };
      this._renameQueue = results.filter(row => row.status === 'error').map(({status, error, ...row}) => row);
      this._deviceRenameQueue = Object.fromEntries(deviceResults.filter(row => row.status === 'error').map(row => [row.devId, row.newName]));
      this._impactResults = null;
      this._loading = false;
      this._activeTab = 'log';
      this._message = {type:'warning',outcome:'unconfirmed',text:this._lang === 'pl' ? 'Sprawdzam wyniki zmian w rejestrze.' : 'Verifying registry change outcomes.'};
      this._saveHistoryToStorage();
      const refreshed = await this._loadData();
      if (!isCurrent()) return;
      if (!operation.cancelled && refreshed && this._sameReadSession(operation.session, refreshed.session)
        && this._sameReadSession(operation.session, this._readSession()) && this._renameExportContext().connected) {
        if (this._confirmRenameReadback(completedEntries, refreshed)) this._saveHistoryToStorage();
      }
      const observed = renameReports.snapshot(this._renameLog.filter(row => !historyBefore.has(row)), true).rows;
      const applied = observed.filter(row => row.status === 'applied').length;
      const unconfirmed = observed.filter(row => row.status === 'unknown').length;
      const rejected = observed.filter(row => row.status === 'failed').length;
      const outcome = unconfirmed > 0 ? (applied > 0 || rejected > 0 ? 'partial' : 'unconfirmed')
        : rejected > 0 ? (applied > 0 ? 'partial' : 'error') : applied > 0 ? 'success' : 'no_data';
      this._lastApplyResult.ok = applied;
      this._lastApplyResult.unknown = unconfirmed;
      this._lastApplyResult.rejectedBeforeWrite = rejected;
      const reviewCount = observed.filter(row => row.status === 'applied' && row.kind === 'entity' && row.old !== row.new)
        .reduce((count,row) => count + Object.values(row.references).filter(value => typeof value === 'number').reduce((a,b) => a+b,0),0);
      const pl = this._lang === 'pl';
      let summary = pl ? `Potwierdzone zmiany: ${applied}. Niepotwierdzone: ${unconfirmed}. Odrzucone przed zapisem: ${rejected}.`
        : `Confirmed changes: ${applied}. Unconfirmed: ${unconfirmed}. Rejected before write: ${rejected}.`;
      if (unconfirmed > 0) summary += pl ? ' Sprawdź rejestr i historię przed kolejną zmianą.' : ' Review the registry and history before another change.';
      if (reviewCount > 0) summary += pl ? ` ${reviewCount} zapisanych odwołań wymaga ręcznego sprawdzenia.` : ` ${reviewCount} recorded references require manual review.`;
      this._message = {type:outcome === 'success' ? 'success' : outcome === 'error' ? 'error' : 'warning',outcome,text:summary};
      this.render();
    } finally {
      if (this._applyOperation === operation) {
        const current = isCurrent();
        this._applyOperation = null;
        this._loading = false;
        if (!current) { this._lastApplyResult = null; this._message = null; }
        this.render();
        this._resumePermittedRegistryRead();
      }
    }
  }


  render() {
    if (!this._hass) return;
    const openAutomaticDevices = new Set(Array.from(this.shadowRoot.querySelectorAll('[data-auto-group][open]'), group => group.dataset.autoGroup));
    const familyFocus=_rememberFamilyFocus(this), familyDrafts=_rememberFamilyDrafts(this);
    const familyRowFocus=_rememberFamilyRowFocus(this, [['.device-header','data-device-id']]);
    this._exportDialog?.cancel();
    const devices = this._getFilteredDevices();
    const queueCount = this._renameQueue.length + Object.keys(this._deviceRenameQueue).length;
    const t = this._t;

    this.shadowRoot.innerHTML = `
    <style>${HA_ENTITY_RENAMER_BENTO_CSS}${createUiV3({language:this._lang}).css}
/* === HA Tools split — premium banners (donate / intro / prereq) === */

/* Donation footer — diamond top */
.donate-section {  margin: 24px 0 4px; padding: 20px 24px; position: relative; overflow: hidden;  background: linear-gradient(135deg, rgba(99,102,241,0.06), rgba(236,72,153,0.06));  border: 1px solid rgba(99,102,241,0.18); border-radius: var(--bento-radius-md, 18px);  display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 18px;  font-family: 'Inter', -apple-system, sans-serif;}
.donate-section:not([data-source="own-card"])::before {  content: ''; position: absolute; top: 0; left: 0; right: 0; height: 3px;  background: linear-gradient(90deg, #6366f1, #8b5cf6, #ec4899);}
.donate-section .donate-text { flex: 1; min-width: 240px; }
.donate-section h3 {  margin: 0 0 6px; font-size: 16px; font-weight: 700; letter-spacing: -0.02em;  background: linear-gradient(135deg, #6366f1, #ec4899);  -webkit-background-clip: text; background-clip: text; color: transparent;}
.donate-section p { margin: 0; font-size: 13px; line-height: 1.55; color: var(--bento-text-secondary, #57534e); letter-spacing: -0.005em; }
.donate-buttons { display: flex; gap: 10px; flex-wrap: wrap; }
.donate-btn {  display: inline-flex; align-items: center; gap: 6px; padding: 10px 18px;  border-radius: 12px; font-weight: 700; font-size: 13px; letter-spacing: -0.005em;  text-decoration: none; transition: transform 0.2s cubic-bezier(0.4,0,0.2,1), box-shadow 0.2s, filter 0.2s;  border: 1px solid transparent;}
.donate-btn:hover { transform: translateY(-2px); filter: brightness(1.05); }
.donate-btn.coffee {  background: linear-gradient(135deg, #FFDD00, #FFC700); color: #000;  box-shadow: 0 4px 14px -2px rgba(255, 221, 0, 0.4);}
.donate-btn.coffee:hover { box-shadow: 0 8px 24px -4px rgba(255, 221, 0, 0.55); }
.donate-btn.paypal {  background: linear-gradient(135deg, #0070ba, #005ea6); color: #fff;  box-shadow: 0 4px 14px -2px rgba(0, 112, 186, 0.45);}
.donate-btn.paypal:hover { box-shadow: 0 8px 24px -4px rgba(0, 112, 186, 0.6); }
:host(.bento-dark) .donate-section { background: linear-gradient(135deg, rgba(129,140,248,0.10), rgba(244,114,182,0.10)); border-color: rgba(129,140,248,0.25); }
:host(.bento-dark) .donate-section h3 { background: linear-gradient(135deg, #a5b4fc, #f9a8d4); -webkit-background-clip: text; background-clip: text; color: transparent; }
:host(.bento-dark) .donate-section p { color: #d6d3d1; }
@media (max-width: 600px) {  .donate-section { flex-direction: column; text-align: center; padding: 18px; }  .donate-buttons { justify-content: center; width: 100%; } }

/* Prereq banner — premium */
.prereq-banner {  display: flex; align-items: flex-start; gap: 14px; padding: 16px 20px;  border-radius: var(--bento-radius-sm, 12px); margin: 0 0 16px;  font-size: 13px; line-height: 1.55; border: 1px solid;  font-family: 'Inter', sans-serif; letter-spacing: -0.005em;  position: relative; overflow: hidden;}
.prereq-banner::before {  content: ''; position: absolute; left: 0; top: 0; bottom: 0; width: 3px;}
.prereq-banner.prereq-error { background: rgba(239,68,68,0.06); border-color: rgba(239,68,68,0.25); color: #991b1b; }
.prereq-banner.prereq-error::before { background: linear-gradient(180deg, #ef4444, #f87171); }
.prereq-banner.prereq-info  { background: rgba(99,102,241,0.06); border-color: rgba(99,102,241,0.25); color: #4338ca; }
.prereq-banner.prereq-info::before  { background: linear-gradient(180deg, #6366f1, #8b5cf6); }
.prereq-banner .prereq-icon { font-size: 22px; line-height: 1; padding-top: 2px; flex-shrink: 0; }
.prereq-banner .prereq-text { flex: 1; min-width: 0; }
.prereq-banner .prereq-text strong { font-weight: 700; letter-spacing: -0.01em; }
.prereq-banner code {  background: rgba(0,0,0,0.06); padding: 1px 7px; border-radius: 5px;  font-size: 12px; font-family: 'JetBrains Mono', ui-monospace, monospace;  border: 1px solid rgba(0,0,0,0.08);}
.prereq-banner .prereq-cta {  display: inline-flex; align-items: center; padding: 8px 16px; border-radius: 10px;  background: linear-gradient(135deg, #6366f1, #8b5cf6); color: #fff !important;  text-decoration: none; font-weight: 700; font-size: 12.5px; flex-shrink: 0;  letter-spacing: -0.005em;  box-shadow: 0 4px 14px -2px rgba(99,102,241,0.45);  transition: all 0.2s cubic-bezier(0.4,0,0.2,1);}
.prereq-banner .prereq-cta:hover { transform: translateY(-1px); box-shadow: 0 8px 24px -4px rgba(99,102,241,0.6); }
:host(.bento-dark) .prereq-banner.prereq-error { background: rgba(248,113,113,0.10); border-color: rgba(248,113,113,0.30); color: #fca5a5; }
:host(.bento-dark) .prereq-banner.prereq-info { background: rgba(129,140,248,0.10); border-color: rgba(129,140,248,0.30); color: #c7d2fe; }
:host(.bento-dark) .prereq-banner code { background: rgba(255,255,255,0.06); border-color: rgba(255,255,255,0.10); }
@media (max-width: 600px) {  .prereq-banner { flex-direction: column; align-items: stretch; padding-left: 20px; }  .prereq-banner .prereq-cta { align-self: flex-start; } }

/* First-run intro banner — premium */
.intro-banner {  position: relative; padding: 18px 52px 18px 22px; margin: 0 0 18px;  background: linear-gradient(135deg, rgba(99,102,241,0.08), rgba(236,72,153,0.06));  border: 1px solid rgba(99,102,241,0.20);  border-radius: var(--bento-radius-sm, 12px);  font-size: 13px; line-height: 1.55; overflow: hidden;  font-family: 'Inter', sans-serif; letter-spacing: -0.005em;  animation: bentoSlideIn 0.4s cubic-bezier(0.4, 0, 0.2, 1);}
.intro-banner::before {  content: ''; position: absolute; top: 0; left: 0; right: 0; height: 3px;  background: linear-gradient(90deg, #6366f1, #8b5cf6, #ec4899);}
.intro-banner .intro-headline {  font-weight: 700; font-size: 14.5px; margin-bottom: 10px; letter-spacing: -0.02em;  background: linear-gradient(135deg, #6366f1, #ec4899);  -webkit-background-clip: text; background-clip: text; color: transparent;  display: flex; align-items: center; gap: 8px;}
.intro-banner .intro-steps {  margin: 8px 0 0; padding: 0; list-style: none; counter-reset: introstep;}
.intro-banner .intro-steps li {  margin-bottom: 8px; line-height: 1.55; color: var(--bento-text, #0c0a09);  padding-left: 32px; position: relative; counter-increment: introstep;  font-size: 12.5px;}
.intro-banner .intro-steps li::before {  content: counter(introstep); position: absolute; left: 0; top: -1px;  width: 22px; height: 22px; border-radius: 50%;  background: var(--bento-card, #fff); border: 1px solid rgba(99,102,241,0.25);  display: flex; align-items: center; justify-content: center;  font-size: 11px; font-weight: 800; color: #6366f1;  font-family: 'JetBrains Mono', ui-monospace, monospace;  font-feature-settings: 'tnum' 1;}
.intro-banner .intro-dismiss {  position: absolute; top: 12px; right: 14px;  background: var(--bento-card, transparent); border: 1px solid var(--bento-border, transparent);  cursor: pointer; font-size: 14px; line-height: 1;  color: var(--bento-text-secondary, #64748B);  padding: 4px 8px; border-radius: 999px;  transition: all 0.15s ease;}
.intro-banner .intro-dismiss:hover {  background: var(--bento-bg-2, #e7e5e4); color: var(--bento-text, #0c0a09);  transform: rotate(90deg);}
:host(.bento-dark) .intro-banner { background: linear-gradient(135deg, rgba(129,140,248,0.14), rgba(244,114,182,0.10)); border-color: rgba(129,140,248,0.30); }
:host(.bento-dark) .intro-banner .intro-headline { background: linear-gradient(135deg, #a5b4fc, #f9a8d4); -webkit-background-clip: text; background-clip: text; color: transparent; }
:host(.bento-dark) .intro-banner .intro-steps li { color: #fafaf9; }
:host(.bento-dark) .intro-banner .intro-steps li::before { background: #16161f; border-color: rgba(129,140,248,0.35); color: #a5b4fc; }
:host(.bento-dark) .intro-banner .intro-dismiss { background: #16161f; border-color: #27272f; color: #d6d3d1; }
:host(.bento-dark) .intro-banner .intro-dismiss:hover { background: #27272f; color: #fafaf9; }


      
/* ===== BENTO DESIGN SYSTEM (local fallback) ===== */

:host {
  --bento-primary: #3B82F6;
  --bento-primary-hover: #2563EB;
  --bento-primary-light: rgba(59, 130, 246, 0.08);
  --bento-success: #10B981;
  --bento-success-light: rgba(16, 185, 129, 0.08);
  --bento-error: #EF4444;
  --bento-error-light: rgba(239, 68, 68, 0.08);
  --bento-warning: #F59E0B;
  --bento-warning-light: rgba(245, 158, 11, 0.08);
  --bento-bg: var(--primary-background-color, #F8FAFC);
  --bento-card: var(--card-background-color, #FFFFFF);
  --bento-border: var(--divider-color, #E2E8F0);
  --bento-text: var(--primary-text-color, #1E293B);
  --bento-text-secondary: var(--secondary-text-color, #64748B);
  --bento-text-muted: var(--disabled-text-color, #94A3B8);
  --bento-radius-xs: 6px;
  --bento-radius-sm: 10px;
  --bento-radius-md: 16px;
  --bento-shadow-sm: 0 1px 3px rgba(0,0,0,0.04), 0 1px 2px rgba(0,0,0,0.06);
  --bento-shadow-md: 0 4px 12px rgba(0,0,0,0.05), 0 2px 4px rgba(0,0,0,0.04);
  --bento-shadow-lg: 0 8px 25px rgba(0,0,0,0.06), 0 4px 10px rgba(0,0,0,0.04);
  --bento-transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
}

:host { display: block; font-family: 'Inter', var(--paper-font-body1_-_font-family, sans-serif); }
      * { box-sizing: border-box; }
      .card {
        background: var(--bento-card, var(--card-background-color, #1E293B));
        border: 1px solid var(--bento-border, var(--divider-color, #334155));
        border-radius: var(--bento-radius-sm, 14px);
        padding: 24px; color: var(--bento-text, var(--primary-text-color, #E2E8F0));
        box-sizing: border-box; max-width: 100%; overflow: hidden;
      }
      h1 { margin: 0 0 4px; font-size: 22px; font-weight: 700; }
      .subtitle { color: var(--bento-text-secondary, var(--secondary-text-color, #94A3B8)); font-size: 13px; margin-bottom: 16px; }
      .msg { padding: 10px 14px; border-radius: 8px; font-size: 13px; margin-bottom: 16px; }
      .msg.info { background: var(--bento-primary-light); color: var(--bento-primary); border: 1px solid var(--bento-primary); }
      .msg.success { background: var(--bento-success-light); color: var(--bento-success); border: 1px solid var(--bento-success); }
      .msg.error { background: var(--bento-error-light); color: var(--bento-error); border: 1px solid var(--bento-error); }
      .msg.warning { background: var(--bento-warning-light); color: var(--bento-warning); border: 1px solid var(--bento-warning); }

      .rename-confirm, .apply-result {
        margin: 0 0 16px; padding: 16px; border-radius: 10px;
        border: 1px solid var(--bento-border, #334155);
        background: var(--bento-bg, rgba(59,130,246,0.06));
      }
      .rename-confirm h2 {
        margin: 0 0 4px; font-size: 16px; font-weight: 700;
      }
      .rename-confirm p {
        margin: 0; color: var(--bento-text-secondary, #94A3B8);
        font-size: 12px; line-height: 1.5;
      }
      .rename-confirm-counts {
        display: flex; flex-wrap: wrap; gap: 8px; margin: 12px 0;
      }
      .rename-confirm-counts span {
        padding: 4px 10px; border-radius: 999px;
        background: rgba(59,130,246,0.12); color: var(--bento-primary, #3B82F6);
        font-size: 11px; font-weight: 700;
      }
      .rename-preview {
        max-height: 260px; overflow-y: auto; border: 1px solid var(--bento-border, #334155);
        border-radius: 8px; background: rgba(0,0,0,0.03);
      }
      .rename-preview-row {
        display: grid; grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
        gap: 8px; padding: 8px 10px; border-bottom: 1px solid rgba(255,255,255,0.05);
        align-items: center; font-size: 11px; font-family: 'JetBrains Mono', monospace;
      }
      .rename-preview-row:last-child { border-bottom: 0; }
      .rename-preview-old, .rename-preview-new {
        min-width: 0; overflow-wrap: anywhere;
      }
      .rename-preview-old { color: #FCA5A5; }
      .rename-preview-new { color: #86EFAC; }
      .rename-preview-arrow { color: var(--bento-text-secondary, #94A3B8); }
      .rename-preview-detail {
        grid-column: 1 / -1; color: var(--bento-text, var(--primary-text-color, #E2E8F0)); font-family: inherit; font-size: 10px;
      }
      .rename-preview-more, .rename-preview-empty {
        padding: 10px; color: var(--bento-text-secondary, #94A3B8);
        font-size: 12px; text-align: center;
      }
      .rename-confirm-actions {
        display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px;
      }
      .apply-result {
        background: var(--bento-success-light, rgba(16,185,129,0.08));
        border-color: var(--bento-success, #10B981);
      }
      .apply-result.has-failures {
        background: var(--bento-warning-light, rgba(245,158,11,0.08));
        border-color: var(--bento-warning, #F59E0B);
      }
      .apply-result-title { font-size: 13px; font-weight: 700; margin-bottom: 6px; }
      .apply-result-ok { font-size: 12px; color: var(--bento-text, #E2E8F0); }
      .apply-result-failures { margin-top: 10px; }
      .apply-result-failures-title {
        font-size: 11px; font-weight: 700; color: var(--bento-warning, #F59E0B);
        text-transform: uppercase; margin-bottom: 6px;
      }
      .apply-result-failure {
        display: grid; grid-template-columns: minmax(0, 1fr); gap: 3px;
        padding: 8px 0; border-top: 1px solid rgba(255,255,255,0.06);
        font-size: 11px;
      }
      .apply-result-path {
        color: var(--bento-text, #E2E8F0); font-family: 'JetBrains Mono', monospace;
        overflow-wrap: anywhere;
      }
      .apply-result-error { color: #FCA5A5; overflow-wrap: anywhere; }

      

      .search-bar { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }
      .search-bar input {
        flex: 1; min-width: 140px; padding: 10px 14px; border-radius: 8px; font-size: 13px;
        border: 1px solid var(--bento-border, #334155);
        background: var(--bento-bg, var(--primary-background-color, #0F172A));
        color: var(--bento-text, #E2E8F0); outline: none;
        box-sizing: border-box; width: 100%; max-width: 100%;
      }
      .search-bar input:focus { border-color: var(--bento-primary, #3B82F6); }

      .stats-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(80px, 1fr)); gap: 12px; margin-bottom: 20px; }
      .stat-card .num { font-size: 26px; font-weight: 700; }
      .stat-card .label { font-size: 11px; text-transform: uppercase; color: var(--bento-text-secondary, #94A3B8); margin-top: 4px; }

      .device-list { max-height: 60vh; overflow-y: auto; }
      .device-item {
        border: 1px solid var(--bento-border, #334155); border-radius: 10px;
        margin-bottom: 8px; overflow: hidden; transition: border-color 0.15s;
      }
      .device-item.selected { border-color: var(--bento-primary, #3B82F6); }
      .device-header {
        display: flex; align-items: center; gap: 10px; padding: 12px 16px;
        cursor: pointer; user-select: none;
      }
      .device-header:hover { background: rgba(59,130,246,0.05); }
      .device-name { flex: 1; font-weight: 600; font-size: 14px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .device-meta { font-size: 11px; color: var(--bento-text-secondary, #94A3B8); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .device-expand { font-size: 12px; transition: transform 0.2s; }
      .device-expand.open { transform: rotate(90deg); }
      .entity-list { padding: 0 16px 12px; }
      .entity-row {
        display: flex; align-items: center; gap: 8px; padding: 6px 0;
        border-top: 1px solid rgba(255,255,255,0.05); font-size: 12px;
      }
      .entity-id { flex: 1; font-family: 'JetBrains Mono', 'Fira Code', monospace; color: var(--bento-text-secondary, #94A3B8); word-break: break-all; }
      .entity-name { flex: 1; min-width: 120px; }
      .entity-domain { font-size: 10px; padding: 2px 6px; border-radius: 4px; background: rgba(59,130,246,0.12); color: var(--bento-text, var(--primary-text-color, #E2E8F0)); }

      .btn {
        padding: 8px 16px; border: none; border-radius: 8px; cursor: pointer;
        font-size: 12px; font-weight: 600; font-family: inherit; transition: all 0.15s;
      }
      .btn-primary { background: var(--bento-primary, #3B82F6); color: white; }
      .btn-primary:hover { opacity: 0.85; }
      .btn-danger { background: #EF4444; color: white; }
      .btn-danger:hover { opacity: 0.85; }
      .btn-sm { padding: 4px 10px; font-size: 11px; }
      .btn-outline {
        background: transparent; border: 1px solid var(--bento-border, #334155);
        color: var(--bento-text, #E2E8F0);
      }
      .btn-outline:hover { border-color: var(--bento-primary, #3B82F6); color: var(--bento-primary, #3B82F6); }
      .btn:disabled { opacity: 0.4; cursor: not-allowed; }
      .automatic-tab .btn:disabled { opacity: 1; }

      .prefix-section {
        background: rgba(59,130,246,0.06); border: 1px solid rgba(59,130,246,0.15);
        border-radius: 10px; padding: 14px; margin: 12px 0;
      }
      .prefix-section h3 { margin: 0 0 8px; font-size: 13px; font-weight: 600; }
      .prefix-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
      .prefix-row input {
        padding: 6px 10px; border-radius: 6px; font-size: 13px; width: 180px;
        border: 1px solid var(--bento-border, #334155);
        background: var(--bento-bg, #0F172A); color: var(--bento-text, #E2E8F0);
        font-family: 'JetBrains Mono', monospace;
      }
      .prefix-row .arrow { color: var(--bento-text-secondary, #94A3B8); }

      .queue-table { width: 100%; border-collapse: collapse; font-size: 12px; }
      .queue-table th { text-align: left; padding: 8px; font-size: 11px; text-transform: uppercase; color: var(--bento-text-secondary, #94A3B8); border-bottom: 1px solid var(--bento-border, #334155); }
      .queue-table td { padding: 8px; border-bottom: 1px solid rgba(255,255,255,0.04); font-family: 'JetBrains Mono', monospace; font-size: 11px; }
      .queue-table .old { color: #FCA5A5; text-decoration: line-through; }
      .queue-table .new { color: #86EFAC; }
      .queue-table .impact { font-family: inherit; font-size: 11px; }
      .impact-badge { display: inline-block; padding: 1px 6px; border-radius: 4px; font-size: 10px; margin: 1px; }
      .impact-badge.automation { background: rgba(168,85,247,0.15); color: #C084FC; }
      .impact-badge.script { background: rgba(245,158,11,0.15); color: #FCD34D; }
      .impact-badge.dashboard { background: rgba(59,130,246,0.15); color: var(--bento-text, var(--primary-text-color, #E2E8F0)); }
      .impact-badge.scene { background: rgba(16,185,129,0.15); color: #6EE7B7; }
      .queue-actions { display: flex; gap: 8px; margin-bottom: 12px; justify-content: flex-end; }

      .log-entry { padding: 6px 0; border-bottom: 1px solid rgba(255,255,255,0.04); font-size: 12px; }
      .log-time { color: var(--bento-text-secondary, #94A3B8); font-size: 10px; }
      .log-ok { color: #86EFAC; }
      .log-err { color: #FCA5A5; }

      .empty-state { text-align: center; padding: 40px; color: var(--bento-text-secondary, #94A3B8); }
      .empty-state .icon { font-size: 40px; margin-bottom: 8px; }
      .spinner { display: inline-block; width: 16px; height: 16px; border: 2px solid rgba(255,255,255,0.2); border-top-color: var(--bento-primary, #3B82F6); border-radius: 50%; animation: spin 0.8s linear infinite; }
      @keyframes spin { to { transform: rotate(360deg); } }
    
        :host(.bento-dark) {
            --bento-bg: var(--primary-background-color, #1a1a2e);
            --bento-card: var(--card-background-color, #16213e);
            --bento-text: var(--primary-text-color, #e2e8f0);
            --bento-text-secondary: var(--secondary-text-color, #94a3b8);
            --bento-border: var(--divider-color, #334155);
            --bento-shadow-sm: 0 1px 3px rgba(0,0,0,0.3);
            --bento-shadow-md: 0 4px 12px rgba(0,0,0,0.4);
          }

        
        .tabs, .tab-bar { scrollbar-width: thin; scrollbar-color: var(--bento-border, #E2E8F0) transparent; }
        .tabs::-webkit-scrollbar, .tab-bar::-webkit-scrollbar { height: 4px; }
        .tabs::-webkit-scrollbar-track, .tab-bar::-webkit-scrollbar-track { background: transparent; }
        .tabs::-webkit-scrollbar-thumb, .tab-bar::-webkit-scrollbar-thumb { background: var(--bento-border, #E2E8F0); border-radius: 4px; }
@media (max-width: 600px) {
          .card { padding: 16px; }
          h1 { font-size: 18px; }
          .stats-row { grid-template-columns: repeat(auto-fit, minmax(70px, 1fr)); gap: 8px; }
          .stat-card .num { font-size: 20px; }
          .stat-card .label { font-size: 10px; }
          .device-header { padding: 10px 12px; gap: 8px; }
          .device-name { font-size: 13px; }
          .device-meta { font-size: 10px; }
          .entity-row { gap: 4px; padding: 4px 0; font-size: 11px; }
          .entity-id { min-width: 0; }
          .entity-name { min-width: 80px; font-size: 11px; }
          .btn { padding: 6px 12px; font-size: 11px; }
          .btn-sm { padding: 3px 8px; font-size: 10px; }
          .prefix-row { gap: 6px; }
          .prefix-row input { width: 100%; max-width: 140px; padding: 5px 8px; font-size: 12px; }
          .queue-table { font-size: 11px; }
          .queue-table th { padding: 6px; font-size: 10px; }
          .queue-table td { padding: 6px; }
          .queue-actions { flex-wrap: wrap; justify-content: center; }
          .log-entry { font-size: 11px; padding: 4px 0; }
        }
        .rename-preview-row { display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px;align-items:center;padding:10px 0;border-bottom:1px solid var(--bento-border,var(--divider-color)); }
        .automatic-row-ids { min-width:0;overflow-wrap:anywhere;font-size:12px;line-height:1.6; }
        .automatic-row-ids code { padding:0;border:0;background:transparent !important;white-space:normal;overflow-wrap:anywhere;font-size:12px; }
        .automatic-target { color:var(--bento-text-secondary); }
        .automatic-row-actions { display:flex;gap:4px;flex-wrap:wrap;justify-content:flex-end;max-width:130px; }
        .automatic-row-actions .btn { margin:0; }
        .queue-bar { position:sticky;bottom:8px;z-index:1;background:var(--bento-card,var(--card-background-color));box-shadow:var(--bento-shadow-md); }
        .list-toolbar,.list-chips,.list-expand,.queue-bar,.queue-undo { display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:12px 0;min-width:0; }
        .list-toolbar input { flex:1;min-width:120px;max-width:100%;box-sizing:border-box; }
        .list-toolbar select { max-width:100%;min-height:44px;color:inherit;background:var(--bento-card,var(--card-background-color));border:1px solid var(--bento-border,var(--divider-color));border-radius:8px;padding:8px; }
        .list-chips button[aria-pressed="true"] { color:var(--primary-color,#3b82f6);border-color:currentColor; }
        .queue-bar,.queue-undo { padding:12px;border:1px solid var(--bento-border,var(--divider-color));border-radius:10px;justify-content:space-between;overflow-wrap:anywhere; }
        .new-badge,.queued-badge,.proposal-source { font-size:12px;border-radius:6px;padding:3px 6px;border:1px solid currentColor; }
        .new-badge { display:inline-block;white-space:nowrap;color:var(--primary-color,#3b82f6);margin-left:6px; }
        .queued-badge { color:var(--success-color,#16a34a); }
        .queue-group-heading { display:flex;justify-content:space-between;gap:8px;overflow-wrap:anywhere;font-size:14px; }
        ${_FAMILY_CONTROL_CSS}
        .card { min-width:0;container-type:inline-size; }
        .prefix-row,.queue-actions { flex-wrap:wrap; }
        .device-header { min-height:44px;min-width:44px; }
        .prefix-row input,.device-detail input { min-width:44px;max-width:100%; }
        </style>
    ${_renderLocalIntro(this._lang)}
    <div class="card">
      <h1>📱️ ${_esc(typeof this._config?.title === 'string' && this._config.title.trim() ? this._config.title : t.deviceEntityRenamer)}</h1>
      <div class="subtitle">${this._registryCount(this._devices.length,'device')} • ${this._registryCount(this._entities.length,'entity')}
        </div>

      ${this._message ? `<div class="msg ${this._message.type}">${this._loading ? '<span class="spinner"></span> ' : ''}${this._message.outcome ? createUiV3({language:this._lang}).statusHtml(this._message.outcome) : ''} ${_esc(this._message.text || '')}</div>` : ''}
      ${this._renderApplyResult()}
      ${this._confirmDialogOpen ? this._renderRenameConfirmation() : ''}
      ${this._renderEntityEditor()}

      <div class="tabs">
        <button class="tab-button ${this._activeTab === 'devices' ? 'active' : ''}" data-tab="devices">📱 ${t.devices}</button>
        <button class="tab-button ${this._activeTab === 'automatic' ? 'active' : ''}" data-tab="automatic">↻ ${t.automatic}</button>
        <button class="tab-button ${this._activeTab === 'queue' ? 'active' : ''}" data-tab="queue">📋 ${t.queue}${queueCount > 0 ? ` (${queueCount})` : ''}</button>
        <button class="tab-button ${this._activeTab === 'log' ? 'active' : ''}" data-tab="log">📜 ${t.log}</button>
      </div>

      ${this._activeTab === 'devices' ? this._renderDevicesTab(devices) : ''}
      ${this._activeTab === 'automatic' ? this._renderAutomaticTab() : ''}
      ${this._activeTab === 'queue' ? this._renderQueueTab() : ''}
      ${this._activeTab === 'log' ? this._renderLogTab() : ''}
      ${this._activeTab!=='queue' && queueCount ? `<div class="queue-bar"><span>${t.inQueue}: ${queueCount}</span><button type="button" class="btn btn-primary" id="openQueue">${this._lang==='pl'?'Przejdź do kolejki →':'Go to queue →'}</button></div>`:''}
      ${this._queueUndo && Date.now()<this._queueUndo.until ? `<div class="queue-undo" role="status"><span>${this._lang==='pl'?'Usunięto z kolejki:':'Removed from queue:'} ${_esc(this._queueUndo.row.oldId)}</span><button type="button" class="btn btn-outline" id="undoQueueRemoval">${this._lang==='pl'?'Cofnij':'Undo'}</button></div>`:''}
    </div>
    ${this._hass?.user?.is_admin && this._config?.show_support !== false && !_localSupportDismissed() ? _LOCAL_DONATE_HTML : ''}`;

    _bindLocalIntroDismiss(this.shadowRoot);
    _bindLocalSupportDismiss(this.shadowRoot);
    this._attachEvents();
    for (const group of this.shadowRoot.querySelectorAll('[data-auto-group]')) group.open = openAutomaticDevices.has(group.dataset.autoGroup);
    _restoreFamilyDrafts(this,familyDrafts);
    _bindFamilyNavigation(this,familyFocus);
    _restoreFamilyRowFocus(this,familyRowFocus);
  }



  _showEntityEditor(oldId) {
    if (this._loading || this._hass?.user?.is_admin !== true) return;
    const entity = this._entities.find(e => e.entity_id === oldId);
    if (!entity) return;
    const objectId = oldId.split('.')[1] || '';
    const currentName = entity.name || entity.original_name || '';
    this._entityEditor = { oldId, objectId, currentName, objectIdDraft: objectId, nameDraft: currentName };
    this.render();
    this.shadowRoot.getElementById('entityEditObjectId')?.focus();
  }

  _renderEntityEditor() {
    if (!this._entityEditor) return '';
    const t = this._t;
    const draft = this._entityEditor;
    return `<form id="entityEditForm" aria-labelledby="entityEditHeading" style="margin:16px 0;padding:16px;border:1px solid var(--bento-border,var(--divider-color,#ddd));border-radius:12px;min-width:0;">
      <h2 id="entityEditHeading" style="font-size:16px;margin:0 0 8px;">${t.editEntity}</h2>
      <p style="overflow-wrap:anywhere;margin:0 0 12px;">${_esc(draft.oldId)}</p>
      <label for="entityEditObjectId" style="display:block;">${t.newEntity}</label>
      <input id="entityEditObjectId" data-family-draft-context="${_esc(draft.oldId)}" type="text" value="${_esc(draft.objectIdDraft)}" style="display:block;width:100%;min-width:0;box-sizing:border-box;margin:6px 0 12px;" ${this._loading ? 'disabled' : ''}>
      <label for="entityEditName" style="display:block;">${t.newFriendly}</label>
      <input id="entityEditName" data-family-draft-context="${_esc(draft.oldId)}" type="text" value="${_esc(draft.nameDraft)}" style="display:block;width:100%;min-width:0;box-sizing:border-box;margin:6px 0 12px;" ${this._loading ? 'disabled' : ''}>
      <div style="display:flex;flex-wrap:wrap;gap:8px;">
        <button id="queueEntityEdit" type="submit" class="btn btn-primary" ${this._loading ? 'disabled' : ''}>${t.queueEdit}</button>
        <button id="cancelEntityEdit" type="button" class="btn btn-outline">${t.cancel}</button>
      </div>
    </form>`;
  }

  _renderRenameConfirmation() {
    // render() calls this whenever _confirmDialogOpen is true (Apply Changes).
    // It was never implemented, so the call threw "is not a function" and
    // blanked the card — same regression class as _renderApplyResult below.
    const t = this._t;
    const queue = this._renameQueue || [];
    const idChanges = queue.filter(r => (r.newId && r.newId !== r.oldId) || r.newName);
    const devEntries = Object.entries(this._deviceRenameQueue || {});
    const MAX = 8;
    const shown = idChanges.slice(0, MAX);
    const more = idChanges.length - shown.length;
    const rowStyle = 'display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:6px 0;border-bottom:1px solid var(--bento-border,#e5e7eb);font-size:12.5px';
    const codeStyle = 'font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;background:var(--bento-bg-2,#f3f4f6);padding:2px 6px;border-radius:6px';
    const sub = 'color:var(--bento-text-secondary,#6b7280)';
    const rows = shown.map(r => `
        <div style="${rowStyle}">
          <code style="${codeStyle}">${_esc(r.oldId)}</code>
          <span style="opacity:.6">&rarr;</span>
          <code style="${codeStyle}">${_esc(r.newId)}</code>
          ${r.newName ? `<span style="${sub}">"${_esc(r.newName)}"</span>` : ''}
        </div>`).join('');
    const devRows = devEntries.slice(0, MAX).map(([id, name]) => `
        <div style="${rowStyle}"><span>&#128295;</span><span>${_esc(this._getDeviceName(this._devices.find(d => d.id === id) || { id }))} → ${_esc(String(name))}</span></div>`).join('');
    const fill = (s, n) => _esc(String(s || '').replace('{count}', n));
    return `
      <div class="confirm-dialog" role="dialog" aria-labelledby="renameConfirmTitle" style="border:1px solid var(--bento-primary,#3B82F6);border-radius:var(--bento-radius-md,14px);background:var(--bento-card,#fff);padding:16px 18px;margin:12px 0;box-shadow:var(--bento-shadow-md)">
        <div id="renameConfirmTitle" style="font-weight:700;font-size:14px;margin-bottom:6px">&#9888;&#65039; ${_esc(t.confirmRenameTitle)}</div>
        <div style="font-size:12.5px;${sub};margin-bottom:10px">${_esc(t.confirmRenameBody)}</div>
        <div style="font-size:12.5px;font-weight:600;margin-bottom:4px">${fill(t.confirmRenameCount, idChanges.length)}</div>
        ${idChanges.length ? rows : `<div style="font-size:12.5px;${sub}">${_esc(t.confirmNoEntityChanges)}</div>`}
        ${more > 0 ? `<div style="font-size:12px;opacity:.7;padding-top:6px">${fill(t.confirmMore, more)}</div>` : ''}
        ${devEntries.length ? `<div style="font-size:12.5px;font-weight:600;margin:10px 0 4px">${fill(t.confirmDeviceCount, devEntries.length)}</div>${devRows}` : ''}
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px">
          <button id="cancelRenameDialog" class="btn btn-outline">${_esc(t.cancel)}</button>
          <button id="confirmRenameApply" class="btn btn-primary">${_esc(t.rename)}</button>
        </div>
      </div>`;
  }

  _renderApplyResult() {
    // Stub: render() referenced this._renderApplyResult() but the method was
    // never implemented, so it threw "is not a function" and blanked the whole
    // card (regression since v4.2.0). Apply/rename feedback is surfaced via
    // this._message above, so an empty string is the correct no-op here.
    return '';
  }

  _renderAutomaticTab() {
    const t = this._t;
    const queued = new Set(this._renameQueue.map(row => row.oldId));
    const groups = new Map();
    for (const row of this._visibleAutomaticRows()) {
      if (!groups.has(row.deviceId)) groups.set(row.deviceId, []);
      groups.get(row.deviceId).push(row);
    }
    const eligible = row => !row.conflict && !queued.has(row.oldId);
    const visibleRows=this._visibleAutomaticRows();
    const canQueue = visibleRows.some(eligible);
    const deviceName = id => this._getDeviceName(this._devices.find(item => item.id === id) || {id});
    const deviceHtml = [...groups].sort(([a], [b]) => this._compareDevices(this._devices.find(d=>d.id===a)||{id:a},this._devices.find(d=>d.id===b)||{id:b})).map(([deviceId, rows]) => {
      const device = this._devices.find(item => item.id === deviceId);
      const deviceQueued = rows.some(row => !row.conflict) && rows.filter(row => !row.conflict).every(row => queued.has(row.oldId));
      return `<details data-auto-group="${_esc(deviceId)}" style="border:1px solid var(--bento-border,#ddd);border-radius:8px;padding:8px;margin:8px 0">
        <summary>${_esc(device ? this._getDeviceName(device) : deviceId)} (${rows.length}) · ${rows.filter(row=>queued.has(row.oldId)).length} ${t.inQueue.toLowerCase()} ${this._newBadge(device?.created_at)}</summary>
        ${[...rows].sort((a,b)=>this._listUi.compare(a.oldId,b.oldId)).map(row => `<div class="rename-preview-row">
          <div class="automatic-row-ids"><div><code>${_esc(row.oldId)}</code>${this._newBadge(this._entities.find(e=>e.entity_id===row.oldId)?.created_at)}</div><div class="automatic-target">→ <code>${_esc(row.newId)}</code></div></div>
          <div class="automatic-row-actions">
          ${queued.has(row.oldId)?`<button class="btn btn-sm btn-outline" data-remove-queue="${_esc(row.oldId)}">${this._lang==='pl'?'Usuń':'Remove'}</button>`:''}
          ${row.conflict ? `<span>${t.automaticConflict}</span>` : `<button class="btn btn-sm btn-outline" data-auto-entity="${_esc(row.oldId)}" ${queued.has(row.oldId) || this._loading ? 'disabled' : ''}>${queued.has(row.oldId) ? '✓ ' + t.inQueue : '+ ' + t.queue}</button>`}
          </div>
        </div>`).join('')}
        <button class="btn btn-sm btn-outline" data-auto-device="${_esc(deviceId)}" ${rows.some(eligible) && !this._loading ? '' : 'disabled'}>${deviceQueued ? '✓ ' + t.inQueue : t.addDeviceAutomatic}</button>
      </details>`;
    }).join('');
    return `<div class="automatic-tab">
      ${this._renderListControls(true)}
      <p>${this._lang==='pl'?'Rejestry HA · bieżący odczyt · propozycje identyfikatorów':'HA registries · current read · ID proposals'}</p>
      <details class="source-limits"><summary>${this._lang==='pl'?'Źródło i ograniczenia':'Source and limits'}</summary><p>${t.automaticInfo}</p></details>
      <button class="btn btn-outline" id="loadAutomatic" ${this._loading ? 'disabled' : ''}>${this._loading ? '…' : t.loadAutomatic}</button>
      ${this._automaticLoaded ? `<p>${this._automaticRows.length} ${t.automatic}; ${this._automaticNull.length} ${t.automaticUnknown}</p>
        ${this._automaticRows.some(row => !row.conflict) ? `<button class="btn btn-primary" id="queueAllAutomatic" ${canQueue && !this._loading ? '' : 'disabled'}>${canQueue ? (this._lang==='pl'?'Dodaj widoczne':'Queue visible')+' ('+visibleRows.filter(eligible).length+')' : '✓ ' + t.inQueue}</button>` : `<p>${t.automaticEmpty}</p>`}
        ${deviceHtml}
        ${this._visibleAutomaticNull().length ? `<details><summary>${t.automaticUnknown} (${this._visibleAutomaticNull().length})</summary>${this._visibleAutomaticNull().map(ent => `<div><code>${_esc(ent.entity_id)}</code></div>`).join('')}</details>` : ''}` : ''}
    </div>`;
  }

  _renderDevicesTab(devices) {
    const t = this._t;
    return `
      ${this._renderListControls()}
      <div class="stats-row">
        <div class="stat-card"><div class="num">${this._devices.length}</div><div class="label">${t.devices}</div></div>
        <div class="stat-card"><div class="num">${this._entities.length}</div><div class="label">${t.entities}</div></div>
        <div class="stat-card"><div class="num">${this._renameQueue.length}</div><div class="label">${t.inQueue}</div></div>
        <button type="button" class="stat-card btn btn-outline" data-list-filter="new" data-list-scope="devices"><span class="num">${this._devices.filter(d=>this._listUi.isNew(d.created_at)).length}</span><span class="label">${this._lang==='pl'?'Nowe (7 dni)':'New (7 days)'}</span></button>
      </div>
      <div class="device-list">
        ${devices.length === 0 ? `<div class="empty-state"><div class="icon">🔍</div>${t.noResults}</div>` :
          devices.map(d => {
            const ents = this._deviceRows(d);
            const isExpanded = this._expandedDevices.has(d.id);
            const isSelected = this._selectedDevice === d.id;
            return `
            <div class="device-item ${isSelected ? 'selected' : ''}">
              <button type="button" class="device-header" data-device-id="${_esc(d.id)}" role="button" tabindex="0" aria-expanded="${isExpanded}" aria-label="${_esc((this._lang === 'pl' ? 'Szczegóły urządzenia: ' : 'Device details: ') + this._getDeviceName(d))}">
                <span class="device-expand ${isExpanded ? 'open' : ''}">▶</span>
                <span class="device-name">${_esc(this._getDeviceName(d))}</span>${this._newBadge(d.created_at)}
                <span class="device-meta">${this._registryCount(ents.length,'entity')} · ${(this._deviceEntities[d.id]||[]).filter(e=>this._renameQueue.some(r=>r.oldId===e.entity_id)).length} ${t.inQueue.toLowerCase()}</span>
              </button>
              ${isExpanded ? `
              <div class="entity-list">
                ${ents.map(e => {
                  const domain = e.entity_id.split('.')[0];
                  const inQueue = this._renameQueue.some(r => r.oldId === e.entity_id);
                  return `
                  <div class="entity-row">
                    <span class="entity-domain">${_esc(domain)}</span>
                    <span class="entity-id">${_esc(e.entity_id)}${this._newBadge(e.created_at)}</span>
                    <span class="entity-name">${_esc(e.name || e.original_name || '')}</span>
                    ${inQueue
                      ? '<span class="queued-badge">✓ ' + t.inQueue + '</span><button class="btn btn-sm btn-outline" data-remove-queue="' + _esc(e.entity_id) + '" aria-label="' + _esc(t.removeFromQueue) + '">' + (this._lang==='pl'?'Usuń':'Remove') + '</button>'
                      : `<button class="btn btn-sm btn-outline" data-add-single="${_esc(e.entity_id)}">+ ${t.queue}</button>`
                    }
                  </div>`;
                }).join('')}
              </div>
              ${isSelected ? `
              <div class="prefix-section">
                <h3>📱 ${t.newDeviceName}</h3>
                <div class="prefix-row" style="margin-bottom:12px">
                  <input type="text" id="deviceName" data-family-draft-context="${_esc(d.id)}" aria-label="${_esc(t.newDeviceName)}" value="${_esc(this._deviceRenameQueue[d.id] || this._getDeviceName(d))}" placeholder="${t.newDeviceName}" style="width:300px;font-family:inherit">
                  <button class="btn btn-primary btn-sm" id="applyDeviceName" data-device-id="${_esc(d.id)}">${t.changeName}</button>
                </div>
                <h3>📄 ${t.prefixChange}</h3>
                <div class="prefix-row">
                  <input type="text" id="prefixOld" data-family-draft-context="${_esc(d.id)}" aria-label="${_esc(t.oldPrefix)}" value="${_esc(this._prefixOld || '')}" placeholder="${t.oldPrefix}">
                  <span class="arrow">→</span>
                  <input type="text" id="prefixNew" data-family-draft-context="${_esc(d.id)}" aria-label="${_esc(t.newPrefix)}" value="${_esc(this._prefixNew || '')}" placeholder="${t.newPrefix}">
                  <button class="btn btn-primary btn-sm" id="applyPrefix">${t.apply}</button>
                </div>
              </div>` : ''}
              ` : ''}
            </div>`;
          }).join('')}
      </div>`;
  }

  _renderQueueTab() {
    const t = this._t;
    if (!this._renameQueue.length && !Object.keys(this._deviceRenameQueue || {}).length) {
      return `<div class="empty-state"><div class="icon">📋</div>${t.queueEmpty}</div>`;
    }
    const devEntries = Object.entries(this._deviceRenameQueue);
    return `
      <div class="queue-actions">
        <button class="btn btn-outline" id="clearQueue" ${this._loading ? 'disabled' : ''}>🗑️ ${t.clear}</button>
        <button class="btn btn-outline" id="analyzeImpact" ${this._loading ? 'disabled' : ''}>🔍 ${t.analyzeImpact}</button>
        <button class="btn btn-danger" id="executeRenames" ${this._loading || this._hass?.user?.is_admin !== true ? 'disabled' : ''} ${this._loading ? 'aria-busy="true"' : ''}>🚀 ${t.executeRenames} (${this._renameQueue.length + devEntries.length})</button>
        ${this._hass?.user?.is_admin !== true ? `<span>${t.adminRequired}</span>` : ''}
      </div>
      <div class="queue-list">
          ${this._queueGroups().map(group => `<section data-queue-group="${_esc(group.device.id)}"><h3 class="queue-group-heading"><span>${_esc(this._getDeviceName(group.device))}</span><span>${group.rows.length+(group.nameChange!==undefined?1:0)}</span></h3>${group.nameChange!==undefined?`<div class="queue-device-name"><strong>${t.newDeviceName}</strong>: ${_esc(this._getDeviceName(group.device))} → ${_esc(group.nameChange)} <span class="proposal-source">${this._lang==='pl'?'Ręcznie':'Manual'}</span> <button type="button" class="btn btn-sm btn-outline" data-remove-dev-queue="${_esc(group.device.id)}" aria-label="${_esc(t.removeFromQueue)}">${this._lang==='pl'?'Usuń':'Remove'}</button></div>`:''}${group.rows.map(r => {
            const imp = this._impactResults ? this._impactResults[r.oldId] : null;
            const hasImpact = imp && (imp.automations.length || imp.scripts.length || imp.dashboards.length || (imp.scenes||[]).length);
            return `<div style="border:1px solid var(--bento-border,#334155);border-radius:8px;padding:12px;margin-bottom:8px;">
              <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">
                <span class="old" style="flex:1;min-width:0;overflow-wrap:anywhere;font-family:'JetBrains Mono',monospace;font-size:11px;">${_esc(r.oldId)}</span>
                <button class="btn btn-sm btn-danger" style="flex-shrink:0;" data-remove-queue="${_esc(r.oldId)}" aria-label="${_esc(t.removeFromQueue)}">✕</button>
              </div>
              <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                <span style="color:var(--bento-text-secondary,#94A3B8);">→</span>
                <span class="new" style="flex:1;min-width:0;overflow-wrap:anywhere;font-family:'JetBrains Mono',monospace;font-size:11px;">${r.newId !== r.oldId ? _esc(r.newId) : '<span>' + t.noIdChange + '</span>'}</span>
              </div>
              <span class="proposal-source">${r.automatic ? t.automatic : r.source==='prefix' ? (this._lang==='pl'?'Prefiks':'Prefix') : (this._lang==='pl'?'Ręcznie':'Manual')}</span>
              ${r.newName ? '<div style="margin:6px 0 0 24px;min-width:0;overflow-wrap:anywhere;font-size:11px;color:var(--bento-text, var(--primary-text-color, #E2E8F0));">📝 ' + _esc(r.newName) + '</div>' : ''}
              ${hasImpact ? `<div style="margin-top:6px;padding-top:6px;border-top:1px solid rgba(255,255,255,0.05);">
                <span style="font-size:10px;color:var(--bento-text-secondary,#94A3B8);">⚠️ ${t.usedIn}</span>
                ${imp.automations.map(a => '<span class="impact-badge automation">⚙ ' + _esc(a) + '</span>').join('')}
                ${imp.scripts.map(s => '<span class="impact-badge script">📜 ' + _esc(s) + '</span>').join('')}
                ${imp.dashboards.map(d => '<span class="impact-badge dashboard">📊 ' + _esc(d) + '</span>').join('')}
                ${(imp.scenes||[]).map(s => '<span class="impact-badge scene">🎬 ' + _esc(s) + '</span>').join('')}
              </div>` : (imp && imp.dashboardCoverage !== 'unavailable' ? `<div style="margin-top:4px;font-size:14px;color:var(--bento-text-secondary,#94A3B8);">${t.notUsed}</div>` : '')}
              ${this._dashboardCoverageHtml(imp)}
            </div>`;
          }).join('')}</section>`).join('')}
      </div>
`;
  }

  _renderLogTab() {
    const t = this._t, ui = createUiV3({language:this._lang});
    if (this._hass?.user?.is_admin !== true || !this._historyKey()) return `<div class="empty-state">${t.adminRequired}</div>`;
    const legacyNotice = this._legacyHistoryPresent ? `<p>${this._lang === 'pl'
      ? 'Starsza historia ma nieznane pochodzenie konta. Zachowano ją lokalnie. Możesz jawnie przejrzeć i wyeksportować te wpisy; nie zostaną przypisane do Twojego konta.'
      : 'Legacy history has unknown origin. It is preserved locally. You can explicitly review and export these entries; they will not be assigned to your account.'}</p><button id="reviewLegacyHistory" type="button" class="btn btn-outline" style="min-height:44px;min-width:44px;margin-bottom:12px;max-width:100%;white-space:normal;">${this._legacyReviewLog.length ? (this._lang === 'pl' ? 'Ukryj starszą historię' : 'Hide legacy history') : (this._lang === 'pl' ? 'Przejrzyj starszą historię z tej przeglądarki' : 'Review legacy history from this browser')}</button>` : '';
    const visibleHistory = this._visibleRenameHistory();
    const exportButton = `<button id="exportRenameLog" type="button" class="btn btn-outline" style="min-height:44px;min-width:44px;margin:0 0 12px;max-width:100%;white-space:normal;overflow-wrap:anywhere;">${this._lang === 'pl' ? 'Eksport historii zmian' : 'Export completed history'}</button>`;
    if (!visibleHistory.length) {
      return legacyNotice + exportButton + `<div class="empty-state"><div class="icon">📜</div>${t.noHistory}</div>`;
    }
    return legacyNotice + exportButton + `
      <div>
        ${visibleHistory.filter(l => l && typeof l === 'object' && typeof l.oldId === 'string' && typeof l.newId === 'string').map(l => {
          const reported = renameReports.snapshot([l], true).rows[0]?.status;
          const resultState = reported === 'applied' ? 'success' : reported === 'failed' ? 'error' : 'unconfirmed';
          const imp = l.impact;
          const automations = Array.isArray(imp?.automations) ? imp.automations : [];
          const scripts = Array.isArray(imp?.scripts) ? imp.scripts : [];
          const dashboards = Array.isArray(imp?.dashboards) ? imp.dashboards : [];
          const scenes = Array.isArray(imp?.scenes) ? imp.scenes : [];
          const hasImpact = automations.length || scripts.length || dashboards.length || scenes.length;
          return `
          <div class="log-entry" style="padding:8px 0;${hasImpact ? 'padding-bottom:12px;' : ''}">
            <span class="log-time">${_esc(this._formatUi.dateTime(l.completedAt || l.attemptedAt) || String(l.time || '—') + (this._lang==='pl'?' · data nieznana':' · date unknown'))}</span>
            ${this._formatUi.dateTime(l.completedAt || l.attemptedAt) ? `<details><summary>${this._lang==='pl'?'Szczegóły czasu':'Time details'}</summary><code>${_esc(l.completedAt || l.attemptedAt)}</code> · ${_esc(this._hass?.config?.time_zone||'UTC')}</details>`:''}
            <span class="${resultState === 'success' ? 'log-ok' : resultState === 'error' ? 'log-err' : ''}">
              ${ui.statusHtml(resultState)} ${_esc(l.oldId)} → ${_esc(l.newId)}
            </span>
            ${l.error ? `<br><small style="color:#FCA5A5">${_esc(l.error)}</small>` : ''}
            ${this._dashboardCoverageHtml(imp)}
            ${hasImpact ? `<div style="margin-top:4px;padding-left:24px;">
              <span style="font-size:10px;color:var(--bento-text-secondary,#94A3B8);">⚠️ ${t.usedIn}</span>
              ${automations.map(a => '<span class="impact-badge automation">⚙ ' + _esc(a) + '</span>').join('')}
              ${scripts.map(s => '<span class="impact-badge script">📜 ' + _esc(s) + '</span>').join('')}
              ${dashboards.map(d => '<span class="impact-badge dashboard">📊 ' + _esc(d) + '</span>').join('')}
              ${scenes.map(s => '<span class="impact-badge scene">🎬 ' + _esc(s) + '</span>').join('')}
            </div>` : ''}
          </div>`;
        }).join('')}
      </div>`;
  }

  _attachEvents() {
    const root = this.shadowRoot;
    const t = this._t;
    root.getElementById('exportRenameLog')?.addEventListener('click', e => this._openRenameExport(e.currentTarget));
    root.getElementById('reviewLegacyHistory')?.addEventListener('click', () => this._reviewLegacyHistory());

    // Tab switching
    root.querySelectorAll('.tab-button').forEach(btn => {
      btn.addEventListener('click', () => {
        this._activeTab = btn.dataset.tab;
        history.replaceState(null, '', location.pathname + '#' + this._toolId + '/' + this._activeTab);
        this.render();
      });
    });

    root.getElementById('openQueue')?.addEventListener('click',()=>root.querySelector('[data-tab="queue"]').click());
    root.getElementById('undoQueueRemoval')?.addEventListener('click',()=>this._undoQueueRemoval());
    root.getElementById('listSort')?.addEventListener('change',event=>{this._listSort=event.target.value;this.render();this.shadowRoot.getElementById('listSort')?.focus();});
    root.querySelectorAll('[data-list-filter]').forEach(button=>button.addEventListener('click',()=>{
      this._listFilters[button.dataset.listScope]=button.dataset.listFilter;
      this.render();
      Array.from(this.shadowRoot.querySelectorAll('[data-list-filter]')).find(el=>el.dataset.listFilter===button.dataset.listFilter)?.focus();
    }));
    root.querySelectorAll('[data-expand-list]').forEach(button=>button.addEventListener('click',()=>{
      const expand=button.dataset.expandList==='true';
      if(this._activeTab==='automatic') root.querySelectorAll('[data-auto-group]').forEach(group=>{group.open=expand;});
      else {this._getFilteredDevices().forEach(device=>expand?this._expandedDevices.add(device.id):this._expandedDevices.delete(device.id));this.render();}
    }));
    root.getElementById('automaticSearch')?.addEventListener('input',event=>{
      const caret=event.target.selectionStart;this._automaticSearch=event.target.value;this.render();
      const field=this.shadowRoot.getElementById('automaticSearch');field?.focus();if(field)field.setSelectionRange(caret,caret);
    });

    const loadAutomatic = root.getElementById('loadAutomatic');
    if (loadAutomatic) loadAutomatic.addEventListener('click', () => this._loadAutomaticIds());
    const queueAllAutomatic = root.getElementById('queueAllAutomatic');
    if (queueAllAutomatic) queueAllAutomatic.addEventListener('click', () => this._queueAutomaticIds());
    root.querySelectorAll('[data-auto-device]').forEach(btn => btn.addEventListener('click', () => this._queueAutomaticIds(btn.dataset.autoDevice)));
    root.querySelectorAll('[data-auto-entity]').forEach(btn => btn.addEventListener('click', () => this._queueAutomaticIds(null, btn.dataset.autoEntity)));

    // Search
    const searchInput = root.getElementById('searchInput');
    if (searchInput) {
      searchInput.addEventListener('input', (e) => {
        this._searchQuery = e.target.value;
        this.render();
        // Restore focus and cursor position
        const el = this.shadowRoot.getElementById('searchInput');
        if (el) { el.focus(); el.selectionStart = el.selectionEnd = e.target.selectionStart; }
      });
    }

    // Device headers – toggle expand + select
    root.querySelectorAll('.device-header').forEach(hdr => {
      hdr.addEventListener('click', () => {
        const did = hdr.dataset.deviceId;
        if (this._expandedDevices.has(did)) {
          this._expandedDevices.delete(did);
          this._selectedDevice = null;
        } else {
          this._expandedDevices.add(did);
          this._selectDevice(did);
          return; // _selectDevice calls render
        }
        this.render();
      });
    });

    _bindFamilyRowKeys(root, '.device-header');

    // Device rename
    const applyDeviceName = root.getElementById('applyDeviceName');
    if (applyDeviceName) {
      applyDeviceName.addEventListener('click', () => {
        if (this._loading) return;
        const devId = applyDeviceName.dataset.deviceId;
        const newName = (root.getElementById('deviceName') || {}).value || '';
        if (newName) {
          this._confirmDialogOpen = false;
          this._confirmation = null;
          this._lastApplyResult = null;
          this._deviceRenameQueue[devId] = newName;
          this._message = { type: 'info', text: t.deviceAdded.replace('{name}', newName) };
          this.render();
        }
      });
    }

    // Stage manual changes in the card; registry writes still require confirmation.
    root.querySelectorAll('[data-add-single]').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        this._showEntityEditor(btn.dataset.addSingle);
      });
    });
    const editor = root.getElementById('entityEditForm');
    if (editor) {
      editor.addEventListener('submit', e => {
        e.preventDefault();
        if (this._loading || !this._entityEditor) return;
        const { oldId, objectId, currentName } = this._entityEditor;
        const newObjectId = root.getElementById('entityEditObjectId').value || objectId;
        const name = root.getElementById('entityEditName').value;
        this._entityEditor = null;
        this._addToQueue(oldId, oldId.split('.')[0] + '.' + newObjectId, name && name !== currentName ? name : null);
        this.render();
      });
      root.getElementById('cancelEntityEdit').addEventListener('click', () => {
        this._entityEditor = null; this.render();
      });
      for (const [id, key] of [['entityEditObjectId','objectIdDraft'], ['entityEditName','nameDraft']]) {
        root.getElementById(id).addEventListener('input', e => { if (this._entityEditor) this._entityEditor[key] = e.target.value; });
      }
      editor.addEventListener('keydown', e => {
        if (e.key === 'Escape') { e.preventDefault(); this._entityEditor = null; this.render(); }
      });
    }

    // Remove from queue
    root.querySelectorAll('[data-remove-queue]').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        this._removeFromQueue(btn.dataset.removeQueue);
      });
    });

    // Remove device from queue
    root.querySelectorAll('[data-remove-dev-queue]').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        this._removeDeviceFromQueue(btn.dataset.removeDevQueue);
      });
    });

    // Prefix rename
    const applyPrefix = root.getElementById('applyPrefix');
    if (applyPrefix) {
      applyPrefix.addEventListener('click', () => {
        this._prefixOld = (root.getElementById('prefixOld') || {}).value || '';
        this._prefixNew = (root.getElementById('prefixNew') || {}).value || '';
        this._addPrefixRename();
      });
    }

    // Queue actions
    const clearQueue = root.getElementById('clearQueue');
    if (clearQueue) clearQueue.addEventListener('click', () => this._clearQueue());

    const analyzeImpact = root.getElementById('analyzeImpact');
    if (analyzeImpact) analyzeImpact.addEventListener('click', () => this._analyzeImpact());

    const cancelRenameDialog = root.getElementById('cancelRenameDialog');
    if (cancelRenameDialog) {
      cancelRenameDialog.addEventListener('click', () => this._cancelRenameConfirmation());
    }

    root.querySelector('.confirm-dialog')?.addEventListener('keydown', e => {
      if (e.key === 'Escape') {
        e.preventDefault(); e.stopPropagation();
        this._cancelRenameConfirmation();
      } else if (e.key === 'Tab') {
        e.preventDefault();
        const cancel = root.getElementById('cancelRenameDialog');
        const apply = root.getElementById('confirmRenameApply');
        (root.activeElement === cancel ? apply : cancel)?.focus();
      }
    });

    const confirmRenameApply = root.getElementById('confirmRenameApply');
    if (confirmRenameApply) {
      confirmRenameApply.addEventListener('click', () => {
        this._confirmDialogOpen = false;
        this._executeRenames(true);
      });
    }

    const executeRenames = root.getElementById('executeRenames');
    if (executeRenames) {
      executeRenames.addEventListener('click', () => this._showRenameConfirmation());
    }
  }

  disconnectedCallback() {
    this._cancelRenameAuthority();
    _disconnectFamilyNavigation(this);this._familyNavWidth=null;
    this._exportDialog?.cancel();
    this._exportEventCleanups.splice(0).forEach(off => off());
    this._exportBoundConnection = null;
    this._exportRevision++;
    this._entityEditor = null;
    this._readEpoch++;
    if (this._applyOperation) this._applyOperation.cancelled = true;
    this._confirmation = null;
    this._confirmDialogOpen = false;
    // Cleanup any active event listeners or timers
  }

  setActiveTab(tabId) {
    this._activeTab = tabId;
    this.render();
  }
}

if (!customElements.get('ha-entity-renamer')) customElements.define('ha-entity-renamer', HAEntityRenamer);

class HaEntityRenamerEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this._config = {};
  }
  setConfig(config) {
    this._config = { ...config };
    this._render();
  }
  set hass(hass) {
    this._hass = hass;
    this._updateTitleLabel();
  }
  _titleLabel() {
    const language = typeof this._config?.language === 'string' ? this._config.language : this._hass?.language || navigator.language || 'en';
    return language.startsWith('pl') ? 'Tytuł' : 'Title';
  }
  _updateTitleLabel() {
    const label = this.shadowRoot.querySelector('label[for="cf_title"]');
    if (label) label.textContent = this._titleLabel();
  }
  _dispatch() {
    this.dispatchEvent(new CustomEvent('config-changed', { detail: { config: this._config }, bubbles: true, composed: true }));
  }
  _render() {
    const title = this.shadowRoot.querySelector('#cf_title');
    const value = String(this._config.title ?? 'Entity Renamer');
    if (title) {
      // Reuse the Config Auditor editor fix: equal HA echoes must not reset selection.
      if (title.value !== value) title.value = value;
      this._updateTitleLabel();
      return;
    }
    this.shadowRoot.innerHTML = `
      <style>
        :host { display:block; padding:16px; }
        h3 { margin:0 0 16px; font-size:15px; font-weight:600; color:var(--bento-text, var(--primary-text-color,#1e293b)); }
        input { box-sizing:border-box;min-height:44px;min-width:44px;max-width:100%;transition:border-color .2s; }
            input:focus-visible { outline:3px solid var(--primary-color,#1769aa);outline-offset:2px; }
        input:focus { border-color:var(--bento-primary, var(--primary-color,#3b82f6)); }
      </style>
      <h3>Entity Renamer</h3>
            <div style="margin-bottom:12px;">
              <label for="cf_title" style="display:block;font-weight:500;margin-bottom:4px;font-size:13px;">${this._titleLabel()}</label>
              <input type="text" id="cf_title" value="${_esc(value)}"
                style="width:100%;padding:8px 12px;border:1px solid var(--divider-color,#e2e8f0);border-radius:8px;background:var(--card-background-color,#fff);color:var(--primary-text-color,#1e293b);font-size:14px;box-sizing:border-box;">
            </div>
    `;
        const f_title = this.shadowRoot.querySelector('#cf_title');
        if (f_title) f_title.addEventListener('input', (e) => {
          this._config = { ...this._config, title: e.target.value };
          this._dispatch();
        });
  }
  connectedCallback() { this._render(); }
}
if (!customElements.get('ha-entity-renamer-editor')) { customElements.define('ha-entity-renamer-editor', HaEntityRenamerEditor); }

})();

window.customCards = window.customCards || [];
window.customCards.push({ type: 'ha-entity-renamer', name: 'Entity Renamer', description: 'Rename entities and devices, then review affected references', preview: false });
