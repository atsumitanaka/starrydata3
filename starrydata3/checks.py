# -*- coding: utf-8 -*-
"""
checks.py — cross-property consistency checks on digitized curves.

Thermoelectric properties measured on the same sample are algebraically
linked: PF = S²σ and ZT = S²σT/κ. When a paper's plots for one sample were
all digitized, the relations can be recomputed and compared against the
digitized target curve. Deviations far beyond digitization error catch the
most damaging real-world failures — axis-unit and exponent mistakes (in the
digitization OR the paper itself) — which published curation projects report
as their worst outlier class.

All values are converted to SI here (independent of KMDS canonical units,
which are sometimes non-SI, e.g. Seebeck in μV/K). The unit parser handles
per-token SI prefixes, exponents, powers of ten, and the Ω⁻¹ ↔ S equivalence.
"""
import json
import re
from statistics import median
from typing import Any, Dict, List, Optional, Tuple

from . import units

# ---------------------------------------------------------------------------
# SI unit parsing (factor + dimension signature)
# ---------------------------------------------------------------------------

_TOKEN = re.compile(r"^(.+?)\^?(-?\d+)?$")
_OHM_ALIASES = {"ohm", "Ohm", "OHM", "Ω", "Ω"}


def si_parse(unit: str) -> Optional[Tuple[float, Dict[str, int]]]:
    """Parse a unit string into (factor_to_SI, dims). dims maps base symbols
    to exponents, with Ω folded into S (Ω¹ ≡ S⁻¹) and cm/mm/... folded into m.
    Returns None when a token can't be interpreted."""
    u = units._clean(unit)
    if u.lower() in units._DIMLESS:
        return 1.0, {}
    mult, rest = units._leading_pow10(u)
    raw_tokens = [t for t in rest.split(" ") if t]
    # merge a detached prefix word with the following token: 'mu V' -> 'μV'
    tokens: List[str] = []
    i = 0
    while i < len(raw_tokens):
        t = raw_tokens[i]
        if (i + 1 < len(raw_tokens)
                and (t.lower() in units._PREFIX_WORD or t in units._PREFIX)
                and t not in units._BASE):
            pf = units._PREFIX_WORD.get(t.lower()) or units._PREFIX.get(t)
            nxt = raw_tokens[i + 1]
            m = _TOKEN.match(nxt)
            exp = int(m.group(2) or 1) if m else 1
            mult *= pf ** exp
            tokens.append(nxt)
            i += 2
            continue
        tokens.append(t)
        i += 1

    factor = mult
    dims: Dict[str, int] = {}
    for tok in tokens:
        m = _TOKEN.match(tok)
        if not m:
            return None
        sym, exp = m.group(1), int(m.group(2) or 1)
        if sym in _OHM_ALIASES:                    # Ω^e ≡ S^-e
            dims["S"] = dims.get("S", 0) - exp
            continue
        pf, base = units._split_prefix(sym)
        if base not in units._BASE:
            return None
        factor *= pf ** exp
        dims[base] = dims.get(base, 0) + exp
    return factor, {k: v for k, v in dims.items() if v != 0}


def si_factor(unit: str, want_dims: Dict[str, int]) -> Optional[float]:
    """Factor converting values in `unit` to SI, or None if the unit doesn't
    have the wanted dimensions."""
    parsed = si_parse(unit)
    if parsed is None or parsed[1] != want_dims:
        return None
    return parsed[0]


# ---------------------------------------------------------------------------
# Quantities and relations
# ---------------------------------------------------------------------------

# quantity -> (accepted property names, SI dimension signature)
QUANTITIES: Dict[str, Tuple[Tuple[str, ...], Dict[str, int]]] = {
    "S":     (("Seebeck coefficient",),                    {"V": 1, "K": -1}),
    "sigma": (("electrical conductivity",),                {"S": 1, "m": -1}),
    "rho":   (("resistivity", "electrical resistivity"),   {"S": -1, "m": 1}),
    "kappa": (("thermal conductivity",),                   {"W": 1, "m": -1, "K": -1}),
    "PF":    (("power factor",),                           {"W": 1, "m": -1, "K": -2}),
    "ZT":    (("ZT", "dimensionless figure of merit",
               "thermoelectric figure of merit"),          {}),
}
_PROP_TO_QTY = {p.lower(): q for q, (props, _) in QUANTITIES.items() for p in props}

# check name -> (target qty, input qtys, expected(T, inputs_at_T))
RELATIONS = {
    "power factor = S²σ": (
        "PF", ("S", "sigma"),
        lambda T, v: v["S"] ** 2 * v["sigma"]),
    "ZT = S²σT/κ": (
        "ZT", ("S", "sigma", "kappa"),
        lambda T, v: v["S"] ** 2 * v["sigma"] * T / v["kappa"]),
}

OK_DEV, WARN_DEV = 0.25, 0.75      # median relative deviation thresholds
MIN_POINTS = 3                     # fewer overlapping points -> no verdict


def _temp_to_k(x: float, unit: str) -> Optional[float]:
    u = units._clean(unit)
    if u in ("K", "Kelvin", "kelvin"):
        return x
    if u in ("°C", "C", "deg C", "degC", "℃"):
        return x + 273.15
    return None


