"""請求內容的格式與範圍檢查（Pydantic）；超出範圍的請求在進到端點之前就回 422"""
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class SimulateRequest(BaseModel):
    lat: float = Field(..., ge=-90, le=90, description="緯度")
    lon: float = Field(..., ge=-180, le=180, description="經度")
    radius: float = Field(..., gt=0, le=100, description="半徑（公里），最大100km")
    type: str = Field(default="earthquake")
    # 疏散的「近」用哪一種交通方式的時間算：步行（等於道路距離最短）或開車（依道路等級的速度）
    mode: Literal["walk", "drive"] = "walk"

    @field_validator("type")
    @classmethod
    def validate_type(cls, v):
        allowed = {"earthquake", "flood", "fire"}
        if v not in allowed:
            raise ValueError(f"災害類型必須是 {allowed} 其中之一")
        return v


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=500, description="使用者問題")
    # 回答語言。英文模式第一階段只翻回答：問題仍以中文最準（services/web/i18n.py）
    lang: Literal["zh", "en"] = "zh"


class OccupancyItem(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    current_ppl: int = Field(..., ge=0, le=1_000_000, description="目前收容人數")


class OccupancyRequest(BaseModel):
    occupancy: list[OccupancyItem] = Field(..., min_length=1, max_length=1000)


class NearestRequest(BaseModel):
    lat: float = Field(..., ge=-90, le=90, description="緯度")
    lon: float = Field(..., ge=-180, le=180, description="經度")
    limit: int = Field(default=5, ge=1, le=20, description="回傳筆數")
    mode: Literal["walk", "drive"] = "walk"


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=1, max_length=200)
