/* HA Tools split — ha-entity-renamer v4.0.0 (2026-05-10) — single-tool standalone repo */
(function() {
'use strict';

// XSS protection helper (global singleton — tools reuse via window._haToolsEsc)
window._haToolsEsc = window._haToolsEsc || ((s) => typeof s === 'string' ? s.replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]) : (s ?? ''));
const _esc = window._haToolsEsc;

/**
 * HA Entity Renamer – Device & Entity Rename Tool
 * Renames devices/entities and propagates changes across dashboards, automations, scripts, config.
 * Part of HA Tools suite.
 */
/* ===== HA Tools split — inline shared infrastructure ===== */
// Bento Design System CSS (inline copy — keeps tool standalone)
if (typeof window !== 'undefined' && !window.HAToolsBentoCSS) {
  window.HAToolsBentoCSS = `
/* ═══════════════════════════════════════════════
   HA Tools — Bento Design System v1.0
   ═══════════════════════════════════════════════ */

/* ── CSS Custom Properties ───────────────────── */
:host {
  /* Primary palette */
  --bento-primary: #3B82F6;
  --bento-primary-hover: #2563EB;
  --bento-primary-light: rgba(59, 130, 246, 0.08);
  --bento-success: #10B981;
  --bento-success-light: rgba(16, 185, 129, 0.08);
  --bento-error: #EF4444;
  --bento-error-light: rgba(239, 68, 68, 0.08);
  --bento-warning: #F59E0B;
  --bento-warning-light: rgba(245, 158, 11, 0.08);

  /* Theme — maps to HA theme vars with light fallbacks */
  --bento-bg: var(--primary-background-color, #F8FAFC);
  --bento-card: var(--card-background-color, #FFFFFF);
  --bento-border: var(--divider-color, #E2E8F0);
  --bento-text: var(--primary-text-color, #1E293B);
  --bento-text-secondary: var(--secondary-text-color, #64748B);
  --bento-text-muted: var(--disabled-text-color, #94A3B8);

  /* Radii */
  --bento-radius-xs: 6px;
  --bento-radius-sm: 10px;
  --bento-radius-md: 16px;

  /* Shadows */
  --bento-shadow-sm: 0 1px 3px rgba(0,0,0,0.04), 0 1px 2px rgba(0,0,0,0.06);
  --bento-shadow-md: 0 4px 12px rgba(0,0,0,0.05), 0 2px 4px rgba(0,0,0,0.04);
  --bento-shadow-lg: 0 8px 25px rgba(0,0,0,0.06), 0 4px 10px rgba(0,0,0,0.04);

  /* Transition */
  --bento-transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);

  /* Typography */
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  display: block;
  color: var(--bento-text);
}

/* ── Dark mode ───────────────────────────────── */
@media (prefers-color-scheme: dark) {
  :host {
    --bento-bg: var(--primary-background-color, #1a1a2e);
    --bento-card: var(--card-background-color, #16213e);
    --bento-border: var(--divider-color, #2a2a4a);
    --bento-text: var(--primary-text-color, #e0e0e0);
    --bento-text-secondary: var(--secondary-text-color, #a0a0b0);
    --bento-text-muted: var(--disabled-text-color, #6a6a7a);
    --bento-shadow-sm: 0 1px 3px rgba(0,0,0,0.3);
    --bento-shadow-md: 0 4px 12px rgba(0,0,0,0.4);
    --bento-primary-light: rgba(59,130,246,0.15);
    --bento-success-light: rgba(16,185,129,0.15);
    --bento-error-light: rgba(239,68,68,0.15);
    --bento-warning-light: rgba(245,158,11,0.15);
    color-scheme: dark !important;
  }
  .card, .card-container, .main-card, .exporter-card, .security-card, .reports-card, .storage-card, .chore-card, .cry-card, .backup-card, .network-card, .sentence-card, .energy-card, .panel-card {
    background: var(--bento-card) !important; color: var(--bento-text) !important; border-color: var(--bento-border) !important;
  }
  input, select, textarea { background: var(--bento-bg); color: var(--bento-text); border-color: var(--bento-border); }
  .stat, .stat-card, .summary-card, .metric-card, .kpi-card, .health-card { background: var(--bento-bg); border-color: var(--bento-border); }
  .tab-content, .section { color: var(--bento-text); }
  table th { background: var(--bento-bg); color: var(--bento-text-secondary); border-color: var(--bento-border); }
  table td { color: var(--bento-text); border-color: var(--bento-border); }
  tr:hover td { background: rgba(59,130,246,0.08); }
  .empty-state, .no-data { color: var(--bento-text-secondary); }
  .schedule-section, .settings-section, .detail-panel, .details, .device-detail { background: var(--bento-bg); border-color: var(--bento-border); }
  .addon-list, .content-item { background: rgba(255,255,255,0.05); }
  .chart-container { background: var(--bento-bg); border-color: var(--bento-border); }
  pre, code { background: #1e293b !important; color: #e2e8f0 !important; }
}

/* ── Reset ───────────────────────────────────── */
* { box-sizing: border-box; }

/* ── Main Card Wrapper ───────────────────────── */
.card {
  background: var(--bento-card);
  border: 1px solid var(--bento-border);
  border-radius: var(--bento-radius-md);
  box-shadow: var(--bento-shadow-sm);
  color: var(--bento-text);
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}

/* ── Header ──────────────────────────────────── */
.header {
  padding: 16px 20px 0;
  display: flex;
  align-items: center;
  gap: 10px;
}
.header-icon { font-size: 22px; }
.header-title {
  font-size: 16px;
  font-weight: 700;
  color: var(--bento-text);
}
.header-badge {
  margin-left: auto;
  background: var(--bento-border);
  color: var(--bento-text-secondary);
  font-size: 11px;
  padding: 3px 8px;
  border-radius: 20px;
  font-weight: 500;
}
.content { padding: 16px 20px 20px; }

/* ── Tabs (Bento unified) ────────────────────── */
.tabs, .tab-bar, .tab-nav, .tab-header {
  display: flex !important;
  gap: 4px !important;
  border-bottom: 2px solid var(--bento-border, var(--divider-color, #334155)) !important;
  padding: 0 4px !important;
  margin-bottom: 20px !important;
  overflow-x: auto !important; overflow-y: hidden !important; -webkit-overflow-scrolling: touch !important;
  flex-wrap: nowrap !important;
}
.tab, .tab-btn, .tab-button, .dtab {
  padding: 10px 18px !important;
  border: none !important;
  background: transparent !important;
  cursor: pointer !important;
  font-size: 13px !important;
  font-weight: 500 !important;
  font-family: 'Inter', sans-serif !important;
  color: var(--bento-text-secondary, var(--secondary-text-color, #94A3B8)) !important;
  border-bottom: 2px solid transparent !important;
  margin-bottom: -2px !important;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;
  white-space: nowrap !important;
  border-radius: 0 !important;
  flex: none !important;
}
.tab:hover, .tab-btn:hover, .tab-button:hover, .dtab:hover {
  color: var(--bento-primary, #3B82F6) !important;
  background: rgba(59, 130, 246, 0.08) !important;
}
.tab.active, .tab-btn.active, .tab-button.active, .dtab.active {
  color: var(--bento-primary, #3B82F6) !important;
  border-bottom-color: var(--bento-primary, #3B82F6) !important;
  background: rgba(59, 130, 246, 0.04) !important;
  font-weight: 600 !important;
}

/* ── Tab content animation ───────────────────── */
.tab-content {
  display: block;
}
.tab-content.active {
  animation: bentoFadeIn 0.3s ease-out;
}
@keyframes bentoFadeIn {
  from { opacity: 0; transform: translateY(6px); }
  to   { opacity: 1; transform: translateY(0); }
}

/* ── Stat / KPI cards ────────────────────────── */
.stat-card, .stat-item, .metric-card, .kpi-card {
  background: var(--bento-card, var(--card-background-color, #1E293B)) !important;
  border: 1px solid var(--bento-border, var(--divider-color, #334155)) !important;
  border-radius: var(--bento-radius-sm, 10px) !important;
  padding: 16px !important;
  text-align: center !important;
  transition: var(--bento-transition);
}
.stat-card:hover, .stat-item:hover, .metric-card:hover, .kpi-card:hover {
  box-shadow: var(--bento-shadow-md);
}
.stat-icon { font-size: 20px; margin-bottom: 4px; }
.stat-value, .stat-val, .metric-value, .kpi-val {
  font-size: 22px;
  font-weight: 700;
  color: var(--bento-text);
}
.stat-label, .stat-lbl, .metric-label, .kpi-lbl {
  font-size: 11px;
  color: var(--bento-text-secondary);
  margin-top: 2px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  font-weight: 500;
}

/* ── Overview grid (2×2 stat layout) ─────────── */
.overview-grid, .stats-grid, .summary-grid, .stat-cards, .kpi-grid, .metrics-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  margin-bottom: 16px;
}

/* ── Section headers ─────────────────────────── */
.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12px;
  font-weight: 600;
  color: var(--bento-text-secondary);
  text-transform: uppercase;
  letter-spacing: .5px;
  margin: 12px 0 8px;
}

/* ── Loading / Empty / Info ──────────────────── */
.loading-bar {
  height: 3px;
  background: linear-gradient(90deg, var(--bento-primary), transparent);
  border-radius: 2px;
  animation: bentoLoad 1s infinite;
  margin-bottom: 8px;
}
@keyframes bentoLoad { 0% { background-position: 0; } 100% { background-position: 200px; } }

.empty-state, .no-data, .no-results {
  text-align: center;
  color: var(--bento-text-secondary);
  padding: 32px 16px;
  font-size: 13px;
  background: var(--bento-bg);
  border-radius: var(--bento-radius-sm);
}
.info-note, .tip-box {
  font-size: 12px;
  color: var(--bento-text-secondary);
  background: var(--bento-bg);
  border-radius: var(--bento-radius-sm);
  padding: 8px 10px;
  border-left: 3px solid var(--bento-primary);
  margin-top: 8px;
}
.last-updated {
  font-size: 11px;
  color: var(--bento-text-muted);
  text-align: right;
  margin-top: 8px;
}

/* ── Buttons ─────────────────────────────────── */
.refresh-btn {
  background: var(--bento-border);
  border: none;
  border-radius: 6px;
  padding: 4px 10px;
  font-size: 11px;
  color: var(--bento-text-secondary);
  cursor: pointer;
  font-weight: 500;
  transition: var(--bento-transition);
}
.refresh-btn:hover { background: var(--bento-primary); color: white; }

.toggle-btn, .action-btn {
  background: var(--bento-primary);
  border: none;
  border-radius: 6px;
  padding: 5px 12px;
  font-size: 12px;
  color: white;
  cursor: pointer;
  font-weight: 500;
  transition: var(--bento-transition);
}
.toggle-btn:hover, .action-btn:hover { opacity: .85; }

.send-btn, .btn-primary {
  width: 100%;
  background: var(--bento-primary);
  color: white;
  border: none;
  border-radius: 8px;
  padding: 10px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: var(--bento-transition);
}
.send-btn:hover, .btn-primary:hover {
  background: var(--bento-primary-hover);
  transform: translateY(-1px);
}
.send-btn:active, .btn-primary:active { transform: translateY(0); }
.send-btn:disabled, .btn-primary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
  transform: none;
}

/* ── Badges / Status ─────────────────────────── */
.badge, .status-badge, .tag, .chip {
  padding: 4px 10px;
  border-radius: 20px;
  font-size: 11px;
  font-weight: 600;
  display: inline-block;
}
.badge-ok, .badge-success { background: var(--bento-success-light); color: var(--bento-success); }
.badge-er, .badge-error   { background: var(--bento-error-light);   color: var(--bento-error); }
.badge-warn, .badge-warning { background: var(--bento-warning-light); color: var(--bento-warning); }
.badge-info { background: var(--bento-primary-light); color: var(--bento-primary); }

/* ── Count badges (inline) ───────────────────── */
.count-badge {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 20px;
}
.error-badge { background: rgba(239,68,68,0.13); color: var(--bento-error); }
.warn-badge  { background: rgba(245,158,11,0.13); color: var(--bento-warning); }
.info-badge  { background: rgba(59,130,246,0.13); color: var(--bento-primary); }
.ok-badge    { background: rgba(16,185,129,0.13); color: var(--bento-success); }

/* ── Tables ───────────────────────────────────── */
table { width: 100%; border-collapse: separate; border-spacing: 0; }
th {
  background: var(--bento-bg);
  color: var(--bento-text-secondary);
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  padding: 10px 14px;
  text-align: left;
  border-bottom: 2px solid var(--bento-border);
}
td {
  padding: 12px 14px;
  border-bottom: 1px solid var(--bento-border);
  color: var(--bento-text);
  font-size: 13px;
}
tr:hover td { background: var(--bento-primary-light); }

/* ── Forms / Inputs ──────────────────────────── */
input, select, textarea {
  padding: 8px 12px;
  border: 1.5px solid var(--bento-border);
  border-radius: var(--bento-radius-xs);
  background: var(--bento-card);
  color: var(--bento-text);
  font-size: 13px;
  font-family: 'Inter', sans-serif;
  transition: var(--bento-transition);
  outline: none;
}
input:focus, select:focus, textarea:focus {
  border-color: var(--bento-primary);
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.1);
}

/* ── Code blocks ─────────────────────────────── */
code {
  background: var(--bento-border);
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 12px;
}
pre {
  background: #1e293b;
  color: #e2e8f0;
  padding: 12px;
  border-radius: 8px;
  font-size: 12px;
  overflow-x: auto;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

/* ── Grid layouts ────────────────────────────── */
.schedule-grid, .send-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}
.schedule-card, .send-card, .info-card {
  background: var(--bento-bg);
  border: 1px solid var(--bento-border);
  border-radius: var(--bento-radius-sm);
  padding: 14px;
}

/* ── Log entries ─────────────────────────────── */
.log-entry {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  gap: 4px 6px;
  padding: 8px;
  border-radius: var(--bento-radius-sm);
  margin-bottom: 4px;
  font-size: 12px;
  min-width: 0;
  overflow: hidden;
}
.error-entry { background: var(--bento-error-light); border: 1px solid rgba(239,68,68,0.13); }
.warn-entry  { background: var(--bento-warning-light); border: 1px solid rgba(245,158,11,0.13); }
.log-time { color: var(--bento-text-muted); flex-shrink: 0; }
.log-domain {
  font-weight: 600;
  flex-shrink: 1;
  min-width: 0;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  word-break: break-all;
}
.error-domain { color: var(--bento-error); }
.warn-domain  { color: var(--bento-warning); }
.log-msg {
  color: var(--bento-text-secondary);
  flex-basis: 100%;
  word-break: break-word;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
  min-width: 0;
}

/* ── Send status ─────────────────────────────── */
.send-status {
  padding: 10px 14px;
  border-radius: var(--bento-radius-sm);
  margin-top: 12px;
  font-size: 13px;
  font-weight: 500;
  text-align: center;
}
.send-status.sending { background: var(--bento-primary-light); color: var(--bento-primary); }
.send-status.success { background: var(--bento-success-light); color: var(--bento-success); }
.send-status.error   { background: var(--bento-error-light);   color: var(--bento-error); }

/* ── Scrollbar ───────────────────────────────── */
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--bento-border); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: var(--bento-text-muted); }

/* ── Animations ──────────────────────────────── */
@keyframes bentoSpin { to { transform: rotate(360deg); } }
@keyframes bentoPulse { 0%,100% { opacity: 1; } 50% { opacity: .5; } }

/* ── Mobile — 768 px ─────────────────────────── */
@media (max-width: 768px) {
  .content { padding: 12px; }
  .tabs { flex-wrap: nowrap !important; overflow-x: auto !important; -webkit-overflow-scrolling: touch !important; gap: 2px !important; }
  .tab, .tab-button, .tab-btn { padding: 6px 10px !important; font-size: 12px !important; white-space: nowrap !important; }
  .overview-grid, .stats-grid, .summary-grid, .stat-cards, .kpi-grid, .metrics-grid { grid-template-columns: repeat(2, 1fr); gap: 8px; }
  .stat-value, .stat-val, .kpi-val, .metric-val { font-size: 18px !important; }
  .stat-label, .stat-lbl, .kpi-lbl, .metric-lbl { font-size: 10px !important; }
  .send-grid, .schedule-grid { grid-template-columns: 1fr; }
  .log-entry { flex-wrap: wrap; gap: 2px 6px; }
  .log-domain { max-width: 60%; font-size: 11px; }
  .log-msg { flex-basis: 100%; max-width: 100%; overflow-wrap: anywhere; font-size: 11px; }
  pre { white-space: pre-wrap; word-break: break-all; max-width: calc(100vw - 80px); overflow-x: auto; }
  .panels, .board { flex-direction: column; }
  .column { min-width: unset; }
  h2 { font-size: 18px; }
  h3 { font-size: 15px; }
}

/* ── Mobile — 480 px ─────────────────────────── */
@media (max-width: 480px) {
  .tabs { gap: 1px !important; }
  .tab, .tab-button, .tab-btn { padding: 5px 8px !important; font-size: 11px !important; }
  .overview-grid, .stats-grid, .summary-grid { grid-template-columns: 1fr 1fr; }
  .stat-value, .stat-val, .kpi-val { font-size: 16px !important; }
}
`;
}
// XSS escape singleton (idempotent)
if (typeof window !== 'undefined') {
  window._haToolsEsc = window._haToolsEsc || (function(){
    var MAP = {};
    MAP[String.fromCharCode(38)] = '&amp;';
    MAP[String.fromCharCode(60)] = '&lt;';
    MAP[String.fromCharCode(62)] = '&gt;';
    MAP[String.fromCharCode(34)] = '&quot;';
    MAP[String.fromCharCode(39)] = '&#39;';
    return function(s){ return typeof s === 'string' ? s.replace(/[&<>"']/g, function(c){ return MAP[c]; }) : (s == null ? '' : s); };
  })();
}
// Universal donate footer injector — guarantees the support box appears
// on every split-tool card regardless of internal render state.
if (typeof window !== 'undefined' && !window.__haToolsSplitDonateInjector) {
  window.__haToolsSplitDonateInjector = true;
  var SPLIT_TAGS = ['ha-purge-cache','ha-yaml-checker','ha-data-exporter','ha-baby-tracker','ha-chore-tracker','ha-energy-optimizer','ha-energy-insights','ha-energy-email','ha-log-email','ha-smart-reports','ha-network-map','ha-trace-viewer','ha-automation-analyzer','ha-storage-monitor','ha-backup-manager','ha-security-check','ha-device-health','ha-sentence-manager','ha-encoding-fixer','ha-entity-renamer','ha-frigate-privacy','ha-vacuum-water-monitor'];
  var DONATE_HTML = ''
    + '<div class="donate-section" data-source="ha-tools-split-injector">'
    + '  <div class="donate-text">'
    + '    <h3>❤️ Support HA Tools Development</h3>'
    + '    <p>If this tool makes your Home Assistant life easier, consider supporting the project. Every coffee motivates further development!</p>'
    + '  </div>'
    + '  <div class="donate-buttons">'
    + '    <a class="donate-btn coffee" href="https://buymeacoffee.com/macsiem" target="_blank" rel="noopener noreferrer">☕ Buy Me a Coffee</a>'
    + '    <a class="donate-btn paypal" href="https://www.paypal.com/donate/?hosted_button_id=Y967H4PLRBN8W" target="_blank" rel="noopener noreferrer">💳 PayPal</a>'
    + '  </div>'
    + '</div>';
  function deepFindAll(tag, root) {
    var out = [];
    (function walk(node){
      if (!node || !node.querySelectorAll) return;
      var children = node.querySelectorAll('*');
      for (var i = 0; i < children.length; i++) {
        var c = children[i];
        if (c.tagName && c.tagName.toLowerCase() === tag) out.push(c);
        if (c.shadowRoot) walk(c.shadowRoot);
      }
    })(root || document);
    return out;
  }
  // Per-tool prerequisite check + inline install banner
  var PREREQS = {
    'ha-energy-email': { service: 'ha_tools_email', repo: 'ha-tools-email-integration', label: 'HA Tools Email integration', kind: 'integration' },
    'ha-log-email':    { service: 'ha_tools_email', repo: 'ha-tools-email-integration', label: 'HA Tools Email integration', kind: 'integration' },
    'ha-encoding-fixer': { shellCommand: 'fix_encoding', label: 'shell_command.fix_encoding (optional advanced feature)', kind: 'shell_command_optional' }
  };
  var PREREQ_HTML_CACHE = {};
  function buildPrereqBanner(tag, prereq, hass) {
    if (PREREQ_HTML_CACHE[tag]) return PREREQ_HTML_CACHE[tag];
    var html = '';
    if (prereq.kind === 'integration') {
      html = '<div class="prereq-banner prereq-error" data-prereq="' + tag + '">' +
        '<div class="prereq-icon">⚠️</div>' +
        '<div class="prereq-text">' +
          '<strong>This tool requires the ' + prereq.label + '</strong><br>' +
          'Install it from HACS: <code>https://github.com/MacSiem/' + prereq.repo + '</code> ' +
          '(Category: <strong>Integration</strong>) — then add <code>' + prereq.service + ':</code> to your <code>configuration.yaml</code> and restart HA.' +
        '</div>' +
        '<a class="prereq-cta" href="https://github.com/MacSiem/' + prereq.repo + '" target="_blank" rel="noopener noreferrer">Open install guide ↗</a>' +
      '</div>';
    } else if (prereq.kind === 'shell_command_optional') {
      html = '<div class="prereq-banner prereq-info" data-prereq="' + tag + '">' +
        '<div class="prereq-icon">💡</div>' +
        '<div class="prereq-text">' +
          '<strong>Optional advanced feature: deep file scan</strong><br>' +
          'To enable scanning of <code>configuration.yaml</code> files, install the bundled <code>encoding_scanner.py</code> + add <code>shell_command:</code> entries. See README.' +
        '</div>' +
      '</div>';
    }
    PREREQ_HTML_CACHE[tag] = html;
    return html;
  }
  function injectAll() {
    SPLIT_TAGS.forEach(function(tag){
      deepFindAll(tag).forEach(function(el){
        // panel_custom auto-init: HA assigns hass/panel/narrow but does not always call setConfig.
        if (typeof el.setConfig === 'function' && !el.config && !el._config) {
          try { el.setConfig({ type: 'custom:' + tag, title: tag }); } catch(e) {}
        }
        if (!el.shadowRoot) return;
        // 1) Prereq banner — checked every poll so it disappears when prereq becomes available
        var prereq = PREREQS[tag];
        if (prereq && el._hass) {
          var hassReady = !!el._hass;
          var present = true;
          if (prereq.service) present = !!(el._hass.services && el._hass.services[prereq.service]);
          if (prereq.shellCommand) present = !!(el._hass.services && el._hass.services.shell_command && el._hass.services.shell_command[prereq.shellCommand]);
          var existing = el.shadowRoot.querySelector('.prereq-banner[data-prereq="' + tag + '"]');
          if (!present && hassReady) {
            if (!existing) {
              var top = el.shadowRoot.querySelector('.card, .card-container, .main-card, [class$="-card"]') || el.shadowRoot.firstElementChild || el.shadowRoot;
              try { top.insertAdjacentHTML('afterbegin', buildPrereqBanner(tag, prereq, el._hass)); } catch(e) {}
            }
          } else if (present && existing) {
            existing.remove();
          }
        }
        // 2) Donate footer
        if (el.shadowRoot.querySelector('.donate-section')) return;
        var target = el.shadowRoot.querySelector('.card, .card-container, .main-card, [class$="-card"]') || el.shadowRoot.firstElementChild || el.shadowRoot;
        try { target.insertAdjacentHTML('beforeend', DONATE_HTML); } catch(e) {}
      });
    });
  }
  // Run immediately, then aggressive MutationObserver for late mounts + view switches.
  injectAll();
  setTimeout(injectAll, 250);
  setTimeout(injectAll, 1000);
  setTimeout(injectAll, 3000);
  // MutationObserver catches every new node anywhere in the DOM, including shadow root attachments
  // that are deferred until the user navigates to a view.
  try {
    var obs = new MutationObserver(function(muts){
      // Debounce: schedule a microtask injection
      if (window.__haToolsDonateScheduled) return;
      window.__haToolsDonateScheduled = true;
      setTimeout(function(){ window.__haToolsDonateScheduled = false; injectAll(); }, 100);
    });
    obs.observe(document.body, { childList: true, subtree: true });
  } catch(e) {}
  // Also re-inject on hash/path change (Lovelace view switches)
  window.addEventListener('hashchange', function(){ setTimeout(injectAll, 200); });
  window.addEventListener('popstate', function(){ setTimeout(injectAll, 200); });
  // Backup interval (every 3s for first 5min — handles cases where MutationObserver missed events)
  var pollCount = 0;
  var pollInterval = setInterval(function(){
    injectAll();
    if (++pollCount >= 100) clearInterval(pollInterval);
  }, 3000);
}
/* ============================================================ */

class HAEntityRenamer extends HTMLElement {
  static getConfigElement() { return document.createElement('ha-entity-renamer-editor'); }
  getCardSize() { return 8; }

  static getStubConfig() { return { type: 'custom:ha-entity-renamer', title: 'Entity Renamer' }; }
  setConfig(config) { this._config = config || {}; }
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
    this._renameQueue = []; // {oldId, newId, newName?, entityObj}
    this._previewMode = false;
    this._deviceRenameQueue = {}; // deviceId -> newName
    this._impactResults = null;
    this._loading = false;
    this._message = null;
    this._activeTab = 'devices'; // devices | queue | log
    this._renameLog = [];
    this._expandedDevices = new Set();
    this._loadHistoryFromStorage();
  }

  _loadHistoryFromStorage() {
    try {
      const stored = window._haToolsPersistence?.load('entity-renamer-history');
      if (Array.isArray(stored)) {
        this._renameLog = stored;
      }
    } catch (e) {
      // Fallback: localStorage as backup
      try {
        const stored = localStorage.getItem('ha-tools-entity-renamer-history');
        if (stored) this._renameLog = JSON.parse(stored);
      } catch (e2) { console.debug('[ha-entity-renamer] caught:', e); }
    }
  }

  _saveHistoryToStorage() {
    try {
      if (window._haToolsPersistence?.save) {
        window._haToolsPersistence.save('entity-renamer-history', this._renameLog);
      } else {
        localStorage.setItem('ha-tools-entity-renamer-history', JSON.stringify(this._renameLog));
      }
    } catch (e) { console.debug('[ha-entity-renamer] caught:', e); }
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
        renameSuccess: 'Zmieniono {ok} encji{devCount}{fail}{impact}. Zrestartuj HA.',
        newEntity: 'Nowy entity_id (object_id) – zostaw bez zmian jeśli chcesz zmienić tylko friendly name:',
        newFriendly: 'Nowy friendly name (zostaw puste = bez zmian):',
        confirmRename: 'Czy na pewno chcesz zmienić nazwy {count} encji? Ta operacja jest nieodwracalna.',
        noHistory: 'Brak historii zmian.\nWykonaj zmiany z zakładki Kolejka.',
        usedIn: 'Używane w:',
        notUsed: 'Nieużywane w automatyzacjach, skryptach ani dashboardach',
        deviceAdded: 'Nazwa urządzenia "{name}" dodana do kolejki.',
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
        renameSuccess: 'Renamed {ok} entities{devCount}{fail}{impact}. Restart HA.',
        newEntity: 'New entity_id (object_id) – leave unchanged if you only want to change the friendly name:',
        newFriendly: 'New friendly name (leave empty = no change):',
        confirmRename: 'Are you sure you want to rename {count} entities? This operation is irreversible.',
        noHistory: 'No rename history.\nMake changes from the Queue tab.',
        usedIn: 'Used in:',
        notUsed: 'Not used in automations, scripts, or dashboards',
        deviceAdded: 'Device name "{name}" added to queue.',
      }
    };
    return T[this._lang] || T.en;
  }

  connectedCallback() {
    this._lang = (navigator.language || '').startsWith('pl') ? 'pl' : 'en';
  }

  _sanitize(str) {
    if (!str) return str;
    try { return decodeURIComponent(escape(str)); } catch(e) { return str; }
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (hass?.language) this._lang = hass.language.startsWith('pl') ? 'pl' : 'en';
    if (first) this._init();
  }

  async _init() {
    this.render();
    await this._loadData();
    this.render();
  }

  async _loadData() {
    this._loading = true;
    this.render();
    try {
      const [devResult, entResult] = await Promise.all([
        this._hass.callWS({ type: 'config/device_registry/list' }),
        this._hass.callWS({ type: 'config/entity_registry/list' }),
      ]);
      this._devices = devResult.sort((a, b) =>
        (a.name_by_user || a.name || '').localeCompare(b.name_by_user || b.name || '')
      );
      this._entities = entResult;
      // Build device->entities map
      this._deviceEntities = {};
      for (const ent of this._entities) {
        const did = ent.device_id;
        if (!did) continue;
        if (!this._deviceEntities[did]) this._deviceEntities[did] = [];
        this._deviceEntities[did].push(ent);
      }
      // Sort entities within each device
      for (const did of Object.keys(this._deviceEntities)) {
        this._deviceEntities[did].sort((a, b) => a.entity_id.localeCompare(b.entity_id));
      }
    } catch (e) {
      this._message = { type: 'error', text: this._t.errorLoadingData + e.message };
    }
    this._loading = false;
  }


  _getDeviceName(d) { return d.name_by_user || d.name || d.id; }

  _getFilteredDevices() {
    const q = this._searchQuery.toLowerCase().trim();
    if (!q) return this._devices.filter(d => (this._deviceEntities[d.id] || []).length > 0);
    return this._devices.filter(d => {
      const name = this._getDeviceName(d).toLowerCase();
      if (name.includes(q)) return true;
      const ents = this._deviceEntities[d.id] || [];
      return ents.some(e => e.entity_id.toLowerCase().includes(q) || (e.name || '').toLowerCase().includes(q));
    });
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


  _addToQueue(oldId, newId, newName) {
    // Allow adding if EITHER entity_id changed OR friendly_name/alias changed
    if (oldId === newId && !newName) return;
    if (this._renameQueue.some(r => r.oldId === oldId)) {
      this._renameQueue = this._renameQueue.map(r => r.oldId === oldId ? { ...r, newId, ...(newName !== undefined ? { newName } : {}) } : r);
    } else {
      const ent = this._entities.find(e => e.entity_id === oldId);
      this._renameQueue.push({ oldId, newId, newName: newName || null, entity: ent });
    }
    this.render();
  }

  _removeFromQueue(oldId) {
    this._renameQueue = this._renameQueue.filter(r => r.oldId !== oldId);
    this.render();
  }

  _clearQueue() {
    this._renameQueue = [];
    this._impactResults = null;
    this.render();
  }

  _addPrefixRename() {
    if (!this._prefixOld || !this._prefixNew || this._prefixOld === this._prefixNew) return;
    const ents = this._deviceEntities[this._selectedDevice] || [];
    for (const ent of ents) {
      const objId = ent.entity_id.split('.')[1] || '';
      if (objId.startsWith(this._prefixOld)) {
        const domain = ent.entity_id.split('.')[0];
        const newObjId = this._prefixNew + objId.substring(this._prefixOld.length);
        const newId = domain + '.' + newObjId;
        this._addToQueue(ent.entity_id, newId);
      }
    }
    this.render();
  }


  async _analyzeImpact() {
    if (!this._renameQueue.length) return;
    this._loading = true;
    this._message = { type: 'info', text: this._t.analyzing };
    this.render();

    const impact = {};
    // 1. Use search/related WS API for each entity (automations, scripts, scenes, areas)
    const searchPromises = this._renameQueue.map(rename =>
      this._hass.callWS({ type: 'search/related', item_type: 'entity', item_id: rename.oldId })
        .then(result => ({ oldId: rename.oldId, result }))
        .catch(() => ({ oldId: rename.oldId, result: {} }))
    );
    const searchResults = await Promise.all(searchPromises);

    // Build friendly names lookup for automations/scripts
    const hass = this._hass;
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
      const lovelaceConfig = await this._loadLovelaceConfigs();
      for (const rename of this._renameQueue) {
        for (const dash of lovelaceConfig) {
          if (JSON.stringify(dash.config || {}).includes(rename.oldId)) {
            impact[rename.oldId].dashboards.push(dash.title || dash.url_path || 'default');
          }
        }
      }
    } catch(e) { console.debug('[ha-entity-renamer] caught:', e); }

    this._impactResults = impact;
    this._loading = false;
    this._message = null;
    this._activeTab = 'queue';
    this.render();
  }

  async _loadLovelaceConfigs() {
    try {
      const dashboards = await this._hass.callWS({ type: 'lovelace/dashboards/list' });
      const configs = [];
      // Default dashboard
      try {
        const defCfg = await this._hass.callWS({ type: 'lovelace/config', force: false });
        configs.push({ url_path: 'default', title: 'Default', config: defCfg });
      } catch(e) { console.debug('[ha-entity-renamer] caught:', e); }
      // Other dashboards
      for (const dash of dashboards) {
        try {
          const cfg = await this._hass.callWS({ type: 'lovelace/config', url_path: dash.url_path });
          configs.push({ url_path: dash.url_path, title: dash.title || dash.url_path, config: cfg });
        } catch(e) { console.debug('[ha-entity-renamer] caught:', e); }
      }
      return configs;
    } catch(e) { return []; }
  }


  async _executeRenames() {
    if (!this._renameQueue.length && !Object.keys(this._deviceRenameQueue).length) return;
    this._loading = true;
    this._message = { type: 'info', text: this._t.analyzing2 };
    this.render();

    // Auto-run impact analysis before execution using search/related
    let impact = {};
    try {
      const hass = this._hass;
      const searchPromises = this._renameQueue.map(rename =>
        hass.callWS({ type: 'search/related', item_type: 'entity', item_id: rename.oldId })
          .then(result => ({ oldId: rename.oldId, result }))
          .catch(() => ({ oldId: rename.oldId, result: {} }))
      );
      const searchResults = await Promise.all(searchPromises);
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
      const lovelaceConfig = await this._loadLovelaceConfigs();
      for (const rename of this._renameQueue) {
        for (const dash of lovelaceConfig) {
          if (JSON.stringify(dash.config || {}).includes(rename.oldId)) {
            impact[rename.oldId].dashboards.push(dash.title || dash.url_path || 'default');
          }
        }
      }
    } catch(e) { console.debug('[ha-entity-renamer] caught:', e); }

    const results = [];
    const ts = new Date().toLocaleTimeString();

    // 1. Rename devices first
    for (const [devId, newName] of Object.entries(this._deviceRenameQueue)) {
      try {
        await this._hass.callWS({ type: 'config/device_registry/update', device_id: devId, name_by_user: newName });
        this._renameLog.unshift({ time: ts, oldId: '📱 ' + devId.substring(0, 8) + '...', newId: '📱 ' + newName, status: 'ok', impact: null });
      } catch (e) {
        this._renameLog.unshift({ time: ts, oldId: '📱 device', newId: '📱 ' + newName, status: 'error', error: e.message, impact: null });
      }
    }

    // 2. Rename entities (entity_id + optional friendly name)
    for (const rename of this._renameQueue) {
      const imp = impact[rename.oldId] || { automations: [], scripts: [], dashboards: [] };
      try {
        const wsPayload = { type: 'config/entity_registry/update', entity_id: rename.oldId };
        if (rename.newId && rename.newId !== rename.oldId) wsPayload.new_entity_id = rename.newId;
        if (rename.newName) wsPayload.name = rename.newName;
        await this._hass.callWS(wsPayload);
        results.push({ ...rename, status: 'ok' });
        this._renameLog.unshift({
          time: ts,
          oldId: rename.oldId,
          newId: (rename.newId !== rename.oldId ? rename.newId : rename.oldId) + (rename.newName ? ' (' + rename.newName + ')' : ''),
          status: 'ok',
          impact: imp,
        });
      } catch (e) {
        results.push({ ...rename, status: 'error', error: e.message });
        this._renameLog.unshift({ time: ts, oldId: rename.oldId, newId: rename.newId, status: 'error', error: e.message, impact: imp });
      }
    }

    const ok = results.filter(r => r.status === 'ok').length;
    const fail = results.filter(r => r.status === 'error').length;
    const devCount = Object.keys(this._deviceRenameQueue).length;
    const totalImpact = Object.values(impact).reduce((a, i) => a + i.automations.length + i.scripts.length + i.dashboards.length, 0);
    
    let msg = this._t.renameSuccess.replace('{ok}', ok);
    msg = msg.replace('{devCount}', devCount ? ', ' + devCount + ' ' + (this._lang === 'pl' ? 'urządzeń' : 'devices') : '');
    msg = msg.replace('{fail}', fail > 0 ? `, ${fail} ${this._lang === 'pl' ? 'błędów' : 'errors'}` : '');
    msg = msg.replace('{impact}', totalImpact > 0 ? ` ⚠️ ${totalImpact} ${this._lang === 'pl' ? 'miejsc wymaga aktualizacji (szczegóły w historii).' : 'places require update (details in history).'}` : '');
    
    this._message = {
      type: fail > 0 ? 'warning' : 'success',
      text: msg,
    };
    this._renameQueue = [];
    this._deviceRenameQueue = {};
    this._impactResults = null;
    this._loading = false;
    this._activeTab = 'log';
    this._saveHistoryToStorage();
    await this._loadData();
    this.render();
  }


  render() {
    if (!this._hass) return;
    const devices = this._getFilteredDevices();
    const queueCount = this._renameQueue.length + Object.keys(this._deviceRenameQueue).length;
    const t = this._t;

    this.shadowRoot.innerHTML = `
    <style>${window.HAToolsBentoCSS || ""}
/* === Support / Donation Section (HA Tools split) === */
.donate-section { margin: 20px 0 4px; padding: 18px 20px;  background: var(--donate-bg, linear-gradient(135deg, #fff5f5 0%, #fff0f6 50%, #f8f0ff 100%));  border: 1px solid var(--donate-border, #f3d3e0); border-radius: var(--bento-radius-md, 16px);  display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 14px; }
.donate-section h3 { margin: 0 0 6px; font-size: 15px; color: var(--donate-heading, #be185d); }
.donate-section p  { margin: 0; font-size: 12.5px; line-height: 1.55; color: var(--donate-text, #6b21a8); }
.donate-buttons { display: flex; gap: 8px; flex-wrap: wrap; }
.donate-btn { display: inline-flex; align-items: center; gap: 6px; padding: 8px 14px; border-radius: 10px;  font-weight: 600; font-size: 12.5px; text-decoration: none; transition: transform .15s ease, box-shadow .15s ease; }
.donate-btn:hover { transform: translateY(-1px); box-shadow: 0 4px 10px rgba(0,0,0,0.08); }
.donate-btn.coffee { background: #FFDD00; color: #000; border: 1px solid #e6c700; }
.donate-btn.paypal { background: #0070ba; color: #fff; border: 1px solid #005ea6; }
@media (prefers-color-scheme: dark) {  .donate-section { background: linear-gradient(135deg, #2a1525 0%, #1e1530 50%, #251530 100%); border-color: #4a3555; }  .donate-section h3 { color: #f0c0d8; }  .donate-section p  { color: #d4a0b8; }  .donate-btn.coffee { background: #b8a100; color: #fff; border-color: #8a7a00; }  .donate-btn.paypal { background: #005a96; color: #e0f0ff; border-color: #004a7a; } }
@media (max-width: 600px) {  .donate-section { flex-direction: column; text-align: center; padding: 16px; }  .donate-buttons { justify-content: center; } }
/* === Prereq banner (HA Tools split injector) === */
.prereq-banner { display: flex; align-items: flex-start; gap: 12px; padding: 14px 18px;  border-radius: var(--bento-radius-sm, 10px); margin: 10px 0 14px; font-size: 13px; line-height: 1.5;  border: 1px solid; }
.prereq-banner.prereq-error { background: rgba(239, 68, 68, 0.08); border-color: rgba(239, 68, 68, 0.3); color: #991b1b; }
.prereq-banner.prereq-info  { background: rgba(59, 130, 246, 0.06); border-color: rgba(59, 130, 246, 0.3); color: #1e40af; }
.prereq-banner .prereq-icon { font-size: 22px; flex-shrink: 0; line-height: 1; padding-top: 2px; }
.prereq-banner .prereq-text { flex: 1; min-width: 0; }
.prereq-banner code { background: rgba(0,0,0,0.06); padding: 1px 6px; border-radius: 4px; font-size: 12px; }
.prereq-banner .prereq-cta { display: inline-block; padding: 6px 12px; border-radius: 6px;  background: var(--bento-primary, #3B82F6); color: #fff !important; text-decoration: none;  font-weight: 600; font-size: 12px; flex-shrink: 0; }
.prereq-banner .prereq-cta:hover { background: var(--bento-primary-hover, #2563EB); }
@media (prefers-color-scheme: dark) {  .prereq-banner.prereq-error { background: rgba(239, 68, 68, 0.15); color: #fca5a5; border-color: rgba(239, 68, 68, 0.4); }  .prereq-banner.prereq-info  { background: rgba(59, 130, 246, 0.12); color: #93c5fd; border-color: rgba(59, 130, 246, 0.4); }  .prereq-banner code { background: rgba(255,255,255,0.08); } }
@media (max-width: 600px) {  .prereq-banner { flex-direction: column; align-items: stretch; }  .prereq-banner .prereq-cta { align-self: flex-start; } }


      
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
      .entity-domain { font-size: 10px; padding: 2px 6px; border-radius: 4px; background: rgba(59,130,246,0.12); color: #93C5FD; }

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
      .impact-badge.dashboard { background: rgba(59,130,246,0.15); color: #93C5FD; }
      .impact-badge.scene { background: rgba(16,185,129,0.15); color: #6EE7B7; }
      .queue-actions { display: flex; gap: 8px; margin-top: 16px; justify-content: flex-end; }

      .log-entry { padding: 6px 0; border-bottom: 1px solid rgba(255,255,255,0.04); font-size: 12px; }
      .log-time { color: var(--bento-text-secondary, #94A3B8); font-size: 10px; }
      .log-ok { color: #86EFAC; }
      .log-err { color: #FCA5A5; }

      .empty-state { text-align: center; padding: 40px; color: var(--bento-text-secondary, #94A3B8); }
      .empty-state .icon { font-size: 40px; margin-bottom: 8px; }
      .spinner { display: inline-block; width: 16px; height: 16px; border: 2px solid rgba(255,255,255,0.2); border-top-color: var(--bento-primary, #3B82F6); border-radius: 50%; animation: spin 0.8s linear infinite; }
      @keyframes spin { to { transform: rotate(360deg); } }
    
        @media (prefers-color-scheme: dark) {
          :host {
            --bento-bg: var(--primary-background-color, #1a1a2e);
            --bento-card: var(--card-background-color, #16213e);
            --bento-text: var(--primary-text-color, #e2e8f0);
            --bento-text-secondary: var(--secondary-text-color, #94a3b8);
            --bento-border: var(--divider-color, #334155);
            --bento-shadow-sm: 0 1px 3px rgba(0,0,0,0.3);
            --bento-shadow-md: 0 4px 12px rgba(0,0,0,0.4);
          }
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
        </style>
    <div class="card">
      <h1>📱️ ${t.deviceEntityRenamer}</h1>
      <div class="subtitle">${this._devices.length} ${t.devices.toLowerCase()} • ${this._entities.length} ${t.entities.toLowerCase()}
        <!-- Support / Donation -->
        <div class="donate-section" data-source="ha-tools-split">
          <div class="donate-text">
            <h3>❤️ ${this._lang === 'pl' ? 'Wesprzyj rozwój HA Tools' : 'Support HA Tools Development'}</h3>
            <p>${this._lang === 'pl' ? 'Jeśli to narzędzie ułatwia Ci życie z Home Assistant, rozważ wsparcie projektu. Każda kawa motywuje do dalszego rozwoju!' : 'If this tool makes your Home Assistant life easier, consider supporting the project. Every coffee motivates further development!'}</p>
          </div>
          <div class="donate-buttons">
            <a class="donate-btn coffee" href="https://buymeacoffee.com/macsiem" target="_blank" rel="noopener noreferrer">☕ Buy Me a Coffee</a>
            <a class="donate-btn paypal" href="https://www.paypal.com/donate/?hosted_button_id=Y967H4PLRBN8W" target="_blank" rel="noopener noreferrer">💳 PayPal</a>
          </div>
        </div>
        </div>

      ${this._message ? `<div class="msg ${this._message.type}">${this._loading ? '<span class="spinner"></span> ' : ''}${_esc(this._message.text || '')}</div>` : ''}

      <div class="tabs">
        <button class="tab-button ${this._activeTab === 'devices' ? 'active' : ''}" data-tab="devices">📱 ${t.devices}</button>
        <button class="tab-button ${this._activeTab === 'queue' ? 'active' : ''}" data-tab="queue">📋 ${t.queue}${queueCount > 0 ? ` (${queueCount})` : ''}</button>
        <button class="tab-button ${this._activeTab === 'log' ? 'active' : ''}" data-tab="log">📜 ${t.log}</button>
      </div>

      ${this._activeTab === 'devices' ? this._renderDevicesTab(devices) : ''}
      ${this._activeTab === 'queue' ? this._renderQueueTab() : ''}
      ${this._activeTab === 'log' ? this._renderLogTab() : ''}
    </div>`;

    this._attachEvents();
  }



  _renderDevicesTab(devices) {
    const t = this._t;
    return `
      <div class="search-bar">
        <input type="text" id="searchInput" placeholder="🔍 ${t.searchPlaceholder}" value="${_esc(this._searchQuery)}">
      </div>
      <div class="stats-row">
        <div class="stat-card"><div class="num">${this._devices.length}</div><div class="label">${t.devices}</div></div>
        <div class="stat-card"><div class="num">${this._entities.length}</div><div class="label">${t.entities}</div></div>
        <div class="stat-card"><div class="num">${this._renameQueue.length}</div><div class="label">${t.inQueue}</div></div>
      </div>
      <div class="device-list">
        ${devices.length === 0 ? `<div class="empty-state"><div class="icon">🔍</div>${t.noResults}</div>` :
          devices.map(d => {
            const ents = this._deviceEntities[d.id] || [];
            const isExpanded = this._expandedDevices.has(d.id);
            const isSelected = this._selectedDevice === d.id;
            return `
            <div class="device-item ${isSelected ? 'selected' : ''}">
              <div class="device-header" data-device-id="${d.id}">
                <span class="device-expand ${isExpanded ? 'open' : ''}">▶</span>
                <span class="device-name">${this._getDeviceName(d)}</span>
                <span class="device-meta">${ents.length} ${t.entities.toLowerCase()}</span>
              </div>
              ${isExpanded ? `
              <div class="entity-list">
                ${ents.map(e => {
                  const domain = e.entity_id.split('.')[0];
                  const inQueue = this._renameQueue.some(r => r.oldId === e.entity_id);
                  return `
                  <div class="entity-row">
                    <span class="entity-domain">${domain}</span>
                    <span class="entity-id">${e.entity_id}</span>
                    <span class="entity-name">${e.name || e.original_name || ''}</span>
                    ${inQueue
                      ? '<button class="btn btn-sm btn-danger" data-remove-queue="' + e.entity_id + '" aria-label="Remove">✕</button>'
                      : `<button class="btn btn-sm btn-outline" data-add-single="${e.entity_id}">+ ${t.queue}</button>`
                    }
                  </div>`;
                }).join('')}
              </div>
              ${isSelected ? `
              <div class="prefix-section">
                <h3>📱 ${t.newDeviceName}</h3>
                <div class="prefix-row" style="margin-bottom:12px">
                  <input type="text" id="deviceName" value="${_esc(this._deviceRenameQueue[d.id] || this._getDeviceName(d))}" placeholder="${t.newDeviceName}" style="width:300px;font-family:inherit">
                  <button class="btn btn-primary btn-sm" id="applyDeviceName" data-device-id="${d.id}">${t.changeName}</button>
                </div>
                <h3>📄 ${t.prefixChange}</h3>
                <div class="prefix-row">
                  <input type="text" id="prefixOld" value="${_esc(this._prefixOld || '')}" placeholder="${t.oldPrefix}">
                  <span class="arrow">→</span>
                  <input type="text" id="prefixNew" value="${_esc(this._prefixNew || '')}" placeholder="${t.newPrefix}">
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
      ${devEntries.length ? `<div style="margin-bottom:12px;padding:10px 14px;border-radius:8px;background:rgba(168,85,247,0.08);border:1px solid rgba(168,85,247,0.2);">
        <strong style="font-size:12px;">📱 ${t.devicesToRename}</strong>
        ${devEntries.map(([did, name]) => {
          const dev = this._devices.find(d => d.id === did);
          return `<div style="font-size:12px;margin-top:4px;"><span class="old">${dev ? this._getDeviceName(dev) : did}</span> → <span class="new">${name}</span> <button class="btn btn-sm btn-danger" data-remove-dev-queue="${did}" aria-label="Remove">✕</button></div>`;
        }).join('')}
      </div>` : ''}
      <div class="queue-list">
          ${this._renameQueue.map(r => {
            const imp = this._impactResults ? this._impactResults[r.oldId] : null;
            const hasImpact = imp && (imp.automations.length || imp.scripts.length || imp.dashboards.length || (imp.scenes||[]).length);
            return `<div style="border:1px solid var(--bento-border,#334155);border-radius:8px;padding:12px;margin-bottom:8px;">
              <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">
                <span class="old" style="flex:1;font-family:'JetBrains Mono',monospace;font-size:11px;">${r.oldId}</span>
                <button class="btn btn-sm btn-danger" data-remove-queue="${r.oldId}" aria-label="Remove">✕</button>
              </div>
              <div style="display:flex;align-items:center;gap:8px;">
                <span style="color:var(--bento-text-secondary,#94A3B8);">→</span>
                <span class="new" style="font-family:'JetBrains Mono',monospace;font-size:11px;">${r.newId !== r.oldId ? r.newId : '<span style="opacity:0.4">no entity_id change</span>'}</span>
                ${r.newName ? '<span style="font-size:11px;color:#93C5FD;">📝 ' + r.newName + '</span>' : ''}
              </div>
              ${hasImpact ? `<div style="margin-top:6px;padding-top:6px;border-top:1px solid rgba(255,255,255,0.05);">
                <span style="font-size:10px;color:var(--bento-text-secondary,#94A3B8);">⚠️ ${t.usedIn}</span>
                ${imp.automations.map(a => '<span class="impact-badge automation">⚙ ' + a + '</span>').join('')}
                ${imp.scripts.map(s => '<span class="impact-badge script">📜 ' + s + '</span>').join('')}
                ${imp.dashboards.map(d => '<span class="impact-badge dashboard">📊 ' + d + '</span>').join('')}
                ${(imp.scenes||[]).map(s => '<span class="impact-badge scene">🎬 ' + s + '</span>').join('')}
              </div>` : (imp ? `<div style="margin-top:4px;font-size:10px;color:var(--bento-text-secondary,#94A3B8);">✓ ${t.notUsed}</div>` : '')}
            </div>`;
          }).join('')}
      </div>
      <div class="queue-actions">
        <button class="btn btn-outline" id="clearQueue">🗑️ ${t.clear}</button>
        <button class="btn btn-outline" id="analyzeImpact" ${this._loading ? 'disabled' : ''}>🔍 ${t.analyzeImpact}</button>
        <button class="btn btn-danger" id="executeRenames" ${this._loading ? 'disabled' : ''}>🚀 ${t.executeRenames} (${this._renameQueue.length})</button>
      </div>`;
  }

  _renderLogTab() {
    const t = this._t;
    if (!this._renameLog.length) {
      return `<div class="empty-state"><div class="icon">📜</div>${t.noHistory}</div>`;
    }
    return `
      <div>
        ${this._renameLog.map(l => {
          const imp = l.impact;
          const hasImpact = imp && (imp.automations.length || imp.scripts.length || imp.dashboards.length || (imp.scenes||[]).length);
          return `
          <div class="log-entry" style="padding:8px 0;${hasImpact ? 'padding-bottom:12px;' : ''}">
            <span class="log-time">${l.time}</span>
            <span class="${l.status === 'ok' ? 'log-ok' : 'log-err'}">
              ${l.status === 'ok' ? '✅' : '❌'} ${l.oldId} → ${l.newId}
            </span>
            ${l.error ? `<br><small style="color:#FCA5A5">${l.error}</small>` : ''}
            ${hasImpact ? `<div style="margin-top:4px;padding-left:24px;">
              <span style="font-size:10px;color:var(--bento-text-secondary,#94A3B8);">⚠️ ${t.usedIn}</span>
              ${imp.automations.map(a => '<span class="impact-badge automation">⚙ ' + a + '</span>').join('')}
              ${imp.scripts.map(s => '<span class="impact-badge script">📜 ' + s + '</span>').join('')}
              ${imp.dashboards.map(d => '<span class="impact-badge dashboard">📊 ' + d + '</span>').join('')}
              ${(imp.scenes||[]).map(s => '<span class="impact-badge scene">🎬 ' + s + '</span>').join('')}
            </div>` : ''}
          </div>`;
        }).join('')}
      </div>`;
  }

  _attachEvents() {
    const root = this.shadowRoot;
    const t = this._t;

    // Tab switching
    root.querySelectorAll('.tab-button').forEach(btn => {
      btn.addEventListener('click', () => {
        this._activeTab = btn.dataset.tab;
        history.replaceState(null, '', location.pathname + '#' + this._toolId + '/' + this._activeTab);
        this.render();
      });
    });

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

    // Device rename
    const applyDeviceName = root.getElementById('applyDeviceName');
    if (applyDeviceName) {
      applyDeviceName.addEventListener('click', () => {
        const devId = applyDeviceName.dataset.deviceId;
        const newName = (root.getElementById('deviceName') || {}).value || '';
        if (newName) {
          this._deviceRenameQueue[devId] = newName;
          this._message = { type: 'info', text: t.deviceAdded.replace('{name}', newName) };
          this.render();
        }
      });
    }

    // Add single entity to queue (prompt for new entity_id and optional friendly name)
    root.querySelectorAll('[data-add-single]').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const oldId = btn.dataset.addSingle;
        const domain = oldId.split('.')[0];
        const objId = oldId.split('.')[1] || '';
        const ent = this._entities.find(en => en.entity_id === oldId);
        const currentName = ent ? (ent.name || ent.original_name || '') : '';
        const newObjId = prompt(t.newEntity, objId);
        if (newObjId === null) return; // cancelled
        const newFriendly = prompt(t.newFriendly, currentName);
        if (newFriendly === null) return; // cancelled
        const newId = domain + '.' + (newObjId || objId);
        const nameToSet = (newFriendly && newFriendly !== currentName) ? newFriendly : null;
        if (newId !== oldId || nameToSet) {
          this._addToQueue(oldId, newId, nameToSet);
        }
      });
    });

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
        delete this._deviceRenameQueue[btn.dataset.removeDevQueue];
        this.render();
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

    const executeRenames = root.getElementById('executeRenames');
    if (executeRenames) {
      executeRenames.addEventListener('click', () => {
        if (confirm(t.confirmRename.replace('{count}', this._renameQueue.length))) {
          this._executeRenames();
        }
      });
    }
  }

  disconnectedCallback() {
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
  _dispatch() {
    this.dispatchEvent(new CustomEvent('config-changed', { detail: { config: this._config }, bubbles: true, composed: true }));
  }
  _render() {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display:block; padding:16px; }
        h3 { margin:0 0 16px; font-size:15px; font-weight:600; color:var(--bento-text, var(--primary-text-color,#1e293b)); }
        input { outline:none; transition:border-color .2s; }
        input:focus { border-color:var(--bento-primary, var(--primary-color,#3b82f6)); }
      </style>
      <h3>Entity Renamer</h3>
            <div style="margin-bottom:12px;">
              <label style="display:block;font-weight:500;margin-bottom:4px;font-size:13px;">Title</label>
              <input type="text" id="cf_title" value="${_esc(this._config?.title || 'Entity Renamer')}"
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
window.customCards.push({ type: 'ha-entity-renamer', name: 'Entity Renamer', description: 'Rename entities and devices with propagation to dashboards and automations', preview: false });
