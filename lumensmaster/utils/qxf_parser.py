"""
Parser de fixtures QLC+ (.qxf) vers le format JSON LumensMaster.

Le format QXF est un XML structuré défini par QLC+ contenant :
    - Manufacturer, Model, Type
    - Channels avec Preset ou Group/Capabilities
    - Modes (configurations de canaux pour un même appareil)
    - Physical (dimensions, optique, connecteur)

Usage :
    # Convertir un fichier
    fixture = parse_qxf("Eurolite-LED-PAR64-RGBA.qxf")
    save_fixture_json(fixture, "assets/fixtures/Eurolite/LED-PAR64-RGBA.json")

    # Convertir un dossier entier
    batch_convert("path/to/qxf/", "assets/fixtures/")
"""

from __future__ import annotations

import json
import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

QXF_NAMESPACE = "http://www.qlcplus.org/FixtureDefinition"
NS = {"q": QXF_NAMESPACE}


def parse_qxf(filepath: str | Path) -> dict[str, Any] | None:
    """
    Parse un fichier .qxf QLC+ et retourne un dict JSON-ready.

    Returns:
        Dict contenant la définition complète de la fixture,
        ou None en cas d'erreur.
    """
    filepath = Path(filepath)
    if not filepath.exists():
        logger.error("Fichier introuvable : %s", filepath)
        return None

    try:
        tree = ET.parse(filepath)
        root = tree.getroot()
    except ET.ParseError:
        logger.exception("Erreur de parsing XML : %s", filepath)
        return None

    # Gérer le namespace (certains fichiers l'ont, d'autres non)
    if root.tag.startswith("{"):
        ns = {"q": root.tag.split("}")[0].strip("{")}
    else:
        ns = {}

    fixture: dict[str, Any] = {
        "format": "lumensmaster_fixture",
        "format_version": 1,
        "source": "qlcplus",
        "source_file": filepath.name,
    }

    # Metadata
    fixture["manufacturer"] = _get_text(root, "Manufacturer", ns) or "Unknown"
    fixture["model"] = _get_text(root, "Model", ns) or filepath.stem
    fixture["type"] = _get_text(root, "Type", ns) or "Other"

    # Creator
    creator = root.find(_ns("Creator", ns))
    if creator is not None:
        fixture["author"] = _get_text(creator, "Author", ns) or ""
    else:
        fixture["author"] = ""

    # Channels
    fixture["channels"] = _parse_channels(root, ns)

    # Modes
    fixture["modes"] = _parse_modes(root, ns)

    # Physical
    fixture["physical"] = _parse_physical(root, ns)

    return fixture


def _ns(tag: str, ns: dict) -> str:
    """Retourne le tag avec namespace si nécessaire."""
    if ns:
        prefix = list(ns.values())[0]
        return f"{{{prefix}}}{tag}"
    return tag


def _get_text(parent, tag: str, ns: dict) -> str | None:
    """Récupère le texte d'un élément enfant."""
    elem = parent.find(_ns(tag, ns))
    if elem is not None and elem.text:
        return elem.text.strip()
    return None


def _parse_channels(root, ns: dict) -> dict[str, Any]:
    """Parse toutes les définitions de canaux."""
    channels = {}

    for channel_elem in root.findall(_ns("Channel", ns)):
        name = channel_elem.get("Name", "")
        if not name:
            continue

        channel: dict[str, Any] = {"name": name}

        # Preset (raccourci pour les canaux standard)
        preset = channel_elem.get("Preset")
        if preset:
            channel["preset"] = preset
            # Déduire le groupe et le rôle depuis le preset
            channel["group"], channel["role"] = _preset_to_group_role(preset)
        else:
            # Group explicite
            group_elem = channel_elem.find(_ns("Group", ns))
            if group_elem is not None:
                channel["group"] = group_elem.text or "Other"
                channel["byte"] = int(group_elem.get("Byte", "0"))
            else:
                channel["group"] = "Other"

            channel["role"] = ""

        # Capabilities
        capabilities = []
        for cap_elem in channel_elem.findall(_ns("Capability", ns)):
            cap: dict[str, Any] = {
                "min": int(cap_elem.get("Min", "0")),
                "max": int(cap_elem.get("Max", "255")),
                "label": (cap_elem.text or "").strip(),
            }
            # Attributs optionnels
            preset_cap = cap_elem.get("Preset")
            if preset_cap:
                cap["preset"] = preset_cap
            res1 = cap_elem.get("Res1")
            if res1:
                cap["res1"] = res1
            res2 = cap_elem.get("Res2")
            if res2:
                cap["res2"] = res2

            capabilities.append(cap)

        if capabilities:
            channel["capabilities"] = capabilities

        channels[name] = channel

    return channels


