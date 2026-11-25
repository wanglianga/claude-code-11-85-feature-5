"""冻结排序 Top-N 命中率评估（纯统计内核 + 留痕装配）。

目标量（estimand）：冻结批次 Top-N 候选中“经规定证据确认欺诈”的比例，
而**不是**已办案件的阳性率。待核/右删失案件没有证据标签，绝不计为阴性。

提供两条相互独立的推断路径：

1. 分层随机复核路（设计推断，Horvitz-Thompson）
   - 每层在未核查总体 M_h 中按已知入样概率 π_h=m_h/M_h 简单随机抽样 m_h 件；
   - 层内未核查确认数估计 K_h_hat = k_h/π_h，加上历史已确认数得层总量；
   - 抽样不确定性用**分层超几何似然区间**：给定 (M_h,m_h,k_h)，未核查确认
     总数 K_h 的似然由超几何分布给出，各层独立相乘后对总确认数取 2.5%/97.5%
     分位数；同时给出带有限总体修正的正态区间作对照。

2. 已办样本概率校正路（IPW，模型推断）
   - 历史核查是选择性样本：按已记录的分层核查概率 π_h 加权，
     层确认总量 T_h_hat = y_h/π_h；
   - 区间用**分层重抽样（bootstrap）**：层内对已办标签有放回重抽后重新加权；
   - 依赖关键可识别性假设：核查选择在地区、分数及**已记录**转介等因素条件下
     可忽略（条件独立/CIA）。若关键层 π_h=0（或缺失），或存在同时影响核查与
     欺诈却未记录的因素，则该层选择模型不可识别，不能外推，必须扩大随机复核，
     且不得宣称排序提升命中。

统计函数全部是纯函数（入参为候选 dict 列表），便于离线复核与单元测试。
"""
from __future__ import annotations

import math
import random
from collections import defaultdict
from datetime import datetime

CONFIRMED = "confirmed"
CLEARED = "cleared"
CENSORED = "censored"
RESOLVED = "resolved"
HISTORICAL = "historical"
RANDOM_AUDIT = "random_audit"

BOOTSTRAP_REPS = 2000
BOOTSTRAP_SEED = 20260331
ALPHA = 0.05


# ---------------------------------------------------------------- 基础分布
def hypergeom_pmf(k: int, K: int, M: int, m: int) -> float:
    """P(X=k)：总体 M 中含 K 个阳性，无放回抽 m 个恰有 k 个阳性。"""
    if k < 0 or k > min(K, m) or (m - k) > (M - K):
        return 0.0
    return math.comb(K, k) * math.comb(M - K, m - k) / math.comb(M, m)


def hypergeom_likelihood(k: int, M: int, m: int) -> list[float]:
    """给定观测阳性数 k，返回 K=0..M 的归一化似然（即 K 的似然分布）。"""
    if m <= 0:
        raise ValueError("样本量 m 必须为正")
    raw = [hypergeom_pmf(k, K, M, m) for K in range(M + 1)]
    total = sum(raw)
    return [v / total for v in raw] if total > 0 else [0.0] * (M + 1)


def _quantile(dist: list[float], q: float) -> float:
    """离散分布（下标即取值）的分位数，线性插值。"""
    target = q
    cum = 0.0
    for x, p in enumerate(dist):
        cum += p
        if cum >= target:
            if p <= 0:
                return float(x)
            prev = cum - p
            return x - 1 + (target - prev) / p
    return float(len(dist) - 1)


def _ppct(v: float) -> float:
    return round(100.0 * v, 2)


