"""
fate-service 路径管理模块
统一管理所有路径，避免硬编码绝对路径
"""
from pathlib import Path
import os

# 服务根目录: services/telegram-service
SERVICE_ROOT = Path(__file__).resolve().parent.parent

# fate-service 根目录: services-preview/fate-service
FATE_SERVICE_ROOT = SERVICE_ROOT.parent.parent

# tradecat 项目根目录
PROJECT_ROOT = FATE_SERVICE_ROOT.parent.parent

# 配置文件路径 (统一使用 tradecat/config/.env)
CONFIG_DIR = PROJECT_ROOT / "config"
ENV_FILE = CONFIG_DIR / ".env"

# fate-service 内部路径
LIBS_DIR = FATE_SERVICE_ROOT / "libs"
EXTERNAL_LIBS_DIR = LIBS_DIR / "external" / "github"
DATA_DIR = LIBS_DIR / "data"
DATABASE_DIR = LIBS_DIR / "database"

# 外部库路径
LUNAR_PYTHON_DIR = EXTERNAL_LIBS_DIR / "lunar-python-master"
BAZI_1_DIR = EXTERNAL_LIBS_DIR / "bazi-1-master"
SXWNL_DIR = EXTERNAL_LIBS_DIR / "sxwnl-master"
IZTRO_DIR = EXTERNAL_LIBS_DIR / "iztro-main"
FORTEL_ZIWEI_DIR = EXTERNAL_LIBS_DIR / "fortel-ziweidoushu-main"
MIKABOSHI_DIR = EXTERNAL_LIBS_DIR / "mikaboshi-main"
CHINESE_DIVINATION_DIR = EXTERNAL_LIBS_DIR / "Chinese-Divination-master"
ICHING_DIR = EXTERNAL_LIBS_DIR / "Iching-master"
HOLIDAY_CALENDAR_DIR = EXTERNAL_LIBS_DIR / "holiday-and-chinese-almanac-calendar-main"
CHINESE_CALENDAR_DIR = EXTERNAL_LIBS_DIR / "chinese-calendar-master"
JS_ASTRO_DIR = EXTERNAL_LIBS_DIR / "js_astro-master"
DANTALION_DIR = EXTERNAL_LIBS_DIR / "dantalion-main"

# 服务内部路径
SRC_DIR = SERVICE_ROOT / "src"
SCRIPTS_DIR = SERVICE_ROOT / "scripts"
OUTPUT_DIR = SERVICE_ROOT / "output"
LOGS_DIR = OUTPUT_DIR / "logs"
TXT_DIR = OUTPUT_DIR / "txt"
QUEUE_DIR = OUTPUT_DIR / "queue"

# 数据库路径
BAZI_DB_DIR = DATABASE_DIR / "bazi"
BAZI_DB_PATH = BAZI_DB_DIR / "bazi.db"

# 数据文件
CHINA_COORDS_CSV = DATA_DIR / "china_coordinates.csv"

# 脚本路径
TRUE_SOLAR_TIME_JS = SCRIPTS_DIR / "true_solar_time.js"
SXWNL_INTERFACE_JS = SXWNL_DIR / "sxwnl_interface.js"
DANTALION_BRIDGE_JS = SCRIPTS_DIR / "dantalion_bridge.js"

# prompts 目录
PROMPTS_DIR = SRC_DIR / "prompts"


def ensure_dirs():
    """确保必要目录存在"""
    for d in [LOGS_DIR, TXT_DIR, QUEUE_DIR, BAZI_DB_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def get_env_file() -> Path:
    """获取环境变量文件路径，优先使用 tradecat/config/.env"""
    if ENV_FILE.exists():
        return ENV_FILE
    # 兼容旧路径
    legacy_env = Path.home() / ".projects/fate-engine-env/.env"
    if legacy_env.exists():
        return legacy_env
    return ENV_FILE