def _series_si(points: List[List[float]], unit_x: str, unit_y: str,
               want_dims: Dict[str, int]) -> Optional[List[Tuple[float, float]]]:
    """Curve points -> [(T_kelvin, value_SI)] sorted by T, or None."""
    fy = si_factor(unit_y, want_dims)
    if fy is None or not points:
        return None
    out = []
    for p in points:
        if len(p) < 2:
            continue
        t = _temp_to_k(float(p[0]), unit_x)
        if t is None:
            return None
        out.append((t, float(p[1]) * fy))
    out.sort(key=lambda q: q[0])
    return out or None


def _interp(series: List[Tuple[float, float]], t: float) -> Optional[float]:
    """Linear interpolation; None outside the series' T range."""
    if not series or t < series[0][0] or t > series[-1][0]:
        return None
    for i in range(1, len(series)):
        t0, v0 = series[i - 1]
        t1, v1 = series[i]
        if t0 <= t <= t1:
            if t1 == t0:
                return v0
            return v0 + (v1 - v0) * (t - t0) / (t1 - t0)
    return series[-1][1]


def _norm_sample(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def check_curves(curves: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Run every applicable relation on a record's temperature curves.

    `curves` are dicts with sample_name, prop_x, prop_y, unit_x, unit_y,
    points (list of [x, y] in the stored units). Returns one flag dict per
    (sample, relation) that had enough overlapping data:
      {sample, check, status: ok|warn|fail, median_dev, n_points, detail}
    """
    # sample -> quantity -> SI series (longest curve wins per quantity)
    by_sample: Dict[str, Dict[str, List[Tuple[float, float]]]] = {}
    labels: Dict[str, str] = {}
    for c in curves:
        qty = _PROP_TO_QTY.get((c.get("prop_y") or "").lower())
        if qty is None:
            continue
        series = _series_si(c.get("points") or [], c.get("unit_x") or "",
                            c.get("unit_y") or "", QUANTITIES[qty][1])
        if series is None:
            continue
        key = _norm_sample(c.get("sample_name") or "")
        labels.setdefault(key, c.get("sample_name") or "")
        slot = by_sample.setdefault(key, {})
        if qty not in slot or len(series) > len(slot[qty]):
            slot[qty] = series

    flags: List[Dict[str, Any]] = []
    for key, qtys in by_sample.items():
        # σ = 1/ρ fallback when conductivity wasn't plotted but resistivity was
        if "sigma" not in qtys and "rho" in qtys:
            qtys["sigma"] = [(t, 1.0 / v) for t, v in qtys["rho"] if v != 0]
        for check_name, (target, inputs, fn) in RELATIONS.items():
            if target not in qtys or any(q not in qtys for q in inputs):
                continue
            devs, ratios = [], []
            for t, meas in qtys[target]:
                vals = {}
                for q in inputs:
                    v = _interp(qtys[q], t)
                    if v is None:
                        break
                    vals[q] = v
                else:
                    exp = fn(t, vals)
                    if exp == 0:
                        continue
                    devs.append(abs(meas - exp) / abs(exp))
                    ratios.append(meas / exp)
            if len(devs) < MIN_POINTS:
                continue
            mdev = median(devs)
            status = ("ok" if mdev <= OK_DEV
                      else "warn" if mdev <= WARN_DEV else "fail")
            mratio = median(ratios)
            detail = (f"median measured/expected = {mratio:.3g} over "
                      f"{len(devs)} points")
            # a clean power-of-ten ratio is the signature of a unit/exponent
            # slip — say so explicitly
            for p in (1e-6, 1e-4, 1e-3, 1e-2, 1e2, 1e3, 1e4, 1e6):
                if p != 1 and abs(mratio / p - 1) < 0.2:
                    detail += f" — consistent with a ×{p:g} unit/exponent error"
                    break
            else:
                # a ratio that DRIFTS across the T range is a different
                # disease: one curve's axis calibration is wrong (log axis
                # read as linear, or bad tick values)
                k = max(1, len(ratios) // 4)
                r_lo, r_hi = median(ratios[:k]), median(ratios[-k:])
                if min(r_lo, r_hi) > 0 and max(r_lo / r_hi, r_hi / r_lo) > 5:
                    status = "fail"
                    detail += (f"; ratio drifts {r_lo:.2g} → {r_hi:.2g} "
                               f"across the T range — one curve's axis "
                               f"calibration is likely wrong (log/linear "
                               f"or tick values)")
            flags.append({"sample": labels[key], "check": check_name,
                          "status": status, "median_dev": round(mdev, 4),
                          "n_points": len(devs), "detail": detail})
    return flags


def curves_from_rows(rows) -> List[Dict[str, Any]]:
    """Adapt GraphCurve DB rows to check_curves() input."""
    return [{"sample_name": r.sample_name, "prop_x": r.prop_x,
             "prop_y": r.prop_y, "unit_x": r.unit_x, "unit_y": r.unit_y,
             "points": json.loads(r.points_json or "[]")} for r in rows]
