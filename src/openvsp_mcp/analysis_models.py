"""Explicit, bounded inputs for native geometry measurements."""

from __future__ import annotations

import math
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from .operations import GeometryOperationRequest


class GeometryAnalysisRequest(GeometryOperationRequest):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    analysis: Literal["comp_geom", "projection"] = Field(
        description="comp_geom computes tessellated wetted areas/volumes; projection computes the union projected area without a boundary or convex hull."
    )
    length_unit: Literal["mm", "cm", "m", "in", "ft"] = Field(
        description="Required declaration of existing model length units. No geometry scaling or unit conversion is performed; areas and volumes use its square and cube."
    )
    projection_direction: Literal["x", "y", "z"] | tuple[float, float, float] | None = Field(
        None,
        description="Required for projection, forbidden for comp_geom. Model-coordinate viewing direction: x, y, z, or a finite nonzero [x,y,z] vector normalized before execution.",
    )

    @model_validator(mode="after")
    def check_direction(self):
        if self.analysis == "comp_geom":
            if self.projection_direction is not None:
                raise ValueError("projection_direction is only valid for projection")
        elif self.projection_direction is None:
            raise ValueError("projection requires an explicit projection_direction")
        elif isinstance(self.projection_direction, tuple):
            # Scaling first avoids overflow/underflow for finite extreme inputs.
            largest = max(abs(value) for value in self.projection_direction)
            if largest == 0 or not math.isfinite(largest):
                raise ValueError("projection_direction must be a finite nonzero vector")
            scaled = tuple(value / largest for value in self.projection_direction)
            norm = math.hypot(*scaled)
            self.projection_direction = tuple(value / norm for value in scaled)
        return self

    @property
    def direction_vector(self) -> tuple[float, float, float] | None:
        if isinstance(self.projection_direction, str):
            return {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}[
                self.projection_direction
            ]
        return self.projection_direction
