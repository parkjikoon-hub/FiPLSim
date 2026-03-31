#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FiPLSim: 보강 시뮬레이션 — 리뷰어 방어용 추가 자료
====================================================
Phase 1: K값 민감도 분석 (OAT sweep + corner cases)
Phase 2: 결함 배치 패턴 비교 (uniform_random, downstream, upstream)
Phase 3: 양방향 불균형 케이스 (비대칭 입구관 길이)

기존 run_uni_bi_final.py 수정 없이 독립 실행.
결과: supplementary_results/ 하위에 저장.

실행:
  PYTHONIOENCODING=utf-8 python3 run_supplementary_sims.py --phase 1
  PYTHONIOENCODING=utf-8 python3 run_supplementary_sims.py --phase 1 --smoke
  PYTHONIOENCODING=utf-8 python3 run_supplementary_sims.py --phase 1 --heads 7 --spacing 2.1
  PYTHONIOENCODING=utf-8 python3 run_supplementary_sims.py --phase 2
  PYTHONIOENCODING=utf-8 python3 run_supplementary_sims.py --phase 3
  PYTHONIOENCODING=utf-8 python3 run_supplementary_sims.py --phase ALL
"""

import sys
import os
import argparse
import time
import math
import json
import importlib
import warnings
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd

# ── 프로젝트 루트를 sys.path에 추가 (paper/scripts/ → 루트) ──
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

# ── 물성치 오버라이드 (논문 통합물성치) ──────────────────
import constants
constants.EPSILON_MM = 0.046
constants.EPSILON_M = 0.046 / 1000.0
constants.RHO = 1000.0
constants.MU = 1.002e-3
constants.NU = constants.MU / constants.RHO

import hydraulics
importlib.reload(hydraulics)
import pipe_network as pn
importlib.reload(pn)

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

_KOREAN_FONTS = ["Malgun Gothic", "맑은 고딕", "NanumGothic", "AppleGothic"]
for _fname in _KOREAN_FONTS:
    if any(_fname in f.name for f in fm.fontManager.ttflist):
        plt.rcParams["font.family"] = _fname
        plt.rcParams["axes.unicode_minus"] = False
        break

warnings.filterwarnings("ignore", category=RuntimeWarning)

# ══════════════════════════════════════════════════════════
#  고정 상수 (run_uni_bi_final.py와 동일)
# ══════════════════════════════════════════════════════════
HEADS_PER_BRANCH = 8
NUM_BRANCHES = 4
HEAD_FLOW_LPM = 80.0
ACTIVE_HEADS = NUM_BRANCHES * HEADS_PER_BRANCH
DESIGN_FLOW_LPM = ACTIVE_HEADS * HEAD_FLOW_LPM
BRANCH_SPACING_M = 3.5
HEAD_SPACING_M = 2.1
BRANCH_INLET_CONFIG = "80A-65A"
SUPPLY_PIPE_SIZE = "100A"
USE_HEAD_FITTING = True
REDUCER_MODE = "crane"
PASS_THRESHOLD_MPA = 0.1
P_REF = None  # main()에서 자동 역산

# 기본 K값 (기준값)
K1_BASE = constants.K1_BASE
K2 = constants.K2
K3_DEFAULT = constants.K3                        # 1.0 (bi 분기)
K_ELBOW_DEFAULT = constants.K_ELBOW_90_65A       # 0.53 (uni 분기)
K_TEE_BRANCH_DEFAULT = constants.K_TEE_BRANCH_80A  # 1.06 (bi T분기)

MC_ITER = 10_000

EQUIPMENT_K_FACTORS = {
    "알람밸브 (습식)":   {"K": 2.0,  "qty": 1},
    "유수검지장치":      {"K": 1.0,  "qty": 1},
    "게이트밸브 (전개)": {"K": 0.15, "qty": 2},
    "체크밸브 (스윙형)": {"K": 2.0,  "qty": 1},
    "90도 엘보":        {"K": 0.75, "qty": 1},
    "리듀서 (점축소)":   {"K": 0.15, "qty": 1},
}

TOPOS = ["uni", "bi"]
BEAD_DET = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
DEFECT_COUNTS = [0, 1, 2, 3, 4]
BEAD_MC = [1.0, 1.5, 2.0, 2.5]
BEAD_STD = [0.25, 0.50, 0.75]

OUT_ROOT = Path("supplementary_results")


# ══════════════════════════════════════════════════════════
#  유틸리티
# ══════════════════════════════════════════════════════════

def elapsed(t0):
    dt = time.time() - t0
    if dt < 60:
        return f"{dt:.1f}초"
    return f"{dt/60:.1f}분"


def save_csv(df, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"    CSV 저장: {path}")


def wilson_ci95(p_hat, n):
    if n == 0:
        return 0.0, 0.0
    z = 1.96
    denom = 1 + z**2 / n
    centre = (p_hat + z**2 / (2 * n)) / denom
    margin = z * math.sqrt((p_hat * (1 - p_hat) + z**2 / (4 * n)) / n) / denom
    return max(0.0, centre - margin), min(1.0, centre + margin)


# ══════════════════════════════════════════════════════════
#  K값 파라미터화된 핵심 함수
# ══════════════════════════════════════════════════════════

def _gen_params_k(topo):
    """토폴로지별 generate_dynamic_system 파라미터 (K값 무관 — 구조만)"""
    return dict(
        heads_per_branch=HEADS_PER_BRANCH,
        branch_spacing_m=BRANCH_SPACING_M,
        head_spacing_m=HEAD_SPACING_M,
        K1_base=K1_BASE,
        K2_val=K2,
        use_head_fitting=USE_HEAD_FITTING,
        branch_inlet_config="80A-65A-elbow" if topo == "uni" else BRANCH_INLET_CONFIG,
    )


def _common_calc_params():
    return dict(
        reducer_mode=REDUCER_MODE,
        supply_pipe_size=SUPPLY_PIPE_SIZE,
    )


def worst_branch_positions(num_branches, defect_count):
    worst = num_branches - 1
    return [(worst, h) for h in range(min(defect_count, HEADS_PER_BRANCH))]


def build_beads_2d(num_branches, positions, bead_mm, bead_std=0.0, rng=None):
    beads = [[0.0] * HEADS_PER_BRANCH for _ in range(num_branches)]
    for b, h in positions:
        if bead_std > 0 and rng is not None:
            beads[b][h] = max(0.0, rng.normal(bead_mm, bead_std))
        else:
            beads[b][h] = bead_mm
    return beads


def calc_equipment_loss_mpa(total_flow_lpm):
    supply_id_m = constants.get_inner_diameter_m(SUPPLY_PIPE_SIZE)
    V = hydraulics.velocity_from_flow(total_flow_lpm, supply_id_m)
    total = 0.0
    for info in EQUIPMENT_K_FACTORS.values():
        total += hydraulics.head_to_mpa(
            hydraulics.minor_loss(info["K"], V)
        ) * info.get("qty", 1)
    return total


def calc_tee_split_loss_mpa(total_flow_lpm, k_tee_branch, cross_main_size="80A"):
    """양방향 T분기 손실 — K_TEE_BRANCH를 파라미터로 받음"""
    id_m = constants.get_inner_diameter_m(cross_main_size)
    V = hydraulics.velocity_from_flow(total_flow_lpm, id_m)
    return hydraulics.head_to_mpa(hydraulics.minor_loss(k_tee_branch, V))


# ──────────────────────────────────────────────────────────
#  K값 파라미터화 결정론 실행
# ──────────────────────────────────────────────────────────

def run_det_k(topo, defect_count, bead_mm, p_in, flow,
              k_elbow, k3_bi, k_tee_branch):
    """K값을 외부에서 받는 결정론 실행"""
    if topo == "uni":
        num_br = 4
        positions = worst_branch_positions(num_br, defect_count)
        beads_2d = build_beads_2d(num_br, positions, bead_mm)
        sys = pn.generate_dynamic_system(
            num_branches=num_br,
            inlet_pressure_mpa=p_in,
            total_flow_lpm=flow,
            bead_heights_2d=beads_2d,
            **_gen_params_k("uni"),
        )
        res = pn.calculate_dynamic_system(
            sys, k_elbow,
            equipment_k_factors=EQUIPMENT_K_FACTORS,
            **_common_calc_params(),
        )
        return {
            "terminal_mpa": res["worst_terminal_mpa"],
            "loss_pipe_mpa": res["loss_pipe_mpa"],
            "loss_fitting_mpa": res["loss_fitting_mpa"],
            "loss_bead_mpa": res["loss_bead_mpa"],
            "cross_main_loss_mpa": res["cross_main_cumulative"],
            "tee_split_loss_mpa": 0.0,
            "equipment_loss_mpa": res["equipment_loss_mpa"],
        }
    else:  # bi
        num_br = 2
        equip_loss = calc_equipment_loss_mpa(flow)
        tee_loss = calc_tee_split_loss_mpa(flow, k_tee_branch)
        p_side = p_in - equip_loss - tee_loss
        half_flow = flow / 2.0
        positions = worst_branch_positions(num_br, defect_count)
        beads_2d = build_beads_2d(num_br, positions, bead_mm)
        sys = pn.generate_dynamic_system(
            num_branches=num_br,
            inlet_pressure_mpa=p_side,
            total_flow_lpm=half_flow,
            bead_heights_2d=beads_2d,
            **_gen_params_k("bi"),
        )
        res = pn.calculate_dynamic_system(
            sys, k3_bi,
            equipment_k_factors=None,
            **_common_calc_params(),
        )
        return {
            "terminal_mpa": res["worst_terminal_mpa"],
            "loss_pipe_mpa": res["loss_pipe_mpa"],
            "loss_fitting_mpa": res["loss_fitting_mpa"] + equip_loss + tee_loss,
            "loss_bead_mpa": res["loss_bead_mpa"],
            "cross_main_loss_mpa": res["cross_main_cumulative"],
            "tee_split_loss_mpa": tee_loss,
            "equipment_loss_mpa": equip_loss,
        }


def bisect_critical_k(topo, defect_count, bead_mm, flow,
                      k_elbow, k3_bi, k_tee_branch,
                      p_lo=0.35, p_hi=0.75, tol=0.0005, max_iter=40):
    """K값 파라미터화 이분탐색"""
    for _ in range(max_iter):
        p_mid = (p_lo + p_hi) / 2.0
        r = run_det_k(topo, defect_count, bead_mm, p_mid, flow,
                      k_elbow, k3_bi, k_tee_branch)
        if r["terminal_mpa"] >= PASS_THRESHOLD_MPA:
            p_hi = p_mid
        else:
            p_lo = p_mid
        if (p_hi - p_lo) < tol:
            break
    return p_hi


def bisect_pref_k(k_elbow, k3_bi, k_tee_branch):
    """K값 조합에 대한 P_REF 역산 (uni 기준, dc=0, bead=0)"""
    return round(
        bisect_critical_k("uni", 0, 0.0, DESIGN_FLOW_LPM,
                          k_elbow, k3_bi, k_tee_branch,
                          tol=0.000001),
        6
    )


# ──────────────────────────────────────────────────────────
#  K값 파라미터화 MC 실행
# ──────────────────────────────────────────────────────────

def run_mc_k(topo, defect_count, bead_mm, bead_std, p_in, flow,
             k_elbow, k3_bi, k_tee_branch,
             mc_iter=MC_ITER, seed=42):
    """K값을 외부에서 받는 MC 실행"""
    rng = np.random.default_rng(seed)
    terminals = np.empty(mc_iter)

    if topo == "uni":
        num_br = 4
        positions = worst_branch_positions(num_br, defect_count)
        for i in range(mc_iter):
            beads_2d = build_beads_2d(num_br, positions, bead_mm, bead_std, rng)
            sys = pn.generate_dynamic_system(
                num_branches=num_br, inlet_pressure_mpa=p_in,
                total_flow_lpm=flow, bead_heights_2d=beads_2d,
                **_gen_params_k("uni"),
            )
            res = pn.calculate_dynamic_system(
                sys, k_elbow, equipment_k_factors=EQUIPMENT_K_FACTORS,
                **_common_calc_params(),
            )
            terminals[i] = res["worst_terminal_mpa"]
    else:  # bi
        num_br = 2
        equip_loss = calc_equipment_loss_mpa(flow)
        tee_loss = calc_tee_split_loss_mpa(flow, k_tee_branch)
        p_side = p_in - equip_loss - tee_loss
        half_flow = flow / 2.0
        positions = worst_branch_positions(num_br, defect_count)
        for i in range(mc_iter):
            beads_2d = build_beads_2d(num_br, positions, bead_mm, bead_std, rng)
            sys = pn.generate_dynamic_system(
                num_branches=num_br, inlet_pressure_mpa=p_side,
                total_flow_lpm=half_flow, bead_heights_2d=beads_2d,
                **_gen_params_k("bi"),
            )
            res = pn.calculate_dynamic_system(
                sys, k3_bi, equipment_k_factors=None,
                **_common_calc_params(),
            )
            terminals[i] = res["worst_terminal_mpa"]

    fail_count = int(np.sum(terminals < PASS_THRESHOLD_MPA))
    fail_rate = fail_count / mc_iter
    ci_low, ci_high = wilson_ci95(fail_rate, mc_iter)
    p5, p50, p95 = np.percentile(terminals, [5, 50, 95])

    return {
        "fail_rate": fail_rate,
        "fail_rate_CI95_low": ci_low,
        "fail_rate_CI95_high": ci_high,
        "mean_terminal_mpa": float(np.mean(terminals)),
        "std_terminal_mpa": float(np.std(terminals)),
        "P5_terminal_mpa": float(p5),
        "P50_terminal_mpa": float(p50),
        "P95_terminal_mpa": float(p95),
    }


# ══════════════════════════════════════════════════════════
#  Phase 1: K값 민감도 분석
# ══════════════════════════════════════════════════════════

# OAT 스윕 정의
K_SWEEP = {
    "K_ELBOW": {
        "values": [0.45, 0.53, 0.60, 0.75],
        "fixed": {"K3_bi": 1.0, "K_TEE": 1.06},
    },
    "K3_bi": {
        "values": [0.9, 1.0, 1.1],
        "fixed": {"K_ELBOW": 0.53, "K_TEE": 1.06},
    },
    "K_TEE": {
        "values": [0.90, 1.06, 1.20],
        "fixed": {"K_ELBOW": 0.53, "K3_bi": 1.0},
    },
}

# Corner cases
K_CORNERS = [
    {"label": "elbow_low_tee_low",   "K_ELBOW": 0.45, "K3_bi": 1.0, "K_TEE": 0.90},
    {"label": "elbow_low_tee_high",  "K_ELBOW": 0.45, "K3_bi": 1.0, "K_TEE": 1.20},
    {"label": "elbow_high_tee_low",  "K_ELBOW": 0.75, "K3_bi": 1.0, "K_TEE": 0.90},
    {"label": "elbow_high_tee_high", "K_ELBOW": 0.75, "K3_bi": 1.0, "K_TEE": 1.20},
]


def _run_k_combo_campaigns(label, k_elbow, k3_bi, k_tee, out_dir):
    """하나의 K 조합에 대해 A1 + B1 + C2 캠페인 실행"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # P_REF 역산 (이 K조합 기준)
    p_ref = bisect_pref_k(k_elbow, k3_bi, k_tee)

    # PIN_TRANS 동적 생성
    _p_lo = round((p_ref - 0.005) / 0.002) * 0.002
    pin_trans = [round(_p_lo + i * 0.002, 3) for i in range(14)]

    print(f"\n  [{label}] K_ELBOW={k_elbow}, K3_bi={k3_bi}, K_TEE={k_tee}")
    print(f"    P_REF = {p_ref} MPa")

    # ── A1: 결정론 비교 ──
    t0 = time.time()
    rows_a1 = []
    for topo in TOPOS:
        for bead in BEAD_DET:
            for dc in DEFECT_COUNTS:
                r = run_det_k(topo, dc, bead, p_ref, DESIGN_FLOW_LPM,
                              k_elbow, k3_bi, k_tee)
                rows_a1.append({
                    "campaign": "A1", "topology": topo,
                    "total_flow_lpm": DESIGN_FLOW_LPM,
                    "inlet_pressure_mpa": p_ref,
                    "defect_count": dc, "bead_height_mm": bead,
                    "terminal_mpa": r["terminal_mpa"],
                    "pressure_margin_mpa": r["terminal_mpa"] - PASS_THRESHOLD_MPA,
                    "pass_fail": "PASS" if r["terminal_mpa"] >= PASS_THRESHOLD_MPA else "FAIL",
                    "loss_pipe_mpa": r["loss_pipe_mpa"],
                    "loss_fitting_mpa": r["loss_fitting_mpa"],
                    "loss_bead_mpa": r["loss_bead_mpa"],
                    "tee_split_loss_mpa": r["tee_split_loss_mpa"],
                    "equipment_loss_mpa": r["equipment_loss_mpa"],
                    "K_ELBOW": k_elbow, "K3_bi": k3_bi, "K_TEE": k_tee,
                })
    df_a1 = pd.DataFrame(rows_a1)
    save_csv(df_a1, out_dir / "A1_deterministic.csv")
    print(f"    A1 완료: {len(rows_a1)}건 ({elapsed(t0)})")

    # ── B1: 임계압력 비교 ──
    t0 = time.time()
    rows_b1 = []
    for topo in TOPOS:
        for bead in BEAD_DET:
            for dc in DEFECT_COUNTS:
                p_crit = bisect_critical_k(topo, dc, bead, DESIGN_FLOW_LPM,
                                           k_elbow, k3_bi, k_tee)
                rows_b1.append({
                    "campaign": "B1", "topology": topo,
                    "total_flow_lpm": DESIGN_FLOW_LPM,
                    "defect_count": dc, "bead_height_mm": bead,
                    "critical_inlet_mpa": round(p_crit, 6),
                    "K_ELBOW": k_elbow, "K3_bi": k3_bi, "K_TEE": k_tee,
                })
    df_b1 = pd.DataFrame(rows_b1)
    save_csv(df_b1, out_dir / "B1_critical_pressure.csv")
    print(f"    B1 완료: {len(rows_b1)}건 ({elapsed(t0)})")

    # ── C2: 기준선 ──
    t0 = time.time()
    rows_c2 = []
    for topo in TOPOS:
        for p_in in pin_trans:
            r = run_det_k(topo, 0, 0.0, p_in, DESIGN_FLOW_LPM,
                          k_elbow, k3_bi, k_tee)
            rows_c2.append({
                "campaign": "C2", "topology": topo,
                "total_flow_lpm": DESIGN_FLOW_LPM,
                "inlet_pressure_mpa": p_in,
                "defect_count": 0, "bead_height_mm": 0.0,
                "terminal_mpa": r["terminal_mpa"],
                "pass_fail": "PASS" if r["terminal_mpa"] >= PASS_THRESHOLD_MPA else "FAIL",
                "K_ELBOW": k_elbow, "K3_bi": k3_bi, "K_TEE": k_tee,
            })
    df_c2 = pd.DataFrame(rows_c2)
    save_csv(df_c2, out_dir / "C2_baseline.csv")
    print(f"    C2 완료: {len(rows_c2)}건 ({elapsed(t0)})")

    return {
        "label": label,
        "K_ELBOW": k_elbow, "K3_bi": k3_bi, "K_TEE": k_tee,
        "P_REF": p_ref,
        "A1_rows": len(rows_a1), "B1_rows": len(rows_b1), "C2_rows": len(rows_c2),
    }