def _parse_modes(root, ns: dict) -> dict[str, Any]:
    """Parse tous les modes de la fixture."""
    modes = {}

    for mode_elem in root.findall(_ns("Mode", ns)):
        mode_name = mode_elem.get("Name", "Default")
        mode: dict[str, Any] = {"name": mode_name, "channels": [], "heads": []}

        # Canaux du mode (ordonnés par numéro)
        channel_entries = []
        for ch_elem in mode_elem.findall(_ns("Channel", ns)):
            number = int(ch_elem.get("Number", "0"))
            name = (ch_elem.text or "").strip()
            acts_on = ch_elem.get("ActsOn")

            entry: dict[str, Any] = {
                "number": number,
                "name": name,
            }
            if acts_on is not None:
                entry["acts_on"] = int(acts_on)

            channel_entries.append(entry)

        # Trier par numéro
        channel_entries.sort(key=lambda x: x["number"])
        mode["channels"] = channel_entries

        # Heads (groupes physiques de canaux)
        for head_elem in mode_elem.findall(_ns("Head", ns)):
            head_channels = []
            for ch_elem in head_elem.findall(_ns("Channel", ns)):
                if ch_elem.text:
                    head_channels.append(int(ch_elem.text.strip()))
            if head_channels:
                mode["heads"].append(head_channels)

        # Nombre de canaux
        mode["channel_count"] = len(mode["channels"])

        modes[mode_name] = mode

    return modes


def _parse_physical(root, ns: dict) -> dict[str, Any]:
    """Parse les caractéristiques physiques."""
    physical: dict[str, Any] = {}

    phys_elem = root.find(_ns("Physical", ns))
    if phys_elem is None:
        return physical

    # Bulb
    bulb = phys_elem.find(_ns("Bulb", ns))
    if bulb is not None:
        physical["bulb_type"] = bulb.get("Type", "")
        physical["bulb_lumens"] = int(bulb.get("Lumens", "0"))
        physical["bulb_color_temp"] = int(bulb.get("ColourTemperature", "0"))

    # Dimensions
    dims = phys_elem.find(_ns("Dimensions", ns))
    if dims is not None:
        physical["width"] = int(float(dims.get("Width", "0")))
        physical["height"] = int(float(dims.get("Height", "0")))
        physical["depth"] = int(float(dims.get("Depth", "0")))
        physical["weight"] = float(dims.get("Weight", "0"))

    # Lens
    lens = phys_elem.find(_ns("Lens", ns))
    if lens is not None:
        physical["lens_name"] = lens.get("Name", "")
        physical["lens_degrees_min"] = float(lens.get("DegreesMin", "0"))
        physical["lens_degrees_max"] = float(lens.get("DegreesMax", "0"))

    # Focus
    focus = phys_elem.find(_ns("Focus", ns))
    if focus is not None:
        physical["focus_type"] = focus.get("Type", "Fixed")
        physical["pan_max"] = int(float(focus.get("PanMax", "0")))
        physical["tilt_max"] = int(float(focus.get("TiltMax", "0")))

    # Layout (pour les multi-heads)
    layout = phys_elem.find(_ns("Layout", ns))
    if layout is not None:
        physical["layout_width"] = int(layout.get("Width", "1"))
        physical["layout_height"] = int(layout.get("Height", "1"))

    # Technical
    tech = phys_elem.find(_ns("Technical", ns))
    if tech is not None:
        physical["power_consumption"] = int(float(
            tech.get("PowerConsumption", "0")))
        physical["dmx_connector"] = tech.get("DmxConnector", "")

    return physical


