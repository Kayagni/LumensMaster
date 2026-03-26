"""
Point d'entrée de LumensMaster.

Lance l'application : charge la configuration, initialise le moteur,
et démarre l'interface graphique.

Usage :
    python main.py
    python main.py --dummy    (sans interface DMX physique)
    python main.py --debug    (logs détaillés)
"""

import argparse
import logging
import sys

from lumensmaster import __app_name__, __version__
from lumensmaster.core.config import AppConfig
from lumensmaster.core.engine import Engine
from lumensmaster.ui.app import App


def setup_logging(debug: bool = False) -> None:
    """Configure le système de logs."""
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=f"{__app_name__} v{__version__}")
    parser.add_argument(
        "--dummy",
        action="store_true",
        help="Mode sans interface DMX physique (pour développement/test)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Active les logs de debug",
    )
    args = parser.parse_args()

    setup_logging(debug=args.debug)
    logger = logging.getLogger(__name__)
    logger.info("%s v%s", __app_name__, __version__)

    # Charger la configuration
    config = AppConfig.load()

    # Créer le moteur
    engine = Engine(config)

    # Lancer l'interface
    app = App(engine)
    app.run(dummy_dmx=args.dummy)


if __name__ == "__main__":
    main()