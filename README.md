# IPTV VOD Downloader

Applicazione desktop in Python per esplorare e scaricare i contenuti VOD (film e serie TV) forniti da server IPTV compatibili con Xtream Codes.

## Requisiti

- Python 3.9 o successivo
- Dipendenze Python: `requests`

Installa i requisiti con:

```bash
pip install -r requirements.txt
```

## Avvio

```bash
python main.py
```

## Funzionalità principali

- Configurazione di URL, username, password e cartella di download della lista IPTV.
- Navigazione per categoria dei film e delle serie VOD.
- Ricerca testuale tra i titoli correnti.
- Selezione multipla dei film e aggiunta alla coda di download.
- Gestione delle serie tramite finestra dedicata per scegliere le singole puntate.
- Gestione della coda con stato e progressione dei download.
- Organizzazione automatica dei file scaricati:
  - Film salvati nella sottocartella `Film`.
  - Serie salvate in `Serie/<Nome Serie>/Stagione XX/`.

## Note

- L'app supporta server IPTV con API Xtream Codes (`player_api.php`).
- I download avvengono in sequenza per ridurre il carico sul server.
- I dati di configurazione sono salvati in `~/.iptv_vod_downloader/config.json`.

