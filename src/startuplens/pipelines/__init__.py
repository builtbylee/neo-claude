"""Data ingestion pipelines for SEC EDGAR, Companies House, and academic datasets."""

from __future__ import annotations

from startuplens.pipelines.academic_datasets import (
    import_kingscrowd,
    import_kleinert,
    import_signori_vismara,
    import_walthoff_borm,
    run_academic_pipeline,
)
from startuplens.pipelines.companies_house import (
    run_companies_house_pipeline,
)
from startuplens.pipelines.manual_research import (
    run_manual_import,
)
from startuplens.pipelines.sec_edgar import (
    download_form_c_index,
    ingest_form_c_batch,
    normalize_form_c_record,
    parse_form_c_filings,
    run_sec_pipeline,
)

__all__ = [
    # SEC EDGAR
    "download_form_c_index",
    "ingest_form_c_batch",
    "normalize_form_c_record",
    "parse_form_c_filings",
    "run_sec_pipeline",
    # Companies House
    "run_companies_house_pipeline",
    # Academic datasets
    "import_kingscrowd",
    "import_kleinert",
    "import_signori_vismara",
    "import_walthoff_borm",
    "run_academic_pipeline",
    # Manual research
    "run_manual_import",
]
