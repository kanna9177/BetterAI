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

    def test_real_odds_override_estimated(self):
        """Quando una leg ha una quota in odds(match_id, selection, ...), la
        schedina la usa al posto della stima MARGIN/p, con la fonte registrata."""
        model = fit_from_db(self.con, "2025-03-01")
        match_rows = self.con.execute(
            "SELECT id FROM matches WHERE season = 'live'").fetchall()
        # Inserisce una quota reale (molto diversa dalla stima) per R:1 di ogni match.
        for (mid,) in match_rows:
            self.con.execute(
                "INSERT INTO odds(match_id, selection, odds, source) VALUES (?,?,?,?)",
                (mid, "R:1", 99.99, "sisal"))
        self.con.commit()
        slips.generate(self.con, model, "2025-03-01", n=30, seed=7)
        rows = self.con.execute(
            "SELECT odds, odds_source FROM legs WHERE selection = 'R:1'").fetchall()
        self.assertGreater(len(rows), 0)
        for r in rows:
            self.assertEqual(r["odds"], 99.99)
            self.assertEqual(r["odds_source"], "sisal")
        # Le leg senza quota reale devono restare stimate.
        est = self.con.execute(
            "SELECT COUNT(*) FROM legs WHERE selection != 'R:1' AND odds_source = 'stimato'"
        ).fetchone()[0]
        self.assertGreater(est, 0)

    def test_unplayed_match_is_voided(self):
        model = fit_from_db(self.con, "2025-03-01")
        slips.generate(self.con, model, "2025-03-01", n=5, seed=1)
        self.con.execute("UPDATE matches SET hg = 1, ag = 1 "
                         "WHERE season = 'live' AND id % 2 = 0")
        learn.settle(self.con, "2025-04-01")
        q = lambda sql: self.con.execute(sql).fetchone()[0]
        self.assertEqual(q("SELECT COUNT(*) FROM slips WHERE status = 'open'"), 0)
        self.assertGreater(q("SELECT COUNT(*) FROM legs WHERE result = -1"), 0)