def run_phase1(scenario_tag):
    """Phase 1: K 민감도 OAT 스윕 + Corner Cases"""
    print("\n" + "=" * 70)
    print(f"  Phase 1: K값 민감도 분석 [{scenario_tag}]")
    print("=" * 70)
    t_total = time.time()

    base_dir = OUT_ROOT / "k_sensitivity"
    summary_rows = []

    # ── OAT 스윕 ──
    for sweep_var, cfg in K_SWEEP.items():
        for val in cfg["values"]:
            if sweep_var == "K_ELBOW":
                ke, k3, kt = val, cfg["fixed"]["K3_bi"], cfg["fixed"]["K_TEE"]
            elif sweep_var == "K3_bi":
                ke, k3, kt = cfg["fixed"]["K_ELBOW"], val, cfg["fixed"]["K_TEE"]
            else:  # K_TEE
                ke, k3, kt = cfg["fixed"]["K_ELBOW"], cfg["fixed"]["K3_bi"], val

            label = f"{scenario_tag}_{sweep_var}_{val}"
            out_dir = base_dir / label
            info = _run_k_combo_campaigns(label, ke, k3, kt, out_dir)
            summary_rows.append(info)

    # ── Corner Cases ──
    for corner in K_CORNERS:
        label = f"{scenario_tag}_{corner['label']}"
        out_dir = base_dir / label
        info = _run_k_combo_campaigns(
            label, corner["K_ELBOW"], corner["K3_bi"], corner["K_TEE"], out_dir
        )
        summary_rows.append(info)

    # ── A1 gap 분석: 각 K 조합에서 uni(dc=0,bead=0) vs bi(dc=0,bead=0) 비교 ──
    gap_rows = []
    for info in summary_rows:
        out_dir = base_dir / info["label"]
        df_a1 = pd.read_csv(out_dir / "A1_deterministic.csv")
        # bead=0, dc=0 기준
        uni_row = df_a1[(df_a1["topology"] == "uni") &
                        (df_a1["defect_count"] == 0) &
                        (df_a1["bead_height_mm"] == 0.0)]
        bi_row = df_a1[(df_a1["topology"] == "bi") &
                       (df_a1["defect_count"] == 0) &
                       (df_a1["bead_height_mm"] == 0.0)]

        uni_t = uni_row["terminal_mpa"].values[0] if len(uni_row) > 0 else None
        bi_t = bi_row["terminal_mpa"].values[0] if len(bi_row) > 0 else None

        # B1 gap: bead=2.0, dc=4 기준 (worst case)
        df_b1 = pd.read_csv(out_dir / "B1_critical_pressure.csv")
        uni_b1 = df_b1[(df_b1["topology"] == "uni") &
                       (df_b1["defect_count"] == 4) &
                       (df_b1["bead_height_mm"] == 2.0)]
        bi_b1 = df_b1[(df_b1["topology"] == "bi") &
                      (df_b1["defect_count"] == 4) &
                      (df_b1["bead_height_mm"] == 2.0)]

        uni_crit = uni_b1["critical_inlet_mpa"].values[0] if len(uni_b1) > 0 else None
        bi_crit = bi_b1["critical_inlet_mpa"].values[0] if len(bi_b1) > 0 else None

        gap_rows.append({
            "label": info["label"],
            "K_ELBOW": info["K_ELBOW"],
            "K3_bi": info["K3_bi"],
            "K_TEE": info["K_TEE"],
            "P_REF": info["P_REF"],
            "uni_terminal_dc0_b0": uni_t,
            "bi_terminal_dc0_b0": bi_t,
            "A1_gap_mpa": (uni_t - bi_t) if (uni_t and bi_t) else None,
            "uni_remains_better": (uni_t > bi_t) if (uni_t and bi_t) else None,
            "uni_crit_dc4_b2": uni_crit,
            "bi_crit_dc4_b2": bi_crit,
            "B1_gap_mpa": (bi_crit - uni_crit) if (uni_crit and bi_crit) else None,
            "bi_needs_more_pressure": (bi_crit > uni_crit) if (uni_crit and bi_crit) else None,
        })

    df_summary = pd.DataFrame(gap_rows)
    save_csv(df_summary, base_dir / "summary_k_sensitivity.csv")

    # 결과 출력
    print(f"\n  {'='*70}")
    print(f"  Phase 1 결과 요약 [{scenario_tag}]")
    print(f"  {'='*70}")
    all_robust = True
    for row in gap_rows:
        status = "OK" if row["uni_remains_better"] else "REVERSED"
        if not row["uni_remains_better"]:
            all_robust = False
        print(f"    {row['label']}: A1_gap={row['A1_gap_mpa']:.6f} MPa "
              f"B1_gap={row['B1_gap_mpa']:.6f} MPa [{status}]")

    if all_robust:
        print(f"\n  >>> 모든 K 조합에서 uni가 bi보다 우수 — 결론 강건 <<<")
    else:
        print(f"\n  >>> 일부 K 조합에서 역전 발생 — 조건부 결론 필요 <<<")

    print(f"\n  Phase 1 전체 완료: {elapsed(t_total)}")
    return df_summary


