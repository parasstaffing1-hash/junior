import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import httpx
import os

np.random.seed(42)

num_rows = 50000
dates = [datetime(2024, 1, 1) + timedelta(days=np.random.randint(0, 365)) for _ in range(num_rows)]
stores = np.random.choice(['Store A', 'Store B', 'Store C', 'Store D', 'Store E'], num_rows)
countries = np.random.choice(['USA', 'Canada', 'UK', 'Germany', 'France'], num_rows)
categories = np.random.choice(['Electronics', 'Clothing', 'Home', 'Beauty', 'Sports'], num_rows)
products = [f'{cat} Product {np.random.randint(1, 10)}' for cat in categories]

units = np.random.randint(1, 20, num_rows)
unit_price = np.random.uniform(10.0, 500.0, num_rows)
sales = units * unit_price
# Profit margin between 5% and 30%
profit = sales * np.random.uniform(0.05, 0.30, num_rows)

df = pd.DataFrame({
    'Order Date': dates,
    'Country': countries,
    'Store ID': stores,
    'Segment': categories,
    'Product Name': products,
    'Units Sold': units,
    'Sales Amount': sales,
    'Gross Profit': profit
})

file_path = 'enterprise_retail_sales.csv'
df.to_csv(file_path, index=False)
print(f'Generated {file_path} with 50,000 rows')

API_URL = 'http://127.0.0.1:8000/api/v1'

print('Uploading to BI Platform...')
with httpx.Client(timeout=60.0) as client:
    with open(file_path, 'rb') as f:
        resp = client.post(f'{API_URL}/datasets/import', files={'file': (file_path, f, 'text/csv')})
        
    if resp.status_code == 200:
        dataset_id = resp.json()['dataset_id']
        print(f'Imported successfully! Dataset ID: {dataset_id}')
        
        print('Generating Dashboard...')
        report = client.get(f'{API_URL}/datasets/{dataset_id}/bi_report')
        
        if report.status_code == 200:
            data = report.json()
            kpis = data.get('kpis', [])
            charts = data.get('charts', [])
            print(f"Success! Generated {len(kpis)} KPIs and {len(charts)} ECharts.")
        else:
            print('Dashboard generation failed:', report.text)
    else:
        print('Import failed:', resp.text)