def _fake_sisal_payload():
    """Mini payload con la shape vera dell'endpoint schedaDisciplina:
    Inter - Milan con 1X2 + DC + Goal/Nogoal + U/O 2.5 + MULTIGOAL (ignorato).
    Un secondo evento (Juve - Napoli) con solo 1X2 e un esito sospeso."""
    return {
        "avvenimentoFeList": [
            {
                "descrizione": "F.C. Internazionale - A.C. Milan",
                "data": "2026-11-20T19:45:00.000Z",
                "codiceManifestazione": 209,  # ITA Serie A
                "eventId": 7777777,
                "scommessaKeyDataList": [
                    {"codiceScommessa": 3,     "key": "100-500-3"},
                    {"codiceScommessa": 28319, "key": "100-500-28319"},
                    {"codiceScommessa": 18,    "key": "100-500-18"},
                    {"codiceScommessa": 7989,  "key": "100-500-7989"},
                    {"codiceScommessa": 30562, "key": "100-500-30562"},  # multigoal ignorato
                ],
            },
            {
                "descrizione": "Juventus - Napoli",
                "data": "2026-11-21T14:00:00.000Z",
                "codiceManifestazione": 209,
                "eventId": 7777778,
                "scommessaKeyDataList": [{"codiceScommessa": 3, "key": "100-501-3"}],
            },
        ],
        "infoAggiuntivaMap": {
            "100-500-3-0": {"stato": 1, "soglia": "", "idInfoAggiuntiva": 0, "esitoList": [
                {"descrizione": "1", "quota": 185, "stato": 1, "codiceEsito": 1},
                {"descrizione": "X", "quota": 360, "stato": 1, "codiceEsito": 2},
                {"descrizione": "2", "quota": 420, "stato": 1, "codiceEsito": 3},
            ]},
            "100-500-28319-0": {"stato": 1, "soglia": "", "idInfoAggiuntiva": 0, "esitoList": [
                {"descrizione": "1X", "quota": 125, "stato": 1, "codiceEsito": 1},
                {"descrizione": "X2", "quota": 170, "stato": 1, "codiceEsito": 3},
                {"descrizione": "12", "quota": 140, "stato": 1, "codiceEsito": 2},
            ]},
            "100-500-18-0": {"stato": 1, "soglia": "", "idInfoAggiuntiva": 0, "esitoList": [
                {"descrizione": "GOAL",   "quota": 165, "stato": 1, "codiceEsito": 1},
                {"descrizione": "NOGOAL", "quota": 220, "stato": 1, "codiceEsito": 2},
            ]},
            "100-500-7989-150": {"stato": 1, "soglia": "1.5", "idInfoAggiuntiva": 150, "esitoList": [
                {"descrizione": "UNDER", "quota": 500, "stato": 1, "codiceEsito": 1},
                {"descrizione": "OVER",  "quota": 110, "stato": 1, "codiceEsito": 2},
            ]},
            "100-500-7989-250": {"stato": 1, "soglia": "2.5", "idInfoAggiuntiva": 250, "esitoList": [
                {"descrizione": "UNDER", "quota": 240, "stato": 1, "codiceEsito": 1},
                {"descrizione": "OVER",  "quota": 180, "stato": 1, "codiceEsito": 2},
            ]},
            "100-500-7989-350": {"stato": 1, "soglia": "3.5", "idInfoAggiuntiva": 350, "esitoList": [
                {"descrizione": "UNDER", "quota": 160, "stato": 1, "codiceEsito": 1},
                {"descrizione": "OVER",  "quota": 210, "stato": 1, "codiceEsito": 2},
            ]},
            "100-500-7989-550": {"stato": 1, "soglia": "5.5", "idInfoAggiuntiva": 550, "esitoList": [
                # linea fuori dal catalogo betterai -> ignorata
                {"descrizione": "UNDER", "quota": 105, "stato": 1, "codiceEsito": 1},
            ]},
            "100-500-30562-0": {"stato": 1, "soglia": "", "idInfoAggiuntiva": 0, "esitoList": [
                # multigoal totale, mappato a MGT:1-3 (solo storage, non modellato)
                {"descrizione": "1-3", "quota": 150, "stato": 1, "codiceEsito": 2},
            ]},
            "100-501-3-0": {"stato": 1, "soglia": "", "idInfoAggiuntiva": 0, "esitoList": [
                {"descrizione": "1", "quota": 170, "stato": 1, "codiceEsito": 1},
                {"descrizione": "X", "quota": 340, "stato": 1, "codiceEsito": 2},
                {"descrizione": "2", "quota": 450, "stato": 0, "codiceEsito": 3},  # sospeso
            ]},
        },
    }


