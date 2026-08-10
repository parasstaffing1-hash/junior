/**
 * Automated Data Analyst - Frontend Application Logic
 */

const App = {
    currentDatasetId: null,
    currentFile: null,

    // Views
    views: {
        home: document.getElementById('view-home'),
        overview: document.getElementById('view-dataset-overview'),
        results: document.getElementById('view-results-shell')
    },

    init() {
        this.setupDragAndDrop();
        this.loadRecentDatasets();
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
        const fileInput = document.getElementById('file-input');
        
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

    async handleFileSelected(file) {
        this.currentFile = file;
        document.getElementById('drop-zone').classList.add('hidden');
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
                document.getElementById('drop-zone').classList.remove('hidden');
                
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
        document.getElementById('drop-zone').classList.remove('hidden');
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

    renderBIReport(report, analysis = null) {
        const reportTitle = report.report?.title || 'BI Report';
        document.getElementById('results-filename').textContent = reportTitle;
        document.getElementById('bi-report-subtitle').textContent =
            `${report.source?.row_count?.toLocaleString?.() || report.source?.row_count || 0} rows analyzed • ${report.source?.column_count || 0} columns`;

        const downloads = document.getElementById('bi-report-downloads');
        const downloadLabels = { html: 'HTML', pdf: 'PDF', xlsx: 'Excel' };
        downloads.innerHTML = Object.entries(report.downloads || {}).map(([format, url]) => `
            <a class="btn btn-outline btn-download" href="${this.escapeHtml(url)}" download>
                <i class="fa-solid fa-download"></i>${downloadLabels[format] || format.toUpperCase()}
            </a>
        `).join('');

        const kpiContainer = document.getElementById('bi-report-kpis');
        kpiContainer.innerHTML = (report.kpis || []).map(kpi => `
            <div class="stat-card bi-kpi-card">
                <i class="fa-solid fa-chart-simple"></i>
                <div><p>${this.escapeHtml(kpi.label)}</p><h4>${this.escapeHtml(kpi.formatted_value ?? kpi.value ?? '--')}</h4></div>
            </div>
        `).join('');

        const findingsSection = document.getElementById('bi-report-findings-section');
        const findingsContainer = document.getElementById('bi-report-findings');
        const findings = report.findings || [];
        findingsSection.classList.toggle('hidden', findings.length === 0);
        findingsContainer.innerHTML = findings.slice(0, 10).map(finding => `
            <li><span class="finding-severity ${this.escapeHtml(finding.severity || 'info')}">${this.escapeHtml(finding.severity || 'info')}</span>${this.escapeHtml(finding.message || finding.code || 'Finding')}</li>
        `).join('');

        const chartContainer = document.getElementById('bi-report-charts');
        chartContainer.innerHTML = (report.charts || []).map(chart => {
            const categoryKey = chart.category_column || chart.x_column;
            const values = (chart.data || []).map(row => Number(row.value)).filter(value => Number.isFinite(value));
            const maxValue = Math.max(1, ...values.map(value => Math.abs(value)));
            const rows = (chart.data || []).map(row => {
                const rawValue = Number(row.value);
                const numericValue = Number.isFinite(rawValue) ? rawValue : 0;
                const width = Math.min(100, Math.abs(numericValue) / maxValue * 100);
                const label = row[categoryKey] ?? row.label ?? '';
                return `<div class="bi-chart-row">
                    <span class="bi-chart-label" title="${this.escapeHtml(label)}">${this.escapeHtml(label)}</span>
                    <span class="bi-chart-track"><span class="bi-chart-bar ${numericValue < 0 ? 'negative' : ''}" style="width:${width}%"></span></span>
                    <span class="bi-chart-value">${this.escapeHtml(numericValue.toLocaleString(undefined, { maximumFractionDigits: 2 }))}</span>
                </div>`;
            }).join('');
            return `<section class="card bi-chart-card">
                <h3>${this.escapeHtml(chart.title || 'Chart')}</h3>
                <div class="bi-chart-rows">${rows || '<p class="empty-text">No chart data available.</p>'}</div>
            </section>`;
        }).join('');

        const tableContainer = document.getElementById('bi-report-tables');
        tableContainer.innerHTML = (report.tables || []).map(table => {
            const columns = table.columns || (table.rows?.[0] ? Object.keys(table.rows[0]) : []);
            const header = columns.map(column => `<th>${this.escapeHtml(column)}</th>`).join('');
            const body = (table.rows || []).map(row => `<tr>${columns.map(column => `<td>${this.escapeHtml(row[column])}</td>`).join('')}</tr>`).join('');
            return `<section class="card bi-report-table"><h3>${this.escapeHtml(table.title || (table.source_ref || 'Detail table'))}</h3>
                <div class="table-container"><table><thead><tr>${header}</tr></thead><tbody>${body}</tbody></table></div></section>`;
        }).join('');

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
            const res = await fetch(`/api/v1/datasets/${this.currentDatasetId}/bi_report`);
            const report = await res.json();
            if (!res.ok) throw new Error(report.error?.message || 'BI report generation failed');
            this.renderBIReport(report);
            this.navigate('results');
            this.showToast('BI report generated!');
        } catch (err) {
            console.error(err);
            status.textContent = err.message || 'BI report generation failed.';
            this.showToast(status.textContent);
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
            const reportRes = await fetch(`/api/v1/datasets/${this.currentDatasetId}/bi_report`);
            const report = await reportRes.json();
            if (!reportRes.ok) throw new Error(report.error?.message || 'BI report generation failed');
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
