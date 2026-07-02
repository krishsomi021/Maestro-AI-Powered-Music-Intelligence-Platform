from enum import StrEnum


class JobType(StrEnum):
    ML_INFERENCE = "ml_inference"
    ETL_PIPELINE = "etl_pipeline"
    REPORT_GENERATION = "report_generation"
    EXPAND_CATALOG = "expand_catalog"


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    RETRYING = "retrying"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"
