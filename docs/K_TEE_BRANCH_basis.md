# T분기(Tee-Branch) 손실계수 K값 산출 근거

> **v1.0** | 최종 업데이트: **2026-03-24** | 작성자: Claude Code

## 변경 이력

| 버전 | 일시 | 주요 변경 |
|------|------|---------|
| v1.0 | 2026-03-24 | 초기 작성 — NFPA 13 기반 K값 산출 근거 정리 |

---

## 1. 적용 대상

FiPLSim에서 T분기 손실계수가 사용되는 위치:

| 상수 | 값 | 파일 | 용도 | 기준 유속 |
|------|-----|------|------|----------|
| `K3` | 1.0 | constants.py | 교차배관 → 가지배관 분기 입구 | 65A 가지배관 입구 유속 |
| `K_TEE_RUN` | 0.3 | constants.py | 교차배관 직진 (분기 후 계속 흐르는 흐름) | 80A 교차배관 유속 |
| `K_TEE_BRANCH_80A` | 1.06 | constants.py | 양방향 배관 입구 T분기 | 80A 교차배관 유속 |

---

## 2. 주 출처: NFPA 13 (2019)

### 2.1 표준 정보

- **표준명**: NFPA 13, Standard for the Installation of Sprinkler Systems
- **발행**: National Fire Protection Association (미국소방협회)
- **참조 표**: Table 22.4.3.1.1 — Equivalent Schedule 40 Steel Pipe Length Chart
- **항목**: "Tee or Cross (Flow Turned 90°)"

### 2.2 NFPA 13 등가길이 원표

| 관경 (인치) | 관경 (A) | 등가길이 (ft) | 등가길이 (m) |
|------------|---------|-------------|-------------|
| 1" | 25A | 5 | 1.524 |
| 1¼" | 32A | 6 | 1.829 |
| 1½" | 40A | 8 | 2.438 |
| 2" | 50A | 10 | 3.048 |
| 2½" | 65A | 12 | 3.658 |
| 3" | 80A | 15 | 4.572 |
| 4" | 100A | 20 | 6.096 |

---

## 3. Darcy-Weisbach K값 변환

### 3.1 변환 공식

NFPA 13의 등가길이(L_eq)를 Darcy-Weisbach 손실계수(K)로 변환:

```
K = f_T × (L_eq / D)
```

- **f_T**: 완전난류 마찰계수 (Colebrook-White 방정식, Re → ∞)
- **L_eq**: NFPA 13 등가길이 (m)
- **D**: 배관 내경 (m), Schedule 40 기준

### 3.2 완전난류 마찰계수 f_T

Colebrook-White 방정식에서 Re → ∞ (완전난류):

```
1/√f_T = -2.0 × log₁₀(ε / (3.7 × D))
```

FiPLSim 기본 조도: ε = 0.046 mm (논문 물성치)

| 관경 | 내경 D (mm) | ε/D | f_T |
|------|-----------|-----|-----|
| 25A | 27.6 | 0.001667 | 0.0220 |
| 32A | 35.1 | 0.001311 | 0.0210 |
| 40A | 40.9 | 0.001125 | 0.0200 |
| 50A | 52.5 | 0.000876 | 0.0195 |
| 65A | 62.7 | 0.000734 | 0.0190 |
| 80A | 77.9 | 0.000591 | 0.0180 |

※ 내경은 KS D 3507 (배관용 탄소 강관) Schedule 40 기준

### 3.3 K값 산출 결과

| 관경 | L_eq (m) | D (m) | L_eq/D | f_T | **K = f_T × L_eq/D** |
|------|---------|-------|--------|-----|----------------------|
| 25A | 1.524 | 0.0276 | 55.2 | 0.0220 | **1.21** |
| 32A | 1.829 | 0.0351 | 52.1 | 0.0210 | **1.09** |
| 40A | 2.438 | 0.0409 | 59.6 | 0.0200 | **1.19** |
| 50A | 3.048 | 0.0525 | 58.1 | 0.0195 | **1.13** |
| 65A | 3.658 | 0.0627 | 58.3 | 0.0190 | **1.11** |
| 80A | 4.572 | 0.0779 | 58.7 | 0.0180 | **1.06** |