# ---------------------------------------------------------------- 分层账务
def build_strata(candidates: list[dict], strata_keys: tuple[str, ...] = ("A", "B")) -> dict:
    """从候选行汇总每层的核查账。候选 dict 字段见 routers/hitrate.py 的序列化。"""
    out = {}
    for h in strata_keys:
        rows = [c for c in candidates if c["region"] == h]
        hist = [c for c in rows if c["review_source"] == HISTORICAL]
        audits = [c for c in rows if c["review_source"] == RANDOM_AUDIT]
        n_total = len(rows)
        n_hist = len(hist)
        y_hist = sum(1 for c in hist if c["evidence_label"] == CONFIRMED)
        cleared_hist = sum(1 for c in hist if c["evidence_label"] == CLEARED)
        m = len(audits)
        k = sum(1 for c in audits if c["evidence_label"] == CONFIRMED)
        cleared_audit = sum(1 for c in audits if c["evidence_label"] == CLEARED)
        n_unreviewed = n_total - n_hist
        # 右删失/待核：无证据标签，不计阴性
        censored = [c for c in rows if c["censor_status"] == CENSORED
                    or c["evidence_label"] is None]
        # 已记录核查概率（历史选择模型），层内应为同一口径
        probs = {c["review_probability"] for c in hist
                 if c["review_probability"] is not None}
        pi_hist = next(iter(probs)) if len(probs) == 1 else (
            sum(probs) / len(probs) if probs else None)
        # 随机复核入样概率 = 抽样比；与候选行登记值核对
        pi_audit_design = (m / n_unreviewed) if n_unreviewed else None
        pi_audit_rows = {c["review_probability"] for c in audits
                         if c["review_probability"] is not None}
        pi_consistent = (not pi_audit_rows) or (
            pi_audit_design is not None
            and all(abs(p - pi_audit_design) < 1e-9 for p in pi_audit_rows))
        out[h] = {
            "region": h, "N": n_total,
            "n_hist": n_hist, "y_hist": y_hist, "cleared_hist": cleared_hist,
            "M_unreviewed": n_unreviewed, "m_audit": m, "k_audit": k,
            "cleared_audit": cleared_audit,
            "n_censored": len(censored),
            "pi_hist": pi_hist,
            "pi_audit": pi_audit_design,
            "pi_audit_consistent": pi_consistent,
            "audit_rows": audits, "hist_rows": hist, "censored_rows": censored,
        }
    return out


# ---------------------------------------------------------------- 路径一：随机复核
def random_audit_path(strata: dict) -> dict:
    """分层随机复核 HT 点估计 + 分层超几何似然 95% 区间 + 正态对照区间。"""
    blocked = []
    point_total = 0.0
    var_total = 0.0
    per_stratum = {}
    # 联合似然：各层未核查确认数 K_h 独立 → 总确认数分布逐层卷积
    joint = [1.0]  # 下标=总确认数（先只放未核查部分，历史确认数最后平移）
    hist_confirmed_total = 0
    for h, s in strata.items():
        M, m, k = s["M_unreviewed"], s["m_audit"], s["k_audit"]
        y_hist = s["y_hist"]
        hist_confirmed_total += y_hist
        if m == 0:
            blocked.append({"stratum": h, "reason": "该层没有任何随机复核样本，"
                            "未核查总体的确认数无设计推断依据"})
            per_stratum[h] = {**_public_stratum(s), "ht_estimate_total": None,
                              "ci95_exact": None}
            continue
        pi = m / M
        k_hat = k / pi  # = M*k/m，未核查部分确认数的 HT 估计
        t_hat = y_hist + k_hat
        point_total += t_hat
        # 有限总体修正的正态方差：M^2 (1-f) p(1-p)/(m-1)
        p = k / m
        denom = max(1, m - 1)
        var_h = (M ** 2) * (1 - pi) * p * (1 - p) / denom
        var_total += var_h
        se_h = math.sqrt(var_h)
        # 精确超几何似然（针对未核查部分 K_h）
        lik = hypergeom_likelihood(k, M, m)
        # 与历史确认数卷积，得到该层总确认数分布
        layer = [0.0] * (M + y_hist + 1)
        for K, pr in enumerate(lik):
            layer[K + y_hist] = pr
        new_joint = [0.0] * (len(joint) + len(layer) - 1)
        for a, pa in enumerate(joint):
            for b, pb in enumerate(layer):
                new_joint[a + b] += pa * pb
        joint = new_joint
        per_stratum[h] = {
            **_public_stratum(s),
            "inclusion_pi": round(pi, 4),
            "unreviewed_confirmed_hat": round(k_hat, 2),
            "ht_estimate_total": round(t_hat, 2),
            "se_total": round(se_h, 3),
            "ci95_normal": [_ppct(max(0, (t_hat - 1.96 * se_h)) / s["N"]),
                            _ppct(min(s["N"], (t_hat + 1.96 * se_h)) / s["N"])],
            "ci95_exact": [_ppct(_quantile(layer, ALPHA / 2) / s["N"]),
                           _ppct(_quantile(layer, 1 - ALPHA / 2) / s["N"])],
        }
    N = sum(s["N"] for s in strata.values())
    identifiable = not blocked
    result = {
        "method": "stratified_random_audit_ht",
        "method_name": "分层随机复核（Horvitz-Thompson + 分层超几何区间）",
        "identifiable": identifiable,
        "blocked_strata": blocked,
        "per_stratum": per_stratum,
        "N": N,

    }
    if identifiable:
        lo = _quantile(joint, ALPHA / 2)
        hi = _quantile(joint, 1 - ALPHA / 2)
        se_rate = math.sqrt(var_total) / N
        result.update({
            "estimated_confirmed": round(point_total, 2),
            "point_estimate": _ppct(point_total / N),
            "se_pp": round(100 * se_rate, 2),
            "ci95_method": "分层超几何似然中心 95% 区间（各层独立卷积）",
            "ci95": [_ppct(lo / N), _ppct(hi / N)],
            "ci95_count": [round(lo, 1), round(hi, 1)],
            "ci95_normal": [_ppct(max(0, point_total - 1.96 * math.sqrt(var_total)) / N),
                            _ppct(min(N, point_total + 1.96 * math.sqrt(var_total)) / N)],
            "formula": "T_h = y_hist_h + k_h/(m_h/M_h)；rate = ΣT_h / N",
        })
    else:
        result.update({
            "estimated_confirmed": None, "point_estimate": None,
            "ci95": None, "ci95_method": None,
            "required_action": "对被阻断层扩大分层随机复核，取得概率样本前不得外推该层",
        })
    return result


