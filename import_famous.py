import os
import urllib.request
import httpx
import time

urls = {
    'titanic': 'https://raw.githubusercontent.com/datasciencedojo/datasets/master/titanic.csv',
    'iris': 'https://raw.githubusercontent.com/mwaskom/seaborn-data/master/iris.csv',
    'tips': 'https://raw.githubusercontent.com/mwaskom/seaborn-data/master/tips.csv',
    'penguins': 'https://raw.githubusercontent.com/mwaskom/seaborn-data/master/penguins.csv',
    'taxis': 'https://raw.githubusercontent.com/mwaskom/seaborn-data/master/taxis.csv',
    'diamonds': 'https://raw.githubusercontent.com/mwaskom/seaborn-data/master/diamonds.csv',
    'flights': 'https://raw.githubusercontent.com/mwaskom/seaborn-data/master/flights.csv',
    'planets': 'https://raw.githubusercontent.com/mwaskom/seaborn-data/master/planets.csv',
    'mpg': 'https://raw.githubusercontent.com/mwaskom/seaborn-data/master/mpg.csv',
    'fmri': 'https://raw.githubusercontent.com/mwaskom/seaborn-data/master/fmri.csv'
}

os.makedirs('famous_datasets', exist_ok=True)
files = []

print("Downloading 10 Famous Datasets...")
for name, url in urls.items():
    path = f'famous_datasets/{name}.csv'
    try:
        urllib.request.urlretrieve(url, path)
        files.append((name, path))
        print(f"  - Downloaded {name}")
    except Exception as e:
        print(f"  - Failed {name}: {e}")

print("\nUploading to local Automated Data Analyst API (http://127.0.0.1:8000)...")
API_URL = "http://127.0.0.1:8000/api/v1"

with httpx.Client() as client:
    for name, path in files:
        print(f"\nProcessing {name}...")
        try:
            with open(path, 'rb') as f:
                files_payload = {'file': (f"{name}.csv", f, 'text/csv')}
                resp = client.post(f"{API_URL}/datasets/import", files=files_payload)
            
            if resp.status_code != 200:
                print(f"  Import failed: {resp.text}")
                continue
                
            data = resp.json()
            dataset_id = data['dataset_id']
            print(f"  Imported! Dataset ID: {dataset_id}")
            
            print(f"  Generating Dashboard...")
            report_resp = client.get(f"{API_URL}/datasets/{dataset_id}/bi_report", timeout=30.0)
            
            if report_resp.status_code != 200:
                print(f"  Report failed: {report_resp.text}")
                continue
                
            report_data = report_resp.json()
            kpis = len(report_data.get('kpis', []))
            charts = len(report_data.get('charts', []))
            print(f"  Success! Generated {kpis} KPIs and {charts} interactive ECharts.")
            
        except Exception as e:
            print(f"  Error processing {name}: {e}")

print("\nAll done! 10 Famous Datasets have been imported and analyzed.")
