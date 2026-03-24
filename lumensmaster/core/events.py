"""
Bus d'événements central de LumensMaster.

Permet la communication découplée entre modules via un pattern publish/subscribe.
Chaque module peut émettre des événements et s'abonner à ceux des autres
sans dépendance directe.

Événements standards :
    - dmx.update          : Le buffer DMX a été modifié
    - fader.changed       : Un fader a changé de niveau (fader_id, level)
    - grandmaster.changed : Le Grand Master a changé (level)
    - patch.updated       : Le patch a été modifié
    - show.loaded         : Un show a été chargé
    - show.saved          : Un show a été sauvegardé
    - engine.started      : Le moteur a démarré
    - engine.stopped      : Le moteur s'est arrêté
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Type pour les callbacks : reçoivent des kwargs arbitraires
EventCallback = Callable[..., None]


class EventBus:
    """
    Bus d'événements thread-safe.
    
    Utilisation :
        bus = EventBus()
        
        # S'abonner à un événement
        bus.on("fader.changed", ma_callback)
        
        # Émettre un événement
        bus.emit("fader.changed", fader_id=1, level=200)
        
        # Se désabonner
        bus.off("fader.changed", ma_callback)
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[EventCallback]] = defaultdict(list)
        self._lock = threading.Lock()

    def on(self, event: str, callback: EventCallback) -> None:
        """Abonne un callback à un type d'événement."""
        with self._lock:
            if callback not in self._subscribers[event]:
                self._subscribers[event].append(callback)
                logger.debug("Abonnement: %s -> %s", event, callback.__qualname__)

    def off(self, event: str, callback: EventCallback) -> None:
        """Désabonne un callback d'un type d'événement."""
        with self._lock:
            try:
                self._subscribers[event].remove(callback)
                logger.debug("Désabonnement: %s -> %s", event, callback.__qualname__)
            except ValueError:
                pass

    def emit(self, event: str, **kwargs: Any) -> None:
        """
        Émet un événement. Tous les callbacks abonnés sont appelés
        de manière synchrone dans le thread courant.
        """
        with self._lock:
            callbacks = list(self._subscribers.get(event, []))

        for callback in callbacks:
            try:
                callback(**kwargs)
            except Exception:
                logger.exception(
                    "Erreur dans le callback %s pour l'événement '%s'",
                    callback.__qualname__,
                    event,
                )

    def clear(self) -> None:
        """Supprime tous les abonnements."""
        with self._lock:
            self._subscribers.clear()