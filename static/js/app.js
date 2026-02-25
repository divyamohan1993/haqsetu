/**
 * HaqSetu -- Main SPA Application
 *
 * A skeuomorphic dashboard for India's government scheme verification
 * platform.  Pure vanilla JS (ES6+), no framework dependencies.
 *
 * Works with the pre-rendered HTML views in index.html:
 *   #/dashboard        -- live stats, trust gauge, source LEDs
 *   #/schemes          -- searchable, filterable scheme explorer
 *   #/scheme/:id       -- full scheme detail with evidence timeline
 *   #/verification     -- verification dashboard with charts and table
 *   #/feedback         -- multi-step citizen feedback form
 */

'use strict';

// =========================================================================
// 1.  CONSTANTS
// =========================================================================

const API_BASE = '/api/v1';

/** Scheme categories from the backend SchemeCategory enum. */
const CATEGORIES = [
    'agriculture', 'health', 'education', 'housing', 'employment',
    'social_security', 'financial_inclusion', 'women_child', 'tribal',
    'disability', 'senior_citizen', 'skill_development', 'infrastructure',
    'other',
];

/** Human-readable labels for verification statuses. */
const STATUS_LABELS = {
    verified:            'Verified',
    partially_verified:  'Partially Verified',
    unverified:          'Unverified',
    disputed:            'Disputed',
    pending:             'Pending',
    revoked:             'Revoked',
};

/** Colour map for verification statuses. */
const STATUS_COLOURS = {
    verified:            '#27ae60',
    partially_verified:  '#f39c12',
    unverified:          '#95a5a6',
    disputed:            '#e74c3c',
    pending:             '#3498db',
    revoked:             '#8e44ad',
};

/** Official verification source names displayed in the UI. */
const SOURCE_LABELS = {
    gazette_of_india:    'Gazette of India',
    india_code:          'India Code',
    parliament_records:  'Parliament Records',
    sansad_parliament:   'Sansad (Parliament)',
    myscheme_gov_in:     'MyScheme.gov.in',
    myscheme_gov:        'MyScheme.gov.in',
    data_gov_in:         'data.gov.in',
    api_setu:            'API Setu',
};


// =========================================================================
// 2.  API CLIENT  -- fetch wrapper with caching, loading, error handling
// =========================================================================

class ApiClient {
    constructor(base = API_BASE) {
        this._base = base;
        this._cache = new Map();          // key -> { data, ts }
        this._cacheTTL = 60_000;          // 60 seconds default TTL
    }

    /**
     * Perform a GET request.  Results are cached for `ttl` ms.
     * @param {string} path   - URL path relative to API_BASE
     * @param {object} params - query string parameters
     * @param {number} ttl    - cache TTL in ms (0 to skip cache)
     */
    async get(path, params = {}, ttl = this._cacheTTL) {
        const url = this._buildUrl(path, params);
        const cacheKey = url.toString();

        // Return from cache if still fresh
        if (ttl > 0) {
            const cached = this._cache.get(cacheKey);
            if (cached && Date.now() - cached.ts < ttl) {
                return cached.data;
            }
        }

        const data = await this._request('GET', url);

        if (ttl > 0) {
            this._cache.set(cacheKey, { data, ts: Date.now() });
        }
        return data;
    }

    /**
     * Perform a POST request (never cached).
     * @param {string} path - URL path relative to API_BASE
     * @param {object} body - JSON body
     */
    async post(path, body = {}) {
        const url = this._buildUrl(path);
        return this._request('POST', url, body);
    }

    /** Clear the entire response cache. */
    clearCache() {
        this._cache.clear();
    }

    // -- internal helpers ---------------------------------------------------

    _buildUrl(path, params = {}) {
        const url = new URL(`${this._base}${path}`, window.location.origin);
        Object.entries(params).forEach(([k, v]) => {
            if (v !== undefined && v !== null && v !== '') {
                url.searchParams.set(k, v);
            }
        });
        return url;
    }

    async _request(method, url, body) {
        const opts = {
            method,
            headers: { 'Content-Type': 'application/json' },
        };
        if (body !== undefined) {
            opts.body = JSON.stringify(body);
        }

        let response;
        try {
            response = await fetch(url, opts);
        } catch (err) {
            Toast.show('Network error -- please check your connection.', 'error');
            throw err;
        }

        if (!response.ok) {
            const detail = await response.json().catch(() => ({}));
            const msg = detail.detail || `Request failed (${response.status})`;
            Toast.show(msg, 'error');
            throw new Error(msg);
        }

        return response.json();
    }
}

const api = new ApiClient();


// =========================================================================
// 3.  TOAST / NOTIFICATION SYSTEM
// =========================================================================

class Toast {
    static _container = null;