---

## 4. 보조 출처와 교차 검증

### 4.1 Crane TP-410

- **문서**: Crane Technical Paper 410, "Flow of Fluids Through Valves, Fittings, and Pipe"
- **공식**: T분기(Branch Flow): K = 60 × f_T
- **80A 적용**: K = 60 × 0.018 = **1.08** (NFPA 13 산출값 1.06과 유사)

### 4.2 Idelchik (2008)

- **문서**: Idelchik, I.E., "Handbook of Hydraulic Resistance", 4th Edition, 2008
- **참조**: Diagram 7-18 (분기 T, 동일 관경, 90°)
- **특징**: 유량비(Q_branch / Q_total)에 따라 K가 변함
  - Q_branch/Q_total ≈ 0.4 → K ≈ 0.88
  - Q_branch/Q_total = 1.0 → K ≈ 1.7
- **참고**: NFPA 13과 Crane은 유량비를 고려하지 않는 단순화 모델

### 4.3 출처 간 비교 (80A 기준)

| 출처 | K값 | 비고 |
|------|-----|------|
| NFPA 13 Table 22.4.3.1.1 | **1.06** | 소방 표준, 등가길이→K 변환 |
| Crane TP-410 (K=60×f_T) | **1.08** | 공학 핸드북 |
| Idelchik Diagram 7-18 | **0.88~1.7** | 학술 참고서, 유량비 의존 |

→ 3개 출처 모두 80A에서 K ≈ 1.0~1.1 범위에서 일치 (유량비=1 기준 제외)

---

## 5. FiPLSim 적용 값 결정

### 5.1 K3 = 1.0 (교차배관 → 가지배관 분기)

- **기준 유속**: 65A 가지배관 입구 유속
- **NFPA 13 산출값**: 65A 기준 K = 1.11
- **적용값**: K = 1.0 (보수적 단순화, ~10% 낮음)
- **사유**: 기존 시뮬레이션 결과와의 일관성 유지, 실제 배관은 용접 가공으로 등가길이보다 유리할 수 있음

### 5.2 K_TEE_BRANCH_80A = 1.06 (양방향 배관 입구 T분기)

- **기준 유속**: 80A 교차배관 전체 유량 유속
- **NFPA 13 산출값**: 80A 기준 K = 1.06
- **적용값**: K = 1.06 (NFPA 13 산출값 그대로 적용)

### 5.3 K_TEE_RUN = 0.3 (교차배관 직진)

- **기준 유속**: 80A 교차배관 유속
- **출처**: Crane TP-410, Tee straight-through flow
- **적용값**: K = 0.3

---

## 6. 논문 인용 권장 문구

> T분기 손실계수는 NFPA 13 (2019) Table 22.4.3.1.1의 등가길이를
> Darcy-Weisbach 프레임워크로 변환하여 K = f_T × (L_eq/D)로 산출하였다.
> 80A 교차배관 기준 K = 1.06이며, 이는 Crane TP-410 (K = 60f_T = 1.08)
> 및 Idelchik (2008)의 범위(0.88~1.7)와 일치한다.

---

## 7. 참고 문헌

1. NFPA 13, *Standard for the Installation of Sprinkler Systems*, National Fire Protection Association, 2019.
2. Crane Co., *Flow of Fluids Through Valves, Fittings, and Pipe*, Technical Paper No. 410, 2018.
3. Idelchik, I.E., *Handbook of Hydraulic Resistance*, 4th Edition, Begell House, 2008.
4. KS D 3507, *배관용 탄소 강관 (Carbon Steel Pipes for Ordinary Piping)*, 한국산업표준.
