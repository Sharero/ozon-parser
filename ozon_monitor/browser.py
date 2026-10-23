"""Select a browser binary without changing the user's personal browser profile."""
import shutil
from pathlib import Path


def browser_options(kind: str, directory: Path, executable: str = '') -> dict:
    if kind == 'system-chromium':
        selected = executable or shutil.which('chromium') or shutil.which('chromium-browser')
        if not selected or not Path(selected).is_file():
            raise ValueError('Системный Chromium не найден. Укажите CHROMIUM_EXECUTABLE_PATH в .env (например /usr/bin/chromium).')
        return {'executable_path': selected, 'user_data_dir': str(directory / 'browser-system-chromium')}
    if kind == 'playwright':
        return {'channel': 'chromium', 'user_data_dir': str(directory / 'browser')}
    raise ValueError('BROWSER должен быть system-chromium или playwright')


def automation_options() -> dict:
    # Same Chromium flags used by the reference project's browser bootstrap.
    return {'args': ['--disable-blink-features=AutomationControlled'],
            'ignore_default_args': ['--enable-automation']}


def prepare_context(context):
    context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
