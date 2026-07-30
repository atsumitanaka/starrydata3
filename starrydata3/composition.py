# -*- coding: utf-8 -*-
"""
composition.py — normalize chemical compositions into a canonical key.

Fifty papers write the same material fifty ways: 'Bi2Te3', '(Bi0.5Sb0.5)2Te3',
'Ta0.84Ti0.16FeSb'. Parsing each composition into element fractions and
serializing them in a fixed order gives every material family ONE key, so all
curves for a phase aggregate on one dossier page regardless of source paper
(the MPDS "distinct phase" idea at local scale).

Deliberately conservative: only strings that parse ENTIRELY as a chemical
formula get a key — sample codes ('LCE+DDM', 'Process (A)') get none rather
than a wrong one.
"""
import re
from typing import Dict, List, Optional, Tuple

_ELEMENTS = {
    "H", "He", "Li", "Be", "B", "C", "N", "O", "F", "Ne", "Na", "Mg", "Al",
    "Si", "P", "S", "Cl", "Ar", "K", "Ca", "Sc", "Ti", "V", "Cr", "Mn", "Fe",
    "Co", "Ni", "Cu", "Zn", "Ga", "Ge", "As", "Se", "Br", "Kr", "Rb", "Sr",
    "Y", "Zr", "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd", "In", "Sn",
    "Sb", "Te", "I", "Xe", "Cs", "Ba", "La", "Ce", "Pr", "Nd", "Pm", "Sm",
    "Eu", "Gd", "Tb", "Dy", "Ho", "Er", "Tm", "Yb", "Lu", "Hf", "Ta", "W",
    "Re", "Os", "Ir", "Pt", "Au", "Hg", "Tl", "Pb", "Bi", "Po", "At", "Rn",
    "Fr", "Ra", "Ac", "Th", "Pa", "U", "Np", "Pu",
}

_TOKEN = re.compile(r"([A-Z][a-z]?)(\d*\.?\d*)|(\()|(\))(\d*\.?\d*)")


def parse_formula(s: str) -> Optional[Dict[str, float]]:
    """'(Bi0.5Sb0.5)2Te3' -> {'Bi': 1.0, 'Sb': 1.0, 'Te': 3.0}; None when the
    string is not entirely a chemical formula."""
    s = (s or "").strip()
    # tolerate subscript digits and common separators inside one formula
    s = s.translate(str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")).replace(" ", "")
    if not s or len(s) > 80:
        return None
    stack: List[Dict[str, float]] = [{}]
    pos = 0
    n_elements = 0
    while pos < len(s):
        m = _TOKEN.match(s, pos)
        if not m or m.start() != pos:
            return None
        el, cnt, open_p, close_p, group_cnt = m.groups()
        if el:
            if el not in _ELEMENTS:
                return None
            n_elements += 1
            c = float(cnt) if cnt else 1.0
            stack[-1][el] = stack[-1].get(el, 0.0) + c
        elif open_p:
            stack.append({})
        elif close_p:
            if len(stack) < 2:
                return None
            group = stack.pop()
            mult = float(group_cnt) if group_cnt else 1.0
            for k, v in group.items():
                stack[-1][k] = stack[-1].get(k, 0.0) + v * mult
        pos = m.end()
    if len(stack) != 1 or n_elements == 0:
        return None
    comp = {k: v for k, v in stack[0].items() if v > 0}
    # a lone element token like 'S' or 'In' is usually a WORD, not a formula,
    # unless it carries an explicit count ('Si3')
    if len(comp) == 1 and n_elements == 1 and not re.search(r"\d", s):
        return None
    return comp or None


def comp_key(text: str) -> Tuple[str, str]:
    """Canonical (key, elements) for a composition-ish string, trying the
    whole string first and then separator-split fragments. Fractions are
    normalized to sum 1 and rounded so 'Bi2Te3' == 'Bi4Te6' == 'BiTe1.5'.
    Returns ('', '') when nothing parses."""
    for cand in _candidates(text):
        comp = parse_formula(cand)
        if comp:
            total = sum(comp.values())
            parts = [f"{el}{round(v / total, 3):g}"
                     for el, v in sorted(comp.items())]
            return "".join(parts), " ".join(sorted(comp))
    return "", ""


def _candidates(text: str):
    t = (text or "").strip()
    if not t:
        return
    yield t
    for frag in re.split(r"[;,/+]| - ", t):
        frag = frag.strip()
        if frag and frag != t:
            yield frag
    # last resort: the longest formula-looking token in the string
    toks = sorted(re.findall(r"[A-Za-z0-9().]{3,}", t), key=len, reverse=True)
    for tok in toks[:3]:
        yield tok