def _preset_to_group_role(preset: str) -> tuple[str, str]:
    """
    Convertit un preset QLC+ en (group, role).
    Permet de catégoriser sémantiquement chaque canal.
    """
    mapping = {
        # Intensity
        "IntensityDimmer": ("Intensity", "dimmer"),
        "IntensityDimmerFine": ("Intensity", "dimmer_fine"),
        "IntensityMasterDimmer": ("Intensity", "master_dimmer"),
        "IntensityMasterDimmerFine": ("Intensity", "master_dimmer_fine"),
        "IntensityRed": ("Intensity", "red"),
        "IntensityRedFine": ("Intensity", "red_fine"),
        "IntensityGreen": ("Intensity", "green"),
        "IntensityGreenFine": ("Intensity", "green_fine"),
        "IntensityBlue": ("Intensity", "blue"),
        "IntensityBlueFine": ("Intensity", "blue_fine"),
        "IntensityAmber": ("Intensity", "amber"),
        "IntensityAmberFine": ("Intensity", "amber_fine"),
        "IntensityWhite": ("Intensity", "white"),
        "IntensityWhiteFine": ("Intensity", "white_fine"),
        "IntensityUV": ("Intensity", "uv"),
        "IntensityUVFine": ("Intensity", "uv_fine"),
        "IntensityCyan": ("Intensity", "cyan"),
        "IntensityCyanFine": ("Intensity", "cyan_fine"),
        "IntensityMagenta": ("Intensity", "magenta"),
        "IntensityMagentaFine": ("Intensity", "magenta_fine"),
        "IntensityYellow": ("Intensity", "yellow"),
        "IntensityYellowFine": ("Intensity", "yellow_fine"),
        "IntensityIndigo": ("Intensity", "indigo"),
        "IntensityIndigoFine": ("Intensity", "indigo_fine"),
        "IntensityLime": ("Intensity", "lime"),
        "IntensityLimeFine": ("Intensity", "lime_fine"),
        "IntensityHue": ("Intensity", "hue"),
        "IntensityHueFine": ("Intensity", "hue_fine"),
        "IntensitySaturation": ("Intensity", "saturation"),
        "IntensityValue": ("Intensity", "value"),
        "IntensityLightness": ("Intensity", "lightness"),
        # Position
        "PositionPan": ("Position", "pan"),
        "PositionPanFine": ("Position", "pan_fine"),
        "PositionTilt": ("Position", "tilt"),
        "PositionTiltFine": ("Position", "tilt_fine"),
        "PositionXAxis": ("Position", "x_axis"),
        "PositionYAxis": ("Position", "y_axis"),
        # Speed
        "SpeedPanTiltFastSlow": ("Speed", "pan_tilt_fast_slow"),
        "SpeedPanTiltSlowFast": ("Speed", "pan_tilt_slow_fast"),
        "SpeedPanFastSlow": ("Speed", "pan_fast_slow"),
        "SpeedPanSlowFast": ("Speed", "pan_slow_fast"),
        "SpeedTiltFastSlow": ("Speed", "tilt_fast_slow"),
        "SpeedTiltSlowFast": ("Speed", "tilt_slow_fast"),
        # Beam
        "BeamFocusNearFar": ("Beam", "focus_near_far"),
        "BeamFocusFarNear": ("Beam", "focus_far_near"),
        "BeamFocusFine": ("Beam", "focus_fine"),
        "BeamZoomSmallBig": ("Beam", "zoom_small_big"),
        "BeamZoomBigSmall": ("Beam", "zoom_big_small"),
        "BeamZoomFine": ("Beam", "zoom_fine"),
        # Shutter
        "ShutterOpen": ("Shutter", "open"),
        "ShutterClose": ("Shutter", "close"),
        "ShutterStrobeSlowFast": ("Shutter", "strobe_slow_fast"),
        "ShutterStrobeFastSlow": ("Shutter", "strobe_fast_slow"),
        "ShutterIrisMinToMax": ("Shutter", "iris_min_max"),
        "ShutterIrisMaxToMin": ("Shutter", "iris_max_min"),
        "ShutterIrisFine": ("Shutter", "iris_fine"),
        # Color
        "ColorMacro": ("Colour", "macro"),
        "ColorDoubleMacro": ("Colour", "double_macro"),
        "ColorWheel": ("Colour", "wheel"),
        "ColorWheelFine": ("Colour", "wheel_fine"),
        "ColorRGBMixer": ("Colour", "rgb_mixer"),
        "ColorCTBMixer": ("Colour", "ctb_mixer"),
        "ColorCTCMixer": ("Colour", "ctc_mixer"),
        "ColorCTOMixer": ("Colour", "cto_mixer"),
        # Maintenance
        "ResetAll": ("Maintenance", "reset_all"),
        "ResetPanTilt": ("Maintenance", "reset_pan_tilt"),
        "ResetPan": ("Maintenance", "reset_pan"),
        "ResetTilt": ("Maintenance", "reset_tilt"),
        "ResetColor": ("Maintenance", "reset_color"),
        "ResetGobo": ("Maintenance", "reset_gobo"),
        "ResetEffects": ("Maintenance", "reset_effects"),
        "ResetZoom": ("Maintenance", "reset_zoom"),
        "ResetIris": ("Maintenance", "reset_iris"),
        "ResetFrost": ("Maintenance", "reset_frost"),
        "ResetPrism": ("Maintenance", "reset_prism"),
        "ResetCMY": ("Maintenance", "reset_cmy"),
        "ResetMotors": ("Maintenance", "reset_motors"),
        "LampOn": ("Maintenance", "lamp_on"),
        "LampOff": ("Maintenance", "lamp_off"),
        # Misc
        "NoFunction": ("Nothing", "none"),
    }

    if preset in mapping:
        return mapping[preset]

    # Fallback : essayer de deviner depuis le nom du preset
    if preset.startswith("Intensity"):
        return ("Intensity", preset.replace("Intensity", "").lower())
    if preset.startswith("Position"):
        return ("Position", preset.replace("Position", "").lower())
    if preset.startswith("Speed"):
        return ("Speed", preset.replace("Speed", "").lower())

    return ("Other", preset.lower())


