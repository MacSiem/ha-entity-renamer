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
// Node tests use the canonical factory; vendored cards keep it local.
if (typeof module !== 'undefined') module.exports = {createFormatUi};