# ══════════════════════════════════════════════════════════
#  Phase 2: 결함 배치 패턴 비교
# ══════════════════════════════════════════════════════════

def placement_worst_branch(num_branches, heads_per_branch, defect_count, rng=None):
    """기존 worst_branch_concentrated 배치"""
    worst = num_branches - 1
    return [(worst, h) for h in range(min(defect_count, heads_per_branch))]


def placement_uniform_random(num_branches, heads_per_branch, defect_count, rng):
    """모든 위치에 균등 확률 배치"""
    total = num_branches * heads_per_branch
    flat = rng.choice(total, size=min(defect_count, total), replace=False)
    return [(int(idx // heads_per_branch), int(idx % heads_per_branch)) for idx in flat]


def placement_downstream_biased(num_branches, heads_per_branch, defect_count, rng):
    """최악 branch에서 하류(말단) 편향 배치: w_i = i+1"""
    worst = num_branches - 1
    weights = np.array([i + 1 for i in range(heads_per_branch)], dtype=float)
    weights /= weights.sum()
    n = min(defect_count, heads_per_branch)
    chosen = rng.choice(heads_per_branch, size=n, replace=False, p=weights)
    return [(worst, int(h)) for h in chosen]


def placement_upstream_biased(num_branches, heads_per_branch, defect_count, rng):
    """최악 branch에서 상류(입구) 편향 배치: w_i = N-i"""
    worst = num_branches - 1
    weights = np.array([heads_per_branch - i for i in range(heads_per_branch)], dtype=float)
    weights /= weights.sum()
    n = min(defect_count, heads_per_branch)
    chosen = rng.choice(heads_per_branch, size=n, replace=False, p=weights)
    return [(worst, int(h)) for h in chosen]


PLACEMENT_FUNCS = {
    "worst": placement_worst_branch,
    "uniform": placement_uniform_random,
    "downstream": placement_downstream_biased,
    "upstream": placement_upstream_biased,
}


def run_mc_placement(topo, defect_count, bead_mm, bead_std, p_in, flow,
                     placement_func, mc_iter=MC_ITER, seed=20260331):
    """배치 패턴 선택 가능한 MC 실행 (기준 K값 사용)"""
    rng = np.random.default_rng(seed)
    terminals = np.empty(mc_iter)

    k_elbow = K_ELBOW_DEFAULT
    k3_bi = K3_DEFAULT
    k_tee = K_TEE_BRANCH_DEFAULT

    if topo == "uni":
        num_br = 4
        for i in range(mc_iter):
            positions = placement_func(num_br, HEADS_PER_BRANCH, defect_count, rng)
            beads_2d = build_beads_2d(num_br, positions, bead_mm, bead_std, rng)
            sys = pn.generate_dynamic_system(
                num_branches=num_br, inlet_pressure_mpa=p_in,
                total_flow_lpm=flow, bead_heights_2d=beads_2d,
                **_gen_params_k("uni"),
            )
            res = pn.calculate_dynamic_system(
                sys, k_elbow, equipment_k_factors=EQUIPMENT_K_FACTORS,
                **_common_calc_params(),
            )
            terminals[i] = res["worst_terminal_mpa"]
    else:  # bi
        num_br = 2
        equip_loss = calc_equipment_loss_mpa(flow)
        tee_loss = calc_tee_split_loss_mpa(flow, k_tee)
        p_side = p_in - equip_loss - tee_loss
        half_flow = flow / 2.0
        for i in range(mc_iter):
            positions = placement_func(num_br, HEADS_PER_BRANCH, defect_count, rng)
            beads_2d = build_beads_2d(num_br, positions, bead_mm, bead_std, rng)
            sys = pn.generate_dynamic_system(
                num_branches=num_br, inlet_pressure_mpa=p_side,
                total_flow_lpm=half_flow, bead_heights_2d=beads_2d,
                **_gen_params_k("bi"),
            )
            res = pn.calculate_dynamic_system(
                sys, k3_bi, equipment_k_factors=None,
                **_common_calc_params(),
            )
            terminals[i] = res["worst_terminal_mpa"]

    fail_count = int(np.sum(terminals < PASS_THRESHOLD_MPA))
    fail_rate = fail_count / mc_iter
    ci_low, ci_high = wilson_ci95(fail_rate, mc_iter)
    p5, p50, p95 = np.percentile(terminals, [5, 50, 95])

    return {
        "fail_rate": fail_rate,
        "fail_rate_CI95_low": ci_low,
        "fail_rate_CI95_high": ci_high,
        "mean_terminal_mpa": float(np.mean(terminals)),
        "std_terminal_mpa": float(np.std(terminals)),
        "P5_terminal_mpa": float(p5),
        "P50_terminal_mpa": float(p50),
        "P95_terminal_mpa": float(p95),
    }


def run_phase2(scenario_tag, mc_iter=MC_ITER):
    """Phase 2: 결함 배치 패턴 비교 (MC) — 핵심 비교 집중"""
    print("\n" + "=" * 70)
    print(f"  Phase 2: 결함 배치 패턴 비교 [{scenario_tag}] MC={mc_iter}")
    print("=" * 70)
    t_total = time.time()

    base_dir = OUT_ROOT / "placement_patterns"

    # P_REF (기준 K값 기준)
    p_ref = bisect_pref_k(K_ELBOW_DEFAULT, K3_DEFAULT, K_TEE_BRANCH_DEFAULT)

    # 핵심 입구압 5포인트 (P_REF 중심)
    pin_focus = [
        round(p_ref - 0.004, 4),
        round(p_ref - 0.002, 4),
        round(p_ref, 6),
        round(p_ref + 0.002, 4),
        round(p_ref + 0.004, 4),
    ]

    print(f"  P_REF = {p_ref} MPa (기준 K값)")
    print(f"  MC iterations = {mc_iter}")

    summary_rows = []
    # 핵심 파라미터만: bead=[1.5, 2.5], std=[0.5], dc=[2, 4]
    focus_bead = [1.5, 2.5]
    focus_std = [0.50]
    focus_dc = [2, 4]

    for pname, pfunc in PLACEMENT_FUNCS.items():
        label = f"{scenario_tag}_{pname}"
        out_dir = base_dir / label
        out_dir.mkdir(parents=True, exist_ok=True)
        print(f"\n  [{label}] 배치 패턴: {pname}")

        # ── MC 비교 (핵심 조합만) ──
        t0 = time.time()
        rows = []
        total = len(TOPOS) * len(focus_bead) * len(focus_std) * len(focus_dc) * len(pin_focus)
        done = 0

        for topo in TOPOS:
            for bead_mm in focus_bead:
                for bead_std in focus_std:
                    for dc in focus_dc:
                        for p_in in pin_focus:
                            r = run_mc_placement(
                                topo, dc, bead_mm, bead_std,
                                p_in, DESIGN_FLOW_LPM, pfunc,
                                mc_iter=mc_iter,
                            )
                            rows.append({
                                "campaign": "C1_focused",
                                "topology": topo,
                                "placement": pname,
                                "total_flow_lpm": DESIGN_FLOW_LPM,
                                "inlet_pressure_mpa": p_in,
                                "defect_count": dc,
                                "bead_height_mm": bead_mm,
                                "bead_height_std_mm": bead_std,
                                "mc_iterations": mc_iter,
                                **r,
                            })
                            done += 1
                            if done % 4 == 0:
                                pct = done / total * 100
                                print(f"    MC: {done}/{total} ({pct:.0f}%) ({elapsed(t0)})")

        df = pd.DataFrame(rows)
        save_csv(df, out_dir / "C1_focused_reliability.csv")
        print(f"    MC 완료: {len(rows)}건 ({elapsed(t0)})")

        # 패턴별 대표 통계 (P_REF, dc=4, bead=2.5, std=0.5)
        ref_rows = df[
            (df["defect_count"] == 4) &
            (df["bead_height_mm"] == 2.5) &
            (df["bead_height_std_mm"] == 0.50) &
            (abs(df["inlet_pressure_mpa"] - p_ref) < 0.001)
        ]
        for topo in TOPOS:
            tr = ref_rows[ref_rows["topology"] == topo]
            if len(tr) > 0:
                summary_rows.append({
                    "scenario": scenario_tag,
                    "placement": pname,
                    "topology": topo,
                    "defect_count": 4,
                    "bead_mm": 2.5,
                    "fail_rate": tr["fail_rate"].values[0],
                    "fail_CI95_low": tr["fail_rate_CI95_low"].values[0],
                    "fail_CI95_high": tr["fail_rate_CI95_high"].values[0],
                    "P50_terminal": tr["P50_terminal_mpa"].values[0],
                    "mean_terminal": tr["mean_terminal_mpa"].values[0],
                    "mc_iterations": mc_iter,
                })

    df_summary = pd.DataFrame(summary_rows)
    if len(df_summary) > 0:
        save_csv(df_summary, base_dir / "summary_placement.csv")

        # 결과 출력
        print(f"\n  {'='*70}")
        print(f"  Phase 2 결과 요약 [{scenario_tag}]")
        print(f"  {'='*70}")
        all_robust = True
        for pname in PLACEMENT_FUNCS:
            uni_r = df_summary[(df_summary["placement"] == pname) &
                               (df_summary["topology"] == "uni")]
            bi_r = df_summary[(df_summary["placement"] == pname) &
                              (df_summary["topology"] == "bi")]
            if len(uni_r) > 0 and len(bi_r) > 0:
                uni_fr = uni_r["fail_rate"].values[0]
                bi_fr = bi_r["fail_rate"].values[0]
                status = "OK" if bi_fr >= uni_fr else "REVERSED"
                if bi_fr < uni_fr:
                    all_robust = False
                print(f"    {pname}: uni_fail={uni_fr:.4f} bi_fail={bi_fr:.4f} [{status}]")

        if all_robust:
            print(f"\n  >>> 모든 배치 패턴에서 bi가 uni 이상 fail — 결론 강건 <<<")
        else:
            print(f"\n  >>> 일부 패턴에서 역전 — 조건부 결론 <<<")

    print(f"\n  Phase 2 전체 완료: {elapsed(t_total)}")
    return df_summary


# ══════════════════════════════════════════════════════════
#  Phase 3: 양방향 불균형 케이스
# ══════════════════════════════════════════════════════════

def run_phase3(scenario_tag):
    """Phase 3: 양방향 불균형 (비대칭 입구관 길이)"""
    print("\n" + "=" * 70)
    print(f"  Phase 3: 양방향 불균형 케이스 [{scenario_tag}]")
    print("=" * 70)
    t_total = time.time()

    base_dir = OUT_ROOT / "bi_imbalance"
    k_elbow = K_ELBOW_DEFAULT
    k3_bi = K3_DEFAULT
    k_tee = K_TEE_BRANCH_DEFAULT

    p_ref = bisect_pref_k(k_elbow, k3_bi, k_tee)
    print(f"  P_REF = {p_ref} MPa")

    # 불균형 케이스 정의
    # BI-0: 대칭 (기준선) — 기존 모델 그대로
    # BI-1: 오른쪽 branch +1 HEAD_SPACING
    # BI-2: 오른쪽 branch +2 HEAD_SPACING
    # BI-3: 한쪽 결함 집중 (beads_2d 비대칭)

    imbalance_cases = [
        {"name": "BI-0_symmetric", "inlet_extra_m": 0.0, "defect_side": "both"},
        {"name": "BI-1_plus1", "inlet_extra_m": HEAD_SPACING_M * 1, "defect_side": "both"},
        {"name": "BI-2_plus2", "inlet_extra_m": HEAD_SPACING_M * 2, "defect_side": "both"},
        {"name": "BI-3_defect_one_side", "inlet_extra_m": 0.0, "defect_side": "right_only"},
    ]

    summary_rows = []

    for case in imbalance_cases:
        label = f"{scenario_tag}_{case['name']}"
        out_dir = base_dir / label
        out_dir.mkdir(parents=True, exist_ok=True)
        print(f"\n  [{label}] extra={case['inlet_extra_m']:.1f}m, "
              f"defect_side={case['defect_side']}")

        # ── A1: 결정론 비교 ──
        t0 = time.time()
        rows = []

        for bead in BEAD_DET:
            for dc in DEFECT_COUNTS:
                # uni는 항상 동일
                r_uni = run_det_k("uni", dc, bead, p_ref, DESIGN_FLOW_LPM,
                                  k_elbow, k3_bi, k_tee)
                rows.append({
                    "campaign": "A1", "topology": "uni",
                    "case": case["name"],
                    "total_flow_lpm": DESIGN_FLOW_LPM,
                    "inlet_pressure_mpa": p_ref,
                    "defect_count": dc, "bead_height_mm": bead,
                    "terminal_mpa": r_uni["terminal_mpa"],
                    "pressure_margin_mpa": r_uni["terminal_mpa"] - PASS_THRESHOLD_MPA,
                    "pass_fail": "PASS" if r_uni["terminal_mpa"] >= PASS_THRESHOLD_MPA else "FAIL",
                })

                # bi 불균형 처리
                num_br = 2
                equip_loss = calc_equipment_loss_mpa(DESIGN_FLOW_LPM)
                tee_loss = calc_tee_split_loss_mpa(DESIGN_FLOW_LPM, k_tee)
                p_side = p_ref - equip_loss - tee_loss
                half_flow = DESIGN_FLOW_LPM / 2.0

                # 결함 배치: BI-3는 branch 1에만 결함 집중
                if case["defect_side"] == "right_only":
                    positions = [(1, h) for h in range(min(dc, HEADS_PER_BRANCH))]
                else:
                    positions = worst_branch_positions(num_br, dc)
                beads_2d = build_beads_2d(num_br, positions, bead)

                # 불균형: inlet_lengths_per_branch 전달
                gen_kw = _gen_params_k("bi")
                if case["inlet_extra_m"] > 0:
                    # inlet_lengths_per_branch 활용 (pipe_network.py Phase 3 수정 후)
                    # 현재는 대안: 입구관 길이를 조절할 수 없으므로
                    # 별도 방법으로 비대칭 처리
                    # → 두 branch를 개별 시스템으로 계산
                    pass

                sys = pn.generate_dynamic_system(
                    num_branches=num_br,
                    inlet_pressure_mpa=p_side,
                    total_flow_lpm=half_flow,
                    bead_heights_2d=beads_2d,
                    **gen_kw,
                )

                # 불균형: 입구관 길이 추가분만큼 추가 손실 계산
                extra_loss = 0.0
                if case["inlet_extra_m"] > 0:
                    inlet_id_m = constants.get_inner_diameter_m("65A")
                    V_inlet = hydraulics.velocity_from_flow(half_flow, inlet_id_m)
                    Re_inlet = hydraulics.reynolds_number(V_inlet, inlet_id_m)
                    f_inlet = hydraulics.friction_factor(Re_inlet, constants.EPSILON_M, inlet_id_m)
                    extra_loss = hydraulics.head_to_mpa(
                        hydraulics.major_loss(f_inlet, case["inlet_extra_m"],
                                              inlet_id_m, V_inlet)
                    )

                res = pn.calculate_dynamic_system(
                    sys, k3_bi,
                    equipment_k_factors=None,
                    **_common_calc_params(),
                )
                bi_terminal = res["worst_terminal_mpa"] - extra_loss

                rows.append({
                    "campaign": "A1", "topology": "bi",
                    "case": case["name"],
                    "total_flow_lpm": DESIGN_FLOW_LPM,
                    "inlet_pressure_mpa": p_ref,
                    "defect_count": dc, "bead_height_mm": bead,
                    "terminal_mpa": bi_terminal,
                    "extra_inlet_loss_mpa": extra_loss,
                    "pressure_margin_mpa": bi_terminal - PASS_THRESHOLD_MPA,
                    "pass_fail": "PASS" if bi_terminal >= PASS_THRESHOLD_MPA else "FAIL",
                })

        df = pd.DataFrame(rows)
        save_csv(df, out_dir / "A1_deterministic.csv")
        print(f"    A1 완료: {len(rows)}건 ({elapsed(t0)})")

        # ── B1: 임계압력 ──
        t0 = time.time()
        rows_b1 = []
        for bead in BEAD_DET:
            for dc in DEFECT_COUNTS:
                # uni
                p_crit_uni = bisect_critical_k("uni", dc, bead, DESIGN_FLOW_LPM,
                                               k_elbow, k3_bi, k_tee)
                rows_b1.append({
                    "campaign": "B1", "topology": "uni",
                    "case": case["name"],
                    "defect_count": dc, "bead_height_mm": bead,
                    "critical_inlet_mpa": round(p_crit_uni, 6),
                })

                # bi 임계압력: 불균형 추가 손실 포함
                # 이분탐색을 직접 구현 (extra_loss 포함)
                p_lo, p_hi = 0.35, 0.75
                for _ in range(40):
                    p_mid = (p_lo + p_hi) / 2.0
                    # bi 계산 (불균형 포함)
                    equip_loss = calc_equipment_loss_mpa(DESIGN_FLOW_LPM)
                    tee_loss = calc_tee_split_loss_mpa(DESIGN_FLOW_LPM, k_tee)
                    p_s = p_mid - equip_loss - tee_loss
                    hf = DESIGN_FLOW_LPM / 2.0

                    if case["defect_side"] == "right_only":
                        pos = [(1, h) for h in range(min(dc, HEADS_PER_BRANCH))]
                    else:
                        pos = worst_branch_positions(2, dc)
                    bd = build_beads_2d(2, pos, bead)

                    s = pn.generate_dynamic_system(
                        num_branches=2, inlet_pressure_mpa=p_s,
                        total_flow_lpm=hf, bead_heights_2d=bd,
                        **_gen_params_k("bi"),
                    )
                    r = pn.calculate_dynamic_system(
                        s, k3_bi, equipment_k_factors=None,
                        **_common_calc_params(),
                    )
                    terminal = r["worst_terminal_mpa"]

                    # 추가 손실
                    if case["inlet_extra_m"] > 0:
                        inlet_id_m = constants.get_inner_diameter_m("65A")
                        V_in = hydraulics.velocity_from_flow(hf, inlet_id_m)
                        Re_in = hydraulics.reynolds_number(V_in, inlet_id_m)
                        f_in = hydraulics.friction_factor(Re_in, constants.EPSILON_M, inlet_id_m)
                        el = hydraulics.head_to_mpa(
                            hydraulics.major_loss(f_in, case["inlet_extra_m"],
                                                  inlet_id_m, V_in)
                        )
                        terminal -= el

                    if terminal >= PASS_THRESHOLD_MPA:
                        p_hi = p_mid
                    else:
                        p_lo = p_mid
                    if (p_hi - p_lo) < 0.0005:
                        break

                rows_b1.append({
                    "campaign": "B1", "topology": "bi",
                    "case": case["name"],
                    "defect_count": dc, "bead_height_mm": bead,
                    "critical_inlet_mpa": round(p_hi, 6),
                })

        df_b1 = pd.DataFrame(rows_b1)
        save_csv(df_b1, out_dir / "B1_critical_pressure.csv")
        print(f"    B1 완료: {len(rows_b1)}건 ({elapsed(t0)})")

        # 요약 통계
        uni_crit = df_b1[(df_b1["topology"] == "uni") &
                         (df_b1["defect_count"] == 4) &
                         (df_b1["bead_height_mm"] == 2.0)]["critical_inlet_mpa"]
        bi_crit = df_b1[(df_b1["topology"] == "bi") &
                        (df_b1["defect_count"] == 4) &
                        (df_b1["bead_height_mm"] == 2.0)]["critical_inlet_mpa"]

        if len(uni_crit) > 0 and len(bi_crit) > 0:
            summary_rows.append({
                "scenario": scenario_tag,
                "case": case["name"],
                "P_REF": p_ref,
                "uni_crit_dc4_b2": uni_crit.values[0],
                "bi_crit_dc4_b2": bi_crit.values[0],
                "B1_gap_mpa": bi_crit.values[0] - uni_crit.values[0],
                "bi_needs_more": bi_crit.values[0] > uni_crit.values[0],
            })

    df_summary = pd.DataFrame(summary_rows)
    if len(df_summary) > 0:
        save_csv(df_summary, base_dir / "summary_imbalance.csv")
        print(f"\n  Phase 3 결과 요약:")
        for _, row in df_summary.iterrows():
            status = "OK" if row["bi_needs_more"] else "REVERSED"
            print(f"    {row['case']}: B1_gap={row['B1_gap_mpa']:.6f} MPa [{status}]")

    print(f"\n  Phase 3 전체 완료: {elapsed(t_total)}")
    return df_summary


# ══════════════════════════════════════════════════════════
#  Smoke Test
# ══════════════════════════════════════════════════════════

def run_smoke_test():
    """기준 K값으로 기존 P_REF 재현 확인"""
    print("\n" + "=" * 70)
    print("  Smoke Test: 기준 K값 P_REF 재현 확인")
    print("=" * 70)

    k_elbow = K_ELBOW_DEFAULT
    k3_bi = K3_DEFAULT
    k_tee = K_TEE_BRANCH_DEFAULT

    p_ref_calc = bisect_pref_k(k_elbow, k3_bi, k_tee)
    print(f"  P_REF (계산): {p_ref_calc} MPa")

    results = []
    passed = True

    # Test 1: uni, bead=0, dc=0 → terminal ≈ 0.1 MPa
    r1 = run_det_k("uni", 0, 0.0, p_ref_calc, DESIGN_FLOW_LPM,
                    k_elbow, k3_bi, k_tee)
    ok1 = abs(r1["terminal_mpa"] - 0.1) < 0.005
    results.append(("uni/bead=0/dc=0", r1["terminal_mpa"], "~0.1000", ok1))

    # Test 2: bi terminal < uni terminal
    r2 = run_det_k("bi", 0, 0.0, p_ref_calc, DESIGN_FLOW_LPM,
                    k_elbow, k3_bi, k_tee)
    ok2 = r2["terminal_mpa"] < r1["terminal_mpa"]
    results.append(("bi/bead=0/dc=0", r2["terminal_mpa"],
                    f"< {r1['terminal_mpa']:.4f}", ok2))

    # Test 3: K값 확인
    results.append(("K_ELBOW", k_elbow, "= 0.53", abs(k_elbow - 0.53) < 0.01))
    results.append(("K3_bi", k3_bi, "= 1.0", abs(k3_bi - 1.0) < 0.01))
    results.append(("K_TEE", k_tee, "= 1.06", abs(k_tee - 1.06) < 0.01))

    # Test 4: tee loss > 0
    ok4 = r2["tee_split_loss_mpa"] > 0
    results.append(("bi tee_loss > 0", r2["tee_split_loss_mpa"], "> 0", ok4))

    # Test 5: 설정값
    results.append(("HEADS_PER_BRANCH", HEADS_PER_BRANCH, f"= {HEADS_PER_BRANCH}", True))
    results.append(("HEAD_SPACING_M", HEAD_SPACING_M, f"= {HEAD_SPACING_M}", HEAD_SPACING_M > 0))
    results.append(("DESIGN_FLOW_LPM", DESIGN_FLOW_LPM, f"= {DESIGN_FLOW_LPM}", True))

    print()
    for name, val, expect, ok in results:
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name}: {val} (기대: {expect})")
        if not ok:
            passed = False

    if not passed:
        print("\n  *** SMOKE TEST 실패 — 실행 중단 ***")
        sys.exit(1)
    else:
        print(f"\n  Smoke Test 전체 PASS (P_REF={p_ref_calc})")

    return p_ref_calc


# ══════════════════════════════════════════════════════════
#  Manifest 저장
# ══════════════════════════════════════════════════════════

def save_manifest(phase, scenario_tag):
    manifest = {
        "script": "run_supplementary_sims.py",
        "run_date": datetime.now().isoformat(),
        "phase": phase,
        "scenario": scenario_tag,
        "description": "보강 시뮬레이션 — 리뷰어 방어용 (K민감도/배치패턴/불균형)",
        "constants": {
            "K_ELBOW_DEFAULT": K_ELBOW_DEFAULT,
            "K3_DEFAULT": K3_DEFAULT,
            "K_TEE_BRANCH_DEFAULT": K_TEE_BRANCH_DEFAULT,
            "K2": K2,
            "K1_BASE": K1_BASE,
            "USE_HEAD_FITTING": USE_HEAD_FITTING,
            "REDUCER_MODE": REDUCER_MODE,
            "HEADS_PER_BRANCH": HEADS_PER_BRANCH,
            "HEAD_SPACING_M": HEAD_SPACING_M,
            "BRANCH_SPACING_M": BRANCH_SPACING_M,
            "BRANCH_INLET_CONFIG": BRANCH_INLET_CONFIG,
            "SUPPLY_PIPE_SIZE": SUPPLY_PIPE_SIZE,
            "PASS_THRESHOLD_MPA": PASS_THRESHOLD_MPA,
            "P_REF": P_REF,
            "DESIGN_FLOW_LPM": DESIGN_FLOW_LPM,
            "MC_ITER": MC_ITER,
            "EPSILON_MM": constants.EPSILON_MM,
            "RHO": constants.RHO,
            "NU": constants.NU,
        },
    }
    try:
        import subprocess
        rev = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(Path(__file__).parent),
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        manifest["git_revision"] = rev
    except Exception:
        manifest["git_revision"] = "unknown"

    path = OUT_ROOT / "run_manifest.json"
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"  Manifest 저장: {path}")


# ══════════════════════════════════════════════════════════
#  Main
# ══════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="보강 시뮬레이션 — 리뷰어 방어용 (K민감도/배치패턴/불균형)"
    )
    parser.add_argument("--phase", type=str, required=True,
                        help="실행 Phase: 1, 2, 3, ALL")
    parser.add_argument("--smoke", action="store_true",
                        help="Smoke test만 실행")
    parser.add_argument("--heads", type=int, default=None,
                        help="가지배관당 헤드 수 (기본: 8)")
    parser.add_argument("--spacing", type=float, default=None,
                        help="헤드 간격 m (기본: 2.1)")
    parser.add_argument("--mc", type=int, default=MC_ITER,
                        help=f"MC 반복 횟수 (기본: {MC_ITER})")
    args = parser.parse_args()

    global HEADS_PER_BRANCH, HEAD_SPACING_M, ACTIVE_HEADS, DESIGN_FLOW_LPM
    global P_REF

    if args.heads is not None:
        HEADS_PER_BRANCH = args.heads
    if args.spacing is not None:
        HEAD_SPACING_M = args.spacing

    ACTIVE_HEADS = NUM_BRANCHES * HEADS_PER_BRANCH
    DESIGN_FLOW_LPM = ACTIVE_HEADS * HEAD_FLOW_LPM

    # 시나리오 태그
    scenario_tag = f"{HEADS_PER_BRANCH}h_{HEAD_SPACING_M}m"

    # P_REF 역산
    P_REF = bisect_pref_k(K_ELBOW_DEFAULT, K3_DEFAULT, K_TEE_BRANCH_DEFAULT)

    print("\n" + "=" * 70)
    print("  FiPLSim 보강 시뮬레이션 — 리뷰어 방어용")
    print("=" * 70)
    print(f"  HEADS = {HEADS_PER_BRANCH}, SPACING = {HEAD_SPACING_M} m")
    print(f"  DESIGN_FLOW = {DESIGN_FLOW_LPM} LPM")
    print(f"  P_REF = {P_REF} MPa")
    print(f"  K_ELBOW = {K_ELBOW_DEFAULT}, K3_bi = {K3_DEFAULT}, K_TEE = {K_TEE_BRANCH_DEFAULT}")
    print(f"  MC = {MC_ITER}")

    if args.smoke:
        run_smoke_test()
        return

    # Smoke test
    run_smoke_test()
    save_manifest(args.phase, scenario_tag)

    phase = args.phase.upper()
    t_total = time.time()

    if phase in ("1", "ALL"):
        run_phase1(scenario_tag)
    if phase in ("2", "ALL"):
        run_phase2(scenario_tag, mc_iter=args.mc)
    if phase in ("3", "ALL"):
        run_phase3(scenario_tag)

    print(f"\n{'='*70}")
    print(f"  전체 완료: {elapsed(t_total)}")
    print(f"  결과: {OUT_ROOT}/")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
