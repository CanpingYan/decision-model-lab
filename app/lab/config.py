"""Read one local .env; credentials never become part of a public status."""
from pathlib import Path
import shlex

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_settings(path=None):
    path = Path(path or PROJECT_ROOT / '.env')
    settings = {}
    if path.exists():
        for number, raw in enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('export '):
                line = line[7:].lstrip()
            key, separator, value = line.partition('=')
            key = key.strip()
            if not separator or not key.replace('_', '').isalnum() or not key[0].isalpha():
                raise ValueError(f'.env 第 {number} 行的配置名称无效')
            value = value.strip()
            if value.startswith(('"', "'")):
                try:
                    parts = shlex.split(value, comments=True)
                    if len(parts) != 1:
                        raise ValueError()
                    value = parts[0]
                except ValueError:
                    raise ValueError(f'.env 第 {number} 行的引号无效') from None
            else:
                value = value.split(' #', 1)[0].rstrip()
            settings[key] = value
    for key in ['LAYA_MODEL_DIR', 'LAYA_PYTHON', 'CHINESE_MODEL_DIR', 'CHINESE_PYTHON', 'LAB_DATA_DIR']:
        value = settings.get(key)
        if value:
            p = Path(value).expanduser()
            settings[key] = str(p if p.is_absolute() else PROJECT_ROOT / p)
    settings.setdefault('LAB_DATA_DIR', str(PROJECT_ROOT / '.lab-data'))
    settings.setdefault('LAB_PORT', '8765')
    return settings
