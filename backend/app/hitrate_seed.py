"""冻结排序命中率评估演示数据（幂等，随 run_seed 一起写入）。

主批次 FB-2026Q1-TOP10PCT：冻结 1000 件线索池中排序前 10%（100 件），
按地区 A/B 各 50 件分层，核查账严格对应题设：
- A：历史核查 40（确认 20），未核查 10 中随机复核 5（确认 2），余 5 件待核删失；
- B：历史核查 10（确认 3），未核查 40 中随机复核 10（确认 2），余 30 件待核删失。
已记录分层核查概率 A=0.8、B=0.2；随机复核入样概率 A=0.5、B=0.25。

压力批次 FB-STRESS-UNRECORDED：B 层历史核查概率为零且无随机复核，
并存在未记录的转介关系混杂，用于演示“不可识别 → 不得外推/不得宣称提升”的护栏。

所有数据均为虚构；确认标签、删失状态、概率预测分三列分别保存，待核件不计阴性。
"""
from datetime import date, datetime

from .models import FreezeBatch, FreezeCandidate

CUTOFF = date(2026, 3, 31)

REGION_A = "A"
REGION_B = "B"


def _dt(y, m, d):
    return datetime(y, m, d, 15, 0)


def _score(rank_no: int) -> float:
    jitter = 0.15 * ((rank_no * 7) % 5 - 2)
    return round(98.5 - 0.38 * (rank_no - 1) + jitter, 2)


def _band(score: float) -> str:
    if score >= 85:
        return "high"
    if score >= 70:
        return "mid"
    return "low"


def _factors(band: str, referral: bool, unrecorded: bool = False,
             factor_name: str | None = None) -> dict:
    return {
        "selection_model": "logit(region, score_band, referral_recorded)",
        "score_band": band,
        "referral_recorded": referral,
        "unrecorded_dual_use": unrecorded,
        "factor_name": factor_name,
    }


def _add(db, batch_id, rank_no, code, region, source, label, pi, basis,
         evidence, censored, factors, reviewed_at):
    db.add(FreezeCandidate(
        batch_id=batch_id, rank_no=rank_no, candidate_code=code, region=region,
        frozen_score=_score(rank_no), score_band=_band(_score(rank_no)),
        review_source=source, review_probability=pi, probability_basis=basis,
        evidence_label=label, evidence_codes=evidence, reviewed_at=reviewed_at,
        censor_status="resolved" if censored is False else "censored",
        propensity_score=pi, recorded_factors=factors,
    ))


def seed_main_batch(db) -> FreezeBatch:
    batch = FreezeBatch(
        code="FB-2026Q1-TOP10PCT",
        title="2026Q1 冻结排序 Top-10% 命中率评估批次（A/B 各 50 件）",
        ranking_version="rule+v2026.1/graphmodel@run-1",
        cutoff_date=CUTOFF,
        scope={
            "target": "top_10pct",
            "universe_size": 1000,
            "top_n": 100,
            "score_field": "frozen composite rank score（规则结构+图模型校准分）",
            "strata": ["A", "B"],
            "stratum_size": 50,
            "frozen_at": "2026-03-31T23:59:59",
        },
        note="候选集与名次在截止日冻结；确认标签/删失状态/概率预测分别存储，"
             "待核件右删失不计阴性。",
    )
    db.add(batch)
    db.flush()

    pi_a, pi_b = 0.8, 0.2
    basis_a = ("历史核查选择模型 logit(地区,分数段,已记录转介因素) 拟合的 A 层条件核查概率 "
               "π=0.80")
    basis_b = ("历史核查选择模型 logit(地区,分数段,已记录转介因素) 拟合的 B 层条件核查概率 "
               "π=0.20")
    audit_plan = "分层随机复核方案 AS-2026Q1：未核查总体简单随机抽样，入样概率 π=m/M"

    seq = {"A": 0, "B": 0}
    for rank in range(1, 101):
        region = REGION_A if rank % 2 == 1 else REGION_B
        seq[region] += 1
        i = seq[region]
        code = f"HC-{rank:03d}"
        band = _band(_score(rank))
        referral = (rank * 3) % 4 == 0
        if region == REGION_A:
            if i <= 40:  # 历史核查 40：20 确认 / 20 排除
                confirmed = i <= 20
                label = "confirmed" if confirmed else "cleared"
                ev = ([f"EV-HA-C{i:02d}（稽核询问笔录+异常结算比对单）"] if confirmed
                      else [f"EV-HA-X{i:02d}（进销存/签收材料给出合法解释）"])
                _add(db, batch.id, rank, code, region, "historical", label, pi_a,
                     basis_a, ev, False, _factors(band, referral),
                     _dt(2026, 2, 1 + i % 28))
            elif i <= 45:  # 随机复核 5：2 确认 / 3 排除
                confirmed = i in (41, 42)
                label = "confirmed" if confirmed else "cleared"
                ev = ([f"EV-RA-C{i-40:02d}（随机复核取证确认书）"] if confirmed
                      else [f"EV-RA-X{i-40:02d}（随机复核排除材料）"])
                _add(db, batch.id, rank, code, region, "random_audit", label, 0.5,
                     f"{audit_plan}，A 层 π=5/10=0.50", ev, False,
                     _factors(band, referral), _dt(2026, 4, 8 + i % 8))
            else:  # 5 件待核右删失
                _add(db, batch.id, rank, code, region, "none", None, None, None,
                     [], True, _factors(band, referral), None)
        else:
            if i <= 10:  # 历史核查 10：3 确认 / 7 排除
                confirmed = i <= 3
                label = "confirmed" if confirmed else "cleared"
                ev = ([f"EV-HB-C{i:02d}（稽核询问笔录+异常结算比对单）"] if confirmed
                      else [f"EV-HB-X{i:02d}（进销存/签收材料给出合法解释）"])
                _add(db, batch.id, rank, code, region, "historical", label, pi_b,
                     basis_b, ev, False, _factors(band, referral),
                     _dt(2026, 2, 1 + i % 20))
            elif i <= 20:  # 随机复核 10：2 确认 / 8 排除
                confirmed = i in (11, 12)
                label = "confirmed" if confirmed else "cleared"
                ev = ([f"EV-RB-C{i-10:02d}（随机复核取证确认书）"] if confirmed
                      else [f"EV-RB-X{i-10:02d}（随机复核排除材料）"])
                _add(db, batch.id, rank, code, region, "random_audit", label, 0.25,
                     f"{audit_plan}，B 层 π=10/40=0.25", ev, False,
                     _factors(band, referral), _dt(2026, 4, 8 + i % 10))
            else:  # 30 件待核右删失
                _add(db, batch.id, rank, code, region, "none", None, None, None,
                     [], True, _factors(band, referral), None)
    db.flush()
    return batch


