// Reviewed completed-history projection. Never serialize arbitrary log/state/config.
function createRenameReports() {
  const entityId = value => typeof value === 'string' && value.length <= 255 && /^[a-z][a-z0-9_]*\.[a-z0-9_]+$/.test(value) ? value : null;
  const timestamp = row => {
    if (typeof row.completedAt === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(row.completedAt)) {
      const date = new Date(row.completedAt);
      if (Number.isFinite(date.getTime()) && date.toISOString() === row.completedAt) return row.completedAt;
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
      const phase = ['not_started', 'attempted', 'acknowledged'].includes(entry.writePhase) ? entry.writePhase : null;
      // ACK is never a readback. Legacy error text cannot prove rejection before a write.
      const status = entry.status === 'error' && phase === 'not_started' ? 'failed' : 'unknown';
      const reason = status === 'failed' ? 'preflight_rejected' : entry.status === 'ok' ? 'ack_without_readback' : phase === 'attempted' ? 'write_outcome_unconfirmed' : 'legacy_error_without_phase';
      const references = Object.fromEntries(referenceTypes.map(type => [type, Array.isArray(entry.impact?.[type]) ? entry.impact[type].length : null]));
      const hasKnownReferences = Object.values(references).some(count => typeof count === 'number' && count > 0);
      rows.push({index: rows.length + 1, timestamp: timestamp(entry), timestampScope: typeof entry.completedAt === 'string' && timestamp(entry) === entry.completedAt ? 'utc_completion' : timestamp(entry) ? 'local_time_date_unknown' : 'unknown',
        kind: entry.operationKind === 'device' ? 'device' : old && next ? 'entity' : 'unreviewed_identifier',
        old: includePrivate ? old : alias(old, `record-${rows.length + 1}-source`),
        new: includePrivate ? next : alias(next, `record-${rows.length + 1}-target`), status, reason,
        references, referenceCoverage: 'known_readable_hits_only_completeness_unknown',
        manualRepair: status === 'failed' ? 'no_change_confirmed_no_repair_claim' : old && next && old !== next && hasKnownReferences ? 'verify_registry_then_review_known_references' : 'verify_registry_no_reference_coverage_claim'});
    }
    return {rows, omitted};
  }
  function build({log, format, includePrivate = false, language = 'en'}) {
    const {rows, omitted} = snapshot(log, includePrivate), pl = language === 'pl';
    if (!rows.length) return {serialized: '', status: 'no_data', confirmed: true, sourceConfirmed: true};
    const limits = pl
      ? 'Tylko ukończone wpisy lokalnej historii. ACK nie potwierdza odczytu po zapisie: applied=0. Starsze błędy bez fazy zapisu są unknown. Pochodzenie konta, pełna data starszych wpisów i kompletność odwołań nie są potwierdzone. Liczby obejmują tylko zapisane trafienia z search/related i czytelnych dashboardów Lovelace; null oznacza brak danych, 0 zapisany brak trafień. Eksport nie wykonuje odczytów ani zmian HA.'
      : 'Completed entries in local history only. ACK has no registry readback: applied=0. Legacy errors without a write phase are unknown. Account origin, full dates of legacy entries and reference completeness are unconfirmed. Counts cover recorded search/related and readable Lovelace hits only; null means unavailable, 0 recorded no hits. Export performs no HA reads or changes.';
    const instructions = pl
      ? 'Dla unknown sprawdź aktualny rejestr przed kolejną zmianą. Dla zapisanych trafień po potwierdzeniu zmiany ID ręcznie sprawdź automatyzacje, skrypty, sceny i dashboardy w HA; popraw stare odwołania i przetestuj. Failed dotyczy wyłącznie odrzuconej próby przed zapisem. Raport nie uruchamia naprawy, undo ani kolejnej zmiany nazw.'
      : 'For unknown, verify the current registry before another change. For recorded hits, after confirming an ID change manually inspect automations, scripts, scenes and dashboards in HA, update old references and test. Failed covers only rejection before a write. This report performs no repair, undo or repeat rename.';
    const included = [pl ? 'Liczby applied/failed/unknown, bezpieczny czas/status/powód, liczby znanych odwołań i ręczne kroki.' : 'Applied/failed/unknown counts, reviewed time/status/reason, known reference counts and manual steps.',
      includePrivate ? (pl ? 'Tylko ścisłe identyfikatory old/new entity_id; pozostałe identyfikatory jako null.' : 'Strict old/new entity_id only; other identifiers are null.') : (pl ? 'Powiązane pseudonimy old → new; bez prawdziwych ID.' : 'Paired old → new pseudonyms; no real IDs.')];
    const excluded = [pl ? 'Tokeny, użytkownicy, adresy, nazwy, surowe błędy/wiadomości, konfiguracja, YAML i szablony.' : 'Tokens, users, addresses, names, raw errors/messages, configuration, YAML and templates.', pl ? 'Propozycje w kolejce, niepotwierdzony sukces i automatyczne naprawy.' : 'Queued proposals, unconfirmed success and automatic repairs.'];
    const report = {schema: 'ha-entity-renamer.completed-history.v1', status: 'partial', source: 'native_completed_local_history', method: 'recorded_registry_attempts_without_readback',
      privateIds: includePrivate === true, counts: {total: rows.length, applied: 0, failed: rows.filter(row => row.status === 'failed').length, unknown: rows.filter(row => row.status === 'unknown').length, omitted}, included, excluded, limits, instructions, rows};
    let serialized;
    if (format === 'json') serialized = JSON.stringify(report, null, 2) + '\n';
    else if (format === 'csv') {
      const cell = value => {
        let text = value === null ? 'null' : String(value);
        if (/^[\s]*[=+\-@]/.test(text) || /^[\t\r\n]/.test(text)) text = "'" + text;
        return '"' + text.replace(/"/g, '""') + '"';
      };
      const records = [['record', 'index', 'timestamp', 'timestamp_scope', 'kind', 'old', 'new', 'status', 'reason', ...referenceTypes, 'reference_coverage', 'manual_repair']];
      for (const key of ['schema', 'status', 'source', 'method', 'privateIds', 'limits', 'instructions']) records.push(['meta', key, report[key]]);
      for (const [key, value] of Object.entries(report.counts)) records.push(['count', key, value]);
      for (const key of ['included', 'excluded']) report[key].forEach(value => records.push([key, value]));
      rows.forEach(row => records.push(['row', row.index, row.timestamp, row.timestampScope, row.kind, row.old, row.new, row.status, row.reason, ...referenceTypes.map(type => row.references[type]), row.referenceCoverage, row.manualRepair]));
      serialized = records.map(values => values.map(cell).join(',')).join('\r\n') + '\r\n';
    } else if (format === 'txt') {
      serialized = [report.schema, `status: ${report.status}`, `source: ${report.source}`, `method: ${report.method}`, `privateIds: ${report.privateIds}`, `counts: ${JSON.stringify(report.counts)}`, ...included.map(text => 'included: ' + text), ...excluded.map(text => 'excluded: ' + text), `limits: ${limits}`, `instructions: ${instructions}`,
        ...rows.map(row => JSON.stringify(row))].join('\n') + '\n';
    } else throw new TypeError('Unreviewed report format');
    return {serialized, confirmed: true, sourceConfirmed: true, status: 'partial', included, excluded, sourceLimit: limits};
  }
  return Object.freeze({snapshot, build});
}
// Node tests use the canonical factory; this line is excluded from runtime copies.
if (typeof module !== 'undefined' && module.exports) module.exports = { createRenameReports };
