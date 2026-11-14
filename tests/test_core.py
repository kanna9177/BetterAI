import unittest

from betterai import db, learn, markets, sisal, slips
from betterai.model import fit_from_db, score_matrix


class MarketsTest(unittest.TestCase):
    def test_atoms(self):
        self.assertTrue(markets.atom_ok("R:X2", 1, 1))
        self.assertFalse(markets.atom_ok("R:X2", 2, 1))
        self.assertTrue(markets.atom_ok("BTTS:N", 2, 0))
        self.assertTrue(markets.atom_ok("TG:O1.5", 1, 1))
        self.assertTrue(markets.atom_ok("TG:U2.5", 1, 1))
        self.assertTrue(markets.atom_ok("AG:2-4", 0, 4))
        self.assertFalse(markets.atom_ok("HG:1-3", 0, 4))

    def test_failed_atoms_and_label(self):
        self.assertEqual(markets.failed_atoms("R:X2&BTTS:Y", 0, 1), ["BTTS:Y"])
        self.assertEqual(markets.label("R:X2&BTTS:Y"), "X2 + GOAL")
        self.assertEqual(markets.family("HG:1-3&AG:2-4"), "HG+AG")

    def test_probabilities_are_consistent(self):
        m = score_matrix(1.6, 1.1)
        p = markets.probabilities(m)
        self.assertAlmostEqual(m.sum(), 1.0)
        self.assertAlmostEqual(p["R:1"] + p["R:X"] + p["R:2"], 1.0)
        self.assertAlmostEqual(p["R:1X"], p["R:1"] + p["R:X"])
        self.assertAlmostEqual(p["TG:O2.5"] + p["TG:U2.5"], 1.0)
        self.assertAlmostEqual(p["R:X2&BTTS:Y"] + p["R:X2&BTTS:N"], p["R:X2"])
        self.assertGreater(p["R:1"], p["R:2"])


class CycleTest(unittest.TestCase):
    def setUp(self):
        self.con = db.connect(":memory:")
        ids = [db.team_id(self.con, f"T{i}") for i in range(6)]
        n = 0
        for rnd in range(6):  # storico: T0 forte, T5 debole
            for i in range(6):
                for j in range(6):
                    if i != j and (i + j + rnd) % 3 == 0:
                        self._match(f"s{rnd}", f"2025-01-{rnd + 1:02d}", ids[i], ids[j],
                                    3 if i < j else 1, 0 if i < j else 2)
                        n += 1
        for i in range(0, 6, 2):  # giornata da pronosticare
            self._match("live", "2025-03-01", ids[i], ids[i + 1], None, None)
        self.con.commit()

    def _match(self, season, day, h, a, hg, ag):
        self.con.execute(
            "INSERT INTO matches(comp, season, date, home_id, away_id, hg, ag) "
            "VALUES ('x', ?, ?, ?, ?, ?, ?)", (season, day, h, a, hg, ag))

    def test_generate_then_settle(self):
        model = fit_from_db(self.con, "2025-03-01")
        self.assertGreater(model.att[1], model.att[6])
        made = slips.generate(self.con, model, "2025-03-01", n=20, seed=1)
        self.assertGreater(made, 0)
        self.assertEqual(slips.generate(self.con, model, "2025-03-01", n=20, seed=1), 0)
        self.assertEqual(learn.settle(self.con, "2025-03-01"), [])  # nessun risultato

        self.con.execute("UPDATE matches SET hg = 2, ag = 1 WHERE season = 'live'")
        self.assertEqual(learn.settle(self.con, "2025-03-02"), ["2025-03-01"])
        q = lambda sql: self.con.execute(sql).fetchone()[0]
        self.assertEqual(q("SELECT COUNT(*) FROM slips WHERE status = 'open'"), 0)
        # ogni schedina vinta ha tutti gli esiti presi e paga puntata x quota
        for s in self.con.execute("SELECT * FROM slips").fetchall():
            res = [r[0] for r in self.con.execute(
                "SELECT result FROM legs WHERE slip_id = ?", (s["id"],))]
            self.assertEqual(s["status"] == "won", all(res))
            self.assertAlmostEqual(
                s["payout"], round(s["stake"] * s["odds"], 2) if all(res) else 0, delta=0.05)
        self.assertEqual(q("SELECT COUNT(*) FROM calibration"), len(markets.CATALOG))
        self.assertEqual(q("SELECT n FROM calibration WHERE selection = 'R:1'"), 3)
        self.assertEqual(q("SELECT hits FROM calibration WHERE selection = 'R:1'"), 3)
        self.assertEqual(q("SELECT slips FROM learning_log"), made)

    def test_unplayed_match_is_voided(self):
        model = fit_from_db(self.con, "2025-03-01")
        slips.generate(self.con, model, "2025-03-01", n=5, seed=1)
        self.con.execute("UPDATE matches SET hg = 1, ag = 1 "
                         "WHERE season = 'live' AND id % 2 = 0")
        learn.settle(self.con, "2025-04-01")
        q = lambda sql: self.con.execute(sql).fetchone()[0]
        self.assertEqual(q("SELECT COUNT(*) FROM slips WHERE status = 'open'"), 0)
        self.assertGreater(q("SELECT COUNT(*) FROM legs WHERE result = -1"), 0)


