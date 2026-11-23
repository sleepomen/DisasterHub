"""
一次模擬裡哪些避難所可以當疏散目的地。/api/simulate_disaster 和容量缺口報告（tools/report/capacity_report.py）共用，
報告裡的數字才會跟在地圖上按同一個點、同一種災害時一樣
"""
import logging
from dataclasses import dataclass, field

from services.simulation.evacuation import EXCLUDED_HAZARDS, HAZARD_WARN_M, split_hazard_hits, undesignated

logger = logging.getLogger(__name__)


@dataclass
class Destinations:
    shelters: list
    # 位在潛勢範圍內、不當目的地的避難所 → 所在範圍編號
    excluded: dict[str, list[str]] = field(default_factory=dict)
    # 範圍外 HAZARD_WARN_M 公尺內，照常分配、只附警告
    near: dict[str, dict] = field(default_factory=dict)
    # 消防署資料沒把這間列為這類災害的避難收容處所
    undesignated: list[str] = field(default_factory=list)
    # 這次模擬的路線要繞開哪一類潛勢範圍（淹水 → 土石流）
    hazard: str | None = None


def destinations_for(repo, impacted: list, sim_type: str) -> Destinations:
    """
    位在對應災害潛勢區域內的避難所不當疏散目的地（淹水模擬避開土石流影響範圍），
    範圍外 HAZARD_WARN_M 公尺內的照常分配、只附上警告；沒列為這類災害避難收容處所的也不去。
    查不到潛勢資料時退回「不排除」，跟沒有這個功能時一樣，模擬本身不能因此失敗
    """
    hazard = EXCLUDED_HAZARDS.get(sim_type)
    excluded: dict[str, list[str]] = {}
    near: dict[str, dict] = {}
    if hazard:
        try:
            inside, nearby = split_hazard_hits(repo.shelter_hazard_distances(hazard, HAZARD_WARN_M))
            excluded = {s.name: inside[s.name] for s in impacted if s.name in inside}
            near = {s.name: nearby[s.name] for s in impacted if s.name in nearby}
        except Exception:
            logger.exception("查詢災害潛勢區域失敗，本次模擬不排除任何避難所")
    not_designated = undesignated(impacted, sim_type)
    shelters = [s for s in impacted if s.name not in excluded and s.name not in not_designated]
    return Destinations(shelters, excluded, near, not_designated, hazard)
