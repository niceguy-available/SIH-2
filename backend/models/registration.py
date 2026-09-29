from datetime import datetime, timezone
from typing import Any, Literal
from pydantic import BaseModel, Field, ConfigDict

class Thresholds(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    min_inliers: int = Field(default=20, ge=20, le=10000)
    min_inlier_ratio: float = Field(default=0.5, ge=0.5, le=1)
    max_rmse: float = Field(default=1.5, gt=0, le=1.5)
    min_overlap: float = Field(default=0.2, ge=0.2, le=1)
    min_coverage: float = Field(default=0.25, ge=0.25, le=1)
    max_checkpoint_rmse: float = Field(default=2.0, gt=0, le=2.0)

class Checkpoint(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    source_x: float = Field(ge=0)
    source_y: float = Field(ge=0)
    reference_x: float = Field(ge=0)
    reference_y: float = Field(ge=0)

class CheckpointSubmission(BaseModel):
    points: list[Checkpoint] = Field(min_length=3, max_length=100)
    provenance: str = Field(min_length=10, max_length=1000)
    independent: Literal[True]

class Adjustment(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    offset_x: float = Field(ge=-20, le=20)
    offset_y: float = Field(ge=-20, le=20)

class RunRecord(BaseModel):
    model_config = ConfigDict(extra='allow')
    id: str
    status: str
    progress: int = 0
    stage: str
    source_filename: str
    generated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    diagnostics: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] | None = None
    match_points: list[dict[str, Any]] = Field(default_factory=list)
    quality_gate: dict[str, Any] = Field(default_factory=lambda: {'passed': False, 'export_allowed': False, 'checks': []})
    thresholds: Thresholds = Field(default_factory=Thresholds)

class ReferenceRecord(BaseModel):
    model_config = ConfigDict(extra='allow')
    id: str
    title: str
    status: str
    image_url: str
    metadata: dict[str, Any]