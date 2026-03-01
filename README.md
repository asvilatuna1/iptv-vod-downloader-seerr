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

## Build eseguibile

Per generare un `.exe` Windows in locale:

```bash
pip install -r requirements.txt pyinstaller
pyinstaller --noconfirm --clean --onedir --windowed --name iptv-vod-downloader main.py
```

Verrà creata una cartella portable in `dist/iptv-vod-downloader/` contenente l'eseguibile e le dipendenze necessarie.

## Release automatiche

Il repository include una pipeline GitHub Actions in `.github/workflows/build-release.yml` che:

- su push a `main` o avvio manuale genera un artifact scaricabile con l'eseguibile Windows;
- su push di un tag `v*` genera uno zip della cartella portable Windows e lo pubblica in GitHub Releases.

Esempio:

```bash
git tag v1.0.0
git push origin v1.0.0
```

Questo produrra' una release con l'asset `iptv-vod-downloader-windows-x64.zip`, da estrarre e avviare tramite `iptv-vod-downloader.exe`.

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
