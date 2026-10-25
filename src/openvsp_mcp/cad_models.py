"""Typed CAD export inputs; OpenVSP model coordinates have no inherent unit."""

from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from .operations import GeometryOperationRequest


class ExportRequest(GeometryOperationRequest):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    format: Literal["step", "iges"] = Field(
        ..., description="CAD interchange format: STEP (.stp) or IGES (.igs)."
    )
    length_unit: Literal["mm", "cm", "m", "in", "ft"] = Field(
        ...,
        description="Unit of existing model coordinates, recorded in the CAD file. Coordinates are not rescaled. Supply the model's actual length unit.",
    )
    export_mode: Literal["standard", "split_and_stitch"] = Field(
        "standard",
        description="Standard exports surface quilts. Split-and-stitch splits each body's feature lines and joins that body's surfaces; it does not intersect, trim or fuse different components.",
    )
    tolerance: float | None = Field(
        None,
        ge=1e-12,
        le=1e12,
        description="STEP export tolerance in model length units; defaults to 1e-6. IGES has no equivalent native setting and rejects this field.",
    )
    step_representation: Literal["shell", "brep"] | None = Field(
        None,
        description="Only for split-and-stitch STEP: shell or BREP representation (default brep). BREP output does not guarantee valid or closed solids.",
    )

    @model_validator(mode="after")
    def applicable_settings(self):
        if self.format == "iges" and self.tolerance is not None:
            raise ValueError("tolerance is supported only for STEP export")
        if self.step_representation is not None and (
            self.format != "step" or self.export_mode != "split_and_stitch"
        ):
            raise ValueError("step_representation requires split_and_stitch STEP export")
        return self

    @property
    def effective_tolerance(self) -> float | None:
        return (
            (self.tolerance if self.tolerance is not None else 1e-6)
            if self.format == "step"
            else None
        )

    @property
    def effective_representation(self) -> str | None:
        if self.format == "step" and self.export_mode == "split_and_stitch":
            return self.step_representation or "brep"
        return None