    /** Ensure the toast container exists in the DOM. */
    static _ensure() {
        if (!Toast._container) {
            Toast._container = document.getElementById('toast-container');
        }
        if (!Toast._container) {
            Toast._container = document.createElement('div');
            Toast._container.id = 'toast-container';
            Toast._container.className = 'toast-container';
            Toast._container.setAttribute('aria-live', 'assertive');
            document.body.appendChild(Toast._container);
        }
    }

    /**
     * Display a toast notification.
     * @param {string} message
     * @param {'info'|'success'|'error'|'warning'} type
     * @param {number} duration  - ms before auto-dismiss
     */
    static show(message, type = 'info', duration = 4000) {
        Toast._ensure();
        const el = document.createElement('div');
        el.className = `toast toast--${type}`;
        el.textContent = message;
        Toast._container.appendChild(el);

        // Trigger enter animation
        requestAnimationFrame(() => el.classList.add('toast--visible'));

        setTimeout(() => {
            el.classList.remove('toast--visible');
            el.addEventListener('transitionend', () => el.remove());
        }, duration);
    }
}


// =========================================================================
// 4.  SVG GAUGE COMPONENT  -- circular trust-score meter
// =========================================================================

class TrustGauge {
    /**
     * Render an SVG gauge into `container`.
     * @param {HTMLElement} container
     * @param {number}      score      - 0 to 1
     * @param {number}      size       - diameter in px
     * @param {string}      label      - text below the number
     */
    static render(container, score, size = 160, label = 'Trust Score') {
        const radius   = (size / 2) - 14;
        const circumf  = 2 * Math.PI * radius;
        const progress = circumf * (1 - Math.max(0, Math.min(1, score)));
        const pct      = Math.round(score * 100);
        const colour   = TrustGauge._colour(score);

        container.innerHTML = `
            <svg class="trust-gauge" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
                <circle class="trust-gauge__track"
                    cx="${size / 2}" cy="${size / 2}" r="${radius}"
                    stroke="#e0d6c8" stroke-width="10" fill="none" />
                <circle class="trust-gauge__fill"
                    cx="${size / 2}" cy="${size / 2}" r="${radius}"
                    stroke="${colour}" stroke-width="10" fill="none"
                    stroke-dasharray="${circumf}"
                    stroke-dashoffset="${progress}"
                    stroke-linecap="round"
                    transform="rotate(-90 ${size / 2} ${size / 2})" />
                <text x="50%" y="46%" text-anchor="middle"
                    class="trust-gauge__value" fill="${colour}"
                    font-size="${size * 0.22}px" font-weight="700">${pct}%</text>
                <text x="50%" y="62%" text-anchor="middle"
                    class="trust-gauge__label" fill="#7c6f5b"
                    font-size="${size * 0.09}px">${label}</text>
            </svg>`;
    }

    /** Map score 0-1 to a red-amber-green colour. */
    static _colour(s) {
        if (s >= 0.75) return '#27ae60';
        if (s >= 0.5)  return '#f39c12';
        if (s >= 0.25) return '#e67e22';
        return '#e74c3c';
    }
}


// =========================================================================
// 5.  ANIMATED COUNTER
// =========================================================================

/**
 * Animate a numeric counter from its current displayed value to `target`.
 * @param {HTMLElement} el      - element whose textContent will be updated
 * @param {number}      target  - final value
 * @param {number}      dur     - duration in ms
 * @param {string}      suffix  - appended after the number (e.g. '%')
 */