# --- Sauvegarde JSON ---

def save_fixture_json(fixture: dict, filepath: str | Path) -> bool:
    """Sauvegarde une fixture au format JSON."""
    filepath = Path(filepath)
    try:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(fixture, f, indent=2, ensure_ascii=False)
        return True
    except Exception:
        logger.exception("Erreur de sauvegarde fixture : %s", filepath)
        return False


def load_fixture_json(filepath: str | Path) -> dict[str, Any] | None:
    """Charge une fixture depuis un fichier JSON."""
    filepath = Path(filepath)
    if not filepath.exists():
        return None
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        logger.exception("Erreur de chargement fixture : %s", filepath)
        return None


# --- Conversion batch ---

def batch_convert(source_dir: str | Path, dest_dir: str | Path,
                  verbose: bool = True) -> tuple[int, int]:
    """
    Convertit tous les .qxf d'un dossier (récursivement) en JSON.

    Args:
        source_dir: Dossier contenant les .qxf (ex: QLC+ fixtures/)
        dest_dir: Dossier de destination (ex: assets/fixtures/)
        verbose: Afficher la progression

    Returns:
        (nombre convertis, nombre d'erreurs)
    """
    source_dir = Path(source_dir)
    dest_dir = Path(dest_dir)
    converted = 0
    errors = 0

    qxf_files = sorted(source_dir.rglob("*.qxf"))

    for qxf_path in qxf_files:
        fixture = parse_qxf(qxf_path)
        if fixture is None:
            errors += 1
            if verbose:
                print(f"  ERREUR: {qxf_path}")
            continue

        # Structure de sortie : dest/Manufacturer/Model.json
        manufacturer = fixture["manufacturer"].replace(" ", "_")
        model = fixture["model"].replace(" ", "-").replace("/", "-")
        json_path = dest_dir / manufacturer / f"{model}.json"

        if save_fixture_json(fixture, json_path):
            converted += 1
            if verbose and converted % 100 == 0:
                print(f"  {converted} fixtures converties...")
        else:
            errors += 1

    if verbose:
        print(f"Conversion terminée : {converted} OK, {errors} erreurs")

    return converted, errors


# --- Point d'entrée CLI ---

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Usage: python qxf_parser.py <source_dir> <dest_dir>")
        print("  source_dir : dossier contenant les .qxf QLC+")
        print("  dest_dir   : dossier de destination pour les .json")
        sys.exit(1)

    source = sys.argv[1]
    dest = sys.argv[2]
    print(f"Conversion QXF → JSON")
    print(f"  Source : {source}")
    print(f"  Dest   : {dest}")
    print()
    batch_convert(source, dest)