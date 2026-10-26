# 🐾 AsciiNeko — Assistente Personale AI Locale & Desktop App (60 FPS)

Un'applicazione desktop indipendente per Windows con un **gattino Neko interattivo in ASCII Art (60 FPS)** e un **Assistente Personale AI locale** alimentato da **Ollama**, con sistema di **Memoria a 3 Livelli**, **Schede Progetto & Pin Server (Zero Scansione)**, **Google Calendar**, **Ispettore Memorie** e **Controllo PC**.

---

## 🚀 Avvio e Chiusura con 1 Click

- **Avvia Neko**: fai doppio click su **`Avvia_Neko_App.bat`**
  - Avvia in background il server locale (`bridge/ai_monitor.py` su `http://127.0.0.1:8765`), apre la finestra dedicata senza barre del browser (`bridge/app_window.py`) e attiva la scorciatoia globale **`Alt + N`**.
- **Chiudi Neko**: fai doppio click su **`Chiudi_Neko_App.bat`**
  - Chiude la finestra di Neko, il listener della scorciatoia e il bridge Python in background.

---

## ✨ Funzionalità Principali

### 1. 🖥️ App Desktop 3-in-1 & Scorciatoia Globale (`Alt + N`)
- **Richiamo istantaneo (`Alt + N`)**: premi **`Alt + N`** da qualsiasi programma (es. 3ds Max, Blender, Photoshop, Browser) per portare Neko in primo piano con il cursore già pronto nella barra di comando (personalizzabile in `Alt + N`, `Alt + Q`, `Ctrl + Spazio` o `Alt + Z` per non avere conflitti con Gemini).
- **Modalità Mini-Widget Always-on-Top (`🗕 Mini`)**: riduce la finestra a un widget compatto sempre in primo piano nell'angolo dello schermo mentre lavori.

### 2. 🧠 Memoria Intelligente a 3 Livelli & Cloud Sync (Google Drive / OneDrive)
Tutti i dati personali sono separati dal codice in una cartella dedicata (`bridge/data/` di default, oppure una cartella a tua scelta su **Google Drive / OneDrive** configurabile in `🔌 Connessioni`), così il repository Git contiene **solo codice** e le tue memorie si sincronizzano in automatico tra Casa e Ufficio:
1. **`🐾 Conosciamoci` (Profilo Base — `profile.json`)**:
   - Rispondi quando vuoi a domande rapide a scelta multipla (o risposta libera) divise per categorie (*Lavoro & Strumenti*, *Routine & Orari*, *Clienti & Progetti*, *Stile & Preferenze*).
2. **`📌 Progetti & Server` (Memoria Calda — `projects.json`)**:
   - Crea schede per i progetti in corso (es. *"Villa Rossi"*) con le regole del cliente (*"Il Sig. Rossi vuole sempre tutto minimale"*).
   - **Pin Rapidi Server (`📌 Fissa Cartelle Aperte`)**: legge in 1 millisecondo le cartelle che hai già aperte in Esplora File di Windows (anche su Server NAS aziendali `\\Server\...` o lettere di rete) senza **mai** scansionare il server.
3. **`📦 Archivio Storico Clienti` (Memoria Fredda — `archive.json`)**:
   - Quando un progetto termina (o è inattivo da >14 giorni), puoi archiviarlo con 1 click: le regole di quel progetto non appesantiscono i task quotidiani, ma se in futuro crei un nuovo progetto per lo stesso cliente Neko le ritrova e ti propone di riattivarle.

### 3. ⚙️ Control Hub Centrale, Ispettore Memorie & Reset
Cliccando su **`⚙️ Impostazioni & Memorie`** si apre il centro di controllo a 5 schede:
- **`🐾 Conosciamoci`**: domande guidate per insegnare a Neko chi sei.
- **`📌 Progetti & Server`**: gestione progetti attivi, Pin cartelle Server e Archivio Storico.
- **`🧠 Memorie & To-Do`**: ispettore trasparente per leggere, aggiungere o **eliminare singolarmente** ogni nota (`memory.json`), To-Do (`todo.json`) o risposta del profilo (`profile.json`).
- **`🔌 Connessioni`**: collegamento facilitato a **Google Calendar (`.ics`)**, selettore e download 1-click modelli **Ollama** (`qwen2.5:7b`), scelta della **Cartella Memoria (Google Drive / OneDrive / Locale)** e Link Rapidi.
- **`⚙️ Scorciatoia & Reset`**: cambio scorciatoia globale in tempo reale, **Reset Selettivi** (solo Chat, solo Profilo, solo Memorie, solo To-Do, solo Progetti) e **`⚠️ RESET TOTALE DI NEKO`**.
