{{ config(materialized='view') }}

-- Deployment teams should replace `raw_dataset` with the governed warehouse
-- source relation and keep the source-version column for reproducibility.
select
    *
from {{ source('governed', 'raw_dataset') }}