# ---------------------------------------------------------------- 路径二：IPW
def ipw_path(strata: dict, reps: int = BOOTSTRAP_REPS, seed: int = BOOTSTRAP_SEED) -> dict:
    """已办样本按记录核查概率 1/π 加权；分层 bootstrap 给 95% 区间。"""
    rng = random.Random(seed)
    blocked = []
    point_total = 0.0
    var_total = 0.0
    per_stratum = {}
    boot_totals = [0.0] * reps
    N = sum(s["N"] for s in strata.values())
    for h, s in strata.items():
        n, y = s["n_hist"], s["y_hist"]
        pi = s["pi_hist"]
        if n == 0 or pi is None or pi <= 0:
            blocked.append({
                "stratum": h,
                "reason": ("历史核查概率为零/缺失，逆概率权重无定义"
                           if (n == 0 or pi is None or pi <= 0) else ""),
            })
            per_stratum[h] = {**_public_stratum(s), "ipw_estimate_total": None,
                              "ci95_bootstrap": None}
            continue
        t_hat = y / pi
        point_total += t_hat
        # 解析方差（二项近似）：n p(1-p)/π²
        p = y / n
        var_h = n * p * (1 - p) / (pi ** 2)
        var_total += var_h
        labels = [1] * y + [0] * (n - y)
        # 分层重抽样：每层独立有放回重抽 n 个标签，重新 1/π 加权
        for r in range(reps):
            boot_totals[r] += sum(labels[rng.randrange(n)] for _ in range(n)) / pi
        per_stratum[h] = {
            **_public_stratum(s),
            "review_pi": round(pi, 4),
            "weight": round(1 / pi, 3),
            "ipw_estimate_total": round(t_hat, 2),
            "analytic_se_total": round(math.sqrt(var_h), 3),
        }
    identifiable = not blocked
    result = {
        "method": "adjudicated_ipw_bootstrap",
        "method_name": "已办样本概率校正（IPW + 分层重抽样区间）",
        "identifiable": identifiable,
        "blocked_strata": blocked,
        "per_stratum": per_stratum,
        "N": N,
        "assumption": "核查选择在地区、分数及已记录转介等因素条件下可忽略（CIA）；"
                      "π 为已记录的分层核查概率",
    }
    if identifiable:
        rates = sorted(t / N for t in boot_totals)
        lo = rates[int(ALPHA / 2 * reps)]
        hi = rates[min(reps - 1, int((1 - ALPHA / 2) * reps))]
        se_rate = math.sqrt(var_total) / N
        result.update({
            "estimated_confirmed": round(point_total, 2),
            "point_estimate": _ppct(point_total / N),
            "se_pp": round(100 * se_rate, 2),
            "ci95_method": f"分层 bootstrap 百分位区间（{reps} 次，种子 {seed}）",
            "ci95": [_ppct(lo), _ppct(hi)],
            "ci95_normal": [_ppct(max(0, point_total - 1.96 * math.sqrt(var_total)) / N),
                            _ppct(min(N, point_total + 1.96 * math.sqrt(var_total)) / N)],
            "bootstrap_reps": reps,
            "formula": "T_h = y_hist_h / π_h；rate = ΣT_h / N",
        })
    else:
        result.update({
            "estimated_confirmed": None, "point_estimate": None,
            "ci95": None, "ci95_method": None,
            "required_action": "零概率/不可识别层须改用随机复核概率样本，"
                               "不得用选择模型外推，不得宣称排序提升命中",
        })
    return result


