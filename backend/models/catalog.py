from datetime import datetime, timezone

from pydantic import BaseModel, Field


class FootprintBounds(BaseModel):
    west: float
    south: float
    east: float
    north: float


class Footprint(BaseModel):
    id: str
    source_url: str
    center_latitude: float
    center_longitude: float
    bounds: FootprintBounds
    resolution_m_per_pixel: float | None = None
    crs: str
    review_status: str
    reviewed_at: str
    review_note: str


class CatalogItem(BaseModel):
    id: str
    product_id: str
    title: str
    product_type: str
    source: str
    status: str
    source_url: str
    download_url: str | None = None
    image_url: str
    location: str
    resolution: str
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    footprint: Footprint


class CoordinateMatch(BaseModel):
    item: CatalogItem
    contains: bool
    distance_deg: float


class CatalogSource(BaseModel):
    name: str
    url: str
    status: str
    note: str