function animateCounter(el, target, dur = 1200, suffix = '') {
    const start   = parseFloat(el.textContent) || 0;
    const delta   = target - start;
    const t0      = performance.now();

    function step(now) {
        const elapsed = now - t0;
        const pct     = Math.min(elapsed / dur, 1);
        // ease-out cubic
        const ease = 1 - Math.pow(1 - pct, 3);
        const current = start + delta * ease;
        el.textContent = (Number.isInteger(target) ? Math.round(current) : current.toFixed(1)) + suffix;
        if (pct < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
}


// =========================================================================
// 6.  UTILITY HELPERS
// =========================================================================

/** Debounce a function by `delay` ms. */
function debounce(fn, delay = 300) {
    let timer;
    return (...args) => {
        clearTimeout(timer);
        timer = setTimeout(() => fn(...args), delay);
    };
}

/** Convert a category slug to a human-readable label. */
function categoryLabel(slug) {
    return (slug || '').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

/** Truncate a string to `len` characters. */
function truncate(str, len = 120) {
    if (!str) return '';
    return str.length > len ? str.slice(0, len) + '...' : str;
}

/** Format an ISO date string for display. */
function fmtDate(iso) {
    if (!iso) return 'N/A';
    try {
        return new Date(iso).toLocaleDateString('en-IN', {
            day: 'numeric', month: 'short', year: 'numeric',
        });
    } catch {
        return iso;
    }
}

/** Create a verification badge stamp element. */
function verificationBadge(status) {
    const label  = STATUS_LABELS[status] || status;
    const colour = STATUS_COLOURS[status] || '#95a5a6';
    return `<span class="badge badge--stamp" style="--badge-clr:${colour}">${label}</span>`;
}

/** Generate a small trust-score bar (inline). */
function trustBar(score) {
    const pct    = Math.round((score || 0) * 100);
    const colour = TrustGauge._colour(score || 0);
    return `
        <div class="trust-bar" title="Trust: ${pct}%">
            <div class="trust-bar__fill" style="width:${pct}%;background:${colour}"></div>
            <span class="trust-bar__label">${pct}%</span>
        </div>`;
}

/** Build a loading spinner HTML string. */
function spinner(text = 'Loading...') {
    return `<div class="loading-spinner"><div class="spinner"></div><p>${text}</p></div>`;
}


// =========================================================================
// 7.  HASH ROUTER  -- shows/hides pre-rendered view sections
// =========================================================================

class Router {
    constructor() {
        /** @type {Array<{pattern: RegExp, handler: Function, keys: string[]}>} */
        this._routes = [];
        window.addEventListener('hashchange', () => this._resolve());
    }

    /**
     * Register a route.
     * @param {string}   path    - e.g. '/scheme/:id'
     * @param {Function} handler - receives an object of matched params
     */
    on(path, handler) {
        const keys = [];
        const pattern = new RegExp(
            '^' + path.replace(/:(\w+)/g, (_, k) => { keys.push(k); return '([^/]+)'; }) + '$',
        );
        this._routes.push({ pattern, handler, keys });
    }

    /** Navigate programmatically. */
    navigate(hash) {
        window.location.hash = hash;
    }

    /** Kick off initial route resolution. */
    start() {
        this._resolve();
    }

    /** @private Resolve the current hash to a registered handler. */
    _resolve() {
        const hash = (window.location.hash || '#/dashboard').slice(1);
        for (const route of this._routes) {
            const m = hash.match(route.pattern);
            if (m) {
                const params = {};
                route.keys.forEach((k, i) => { params[k] = decodeURIComponent(m[i + 1]); });
                route.handler(params);
                return;
            }
        }
        // Fallback to dashboard
        this.navigate('#/dashboard');
    }
}


// =========================================================================
// 8.  APPLICATION CLASS  -- orchestrates views using pre-rendered HTML
// =========================================================================

class HaqSetuApp {
    constructor() {
        this.router = new Router();
        this._viewMode = 'grid';      // scheme explorer grid/list toggle
        this._schemesPage = 1;        // current pagination page
        this._registerRoutes();
        this._bindGlobalListeners();
    }

    // ---- boot -----------------------------------------------------------

    /** Start the app by resolving the initial route and hiding loading overlay. */
    start() {
        this.router.start();
        // Dismiss loading overlay after a short delay
        setTimeout(() => {
            const overlay = document.getElementById('loading-overlay');
            if (overlay) overlay.classList.add('hidden');
        }, 600);
    }

    // ---- routes ---------------------------------------------------------

    _registerRoutes() {
        this.router.on('/dashboard',       () => this._activateView('dashboard'));
        this.router.on('/schemes',         () => this._activateView('schemes'));
        this.router.on('/scheme/:id',      (p) => this._activateView('scheme-detail', p));
        this.router.on('/verification',    () => this._activateView('verification'));
        this.router.on('/feedback',        () => this._activateView('feedback'));
    }

    // ---- view switching -------------------------------------------------

    /**
     * Show the given view section and hide all others.
     * @param {string} viewName - matches the section ID suffix, e.g. 'dashboard' -> 'view-dashboard'
     * @param {object} params   - route params (e.g. { id: 'pm-kisan' })
     */
    _activateView(viewName, params = {}) {
        // Hide all view sections
        document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));

        // Show the target view
        const target = document.getElementById(`view-${viewName}`);
        if (target) {
            target.classList.add('active');
        }

        // Update navbar active tab
        document.querySelectorAll('.nav-tab').forEach(tab => {
            const isActive = tab.dataset.view === viewName;
            tab.classList.toggle('active', isActive);
            tab.setAttribute('aria-selected', isActive ? 'true' : 'false');
        });

        // Trigger the data-loading logic for each view
        switch (viewName) {
            case 'dashboard':
                this._loadDashboardData();
                break;
            case 'schemes':
                this._loadSchemes();
                break;
            case 'scheme-detail':
                this._loadSchemeDetail(params.id);
                break;
            case 'verification':
                this._loadVerificationData();
                break;
            case 'feedback':
                this._initFeedbackForm();
                break;
        }
    }

    // ---- global event listeners -----------------------------------------

    _bindGlobalListeners() {
        // Dark mode toggle
        const darkToggle = document.getElementById('dark-mode-toggle');
        if (darkToggle) {
            darkToggle.addEventListener('click', () => {
                document.body.classList.toggle('dark-mode');
            });
        }

        // Language selector
        const langSelector = document.getElementById('language-selector');
        if (langSelector) {
            langSelector.addEventListener('change', () => {
                Toast.show(`Language changed to ${langSelector.options[langSelector.selectedIndex].text}`, 'info');
            });
        }

        // Hero search
        const heroInput = document.getElementById('hero-search-input');
        const heroBtn   = document.getElementById('hero-search-btn');
        if (heroInput && heroBtn) {
            const doSearch = () => {
                const q = heroInput.value.trim();
                if (q) {
                    this.router.navigate(`#/schemes?q=${encodeURIComponent(q)}`);
                }
            };
            heroBtn.addEventListener('click', doSearch);
            heroInput.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') doSearch();
            });
        }

        // Scheme search and filter controls
        const schemeSearch = document.getElementById('scheme-search');
        const schemeFilter = document.getElementById('scheme-filter-status');
        if (schemeSearch) {
            schemeSearch.addEventListener('input', debounce(() => this._loadSchemes(), 350));
        }
        if (schemeFilter) {
            schemeFilter.addEventListener('change', () => this._loadSchemes());
        }

        // Grid/list view toggle
        const gridBtn = document.getElementById('grid-view-btn');
        const listBtn = document.getElementById('list-view-btn');
        if (gridBtn && listBtn) {
            gridBtn.addEventListener('click', () => {
                this._viewMode = 'grid';
                gridBtn.classList.add('active');
                gridBtn.setAttribute('aria-pressed', 'true');
                listBtn.classList.remove('active');
                listBtn.setAttribute('aria-pressed', 'false');
                const container = document.getElementById('schemes-container');
                if (container) {
                    container.classList.remove('schemes-list');
                    container.classList.add('schemes-grid');
                }
            });
            listBtn.addEventListener('click', () => {
                this._viewMode = 'list';
                listBtn.classList.add('active');
                listBtn.setAttribute('aria-pressed', 'true');
                gridBtn.classList.remove('active');
                gridBtn.setAttribute('aria-pressed', 'false');
                const container = document.getElementById('schemes-container');
                if (container) {
                    container.classList.remove('schemes-grid');
                    container.classList.add('schemes-list');
                }
            });
        }

        // Feedback form character counter
        const fbDesc    = document.getElementById('fb-description');
        const charCount = document.getElementById('fb-char-count');
        if (fbDesc && charCount) {
            fbDesc.addEventListener('input', () => {
                charCount.textContent = fbDesc.value.length;
            });
        }

        // Feedback form submission
        const feedbackForm = document.getElementById('feedback-form');
        if (feedbackForm) {
            feedbackForm.addEventListener('submit', (e) => this._handleFeedbackSubmit(e));
        }
    }


    // =====================================================================
    //  VIEW: DASHBOARD
    // =====================================================================

    async _loadDashboardData() {
        try {
            const data = await api.get('/verification/dashboard');
            this._populateDashboardStats(data);
            this._populateDashboardGauge(data);
            this._populateDashboardSources(data);
            this._populateDashboardActivity(data);
        } catch {
            // Toast already shown by api client
        }
    }

    _populateDashboardStats(data) {
        const pairs = [
            ['stat-total',      data.total_schemes],
            ['stat-verified',   data.verified],
            ['stat-partial',    data.partially_verified],
            ['stat-unverified', data.unverified],
        ];
        pairs.forEach(([id, val]) => {
            const el = document.getElementById(id);
            if (el) animateCounter(el, val ?? 0);
        });
    }

    _populateDashboardGauge(data) {
        const container = document.getElementById('trust-gauge');
        if (!container) return;
        TrustGauge.render(
            container,
            data.average_trust_score ?? 0,
            160,
            'Avg. Trust',
        );
    }

    _populateDashboardSources(data) {
        const container = document.getElementById('source-status-list');
        if (!container) return;

        const health = data.source_health || {};
        let html = '';
        for (const [key, online] of Object.entries(health)) {
            const label   = SOURCE_LABELS[key] || key.replace(/_/g, ' ');
            const ledCls  = online ? 'led-green' : 'led-red';
            const status  = online ? 'Online' : 'Offline';
            html += `
                <div class="source-item" role="listitem">
                    <span class="led ${ledCls}" aria-label="Status: ${status}"></span>
                    <span>${label}</span>
                    <span class="source-weight">Status: ${status}</span>
                </div>`;
        }

        // Only replace content if we got data; otherwise keep the static HTML
        if (html) {
            container.innerHTML = html;
        }
    }

    _populateDashboardActivity(data) {
        const container = document.getElementById('recent-activity');
        if (!container) return;

        const recent = data.recently_verified || [];
        if (recent.length === 0) {
            container.innerHTML = '<p class="placeholder-text">No recent activity.</p>';
            return;
        }

        let html = '';
        recent.forEach(item => {
            html += `
                <div class="activity-item">
                    <a href="#/scheme/${item.scheme_id}" class="activity-link">
                        <strong>${item.scheme_name || item.scheme_id}</strong>
                        ${trustBar(item.trust_score)}
                        <time class="activity-time">${fmtDate(item.last_verified)}</time>
                    </a>
                </div>`;
        });
        container.innerHTML = html;
    }


    // =====================================================================
    //  VIEW: SCHEME EXPLORER
    // =====================================================================

    async _loadSchemes() {
        // Check if there is a query in the URL (from hero search)
        const urlParams = new URLSearchParams(window.location.hash.split('?')[1] || '');
        const urlQuery = urlParams.get('q') || '';

        const searchInput = document.getElementById('scheme-search');
        const statusFilter = document.getElementById('scheme-filter-status');
        const results = document.getElementById('schemes-container');
        if (!results) return;

        // If we came from a URL query, populate the search box
        if (urlQuery && searchInput && !searchInput.value) {
            searchInput.value = urlQuery;
        }

        const q = (searchInput?.value || '').trim();
        const status = statusFilter?.value || '';

        results.innerHTML = '<p class="placeholder-text">Loading schemes...</p>';

        try {
            let schemes = [];

            if (q) {
                const data = await api.get('/schemes/search', { q, top_k: 50 }, 30_000);
                schemes = data.results || [];
            } else {
                const data = await api.get('/schemes', { page_size: 100 }, 30_000);
                schemes = data.schemes || [];
            }

            // Client-side status filter
            if (status) {
                schemes = schemes.filter(s =>
                    (s.verification_status || '').toLowerCase() === status.toLowerCase()
                );
            }

            if (schemes.length === 0) {
                results.innerHTML = '<p class="placeholder-text">No schemes found matching your criteria.</p>';
                return;
            }

            results.innerHTML = schemes.map(s => this._renderSchemeCard(s)).join('');

        } catch {
            results.innerHTML = '<p class="placeholder-text">Failed to load schemes. Please try again.</p>';
        }
    }

    _renderSchemeCard(scheme) {
        const id       = scheme.scheme_id || '';
        const name     = scheme.name || 'Unnamed Scheme';
        const ministry = scheme.ministry || '';
        const cat      = categoryLabel(scheme.category);
        const desc     = truncate(scheme.description || scheme.benefits || '', 140);
        const score    = scheme.trust_score ?? scheme.relevance_score ?? 0;
        const status   = scheme.verification_status || '';

        return `
            <article class="scheme-card paper-card" data-id="${id}" tabindex="0"
                     role="button" aria-label="View ${name}"
                     onclick="window.location.hash='#/scheme/${id}'">
                <div class="scheme-card__header">
                    <h3 class="scheme-card__name">${name}</h3>
                    ${status ? verificationBadge(status) : ''}
                </div>
                <p class="scheme-card__ministry">${ministry}</p>
                <span class="scheme-card__cat">${cat}</span>
                <p class="scheme-card__desc">${desc}</p>
                ${trustBar(score)}
            </article>`;
    }


    // =====================================================================
    //  VIEW: SCHEME DETAIL
    // =====================================================================

    async _loadSchemeDetail(schemeId) {
        const container = document.getElementById('scheme-detail-content');
        if (!container) return;
        container.innerHTML = '<p class="placeholder-text">Loading scheme details...</p>';

        try {
            const [scheme, verification, evidence, changelog] = await Promise.allSettled([
                api.get(`/schemes/${schemeId}`),
                api.get(`/verification/status/${schemeId}`).catch(() => null),
                api.get(`/verification/evidence/${schemeId}`).catch(() => null),
                api.get(`/verification/changelog/${schemeId}`).catch(() => null),
            ]);

            const s = scheme.status === 'fulfilled' ? scheme.value : null;
            const v = verification.status === 'fulfilled' ? verification.value : null;
            const e = evidence.status === 'fulfilled' ? evidence.value : null;
            const c = changelog.status === 'fulfilled' ? changelog.value : null;

            if (!s) {
                container.innerHTML = '<p class="placeholder-text">Scheme not found.</p>';
                return;
            }

            this._renderSchemeDetailContent(container, s, v, e, c);

        } catch {
            container.innerHTML = '<p class="placeholder-text">Failed to load scheme details.</p>';
        }
    }

    _renderSchemeDetailContent(container, scheme, verification, evidence, changelog) {
        const trustScore = verification?.trust_score ?? scheme.trust_score ?? 0;
        const status     = verification?.status ?? scheme.verification_status ?? 'unverified';

        // Build eligibility table rows
        const elig = scheme.eligibility || {};
        let eligRows = '';
        const eligMap = {
            min_age: 'Minimum Age', max_age: 'Maximum Age', gender: 'Gender',
            income_limit: 'Income Limit', category: 'Social Category',
            occupation: 'Occupation', state: 'State', is_bpl: 'BPL Required',
            land_holding_acres: 'Max Land Holding (acres)',
        };
        for (const [k, label] of Object.entries(eligMap)) {
            if (elig[k] !== undefined && elig[k] !== null) {
                let val = elig[k];
                if (typeof val === 'boolean') val = val ? 'Yes' : 'No';
                if (k === 'income_limit') val = `Rs. ${Number(val).toLocaleString('en-IN')}`;
                eligRows += `<tr><td><strong>${label}</strong></td><td>${val}</td></tr>`;
            }
        }
        if (elig.custom_criteria && elig.custom_criteria.length) {
            elig.custom_criteria.forEach(cr => {
                eligRows += `<tr><td><strong>Criteria</strong></td><td>${cr}</td></tr>`;
            });
        }

        // Evidence timeline
        let evidenceHTML = '<p class="placeholder-text">No evidence collected yet.</p>';
        const chain = evidence?.evidence_chain || verification?.evidence_chain || [];
        if (chain.length) {
            evidenceHTML = '<div class="evidence-timeline">' +
                chain.map(ev => `
                    <div class="evidence-card paper-card">
                        <div class="evidence-card__header">
                            <strong>${SOURCE_LABELS[ev.source] || ev.source || 'Unknown'}</strong>
                            <span class="evidence-card__date">${fmtDate(ev.verified_at || ev.document_date)}</span>
                        </div>
                        <p class="evidence-card__title">${ev.title || ''}</p>
                        <p class="evidence-card__excerpt">${truncate(ev.excerpt, 200)}</p>
                        ${ev.source_url ? `<a href="${ev.source_url}" target="_blank" rel="noopener" class="evidence-card__link">View Source</a>` : ''}
                        <span class="evidence-card__weight">Weight: ${(ev.trust_weight ?? 0).toFixed(2)}</span>
                    </div>
                `).join('') +
                '</div>';
        }

        // Changelog
        let changelogHTML = '<p class="placeholder-text">No changes recorded.</p>';
        const changes = changelog?.changes || [];
        if (changes.length) {
            changelogHTML = `
                <div class="table-wrapper">
                    <table class="skeuo-table">
                        <thead><tr><th>Date</th><th>Type</th><th>Field</th><th>Details</th></tr></thead>
                        <tbody>
                            ${changes.map(ch => `
                                <tr>
                                    <td>${fmtDate(ch.detected_at)}</td>
                                    <td>${ch.change_type || ''}</td>
                                    <td>${ch.field_changed || ''}</td>
                                    <td>${ch.new_value ? truncate(ch.new_value, 80) : ''}</td>
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                </div>`;
        }

        container.innerHTML = `
            <div class="detail-grid">
                <!-- Left column: main info -->
                <div class="detail-main">
                    <h2>${scheme.name}</h2>
                    <div class="detail-meta">
                        <span class="scheme-card__cat">${categoryLabel(scheme.category)}</span>
                        ${verificationBadge(status)}
                        ${scheme.ministry ? `<span>${scheme.ministry}</span>` : ''}
                    </div>

                    <div class="detail-section">
                        <h3 class="card-title">Description</h3>
                        <p>${scheme.description || 'No description available.'}</p>
                    </div>

                    <div class="detail-section">
                        <h3 class="card-title">Benefits</h3>
                        <p>${scheme.benefits || 'Not specified.'}</p>
                    </div>

                    <div class="detail-section">
                        <h3 class="card-title">Eligibility</h3>
                        ${eligRows
                            ? `<div class="table-wrapper"><table class="skeuo-table">${eligRows}</table></div>`
                            : '<p class="placeholder-text">No specific criteria listed.</p>'}
                    </div>

                    <div class="detail-section">
                        <h3 class="card-title">Application Process</h3>
                        <p>${scheme.application_process || 'Not specified.'}</p>
                    </div>

                    ${scheme.documents_required && scheme.documents_required.length ? `
                        <div class="detail-section">
                            <h3 class="card-title">Documents Required</h3>
                            <ul>${scheme.documents_required.map(d => `<li>${d}</li>`).join('')}</ul>
                        </div>` : ''}

                    ${scheme.helpline ? `<p>Helpline: <strong>${scheme.helpline}</strong></p>` : ''}
                    ${scheme.website ? `<p>Website: <a href="${scheme.website}" target="_blank" rel="noopener">${scheme.website}</a></p>` : ''}
                </div>

                <!-- Right column: trust gauge + evidence -->
                <aside class="detail-sidebar">
                    <div class="paper-card" id="detail-gauge"></div>

                    ${verification ? `
                        <div class="paper-card">
                            <h3 class="card-title">Source Confirmation</h3>
                            <div class="source-list">
                                <div class="source-item">
                                    <span class="led ${verification.gazette_confirmed ? 'led-green' : 'led-red'}"></span>
                                    <span>Gazette of India</span>
                                </div>
                                <div class="source-item">
                                    <span class="led ${verification.act_confirmed ? 'led-green' : 'led-red'}"></span>
                                    <span>Enabling Act (India Code)</span>
                                </div>
                                <div class="source-item">
                                    <span class="led ${verification.parliament_confirmed ? 'led-green' : 'led-red'}"></span>
                                    <span>Parliament Records</span>
                                </div>
                            </div>
                        </div>` : ''}

                    <a href="#/feedback?scheme_id=${scheme.scheme_id}" class="skeuo-btn btn-primary" style="width:100%;text-align:center;margin-top:1rem;">
                        Report Issue
                    </a>
                </aside>
            </div>

            <!-- Evidence timeline -->
            <div class="detail-section">
                <h3 class="card-title">Verification Evidence</h3>
                ${evidenceHTML}
            </div>

            <!-- Changelog -->
            <div class="detail-section">
                <h3 class="card-title">Change History</h3>
                ${changelogHTML}
            </div>`;

        // Render the trust gauge into the slot
        const gaugeSlot = document.getElementById('detail-gauge');
        if (gaugeSlot) {
            TrustGauge.render(gaugeSlot, trustScore, 180, STATUS_LABELS[status] || 'Trust Score');
        }
    }


    // =====================================================================
    //  VIEW: VERIFICATION DASHBOARD
    // =====================================================================

    async _loadVerificationData() {
        try {
            const data = await api.get('/verification/dashboard');
            this._renderVerifChart(data);
            this._renderVerifTable(data);
        } catch {
            // handled by toast
        }
    }

    /** Render a donut chart using SVG arcs into #verification-chart. */
    _renderVerifChart(data) {
        const container = document.getElementById('verification-chart');
        if (!container) return;

        const slices = [
            { label: 'Verified',            value: data.verified || 0,            colour: STATUS_COLOURS.verified },
            { label: 'Partially Verified',  value: data.partially_verified || 0,  colour: STATUS_COLOURS.partially_verified },
            { label: 'Unverified',          value: data.unverified || 0,          colour: STATUS_COLOURS.unverified },
            { label: 'Disputed',            value: data.disputed || 0,            colour: STATUS_COLOURS.disputed },
        ];

        const total = slices.reduce((s, sl) => s + sl.value, 0) || 1;
        const size = 220;
        const cx = size / 2, cy = size / 2, r = 80, stroke = 32;

        let cumulativeAngle = -90;  // start from top
        let paths = '';
        let legendItems = '';

        slices.forEach(sl => {
            const pct = sl.value / total;
            if (pct === 0) {
                legendItems += `<li class="legend-item"><span class="legend-dot" style="background:${sl.colour}"></span> ${sl.label}: 0</li>`;
                return;
            }
            const angle = pct * 360;
            const startAngle = cumulativeAngle;
            const endAngle   = cumulativeAngle + angle;

            const startRad = (startAngle * Math.PI) / 180;
            const endRad   = (endAngle * Math.PI) / 180;

            const x1 = cx + r * Math.cos(startRad);
            const y1 = cy + r * Math.sin(startRad);
            const x2 = cx + r * Math.cos(endRad);
            const y2 = cy + r * Math.sin(endRad);

            const largeArc = angle > 180 ? 1 : 0;

            paths += `<path d="M ${x1} ${y1} A ${r} ${r} 0 ${largeArc} 1 ${x2} ${y2}"
                             fill="none" stroke="${sl.colour}" stroke-width="${stroke}" />`;

            cumulativeAngle = endAngle;
            legendItems += `<li class="legend-item"><span class="legend-dot" style="background:${sl.colour}"></span> ${sl.label}: ${sl.value}</li>`;
        });

        container.innerHTML = `
            <div class="donut-wrapper">
                <svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
                    ${paths}
                    <text x="${cx}" y="${cy - 6}" text-anchor="middle" font-size="28" font-weight="700" fill="#4a3f35">${total}</text>
                    <text x="${cx}" y="${cy + 16}" text-anchor="middle" font-size="11" fill="#7c6f5b">Total</text>
                </svg>
                <ul class="chart-legend">${legendItems}</ul>
            </div>`;
    }

    _renderVerifTable(data) {
        const tbody = document.getElementById('verification-table-body');
        if (!tbody) return;

        const allSchemes = [
            ...(data.top_verified_schemes || []),
            ...(data.recently_verified || []),
        ];

        // Deduplicate by scheme_id
        const seen = new Set();
        const unique = allSchemes.filter(s => {
            if (seen.has(s.scheme_id)) return false;
            seen.add(s.scheme_id);
            return true;
        });

        if (unique.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" class="placeholder-text">No schemes available.</td></tr>';
            return;
        }

        tbody.innerHTML = unique.map(s => `
            <tr>
                <td><a href="#/scheme/${s.scheme_id}">${s.scheme_name || s.scheme_id}</a></td>
                <td>${verificationBadge(s.status || 'unverified')}</td>
                <td>${trustBar(s.trust_score)}</td>
                <td>${s.sources_confirmed ? s.sources_confirmed.length : '--'}</td>
                <td>${fmtDate(s.last_verified)}</td>
                <td>
                    <button class="skeuo-btn btn-sm trigger-verif"
                            data-id="${s.scheme_id}" data-name="${s.scheme_name || ''}">
                        Verify
                    </button>
                </td>
            </tr>
        `).join('');

        // Wire up verify buttons
        tbody.querySelectorAll('.trigger-verif').forEach(btn => {
            btn.addEventListener('click', async (e) => {
                e.preventDefault();
                btn.disabled = true;
                btn.textContent = 'Queued...';
                try {
                    await api.post('/verification/trigger', null);
                    Toast.show('Verification queued.', 'success');
                } catch {
                    btn.disabled = false;
                    btn.textContent = 'Verify';
                }
            });
        });
    }


    // =====================================================================
    //  VIEW: FEEDBACK FORM
    // =====================================================================

    _initFeedbackForm() {
        // Parse optional scheme context from URL
        const urlParams = new URLSearchParams(window.location.hash.split('?')[1] || '');
        const preSchemeId = urlParams.get('scheme_id') || '';

        // Pre-fill scheme ID if provided in URL
        const schemeInput = document.getElementById('fb-scheme');
        if (schemeInput && preSchemeId) {
            schemeInput.value = preSchemeId;
        }

        // Load feedback stats
        this._loadFeedbackStats();
    }

    async _loadFeedbackStats() {
        const container = document.getElementById('feedback-stats');
        if (!container) return;

        try {
            const data = await api.get('/feedback/stats', {}, 30_000);
            let html = '';
            if (data.total !== undefined) {
                html += `<div class="stat-card"><div class="stat-value">${data.total}</div><div class="stat-label">Total Feedback</div></div>`;
            }
            if (data.by_type) {
                html += '<h4 class="card-title" style="margin-top:1rem;">By Type</h4>';
                for (const [type, count] of Object.entries(data.by_type)) {
                    html += `<div class="source-item"><span>${categoryLabel(type)}</span><span class="source-weight">${count}</span></div>`;
                }
            }
            if (html) {
                container.innerHTML = html;
            }
        } catch {
            // Keep the placeholder text
        }
    }

    async _handleFeedbackSubmit(e) {
        e.preventDefault();
        const form = e.target;

        const body = {
            scheme_id:     document.getElementById('fb-scheme')?.value || null,
            feedback_type: document.getElementById('fb-type')?.value,
            description:   document.getElementById('fb-description')?.value,
            name:          document.getElementById('fb-name')?.value || null,
            phone:         document.getElementById('fb-phone')?.value || null,
            language:      document.getElementById('fb-language')?.value || 'en',
        };

        // Basic validation
        if (!body.feedback_type) {
            Toast.show('Please select a feedback type.', 'warning');
            return;
        }
        if (!body.description || body.description.length < 10) {
            Toast.show('Description must be at least 10 characters.', 'warning');
            return;
        }

        const submitBtn = form.querySelector('button[type="submit"]');
        if (submitBtn) {
            submitBtn.disabled = true;
            submitBtn.textContent = 'Submitting...';
        }

        try {
            const result = await api.post('/feedback', body);
            Toast.show(result.message || 'Feedback submitted successfully!', 'success');
            form.reset();
            // Reset the char counter
            const charCount = document.getElementById('fb-char-count');
            if (charCount) charCount.textContent = '0';
        } catch {
            // Toast already shown
        } finally {
            if (submitBtn) {
                submitBtn.disabled = false;
                submitBtn.textContent = 'Submit Feedback';
            }
        }
    }
}


// =========================================================================
// 9.  BOOTSTRAP
// =========================================================================

document.addEventListener('DOMContentLoaded', () => {
    const app = new HaqSetuApp();
    app.start();
});
