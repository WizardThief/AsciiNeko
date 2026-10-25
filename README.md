# ASCII AI Neko Companion // Assistente Locale Desktop (60 FPS)

Un wallpaper e assistente personale interattivo in stile **Terminale Moderno Monocromatico (60 FPS)** progettato per **Windows**, **Lively Wallpaper**, **Wallpaper Engine** e Browser, con un **Neko AI in ASCII Art** alimentato da **Ollama locale**, **Cronologia Chat & Memoria**, **Google Calendar** e **Controllo PC**.

---

## 🚀 Avvio con 1 Click

Fai semplicemente doppio click su:
- **[`start_companion.bat`](file:///C:/Users/lodir/WizardThief/AsciiNeko/start_companion.bat)**: avvia l'unico micro-server locale ([`bridge/ai_monitor.py`](file:///C:/Users/lodir/WizardThief/AsciiNeko/bridge/ai_monitor.py) su porta `8765`) e apre subito il Neko nel browser (o aggiorna lo sfondo su Lively Wallpaper).
- Per chiudere il bridge in background: fai doppio click su **[`stop_companion.bat`](file:///C:/Users/lodir/WizardThief/AsciiNeko/stop_companion.bat)**.

---

## ✨ Cosa può fare Neko nel Terminale in Basso

### 1. 💬 Chat Continua con Cronologia (`📜 STORICO`) e Memoria (`🧠 MEM`)
- **Cronologia Conversazioni**: Clicca su **`📜 STORICO`** nel terminale per aprire il pannello scorrevole con tutte le tue domande e le risposte di Neko (salvate in `bridge/chat_history.json`, così rimangono anche dopo il riavvio del PC).
- **Memoria Conversazionale**: Ollama riceve automaticamente gli ultimi messaggi della chat, le tue note salvate, i tuoi To-Do e i tuoi prossimi appuntamenti.
- **Insegnare cose a Neko**: Scrivi semplicemente:
  - `Ricorda che il cliente Rossi preferisce i render in formato EXR 16-bit`
  - `Nota: la password del server di test è nella cartella Progetti`
  - `/mem` (per vedere tutte le note salvate) o `/mem del 1` (per cancellarne una).

### 2. 📅 Google Calendar & Agenda Scadenze (`📅 AGENDA` / `⚙️ CAL`)
- **Collegare Google Calendar in 10 secondi (Zero configurazione Cloud)**:
  1. Clicca su **`⚙️ CAL`** nel terminale di Neko.
  2. Su Google Calendar Web vai in *Impostazioni ⚙️ → Clicca sul tuo calendario a sinistra → "Indirizzo segreto in formato iCal"* e copia il link `.ics`.
  3. Incollalo nella barra di Neko e clicca **SALVA LINK**.
- **Creare nuovi impegni da chat**:
  - Scrivi `Fissa call con Marco domani alle 15:00` oppure `/cal add 2026-10-28 10:30 Revisione 3D`.
  - Neko salva l'impegno nell'agenda locale (con **promemoria automatico sul desktop 15 minuti prima**) e ti mostra anche il pulsante **`[📅 + Aggiungi su Google Calendar Web]`** per salvarlo con 1 click sul tuo Google Calendar!

### 3. 🖥️ Controllo Applicazioni, File e Appunti del PC
Direttamente dalla chat di Neko puoi scrivere:
- **Avviare programmi o cartelle**:
  - `Apri Blender`, `Apri Unreal`, `Apri Photoshop`, `Apri VS Code`, `Apri Spotify`, `Apri Calcolatrice` *(cerca automaticamente in tutti i programmi del Menu Start di Windows!)*.
  - `Apri Download`, `Apri Documenti`, `Apri Desktop`, `Apri Google Calendar`, `Apri YouTube`.
- **Cercare file sul PC**:
  - `Cerca file: preventivo` oppure `/find modello` → trova i file nelle tue cartelle principali e ti mostra il pulsante **`[📂 Apri Cartella]`**.
- **Leggere / Riassumere gli Appunti di Windows (`📋 APPUNTI`)**:
  - Copia qualsiasi testo o email con `Ctrl+C` e clicca su **`📋 APPUNTI`** (o scrivi `Riassumi gli appunti` / `Traduci gli appunti` in chat).

### 4. ☀️ Briefing del Mattino & To-Do List
- Clicca su **`☀️ BRIEFING`** (o scrivi `/briefing` / `buongiorno`) per avere in un colpo solo: **Impegni di oggi + To-Do attivi + Note in Memoria + Stato CPU/RAM**.
- Usa `todo: <cosa da fare>` per aggiungere un To-Do, `/todo` per vederli e `todo done 1` per completarlo.
