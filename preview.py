import requests
import json
import os

API_URL = 'http://127.0.0.1:8000/api/v1'
datasets = requests.get(f'{API_URL}/datasets').json()
if not datasets:
    print('No datasets found.')
    exit()

latest_id = datasets[0]['id']
report = requests.get(f'{API_URL}/datasets/{latest_id}/bi_report').json()

md = f'# Dashboard Preview: {datasets[0]["name"]}\n\n'
md += '## Key Performance Indicators\n\n'
md += '<div style="display: flex; gap: 20px; flex-wrap: wrap; margin-bottom: 20px;">\n'

for kpi in report.get('kpis', []):
    md += f'<div style="border: 1px solid #444; padding: 15px; border-radius: 8px; min-width: 150px;">\n'
    md += f'  <p style="margin:0; font-size: 0.9em; color: #aaa;">{kpi["label"]}</p>\n'
    val = kpi.get('formatted_value', kpi.get('value', '--'))
    md += f'  <h3 style="margin: 5px 0 0 0;">{val}</h3>\n'
    md += f'</div>\n'
md += '</div>\n\n'

md += '## Interactive ECharts Configurations\n\n'
for i, chart in enumerate(report.get('charts', [])):
    title = chart.get('title', f'Chart {i+1}')
    md += f'### {title}\n'
    md += '*(Rendered natively in browser via Apache ECharts)*\n\n'
    md += '```json\n'
    md += json.dumps(chart.get('echarts_option', {}), indent=2)
    md += '\n```\n\n'

with open('C:/Users/HP/.gemini/antigravity/brain/36f3baa3-de25-4382-9f15-2d930d33b614/dashboard_preview.md', 'w') as f:
    f.write(md)

print('Created dashboard_preview.md artifact!')
