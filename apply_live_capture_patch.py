from pathlib import Path
import re
import shutil
import sys

root = Path.cwd()
cli = root / "openbot" / "cli.py"
module = root / "openbot" / "data" / "live_capture.py"
browser_login = root / "openbot" / "data" / "browser_login.py"

if not cli.is_file():
    raise SystemExit("Execute este arquivo na raiz do projeto openbot-polarium (onde existe openbot/cli.py).")
if not module.is_file():
    raise SystemExit("O arquivo openbot/data/live_capture.py precisa estar ao lado deste instalador.")

source = cli.read_text(encoding="utf-8")
if "from .data.live_capture import BrowserLivePolariumClient" not in source:
    anchor = "from .data.browser_login import BrowserLoginCapture, STATE_FILE"
    if anchor in source:
        source = source.replace(anchor, anchor + "\nfrom .data.live_capture import BrowserLivePolariumClient", 1)
    else:
        anchor = "from .data.models import"
        pos = source.find(anchor)
        if pos < 0:
            raise SystemExit("Não encontrei o bloco de imports do openbot/cli.py; nenhum arquivo foi alterado.")
        source = source[:pos] + "from .data.live_capture import BrowserLivePolariumClient\n" + source[pos:]

start = source.find("def _make_polarium(env_cfg, yaml_cfg, stub=False):")
end = source.find("\n\ndef _make_apinex", start)
if start < 0 or end < 0:
    raise SystemExit("Não encontrei a função _make_polarium esperada; nenhum arquivo foi alterado.")

replacement = """def _make_polarium(env_cfg, yaml_cfg, stub=False):
    # Synthetic data is used only when explicitly requested with --stub.
    if stub:
        return StubPolariumClient()
    # Normal mode reads the authenticated Polarium trader WebSocket in-browser.
    return BrowserLivePolariumClient(login_timeout=300, headless=False)"""
source = source[:start] + replacement + source[end:]

# Live historical backtests must never silently use synthetic candles.
source = source.replace('stub: bool = typer.Option(True),', 'stub: bool = typer.Option(False),')

source = source.replace(
    'pc = _make_polarium(env_cfg, yaml_cfg, stub=stub or (not env_cfg["polarium_sso_token"] and not env_cfg["polarium_email"]))',
    'pc = _make_polarium(env_cfg, yaml_cfg, stub=stub)',
)

backup = cli.with_suffix(".py.before_live_capture.bak")
if not backup.exists():
    shutil.copy2(cli, backup)
cli.write_text(source, encoding="utf-8")

if browser_login.is_file():
    login_source = browser_login.read_text(encoding="utf-8")
    login_source = login_source.replace(
        'POLARIUM_LOGIN_URL = "https://polarium.com/login"',
        'POLARIUM_LOGIN_URL = "https://trade.polariumbroker.com/pt/login?redirect_url=traderoom%2F"',
    ).replace(
        'POLARIUM_TRADER_URL = "https://polarium.com/trader"',
        'POLARIUM_TRADER_URL = "https://trade.polariumbroker.com/traderoom"',
    ).replace(
        'success_url_pattern: str = "polarium.com"',
        'success_url_pattern: str = "trade.polariumbroker.com"',
    )
    browser_login.write_text(login_source, encoding="utf-8")

print("PATCH APLICADO — integração Polarium Traderoom, sem substituir o restante do projeto.")
print("Login: https://trade.polariumbroker.com/pt/login?redirect_url=traderoom%2F")
print("Gráficos: https://trade.polariumbroker.com/traderoom")
print("Fonte padrão: candles reais capturados do WebSocket da página de negociação.")
print("Dados sintéticos: somente com --stub explícito.")
print("Ordens: não são enviadas por este adaptador.")
print("Backup:", backup)