class SisalTest(unittest.TestCase):
    def test_share_url(self):
        url = sisal.share_url([("4944729_28319_0_3", 1.48),
                               ("4944730_3_0_1", 1.65)])
        self.assertIn("fillTicket=4944729_28319_0_3_148;4944730_3_0_1_165", url)
        self.assertTrue(url.startswith(
            "https://www.sisal.it/scommesse-matchpoint/betslip?"))
        # Se manca un tag, None
        self.assertIsNone(sisal.share_url([("abc", 2.0), (None, 1.5)]))

    def test_comp_label(self):
        # Codice Serie A noto (dal file sisal_manifestazioni.json)
        cc, name = sisal.comp_label("sisal:209")
        self.assertEqual(cc, "ITA")
        self.assertIn("Serie A", name)
        # Codice non noto -> fallback
        self.assertEqual(sisal.comp_label("sisal:999999"),
                         ("", "Manifestazione 999999"))
        # Codice non sisal
        self.assertEqual(sisal.comp_label("it.1"), ("", "it.1"))

    def test_translate_outcome(self):
        t = sisal.translate_outcome
        self.assertEqual(t(sisal.MARKET_1X2, "1", ""), "R:1")
        self.assertEqual(t(sisal.MARKET_1X2, "X", ""), "R:X")
        self.assertEqual(t(sisal.MARKET_DC, "1X", ""), "R:1X")
        self.assertEqual(t(sisal.MARKET_DC, "X2", ""), "R:X2")
        self.assertEqual(t(sisal.MARKET_BTTS, "GOAL", ""), "BTTS:Y")
        self.assertEqual(t(sisal.MARKET_BTTS, "NOGOAL", ""), "BTTS:N")
        self.assertEqual(t(sisal.MARKET_UO, "UNDER", "2.5"), "TG:U2.5")
        self.assertEqual(t(sisal.MARKET_UO, "OVER", "3.5"), "TG:O3.5")
        self.assertIsNone(t(sisal.MARKET_UO, "OVER", "5.5"))  # linea fuori catalogo
        self.assertIsNone(t(sisal.MARKET_1X2, "pareggio", ""))
        self.assertIsNone(t(99999, "1", ""))

    def test_season_of(self):
        self.assertEqual(sisal._season_of("2026-11-20"), "2026-27")
        self.assertEqual(sisal._season_of("2026-03-15"), "2025-26")
        self.assertEqual(sisal._season_of("2026-07-01"), "2026-27")
        self.assertEqual(sisal._season_of("2026-06-30"), "2025-26")

    def test_parse_curl_strips_conditional_headers(self):
        text = """curl 'https://x.example/api?z=1' \\
          -H 'Accept: application/json' \\
          -H 'If-None-Match: "abc"' \\
          -H 'User-Agent: test/1' \\
          -b 'session=42'"""
        url, hdrs = sisal.parse_curl(text)
        self.assertEqual(url, "https://x.example/api?z=1")
        self.assertNotIn("If-None-Match", hdrs)
        self.assertEqual(hdrs["Cookie"], "session=42")
        self.assertEqual(hdrs["User-Agent"], "test/1")

    def test_parse_and_store(self):
        con = db.connect(":memory:")
        events = sisal.parse_events(_fake_sisal_payload())
        self.assertEqual(len(events), 2)
        inter_milan, juve_napoli = events

        self.assertEqual(inter_milan["home"], "Inter")
        self.assertEqual(inter_milan["away"], "AC Milan")
        self.assertEqual(inter_milan["date"], "2026-11-20")
        self.assertEqual(inter_milan["time"], "19:45")
        q = inter_milan["quotes"]
        self.assertEqual(q["R:1"]["odds"], 1.85)
        self.assertEqual(q["R:X"]["odds"], 3.60)
        self.assertEqual(q["R:2"]["odds"], 4.20)
        self.assertEqual(q["R:1X"]["odds"], 1.25)
        self.assertEqual(q["R:X2"]["odds"], 1.70)
        self.assertEqual(q["R:12"]["odds"], 1.40)
        self.assertEqual(q["BTTS:Y"]["odds"], 1.65)
        self.assertEqual(q["BTTS:N"]["odds"], 2.20)
        self.assertEqual(q["TG:O1.5"]["odds"], 1.10)
        self.assertEqual(q["TG:O2.5"]["odds"], 1.80)
        self.assertEqual(q["TG:U3.5"]["odds"], 1.60)
        # 3 (1X2) + 3 (DC) + 2 (BTTS) + 2*3 (U/O 1.5/2.5/3.5) + 1 (MGT:1-3) = 15;
        # U/O 5.5 scartato (linea fuori catalogo)
        self.assertEqual(len(q), 15)
        self.assertEqual(q["MGT:1-3"]["odds"], 1.50)

        # esito sospeso (stato=0) non entra
        self.assertEqual(sorted(juve_napoli["quotes"].keys()), ["R:1", "R:X"])
        self.assertEqual(juve_napoli["quotes"]["R:1"]["odds"], 1.70)
        self.assertEqual(juve_napoli["quotes"]["R:X"]["odds"], 3.40)

        m, nq = sisal.store(con, "it.1", events)
        self.assertEqual(m, 2)
        self.assertEqual(nq, 15 + 2)
        row = con.execute(
            """SELECT o.odds, o.source_id FROM odds o JOIN matches m ON m.id = o.match_id
               JOIN teams h ON h.id = m.home_id
               WHERE h.name = 'Inter' AND o.selection = 'R:1'""").fetchone()
        self.assertEqual(row["odds"], 1.85)
        # tag = eventId_codScom_idInfoAgg_codEsito -> 1X2 esito 1 su event 7777777
        self.assertEqual(row["source_id"], "7777777_3_0_1")
        # Comp e' il codice sisal della manifestazione, non il fallback it.1
        comp = con.execute(
            "SELECT comp FROM matches WHERE id = (SELECT MIN(id) FROM matches)"
        ).fetchone()[0]
        self.assertEqual(comp, "sisal:209")
        # re-store aggiorna invece di duplicare
        sisal.store(con, "it.1", events)
        self.assertEqual(
            con.execute("SELECT COUNT(*) FROM odds").fetchone()[0], 17)


if __name__ == "__main__":
    unittest.main()
