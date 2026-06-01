from enum import StrEnum


class JobType(StrEnum):
    ML_INFERENCE = "ml_inference"
    ETL_PIPELINE = "etl_pipeline"
    REPORT_GENERATION = "report_generation"


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
