from pathlib import Path
from typing import Any, Dict, Optional
import yaml

DEFAULT_SETTINGS_PATH = Path(__file__).parent / "settings.yaml"


def load_settings(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """
    Loads configuration settings from settings.yaml.
    """
    target_path = Path(config_path) if config_path else DEFAULT_SETTINGS_PATH
    if not target_path.exists():
        raise FileNotFoundError(f"Configuration file not found at {target_path.resolve()}")
    
    with open(target_path, "r", encoding="utf-8") as f:
        settings = yaml.safe_load(f)
    
    if not isinstance(settings, dict):
        raise ValueError(f"Invalid configuration format in {target_path}")
    
    return settings

