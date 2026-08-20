const fs = require('node:fs');
const path = require('node:path');

const source = fs.readFileSync(
  path.join(__dirname, '..', 'ha-entity-renamer.js'),
  'utf8',
);

const requiredEscapedSinks = [
  '${_esc(d.id)}',
  '${_esc(this._getDeviceName(d))}',
  '${_esc(domain)}',
  '${_esc(e.entity_id)}',
  "${_esc(e.name || e.original_name || '')}",
  "_esc(e.entity_id) + '\" aria-label=\"Remove",
  'data-add-single="${_esc(e.entity_id)}"',
  'data-device-id="${_esc(d.id)}"',
  '${_esc(dev ? this._getDeviceName(dev) : did)}',
  '${_esc(name)}',
  'data-remove-dev-queue="${_esc(did)}"',
  '${_esc(r.oldId)}',
  'data-remove-queue="${_esc(r.oldId)}"',
  '${r.newId !== r.oldId ? _esc(r.newId)',
  "_esc(r.newName) + '</span>'",
  "_esc(a) + '</span>'",
  "_esc(s) + '</span>'",
  "_esc(d) + '</span>'",
  '${_esc(l.time)}',
  '${_esc(l.oldId)} → ${_esc(l.newId)}',
  '${_esc(l.error)}',
];

const missing = requiredEscapedSinks.filter((snippet) => !source.includes(snippet));
if (missing.length) {
  console.error('Unsafe HTML interpolation regression: missing escaped sinks:');
  for (const snippet of missing) console.error(`- ${snippet}`);
  process.exit(1);
}

console.log(`OK: ${requiredEscapedSinks.length} dynamic HTML sinks are escaped`);
