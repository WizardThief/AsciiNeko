# ASCII AI Terminal Companion // Interactive Wallpaper

Un wallpaper interattivo in stile **Terminale Moderno Monocromatico (60 FPS)** progettato per **Lively Wallpaper**, **Wallpaper Engine** e Browser, con un **Companion AI in ASCII Art** che ti tiene compagnia sul desktop.

---

## 🚀 Avvio Rapido (Test Immediato + Bridge AI)

Fai doppio click su:
- **[`start_companion.bat`](file:///C:/Users/AMD04/EPO/AsciiArt/start_companion.bat)**: avvia il micro-server locale (`ai_monitor.py` su porta `8765`) che rileva quando **Antigravity / Claude / Ollama** stanno lavorando + legge CPU/RAM reali di Windows, e apre subito il wallpaper nel browser.
- Oppure apri direttamente **[`index.html`](file:///C:/Users/AMD04/EPO/AsciiArt/index.html)** nel browser.

---

## 🖥️ Come impostarlo come Sfondo Animato su Windows

### Opzione A: Lively Wallpaper (Consigliato, Gratuito & Open Source)
1. Apri **Lively Wallpaper** e clicca su **Aggiungi Sfondo (`+`)**.
2. Trascina la cartella `C:\Users\AMD04\EPO\AsciiArt` (o il file `C:\Users\AMD04\EPO\AsciiArt\index.html`) dentro Lively.
3. Grazie al file [`LivelyInfo.json`](file:///C:/Users/AMD04/EPO/AsciiArt/LivelyInfo.json) già configurato, Lively passerà in automatico i movimenti/click del mouse, lo spettro audio del PC e i dati di sistema!
4. Per avere anche il **rilevamento automatico di quando lavora l'AI** in background senza finestre aperte, avvia [`bridge/start_bridge_hidden.vbs`](file:///C:/Users/AMD04/EPO/AsciiArt/bridge/start_bridge_hidden.vbs) (puoi anche metterne un collegamento nella cartella `Esecuzione automatica` di Windows premendo `Win + R` -> `shell:startup`).

### Opzione B: Wallpaper Engine
1. Apri l'editor di **Wallpaper Engine** o scegli **Apri da file**.
2. Seleziona [`project.json`](file:///C:/Users/AMD04/EPO/AsciiArt/project.json) (o `index.html`) nella cartella `C:\Users\AMD04\EPO\AsciiArt`.

---

## 🎮 Interazioni & Stati del Companion

### Interazioni col Mouse
- **Sguardo & Parallasse 3D**: Muovi il mouse ovunque sullo schermo; il visore, gli occhi e le antenne del Companion ti seguono con fisica a molla 3D, mentre il campo fluido di sfondo si deforma al passaggio del cursore.
- **Carezza sulla Testa (`^ ‿ ^`)**: Muovi lentamente il mouse avanti e indietro sopra la parte alta della testa del Companion per fargli chiudere gli occhi felice, emettere scintille/cuori ASCII e aumentare il livello `SYNC LVL`.
- **Click / Poke (`O _ O`)**: Clicca sul Companion per farlo rimbalzare o clicca in qualsiasi punto dello schermo per generare un'**onda d'urto circolare in ASCII**.
- **Scuotimento Rapido (`@ _ @`)**: Muovi molto velocemente il mouse davanti ai suoi occhi per farlo andare temporaneamente in tilt ("dizzy").

### Toggle di Stato (Cliccabili sull'HUD o da Tastiera)
- **`[1]` `● COMPANION`**: Modalità standard; ti segue con lo sguardo, respira, sbatte le palpebre e commenta nel terminale.
- **`[2]` / `[A]` `⚡ AI WORK`**: Si attiva **in automatico** quando il bridge rileva che Antigravity, Claude o Ollama stanno lavorando (oppure manualmente). Il visore diventa uno scanner neurale ad alta frequenza e flussi di codice/token convergono verso il nucleo.
- **`[3]` / `[C]` `☕ PAUSA CAFFÈ`**: Il Companion si rilassa, compare una **tazzina di caffè fumante in ASCII** e parte il timer della pausa.
- **`[4]` `🌙 AFK / OUT`**: Attivabile con un click (o in automatico dopo 3 minuti senza muovere il mouse). Il Companion dorme (`zZz`), il campo rallenta al minimo e si risveglia appena muovi il mouse.
- **`[5]` / `[F]` `🎯 FOCUS`**: Griglia geometrica minimale e visore tattico senza distrazioni con timer sessione.
- **`[♪ AUDIO: PULSE]`**: Attiva un sintetizzatore ritmico di test per vedere la bocca/waveform del Companion e il campo ASCII pulsare a tempo (su Lively e Wallpaper Engine reagisce automaticamente alla musica/voce del PC).
- **`[◈ STYLE: BOT / 3D MASK]`**: Cambia in tempo reale il corpo del Companion tra **AI Terminal Bot** e **Maschera Neurale 3D Scolpita (SDF)**.
