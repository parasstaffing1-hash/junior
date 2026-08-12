/**
 * Automated Data Analyst - Frontend Application Logic
 */

// Significance tests and trained models need a realistic sample to say anything
// meaningful, so demonstration builds use a full fixture rather than a token one.
const PROJECT_FIXTURE_ROWS = 400;

const App = {
    currentDatasetId: null,
    currentFile: null,
    currentDashboardTemplateId: 'executive',
    dashboardFilters: {},
    dashboardCharts: [],
    currentBIReport: null,
    currentColumns: [],
    registeredModels: [],
    currentInfographic: null,
    geographicCatalog: null,
    geographicBoundaries: [],
    savedGeographicMaps: [],
    currentGeographicMap: null,
    geographicMapInstance: null,
    infographicBoundaries: [],
    staffControlCenterData: null,
    healthImprovementPlan: null,
    biReadinessProfile: null,
    conversationId: null,
    conversationHistory: [],

    /** Add deployment credentials to API calls without persisting them in the database. */
    apiFetch(input, options = {}) {
        const headers = new Headers(options.headers || {});
        try {
            const apiKey = window.sessionStorage.getItem('automated-data-analyst.api-key');
            const tenantId = window.sessionStorage.getItem('automated-data-analyst.tenant-id');
            if (apiKey && !headers.has('Authorization') && !headers.has('X-API-Key')) headers.set('Authorization', `Bearer ${apiKey}`);
            if (tenantId && !headers.has('X-Tenant-ID')) headers.set('X-Tenant-ID', tenantId);
        } catch (_) {
            // Private browsing or storage restrictions should not break local mode.
        }
        const nativeFetch = window.__jdaNativeFetch || window.fetch.bind(window);
        return nativeFetch(input, { ...options, headers });
    },

    // Views
    views: {
        home: document.getElementById('view-home'),
        overview: document.getElementById('view-dataset-overview'),
        results: document.getElementById('view-results-shell'),
        settings: document.getElementById('view-settings')
    },

    init() {
        // Existing feature modules use fetch directly; wrap it once so every
        // API request carries the current deployment tenant/session credentials.
        if (!window.__jdaNativeFetch) {
            window.__jdaNativeFetch = window.fetch.bind(window);
            window.fetch = (...args) => this.apiFetch(...args);
        }
        try {
            this.currentDashboardTemplateId = window.localStorage.getItem('automated-data-analyst.dashboard-template') || this.currentDashboardTemplateId;
        } catch (_) {
            // Template selection is an enhancement; the dashboard still works when storage is unavailable.
        }
        this.setupDragAndDrop();
        this.loadRecentDatasets();
        this.loadProjectCatalog();
        this.setupInfographicMaker();
        window.addEventListener('maplibre-ready', () => {
            if (this.currentGeographicMap) this.renderGeographicMap(this.currentGeographicMap);
        });
        window.addEventListener('echarts-ready', () => {
            const dashboard = this.currentBIReport?.dashboard;
            if (dashboard) this.renderDashboardCharts(dashboard.widgets || [], dashboard.template || {});
        });
    },

    navigate(viewName) {
        Object.values(this.views).forEach(v => {
            if (v) {
                v.classList.remove('active');
                v.classList.add('hidden');
            }
        });
        if (this.views[viewName]) {
            this.views[viewName].classList.remove('hidden');
            this.views[viewName].classList.add('active');
        }
        this.syncShell(viewName);
    },

    openWorkspace(viewName) {
        // Sidebar analysis entries open the workspace view first, then the panel.
        this.navigate('results');
        this.switchView(viewName);
    },

    openSettings() {
        this.navigate('settings');
        this.loadSettings();
    },

    syncShell(viewName) {
        // Keep the Notion-style breadcrumb and sidebar selection in sync.
        const breadcrumb = document.getElementById('breadcrumb-current');
        if (breadcrumb) {
            const labels = { home: 'Home', overview: 'Dataset', results: 'Analyst Workspace', settings: 'Settings' };
            if (viewName === 'overview') {
                const name = document.getElementById('overview-filename');
                breadcrumb.textContent = (name && name.textContent.trim()) || 'Dataset';
            } else {
                breadcrumb.textContent = labels[viewName] || 'Home';
            }
        }
        const homeItems = ['side-nav-home', 'side-nav-get-data'];
        homeItems.forEach(id => {
            const el = document.getElementById(id);
            if (!el) return;
            if (viewName === 'home' && id === 'side-nav-home') el.classList.add('active');
            else el.classList.remove('active');
        });
        const settingsItem = document.getElementById('nav-settings');
        if (settingsItem) settingsItem.classList.toggle('active', viewName === 'settings');
    },

    loadSettings() {
        const template = document.getElementById('settings-dashboard-template');
        if (template) template.value = this.currentDashboardTemplateId || 'executive';
        const runtime = document.getElementById('settings-runtime-status');
        if (runtime) runtime.textContent = this.currentDatasetId ? `Dataset selected: ${this.currentDatasetId}` : 'Local workspace';
        const tenant = document.getElementById('settings-tenant-id');
        try {
            if (tenant) tenant.value = window.sessionStorage.getItem('automated-data-analyst.tenant-id') || '';
        } catch (_) { /* keep the field empty when session storage is unavailable */ }
        this.refreshSettingsStatus();
    },

    async refreshSettingsStatus() {
        const status = document.getElementById('settings-api-status');
        if (!status) return;
        status.className = 'status-chip warning';
        status.textContent = 'Checking...';
        try {
            let endpoint = '/health/ready';
            try {
                endpoint = window.sessionStorage.getItem('automated-data-analyst.api-key') ? '/api/v1/security/session' : endpoint;
            } catch (_) { /* fall back to public health */ }
            const response = await this.apiFetch(endpoint, { cache: 'no-store' });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload.detail || 'API unavailable');
            status.className = 'status-chip success';
            status.textContent = payload.tenant_id ? `Authenticated: ${payload.tenant_id}` : (payload.status === 'ready' ? 'Ready' : 'Connected');
        } catch (error) {
            status.className = 'status-chip danger';
            status.textContent = 'Unavailable';
            this.showToast(error.message || 'API connection failed.');
        }
    },

    saveSettings() {
        const template = document.getElementById('settings-dashboard-template')?.value || 'executive';
        this.currentDashboardTemplateId = template;
        try {
            window.localStorage.setItem('automated-data-analyst.dashboard-template', template);
        } catch (_) {
            // Keep the setting active for this session if browser storage is unavailable.
        }
        const status = document.getElementById('settings-save-status');
        if (status) status.textContent = 'Saved locally';
        this.showToast('Workspace settings saved.');
        if (this.currentDatasetId) this.selectDashboardTemplate(template);
    },

    saveSessionCredentials() {
        const apiKey = document.getElementById('settings-api-key')?.value.trim() || '';
        const tenantId = document.getElementById('settings-tenant-id')?.value.trim() || '';
        try {
            if (apiKey) window.sessionStorage.setItem('automated-data-analyst.api-key', apiKey);
            else window.sessionStorage.removeItem('automated-data-analyst.api-key');
            if (tenantId) window.sessionStorage.setItem('automated-data-analyst.tenant-id', tenantId);
            else window.sessionStorage.removeItem('automated-data-analyst.tenant-id');
            const input = document.getElementById('settings-api-key');
            if (input) input.value = '';
            const status = document.getElementById('settings-credentials-status');
            if (status) status.textContent = apiKey || tenantId ? 'Session access saved' : 'Session access cleared';
            this.showToast(apiKey || tenantId ? 'Session access saved for this tab.' : 'Session access cleared.');
            this.refreshSettingsStatus();
        } catch (_) {
            this.showToast('Browser session storage is unavailable.');
        }
    },

    clearSessionCredentials() {
        try {
            window.sessionStorage.removeItem('automated-data-analyst.api-key');
            window.sessionStorage.removeItem('automated-data-analyst.tenant-id');
        } catch (_) { /* ignore restricted storage */ }
        const key = document.getElementById('settings-api-key');
        const tenant = document.getElementById('settings-tenant-id');
        const status = document.getElementById('settings-credentials-status');
        if (key) key.value = '';
        if (tenant) tenant.value = '';
        if (status) status.textContent = 'Session access cleared';
        this.showToast('Session access cleared.');
        this.refreshSettingsStatus();
    },

    resetSettings() {
        this.currentDashboardTemplateId = 'executive';
        try {
            window.localStorage.removeItem('automated-data-analyst.dashboard-template');
        } catch (_) {
            // The in-memory default still applies when storage is unavailable.
        }
        const template = document.getElementById('settings-dashboard-template');
        if (template) template.value = 'executive';
        const status = document.getElementById('settings-save-status');
        if (status) status.textContent = 'Defaults restored';
        this.showToast('Workspace defaults restored.');
    },

    showToast(msg) {
        const toast = document.getElementById('toast');
        toast.textContent = msg;
        toast.classList.add('show');
        setTimeout(() => toast.classList.remove('show'), 3000);
    },

    setupDragAndDrop() {
        const dropZone = document.getElementById('drop-zone');
        const fileInput = document.getElementById('file-input-csv') || document.querySelector('input[type="file"]');
        if (!dropZone || !fileInput) return;
        
        dropZone.addEventListener('click', () => fileInput.click());
        
        dropZone.addEventListener('dragover', (e) => {
            e.preventDefault();
            dropZone.classList.add('dragover');
        });
        
        dropZone.addEventListener('dragleave', () => {
            dropZone.classList.remove('dragover');
        });
        
        dropZone.addEventListener('drop', (e) => {
            e.preventDefault();
            dropZone.classList.remove('dragover');
            if (e.dataTransfer.files.length) {
                this.handleFileSelected(e.dataTransfer.files[0]);
            }
        });
        
        fileInput.addEventListener('change', (e) => {
            if (e.target.files.length) {
                this.handleFileSelected(e.target.files[0]);
            }
        });
    },

    async loadRecentDatasets() {
        try {
            const res = await fetch('/api/v1/datasets');
            const datasets = await res.json();
            const grid = document.getElementById('recent-datasets-grid');
            this.renderSidebarDatasets(datasets);

            if (datasets.length > 0) {
                grid.innerHTML = '';
                datasets.forEach(d => {
                    const health = d.health_score?.score;
                    const card = document.createElement('div');
                    card.className = 'dataset-card';
                    card.innerHTML = `
                        <div class="dataset-card-title">
                            ${d.name} <i class="fa-solid fa-chevron-right" style="color:var(--text-muted); font-size:0.8rem;"></i>
                        </div>
                        <div class="dataset-card-meta">
                            <span>${d.row_count.toLocaleString()} rows</span>
                            <span>${d.column_count.toLocaleString()} cols</span>
                        </div>
                        <div style="font-size: 0.85rem; color: var(--success);"><i class="fa-solid fa-heart-pulse"></i> Health: ${health == null ? 'Pending' : `${health}/100`}</div>
                    `;
                    card.onclick = () => {
                        this.currentDatasetId = d.id;
                        this.loadDatasetOverview(d.id, d.name);
                    };
                    grid.appendChild(card);
                });
            }
        } catch (err) {
            console.error('Failed to load recent datasets', err);
        }
    },

    renderSidebarDatasets(datasets) {
        const list = document.getElementById('sidebar-dataset-list');
        if (!list) return;
        if (!datasets || !datasets.length) {
            list.innerHTML = '<p class="sidebar-empty">No datasets yet</p>';
            return;
        }
        list.innerHTML = '';
        datasets.slice(0, 12).forEach(d => {
            const item = document.createElement('div');
            item.className = 'sidebar-dataset-item';
            item.title = d.name;
            item.innerHTML = `<i class="fa-solid fa-table"></i><span>${this.escapeHtml(d.name)}</span>`;
            item.onclick = () => {
                this.currentDatasetId = d.id;
                this.loadDatasetOverview(d.id, d.name);
            };
            list.appendChild(item);
        });
    },

    async loadProjectCatalog() {
        const grid = document.getElementById('project-catalog-grid');
        if (!grid) return;
        try {
            const res = await fetch('/api/v1/project-catalog');
            const data = await res.json();
            if (!res.ok) throw new Error(data.error?.message || 'Project catalog unavailable');
            this.renderProjectCatalog(data.projects || []);
        } catch (err) {
            grid.innerHTML = `<p class="empty-text">${this.escapeHtml(err.message || 'Project catalog unavailable')}</p>`;
        }
    },

    renderProjectCatalog(projects) {
        const grid = document.getElementById('project-catalog-grid');
        if (!grid) return;
        const ordered = [...projects].sort((left, right) => (left.portfolio_tier === 'flagship' ? 0 : 1) - (right.portfolio_tier === 'flagship' ? 0 : 1));
        grid.innerHTML = ordered.map(project => `
            <article class="project-card">
                <div class="project-card-top"><span class="project-number">${this.escapeHtml(project.portfolio_tier === 'flagship' ? 'FLAGSHIP' : 'SKILL DRILL')}</span><span class="project-difficulty">${this.escapeHtml(project.difficulty)}</span></div>
                <h4>${this.escapeHtml(project.name)}</h4>
                <p>${this.escapeHtml(project.description)}</p>
                <div class="project-card-meta"><span>${this.escapeHtml(project.category)}</span><span>${this.escapeHtml(project.template_id)}</span></div>
                <button type="button" class="btn btn-outline project-build-button" data-project-build="${this.escapeHtml(project.id)}"><i class="fa-solid fa-hammer"></i> Build project</button>
            </article>
        `).join('');
        grid.querySelectorAll('[data-project-build]').forEach(button => {
            button.addEventListener('click', () => this.buildPortfolioProject(button.dataset.projectBuild, button));
        });
    },

    async buildPortfolioProject(projectId, button) {
        const output = document.getElementById('project-validation-results');
        if (button) button.disabled = true;
        if (output) output.innerHTML = `<div class="project-status running"><span class="spinner-small"></span> Building ${this.escapeHtml(projectId.replace(/_/g, ' '))}...</div>`;
        try {
            const res = await fetch(`/api/v1/projects/${encodeURIComponent(projectId)}/build`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ rows: PROJECT_FIXTURE_ROWS }) });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error?.message || 'Project build failed');
            this.renderProjectBuildResult(data);
            this.showToast(`${data.project.name} built successfully`);
        } catch (err) {
            if (output) output.innerHTML = `<div class="project-status failed">${this.escapeHtml(err.message || 'Project build failed')}</div>`;
            this.showToast(err.message || 'Project build failed');
        } finally {
            if (button) button.disabled = false;
        }
    },

    async buildAllPortfolioProjects() {
        const output = document.getElementById('project-validation-results');
        const button = document.getElementById('project-build-all');
        if (button) button.disabled = true;
        const flagshipIds = ['superstore_sales_dashboard', 'customer_rfm_segmentation', 'supply_chain_inventory'];
        if (output) output.innerHTML = '<div class="project-status running"><span class="spinner-small"></span> Validating the 3 flagship end-to-end projects...</div>';
        try {
            const res = await fetch('/api/v1/project-validation/run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ rows: PROJECT_FIXTURE_ROWS, project_ids: flagshipIds }) });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error?.message || 'Portfolio validation failed');
            const failed = (data.failures || []).length;
            if (output) output.innerHTML = `<div class="project-status ${failed ? 'failed' : 'success'}"><strong>${this.escapeHtml(data.passed)} / ${this.escapeHtml(data.count)} passed</strong> ${failed ? `• ${this.escapeHtml(failed)} failed` : '• Every project produced a valid BI report'}</div>` + (failed ? `<ul class="project-result-list">${data.failures.map(item => `<li>${this.escapeHtml(item.project_id)}: ${this.escapeHtml(item.error)}</li>`).join('')}</ul>` : '');
            this.showToast(failed ? 'Flagship validation finished with failures' : 'All 3 flagship projects validated');
        } catch (err) {
            if (output) output.innerHTML = `<div class="project-status failed">${this.escapeHtml(err.message || 'Portfolio validation failed')}</div>`;
            this.showToast(err.message || 'Portfolio validation failed');
        } finally {
            if (button) button.disabled = false;
        }
    },

    renderProjectBuildResult(data) {
        const output = document.getElementById('project-validation-results');
        if (!output) return;
        const artifacts = Object.entries(data.artifacts || {}).map(([kind, url]) => `<a class="btn btn-outline btn-download" href="${this.escapeHtml(url)}" download>${this.escapeHtml(kind.toUpperCase())}</a>`).join('');
        output.innerHTML = `<div class="project-status success"><strong>${this.escapeHtml(data.project.name)}</strong> completed • ${this.escapeHtml(data.validation.kpi_count)} KPIs • ${this.escapeHtml(data.validation.chart_count)} charts • ${this.escapeHtml(data.validation.table_count)} tables <span class="project-artifacts">${artifacts}</span></div>`
            + this.renderAdvancedAnalysis((data.analytics || {}).advanced_analysis);
    },

    renderAdvancedAnalysis(analysis) {
        // Statistical, forecasting, and model evidence is the point of these projects,
        // so it is shown as a finding rather than left inside the downloaded report.
        if (!analysis) return '';
        const method = String(analysis.method || '').replace(/_/g, ' ');
        if (analysis.status !== 'completed') {
            return `<div class="analysis-evidence skipped"><span class="analysis-method">${this.escapeHtml(method)}</span><p>${this.escapeHtml(analysis.headline || 'Analysis unavailable')}</p></div>`;
        }
        const metrics = (analysis.kpis || []).map(kpi =>
            `<div class="analysis-metric"><span>${this.escapeHtml(kpi.label)}</span><strong>${this.escapeHtml(kpi.formatted_value)}</strong></div>`
        ).join('');
        return `<div class="analysis-evidence">
            <span class="analysis-method">${this.escapeHtml(method)}</span>
            <p class="analysis-headline">${this.escapeHtml(analysis.headline || '')}</p>
            <div class="analysis-metrics">${metrics}</div>
            <details><summary>Method and caveats</summary><p>${this.escapeHtml(analysis.narrative || '')}</p></details>
        </div>`;
    },

    async handleFileUpload(files) {
        if (files && files.length > 0) {
            await this.handleFileSelected(files[0]);
        }
    },

    async handleFileSelected(file) {
        this.currentFile = file;
        // Hide the whole connector grid
        const sourcesSection = document.getElementById('sources-section');
        if (sourcesSection) {
            const grid = sourcesSection.querySelector('.connector-grid');
            if (grid) grid.classList.add('hidden');
        }
        
        const dropZone = document.getElementById('drop-zone');
        if (dropZone) dropZone.classList.add('hidden');
        
        document.getElementById('upload-progress').classList.remove('hidden');
        document.getElementById('upload-status-text').textContent = "Inspecting dataset...";

        const formData = new FormData();
        formData.append('file', file);

        try {
            const res = await fetch('/api/v1/datasets/import/inspect', { method: 'POST', body: formData });
            const data = await res.json();

            if (res.ok) {
                if (data.requires_selection) {
                    this.excelInspection = data;
                    document.getElementById('upload-progress').classList.add('hidden');
                    this.showExcelSelection(data.sheets);
                } else {
                    document.getElementById('upload-status-text').textContent = "Importing dataset...";
                    await this.importFile(null); // import without sheet selection
                }
            } else {
                throw new Error(data.error?.message || data.detail || 'Inspection failed');
            }
        } catch (err) {
            this.handleUploadError(err);
        }
    },

    showExcelSelection(sheets) {
        document.getElementById('excel-selection-section').classList.remove('hidden');
        const canCombine = Boolean(this.excelInspection?.can_combine_sheets);
        document.getElementById('excel-sheet-count').textContent = `Workbook contains ${sheets.length} sheets. Select one to analyze or combine compatible sheets:`;
        
        const list = document.getElementById('sheet-list');
        list.innerHTML = canCombine ? `<div class="sheet-item sheet-combine-option"><div><h4>Combine all sheets</h4><p>Union columns, preserve each sheet name in <code>_source_sheet</code>, and build one MIS dataset.</p></div><button class="btn btn-primary" style="padding: 0.5rem 1rem;" onclick="App.importFile('__ALL_SHEETS__')">Combine</button></div>` : '';
        sheets.forEach(s => {
            const item = document.createElement('div');
            item.className = 'sheet-item';
            item.innerHTML = `
                <div>
                    <h4>${s.name}</h4>
                    <p>${s.rows.toLocaleString()} rows • ${s.columns.toLocaleString()} columns</p>
                </div>
                <button class="btn btn-primary" style="padding: 0.5rem 1rem;" onclick="App.importFile('${s.name}')">Import</button>
            `;
            list.appendChild(item);
        });
    },

    async importFile(sheetName) {
        document.getElementById('excel-selection-section').classList.add('hidden');
        document.getElementById('upload-progress').classList.remove('hidden');
        document.getElementById('upload-status-text').textContent = "Importing dataset...";

        const formData = new FormData();
        formData.append('file', this.currentFile);
        if (sheetName) {
            formData.append('sheet_name', sheetName);
        }

        try {
            const res = await fetch('/api/v1/datasets/import', { method: 'POST', body: formData });
            const data = await res.json();

            if (res.ok) {
                this.currentDatasetId = data.dataset_id;
                this.showToast('Dataset imported successfully!');
                
                // Reset upload state for next time
                document.getElementById('upload-progress').classList.add('hidden');
                const sourcesSection = document.getElementById('sources-section');
                if (sourcesSection) {
                    const grid = sourcesSection.querySelector('.connector-grid');
                    if (grid) grid.classList.remove('hidden');
                }
                const dropZone = document.getElementById('drop-zone');
                if (dropZone) dropZone.classList.remove('hidden');
                
                this.loadDatasetOverview(data.dataset_id, data.name);
            } else {
                throw new Error(data.error?.message || data.detail || 'Import failed');
            }
        } catch (err) {
            this.handleUploadError(err);
        }
    },

    handleUploadError(err) {
        console.error(err);
        this.showToast(err.message);
        document.getElementById('upload-progress').classList.add('hidden');
        document.getElementById('excel-selection-section').classList.add('hidden');
        
        const sourcesSection = document.getElementById('sources-section');
        if (sourcesSection) {
            const grid = sourcesSection.querySelector('.connector-grid');
            if (grid) grid.classList.remove('hidden');
        }
        
        const dropZone = document.getElementById('drop-zone');
        if (dropZone) dropZone.classList.remove('hidden');
    },

    async loadDatasetOverview(id, name) {
        document.getElementById('overview-filename').textContent = name || 'Dataset';
        this.navigate('overview');
        
        try {
            const res = await fetch(`/api/v1/datasets/${id}/preview`);
            const data = await res.json();
            
            if (res.ok) {
                if (data.schema) {
                    document.getElementById('overview-rows').textContent = data.schema.row_count.toLocaleString();
                    document.getElementById('overview-cols').textContent = data.schema.column_count.toLocaleString();
                }
                
                if (data.health_score) {
                    document.getElementById('overview-health').textContent = `${data.health_score.score}/100`;
                    const issues = data.health_score.issues || [];
                    const issueValue = code => issues.find(issue => issue.code === code)?.value || 0;
                    document.getElementById('overview-missing').textContent = issueValue('MISSING_VALUES');
                    document.getElementById('overview-duplicates').textContent = issueValue('DUPLICATES');
                }
                
                if (data.preview) {
                    this.currentColumns = data.preview.columns || [];
                    this.renderOverviewTable(data.preview);
                    this.populateIntelligenceFields();
                }
            }
        } catch (err) {
            console.error('Failed to load dataset overview', err);
        }
    },

    escapeHtml(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    },

    renderAnalysisResults(data) {
        const grid = document.getElementById('final-metrics-grid');
        if (!grid) return;
        const analysisBasis = data.analysis_basis || {};
        const metrics = [
            ['Initial health', data.initial_health_score == null ? '--' : `${data.initial_health_score}/100`],
            ['Cleaned version', analysisBasis.sampled ? 'Sample review only' : (data.cleaned ? 'Created' : 'Not needed')],
            ['Final version', data.final_version_id || '--'],
            ['Status', data.status || '--'],
            ['Analysis scope', analysisBasis.sampled ? `${analysisBasis.analysis_row_count?.toLocaleString?.() || analysisBasis.analysis_row_count || 0} sampled rows` : 'Full dataset']
        ];
        grid.innerHTML = metrics.map(([label, value]) => `
            <div class="stat-card"><div><p>${this.escapeHtml(label)}</p><h4>${this.escapeHtml(value)}</h4></div></div>
        `).join('');
        const decision = data.business_analysis;
        const card = document.getElementById('business-decision-card');
        const content = document.getElementById('business-decision-content');
        if (decision && card && content) {
            const recommendation = decision.recommendation || {};
            card.classList.remove('hidden');
            content.innerHTML = `
                <div class="business-decision-section"><strong>Problem</strong><p>${this.escapeHtml(decision.problem || '')}</p></div>
                <div class="business-decision-section"><strong>Analysis</strong><p>${this.escapeHtml((decision.analysis?.steps || []).map(item => item.method).join(' '))}</p></div>
                <div class="business-decision-section"><strong>Evidence</strong><ul>${(decision.evidence || []).slice(0, 6).map(item => `<li>${this.escapeHtml(item.finding)}</li>`).join('')}</ul></div>
                <div class="business-decision-section"><strong>Insights</strong><ul>${(decision.insights || []).slice(0, 6).map(item => `<li>${this.escapeHtml(item.statement)}</li>`).join('')}</ul></div>
                <div class="business-decision-section recommendation"><strong>Recommendation</strong><p>${this.escapeHtml(recommendation.action || '')}</p><small>Monitor next: ${this.escapeHtml(decision.metric_to_monitor_next || '')}</small></div>
            `;
        }
    },

    buildBIReportUrl() {
        const params = new URLSearchParams();
        if (this.currentDashboardTemplateId) {
            params.set('template_id', this.currentDashboardTemplateId);
        }
        Object.entries(this.dashboardFilters || {}).forEach(([name, value]) => {
            if (value) params.set(name, value);
        });
        const query = params.toString();
        return `/api/v1/datasets/${encodeURIComponent(this.currentDatasetId)}/bi_report${query ? `?${query}` : ''}`;
    },

    async fetchBIReport() {
        const response = await fetch(this.buildBIReportUrl());
        const report = await response.json();
        if (!response.ok) throw new Error(report.error?.message || 'BI report generation failed');
        return report;
    },

    toggleDashboardTemplates() {
        const gallery = document.getElementById('dashboard-template-gallery');
        const toggle = document.getElementById('dashboard-template-toggle');
        if (!gallery || !toggle) return;
        const willOpen = gallery.classList.contains('hidden');
        gallery.classList.toggle('hidden', !willOpen);
        toggle.setAttribute('aria-expanded', String(willOpen));
    },

    async selectDashboardTemplate(templateId) {
        if (!templateId) return;
        this.currentDashboardTemplateId = templateId;
        try {
            window.localStorage.setItem('automated-data-analyst.dashboard-template', templateId);
        } catch (_) {
            // Selection remains active for the current session when storage is unavailable.
        }
        const gallery = document.getElementById('dashboard-template-gallery');
        const toggle = document.getElementById('dashboard-template-toggle');
        if (gallery) gallery.classList.add('hidden');
        if (toggle) toggle.setAttribute('aria-expanded', 'false');
        if (!this.currentDatasetId) return;

        const status = document.getElementById('bi-report-status');
        if (status) status.textContent = 'Applying dashboard design...';
        try {
            const report = await this.fetchBIReport();
            this.renderBIReport(report);
            this.showToast('Dashboard design applied.');
        } catch (err) {
            console.error(err);
            if (status) status.textContent = err.message || 'Dashboard design could not be applied.';
            this.showToast(status?.textContent || 'Dashboard design could not be applied.');
        }
    },

    renderDashboardTemplateControls(dashboard) {
        const studio = document.getElementById('dashboard-design-studio');
        const template = dashboard?.template || {};
        const templateId = template.id || this.currentDashboardTemplateId || 'executive';
        const templates = dashboard?.available_templates || [];
        this.currentDashboardTemplateId = templateId;
        studio?.classList.remove('hidden');

        const icon = document.getElementById('dashboard-template-icon');
        const name = document.getElementById('dashboard-template-name');
        const description = document.getElementById('dashboard-template-description');
        if (icon) icon.innerHTML = `<i class="fa-solid ${this.escapeHtml(template.icon || 'fa-chart-line')}"></i>`;
        if (name) name.textContent = template.name || 'Executive overview';
        if (description) description.textContent = template.description || 'A focused operating pulse for fast decisions.';

        const list = document.getElementById('dashboard-template-list');
        if (!list) return;
        list.innerHTML = templates.map(item => `
            <button type="button" class="dashboard-template-option${item.id === templateId ? ' selected' : ''}" data-template-id="${this.escapeHtml(item.id)}" aria-pressed="${item.id === templateId ? 'true' : 'false'}">
                <span class="dashboard-template-option-icon"><i class="fa-solid ${this.escapeHtml(item.icon || 'fa-chart-line')}"></i></span>
                <span class="dashboard-template-option-copy">
                    <strong>${this.escapeHtml(item.name)}</strong>
                    <small>${this.escapeHtml(item.recommended_for || item.description || '')}</small>
                </span>
                ${item.id === templateId ? '<i class="fa-solid fa-check dashboard-template-selected-mark"></i>' : ''}
            </button>
        `).join('');
        list.querySelectorAll('[data-template-id]').forEach(button => {
            button.addEventListener('click', () => this.selectDashboardTemplate(button.dataset.templateId));
        });
    },

    renderDashboardFilters(report) {
        const bar = document.getElementById('dashboard-filter-bar');
        if (!bar) return;
        const filters = (report.filters || []).filter(filter => filter.column && (filter.values || []).length > 0);
        if (!filters.length) {
            bar.classList.add('hidden');
            bar.innerHTML = '';
            return;
        }

        bar.classList.remove('hidden');
        bar.innerHTML = `
            <div class="dashboard-filter-title"><i class="fa-solid fa-filter"></i> Filter dashboard</div>
            <form class="dashboard-filter-form" id="dashboard-filter-form">
                ${filters.map(filter => {
                    const selected = this.dashboardFilters[filter.name] || report.applied_filters?.[filter.name] || '';
                    return `<label>${this.escapeHtml(filter.name)}
                        <select data-filter-name="${this.escapeHtml(filter.name)}">
                            <option value="">All ${this.escapeHtml(filter.name)}s</option>
                            ${(filter.values || []).map(value => `<option value="${this.escapeHtml(value)}"${String(value) === String(selected) ? ' selected' : ''}>${this.escapeHtml(value)}</option>`).join('')}
                        </select>
                    </label>`;
                }).join('')}
                <div class="dashboard-filter-actions">
                    <button type="submit" class="btn btn-filter-apply"><i class="fa-solid fa-check"></i> Apply</button>
                    <button type="button" class="btn btn-filter-clear" id="dashboard-filter-clear">Clear</button>
                </div>
            </form>`;

        const form = document.getElementById('dashboard-filter-form');
        form?.addEventListener('submit', event => {
            event.preventDefault();
            this.dashboardFilters = {};
            form.querySelectorAll('[data-filter-name]').forEach(select => {
                if (select.value) this.dashboardFilters[select.dataset.filterName] = select.value;
            });
            this.refreshDashboardFilters();
        });
        document.getElementById('dashboard-filter-clear')?.addEventListener('click', () => {
            this.dashboardFilters = {};
            this.refreshDashboardFilters();
        });
    },

    async refreshDashboardFilters() {
        const status = document.getElementById('bi-report-status');
        if (status) status.textContent = 'Refreshing dashboard filters...';
        try {
            const report = await this.fetchBIReport();
            this.renderBIReport(report);
            this.showToast('Dashboard filters applied.');
        } catch (err) {
            console.error(err);
            if (status) status.textContent = err.message || 'Dashboard filters could not be applied.';
            this.showToast(status?.textContent || 'Dashboard filters could not be applied.');
        }
    },

    dashboardWidgets(report) {
        const widgets = report.dashboard?.widgets || [];
        if (widgets.length) return widgets;
        let nextY = 0;
        const fallback = (report.kpis || []).map((kpi, index) => ({
            id: kpi.source_ref || `kpi-${index}`,
            widget_type: 'kpi',
            title: kpi.label,
            config: { value: kpi.formatted_value ?? kpi.value ?? '--', label: kpi.label },
            x: (index % 4) * 3,
            y: Math.floor(index / 4) * 2,
            w: 3,
            h: 2,
        }));
        nextY = Math.ceil(fallback.length / 4) * 2;
        (report.charts || []).forEach((chart, index) => fallback.push({
            id: chart.source_ref || `chart-${index}`,
            widget_type: 'chart',
            title: chart.title || 'Chart',
            config: { chart },
            x: index % 2 ? 6 : 0,
            y: nextY + Math.floor(index / 2) * 4,
            w: 6,
            h: 4,
        }));
        return fallback;
    },

    renderDashboardTable(widget) {
        const table = widget.config?.table || {};
        const columns = table.columns || (table.rows?.[0] ? Object.keys(table.rows[0]) : []);
        const header = columns.map(column => `<th>${this.escapeHtml(column)}</th>`).join('');
        const body = (table.rows || []).slice(0, 10).map(row => `<tr>${columns.map(column => `<td>${this.escapeHtml(row[column] ?? '—')}</td>`).join('')}</tr>`).join('');
        return `<section class="dashboard-widget dashboard-table-widget" data-widget-type="table" style="grid-column:${Number(widget.x || 0) + 1} / span ${Number(widget.w || 12)}; grid-row:${Number(widget.y || 0) + 1} / span ${Number(widget.h || 4)};">
            <div class="dashboard-widget-heading"><h4>${this.escapeHtml(widget.title || 'Detail table')}</h4><span>Top records</span></div>
            <div class="dashboard-table-scroll"><table><thead><tr>${header}</tr></thead><tbody>${body || `<tr><td colspan="${Math.max(columns.length, 1)}">No rows available</td></tr>`}</tbody></table></div>
        </section>`;
    },

    renderDashboardWidgets(report) {
        const dashboard = report.dashboard || {};
        const template = dashboard.template || {};
        const theme = template.theme || {};
        const container = document.getElementById('bi-dashboard-container');
        if (!container) return [];
        const safeTemplateId = String(template.id || 'executive').replace(/[^a-z0-9_-]/gi, '');
        container.dataset.dashboardTemplate = safeTemplateId;
        container.className = `bi-dashboard-grid dashboard-template dashboard-template-${safeTemplateId}`;
        container.dataset.template = safeTemplateId;
        Object.entries({
            '--dashboard-accent': theme.accent || '#2563eb',
            '--dashboard-secondary': theme.accent_secondary || '#14b8a6',
            '--dashboard-surface': theme.surface || '#f8fafc',
            '--dashboard-surface-alt': theme.surface_alt || '#eff6ff',
            '--dashboard-card': theme.card || '#ffffff',
            '--dashboard-ink': theme.ink || '#0f172a',
            '--dashboard-muted': theme.muted || '#64748b',
            '--dashboard-border': theme.border || '#dbe4ee',
            '--dashboard-positive': theme.positive || '#059669',
            '--dashboard-negative': theme.negative || '#e11d48',
        }).forEach(([name, value]) => container.style.setProperty(name, value));
        const backgroundAsset = String(template.background_asset || '');
        if (backgroundAsset.startsWith('/static/')) {
            container.style.backgroundImage = `url("${backgroundAsset}")`;
            container.style.backgroundSize = 'cover';
            container.style.backgroundPosition = 'center top';
        } else {
            container.style.backgroundImage = '';
            container.style.backgroundSize = '';
            container.style.backgroundPosition = '';
        }
        if (template.source_reference?.source_path) {
            container.dataset.referenceSource = template.source_reference.source_path;
        } else {
            delete container.dataset.referenceSource;
        }

        const widgets = this.dashboardWidgets(report).slice().sort((left, right) => (left.position ?? 0) - (right.position ?? 0));
        container.innerHTML = widgets.map((widget, index) => {
            if (widget.widget_type === 'table') return this.renderDashboardTable(widget);
            const x = Number(widget.x || 0) + 1;
            const y = Number(widget.y || 0) + 1;
            const w = Number(widget.w || (widget.widget_type === 'kpi' ? 3 : 6));
            const h = Number(widget.h || (widget.widget_type === 'kpi' ? 2 : 4));
            const layout = `grid-column:${x} / span ${w}; grid-row:${y} / span ${h};`;
            if (widget.widget_type === 'kpi') {
                const value = widget.config?.value ?? widget.config?.formatted_value ?? '—';
                return `<section class="dashboard-widget dashboard-kpi-widget" data-widget-type="kpi" style="${layout}">
                    <div class="dashboard-kpi-label"><i class="fa-solid fa-chart-simple"></i>${this.escapeHtml(widget.title || widget.config?.label || 'Metric')}</div>
                    <strong>${this.escapeHtml(value)}</strong>
                    <span>Source-backed metric</span>
                </section>`;
            }
            return `<section class="dashboard-widget dashboard-chart-widget" data-widget-type="chart" style="${layout}">
                <div class="dashboard-widget-heading"><h4>${this.escapeHtml(widget.title || 'Chart')}</h4><span>${this.escapeHtml(widget.presentation?.variant === 'chart-primary' ? 'Primary view' : 'Comparison')}</span></div>
                <div class="dashboard-chart-canvas" id="dashboard-echart-${index}" aria-label="${this.escapeHtml(widget.title || 'Chart')}"></div>
            </section>`;
        }).join('');
        return widgets;
    },

    buildFallbackChartOption(chart, palette) {
        const categoryKey = chart.category_column || chart.x_column;
        const data = chart.data || [];
        const categories = data.map(row => row[categoryKey] ?? row.label ?? 'Unknown');
        return {
            backgroundColor: 'transparent',
            color: palette,
            tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
            grid: { left: '5%', right: '4%', bottom: '8%', top: '10%', containLabel: true },
            xAxis: { type: 'category', data: categories, axisLabel: { interval: 0, rotate: categories.length > 5 ? 30 : 0 } },
            yAxis: { type: 'value' },
            series: [{
                name: chart.value_column || 'Value',
                type: chart.chart_type === 'line' ? 'line' : 'bar',
                data: data.map(row => Number(row.value) || 0),
                smooth: chart.chart_type === 'line',
                itemStyle: { borderRadius: chart.chart_type === 'line' ? 0 : [4, 4, 0, 0] },
            }],
        };
    },

    applyChartTheme(option, theme) {
        const palette = theme.palette || ['#2563eb', '#14b8a6', '#8b5cf6', '#f59e0b', '#e11d48', '#0891b2'];
        const themed = option ? JSON.parse(JSON.stringify(option)) : {};
        const muted = theme.muted || '#64748b';
        const border = theme.border || '#dbe4ee';
        themed.backgroundColor = 'transparent';
        themed.color = palette;
        themed.textStyle = { ...(themed.textStyle || {}), color: muted };
        themed.grid = { left: '5%', right: '4%', bottom: '8%', top: '10%', containLabel: true, ...(themed.grid || {}) };
        themed.tooltip = {
            backgroundColor: theme.ink || '#0f172a',
            borderWidth: 0,
            textStyle: { color: '#ffffff' },
            ...(themed.tooltip || {}),
        };
        const styleAxis = axis => ({
            ...axis,
            axisLine: { lineStyle: { color: border }, ...(axis?.axisLine || {}) },
            axisTick: { lineStyle: { color: border }, ...(axis?.axisTick || {}) },
            axisLabel: { color: muted, ...(axis?.axisLabel || {}) },
            splitLine: { lineStyle: { color: border, type: 'dashed' }, ...(axis?.splitLine || {}) },
        });
        const mapAxes = axis => Array.isArray(axis) ? axis.map(styleAxis) : styleAxis(axis || {});
        themed.xAxis = mapAxes(themed.xAxis);
        themed.yAxis = mapAxes(themed.yAxis);
        if (themed.legend) themed.legend = { ...themed.legend, textStyle: { color: muted, ...(themed.legend.textStyle || {}) } };
        return themed;
    },

    disposeDashboardCharts() {
        this.dashboardCharts.forEach(chart => {
            try { chart.dispose(); } catch (_) { /* Chart may already be disposed. */ }
        });
        this.dashboardCharts = [];
    },

    renderDashboardCharts(widgets, template) {
        this.disposeDashboardCharts();
        if (!window.echarts) return;
        const theme = template?.theme || {};
        const palette = theme.palette || ['#2563eb', '#14b8a6', '#8b5cf6', '#f59e0b', '#e11d48', '#0891b2'];
        widgets.forEach((widget, index) => {
            if (widget.widget_type !== 'chart') return;
            const chartDiv = document.getElementById(`dashboard-echart-${index}`);
            if (!chartDiv) return;
            const chartSpec = widget.config?.chart || {};
            const rawOption = chartSpec.echarts_option || this.buildFallbackChartOption(chartSpec, palette);
            const chart = echarts.init(chartDiv);
            chart.setOption(this.applyChartTheme(rawOption, theme), true);
            this.dashboardCharts.push(chart);
        });
        if (!this.dashboardResizeBound) {
            this.dashboardResizeBound = true;
            window.addEventListener('resize', () => this.dashboardCharts.forEach(chart => chart.resize()));
        }
    },

    withGeneratedReportDownloads(report) {
        if (!report || report.downloads || !this.currentDatasetId) return report;
        const templateId = report.dashboard?.template?.id || this.currentDashboardTemplateId;
        const suffix = templateId ? `?template_id=${encodeURIComponent(templateId)}` : '';
        const formats = ['html', 'pdf', 'xlsx', 'powerbi', 'tableau', 'tableau_twb'];
        return {
            ...report,
            downloads: Object.fromEntries(formats.map(format => [
                format,
                `/api/v1/datasets/${this.currentDatasetId}/bi_report/${format}${suffix}`
            ]))
        };
    },

    renderBIReport(report, analysis = null) {
        this.currentBIReport = report;
        const reportTitle = report.report?.title || 'BI Report';
        const analysisBasis = report.analysis_basis || analysis?.analysis_basis || {};
        document.getElementById('results-filename').textContent = reportTitle;
        document.getElementById('bi-report-subtitle').textContent =
            `${report.source?.row_count?.toLocaleString?.() || report.source?.row_count || 0} rows analyzed • ${report.source?.column_count || 0} columns`;

        if (analysisBasis.sampled) {
            document.getElementById('bi-report-subtitle').textContent =
                `${analysisBasis.analysis_row_count?.toLocaleString?.() || analysisBasis.analysis_row_count || 0} rows analyzed • ${report.source?.column_count || 0} columns • sample of ${analysisBasis.source_row_count?.toLocaleString?.() || analysisBasis.source_row_count || 0} source rows`;
        }

        const downloads = document.getElementById('bi-report-downloads');
        const downloadLabels = {
            html: 'HTML',
            pdf: 'PDF',
            xlsx: 'Excel',
            powerbi: 'Power BI project (.zip)',
            tableau: 'Tableau packaged (.twbx)',
            tableau_twb: 'Tableau source (.twb)',
        };
        downloads.innerHTML = Object.entries(report.downloads || {}).map(([format, url]) => {
            const details = report.download_formats?.[format] || {};
            const filename = details.filename ? ` download="${this.escapeHtml(details.filename)}"` : ' download';
            const instructions = details.instructions || `Download ${downloadLabels[format] || format.toUpperCase()}`;
            return `
                <a class="btn btn-outline btn-download" href="${this.escapeHtml(url)}"${filename}
                   title="${this.escapeHtml(instructions)}" aria-label="${this.escapeHtml(instructions)}">
                    <i class="fa-solid fa-download"></i>${downloadLabels[format] || format.toUpperCase()}
                </a>
            `;
        }).join('');

        this.renderDashboardTemplateControls(report.dashboard || {});
        this.renderDashboardFilters(report);
        const widgets = this.renderDashboardWidgets(report);
        this.renderDashboardCharts(widgets, report.dashboard?.template || {});

        const findingsSection = document.getElementById('bi-report-findings-section');
        const findingsContainer = document.getElementById('bi-report-findings');
        const findings = report.findings || [];
        findingsSection.classList.toggle('hidden', findings.length === 0);
        findingsContainer.innerHTML = findings.slice(0, 10).map(finding => `
            <li><span class="finding-severity ${this.escapeHtml(finding.severity || 'info')}">${this.escapeHtml(finding.severity || 'info')}</span>${this.escapeHtml(finding.message || finding.code || 'Finding')}</li>
        `).join('');

        const modelSection = document.getElementById('bi-model-explanation');
        const model = report.bi_model;
        if (modelSection) {
            modelSection.classList.toggle('hidden', !model);
            if (model) {
                modelSection.innerHTML = `
                    <h3>Why the data is modeled this way</h3>
                    <p><strong>${this.escapeHtml(model.fact_table?.name || 'FactData')}</strong> stays at ${this.escapeHtml(model.fact_table?.grain || 'the cleaned source grain')}. Dimensions provide stable one-direction filter paths, while explicit measures prevent ambiguous implicit totals.</p>
                    <div class="model-contract-grid">
                        <div><span>Dimensions</span><strong>${this.escapeHtml((model.dimensions || []).length)}</strong></div>
                        <div><span>Relationships</span><strong>${this.escapeHtml((model.relationships || []).length)}</strong></div>
                        <div><span>Date table</span><strong>${this.escapeHtml(model.date_table || 'Not available')}</strong></div>
                        <div><span>Measures</span><strong>${this.escapeHtml(model.measure_count || 0)}</strong></div>
                    </div>
                    <p><strong>RLS:</strong> ${this.escapeHtml(model.rls?.mode || 'not configured')} — client identity mapping is required before publishing.</p>
                `;
            }
        }

        const tableContainer = document.getElementById('bi-report-tables');
        if (tableContainer) tableContainer.innerHTML = '';
        const status = document.getElementById('bi-report-status');
        const filterText = Object.entries(report.applied_filters || {}).map(([key, value]) => `${key}=${value}`).join(', ');
        const analysisText = analysis ? ` Initial health: ${analysis.initial_health_score ?? '--'}/100.` : '';
        status.textContent = `BI report generated from ${report.source?.name || 'the selected dataset'}.${analysisText}${filterText ? ` Filters: ${filterText}.` : ''}`;
        if (analysisBasis.sampled) {
            status.textContent += ` Sampled ${analysisBasis.analysis_row_count?.toLocaleString?.() || analysisBasis.analysis_row_count || 0} of ${analysisBasis.source_row_count?.toLocaleString?.() || analysisBasis.source_row_count || 0} rows.`;
        }
    },

    renderOverviewTable(previewData) {
        const thead = document.getElementById('overview-preview-thead');
        const tbody = document.getElementById('overview-preview-tbody');
        
        thead.innerHTML = '';
        tbody.innerHTML = '';

        previewData.columns.forEach(col => {
            const th = document.createElement('th');
            th.textContent = col;
            thead.appendChild(th);
        });

        previewData.rows.forEach(row => {
            const tr = document.createElement('tr');
            previewData.columns.forEach(col => {
                const td = document.createElement('td');
                td.textContent = row[col] !== null ? row[col] : 'null';
                tr.appendChild(td);
            });
            tbody.appendChild(tr);
        });
    },

    exploreManually() {
        this.navigate('results');
        this.populateIntelligenceFields();
        this.showToast("Manual exploration mode activated.");
    },

    async generateBIReport() {
        if (!this.currentDatasetId) return;
        const status = document.getElementById('bi-report-status');
        status.textContent = 'Generating BI report...';
        try {
            const report = await this.fetchBIReport();
            this.renderBIReport(report);
            this.navigate('results');
            this.showToast('BI report generated!');
        } catch (err) {
            console.error(err);
            status.textContent = err.message || 'BI report generation failed.';
            this.showToast(status.textContent);
        }
    },

    infographicPayload() {
        const value = id => document.getElementById(id)?.value?.trim();
        return {
            panel_type: value('infographic-panel-type') || undefined,
            theme_id: value('infographic-theme') || 'midnight',
            format_id: value('infographic-format') || 'square',
            category_column: value('infographic-category-column') || undefined,
            measure_column: value('infographic-measure-column') || undefined,
            date_column: value('infographic-date-column') || undefined,
            aggregation: value('infographic-aggregation') || 'sum',
            title: value('infographic-title') || undefined,
            source: value('infographic-source') || undefined,
            handle: value('infographic-handle') || undefined,
            suffix: value('infographic-suffix') || undefined,
            boundary_id: value('infographic-boundary') || undefined,
            boundary_property: value('infographic-boundary-property') || undefined,
            geography_column: value('infographic-category-column') || undefined,
            map_palette: value('infographic-map-palette') || 'blue',
            map_classification: value('infographic-map-classification') || 'quantile',
            map_classes: Number(value('infographic-map-classes') || 5),
            show_labels: Boolean(document.getElementById('infographic-show-labels')?.checked),
        };
    },

    setupInfographicMaker() {
        const sourceMode = document.getElementById('infographic-source-mode');
        const pasted = document.getElementById('infographic-paste-data');
        const panelType = document.getElementById('infographic-panel-type');
        if (sourceMode) sourceMode.addEventListener('change', () => this.syncInfographicSourceFields());
        if (panelType) panelType.addEventListener('change', () => this.syncInfographicStoryControls());
        if (pasted) pasted.addEventListener('input', () => {
            if (sourceMode?.value === 'paste') this.syncInfographicSourceFields();
        });
        this.loadInfographicBoundaries();
        this.syncInfographicStoryControls();
    },

    async loadInfographicBoundaries() {
        try {
            const response = await fetch('/api/v1/geographic/boundaries');
            const result = await response.json();
            if (!response.ok) return;
            this.infographicBoundaries = result.boundaries || [];
            this.populateInfographicBoundaries();
        } catch (_) {
            this.infographicBoundaries = [];
        }
    },

    async bootstrapIndiaBoundary() {
        return this.importOfficialIndiaBoundary();
    },

    openOfficialIndiaBoundarySource() {
        window.open('https://surveyofindia.gov.in/pages/administrative-boundary-data-base-abdb-', '_blank', 'noopener');
        this.showToast('Survey of India boundary source opened. Export the India state/UT layer as GeoJSON, then import it here.');
    },

    async importOfficialIndiaBoundary() {
        const input = document.createElement('input');
        input.type = 'file';
        input.accept = '.geojson,.json,application/geo+json,application/json';
        input.onchange = async () => {
            const file = input.files?.[0];
            if (!file) return;
            try {
                const geometry = JSON.parse(await file.text());
                const sourceVersion = window.prompt('Enter the Survey of India release/version shown with this file:', 'ABDB');
                if (!sourceVersion?.trim()) return;
                const license = window.prompt('Enter the Survey of India usage/license terms for this file:', 'Survey of India terms of use');
                if (!license?.trim()) return;
                this.showToast('Registering the official India-only boundary...');
                const response = await fetch('/api/v1/geographic/boundaries/import/official-india', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        name: `India States/UTs - Survey of India (${sourceVersion.trim()})`,
                        country_code: 'IN',
                        admin_level: 1,
                        source: 'Survey of India',
                        source_version: sourceVersion.trim(),
                        source_url: 'https://surveyofindia.gov.in/pages/administrative-boundary-data-base-abdb-',
                        license: license.trim(),
                        geojson: geometry,
                    }),
                });
                const result = await response.json();
                if (!response.ok) throw new Error(result.error?.message || 'Official India boundary import failed.');
                this.infographicBoundaries = [result, ...this.infographicBoundaries.filter(item => item.boundary_id !== result.boundary_id)];
                this.geographicBoundaries = [result, ...this.geographicBoundaries.filter(item => item.boundary_id !== result.boundary_id)];
                this.populateInfographicBoundaries();
                this.populateBoundarySelectors();
                document.getElementById('infographic-boundary')?.setAttribute('value', result.boundary_id);
                const infographicSelect = document.getElementById('infographic-boundary');
                if (infographicSelect) infographicSelect.value = result.boundary_id;
                const mapSelect = document.getElementById('geo-boundary');
                if (mapSelect) mapSelect.value = result.boundary_id;
                this.showToast('Official Survey of India boundary imported and selected.');
            } catch (error) {
                this.showToast(error.message || 'Official India boundary import failed.');
            }
        };
        input.click();
    },

    async bootstrapIndiaBoundaryFallback() {
        this.showToast('Installing the India-only demo fallback (not official)...');
        try {
            const response = await fetch('/api/v1/geographic/boundaries/bootstrap/india-fallback', { method: 'POST' });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error?.message || 'India fallback installation failed.');
            this.infographicBoundaries = [result, ...this.infographicBoundaries.filter(item => item.boundary_id !== result.boundary_id)];
            this.populateInfographicBoundaries();
            this.geographicBoundaries = [result, ...this.geographicBoundaries.filter(item => item.boundary_id !== result.boundary_id)];
            this.populateBoundarySelectors();
            const select = document.getElementById('infographic-boundary');
            if (select) select.value = result.boundary_id;
            this.showToast('India-only demo boundary installed. Use Survey of India import for official publishing.');
        } catch (error) {
            this.showToast(error.message || 'India fallback installation failed.');
        }
    },

    async bootstrapWorldBoundary() {
        this.showToast('Installing the public-domain global country boundary...');
        try {
            const response = await fetch('/api/v1/geographic/boundaries/bootstrap/world', { method: 'POST' });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error?.message || 'Global boundary installation failed.');
            this.infographicBoundaries = [result, ...this.infographicBoundaries.filter(item => item.boundary_id !== result.boundary_id)];
            this.geographicBoundaries = [result, ...this.geographicBoundaries.filter(item => item.boundary_id !== result.boundary_id)];
            this.populateInfographicBoundaries();
            this.populateBoundarySelectors();
            const select = document.getElementById('infographic-boundary');
            if (select) select.value = result.boundary_id;
            const mapSelect = document.getElementById('geo-boundary');
            if (mapSelect) mapSelect.value = result.boundary_id;
            this.showToast(`Global country boundary installed (${Number(result.feature_count || 0).toLocaleString()} features).`);
        } catch (error) {
            this.showToast(error.message || 'Global boundary installation failed.');
        }
    },

    populateInfographicBoundaries() {
        const select = document.getElementById('infographic-boundary');
        if (!select) return;
        const previous = select.value;
        const panelType = document.getElementById('infographic-panel-type')?.value;
        const isWorld = panelType === 'world_map_story';
        const boundaries = this.infographicBoundaries.filter(item => isWorld ? item.country_code === 'WLD' && Number(item.admin_level) === 0 : item.country_code === 'IN' && Number(item.admin_level) === 1);
        select.innerHTML = `<option value="">Select a registered ${isWorld ? 'global country' : 'India state'} boundary</option>${boundaries.map(item => `<option value="${this.escapeHtml(item.boundary_id)}">${this.escapeHtml(item.name)} · ${this.escapeHtml(item.feature_count)} features · ${this.escapeHtml(item.license)}</option>`).join('')}`;
        if (boundaries.some(item => item.boundary_id === previous)) select.value = previous;
    },

    syncInfographicStoryControls() {
        const panelType = document.getElementById('infographic-panel-type')?.value;
        const isMapStory = ['india_map_story', 'world_map_story'].includes(panelType);
        document.getElementById('infographic-map-story-fields')?.classList.toggle('hidden', !isMapStory);
        if (isMapStory) {
            const theme = document.getElementById('infographic-theme');
            if (theme && panelType === 'india_map_story' && theme.value === 'midnight') theme.value = 'india_pixels';
            this.populateInfographicBoundaries();
        }
    },

    syncInfographicSourceFields() {
        const pasteMode = document.getElementById('infographic-source-mode')?.value === 'paste';
        if (!pasteMode) {
            this.populateIntelligenceFields();
            return;
        }
        const text = document.getElementById('infographic-paste-data')?.value || '';
        const firstLine = text.split(/\r?\n/).find(line => line.trim()) || '';
        const delimiter = firstLine.includes('\t') ? '\t' : firstLine.includes(';') ? ';' : firstLine.includes('|') ? '|' : ',';
        const columns = firstLine.split(delimiter).map(item => item.trim().replace(/^['"]|['"]$/g, '')).filter(Boolean);
        ['infographic-category-column', 'infographic-measure-column', 'infographic-date-column'].forEach(id => {
            const select = document.getElementById(id);
            if (!select) return;
            const previous = select.value;
            select.innerHTML = `<option value="">Auto-select</option>${columns.map(column => `<option value="${this.escapeHtml(column)}">${this.escapeHtml(column)}</option>`).join('')}`;
            if (columns.includes(previous)) select.value = previous;
        });
    },

    renderInfographic(result) {
        this.currentInfographic = result;
        const container = document.getElementById('infographic-results');
        if (!container) return;
        const basis = result.analysis_basis || {};
        const scope = basis.sampled
            ? `Created from a deterministic ${Number(basis.analysis_row_count || 0).toLocaleString()}-row sample of ${Number(basis.source_row_count || 0).toLocaleString()} source rows.`
            : 'Created from the complete supplied data.';
        container.innerHTML = `
            <div class="infographic-result-header">
                <div><span class="block-label">Ready to publish</span><h4>${this.escapeHtml(result.spec?.title || 'Data story')}</h4><p>${this.escapeHtml(scope)}</p></div>
                <a class="btn btn-primary" href="${this.escapeHtml(result.download_url)}"><i class="fa-solid fa-download"></i> Download PNG</a>
            </div>
            <img class="infographic-preview" src="${this.escapeHtml(result.image_url)}?v=${encodeURIComponent(result.infographic_id)}" alt="Generated data infographic">
            <label class="infographic-post-copy">Suggested X post<textarea class="intelligence-input" rows="4" readonly>${this.escapeHtml(result.suggested_post || '')}</textarea></label>
        `;
    },

    async generateInfographic() {
        const container = document.getElementById('infographic-results');
        const sourceMode = document.getElementById('infographic-source-mode')?.value || 'dataset';
        const payload = this.infographicPayload();
        let path;
        if (sourceMode === 'paste') {
            const data = document.getElementById('infographic-paste-data')?.value || '';
            if (!data.trim()) {
                this.showToast('Paste a table before generating an infographic.');
                return;
            }
            path = '/api/v1/infographics/paste';
            payload.data = data;
        } else {
            if (!this.currentDatasetId) {
                this.showToast('Select or upload a dataset, or switch to pasted data.');
                return;
            }
            path = `/api/v1/datasets/${encodeURIComponent(this.currentDatasetId)}/infographics`;
        }
        if (container) container.innerHTML = '<div class="spinner-small"></div> Designing a source-backed infographic...';
        try {
            const response = await fetch(path, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error?.message || 'Infographic generation failed.');
            this.renderInfographic(result);
            this.showToast('Infographic is ready to download.');
        } catch (error) {
            if (container) container.innerHTML = `<p class="text-red">${this.escapeHtml(error.message || 'Infographic generation failed.')}</p>`;
            this.showToast(error.message || 'Infographic generation failed.');
        }
    },

    async initializeGeographicStudio() {
        const status = document.getElementById('geo-map-status');
        if (!this.currentDatasetId) {
            if (status) status.innerHTML = '<span class="status-dot warning"></span> Select or upload a dataset first';
            return;
        }
        try {
            const [catalogResponse, boundaryResponse, profileResponse] = await Promise.all([
                fetch('/api/v1/geographic/catalog'),
                fetch('/api/v1/geographic/boundaries'),
                fetch(`/api/v1/datasets/${encodeURIComponent(this.currentDatasetId)}/geographic/profile`, { method: 'POST' }),
            ]);
            const catalog = await catalogResponse.json();
            const boundaries = await boundaryResponse.json();
            const profile = await profileResponse.json();
            if (!catalogResponse.ok) throw new Error(catalog.error?.message || 'Geographic catalog unavailable.');
            if (!boundaryResponse.ok) throw new Error(boundaries.error?.message || 'Boundary registry unavailable.');
            if (!profileResponse.ok) throw new Error(profile.error?.message || 'Geographic profile failed.');
            this.geographicCatalog = catalog;
            this.geographicBoundaries = boundaries.boundaries || [];
            this.geographicCountryContext = profile.country_context || null;
            this.populateGeographicFields(profile);
            this.renderGeographicTemplates(catalog.templates || []);
            this.populateBoundarySelectors();
            if (status) {
                const detected = profile.geographic_columns?.length || 0;
                status.innerHTML = `<span class="status-dot ${detected ? '' : 'warning'}"></span> ${this.escapeHtml(detected)} geographic field${detected === 1 ? '' : 's'} detected${profile.country_context ? ` • ${this.escapeHtml(profile.country_context)} context` : ''}`;
            }
        } catch (error) {
            if (status) status.innerHTML = `<span class="status-dot error"></span> ${this.escapeHtml(error.message || 'Geographic setup failed')}`;
        }
    },

    populateGeographicFields(profile) {
        const columns = this.currentColumns || [];
        const semantic = profile.geographic_columns || [];
        const geographic = semantic.filter(item => !['geo_latitude', 'geo_longitude'].includes(item.semantic_type)).map(item => item.column);
        const latitude = semantic.find(item => item.semantic_type === 'geo_latitude')?.column;
        const longitude = semantic.find(item => item.semantic_type === 'geo_longitude')?.column;
        const setOptions = (id, values, placeholder) => {
            const select = document.getElementById(id);
            if (!select) return;
            const previous = select.value;
            select.innerHTML = `<option value="">${this.escapeHtml(placeholder)}</option>${values.map(value => `<option value="${this.escapeHtml(value)}">${this.escapeHtml(value)}</option>`).join('')}`;
            if (values.includes(previous)) select.value = previous;
        };
        setOptions('geo-geography-column', geographic, 'Auto-detect');
        setOptions('geo-measure-column', columns, 'Count rows');
        setOptions('geo-category-column', columns, 'Not specified');
        setOptions('geo-latitude-column', columns, 'Auto-detect');
        setOptions('geo-longitude-column', columns, 'Auto-detect');
        if (geographic.length) document.getElementById('geo-geography-column').value = geographic[0];
        if (latitude) document.getElementById('geo-latitude-column').value = latitude;
        if (longitude) document.getElementById('geo-longitude-column').value = longitude;
        const numeric = profile.recommendations?.length ? (profile.geographic_columns || []) : [];
        const likelyMetric = columns.find(column => /revenue|sales|amount|value|profit|count|quantity|delay|distance|population/i.test(column));
        if (likelyMetric) document.getElementById('geo-measure-column').value = likelyMetric;
        const recommended = profile.recommendations?.find(item => ['ready', 'ready_with_boundary'].includes(item.implementation_status));
        if (recommended) document.getElementById('geo-map-type').value = recommended.type;
    },

    populateBoundarySelectors() {
        const indiaContext = this.geographicCountryContext === 'IN';
        if (indiaContext) this.geographicBoundaries.sort((left, right) => (left.country_code === 'IN' ? -1 : 0) - (right.country_code === 'IN' ? -1 : 0) || (left.metadata?.official === true ? -1 : 0) - (right.metadata?.official === true ? -1 : 0));
        const ordered = [...this.geographicBoundaries].sort((left, right) => {
            const leftPreferred = indiaContext ? left.country_code === 'IN' : left.country_code === 'WLD';
            const rightPreferred = indiaContext ? right.country_code === 'IN' : right.country_code === 'WLD';
            return Number(rightPreferred) - Number(leftPreferred) || Number(right.metadata?.official === true) - Number(left.metadata?.official === true) || String(left.name).localeCompare(String(right.name));
        });
        const options = ordered.map(item => `<option value="${this.escapeHtml(item.boundary_id)}">${this.escapeHtml(item.name)} • ${this.escapeHtml(item.country_code)} L${this.escapeHtml(item.admin_level)}</option>`).join('');
        const mapSelect = document.getElementById('geo-boundary');
        const territorySelect = document.getElementById('geo-territory-boundary');
        if (mapSelect) {
            mapSelect.innerHTML = `<option value="">${indiaContext ? 'Select India-only boundary' : 'No boundary selected'}</option>${options}`;
            if (indiaContext) {
                const preferred = ordered.find(item => item.country_code === 'IN' && Number(item.admin_level) === 1);
                if (preferred) mapSelect.value = preferred.boundary_id;
            }
        }
        if (territorySelect) territorySelect.innerHTML = `<option value="">Select boundary</option>${options}`;
    },

    switchGeographicTab(tabName) {
        document.querySelectorAll('[data-geo-tab]').forEach(button => button.classList.toggle('active', button.dataset.geoTab === tabName));
        document.querySelectorAll('[data-geo-panel]').forEach(panel => {
            const visible = panel.dataset.geoPanel === tabName || (tabName === 'studio' && panel.dataset.geoPanel === 'quick');
            panel.classList.toggle('hidden', !visible);
        });
        if (tabName === 'saved') this.loadSavedMaps();
        if (tabName === 'quick' && this.geographicMapInstance) setTimeout(() => this.geographicMapInstance.resize(), 0);
    },

    geographicPayload() {
        const value = id => document.getElementById(id)?.value?.trim();
        const numberValue = id => value(id) === '' || value(id) == null ? undefined : Number(value(id));
        return {
            map_type: value('geo-map-type') || 'auto',
            geography_column: value('geo-geography-column') || undefined,
            measure_column: value('geo-measure-column') || undefined,
            category_column: value('geo-category-column') || undefined,
            latitude_column: value('geo-latitude-column') || undefined,
            longitude_column: value('geo-longitude-column') || undefined,
            aggregation: value('geo-aggregation') || 'sum',
            boundary_id: value('geo-boundary') || undefined,
            boundary_property: value('geo-boundary-property') || undefined,
            title: value('geo-title') || undefined,
            tooltip_fields: (value('geo-tooltip-fields') || '').split(',').map(item => item.trim()).filter(Boolean),
            palette: value('geo-palette') || 'blue',
            reverse_palette: Boolean(document.getElementById('geo-reverse-palette')?.checked),
            classification: value('geo-classification') || 'quantile',
            classes: numberValue('geo-classes') || 5,
            custom_breaks: (value('geo-custom-breaks') || '').split(',').map(Number).filter(Number.isFinite),
            minimum: numberValue('geo-minimum'), maximum: numberValue('geo-maximum'),
            opacity: numberValue('geo-opacity') || 0.82,
            border_width: numberValue('geo-border-width') ?? 1,
            radius: numberValue('geo-radius') || 18,
            missing_color: value('geo-missing-color') || '#d1d5db',
            zero_color: value('geo-zero-color') || '#f3f4f6',
            prefix: value('geo-prefix') || '', suffix: value('geo-suffix') || '',
        };
    },

    async generateGeographicMap() {
        if (!this.currentDatasetId) {
            this.showToast('Select or upload a dataset first.');
            return;
        }
        const status = document.getElementById('geo-map-status');
        if (status) status.innerHTML = '<span class="spinner-small"></span> Building a bounded, source-backed map...';
        try {
            const response = await fetch(`/api/v1/datasets/${encodeURIComponent(this.currentDatasetId)}/geographic/maps/preview`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(this.geographicPayload()),
            });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error?.message || 'Map generation failed.');
            this.currentGeographicMap = result;
            this.switchGeographicTab('quick');
            this.renderGeographicMap(result);
            this.showToast('Interactive map is ready.');
        } catch (error) {
            if (status) status.innerHTML = `<span class="status-dot error"></span> ${this.escapeHtml(error.message || 'Map generation failed')}`;
            this.showToast(error.message || 'Map generation failed.');
        }
    },

    mapBaseStyle() {
        return {
            version: 8,
            sources: { osm: { type: 'raster', tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'], tileSize: 256, attribution: '© OpenStreetMap contributors' } },
            layers: [{ id: 'osm', type: 'raster', source: 'osm', paint: { 'raster-saturation': -0.35, 'raster-opacity': 0.82 } }],
        };
    },

    renderGeographicFallback(result) {
        const container = document.getElementById('geographic-map');
        if (!container) return;
        if (this.geographicMapInstance) {
            this.geographicMapInstance.remove();
            this.geographicMapInstance = null;
        }
        const features = Array.isArray(result.geojson?.features) ? result.geojson.features : [];
        const width = 1000, height = 560, padding = 42;
        let [west, south, east, north] = Array.isArray(result.bbox) && result.bbox.length === 4 ? result.bbox.map(Number) : [-180, -90, 180, 90];
        if (![west, south, east, north].every(Number.isFinite)) [west, south, east, north] = [-180, -90, 180, 90];
        if (west === east) { west -= 0.5; east += 0.5; }
        if (south === north) { south -= 0.5; north += 0.5; }
        const project = coordinate => {
            const longitude = Number(coordinate?.[0]), latitude = Number(coordinate?.[1]);
            return [padding + ((longitude - west) / (east - west)) * (width - padding * 2), height - padding - ((latitude - south) / (north - south)) * (height - padding * 2)];
        };
        const colors = result.style?.colors?.length ? result.style.colors : ['#2563eb'];
        const missingColor = result.style?.missing_color || '#d1d5db';
        const zeroColor = result.style?.zero_color || '#f3f4f6';
        const opacity = Number(result.style?.opacity || 0.82);
        const values = features.map(feature => Number(feature.properties?.value ?? feature.properties?.metric)).filter(Number.isFinite);
        const minimum = values.length ? Math.min(...values) : 0;
        const maximum = values.length ? Math.max(...values) : 1;
        const radiusFor = value => {
            if (result.map_type !== 'bubble') return Math.max(4, Number(result.style?.radius || 18) / 2);
            const ratio = maximum === minimum ? 0.55 : Math.max(0, Math.min(1, (Number(value) - minimum) / (maximum - minimum)));
            return 6 + ratio * Math.min(34, Number(result.style?.radius || 18) * 1.5);
        };
        const colorFor = properties => {
            if (Number(properties?.metric) === 0) return zeroColor;
            const index = Number(properties?.class_index);
            return Number.isInteger(index) && index >= 0 ? colors[index % colors.length] : colors[colors.length - 1] || missingColor;
        };
        const featureTitle = properties => Object.entries(properties || {}).filter(([, value]) => value != null).slice(0, 8).map(([key, value]) => `${key.replace(/_/g, ' ')}: ${value}`).join(' | ');
        const ringPath = ring => (Array.isArray(ring) ? ring : []).map((coordinate, index) => {
            const [x, y] = project(coordinate);
            return `${index ? 'L' : 'M'}${x.toFixed(2)},${y.toFixed(2)}`;
        }).join(' ') + ' Z';
        const geometryPath = geometry => {
            if (geometry?.type === 'Polygon') return (geometry.coordinates || []).map(ringPath).join(' ');
            if (geometry?.type === 'MultiPolygon') return (geometry.coordinates || []).flatMap(polygon => polygon.map(ringPath)).join(' ');
            return '';
        };
        const grid = Array.from({ length: 9 }, (_, index) => {
            const x = padding + index * (width - padding * 2) / 8;
            return `<line x1="${x}" y1="${padding}" x2="${x}" y2="${height - padding}" />`;
        }).join('') + Array.from({ length: 6 }, (_, index) => {
            const y = padding + index * (height - padding * 2) / 5;
            return `<line x1="${padding}" y1="${y}" x2="${width - padding}" y2="${y}" />`;
        }).join('');
        const isRegion = ['choropleth', 'categorical_region'].includes(result.map_type);
        const visibleFeatures = features.slice(0, isRegion ? 5000 : 2500);
        let marks;
        if (isRegion) {
            marks = visibleFeatures.map(feature => {
                const path = geometryPath(feature.geometry);
                const properties = feature.properties || {};
                const fill = properties.metric == null ? missingColor : colorFor(properties);
                return path ? `<path d="${path}" fill="${fill}" fill-opacity="${opacity}" stroke="${result.style?.border_color || '#ffffff'}" stroke-width="${Number(result.style?.border_width ?? 1)}" vector-effect="non-scaling-stroke"><title>${this.escapeHtml(featureTitle(properties))}</title></path>` : '';
            }).join('');
        } else {
            marks = visibleFeatures.map(feature => {
                const [x, y] = project(feature.geometry?.coordinates);
                if (![x, y].every(Number.isFinite)) return '';
                const properties = feature.properties || {};
                const radius = result.map_type === 'heatmap' ? Math.max(12, Number(result.style?.radius || 18)) : radiusFor(properties.value);
                const fillOpacity = result.map_type === 'heatmap' ? Math.min(0.3, opacity * 0.35) : opacity;
                return `<circle cx="${x.toFixed(2)}" cy="${y.toFixed(2)}" r="${radius.toFixed(2)}" fill="${colors[colors.length - 1]}" fill-opacity="${fillOpacity}" stroke="${result.map_type === 'heatmap' ? 'none' : '#ffffff'}" stroke-width="1.5"><title>${this.escapeHtml(featureTitle(properties))}</title></circle>`;
            }).join('');
        }
        const truncated = visibleFeatures.length < features.length ? ` Showing ${visibleFeatures.length.toLocaleString()} of ${features.length.toLocaleString()} marks.` : '';
        container.innerHTML = `<svg class="geographic-fallback-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="${this.escapeHtml(result.title || 'Geographic analysis')}"><rect width="${width}" height="${height}" fill="#eef3f7"/><g class="geographic-fallback-grid">${grid}</g><g>${marks}</g><text x="${padding}" y="25" class="geographic-fallback-title">${this.escapeHtml(result.title || 'Geographic analysis')}</text><text x="${width - padding}" y="${height - 15}" text-anchor="end" class="geographic-fallback-caption">Offline SVG renderer.${this.escapeHtml(truncated)}</text></svg>`;
    },

    renderGeographicMap(result) {
        const container = document.getElementById('geographic-map');
        const status = document.getElementById('geo-map-status');
        const evidence = document.getElementById('geographic-map-evidence');
        if (!container) return;
        if (!window.maplibregl) {
            this.renderGeographicFallback(result);
            if (status) status.innerHTML = `<span class="status-dot"></span> ${this.escapeHtml(result.map_type.replace(/_/g, ' '))} • ${Number(result.analysis_basis?.analysis_row_count || result.source?.row_count || 0).toLocaleString()} rows`;
            if (evidence) {
                const match = result.matching ? `${result.matching.matched_features}/${result.matching.boundary_features} boundaries matched` : `${result.geojson.features.length.toLocaleString()} valid locations`;
                evidence.innerHTML = `<div><span>Evidence</span><strong>${this.escapeHtml(match)}</strong></div><div><span>Source scope</span><strong>${result.analysis_basis?.sampled ? `${Number(result.analysis_basis.analysis_row_count).toLocaleString()} sampled of ${Number(result.analysis_basis.source_row_count).toLocaleString()}` : 'Complete supplied dataset'}</strong></div><div><span>Renderer</span><strong>Offline SVG fallback</strong></div>`;
            }
            return;
        }
        if (this.geographicMapInstance) this.geographicMapInstance.remove();
        container.innerHTML = '';
        const [west, south, east, north] = Array.isArray(result.bbox) && result.bbox.length === 4 ? result.bbox.map(Number) : [-180, -90, 180, 90];
        const center = [Number.isFinite(west) && Number.isFinite(east) ? (west + east) / 2 : 0, Number.isFinite(south) && Number.isFinite(north) ? (south + north) / 2 : 0];
        const span = Math.max(Math.abs((east || 180) - (west || -180)), Math.abs((north || 90) - (south || -90)));
        const zoom = span >= 300 ? 1.05 : span >= 100 ? 1.8 : span >= 40 ? 2.5 : 3.2;
        const map = new window.maplibregl.Map({ container, style: this.mapBaseStyle(), center, zoom, attributionControl: true });
        this.geographicMapInstance = map;
        map.addControl(new window.maplibregl.NavigationControl(), 'top-right');
        map.addControl(new window.maplibregl.FullscreenControl(), 'top-right');
        map.on('load', () => {
            const clustered = result.map_type === 'cluster';
            map.addSource('analysis', { type: 'geojson', data: result.geojson, cluster: clustered, clusterMaxZoom: 14, clusterRadius: 50 });
            const colors = result.style.colors || ['#2563eb'];
            if (['choropleth', 'categorical_region'].includes(result.map_type)) {
                let fillColor;
                if (result.map_type === 'categorical_region') {
                    const categories = [...new Set(result.geojson.features.map(feature => feature.properties?.category).filter(Boolean))].slice(0, colors.length);
                    const match = ['match', ['get', 'category']];
                    categories.forEach((category, index) => match.push(category, colors[index % colors.length]));
                    match.push(result.style.missing_color);
                    fillColor = match;
                } else {
                    const match = ['match', ['get', 'class_index']];
                    colors.forEach((color, index) => match.push(index, color));
                    match.push(result.style.missing_color);
                    fillColor = ['case', ['==', ['get', 'metric'], 0], result.style.zero_color, match];
                }
                map.addLayer({ id: 'analysis-fill', type: 'fill', source: 'analysis', paint: { 'fill-color': fillColor, 'fill-opacity': result.style.opacity } });
                map.addLayer({ id: 'analysis-outline', type: 'line', source: 'analysis', paint: { 'line-color': result.style.border_color, 'line-width': result.style.border_width } });
            } else if (result.map_type === 'heatmap') {
                map.addLayer({ id: 'analysis-heat', type: 'heatmap', source: 'analysis', maxzoom: 15, paint: { 'heatmap-weight': ['interpolate', ['linear'], ['coalesce', ['get', 'value'], 1], 0, 0, 100, 1], 'heatmap-intensity': 1.2, 'heatmap-radius': result.style.radius, 'heatmap-opacity': result.style.opacity, 'heatmap-color': ['interpolate', ['linear'], ['heatmap-density'], 0, 'rgba(0,0,0,0)', 0.2, colors[0], 0.5, colors[Math.floor(colors.length / 2)], 1, colors[colors.length - 1]] } });
            } else if (clustered) {
                map.addLayer({ id: 'clusters', type: 'circle', source: 'analysis', filter: ['has', 'point_count'], paint: { 'circle-color': colors[Math.min(2, colors.length - 1)], 'circle-radius': ['step', ['get', 'point_count'], 16, 100, 23, 1000, 31], 'circle-opacity': result.style.opacity, 'circle-stroke-width': 2, 'circle-stroke-color': '#ffffff' } });
                map.addLayer({ id: 'cluster-count', type: 'symbol', source: 'analysis', filter: ['has', 'point_count'], layout: { 'text-field': ['get', 'point_count_abbreviated'], 'text-size': 12 }, paint: { 'text-color': '#ffffff' } });
                map.addLayer({ id: 'unclustered', type: 'circle', source: 'analysis', filter: ['!', ['has', 'point_count']], paint: { 'circle-color': colors[colors.length - 1], 'circle-radius': 6, 'circle-stroke-width': 1.5, 'circle-stroke-color': '#ffffff' } });
            } else {
                const values = result.geojson.features.map(feature => Number(feature.properties?.value)).filter(Number.isFinite);
                const low = Math.min(...values, 0), high = Math.max(...values, 1);
                const radius = result.map_type === 'bubble' ? ['interpolate', ['linear'], ['coalesce', ['get', 'value'], 0], low, 6, high === low ? low + 1 : high, Math.min(42, result.style.radius * 2)] : result.style.radius / 2;
                map.addLayer({ id: 'analysis-points', type: 'circle', source: 'analysis', paint: { 'circle-color': colors[colors.length - 1], 'circle-radius': radius, 'circle-opacity': result.style.opacity, 'circle-stroke-width': 2, 'circle-stroke-color': '#ffffff' } });
            }
            const interactiveLayer = ['choropleth', 'categorical_region'].includes(result.map_type) ? 'analysis-fill' : result.map_type === 'cluster' ? 'unclustered' : result.map_type === 'heatmap' ? null : 'analysis-points';
            if (interactiveLayer) {
                const popup = new window.maplibregl.Popup({ closeButton: false, closeOnClick: false });
                map.on('mousemove', interactiveLayer, event => {
                    map.getCanvas().style.cursor = 'pointer';
                    const properties = event.features?.[0]?.properties || {};
                    const lines = Object.entries(properties).filter(([, value]) => value != null).slice(0, 8).map(([key, value]) => `<div><span>${this.escapeHtml(key.replace(/_/g, ' '))}</span><strong>${this.escapeHtml(value)}</strong></div>`).join('');
                    popup.setLngLat(event.lngLat).setHTML(`<div class="geo-popup">${lines}</div>`).addTo(map);
                });
                map.on('mouseleave', interactiveLayer, () => { map.getCanvas().style.cursor = ''; popup.remove(); });
            }
            if (Array.isArray(result.bbox) && result.bbox.length === 4) {
                const [west, south, east, north] = result.bbox;
                const padding = 34;
                if (west === east && south === north) map.setCenter([west, south]);
                else map.fitBounds([[west, south], [east, north]], { padding, maxZoom: 12, duration: 0 });
            }
        });
        if (status) status.innerHTML = `<span class="status-dot"></span> ${this.escapeHtml(result.map_type.replace(/_/g, ' '))} • ${Number(result.analysis_basis?.analysis_row_count || result.source?.row_count || 0).toLocaleString()} rows`;
        if (evidence) {
            const match = result.matching ? `${result.matching.matched_features}/${result.matching.boundary_features} boundaries matched` : `${result.geojson.features.length.toLocaleString()} valid locations`;
            evidence.innerHTML = `<div><span>Evidence</span><strong>${this.escapeHtml(match)}</strong></div><div><span>Source scope</span><strong>${result.analysis_basis?.sampled ? `${Number(result.analysis_basis.analysis_row_count).toLocaleString()} sampled of ${Number(result.analysis_basis.source_row_count).toLocaleString()}` : 'Complete supplied dataset'}</strong></div><div><span>Recommendation</span><strong>${this.escapeHtml(result.recommendations?.[0]?.reason || 'User-selected map')}</strong></div>`;
        }
    },

    async runGeographicEDA() {
        if (!this.currentDatasetId) return this.showToast('Select a dataset first.');
        const container = document.getElementById('geographic-eda-results');
        if (container) container.innerHTML = '<span class="spinner-small"></span> Profiling geographic semantics...';
        this.switchGeographicTab('eda');
        try {
            const response = await fetch(`/api/v1/datasets/${encodeURIComponent(this.currentDatasetId)}/geographic/profile`, { method: 'POST' });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error?.message || 'Geographic profile failed.');
            const cards = (result.geographic_columns || []).map(item => `<article class="geo-semantic-card"><div><strong>${this.escapeHtml(item.column)}</strong><span>${this.escapeHtml(item.semantic_type)}</span></div><p>${Math.round(Number(item.confidence || 0) * 100)}% confidence${item.country ? ` • ${this.escapeHtml(item.country)}` : ''}</p><small>${(item.evidence || []).map(evidence => this.escapeHtml(evidence.replace(/_/g, ' '))).join(' • ') || 'No evidence'}</small></article>`).join('');
            container.innerHTML = cards ? `<div class="geo-semantic-grid">${cards}</div>` : '<p class="empty-text">No geographic fields were detected. Add country/state/city or latitude/longitude columns.</p>';
        } catch (error) {
            if (container) container.innerHTML = `<p class="text-red">${this.escapeHtml(error.message)}</p>`;
        }
    },

    async runLocationAnalytics() {
        if (!this.currentDatasetId) return this.showToast('Select a dataset first.');
        const container = document.getElementById('geographic-location-results');
        if (container) container.innerHTML = '<span class="spinner-small"></span> Calculating coordinate coverage and spread...';
        this.switchGeographicTab('location');
        try {
            const payload = this.geographicPayload();
            const response = await fetch(`/api/v1/datasets/${encodeURIComponent(this.currentDatasetId)}/geographic/location-analytics`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error?.message || 'Location analytics failed.');
            const metrics = [['Valid locations', result.location_count], ['Invalid locations', result.invalid_location_count], ['Centroid', `${result.centroid.latitude.toFixed(4)}, ${result.centroid.longitude.toFixed(4)}`], ['Median spread', `${result.distance_from_centroid_km.median.toLocaleString()} km`], ['90th percentile', `${result.distance_from_centroid_km.p90.toLocaleString()} km`], ['Maximum spread', `${result.distance_from_centroid_km.maximum.toLocaleString()} km`]];
            container.innerHTML = `<div class="intelligence-metric-grid">${metrics.map(([label, value]) => `<div><span>${this.escapeHtml(label)}</span><strong>${this.escapeHtml(value)}</strong></div>`).join('')}</div>`;
        } catch (error) {
            if (container) container.innerHTML = `<p class="text-red">${this.escapeHtml(error.message)}</p>`;
        }
    },

    renderGeographicTemplates(templates) {
        const container = document.getElementById('geographic-template-results');
        if (!container) return;
        container.innerHTML = templates.map((template, index) => `<button class="geographic-template-card" onclick="App.applyGeographicTemplate(App.geographicCatalog.templates[${index}])"><i class="fa-solid fa-map"></i><strong>${this.escapeHtml(template.name)}</strong><span>${this.escapeHtml(template.map_type.replace(/_/g, ' '))}</span></button>`).join('');
    },

    applyGeographicTemplate(template) {
        const assign = (id, value) => { const element = document.getElementById(id); if (element && value != null) element.value = value; };
        assign('geo-map-type', template.map_type); assign('geo-palette', template.palette); assign('geo-classification', template.classification); assign('geo-classes', template.classes); assign('geo-radius', template.radius);
        this.switchGeographicTab('quick');
        this.showToast(`${template.name} template applied.`);
    },

    async saveGeographicMap() {
        if (!this.currentDatasetId || !this.currentGeographicMap) return this.showToast('Generate a map before saving it.');
        const name = document.getElementById('geo-title')?.value?.trim() || this.currentGeographicMap.title || 'Geographic analysis';
        try {
            const response = await fetch('/api/v1/geographic/saved-maps', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, dataset_id: this.currentDatasetId, configuration: this.geographicPayload() }) });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error?.message || 'Map could not be saved.');
            this.showToast(`Saved ${result.name} v${result.version}.`);
        } catch (error) { this.showToast(error.message || 'Map could not be saved.'); }
    },

    async loadSavedMaps() {
        const container = document.getElementById('geographic-saved-results');
        if (container) container.innerHTML = '<span class="spinner-small"></span> Loading saved maps...';
        try {
            const response = await fetch('/api/v1/geographic/saved-maps');
            const result = await response.json();
            if (!response.ok) throw new Error(result.error?.message || 'Saved maps unavailable.');
            const maps = result.maps || [];
            this.savedGeographicMaps = maps;
            if (container) container.innerHTML = maps.length ? `<div class="geographic-template-grid">${maps.map((item, index) => `<button class="geographic-template-card" onclick="App.applySavedGeographicMap(App.savedGeographicMaps[${index}].configuration?.configuration || {})"><i class="fa-solid fa-bookmark"></i><strong>${this.escapeHtml(item.name)}</strong><span>v${this.escapeHtml(item.version)} • ${this.escapeHtml(item.configuration?.map_type || '')}</span></button>`).join('')}</div>` : '<p class="empty-text">No saved maps yet. Generate a map and click Save map.</p>';
        } catch (error) { if (container) container.innerHTML = `<p class="text-red">${this.escapeHtml(error.message)}</p>`; }
    },

    applySavedGeographicMap(configuration) {
        const mapping = { map_type: 'geo-map-type', geography_column: 'geo-geography-column', measure_column: 'geo-measure-column', category_column: 'geo-category-column', latitude_column: 'geo-latitude-column', longitude_column: 'geo-longitude-column', aggregation: 'geo-aggregation', boundary_id: 'geo-boundary', boundary_property: 'geo-boundary-property', title: 'geo-title', palette: 'geo-palette', classification: 'geo-classification', classes: 'geo-classes', opacity: 'geo-opacity', border_width: 'geo-border-width', radius: 'geo-radius', prefix: 'geo-prefix', suffix: 'geo-suffix' };
        Object.entries(mapping).forEach(([key, id]) => { const element = document.getElementById(id); if (element && configuration[key] != null) element.value = configuration[key]; });
        const reverse = document.getElementById('geo-reverse-palette'); if (reverse) reverse.checked = Boolean(configuration.reverse_palette);
        this.switchGeographicTab('quick');
        this.showToast('Saved map configuration loaded.');
    },

    async buildTerritory() {
        const container = document.getElementById('geographic-territory-results');
        const payload = { boundary_id: document.getElementById('geo-territory-boundary')?.value, property: document.getElementById('geo-territory-property')?.value?.trim(), values: (document.getElementById('geo-territory-values')?.value || '').split(',').map(item => item.trim()).filter(Boolean) };
        if (container) container.innerHTML = '<span class="spinner-small"></span> Building territory...';
        try {
            const response = await fetch('/api/v1/geographic/territories/build', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error?.message || 'Territory build failed.');
            if (container) container.innerHTML = `<div class="project-status success"><strong>${this.escapeHtml(result.name)}</strong> • ${this.escapeHtml(result.feature_count)} boundary features combined</div>`;
        } catch (error) { if (container) container.innerHTML = `<p class="text-red">${this.escapeHtml(error.message)}</p>`; }
    },

    switchView(viewName) {
        // Update sidebar active states
        const navIds = ['nav-dashboard', 'nav-conversation', 'nav-quality', 'nav-bi-readiness', 'nav-sql', 'nav-statistics', 'nav-eda', 'nav-forecast', 'nav-machine-learning', 'nav-geographic', 'nav-models', 'nav-monitoring', 'nav-data-engineering', 'nav-automation', 'nav-infographics', 'nav-reports'];
        navIds.forEach(id => {
            const el = document.getElementById(id);
            if(el) {
                if (id === `nav-${viewName}`) el.classList.add('active');
                else el.classList.remove('active');
            }
        });

        // Toggle panel visibility
        const panelIds = ['view-panel-dashboard', 'view-panel-conversation', 'view-panel-quality', 'view-panel-bi-readiness', 'view-panel-sql', 'view-panel-statistics', 'view-panel-eda', 'view-panel-forecast', 'view-panel-machine-learning', 'view-panel-geographic', 'view-panel-models', 'view-panel-monitoring', 'view-panel-data-engineering', 'view-panel-automation', 'view-panel-infographics', 'view-panel-reports'];
        panelIds.forEach(id => {
            const el = document.getElementById(id);
            if(el) {
                if (id === `view-panel-${viewName}`) el.classList.remove('hidden');
                else el.classList.add('hidden');
            }
        });
        if (viewName === 'models' || viewName === 'monitoring') this.loadModels();
        if (viewName === 'geographic') this.initializeGeographicStudio();
        if (viewName === 'automation') this.loadStaffControlCenter();

        const breadcrumb = document.getElementById('breadcrumb-current');
        if (breadcrumb) {
            const activeLink = document.getElementById(`nav-${viewName}`);
            const label = activeLink ? activeLink.textContent.trim() : '';
            breadcrumb.textContent = label ? `Analyst Workspace / ${label}` : 'Analyst Workspace';
        }
    },

    appendConversationMessage(role, text) {
        const container = document.getElementById('conversation-messages');
        if (!container) return;
        const label = role === 'user' ? 'You' : 'Data Intelligence';
        const item = document.createElement('article');
        item.className = `conversation-message ${role}`;
        item.innerHTML = `<span class="conversation-message-role">${this.escapeHtml(label)}</span><p>${this.escapeHtml(text).replace(/\n/g, '<br>')}</p>`;
        container.appendChild(item);
        container.scrollTop = container.scrollHeight;
    },

    renderConversationResult(result) {
        const container = document.getElementById('conversation-analysis-result');
        if (!container) return;
        const evidence = (result.evidence || []).map(item => `<article class="conversation-evidence"><strong>${this.escapeHtml(item.metric || 'Evidence')}</strong><p>${this.escapeHtml(item.finding || '')}</p><small>Columns: ${this.escapeHtml((item.source_columns || []).join(', ') || 'derived')}</small></article>`).join('');
        const plan = (result.plan || []).map(item => `<li><strong>${this.escapeHtml(item.action)}</strong><span>${this.escapeHtml(item.status)} · ${this.escapeHtml(item.detail || '')}</span></li>`).join('');
        const followUps = (result.follow_up_questions || []).map(item => `<button class="conversation-chip" onclick="App.useConversationPrompt('${this.escapeHtml(item).replace(/'/g, '&#39;')}')">${this.escapeHtml(item)}</button>`).join('');
        const scope = result.data_scope || {};
        container.innerHTML = `<div class="conversation-answer-meta"><span class="status-chip success">${this.escapeHtml(result.intent?.id || 'analysis')}</span><span>${this.escapeHtml(scope.sampled ? `${scope.rows_used} sampled of ${scope.rows_scanned} rows` : `${scope.rows_used || 0} rows analyzed`)}</span><span>${this.escapeHtml(result.provenance?.mode || 'Python')}</span></div>${evidence ? `<div class="conversation-evidence-grid">${evidence}</div>` : ''}<details class="conversation-plan"><summary>Show execution plan and caveats</summary><ol>${plan}</ol><ul>${(result.warnings || []).map(item => `<li>${this.escapeHtml(item)}</li>`).join('')}</ul></details><div class="conversation-followups">${followUps}</div>`;
    },

    useConversationPrompt(prompt) {
        const input = document.getElementById('conversation-input');
        if (input) { input.value = prompt; input.focus(); }
    },

    clearConversation() {
        this.conversationId = null;
        this.conversationHistory = [];
        const messages = document.getElementById('conversation-messages');
        const results = document.getElementById('conversation-analysis-result');
        if (messages) messages.innerHTML = '<article class="conversation-message assistant"><span class="conversation-message-role">Data Intelligence</span><p>Ask a question about the selected dataset. I will show the answer, evidence, execution plan, and caveats.</p></article>';
        if (results) results.innerHTML = '<p class="empty-text">Your evidence and execution details will appear here.</p>';
    },

    async askConversational() {
        const input = document.getElementById('conversation-input');
        const question = input?.value?.trim();
        if (!this.currentDatasetId) return this.showToast('Select or upload a dataset before asking a question.');
        if (!question) return this.showToast('Ask a question about the selected dataset.');
        const resultContainer = document.getElementById('conversation-analysis-result');
        if (resultContainer) resultContainer.innerHTML = '<div class="spinner-small"></div> Thinking through the data with bounded Python analysis...';
        this.appendConversationMessage('user', question);
        input.value = '';
        try {
            const response = await fetch(`/api/v1/datasets/${encodeURIComponent(this.currentDatasetId)}/conversation/ask`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: question, conversation_id: this.conversationId, history: this.conversationHistory.slice(-12) }),
            });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error?.message || 'Conversational analysis failed.');
            this.conversationId = result.conversation_id;
            this.conversationHistory.push({ role: 'user', content: question }, { role: 'assistant', content: result.answer });
            this.appendConversationMessage('assistant', result.answer || 'Analysis completed.');
            this.renderConversationResult(result);
        } catch (error) {
            if (resultContainer) resultContainer.innerHTML = `<p class="text-red">${this.escapeHtml(error.message || 'Conversational analysis failed.')}</p>`;
            this.appendConversationMessage('assistant', error.message || 'Conversational analysis failed.');
        }
    },

    async loadStaffControlCenter() {
        const checklist = document.getElementById('staff-stage-checklist');
        if (!checklist) return;
        try {
            const query = this.currentDatasetId ? `?dataset_id=${encodeURIComponent(this.currentDatasetId)}` : '';
            const response = await fetch(`/api/v1/platform/staff-control-center${query}`);
            const data = await response.json();
            if (!response.ok) throw new Error(data.error?.message || 'Staff control center unavailable.');
            this.staffControlCenterData = data;
            checklist.innerHTML = (data.stages || []).map(stage => `<label><input type="checkbox" value="${this.escapeHtml(stage.id)}" checked><span><strong>${this.escapeHtml(stage.name)}</strong><small>${this.escapeHtml(stage.handoff)} · ${this.escapeHtml(stage.status)}</small></span></label>`).join('');
            const summary = document.getElementById('staff-capability-summary');
            if (summary) summary.innerHTML = (data.readiness?.domains || []).map(domain => `<div><span>${this.escapeHtml(domain.domain.replace(/_/g, ' '))}</span><strong>${this.escapeHtml(domain.score)}%</strong></div>`).join('');
        } catch (error) {
            checklist.innerHTML = `<p class="text-red">${this.escapeHtml(error.message || 'Staff control center unavailable.')}</p>`;
        }
    },

    selectedStaffStages() {
        return [...document.querySelectorAll('#staff-stage-checklist input[type="checkbox"]:checked')].map(input => input.value);
    },

    renderStaffPlan(plan) {
        const container = document.getElementById('staff-control-results');
        if (!container) return;
        const stages = plan.stages || [];
        const isAutomatic = plan.execution_mode === 'automatic';
        const gates = (plan.approval_gates || []).map(gate => `<span class="status-chip warning">${this.escapeHtml(gate.id.replace(/_/g, ' '))} approval required</span>`).join(' ');
        container.innerHTML = `<div class="project-status ${isAutomatic ? 'success' : 'warning'}"><strong>${this.escapeHtml(plan.status)}</strong> · ${this.escapeHtml(plan.next_step || '')}<div style="margin-top:8px">${gates}</div></div><div class="staff-plan-grid">${stages.map(stage => `<article class="staff-plan-stage"><div><strong>${this.escapeHtml(stage.name)}</strong><em>${this.escapeHtml(stage.status)}</em></div><p>${isAutomatic ? `Automatic action: ${this.escapeHtml(stage.automatic?.action || 'plan')}` : `Manual controls: ${this.escapeHtml((stage.manual?.controls || []).join(' · '))}`}</p><small>Evidence: ${this.escapeHtml(`${stage.evidence?.available || 0} available · ${stage.evidence?.partial || 0} partial · ${stage.evidence?.planned || 0} planned`)}</small><button class="staff-control-link" onclick="App.openStaffStage('${this.escapeHtml(stage.id)}')">Open stage controls →</button></article>`).join('')}</div>${isAutomatic ? '<div class="intelligence-actions" style="margin-top:12px"><button class="btn btn-primary" onclick="App.runStaffAutomatic()"><i class="fa-solid fa-play"></i> Run safe automatic checks</button></div>' : '<p class="empty-text" style="margin-top:12px">Manual mode keeps every transformation, query, model, security policy and export decision under your control.</p>'}`;
    },

    async buildStaffPlan() {
        const mode = document.getElementById('staff-mode')?.value || 'automatic';
        const objective = document.getElementById('staff-objective')?.value || 'Build a trusted, decision-ready data product';
        const path = this.currentDatasetId ? `/api/v1/datasets/${encodeURIComponent(this.currentDatasetId)}/staff-control-center/plan` : '/api/v1/platform/staff-control-center/plan';
        const container = document.getElementById('staff-control-results');
        if (container) container.innerHTML = '<div class="spinner-small"></div> Building the governed staff workflow...';
        try {
            const response = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode, objective, requested_stages: this.selectedStaffStages() }) });
            const plan = await response.json();
            if (!response.ok) throw new Error(plan.error?.message || 'Staff plan could not be built.');
            this.renderStaffPlan(plan);
            this.showToast(`${mode === 'automatic' ? 'Automatic' : 'Manual'} staff workflow is ready.`);
        } catch (error) {
            if (container) container.innerHTML = `<p class="text-red">${this.escapeHtml(error.message || 'Staff plan could not be built.')}</p>`;
            this.showToast(error.message || 'Staff plan could not be built.');
        }
    },

    async runStaffAutomatic() {
        if (!this.currentDatasetId) return this.showToast('Select a dataset before running automatic checks.');
        const target = document.getElementById('orchestration-target')?.value;
        const date = document.getElementById('orchestration-date')?.value;
        const value = document.getElementById('orchestration-value')?.value;
        const payload = { objective: document.getElementById('staff-objective')?.value, target_column: target || undefined, feature_columns: target ? (this.currentColumns || []).filter(column => column !== target && column !== date) : undefined, date_column: date || undefined, value_column: value || undefined };
        await this.intelligenceRequest(`/api/v1/datasets/${encodeURIComponent(this.currentDatasetId)}/orchestrate`, payload, 'staff-control-results', 'Running bounded automatic checks and approval gates...');
    },

    openStaffStage(stageId) {
        const views = { intake_quality: 'quality', modeling: 'dashboard', sql_analysis: 'sql', bi_delivery: 'reports', engineering: 'data-engineering', forecast_ml_monitoring: 'machine-learning', governance: 'models', operations: 'automation' };
        const view = views[stageId] || 'automation';
        this.openWorkspace(view);
        this.showToast(`Opened ${stageId.replace(/_/g, ' ')} controls.`);
    },

    populateIntelligenceFields() {
        const columns = this.currentColumns || [];
        const targets = ['forecast-date-column', 'forecast-value-column', 'ml-target-column', 'de-timestamp-column', 'orchestration-target', 'orchestration-date', 'orchestration-value', 'infographic-category-column', 'infographic-measure-column', 'infographic-date-column'];
        targets.forEach(id => {
            const select = document.getElementById(id);
            if (!select) return;
            const previous = select.value;
            const optional = ['de-timestamp-column', 'orchestration-target', 'orchestration-date', 'orchestration-value', 'infographic-category-column', 'infographic-measure-column', 'infographic-date-column'].includes(id);
            select.innerHTML = `${optional ? '<option value="">Not specified</option>' : ''}${columns.map(column => `<option value="${this.escapeHtml(column)}">${this.escapeHtml(column)}</option>`).join('')}`;
            if (columns.includes(previous) || (optional && previous === '')) select.value = previous;
        });
        const likelyDate = columns.find(column => /date|time|timestamp/i.test(column));
        const likelyValue = columns.find(column => /revenue|sales|amount|value|spend|quantity|count/i.test(column));
        if (likelyDate) {
            ['forecast-date-column', 'orchestration-date', 'de-timestamp-column', 'infographic-date-column'].forEach(id => { const select = document.getElementById(id); if (select && !select.value) select.value = likelyDate; });
        }
        if (likelyValue) {
            ['forecast-value-column', 'orchestration-value', 'infographic-measure-column'].forEach(id => { const select = document.getElementById(id); if (select) select.value = likelyValue; });
        }
    },

    parseColumnList(value) {
        return String(value || '').split(',').map(item => item.trim()).filter(Boolean);
    },

    intelligencePayload() {
        const target = document.getElementById('ml-target-column')?.value;
        const requestedFeatures = this.parseColumnList(document.getElementById('ml-feature-columns')?.value);
        const featureColumns = requestedFeatures.length ? requestedFeatures : (this.currentColumns || []).filter(column => column !== target && !/date|time|timestamp/i.test(column));
        return {
            target_column: target,
            feature_columns: featureColumns,
            task_type: document.getElementById('ml-task-type')?.value || 'auto',
            algorithm: document.getElementById('ml-algorithm')?.value || 'logistic_regression',
            model_name: document.getElementById('ml-model-name')?.value || undefined,
            max_rows: 20000,
            max_trials: 12
        };
    },

    renderIntelligenceResult(containerId, data) {
        const container = document.getElementById(containerId);
        if (!container) return;
        const metrics = [];
        const add = (label, value) => { if (value !== undefined && value !== null && value !== '') metrics.push([label, value]); };
        add('Status', data.status || data.execution_status);
        add('Readiness', data.readiness_score == null ? null : `${Number(data.readiness_score).toFixed(1)}/100`);
        add('Model', data.model || data.algorithm || data.champion_model || data.champion_algorithm || data.champion_algorithm);
        add('Task', data.task_type);
        add('Rows used', data.execution?.rows_used || data.row_count);
        add('Execution', data.execution?.execution_ms == null ? null : `${Number(data.execution.execution_ms).toFixed(0)} ms`);
        add('Source version', data.source_version_id);
        const cards = metrics.length ? `<div class="intelligence-metric-grid">${metrics.map(([label, value]) => `<div><span>${this.escapeHtml(label)}</span><strong>${this.escapeHtml(value)}</strong></div>`).join('')}</div>` : '';
        let special = '';
        if (Array.isArray(data.forecast)) {
            special = `<div class="table-container intelligence-table"><table><thead><tr><th>Date</th><th>Forecast</th><th>Lower</th><th>Upper</th></tr></thead><tbody>${data.forecast.slice(0, 24).map(row => `<tr><td>${this.escapeHtml(row.date)}</td><td>${this.escapeHtml(Number(row.forecast).toFixed(2))}</td><td>${this.escapeHtml(row.lower == null ? '--' : Number(row.lower).toFixed(2))}</td><td>${this.escapeHtml(row.upper == null ? '--' : Number(row.upper).toFixed(2))}</td></tr>`).join('')}</tbody></table></div>`;
        } else if (Array.isArray(data.execution_plan)) {
            special = `<div class="workflow-stage-grid">${data.execution_plan.map(stage => `<div><span>${this.escapeHtml(stage.name || stage.stage)}</span><strong>${this.escapeHtml(stage.status)}</strong><small>${this.escapeHtml(stage.service || (stage.capabilities || []).join(' â€¢ '))}</small></div>`).join('')}</div>`;
        } else if (Array.isArray(data.leaderboard)) {
            special = `<div class="workflow-stage-grid">${data.leaderboard.slice(0, 8).map(item => `<div><span>#${this.escapeHtml(item.rank)} ${this.escapeHtml(item.algorithm)}</span><strong>${this.escapeHtml(JSON.stringify(item.metrics))}</strong></div>`).join('')}</div>`;
        }
        const details = this.escapeHtml(JSON.stringify(data, null, 2));
        container.innerHTML = `${cards}${special}<details class="intelligence-details"><summary>Technical evidence and lineage</summary><pre>${details}</pre></details>`;
    },

    async intelligenceRequest(path, payload, containerId, loadingText) {
        if (!this.currentDatasetId) return null;
        const container = document.getElementById(containerId);
        if (container) container.innerHTML = `<div class="spinner-small"></div> ${this.escapeHtml(loadingText)}`;
        try {
            const response = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload || {}) });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error?.message || 'The operation failed');
            this.renderIntelligenceResult(containerId, data);
            return data;
        } catch (error) {
            if (container) container.innerHTML = `<p class="text-red">${this.escapeHtml(error.message || 'The operation failed')}</p>`;
            this.showToast(error.message || 'The operation failed');
            return null;
        }
    },

    forecastPayload() {
        const seasonal = Number(document.getElementById('forecast-seasonal-period')?.value || 0);
        const frequency = document.getElementById('forecast-frequency')?.value?.trim();
        return {
            date_column: document.getElementById('forecast-date-column')?.value,
            value_column: document.getElementById('forecast-value-column')?.value,
            model: document.getElementById('forecast-model')?.value || 'auto',
            horizon: Number(document.getElementById('forecast-horizon')?.value || 12),
            seasonal_period: seasonal || undefined,
            frequency: frequency || undefined
        };
    },

    async runForecast() {
        await this.intelligenceRequest(`/api/v1/datasets/${encodeURIComponent(this.currentDatasetId)}/forecasting/forecast`, this.forecastPayload(), 'forecast-results', 'Selecting and validating forecast models...');
    },

    async runForecastReadiness() {
        await this.intelligenceRequest(`/api/v1/datasets/${encodeURIComponent(this.currentDatasetId)}/forecasting/readiness`, this.forecastPayload(), 'forecast-results', 'Checking time-series readiness...');
    },

    async runForecastDiagnostics() {
        await this.intelligenceRequest(`/api/v1/datasets/${encodeURIComponent(this.currentDatasetId)}/forecasting/trend_seasonality`, this.forecastPayload(), 'forecast-results', 'Analyzing trend and seasonality...');
    },

    async runMLAction(action) {
        const payload = this.intelligencePayload();
        if (action === 'compare') delete payload.algorithm;
        const result = await this.intelligenceRequest(`/api/v1/datasets/${encodeURIComponent(this.currentDatasetId)}/ml/${action}`, payload, 'ml-results', `${action === 'readiness' ? 'Checking readiness' : action === 'compare' ? 'Comparing candidates' : 'Training and registering model'}...`);
        if (result?.registered_model || result?.registered_champion) await this.loadModels(false);
    },

    async runUnsupervised() {
        const features = this.parseColumnList(document.getElementById('ml-feature-columns')?.value);
        const chosen = features.length ? features : (this.currentColumns || []).filter(column => !/date|time|timestamp|churn|target|label/i.test(column)).slice(0, 8);
        await this.intelligenceRequest(`/api/v1/datasets/${encodeURIComponent(this.currentDatasetId)}/ml/unsupervised/run`, { algorithm: 'kmeans', feature_columns: chosen, n_clusters: 3 }, 'ml-results', 'Finding stable groups...');
    },

    async loadModels(render = true) {
        const container = document.getElementById('models-results');
        try {
            const response = await fetch('/api/v1/models');
            const data = await response.json();
            if (!response.ok) throw new Error(data.error?.message || 'Model registry unavailable');
            this.registeredModels = data.models || [];
            const versions = this.registeredModels.flatMap(model => (model.versions || []).map(version => ({ ...version, model_name: model.name })));
            const monitorSelect = document.getElementById('monitor-model-version');
            if (monitorSelect) monitorSelect.innerHTML = versions.length ? versions.map(version => `<option value="${this.escapeHtml(version.id)}">${this.escapeHtml(version.model_name)} v${this.escapeHtml(version.version)} â€” ${this.escapeHtml(version.status)}</option>`).join('') : '<option value="">No registered models</option>';
            if (render && container) {
                container.innerHTML = this.registeredModels.length ? `<div class="model-registry-grid">${this.registeredModels.map(model => `<article><div><span>${this.escapeHtml(model.task_type)}</span><strong>${this.escapeHtml(model.status)}</strong></div><h4>${this.escapeHtml(model.name)}</h4><p>${this.escapeHtml(model.description || 'Governed application model')}</p>${(model.versions || []).map(version => `<small>v${this.escapeHtml(version.version)} â€¢ ${this.escapeHtml(version.algorithm)} â€¢ ${this.escapeHtml(version.status)}<br>Source ${this.escapeHtml(version.training_source_version_id)}<br>SHA-256 ${this.escapeHtml(String(version.artifact_sha256).slice(0, 16))}...</small>`).join('')}</article>`).join('')}</div>` : '<p class="empty-text">No models have been registered for this workspace.</p>';
            }
            return versions;
        } catch (error) {
            if (container && render) container.innerHTML = `<p class="text-red">${this.escapeHtml(error.message)}</p>`;
            return [];
        }
    },

    async runMonitoring() {
        const modelVersion = document.getElementById('monitor-model-version')?.value;
        if (!modelVersion) { this.showToast('Train or select a model first.'); return; }
        const operation = document.getElementById('monitor-operation')?.value || 'feature_drift';
        await this.intelligenceRequest(`/api/v1/models/${encodeURIComponent(modelVersion)}/monitor/${encodeURIComponent(operation)}`, { dataset_id: this.currentDatasetId }, 'monitoring-results', 'Comparing reference and current behavior...');
    },

    async runDataEngineering() {
        const operation = document.getElementById('de-operation')?.value || 'source_readiness';
        const primaryKeys = this.parseColumnList(document.getElementById('de-primary-key')?.value);
        const timestamp = document.getElementById('de-timestamp-column')?.value;
        await this.intelligenceRequest(`/api/v1/datasets/${encodeURIComponent(this.currentDatasetId)}/data-engineering/${encodeURIComponent(operation)}`, { primary_key_columns: primaryKeys, timestamp_column: timestamp || undefined, sla_minutes: Number(document.getElementById('de-sla-minutes')?.value || 1440) }, 'data-engineering-results', 'Evaluating engineering architecture...');
    },

    async runOrchestration() {
        const target = document.getElementById('orchestration-target')?.value;
        const date = document.getElementById('orchestration-date')?.value;
        const value = document.getElementById('orchestration-value')?.value;
        const payload = { objective: document.getElementById('orchestration-objective')?.value, target_column: target || undefined, feature_columns: target ? (this.currentColumns || []).filter(column => column !== target && column !== date) : undefined, date_column: date || undefined, value_column: value || undefined };
        await this.intelligenceRequest(`/api/v1/datasets/${encodeURIComponent(this.currentDatasetId)}/orchestrate`, payload, 'automation-results', 'Coordinating safe read-only checks and governance gates...');
    },

    async runSQLQuery() {
        if (!this.currentDatasetId) return;
        const editor = document.getElementById('sql-query-editor');
        const container = document.getElementById('sql-query-results');
        if (!editor || !container) return;
        container.innerHTML = '<div class="spinner-small"></div> Running bounded SQL...';
        try {
            const response = await fetch(`/api/v1/datasets/${encodeURIComponent(this.currentDatasetId)}/sql/query`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ sql: editor.value, max_rows: 500, timeout_seconds: 5 })
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error?.message || 'SQL query failed');
            const columns = data.columns || [];
            const rows = data.rows || [];
            const table = rows.length ? `<div class="table-container"><table><thead><tr>${columns.map(column => `<th>${this.escapeHtml(column)}</th>`).join('')}</tr></thead><tbody>${rows.map(row => `<tr>${columns.map(column => `<td>${this.escapeHtml(row[column])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>` : '<p>No rows returned.</p>';
            container.innerHTML = `<div class="project-status success"><strong>${this.escapeHtml(data.row_count)} rows</strong> • ${this.escapeHtml(data.execution_ms)} ms • ${(data.features || []).map(item => this.escapeHtml(item)).join(' • ') || 'basic select'}</div>${table}<div class="sql-optimization"><strong>Optimization review</strong><ul>${(data.optimization?.suggestions || []).map(item => `<li>${this.escapeHtml(item)}</li>`).join('')}</ul></div>`;
        } catch (error) {
            container.innerHTML = `<p class="text-red">Error: ${this.escapeHtml(error.message || 'SQL query failed')}</p>`;
        }
    },

    async runQualityAnalysis() {
        if (!this.currentDatasetId) return;
        const container = document.getElementById('quality-results-container');
        container.innerHTML = '<div class="spinner-small"></div> Running quality analysis...';
        try {
            const res = await fetch(`/api/v1/datasets/${this.currentDatasetId}/quality/analyze`, { method: 'POST' });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error?.message || 'Failed to analyze quality');
            const health = data.health || {};
            const missing = data.missing_analysis || {};
            const duplicates = data.duplicate_analysis || {};
            container.innerHTML = `
                <div class="metrics-grid">
                    <div class="stat-card">
                        <i class="fa-solid fa-heart-pulse"></i>
                        <div><p>Health Score</p><h4>${this.escapeHtml(health.score ?? data.health_score ?? '--')}/100</h4></div>
                    </div>
                    <div class="stat-card">
                        <i class="fa-solid fa-triangle-exclamation"></i>
                        <div><p>Missing Cells</p><h4>${this.escapeHtml(missing.missing_cells ?? data.missing_cells_count ?? 0)}</h4></div>
                    </div>
                    <div class="stat-card">
                        <i class="fa-solid fa-copy"></i>
                        <div><p>Duplicate Rows</p><h4>${this.escapeHtml(duplicates.redundant_duplicate_rows ?? 0)}</h4></div>
                    </div>
                </div>
                <div class="quality-findings-list"><strong>${this.escapeHtml(health.grade || 'QUALITY REVIEW')}</strong><ul>${(health.issues || []).map(issue => `<li>${this.escapeHtml(issue.code)}: ${this.escapeHtml(issue.value ?? '')}</li>`).join('') || '<li>No blocking quality findings in the current snapshot.</li>'}</ul></div>
            `;
        } catch (err) {
            container.innerHTML = `<p class="text-red">Error: ${err.message}</p>`;
        }
    },

    async buildHealthPlan() {
        if (!this.currentDatasetId) return this.showToast('Select a dataset before improving its health.');
        const container = document.getElementById('health-plan-container');
        if (container) container.innerHTML = '<div class="spinner-small"></div> Building an evidence-backed improvement plan...';
        try {
            const response = await fetch(`/api/v1/datasets/${encodeURIComponent(this.currentDatasetId)}/quality/improvement-plan`, { method: 'POST' });
            const plan = await response.json();
            if (!response.ok) throw new Error(plan.error?.message || 'Health improvement plan failed.');
            this.healthImprovementPlan = plan;
            this.renderHealthPlan(plan);
        } catch (error) {
            if (container) container.innerHTML = `<p class="text-red">${this.escapeHtml(error.message || 'Health improvement plan failed.')}</p>`;
            this.showToast(error.message || 'Health improvement plan failed.');
        }
    },

    renderHealthPlan(plan) {
        const container = document.getElementById('health-plan-container');
        if (!container) return;
        const before = plan.before || {};
        const after = plan.after_preview || {};
        const delta = Number(after.delta || 0);
        const recommendations = plan.recommendations || [];
        const steps = plan.steps || [];
        const review = plan.manual_review || [];
        container.innerHTML = `<div class="health-plan-header"><div><span class="product-kicker">APPROVAL-FIRST CLEANING</span><h4>${this.escapeHtml(plan.status === 'IMPROVEMENTS_AVAILABLE' ? 'Improvement plan ready' : 'No low-risk automatic changes')}</h4><p>${this.escapeHtml(plan.message || '')}</p></div><div class="health-score-delta"><span>Projected score</span><strong>${this.escapeHtml(before.score)} → ${this.escapeHtml(after.score)}</strong><em class="${delta > 0 ? 'positive' : delta < 0 ? 'negative' : 'neutral'}">${delta > 0 ? '+' : ''}${this.escapeHtml(delta)} pts</em></div></div>${recommendations.length ? `<div class="health-recommendations">${recommendations.map((item, index) => `<label class="health-plan-step"><input type="checkbox" data-health-step="${index}" checked><span><strong>${this.escapeHtml(item.label)}</strong><small>${this.escapeHtml(item.reason)} · Risk: ${this.escapeHtml(item.risk)}</small></span></label>`).join('')}</div><div class="health-plan-actions"><button class="btn" type="button" onclick="App.previewHealthCleanup()"><i class="fa-solid fa-eye"></i> Preview selected changes</button><button class="btn btn-primary" type="button" onclick="App.applyHealthCleanup()"><i class="fa-solid fa-check"></i> Apply approved cleanup</button></div>` : ''}${review.length ? `<div class="health-manual-review"><strong>Manual review required</strong><ul>${review.map(item => `<li>${this.escapeHtml(item.column)}: ${this.escapeHtml(item.reason)}</li>`).join('')}</ul></div>` : ''}<div id="health-plan-preview" class="health-plan-preview"></div>`;
    },

    selectedHealthSteps() {
        const plan = this.healthImprovementPlan || {};
        return [...document.querySelectorAll('#health-plan-container [data-health-step]:checked')].map(input => plan.steps?.[Number(input.dataset.healthStep)]).filter(Boolean);
    },

    async previewHealthCleanup() {
        const steps = this.selectedHealthSteps();
        const preview = document.getElementById('health-plan-preview');
        if (!steps.length) return this.showToast('Select at least one improvement step.');
        if (preview) preview.innerHTML = '<div class="spinner-small"></div> Previewing selected changes...';
        try {
            const response = await fetch(`/api/v1/datasets/${encodeURIComponent(this.currentDatasetId)}/cleaning/preview`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ steps, preview_limit: 8 }) });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error?.message || 'Cleanup preview failed.');
            const recipe = result.recipe || {};
            if (preview) preview.innerHTML = `<div class="project-status success"><strong>Preview only</strong> · ${this.escapeHtml(recipe.total_changed_cells || 0)} changed cell(s), ${this.escapeHtml(recipe.rows_before || 0)} → ${this.escapeHtml(recipe.rows_after || 0)} rows. Nothing has been saved.</div>`;
        } catch (error) {
            if (preview) preview.innerHTML = `<p class="text-red">${this.escapeHtml(error.message || 'Cleanup preview failed.')}</p>`;
        }
    },

    async applyHealthCleanup() {
        const steps = this.selectedHealthSteps();
        if (!steps.length) return this.showToast('Select at least one improvement step.');
        if (!window.confirm('Apply the selected cleanup and create a new immutable dataset version?')) return;
        const preview = document.getElementById('health-plan-preview');
        if (preview) preview.innerHTML = '<div class="spinner-small"></div> Applying approved cleanup...';
        try {
            const response = await fetch(`/api/v1/datasets/${encodeURIComponent(this.currentDatasetId)}/cleaning/apply`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ steps, preview_limit: 8 }) });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error?.message || 'Cleanup apply failed.');
            if (preview) preview.innerHTML = `<div class="project-status success"><strong>New version created</strong> · ${this.escapeHtml(result.output_version_id || '')} · Re-running health analysis...</div>`;
            await this.runQualityAnalysis();
            await this.buildHealthPlan();
            this.showToast('Approved cleanup applied and health rechecked.');
        } catch (error) {
            if (preview) preview.innerHTML = `<p class="text-red">${this.escapeHtml(error.message || 'Cleanup apply failed.')}</p>`;
            this.showToast(error.message || 'Cleanup apply failed.');
        }
    },

    renderBIReadinessProfile(profile, extraHtml = '') {
        const container = document.getElementById('bi-readiness-results');
        if (!container) return;
        this.biReadinessProfile = profile;
        const actions = (profile.actions || []).map((action, index) => `
            <label class="bi-readiness-action">
                <input type="checkbox" data-bi-readiness-action="${this.escapeHtml(action.id)}" ${action.default_selected ? 'checked' : ''}>
                <span><strong>${this.escapeHtml(action.label)}</strong><small>${this.escapeHtml(action.reason)} · Risk: ${this.escapeHtml(action.risk)}</small></span>
            </label>
        `).join('');
        const blockers = (profile.blockers || []).map(item => `<li><strong>${this.escapeHtml(item.code)}</strong> · ${this.escapeHtml(item.message)}</li>`).join('');
        const roles = (profile.columns || []).map(item => `<span class="bi-role-chip ${this.escapeHtml(item.role)}"><strong>${this.escapeHtml(item.bi_name)}</strong> · ${this.escapeHtml(item.role)}</span>`).join('');
        const model = profile.model_contract || {};
        container.innerHTML = `
            <div class="bi-readiness-summary">
                <div class="stat-card"><div><p>Readiness</p><h4>${this.escapeHtml(profile.readiness_score)}/100</h4></div></div>
                <div class="stat-card"><div><p>Status</p><h4>${this.escapeHtml(profile.readiness_status)}</h4></div></div>
                <div class="stat-card"><div><p>Fact grain</p><h4>${this.escapeHtml(model.fact_table?.grain || 'One row per source record')}</h4></div></div>
                <div class="stat-card"><div><p>Measures</p><h4>${this.escapeHtml((profile.measure_columns || []).length)}</h4></div></div>
            </div>
            <div class="bi-readiness-section"><div class="section-heading"><h4>Recommended transformations</h4><small>Review each action before preview or apply.</small></div><div class="bi-readiness-actions">${actions}</div></div>
            <div class="bi-readiness-section"><div class="section-heading"><h4>Semantic roles</h4><small>These roles feed the FactData and dimension contract.</small></div><div class="bi-role-chips">${roles || '<span class="empty-text">No semantic roles detected.</span>'}</div></div>
            ${blockers ? `<div class="bi-readiness-blockers"><strong>Review before publishing</strong><ul>${blockers}</ul></div>` : '<div class="project-status success"><strong>No blocking readiness issue detected.</strong> Review metric definitions and security before publishing.</div>'}
            <div id="bi-readiness-extra">${extraHtml}</div>
        `;
    },

    selectedBIReadinessOptions() {
        const profile = this.biReadinessProfile || {};
        const selected = {};
        (profile.actions || []).forEach(action => {
            const input = document.querySelector(`[data-bi-readiness-action="${CSS.escape(action.id)}"]`);
            selected[action.id] = input ? input.checked : Boolean(action.default_selected);
        });
        return selected;
    },

    async profileBIReadiness() {
        if (!this.currentDatasetId) return this.showToast('Select a dataset before profiling BI readiness.');
        const container = document.getElementById('bi-readiness-results');
        if (container) container.innerHTML = '<div class="spinner-small"></div> Profiling BI readiness and semantic roles...';
        try {
            const response = await fetch(`/api/v1/datasets/${encodeURIComponent(this.currentDatasetId)}/bi-readiness/profile`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
            const profile = await response.json();
            if (!response.ok) throw new Error(profile.error?.message || 'BI-readiness profiling failed.');
            this.renderBIReadinessProfile(profile);
        } catch (error) {
            if (container) container.innerHTML = `<p class="text-red">${this.escapeHtml(error.message || 'BI-readiness profiling failed.')}</p>`;
        }
    },

    async previewBIReadiness() {
        if (!this.currentDatasetId) return this.showToast('Select a dataset before previewing BI readiness.');
        if (!this.biReadinessProfile) await this.profileBIReadiness();
        if (!this.biReadinessProfile) return;
        const extra = document.getElementById('bi-readiness-extra');
        if (extra) extra.innerHTML = '<div class="spinner-small"></div> Previewing selected BI transformations...';
        try {
            const response = await fetch(`/api/v1/datasets/${encodeURIComponent(this.currentDatasetId)}/bi-readiness/preview`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ options: this.selectedBIReadinessOptions() }) });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error?.message || 'BI-readiness preview failed.');
            const execution = result.execution || {};
            const contract = result.model_contract || {};
            if (extra) extra.innerHTML = `<div class="project-status success"><strong>Preview only</strong> · ${this.escapeHtml(execution.rows_before || 0)} → ${this.escapeHtml(execution.rows_after || 0)} rows · ${this.escapeHtml(execution.columns_before || 0)} → ${this.escapeHtml(execution.columns_after || 0)} columns · ${this.escapeHtml(execution.changed_cells || 0)} changed cells. Nothing was saved.</div><div class="bi-contract-summary"><strong>Model handoff</strong><span>FactData: ${this.escapeHtml(contract.fact_table?.grain || 'one row per source record')}</span><span>Dimensions: ${this.escapeHtml((contract.dimensions || []).length)}</span><span>Measures: ${this.escapeHtml((contract.measures || []).length)}</span></div>`;
        } catch (error) {
            if (extra) extra.innerHTML = `<p class="text-red">${this.escapeHtml(error.message || 'BI-readiness preview failed.')}</p>`;
        }
    },

    async applyBIReadiness() {
        if (!this.currentDatasetId) return this.showToast('Select a dataset before creating BI-ready data.');
        if (!this.biReadinessProfile) await this.profileBIReadiness();
        if (!this.biReadinessProfile) return;
        if (!window.confirm('Create a new immutable BI-ready dataset version using the selected actions?')) return;
        const extra = document.getElementById('bi-readiness-extra');
        if (extra) extra.innerHTML = '<div class="spinner-small"></div> Creating BI-ready dataset version...';
        try {
            const response = await fetch(`/api/v1/datasets/${encodeURIComponent(this.currentDatasetId)}/bi-readiness/apply`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ options: this.selectedBIReadinessOptions() }) });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error?.message || 'BI-ready version creation failed.');
            this.currentColumns = result.profile?.columns?.map(item => item.bi_name) || this.currentColumns;
            this.populateIntelligenceFields();
            this.currentBIReport = null;
            const version = result.output_version_id || result.version_id || 'new version';
            if (extra) extra.innerHTML = `<div class="project-status success"><strong>BI-ready version created</strong> · ${this.escapeHtml(version)} · FactData and semantic-model contract are ready for review.</div>`;
            this.renderBIReadinessProfile(result.profile, document.getElementById('bi-readiness-extra')?.innerHTML || '');
            this.showToast('BI-ready data created as a new immutable version.');
            this.loadRecentDatasets();
        } catch (error) {
            if (extra) extra.innerHTML = `<p class="text-red">${this.escapeHtml(error.message || 'BI-ready version creation failed.')}</p>`;
            this.showToast(error.message || 'BI-ready version creation failed.');
        }
    },

    async runStatistics() {
        if (!this.currentDatasetId) return;
        const container = document.getElementById('statistics-results-container');
        container.innerHTML = '<div class="spinner-small"></div> Generating statistics...';
        try {
            const res = await fetch(`/api/v1/datasets/${this.currentDatasetId}/statistics/summary`, { method: 'POST' });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error?.message || 'Failed to generate statistics');
            
            let html = '';
            const sections = data.sections || [];
            sections.forEach(section => {
                if (section.type === 'table' && section.content && section.content.rows) {
                    const columns = Object.keys(section.content.rows[0] || {});
                    html += `<h4>${this.escapeHtml(section.title)}</h4>`;
                    html += `<div class="table-container"><table><thead><tr>`;
                    columns.forEach(c => html += `<th>${this.escapeHtml(c)}</th>`);
                    html += `</tr></thead><tbody>`;
                    section.content.rows.forEach(r => {
                        html += `<tr>`;
                        columns.forEach(c => html += `<td>${this.escapeHtml(r[c])}</td>`);
                        html += `</tr>`;
                    });
                    html += `</tbody></table></div>`;
                }
            });
            container.innerHTML = html || '<p>No numeric statistics available.</p>';
        } catch (err) {
            container.innerHTML = `<p class="text-red">Error: ${err.message}</p>`;
        }
    },

    async runEDA() {
        if (!this.currentDatasetId) return;
        const container = document.getElementById('eda-results-container');
        container.innerHTML = '<div class="spinner-small"></div> Generating EDA...';
        try {
            const res = await fetch(`/api/v1/datasets/${this.currentDatasetId}/eda/report`, { method: 'POST' });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error?.message || 'Failed to generate EDA');
            
            let html = '';
            const sections = data.sections || [];
            sections.forEach(section => {
                if (section.type === 'table' && section.content && section.content.rows) {
                    const columns = Object.keys(section.content.rows[0] || {});
                    html += `<h4>${this.escapeHtml(section.title)}</h4>`;
                    html += `<div class="table-container"><table><thead><tr>`;
                    columns.forEach(c => html += `<th>${this.escapeHtml(c)}</th>`);
                    html += `</tr></thead><tbody>`;
                    section.content.rows.forEach(r => {
                        html += `<tr>`;
                        columns.forEach(c => html += `<td>${this.escapeHtml(r[c])}</td>`);
                        html += `</tr>`;
                    });
                    html += `</tbody></table></div>`;
                }
            });
            container.innerHTML = html || '<p>EDA generated successfully.</p>';
        } catch (err) {
            container.innerHTML = `<p class="text-red">Error: ${err.message}</p>`;
        }
    },

    async runAutoAnalyst() {
        if (!this.currentDatasetId) return;

        const progressModal = document.getElementById('analysis-progress');
        progressModal.classList.remove('hidden');
        const steps = document.getElementById('progress-steps').children;
        const stepLabels = [...steps].map(step => step.textContent.trim());
        [...steps].forEach((step, index) => {
            step.className = index === 0 ? 'step-done' : index === 1 ? 'step-active' : 'step-pending';
            step.innerHTML = index === 0
                ? `<i class="fa-solid fa-check text-green"></i> ${this.escapeHtml(stepLabels[index])}`
                : index === 1
                    ? `<div class="spinner-small"></div> ${this.escapeHtml(stepLabels[index])}`
                    : `<i class="fa-regular fa-circle"></i> ${this.escapeHtml(stepLabels[index])}`;
        });

        let currentStep = 1;
        const interval = window.setInterval(() => {
            if (currentStep >= steps.length) {
                window.clearInterval(interval);
                return;
            }
            steps[currentStep - 1].className = 'step-done';
            steps[currentStep - 1].innerHTML = `<i class="fa-solid fa-check text-green"></i> ${this.escapeHtml(stepLabels[currentStep - 1])}`;
            steps[currentStep].className = 'step-active';
            steps[currentStep].innerHTML = `<div class="spinner-small"></div> ${this.escapeHtml(stepLabels[currentStep])}`;
            currentStep += 1;
        }, 900);
        const controller = new AbortController();
        const timeout = window.setTimeout(() => controller.abort(), 180000);

        try {
            const res = await fetch(`/api/v1/datasets/${this.currentDatasetId}/automated_analyst`, {
                method: 'POST',
                signal: controller.signal
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error?.message || 'Analysis failed');
            const report = this.withGeneratedReportDownloads(data.report) || await this.fetchBIReport();
            this.renderAnalysisResults(data);
            this.renderBIReport(report, data);
            this.navigate('results');
            this.showToast('Professional end-to-end analysis complete!');

        } catch (err) {
            console.error(err);
            const message = err.name === 'AbortError'
                ? 'Analysis timed out after three minutes. Please retry with a smaller extract.'
                : (err.message || 'Analysis Error');
            this.showToast(message);
        } finally {
            window.clearInterval(interval);
            window.clearTimeout(timeout);
            progressModal.classList.add('hidden');
        }
    }
};

// Initialize app when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    App.init();
});
