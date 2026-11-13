# BetterAI

Simulatore di schedine combo che impara dai risultati reali. Nessun euro viene
scommesso: ogni giorno il sistema genera molte schedine, le verifica sui
risultati veri e aggiorna quello che sa.

## Il ciclo giornaliero

1. **Dati** – scarica calendari e risultati (Serie A, Premier, Liga, Bundesliga, Ligue 1).
2. **Verifica** – chiude le schedine aperte; per ogni esito perso salva il motivo
   (es. `finale 1-1 (gol attesi 1.0-1.5): fallito 12`).
3. **Impara**
   - ristima attacco, difesa e fattore campo di ogni squadra su tutti i risultati,
     pesando di più quelli recenti;
   - confronta, esito per esito, la probabilità prevista con la frequenza reale e
     corregge le previsioni future;
   - assegna più schedine alle strategie che hanno reso di più.
4. **Genera** – crea le schedine del giorno (default 50) con quattro strategie:
   `sicura`, `bilanciata`, `combo` (6-8 partite, come una multipla da quota alta),
   `multigoal`.

Esiti supportati: 1X2, doppia chance, goal/nogoal, under/over 1.5-2.5-3.5,
DC + goal/nogoal, 1X2/DC + under/over, multigoal casa + multigoal ospite.

## Uso

Serve Python 3.11+ con `numpy`.

```bash
python -m betterai sync                         # scarica partite e risultati
python -m betterai backtest --start 2024-08-01  # rivive i giorni passati e impara
python -m betterai daily                        # ciclo giornaliero (da lanciare ogni giorno)
python -m betterai report                       # risultati e correzioni imparate
python -m betterai slips --date 2026-09-20      # schedine di un giorno, con i motivi
python -m betterai team Inter                   # scheda squadra
python -m unittest discover -s tests            # test
```

Il database è `data/betterai.db` (SQLite, non versionato: si ricostruisce con
`sync` + `backtest`).

## Limiti attuali

- **Le quote sono stimate**, non reali: `0.93 / probabilità del modello`. Il saldo
  simulato quindi misura solo l'effetto dell'aggio, non un guadagno reale. La
  misura vera del miglioramento è l'errore di previsione nel `report`.
- **La fonte dati (openfootball) è gratuita ma in ritardo di settimane** e contiene
  solo i risultati. Formazioni, giocatori, nazionali e quote reali richiedono
  un'API a pagamento o con chiave; le tabelle `players` e `lineups` esistono ma
  sono vuote.