# ---------------------------------------------------------------- 可识别性
def identifiability_check(candidates: list[dict], strata: dict) -> dict:
    """检查零核查概率与未记录混杂；随机复核路不受选择混杂影响。"""
    issues_ipw, issues_random = [], []
    unrecorded = []
    for c in candidates:
        factors = c.get("recorded_factors") or {}
        if factors.get("unrecorded_dual_use"):
            unrecorded.append({
                "candidate_code": c["candidate_code"], "region": c["region"],
                "factor": factors.get("factor_name") or "未命名双重用途因素",
                "detail": "该因素同时影响核查转介与欺诈概率，但未记录在选择模型中",
            })
    for h, s in strata.items():
        if s["n_hist"] > 0 and (s["pi_hist"] is None or s["pi_hist"] <= 0):
            issues_ipw.append({"stratum": h,
                               "reason": "关键层历史核查概率为零或缺失，逆概率权重无定义"})
        if s["m_audit"] == 0:
            issues_random.append({"stratum": h,
                                  "reason": "关键层无随机复核样本，设计推断无法覆盖"})
    if unrecorded:
        issues_ipw.append({"stratum": "ALL",
                           "reason": "存在同时影响核查与欺诈却未记录的因素，"
                                     "条件独立假设不成立，选择模型不可识别"})
    random_ok = not issues_random
    ipw_ok = not issues_ipw
    return {
        "random_audit_identifiable": random_ok,
        "ipw_identifiable": ipw_ok,
        "random_audit_issues": issues_random,
        "ipw_issues": issues_ipw,
        "unrecorded_confounders": unrecorded,
        # 概率抽样是金标准：只要随机复核覆盖全部层，命中率结论仍可成立
        "can_claim_ranking_lift": random_ok,
        "rule": "待核/右删失案件不计阴性；确认标签、删失状态、概率预测分别存储；"
                "不可识别层不得外推，须先扩大分层随机复核",
    }


# ---------------------------------------------------------------- 对比与总装
def compare_paths(rand_res: dict, ipw_res: dict) -> dict:
    if not (rand_res["identifiable"] and ipw_res["identifiable"]):
        return {"overlap": None, "divergence_pp": None, "verdict": "存在不可识别层，"
                "两路不可直接比较，先按 required_action 补样"}
    a, b = rand_res["ci95"], ipw_res["ci95"]
    overlap_lo = max(a[0], b[0])
    overlap_hi = min(a[1], b[1])
    overlap = overlap_lo <= overlap_hi
    diff = round(rand_res["point_estimate"] - ipw_res["point_estimate"], 2)
    if overlap and abs(diff) <= 5:
        verdict = "两路 95% 区间重叠且点估计差≤5pp：结论一致，偏差可控，持续随机复核校准"
    elif overlap:
        verdict = "区间重叠但点估计存在分歧：以随机复核路为准，持续补样并核查 IPW 选择假设"
    else:
        verdict = "两路区间不重叠：选择模型可能受未记录混杂影响，不得据此宣称排序提升命中"
    return {
        "overlap": overlap,
        "overlap_interval": [round(overlap_lo, 2), round(overlap_hi, 2)] if overlap else None,
        "divergence_pp": diff,
        "random_minus_ipw_pp": diff,
        "verdict": verdict,
    }


