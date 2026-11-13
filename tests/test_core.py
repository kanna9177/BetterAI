import unittest

from betterai import db, learn, markets, slips
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


if __name__ == "__main__":
    unittest.main()
