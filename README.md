# BetterAI

Simulatore di schedine combo che impara dai risultati reali. Nessun euro viene
scommesso: ogni giorno il sistema genera molte schedine, le verifica sui
risultati veri e aggiorna quello che sa.

## Il ciclo giornaliero

1. **Dati** – scarica calendari e risultati (Serie A, Premier, Liga, Bundesliga, Ligue 1).
2. **Quote reali** – opzionale, da Sisal: copia un cURL del palinsesto calcio
   da DevTools e lo passa a `sisal-sync`. Le quote entrano in `odds(match_id,
   selection, odds, source)`.
3. **Verifica** – chiude le schedine aperte; per ogni esito perso salva il motivo
   (es. `finale 1-1 (gol attesi 1.0-1.5): fallito 12`).
4. **Impara**
   - ristima attacco, difesa e fattore campo di ogni squadra su tutti i risultati,
     pesando di più quelli recenti;
   - confronta, esito per esito, la probabilità prevista con la frequenza reale e
     corregge le previsioni future;
   - assegna più schedine alle strategie che hanno reso di più.
5. **Genera** – crea le schedine del giorno (default 50) con quattro strategie:
   `sicura`, `bilanciata`, `combo` (6-8 partite, come una multipla da quota alta),
   `multigoal`. Ogni leg usa la **quota reale** quando c'è nella tabella `odds`,
   altrimenti ricade sulla stima `0.93 / probabilità del modello` (fonte segnata
   in `legs.odds_source`).

Esiti supportati: 1X2, doppia chance, goal/nogoal, under/over 1.5-2.5-3.5,
DC + goal/nogoal, 1X2/DC + under/over, multigoal casa + multigoal ospite.

## Setup

Serve Python 3.11+. Dipendenze: `numpy` (modello) e `flask` (dashboard web,
opzionale).

```bash
pip install numpy flask
```

## Uso da linea di comando

```bash
# archivio partite + risultati (fonte openfootball)
python -m betterai sync

# backtest dall'inizio di una stagione in avanti (impara giorno per giorno)
python -m betterai backtest --start 2024-08-01

# ciclo giornaliero: sync, verifica, genera schedine di oggi
python -m betterai daily

# report: cosa ha imparato, come se la cava
python -m betterai report

# schedine di un giorno con motivi
python -m betterai slips --date 2026-09-20

# scheda squadra
python -m betterai team Inter

# dashboard web
python -m betterai web               # http://127.0.0.1:5000

# test
python -m unittest discover -s tests
```

## Quote reali da Sisal

Sisal protegge l'endpoint con Akamai Bot Manager: serve un browser vero per
generare i cookies (`_abck`, `bm_sz`, `ak_bmsc`). Workflow una volta al giorno:

1. apri Brave/Chrome/Edge su
   <https://www.sisal.it/scommesse-matchpoint/palinsesto/calcio?day=today>
   (su Brave disattiva Shields per sisal.it);
2. DevTools (F12) → Network → filtra **Fetch/XHR** → ordina per **Size** desc;
3. trova la risposta più grossa (~15 MB, path contiene `schedaDisciplina`);
4. tasto destro sulla riga → **Copy → Copy as cURL (bash)**;
5. salva in `curl.txt` nella root del progetto;
6. `python -m betterai sisal-sync --curl curl.txt`.

I cookies durano ~30-60 minuti. Dopo una fetch riuscita hai 423+ partite e
~5800 quote (R:1/X/2, DC, BTTS, U/O 1.5-2.5-3.5) in `odds`. Da quel momento
`daily` e `backtest` le usano automaticamente.

Comandi collaterali:

```bash
# sincronizza da un JSON già salvato (offline / test / replay)
python -m betterai sisal-sync --json sisal.json

# probe: scarica, salva raw e stampa quanti eventi/quote estrae
python -m betterai sisal-probe --curl curl.txt --dump sisal.json
```

## Dashboard web

```bash
python -m betterai web
```

Apre un mini-sito stile bookmaker (dark theme, niente branding di terzi):

- **Palinsesto** — partite del giorno, le sei quote principali in tabella, click sul nome per i mercati completi.
- **Schedine AI** — schedine di oggi (o dell'ultimo giorno disponibile) raggruppate, con la fonte di ogni quota.
- **Modello** — fonti quote, squadre in archivio, correzioni imparate per esito, andamento giornate.
- **Aggiorna quote** (in alto a destra) — ri-sincronizza leggendo `curl.txt` dalla cwd.

## File del progetto

```
betterai/
  data.py        fonti calendari + risultati (openfootball)
  sisal.py       scraper quote Sisal (schedaDisciplina) + parser + store
  model.py       Poisson bivariato: attacco, difesa, fattore campo per squadra
  markets.py     catalogo esiti (R, DC, BTTS, TG, HG, AG) + verifica sul finale
  slips.py       generazione schedine giornaliere, 4 strategie, legge odds reali
  learn.py       chiude schedine, calibra probabilità, aggiorna strategie
  db.py          schema SQLite (teams, matches, odds, slips, calibration, ...)
  cli.py         comandi: sync, daily, backtest, report, slips, team, web,
                 sisal-sync, sisal-probe
  web.py         dashboard Flask
  templates/     HTML (base/oggi/partita/schedine/modello)
  static/        CSS

tests/test_core.py     10 test (markets, ciclo, parser Sisal, quote reali)
```

Il database è `data/betterai.db` (SQLite, non versionato: si ricostruisce con
`sync` + `backtest` + `sisal-sync`).

## Limiti attuali

- **Quote reali solo da Sisal e solo prematch**. Mancano altre fonti; serve un
  cookie refresh (manuale via `curl.txt` o automatico via playwright warmup,
  non ancora implementato).
- **I cookies Akamai scadono in ~30-60 min**; una fetch al giorno per il daily
  va bene, se vuoi live serve warmup headless-browser.
- **Giocatori e formazioni** — schema esiste, tabelle vuote: nessuna fonte
  gratuita li espone. Serve API a pagamento (API-Football ecc.) o scraping
  dedicato.
- **La fonte dati storica (openfootball) è gratuita ma in ritardo di settimane**
  e contiene solo risultati: niente marcatori, niente possesso, niente tiri.

## Idee a seguire

- warmup Playwright: apre Sisal in headless, raccoglie cookies freschi,
  riscrive `curl.txt`. Permetterebbe `daily` davvero autonomo.
- multi-disciplina: l'endpoint prende `/schedaDisciplina/{id}/...`, un loop
  copre basket/tennis/etc.
- giocatori + formazioni da una fonte a pagamento, con un `lineups` sync
  dedicato.
