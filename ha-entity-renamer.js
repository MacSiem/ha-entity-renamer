/* HA Tools split — ha-entity-renamer v4.2.11 (2026-09-29) — single-tool standalone repo */
(function() {
'use strict';

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
 * Renames devices/entities and propagates changes across dashboards, automations, scripts, config.
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
  steps: ["Pick an entity, set new ID — entity_registry/update.","Bulk pattern: sensor.old_* → sensor.new_*.","Optional: rewrite Lovelace dashboard refs."]
};
const _LOCAL_SUPPORT_KEY = 'ha-entity-renamer-support-dismissed';
const _LOCAL_DONATE_HTML = '<div class="donate-section" data-source="own-card" style="margin:8px 0 0;padding:4px 0;background:none;border:0;box-shadow:none;min-height:0;display:flex;gap:8px;align-items:center;flex-wrap:wrap;flex-direction:row;justify-content:flex-start;text-align:left"><a href="https://buymeacoffee.com/macsiem" target="_blank" rel="noopener noreferrer" style="font-size:11px;color:var(--secondary-text-color,#64748b);font-weight:400;text-decoration:underline">Optional support for HA Tools</a><button type="button" class="support-dismiss" aria-label="Dismiss support link" style="margin-left:auto;padding:2px 6px;min-height:0;line-height:1;border:0;background:none;color:var(--secondary-text-color,#64748b);cursor:pointer">×</button></div>';
function _localSupportDismissed() { try { return localStorage.getItem(_LOCAL_SUPPORT_KEY) === '1'; } catch (_) { return false; } }
function _bindLocalSupportDismiss(root) { root.querySelector('.support-dismiss')?.addEventListener('click', () => { try { localStorage.setItem(_LOCAL_SUPPORT_KEY, '1'); } catch (_) {} root.querySelector('.donate-section[data-source="own-card"]')?.remove(); }); }
function _localIntroDismissed() {
  try { return localStorage.getItem(_LOCAL_INTRO_KEY) === '1'; } catch(e) { return false; }
}
function _renderLocalIntro() {
  if (_localIntroDismissed()) return '';
  const steps = _LOCAL_INTRO.steps.map(step => '<li>' + _esc(step) + '</li>').join('');
  return '<div class="intro-banner" data-intro="ha-entity-renamer">'
    + '<button class="intro-dismiss" type="button" title="Dismiss" aria-label="Dismiss">✕</button>'
    + '<div class="intro-headline">💡 ' + _esc(_LOCAL_INTRO.headline) + '</div>'
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
    this._renameQueue = []; // {oldId, newId, newName?, entityObj}
    this._previewMode = false;
    this._deviceRenameQueue = {}; // deviceId -> newName
    this._impactResults = null;
    this._loading = false;
    this._message = null;
    this._confirmDialogOpen = false;
    this._lastApplyResult = null;
    this._activeTab = 'devices'; // devices | queue | log
    this._automaticRows = [];
    this._automaticNull = [];
    this._automaticLoaded = false;
    this._renameLog = [];
    this._expandedDevices = new Set();
    this._loadHistoryFromStorage();
  }

  _loadHistoryFromStorage() {
    try {
      const stored = haToolsPersistence.loadSync('entity-renamer-history');
      if (Array.isArray(stored)) {
        this._renameLog = stored;
        return;
      }
      // Backward-compatible fallback for installs that predate the persistence stub.
      const legacy = localStorage.getItem('ha-tools-entity-renamer-history');
      if (legacy) {
        const parsed = JSON.parse(legacy);
        if (Array.isArray(parsed)) this._renameLog = parsed;
      }
    } catch (e) { console.debug('[ha-entity-renamer] caught:', e); }
  }

  _saveHistoryToStorage() {
    try {
      haToolsPersistence.save('entity-renamer-history', this._renameLog);
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
        renameSuccess: 'Zmieniono {ok} encji{devCount}{fail}{impact}. Sprawdź powiązania zmienionych encji.',
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

  async _loadAutomaticIds() {
    if (this._loading || !this._hass) return;
    this._loading = true;
    this._automaticLoaded = false;
    this._message = null;
    this.render();
    try {
      const ids = this._entities.filter(ent => ent.device_id).map(ent => ent.entity_id);
      const mapping = {};
      for (let i = 0; i < ids.length; i += 100) {
        const batch = ids.slice(i, i + 100);
        const result = await this._hass.callWS({ type: 'config/entity_registry/get_automatic_entity_ids', entity_ids: batch });
        for (const id of batch) {
          if (!Object.prototype.hasOwnProperty.call(result, id)) throw new Error('Incomplete HA automatic ID response');
          mapping[id] = result[id];
        }
      }
      const occupied = new Set(this._entities.map(ent => ent.entity_id));
      const seenTargets = new Set();
      const rows = [];
      const noId = [];
      for (const ent of this._entities.filter(entry => entry.device_id)) {
        const target = mapping[ent.entity_id];
        if (target === null) { noId.push(ent); continue; }
        if (!target || target === ent.entity_id) continue;
        const conflict = occupied.has(target) || seenTargets.has(target);
        seenTargets.add(target);
        rows.push({ oldId: ent.entity_id, newId: target, deviceId: ent.device_id, uniqueId: ent.unique_id, conflict });
      }
      this._automaticRows = rows;
      this._automaticNull = noId;
      this._automaticLoaded = true;
    } catch (error) {
      this._automaticRows = [];
      this._automaticNull = [];
      this._message = { type: 'error', text: this._formatError(error) };
    } finally {
      this._loading = false;
      this.render();
    }
  }

  _queueAutomaticIds(deviceId = null, oldId = null) {
    const queued = new Set(this._renameQueue.map(row => row.oldId));
    for (const row of this._automaticRows) {
      if (row.conflict || queued.has(row.oldId)) continue;
      if (deviceId && row.deviceId !== deviceId) continue;
      if (oldId && row.oldId !== oldId) continue;
      this._renameQueue.push({ ...row, automatic: true, newName: null });
      queued.add(row.oldId);
    }
    this._activeTab = 'queue';
    this._impactResults = null;
    this.render();
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
    this._confirmDialogOpen = false;
    this._lastApplyResult = null;
    if (this._renameQueue.some(r => r.oldId === oldId)) {
      this._renameQueue = this._renameQueue.map(r => r.oldId === oldId ? { ...r, newId, ...(newName !== undefined ? { newName } : {}) } : r);
    } else {
      const ent = this._entities.find(e => e.entity_id === oldId);
      this._renameQueue.push({ oldId, newId, newName: newName || null, entity: ent });
    }
    this.render();
  }

  _removeFromQueue(oldId) {
    this._confirmDialogOpen = false;
    this._lastApplyResult = null;
    this._renameQueue = this._renameQueue.filter(r => r.oldId !== oldId);
    this.render();
  }

  _clearQueue() {
    this._confirmDialogOpen = false;
    this._lastApplyResult = null;
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
    let searchResults;
    try { searchResults = await this._loadRelatedResults(this._renameQueue); }
    catch (error) {
      this._loading = false;
      this._message = { type: 'error', text: 'Impact lookup failed: ' + this._formatError(error) };
      this.render();
      return;
    }

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
      const dashboardText = lovelaceConfig.map(dash => ({ title: dash.title || dash.url_path || 'default', text: JSON.stringify(dash.config || {}) }));
      for (const rename of this._renameQueue) {
        if (!impact[rename.oldId]) continue;
        for (const dash of dashboardText) {
          if (dash.text.includes(rename.oldId)) {
            impact[rename.oldId].dashboards.push(dash.title);
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

  async _loadRelatedResults(rows) {
    const results = [];
    for (let i = 0; i < rows.length; i += 20) {
      const batch = rows.slice(i, i + 20);
      const related = await Promise.all(batch.map(async row => ({
        oldId: row.oldId,
        result: await this._hass.callWS({ type: 'search/related', item_type: 'entity', item_id: row.oldId })
      })));
      results.push(...related);
    }
    return results;
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

  _formatError(e) {
    if (!e) return 'Unknown error';
    if (typeof e === 'string') return e;
    if (e.message) return e.message;
    try { return JSON.stringify(e); } catch(err) { return String(e); }
  }

  async _applyEntityRename(rename) {
    const entityIdChanged = rename.newId && rename.newId !== rename.oldId;
    const targetEntityId = entityIdChanged ? rename.newId : rename.oldId;
    if (entityIdChanged) {
      await this._hass.callWS({
        type: 'config/entity_registry/update',
        entity_id: rename.oldId,
        new_entity_id: rename.newId,
      });
    }
    if (rename.newName) {
      await this._hass.callWS({
        type: 'config/entity_registry/update',
        entity_id: targetEntityId,
        name: rename.newName,
      });
    }
  }

  async _revalidateAutomaticQueue() {
    const automatic = this._renameQueue.filter(row => row.automatic);
    const failures = new Map();
    if (!automatic.length) return failures;
    try {
      const current = await this._hass.callWS({ type: 'config/entity_registry/list' });
      const byId = new Map(current.map(row => [row.entity_id, row]));
      const ids = automatic.map(row => row.oldId);
      const targets = {};
      for (let i = 0; i < ids.length; i += 100) {
        Object.assign(targets, await this._hass.callWS({
          type: 'config/entity_registry/get_automatic_entity_ids', entity_ids: ids.slice(i, i + 100)
        }));
      }
      const reserved = new Set();
      for (const row of automatic) {
        const entry = byId.get(row.oldId);
        if (!entry || entry.unique_id !== row.uniqueId || entry.device_id !== row.deviceId) {
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
    if (this._loading || (!this._renameQueue.length && !Object.keys(this._deviceRenameQueue || {}).length)) return;
    this._confirmDialogOpen = true;
    this._lastApplyResult = null;
    this._activeTab = 'queue';
    this.render();
  }

  async _executeRenames(confirmed = false) {
    if (!this._renameQueue.length && !Object.keys(this._deviceRenameQueue).length) return;
    if (this._hass?.user?.is_admin === false) {
      this._message = { type: 'error', text: this._t.adminRequired };
      this.render();
      return;
    }
    if (!confirmed) {
      this._showRenameConfirmation();
      return;
    }
    this._confirmDialogOpen = false;
    this._lastApplyResult = null;
    this._loading = true;
    this._message = { type: 'info', text: this._t.analyzing2 };
    this.render();
    const automaticFailures = await this._revalidateAutomaticQueue();

    // Auto-run impact analysis before execution using search/related
    let impact = {};
    try {
      const hass = this._hass;
      const searchResults = await this._loadRelatedResults(this._renameQueue.filter(row => !automaticFailures.has(row.oldId)));
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
      const dashboardText = lovelaceConfig.map(dash => ({ title: dash.title || dash.url_path || 'default', text: JSON.stringify(dash.config || {}) }));
      for (const rename of this._renameQueue) {
        if (!impact[rename.oldId]) continue;
        for (const dash of dashboardText) {
          if (dash.text.includes(rename.oldId)) {
            impact[rename.oldId].dashboards.push(dash.title);
          }
        }
      }
    } catch(e) {
      this._loading = false;
      this._message = { type: 'error', text: 'Impact lookup failed; no changes applied: ' + this._formatError(e) };
      this.render();
      return;
    }

    const results = [];
    const deviceResults = [];
    const ts = new Date().toLocaleTimeString();

    // 1. Rename devices first
    for (const [devId, newName] of Object.entries(this._deviceRenameQueue)) {
      try {
        await this._hass.callWS({ type: 'config/device_registry/update', device_id: devId, name_by_user: newName });
        deviceResults.push({ devId, newName, status: 'ok' });
        this._renameLog.unshift({ time: ts, oldId: '📱 ' + devId.substring(0, 8) + '...', newId: '📱 ' + newName, status: 'ok', impact: null });
      } catch (e) {
        const error = this._formatError(e);
        deviceResults.push({ devId, newName, status: 'error', error });
        this._renameLog.unshift({ time: ts, oldId: '📱 device', newId: '📱 ' + newName, status: 'error', error, impact: null });
      }
    }

    // 2. Rename entities (entity_id + optional friendly name)
    for (const rename of this._renameQueue) {
      const imp = impact[rename.oldId] || { automations: [], scripts: [], dashboards: [] };
      try {
        if (automaticFailures.has(rename.oldId)) throw new Error(automaticFailures.get(rename.oldId));
        await this._applyEntityRename(rename);
        results.push({ ...rename, status: 'ok' });
        this._renameLog.unshift({
          time: ts,
          oldId: rename.oldId,
          newId: (rename.newId !== rename.oldId ? rename.newId : rename.oldId) + (rename.newName ? ' (' + rename.newName + ')' : ''),
          status: 'ok',
          impact: imp,
        });
      } catch (e) {
        const error = this._formatError(e);
        results.push({ ...rename, status: 'error', error });
        this._renameLog.unshift({ time: ts, oldId: rename.oldId, newId: rename.newId, status: 'error', error, impact: imp });
      }
    }

    const ok = results.filter(r => r.status === 'ok').length;
    const fail = results.filter(r => r.status === 'error').length;
    const devOk = deviceResults.filter(r => r.status === 'ok').length;
    const devFail = deviceResults.filter(r => r.status === 'error').length;
    const devCount = deviceResults.length;
    const totalImpact = Object.values(impact).reduce((a, i) => a + i.automations.length + i.scripts.length + i.dashboards.length, 0);
    
    let msg = this._t.renameSuccess.replace('{ok}', ok);
    msg = msg.replace('{devCount}', devCount ? ', ' + devCount + ' ' + (this._lang === 'pl' ? 'urządzeń' : 'devices') : '');
    msg = msg.replace('{fail}', fail + devFail > 0 ? `, ${fail + devFail} ${this._lang === 'pl' ? 'błędów' : 'errors'}` : '');
    msg = msg.replace('{impact}', totalImpact > 0 ? ` ⚠️ ${totalImpact} ${this._lang === 'pl' ? 'miejsc wymaga aktualizacji (szczegóły w historii).' : 'places require update (details in history).'}` : '');
    
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
    <style>${HA_ENTITY_RENAMER_BENTO_CSS}
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
        grid-column: 1 / -1; color: #93C5FD; font-family: inherit; font-size: 10px;
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
        </style>
    ${_renderLocalIntro()}
    <div class="card">
      <h1>📱️ ${t.deviceEntityRenamer}</h1>
      <div class="subtitle">${this._devices.length} ${t.devices.toLowerCase()} • ${this._entities.length} ${t.entities.toLowerCase()}
        </div>

      ${this._message ? `<div class="msg ${this._message.type}">${this._loading ? '<span class="spinner"></span> ' : ''}${_esc(this._message.text || '')}</div>` : ''}
      ${this._renderApplyResult()}
      ${this._confirmDialogOpen ? this._renderRenameConfirmation() : ''}

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
    </div>
    ${this._hass?.user?.is_admin && this._config?.show_support !== false && !_localSupportDismissed() ? _LOCAL_DONATE_HTML : ''}`;

    _bindLocalIntroDismiss(this.shadowRoot);
    _bindLocalSupportDismiss(this.shadowRoot);
    this._attachEvents();
  }



  _renderRenameConfirmation() {
    // render() calls this whenever _confirmDialogOpen is true (Apply Changes).
    // It was never implemented, so the call threw "is not a function" and
    // blanked the card — same regression class as _renderApplyResult below.
    const t = this._t;
    const queue = this._renameQueue || [];
    const idChanges = queue.filter(r => r.newId && r.newId !== r.oldId);
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
    const devRows = devEntries.slice(0, MAX).map(([, name]) => `
        <div style="${rowStyle}"><span>&#128295;</span><span>${_esc(String(name))}</span></div>`).join('');
    const fill = (s, n) => _esc(String(s || '').replace('{count}', n));
    return `
      <div class="confirm-dialog" style="border:1px solid var(--bento-primary,#3B82F6);border-radius:var(--bento-radius-md,14px);background:var(--bento-card,#fff);padding:16px 18px;margin:12px 0;box-shadow:var(--bento-shadow-md)">
        <div style="font-weight:700;font-size:14px;margin-bottom:6px">&#9888;&#65039; ${_esc(t.confirmRenameTitle)}</div>
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
    const groups = new Map();
    for (const row of this._automaticRows) {
      if (!groups.has(row.deviceId)) groups.set(row.deviceId, []);
      groups.get(row.deviceId).push(row);
    }
    const canQueue = this._automaticRows.some(row => !row.conflict);
    const deviceHtml = [...groups].map(([deviceId, rows]) => {
      const device = this._devices.find(item => item.id === deviceId);
      return `<details style="border:1px solid var(--bento-border,#ddd);border-radius:8px;padding:8px;margin:8px 0">
        <summary>${_esc(device ? this._getDeviceName(device) : deviceId)} (${rows.length})</summary>
        ${rows.map(row => `<div class="rename-preview-row" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <code>${_esc(row.oldId)}</code> → <code>${_esc(row.newId)}</code>
          ${row.conflict ? `<span>${t.automaticConflict}</span>` : `<button class="btn btn-sm btn-outline" data-auto-entity="${_esc(row.oldId)}">+ ${t.queue}</button>`}
        </div>`).join('')}
        <button class="btn btn-sm btn-outline" data-auto-device="${_esc(deviceId)}" ${rows.some(row => !row.conflict) ? '' : 'disabled'}>${t.addDeviceAutomatic}</button>
      </details>`;
    }).join('');
    return `<div class="automatic-tab">
      <p>${t.automaticInfo}</p>
      <button class="btn btn-outline" id="loadAutomatic" ${this._loading ? 'disabled' : ''}>${this._loading ? '…' : t.loadAutomatic}</button>
      ${this._automaticLoaded ? `<p>${this._automaticRows.length} ${t.automatic}; ${this._automaticNull.length} ${t.automaticUnknown}</p>
        ${canQueue ? `<button class="btn btn-primary" id="queueAllAutomatic">${t.addAllAutomatic}</button>` : `<p>${t.automaticEmpty}</p>`}
        ${deviceHtml}
        ${this._automaticNull.length ? `<details><summary>${t.automaticUnknown} (${this._automaticNull.length})</summary>${this._automaticNull.map(ent => `<div><code>${_esc(ent.entity_id)}</code></div>`).join('')}</details>` : ''}` : ''}
    </div>`;
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
              <div class="device-header" data-device-id="${_esc(d.id)}">
                <span class="device-expand ${isExpanded ? 'open' : ''}">▶</span>
                <span class="device-name">${_esc(this._getDeviceName(d))}</span>
                <span class="device-meta">${ents.length} ${t.entities.toLowerCase()}</span>
              </div>
              ${isExpanded ? `
              <div class="entity-list">
                ${ents.map(e => {
                  const domain = e.entity_id.split('.')[0];
                  const inQueue = this._renameQueue.some(r => r.oldId === e.entity_id);
                  return `
                  <div class="entity-row">
                    <span class="entity-domain">${_esc(domain)}</span>
                    <span class="entity-id">${_esc(e.entity_id)}</span>
                    <span class="entity-name">${_esc(e.name || e.original_name || '')}</span>
                    ${inQueue
                      ? '<button class="btn btn-sm btn-danger" data-remove-queue="' + _esc(e.entity_id) + '" aria-label="Remove">✕</button>'
                      : `<button class="btn btn-sm btn-outline" data-add-single="${_esc(e.entity_id)}">+ ${t.queue}</button>`
                    }
                  </div>`;
                }).join('')}
              </div>
              ${isSelected ? `
              <div class="prefix-section">
                <h3>📱 ${t.newDeviceName}</h3>
                <div class="prefix-row" style="margin-bottom:12px">
                  <input type="text" id="deviceName" value="${_esc(this._deviceRenameQueue[d.id] || this._getDeviceName(d))}" placeholder="${t.newDeviceName}" style="width:300px;font-family:inherit">
                  <button class="btn btn-primary btn-sm" id="applyDeviceName" data-device-id="${_esc(d.id)}">${t.changeName}</button>
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
      <div class="queue-actions">
        <button class="btn btn-outline" id="clearQueue">🗑️ ${t.clear}</button>
        <button class="btn btn-outline" id="analyzeImpact" ${this._loading ? 'disabled' : ''}>🔍 ${t.analyzeImpact}</button>
        <button class="btn btn-danger" id="executeRenames" ${this._loading || this._hass?.user?.is_admin === false ? 'disabled' : ''} ${this._loading ? 'aria-busy="true"' : ''}>🚀 ${t.executeRenames} (${this._renameQueue.length})</button>
        ${this._hass?.user?.is_admin === false ? `<span>${t.adminRequired}</span>` : ''}
      </div>
      ${devEntries.length ? `<div style="margin-bottom:12px;padding:10px 14px;border-radius:8px;background:rgba(168,85,247,0.08);border:1px solid rgba(168,85,247,0.2);">
        <strong style="font-size:12px;">📱 ${t.devicesToRename}</strong>
        ${devEntries.map(([did, name]) => {
          const dev = this._devices.find(d => d.id === did);
          return `<div style="font-size:12px;margin-top:4px;"><span class="old">${_esc(dev ? this._getDeviceName(dev) : did)}</span> → <span class="new">${_esc(name)}</span> <button class="btn btn-sm btn-danger" data-remove-dev-queue="${_esc(did)}" aria-label="Remove">✕</button></div>`;
        }).join('')}
      </div>` : ''}
      <div class="queue-list">
          ${this._renameQueue.map(r => {
            const imp = this._impactResults ? this._impactResults[r.oldId] : null;
            const hasImpact = imp && (imp.automations.length || imp.scripts.length || imp.dashboards.length || (imp.scenes||[]).length);
            return `<div style="border:1px solid var(--bento-border,#334155);border-radius:8px;padding:12px;margin-bottom:8px;">
              <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">
                <span class="old" style="flex:1;font-family:'JetBrains Mono',monospace;font-size:11px;">${_esc(r.oldId)}</span>
                <button class="btn btn-sm btn-danger" data-remove-queue="${_esc(r.oldId)}" aria-label="Remove">✕</button>
              </div>
              <div style="display:flex;align-items:center;gap:8px;">
                <span style="color:var(--bento-text-secondary,#94A3B8);">→</span>
                <span class="new" style="font-family:'JetBrains Mono',monospace;font-size:11px;">${r.newId !== r.oldId ? _esc(r.newId) : '<span style="opacity:0.4">no entity_id change</span>'}</span>
                ${r.newName ? '<span style="font-size:11px;color:#93C5FD;">📝 ' + _esc(r.newName) + '</span>' : ''}
              </div>
              ${hasImpact ? `<div style="margin-top:6px;padding-top:6px;border-top:1px solid rgba(255,255,255,0.05);">
                <span style="font-size:10px;color:var(--bento-text-secondary,#94A3B8);">⚠️ ${t.usedIn}</span>
                ${imp.automations.map(a => '<span class="impact-badge automation">⚙ ' + _esc(a) + '</span>').join('')}
                ${imp.scripts.map(s => '<span class="impact-badge script">📜 ' + _esc(s) + '</span>').join('')}
                ${imp.dashboards.map(d => '<span class="impact-badge dashboard">📊 ' + _esc(d) + '</span>').join('')}
                ${(imp.scenes||[]).map(s => '<span class="impact-badge scene">🎬 ' + _esc(s) + '</span>').join('')}
              </div>` : (imp ? `<div style="margin-top:4px;font-size:10px;color:var(--bento-text-secondary,#94A3B8);">✓ ${t.notUsed}</div>` : '')}
            </div>`;
          }).join('')}
      </div>
`;
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
          const automations = Array.isArray(imp?.automations) ? imp.automations : [];
          const scripts = Array.isArray(imp?.scripts) ? imp.scripts : [];
          const dashboards = Array.isArray(imp?.dashboards) ? imp.dashboards : [];
          const scenes = Array.isArray(imp?.scenes) ? imp.scenes : [];
          const hasImpact = automations.length || scripts.length || dashboards.length || scenes.length;
          return `
          <div class="log-entry" style="padding:8px 0;${hasImpact ? 'padding-bottom:12px;' : ''}">
            <span class="log-time">${_esc(l.time)}</span>
            <span class="${l.status === 'ok' ? 'log-ok' : 'log-err'}">
              ${l.status === 'ok' ? '✅' : '❌'} ${_esc(l.oldId)} → ${_esc(l.newId)}
            </span>
            ${l.error ? `<br><small style="color:#FCA5A5">${_esc(l.error)}</small>` : ''}
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

    // Tab switching
    root.querySelectorAll('.tab-button').forEach(btn => {
      btn.addEventListener('click', () => {
        this._activeTab = btn.dataset.tab;
        history.replaceState(null, '', location.pathname + '#' + this._toolId + '/' + this._activeTab);
        this.render();
      });
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

    // Device rename
    const applyDeviceName = root.getElementById('applyDeviceName');
    if (applyDeviceName) {
      applyDeviceName.addEventListener('click', () => {
        const devId = applyDeviceName.dataset.deviceId;
        const newName = (root.getElementById('deviceName') || {}).value || '';
        if (newName) {
          this._confirmDialogOpen = false;
          this._lastApplyResult = null;
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
        this._confirmDialogOpen = false;
        this._lastApplyResult = null;
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

    const cancelRenameDialog = root.getElementById('cancelRenameDialog');
    if (cancelRenameDialog) {
      cancelRenameDialog.addEventListener('click', () => {
        this._confirmDialogOpen = false;
        this.render();
      });
    }

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
