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

        const dashboardContainer = document.getElementById('bi-dashboard-container');
        
        // 1. Generate KPIs HTML (4 across -> span-3)
        const kpiHtml = (report.kpis || []).map(kpi => `
            <div class="pbi-card span-3">
                <div class="pbi-card-title">
                    <i class="fa-solid fa-chart-simple"></i> ${this.escapeHtml(kpi.label)}
                </div>
                <div class="pbi-kpi-value">${this.escapeHtml(kpi.formatted_value ?? kpi.value ?? '--')}</div>
            </div>
        `).join('');

        // 2. Generate Charts HTML (Alternating spans or just span-6 for 2 across)
        const chartHtml = (report.charts || []).map((chart, index) => {
            // Logic to make wider charts depending on index to match Power BI layouts
            const spanClass = (index % 3 === 0) ? 'span-12' : 'span-6';
            return `
            <div class="pbi-card ${spanClass} row-span-3">
                <div class="pbi-card-title">${this.escapeHtml(chart.title || 'Chart')}</div>
                <div id="echart-${index}" style="width: 100%; height: 100%; min-height: 350px;"></div>
            </div>
            `;
        }).join('');

        dashboardContainer.innerHTML = kpiHtml + chartHtml;

        const findingsSection = document.getElementById('bi-report-findings-section');
        const findingsContainer = document.getElementById('bi-report-findings');
        const findings = report.findings || [];
        findingsSection.classList.toggle('hidden', findings.length === 0);
        findingsContainer.innerHTML = findings.slice(0, 10).map(finding => `
            <li><span class="finding-severity ${this.escapeHtml(finding.severity || 'info')}">${this.escapeHtml(finding.severity || 'info')}</span>${this.escapeHtml(finding.message || finding.code || 'Finding')}</li>
        `).join('');

        // Initialize ECharts instances
        if (window.echarts) {
            const pbiPalette = ['#118DFF', '#12239E', '#E66C37', '#6B007B', '#E044A7', '#744EC2', '#D9B300', '#D64550', '#197278'];
            
            (report.charts || []).forEach((chart, index) => {
                const chartDiv = document.getElementById(`echart-${index}`);
                if (!chartDiv) return;
                
                const myChart = echarts.init(chartDiv, 'dark'); // Initialize in dark theme to match our UI
                
                let option = chart.echarts_option;
                if (!option) {
                    // Fallback generator for older spec format
                    const categoryKey = chart.category_column || chart.x_column;
                    const data = chart.data || [];
                    
                    const xAxisData = data.map(row => row[categoryKey] ?? row.label ?? 'Unknown');
                    const seriesData = data.map(row => Number(row.value) || 0);

                    option = {
                        backgroundColor: 'transparent',
                        color: pbiPalette,
                        tooltip: {
                            trigger: 'axis',
                            axisPointer: { type: 'shadow' }
                        },
                        grid: {
                            left: '3%',
                            right: '4%',
                            bottom: '5%',
                            containLabel: true
                        },
                        xAxis: {
                            type: 'category',
                            data: xAxisData,
                            axisLabel: {
                                interval: 0,
                                rotate: xAxisData.length > 5 ? 30 : 0
                            }
                        },
                        yAxis: {
                            type: 'value'
                        },
                        series: [
                            {
                                name: chart.value_column || 'Value',
                                type: chart.chart_type === 'line' ? 'line' : 'bar',
                                data: seriesData,
                                itemStyle: {
                                    borderRadius: [4, 4, 0, 0]
                                }
                            }
                        ]
                    };
                } else {
                    // Ensure backend options use the PBI color theme
                    if (!option.color) {
                        option.color = pbiPalette;
                    }
                }
                
                // Force transparent background for UI consistency
                option.backgroundColor = 'transparent';
                
                myChart.setOption(option);
                
                // Make chart responsive
                window.addEventListener('resize', () => {
                    myChart.resize();
                });
            });
        }

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
