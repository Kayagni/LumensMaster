# LumensMaster

**Console d'éclairage scénique open source pour le spectacle vivant.**

LumensMaster est un logiciel de régie lumière conçu pour le théâtre et le spectacle vivant. Il offre une interface moderne et épurée pour piloter des éclairages via le protocole DMX512.

Ce projet est une réécriture complète en Python du logiciel [WhiteCat](https://github.com/ChristophGuillermet/whitecat_crossplateform), initialement développé en C/C++ par Christoph Guillermet (GPL-2.0).

## Fonctionnalités

### Cœur
- **Séquenceur** — Gestion de cues avec temps de montée/descente et crossfades
- **Bangers** — Déclencheurs d'actions multiples avec nombre de steps extensible
- **Faders** — Submasters avec Grand Master
- **Trichromie** — Contrôle RGB/CMY des projecteurs à mélange de couleurs
- **MIDI** — Contrôleurs physiques, Launchpad, envoi MIDI vers logiciels/matériels externes
- **Audio** — Lecteur de pistes audio intégré
- **Sauvegarde** — Fichiers de show au format JSON

### Modules complémentaires
- Chasers
- Contrôle de motorisés (Pan/Tilt)
- Patch circuits/canaux DMX
- Light plot
- Wizard de configuration
- Intégration Arduino

## Stack technique

| Composant | Technologie |
|---|---|
| Langage | Python 3.12+ |
| Interface graphique | Dear PyGui |
| Sortie DMX | pyftdi (OpenDMX / FTDI) |
| MIDI | python-rtmidi |
| Audio | miniaudio |
| Sauvegarde | JSON |
| Distribution | PyInstaller |

## Architecture

```
lumensmaster/
├── core/               # Moteur central
│   ├── engine.py           # Boucle principale, scheduling
│   ├── dmx.py              # Sortie DMX (FTDI/OpenDMX)
│   ├── show.py             # Gestion du fichier show (save/load)
│   └── events.py           # Bus d'événements interne
├── modules/            # Fonctionnalités métier
│   ├── sequencer.py        # Séquenceur (cues, GO, timings)
│   ├── bangers.py          # Bangers multi-steps
│   ├── faders.py           # Faders submasters
│   ├── trichromie.py       # Gestion couleur RGB/CMY
│   ├── midi.py             # Communication MIDI in/out
│   ├── audio.py            # Lecteur audio
│   ├── patch.py            # Patch circuits/canaux
│   └── ...
├── ui/                 # Interface graphique
│   ├── app.py              # Fenêtre principale
│   ├── views/              # Vues par module
│   └── widgets/            # Composants réutilisables
├── main.py
└── requirements.txt
```

## Matériel supporté

- **Interface DMX** : OpenDMX (FTDI) — support natif
- **Contrôleurs MIDI** : tout contrôleur MIDI standard, support spécifique Launchpad
- **OS** : Windows 10/11

## Installation

> ⚠️ Projet en cours de développement — instructions à venir.

## Licence

Ce projet est distribué sous licence **GPL-2.0**, conformément au projet WhiteCat original.

Voir le fichier [LICENSE](LICENSE) pour plus de détails.

## Historique

LumensMaster est né du besoin de moderniser WhiteCat, un logiciel d'éclairage scénique open source dont le code source (C/C++, Allegro 4.4.2) est devenu incompatible avec les systèmes d'exploitation récents. Plutôt qu'un portage, le choix a été fait de réécrire entièrement le logiciel en Python avec une architecture modulaire, une interface moderne, et une base de code maintenable.
