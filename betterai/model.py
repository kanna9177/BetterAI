"""Modello dei gol: Poisson con forza d'attacco/difesa per squadra e fattore campo.

Viene ristimato a ogni giornata su tutti i risultati precedenti, pesando di piu'
le partite recenti: ogni nuovo risultato sposta le valutazioni delle squadre.
"""
import math
from dataclasses import dataclass, field
from datetime import date

import numpy as np

MAX_GOALS = 10
RHO = -0.05  # correzione Dixon-Coles sui punteggi bassi


@dataclass
class Model:
    att: dict = field(default_factory=dict)   # >1 segna piu' della media
    dfn: dict = field(default_factory=dict)   # >1 subisce piu' della media
    mu: dict = field(default_factory=dict)    # comp -> (gol casa, gol ospite) medi
    mu_all: tuple = (1.5, 1.2)

    def xg(self, comp, home, away):
        mh, ma = self.mu.get(comp, self.mu_all)
        return (mh * self.att.get(home, 1.0) * self.dfn.get(away, 1.0),
                ma * self.att.get(away, 1.0) * self.dfn.get(home, 1.0))


def fit(rows, ref_date, half_life=240.0, prior=8.0, iters=30):
    """rows: (comp, date, home_id, away_id, hg, ag) di partite gia' giocate."""
    if not rows:
        return Model()
    comps = sorted({r[0] for r in rows})
    teams = sorted({r[2] for r in rows} | {r[3] for r in rows})
    ci_of = {c: i for i, c in enumerate(comps)}
    ti_of = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    ref = date.fromisoformat(ref_date).toordinal()
    ci = np.array([ci_of[r[0]] for r in rows])
    h = np.array([ti_of[r[2]] for r in rows])
    a = np.array([ti_of[r[3]] for r in rows])
    hg = np.array([r[4] for r in rows], dtype=float)
    ag = np.array([r[5] for r in rows], dtype=float)
    age = ref - np.array([date.fromisoformat(r[1]).toordinal() for r in rows])
    w = 0.5 ** (np.maximum(age, 0) / half_life)

    wsum = np.bincount(ci, w, len(comps))
    mu_h = np.bincount(ci, w * hg, len(comps)) / wsum
    mu_a = np.bincount(ci, w * ag, len(comps)) / wsum
    mh, ma = mu_h[ci], mu_a[ci]

    att, dfn = np.ones(n), np.ones(n)
    for _ in range(iters):
        att = ((np.bincount(h, w * hg, n) + np.bincount(a, w * ag, n) + prior)
               / (np.bincount(h, w * mh * dfn[a], n)
                  + np.bincount(a, w * ma * dfn[h], n) + prior))
        dfn = ((np.bincount(h, w * ag, n) + np.bincount(a, w * hg, n) + prior)
               / (np.bincount(h, w * ma * att[a], n)
                  + np.bincount(a, w * mh * att[h], n) + prior))
    return Model(
        att={t: float(att[i]) for t, i in ti_of.items()},
        dfn={t: float(dfn[i]) for t, i in ti_of.items()},
        mu={c: (float(mu_h[i]), float(mu_a[i])) for c, i in ci_of.items()},
        mu_all=(float(np.average(hg, weights=w)), float(np.average(ag, weights=w))))


def fit_from_db(con, before_date):
    rows = con.execute(
        """SELECT comp, date, home_id, away_id, hg, ag FROM matches
           WHERE hg IS NOT NULL AND date < ?""", (before_date,)).fetchall()
    return fit([tuple(r) for r in rows], before_date)


def score_matrix(lam, nu):
    """P[h, a] = probabilita' del risultato esatto h-a."""
    k = np.arange(MAX_GOALS + 1)
    fact = np.array([math.factorial(i) for i in k], dtype=float)
    ph = np.exp(-lam) * lam ** k / fact
    pa = np.exp(-nu) * nu ** k / fact
    p = np.outer(ph, pa)
    p[0, 0] *= max(1 - lam * nu * RHO, 0.0)
    p[0, 1] *= max(1 + lam * RHO, 0.0)
    p[1, 0] *= max(1 + nu * RHO, 0.0)
    p[1, 1] *= max(1 - RHO, 0.0)
    return p / p.sum()