def naive_adjudicated_rate(strata: dict) -> dict:
    """已办案件阳性率（对照展示，非目标量）。"""
    n = sum(s["n_hist"] + s["m_audit"] for s in strata.values())
    y = sum(s["y_hist"] + s["k_audit"] for s in strata.values())
    return {
        "reviewed": n, "confirmed": y,
        "rate_pp": _ppct(y / n) if n else None,
        "warning": "这是已办案件阳性率，受核查选择偏差影响，不是冻结候选集命中率估计，"
                   "禁止用它替代目标量或宣称排序提升",
    }


def evaluate_hitrate(candidates: list[dict],
                     strata_keys: tuple[str, ...] = ("A", "B")) -> dict:
    """完整评估：分层账 → 两路估计 → 可识别性 → 对比结论。"""
    strata = build_strata(candidates, strata_keys)
    rand_res = random_audit_path(strata)
    ipw_res = ipw_path(strata)
    ident = identifiability_check(candidates, strata)
    # 用统一可识别性检查回写路径标志（如未记录混杂只在 ident 检查中可见）
    if not ident["random_audit_identifiable"]:
        rand_res["identifiable"] = False
        for issue in ident["random_audit_issues"]:
            if issue not in rand_res["blocked_strata"]:
                rand_res["blocked_strata"].append(issue)
        rand_res["required_action"] = "对被阻断层扩大分层随机复核，取得概率样本前不得外推该层"
    if not ident["ipw_identifiable"]:
        ipw_res["identifiable"] = False
        for issue in ident["ipw_issues"]:
            if issue not in ipw_res["blocked_strata"]:
                ipw_res["blocked_strata"].append(issue)
        ipw_res["required_action"] = ("零概率/未记录混杂层不可用选择模型外推，须扩大随机复核，"
                                      "且不得宣称排序提升命中")
    comparison = compare_paths(rand_res, ipw_res)
    conclusion = _conclusion(rand_res, ipw_res, ident, comparison)
    return {
        "evaluated_at": datetime.utcnow().isoformat() + "Z",
        "estimand": "冻结 Top-N 候选中经规定证据确认欺诈的比例（非已办阳性率）",
        "censor_rule": "右删失/待核件无证据标签，不计阴性、不进已办率分母以外的阴性推断",
        "strata_accounting": [_public_stratum(s) for s in strata.values()],
        "random_audit": rand_res,
        "ipw": ipw_res,
        "identifiability": ident,
        "comparison": comparison,
        "naive_adjudicated": naive_adjudicated_rate(strata),
        "conclusion": conclusion,
    }


def _conclusion(rand_res, ipw_res, ident, comparison) -> str:
    parts = []
    if rand_res["identifiable"]:
        parts.append(f"随机复核路点估计 {rand_res['point_estimate']}%，"
                     f"95% 区间 {rand_res['ci95'][0]}%~{rand_res['ci95'][1]}%")
    else:
        parts.append("随机复核路存在不可识别层："
                     + "；".join(x["reason"] for x in rand_res["blocked_strata"]))
    if ipw_res["identifiable"]:
        parts.append(f"概率校正路点估计 {ipw_res['point_estimate']}%，"
                     f"95% 区间 {ipw_res['ci95'][0]}%~{ipw_res['ci95'][1]}%")
    else:
        parts.append("概率校正路不可识别："
                     + "；".join(x["reason"] for x in ipw_res["blocked_strata"]))
    parts.append(comparison["verdict"])
    if not ident["can_claim_ranking_lift"]:
        parts.append("当前不得宣称排序提升命中，须先扩大分层随机复核")
    return "。".join(parts) + "。"


def _public_stratum(s: dict) -> dict:
    """去掉内部行集合，保留可展示的账务字段。"""
    return {k: v for k, v in s.items()
            if k not in ("audit_rows", "hist_rows", "censored_rows")}
