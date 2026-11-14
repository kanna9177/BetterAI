"""Catalogo degli esiti (anche combo) e loro verifica sul risultato finale.

Un esito e' una o piu' condizioni in AND, scritte "R:X2&BTTS:Y":
  R     esito finale o doppia chance (1, X, 2, 1X, X2, 12)
  BTTS  goal / nogoal (Y, N)
  TG    under/over sui gol totali (O2.5, U1.5, ...)
  HG/AG multigoal casa / ospite (1-3 = da 1 a 3 gol)
"""
import numpy as np

from .model import MAX_GOALS

RANGES = ["0-1", "1-2", "1-3", "2-3", "2-4"]


def atom_ok(atom, h, a):
    kind, val = atom.split(":")
    if kind == "R":
        sign = "1" if h > a else "2" if a > h else "X"
        return sign in val
    if kind == "BTTS":
        return (h > 0 and a > 0) == (val == "Y")
    if kind == "TG":
        return (h + a > float(val[1:])) == (val[0] == "O")
    lo, hi = map(int, val.split("-"))
    return lo <= (h if kind == "HG" else a) <= hi


def atoms(selection):
    return selection.split("&")


def failed_atoms(selection, h, a):
    return [x for x in atoms(selection) if not atom_ok(x, h, a)]


def family(selection):
    return "+".join(x.split(":")[0] for x in atoms(selection))


def _catalog():
    out = [f"R:{r}" for r in ("1", "X", "2", "1X", "X2", "12")]
    out += ["BTTS:Y", "BTTS:N"]
    out += [f"TG:{s}{line}" for line in ("1.5", "2.5", "3.5") for s in "OU"]
    out += [f"R:{r}&BTTS:{b}" for r in ("1X", "X2", "12") for b in "YN"]
    out += [f"R:{r}&TG:{s}{line}" for r in ("1", "X", "2", "1X", "X2", "12")
            for line in ("1.5", "2.5") for s in "OU"]
    out += [f"HG:{x}&AG:{y}" for x in RANGES for y in RANGES]
    return out


CATALOG = _catalog()
_N = MAX_GOALS + 1
_MASKS = {
    sel: np.array([[all(atom_ok(x, h, a) for x in atoms(sel)) for a in range(_N)]
                   for h in range(_N)], dtype=float)
    for sel in CATALOG}


def probabilities(matrix):
    return {sel: float((matrix * mask).sum()) for sel, mask in _MASKS.items()}


_ATOM_LABEL = {"BTTS:Y": "GOAL", "BTTS:N": "NOGOAL"}


def label(selection):
    parts = []
    for x in atoms(selection):
        kind, val = x.split(":")
        if x in _ATOM_LABEL:
            parts.append(_ATOM_LABEL[x])
        elif kind == "R":
            parts.append(val)
        elif kind == "TG":
            parts.append(("OVER " if val[0] == "O" else "UNDER ") + val[1:])
        elif kind == "HG":
            parts.append(f"MULTIGOAL CASA {val}")
        elif kind == "AG":
            parts.append(f"MULTIGOAL OSPITE {val}")
        elif kind == "MGT":
            parts.append(f"MULTIGOAL {val}")
        else:
            parts.append(x)
    return " + ".join(parts)