def seed_stress_batch(db) -> FreezeBatch:
    """不可识别压力批次：B 层零核查概率 + 未记录转介混杂。"""
    batch = FreezeBatch(
        code="FB-STRESS-UNRECORDED",
        title="压力批次：B 层零核查概率且转介混杂未记录（不可识别演示）",
        ranking_version="rule+v2026.1/graphmodel@run-1",
        cutoff_date=CUTOFF,
        scope={
            "target": "top_10pct", "universe_size": 200, "top_n": 20,
            "score_field": "frozen composite rank score",
            "strata": ["A", "B"], "stratum_size": 10,
            "frozen_at": "2026-03-31T23:59:59",
        },
        note="用于验证护栏：关键层核查概率为零/未记录混杂时不得外推，"
             "须扩大随机复核，且不得宣称排序提升命中。",
    )
    db.add(batch)
    db.flush()
    for rank in range(1, 21):
        region = REGION_A if rank % 2 == 1 else REGION_B
        i = (rank + 1) // 2  # 层内序号 1..10
        code = f"HC-S{rank:02d}"
        band = _band(_score(rank))
        if region == REGION_A:
            if i <= 8:  # 历史核查 8：4 确认
                confirmed = i <= 4
                _add(db, batch.id, rank, code, region, "historical",
                     "confirmed" if confirmed else "cleared", 0.8,
                     "A 层条件核查概率 π=0.80",
                     [f"EV-SA-{'C' if confirmed else 'X'}{i:02d}"],
                     False, _factors(band, i % 2 == 0),
                     _dt(2026, 2, 2 + i))
            elif i == 9:  # 1 件随机复核确认
                _add(db, batch.id, rank, code, region, "random_audit", "confirmed",
                     0.5, "A 层 π=1/2=0.50", [f"EV-SRA-C{i:02d}"], False,
                     _factors(band, False), _dt(2026, 4, 9))
            else:  # 1 件删失
                _add(db, batch.id, rank, code, region, "none", None, None, None,
                     [], True, _factors(band, False), None)
        else:
            # B 层：无历史核查（π=0）、无随机复核，全部右删失；
            # 转介人关系同时影响转介核查与欺诈，却未进入选择模型
            _add(db, batch.id, rank, code, region, "none", None, None, None,
                 [], True,
                 _factors(band, True, unrecorded=True,
                          factor_name="转介人关系（未记录）"), None)
    db.flush()
    return batch


def seed_freeze_hitrate(db):
    if db.query(FreezeBatch).count() > 0:
        return
    seed_main_batch(db)
    seed_stress_batch(db)
    db.commit()
