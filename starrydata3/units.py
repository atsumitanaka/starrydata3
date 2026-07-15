# -*- coding: utf-8 -*-
"""
units.py — unit normalization for KMDS data unification.

Materials figures report the same property in different units — Seebeck in
μV/K vs V/K, conductivity as '10^4 S m^-1' vs 'S m^-1'. To overlay curves from
different papers on one axis, values must be converted to the property's
canonical KMDS unit. This does that conservatively: it only converts when the
two units share the same BASE (same physical quantity); otherwise it reports
'incompatible' and the caller keeps the raw values + a flag.

Handles the real variation in the data:
  - a leading power-of-ten multiplier: '10^4 S m^-1', 'x10^{3}'
  - an SI prefix on the leading unit token: μ, m, k, M, G, ...
Anything past that (denominators, spelled-out units) must match textually.
"""
import re
from typing import Optional, Tuple

_PREFIX = {
    "Y": 1e24, "Z": 1e21, "E": 1e18, "P": 1e15, "T": 1e12, "G": 1e9, "M": 1e6,
    "k": 1e3, "h": 1e2, "da": 1e1, "d": 1e-1, "c": 1e-2, "m": 1e-3,
    "u": 1e-6, "µ": 1e-6, "μ": 1e-6, "n": 1e-9, "p": 1e-12, "f": 1e-15,
}
# base unit symbols we recognize a prefix in front of (case-sensitive)
_BASE = {"V", "A", "W", "S", "Hz", "K", "Pa", "J", "N", "m", "g", "s", "Ω",
         "ohm", "T", "F", "C", "eV", "bar", "L", "mol", "H", "Wb", "cd",
         "lm", "lx", "Gy", "Sv", "wt", "at"}
_DIMLESS = {"", "-", "none", "a.u.", "au", "arb", "arb.units", "1", "ratio",
            "dimensionless"}


def _clean(u: Optional[str]) -> str:
    u = (u or "").strip()
    # strip a trailing parenthetical gloss: 'W m^-1 K^-1 (watts per ...)' -> unit
    u = re.sub(r"\s*\(.*?\)\s*$", "", u)
    u = u.replace("{", "").replace("}", "").replace("\\", "")
    u = u.replace("·", " ").replace("⋅", " ")
    u = re.sub(r"\s+", " ", u).strip()
    return u


def _leading_pow10(u: str) -> Tuple[float, str]:
    """Pull a leading power-of-ten multiplier ('10^4 ...', 'x10^3 ...')."""
    m = re.match(r"^\s*[x×]?\s*10\s*\^?\s*(-?\d+)\s*", u)
    if m:
        return 10.0 ** int(m.group(1)), u[m.end():].strip()
    return 1.0, u


def _split_prefix(token: str) -> Tuple[float, str]:
    """A unit token like 'μV', 'GHz', 'mA', 'S' -> (prefix_factor, base_symbol).
    Only strips a prefix when the remainder is a known base unit, so 'm'
    (metre) is not mistaken for milli."""
    if token in _BASE:
        return 1.0, token
    if token[:2] == "da" and token[2:] in _BASE:
        return 10.0, token[2:]
    if len(token) > 1 and token[0] in _PREFIX and token[1:] in _BASE:
        return _PREFIX[token[0]], token[1:]
    return 1.0, token


# prefixes written as their own token: 'mu V', 'micro V', 'm V', 'k Hz'
_PREFIX_WORD = {"micro": 1e-6, "mu": 1e-6, "milli": 1e-3, "kilo": 1e3,
                "mega": 1e6, "giga": 1e9, "nano": 1e-9, "pico": 1e-12,
                "centi": 1e-2, "deci": 1e-1}


def factor_to_base(unit: str) -> Tuple[float, str]:
    """Return (factor, base_signature): multiply a value in `unit` by `factor`
    to express it in the base unit whose textual signature is base_signature.
    Dimensionless units map to signature ''."""
    u = _clean(unit)
    if u.lower() in _DIMLESS:
        return 1.0, ""
    mult, rest = _leading_pow10(u)
    parts = [p for p in rest.split(" ") if p]
    if not parts:
        return mult, ""
    pf = 1.0
    # a leading DETACHED prefix ('mu V', 'm V') binds to the next token
    if len(parts) >= 2:
        w = parts[0]
        if w.lower() in _PREFIX_WORD and parts[1] in _BASE:
            pf, parts = _PREFIX_WORD[w.lower()], parts[1:]
        elif w in _PREFIX and parts[1] in _BASE:
            pf, parts = _PREFIX[w], parts[1:]
    pf2, base0 = _split_prefix(parts[0])          # or an attached prefix ('μV')
    sig = re.sub(r"\s+", " ", " ".join([base0] + parts[1:])).strip()
    return mult * pf * pf2, sig


def convert_factor(source_unit: str, canonical_unit: str
                   ) -> Tuple[Optional[float], bool]:
    """Factor to multiply SOURCE values by to express them in CANONICAL unit.
    Returns (factor, compatible). compatible is False when the base units
    differ (don't convert — keep raw). Both dimensionless -> factor 1."""
    fs, sig_s = factor_to_base(source_unit)
    fc, sig_c = factor_to_base(canonical_unit)
    if sig_s != sig_c:
        return None, False
    if fc == 0:
        return None, False
    return fs / fc, True
