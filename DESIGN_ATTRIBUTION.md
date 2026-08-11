# Dashboard design reference

This product integrates a small, auditable subset of the published design
assets from [Dashboard-Design/Power-BI-Design-Files](https://github.com/Dashboard-Design/Power-BI-Design-Files)
into web-native, data-driven dashboard templates. The runtime does not require
Power BI Desktop: it keeps the repository's visual language while computing all
KPIs, charts, tables, and filters from the selected dataset.

Integrated source assets:

- `frontend/assets/powerbi/ecommerce_background.svg` — `Full Dashboards/Ecommerce Conversion Dashboard/Images/Background.svg`, source SHA `2aa8cda50c542870ed427264625418c09edf50cd`.
- `frontend/assets/powerbi/bibb_free_default.json` — `Theme .JSON Files/BIBB - Free Default.json`, source SHA `9f6331034e7d0d1f8242894fa414dc2865d31c08`.
- `frontend/assets/powerbi/teals.json` — `Theme .JSON Files/kerrykolosko - Teals.json`, source SHA `97184407089b1d98c93ffea335643cbcc3a0279e`.
- `frontend/assets/powerbi/executive_sales_reference.css` — `Full Dashboards/Exceutive Sales Report/style.css`, source SHA `09875c7d29a25134292dfee1662673b23a3cd410`.

The source-backed templates expose their repository path, SHA, theme asset, and
`web-native adaptation` status through the dashboard-template API. The original
`.pbix` files and external Power BI runtime are intentionally not bundled;
datasets remain user-provided and report generation stays inside this app. The
repository's native SVG Button Slicer README is used as the interaction
reference for `powerbi_kpi_slicer` (source SHA
`569cf96af2959ea9be0f67e9daaf56892fbb5a59`).

The reference repository is licensed under the MIT License:

Copyright (c) 2024 Sajjad Ahmadi

Permission is hereby granted, free of charge, to any person obtaining a copy of
the Software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies
of the Software, and to permit persons to whom the Software is furnished to do
so, subject to the following conditions: The above copyright notice and this
permission notice shall be included in all copies or substantial portions of
the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