class SisalTest(unittest.TestCase):
    def test_translate_selection(self):
        t = sisal.translate_selection
        self.assertEqual(t("1X2", "1"), "R:1")
        self.assertEqual(t("Esito finale", "X"), "R:X")
        self.assertEqual(t("Doppia chance", "X2"), "R:X2")
        self.assertEqual(t("Goal/Nogoal", "GOAL"), "BTTS:Y")
        self.assertEqual(t("Goal/Nogoal", "NOGOAL"), "BTTS:N")
        self.assertEqual(t("U/O 2.5", "UNDER 2.5"), "TG:U2.5")
        self.assertEqual(t("Under/Over 2,5", "OVER 2,5"), "TG:O2.5")
        self.assertEqual(t("Multigol casa", "1-3"), "HG:1-3")
        self.assertEqual(t("Multigol ospite", "2-4"), "AG:2-4")
        self.assertIsNone(t("Marcatore", "Lautaro"))
        self.assertIsNone(t("1X2", "pareggio"))

    def test_season_of(self):
        self.assertEqual(sisal._season_of("2026-11-20"), "2026-27")
        self.assertEqual(sisal._season_of("2026-03-15"), "2025-26")
        self.assertEqual(sisal._season_of("2026-07-01"), "2026-27")
        self.assertEqual(sisal._season_of("2026-06-30"), "2025-26")

    def test_parse_and_store(self):
        con = db.connect(":memory:")
        payload = {"eventi": [{
            "dataOra": "2026-11-20T20:45",
            "casa": "F.C. Internazionale", "ospite": "A.C. Milan",
            "quote": [
                {"tipo": "1X2", "esito": "1", "quota": 1.85},
                {"tipo": "1X2", "esito": "X", "quota": 3.60},
                {"tipo": "1X2", "esito": "2", "quota": 4.20},
                {"tipo": "Doppia chance", "esito": "1X", "quota": 1.25},
                {"tipo": "Goal/Nogoal", "esito": "GOAL", "quota": 1.65},
                {"tipo": "U/O 2.5", "esito": "OVER 2.5", "quota": 1.80},
                {"tipo": "Multigol casa", "esito": "1-3", "quota": 1.40},
                {"tipo": "Marcatore", "esito": "Lautaro", "quota": 2.50},
            ],
        }, {
            "dataOra": "2026-11-21T15:00",
            "casa": "Juventus", "ospite": "Napoli",
            "quote": [{"tipo": "1X2", "esito": "2", "quota": 2.10}],
        }]}
        events = sisal.parse_events(payload)
        self.assertEqual(len(events), 2)
        inter_milan, juve_napoli = events
        self.assertEqual(inter_milan["home"], "Inter")
        self.assertEqual(inter_milan["away"], "AC Milan")
        self.assertEqual(inter_milan["date"], "2026-11-20")
        self.assertEqual(inter_milan["time"], "20:45")
        self.assertNotIn("Marcatore", inter_milan["quotes"])  # non modellato -> scartato
        self.assertEqual(inter_milan["quotes"]["R:1"], 1.85)
        self.assertEqual(inter_milan["quotes"]["BTTS:Y"], 1.65)
        self.assertEqual(inter_milan["quotes"]["TG:O2.5"], 1.80)
        self.assertEqual(inter_milan["quotes"]["HG:1-3"], 1.40)

        m, q = sisal.store(con, "it.1", events)
        self.assertEqual(m, 2)
        self.assertEqual(q, 7 + 1)  # 7 quote Inter-Milan modellate, 1 Juve-Napoli
        row = con.execute(
            """SELECT o.odds FROM odds o JOIN matches m ON m.id = o.match_id
               JOIN teams h ON h.id = m.home_id
               WHERE h.name = 'Inter' AND o.selection = 'R:1'""").fetchone()
        self.assertEqual(row[0], 1.85)
        # re-store aggiorna invece di duplicare
        sisal.store(con, "it.1", events)
        self.assertEqual(
            con.execute("SELECT COUNT(*) FROM odds").fetchone()[0], 8)


if __name__ == "__main__":
    unittest.main()
