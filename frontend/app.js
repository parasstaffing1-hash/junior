/**
 * Automated Data Analyst - Frontend Application Logic
 */

const App = {
    currentDatasetId: null,
    currentFile: null,
    currentDashboardTemplateId: 'executive',
    dashboardFilters: {},
    dashboardCharts: [],
    currentBIReport: null,

    // Views
    views: {
        home: document.getElementById('view-home'),
        overview: document.getElementById('view-dataset-overview'),
        results: document.getElementById('view-results-shell')
    },

    init() {
        try {
            this.currentDashboardTemplateId = window.localStorage.getItem('automated-data-analyst.dashboard-template') || this.currentDashboardTemplateId;
        } catch (_) {
            // Template selection is an enhancement; the dashboard still works when storage is unavailable.
        }
        this.setupDragAndDrop();
        this.loadAcquisitionReadiness();
        this.loadRecentDatasets();
        this.loadProjectCatalog();
    },

    async loadAcquisitionReadiness() {
        const cockpit = document.getElementById('acquisition-cockpit');
        if (!cockpit) return;
        try {
            const response = await fetch('/api/v1/platform/acquisition-readiness');
            const data = await response.json();
            if (!response.ok) throw new Error(data.error?.message || 'Readiness evidence unavailable');
            const score = Number(data.score || 0);
            document.getElementById('readiness-score').innerHTML = `<strong>${score}</strong><span>/ 100</span>`;
            const status = document.getElementById('readiness-status');
            status.className = `readiness-status ${data.acquisition_ready ? 'ready' : 'investment-required'}`;
            status.innerHTML = `<i class="fa-solid ${data.acquisition_ready ? 'fa-circle-check' : 'fa-triangle-exclamation'}"></i><strong>${this.escapeHtml(String(data.grade || '').replace(/_/g, ' '))}</strong><span>${this.escapeHtml(data.method || '')}</span>`;
            document.getElementById('readiness-domains').innerHTML = (data.domains || []).map(domain => `
                <div class="readiness-domain">
                    <div><span>${this.escapeHtml(String(domain.domain || '').replace(/_/g, ' '))}</span><strong>${Number(domain.score || 0)}%</strong></div>
                    <div class="readiness-meter"><span style="width:${Math.max(0, Math.min(100, Number(domain.score || 0)))}%"></span></div>
                </div>
            `).join('');
            const blockers = data.critical_blockers || [];
            document.getElementById('readiness-blockers').innerHTML = blockers.length
                ? `<div class="readiness-blocker-title"><span>Critical investment gates</span><strong>${blockers.length} open</strong></div><div class="readiness-blocker-list">${blockers.slice(0, 6).map(item => `<span>${this.escapeHtml(item.name)}</span>`).join('')}</div>`
                : '<div class="readiness-blocker-title"><span>No critical capability blockers</span><strong>Gate clear</strong></div>';
        } catch (error) {
            document.getElementById('readiness-status').textContent = error.message || 'Readiness evidence unavailable';
        }
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
        grid.innerHTML = projects.map(project => `
            <article class="project-card">
                <div class="project-card-top"><span class="project-number">${this.escapeHtml(project.id.replace(/_/g, ' '))}</span><span class="project-difficulty">${this.escapeHtml(project.difficulty)}</span></div>
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
            const res = await fetch(`/api/v1/projects/${encodeURIComponent(projectId)}/build`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ rows: 48 }) });
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
        if (output) output.innerHTML = '<div class="project-status running"><span class="spinner-small"></span> Validating all 20 portfolio projects...</div>';
        try {
            const res = await fetch('/api/v1/project-validation/run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ rows: 48 }) });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error?.message || 'Portfolio validation failed');
            const failed = (data.failures || []).length;
            if (output) output.innerHTML = `<div class="project-status ${failed ? 'failed' : 'success'}"><strong>${this.escapeHtml(data.passed)} / ${this.escapeHtml(data.count)} passed</strong> ${failed ? `• ${this.escapeHtml(failed)} failed` : '• Every project produced a valid BI report'}</div>` + (failed ? `<ul class="project-result-list">${data.failures.map(item => `<li>${this.escapeHtml(item.project_id)}: ${this.escapeHtml(item.error)}</li>`).join('')}</ul>` : '');
            this.showToast(failed ? 'Portfolio validation finished with failures' : 'All 20 portfolio projects validated');
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
        output.innerHTML = `<div class="project-status success"><strong>${this.escapeHtml(data.project.name)}</strong> completed • ${this.escapeHtml(data.validation.kpi_count)} KPIs • ${this.escapeHtml(data.validation.chart_count)} charts • ${this.escapeHtml(data.validation.table_count)} tables <span class="project-artifacts">${artifacts}</span></div>`;
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
        document.getElementById('excel-sheet-count').textContent = `Workbook contains ${sheets.length} sheets. Select one to analyze:`;
        
        const list = document.getElementById('sheet-list');
        list.innerHTML = '';
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
        this.navigate('overview');
        document.getElementById('overview-filename').textContent = name || 'Dataset';
        
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
                    this.renderOverviewTable(data.preview);
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
        const metrics = [
            ['Initial health', data.initial_health_score == null ? '--' : `${data.initial_health_score}/100`],
            ['Cleaned version', data.cleaned ? 'Created' : 'Not needed'],
            ['Final version', data.final_version_id || '--'],
            ['Status', data.status || '--']
        ];
        grid.innerHTML = metrics.map(([label, value]) => `
            <div class="stat-card"><div><p>${this.escapeHtml(label)}</p><h4>${this.escapeHtml(value)}</h4></div></div>
        `).join('');
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

    renderBIReport(report, analysis = null) {
        this.currentBIReport = report;
        const reportTitle = report.report?.title || 'BI Report';
        document.getElementById('results-filename').textContent = reportTitle;
        document.getElementById('bi-report-subtitle').textContent =
            `${report.source?.row_count?.toLocaleString?.() || report.source?.row_count || 0} rows analyzed • ${report.source?.column_count || 0} columns`;

        const downloads = document.getElementById('bi-report-downloads');
        const downloadLabels = {
            html: 'HTML',
            pdf: 'PDF',
            xlsx: 'Excel',
            powerbi: 'Power BI PBIP',
            tableau: 'Tableau TWBX',
            tableau_twb: 'Tableau TWB',
        };
        downloads.innerHTML = Object.entries(report.downloads || {}).map(([format, url]) => `
            <a class="btn btn-outline btn-download" href="${this.escapeHtml(url)}" download>
                <i class="fa-solid fa-download"></i>${downloadLabels[format] || format.toUpperCase()}
            </a>
        `).join('');

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

        const tableContainer = document.getElementById('bi-report-tables');
        if (tableContainer) tableContainer.innerHTML = '';
        const status = document.getElementById('bi-report-status');
        const filterText = Object.entries(report.applied_filters || {}).map(([key, value]) => `${key}=${value}`).join(', ');
        const analysisText = analysis ? ` Initial health: ${analysis.initial_health_score ?? '--'}/100.` : '';
        status.textContent = `BI report generated from ${report.source?.name || 'the selected dataset'}.${analysisText}${filterText ? ` Filters: ${filterText}.` : ''}`;
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

    switchView(viewName) {
        // Update sidebar active states
        const navIds = ['nav-dashboard', 'nav-quality', 'nav-statistics', 'nav-eda', 'nav-reports'];
        navIds.forEach(id => {
            const el = document.getElementById(id);
            if(el) {
                if (id === `nav-${viewName}`) el.classList.add('active');
                else el.classList.remove('active');
            }
        });

        // Toggle panel visibility
        const panelIds = ['view-panel-dashboard', 'view-panel-quality', 'view-panel-statistics', 'view-panel-eda', 'view-panel-reports'];
        panelIds.forEach(id => {
            const el = document.getElementById(id);
            if(el) {
                if (id === `view-panel-${viewName}`) el.classList.remove('hidden');
                else el.classList.add('hidden');
            }
        });
    },

    async runQualityAnalysis() {
        if (!this.currentDatasetId) return;
        const container = document.getElementById('quality-results-container');
        container.innerHTML = '<div class="spinner-small"></div> Running quality analysis...';
        try {
            const res = await fetch(`/api/v1/datasets/${this.currentDatasetId}/quality/analyze`, { method: 'POST' });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error?.message || 'Failed to analyze quality');
            
            container.innerHTML = `
                <div class="metrics-grid">
                    <div class="stat-card">
                        <i class="fa-solid fa-heart-pulse"></i>
                        <div><p>Health Score</p><h4>${data.health_score || '--'}/100</h4></div>
                    </div>
                    <div class="stat-card">
                        <i class="fa-solid fa-triangle-exclamation"></i>
                        <div><p>Missing Cells</p><h4>${data.missing_cells_count || 0}</h4></div>
                    </div>
                </div>
                <div style="margin-top: 20px;">
                    <h4>Column Profiles</h4>
                    <ul>
                        ${Object.entries(data.column_profiles || {}).map(([col, prof]) => 
                            `<li><strong>${this.escapeHtml(col)}:</strong> ${prof.completeness_percentage}% complete, ${prof.unique_count} unique values</li>`
                        ).join('')}
                    </ul>
                </div>
            `;
        } catch (err) {
            container.innerHTML = `<p class="text-red">Error: ${err.message}</p>`;
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
        
        // Setup initial ticking animation
        const steps = document.getElementById('progress-steps').children;
        
        try {
            // Trigger backend
            const res = await fetch(`/api/v1/datasets/${this.currentDatasetId}/automated_analyst`, { method: 'POST' });
            
            // Just for UX, let's step through the animation quickly assuming it finishes
            let currentStep = 1;
            const interval = setInterval(() => {
                if (currentStep < steps.length) {
                    steps[currentStep - 1].className = 'step-done';
                    steps[currentStep - 1].innerHTML = `<i class="fa-solid fa-check text-green"></i> ${steps[currentStep - 1].textContent}`;
                    
                    steps[currentStep].className = 'step-active';
                    steps[currentStep].innerHTML = `<div class="spinner-small"></div> ${steps[currentStep].textContent}`;
                    currentStep++;
                } else {
                    clearInterval(interval);
                    steps[steps.length - 1].className = 'step-done';
                    steps[steps.length - 1].innerHTML = `<i class="fa-solid fa-check text-green"></i> ${steps[steps.length - 1].textContent}`;
                }
            }, 500);

            const data = await res.json();
            await new Promise(resolve => setTimeout(resolve, 3000));
            clearInterval(interval);
            progressModal.classList.add('hidden');

            if (!res.ok) throw new Error(data.error?.message || 'Analysis failed');
            const report = await this.fetchBIReport();
            this.renderAnalysisResults(data);
            this.renderBIReport(report, data);
            this.navigate('results');
            this.showToast('Analysis and BI report complete!');

        } catch (err) {
            console.error(err);
            this.showToast(err.message || 'Analysis Error');
            progressModal.classList.add('hidden');
        }
    }
};

// Initialize app when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    App.init();
});
