'use strict';
// --write propagates REVIEWED local sources; no runtime, network or HA calls.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const root = path.resolve(__dirname, '..');
const hash = value => crypto.createHash('sha256').update(value).digest('hex');
const provenance = JSON.parse(fs.readFileSync(path.join(root,'shared/renamer-report-provenance.json'),'utf8'));
const units = [
 ['HA TOOLS FORMAT V4','shared/ha-tools-format.js',s=>s.split('// Node tests use the canonical factory;')[0].trimEnd()],
 ['HA TOOLS LISTS V4','shared/ha-tools-lists.js',s=>s.split('// Node tests use the canonical factory;')[0].trimEnd()],
 ['HA TOOLS UI V3','shared/ha-tools-ui.js',s=>s.split('// Node tests use the canonical factory;')[0].trimEnd()],
 ['HA TOOLS EXPORT V3','shared/ha-tools-export.js',s=>s.replace('export function createExportDialog','function createExportDialog').trimEnd()],
 ['HA RENAMER REPORTS V1','shared/ha-renamer-reports.js',s=>s.split('// Node tests use the canonical factory;')[0].trimEnd()],
];
let card = fs.readFileSync(path.join(root,'ha-entity-renamer.js'),'utf8');
for(const [name,file,transform] of units) {
 const source=fs.readFileSync(path.join(root,file),'utf8'),digest=hash(source),runtime=transform(source);
 if(digest!==provenance.files[file].sourceSha256 || hash(runtime)!==provenance.files[file].runtimeSha256) throw new Error('Reviewed source/provenance drift: '+file);
 const start='// BEGIN '+name,end='// END '+name;
 const first=card.indexOf(start),last=card.indexOf(end,first);
 if(first<0 || last<0 || card.indexOf(start,first+1)>=0 || card.indexOf(end,last+1)>=0) throw new Error('Missing or competing local vendor: '+name);
 const expected=start+' source-sha256='+digest+'\n'+runtime+'\n'+end;
 if(process.argv.includes('--write')) card=card.slice(0,first)+expected+card.slice(last+end.length);
 else if(card.slice(first,last+end.length)!==expected) throw new Error('Runtime/source drift: '+name);
}
for(const factory of ['createUiV3','createExportDialog','createRenameReports']) {
 if((card.match(new RegExp('function '+factory+'\\(','g'))||[]).length!==1 || card.includes('window.'+factory)) throw new Error('Competing/global factory: '+factory);
}
if(process.argv.includes('--upstream-check')) for(const file of ['ha-tools-export.js','ha-tools-ui.js']) {
 const upstream=fs.readFileSync(path.resolve(root,'../ha-config-auditor/shared',file));
 if(hash(upstream)!==provenance.files['shared/'+file].sourceSha256)throw new Error('Config upstream changed; review before updating provenance: '+file);
}
if(process.argv.includes('--write')) fs.writeFileSync(path.join(root,'ha-entity-renamer.js'),card);
process.stdout.write(process.argv.includes('--write')?'Reviewed vendors propagated.\n':'Reviewed source/vendor parity confirmed.\n');
