# openbot-polarium v2.0

Bot autônomo de opções binárias para a Polarium, com login via browser,
ensemble de 8 estratégias + 15 indicadores com **pesos aprendidos online**,
scanner multi-ativo, auto-execução (opt-in), e TUI premium.

> **Aviso realista:** Nenhum sistema pode prever mercado com precisão garantida.
> Esta ferramenta é um assistente de decisão que aprende com seus próprios erros.
> Win rate realista esperado: 50-58%, não 80%+. Use em demo primeiro.

## Diferenciais vs v1

| Recurso | v1 | v2 |
|---|---|---|
| Login Polarium | Manual (email/senha) | **Browser auto-capture** (Playwright) |
| Indicadores | 6 | **15** (+ ADX, Ichimoku, PSAR, OBV, VWAP, Williams %R, CCI, Pivot, Fib, ATR Bands) |
| Estratégias | 1 (agregada) | **8 estratégias votantes** |
| Pesos dos indicadores | Fixos | **Adaptativos** (online learning) |
| Revalidação após loss | Não | **Sim** (re-pesos top performers) |
| Scanner | 1 ativo por vez | **Multi-ativo × multi-TF paralelo** |
| Auto-execução | Não | **Sim** (com opt-in + circuit breaker) |
| Self-healing | Retry simples | **Retry + circuit breaker + watchdog + auto-reconnect WS** |
| UI | Rich CLI | **Textual TUI premium** com painel ao vivo |
| Stop win/loss | Por % | **Configurável + max trades/dia + max concurrent** |

## Arquitetura

```
openbot-polarium/
├── openbot/
│   ├── cli.py                    # CLI: login, scan, autotrade, backtest, train, status, assets, models, reset
│   ├── data/
│   │   ├── models.py             # Candle, Signal, Trade, IndicatorPerformance, etc.
│   │   ├── polarium_client.py    # Cliente Quadcode com auto-detect + self-healing
│   │   └── browser_login.py      # Playwright captura SSO/cookies automaticamente
│   ├── indicators/
│   │   ├── technical.py          # 15 indicadores + compute_indicators
│   │   ├── patterns.py           # 13 padrões de candle (Doji, Engolfo, Marubozu, etc)
│   │   └── price_action.py       # Tendência, S/R, breakout, volume
│   ├── strategies/
│   │   └── ensemble.py           # 8 estratégias independentes votantes
│   ├── core/
│   │   ├── apinex_client.py      # Cliente APInex com retry
│   │   ├── analyzer.py           # Ensemble analyzer + LLM fusion
│   │   ├── learning.py           # Online weighting engine (persiste em JSON)
│   │   └── self_healing.py       # Retry decorator + CircuitBreaker + Watchdog
│   ├── execution/
│   │   ├── bankroll.py           # Gestão com stop win/loss/entry/gale/soros
│   │   ├── scanner.py            # Scanner multi-ativo paralelo
│   │   └── executor.py           # Auto-executor (paper + real)
│   ├── ui/
│   │   └── dashboard.py           # Textual TUI premium
│   └── utils/helpers.py
├── config.yaml                   # Configuração completa
├── .env.example                  # Template de credenciais
├── requirements.txt
└── pyproject.toml
```

## Instalação

```bash
cd openbot-polarium
python -m venv .venv
source .venv/bin/activate         # Linux/macOS
# .venv\Scripts\activate         # Windows
pip install -r requirements.txt
playwright install chromium       # para o comando `openbot login`
pip install -e .
cp .env.example .env
```

## Fluxo de uso

### 1. Login automático (uma única vez)

```bash
openbot login
```

Abre um navegador Chromium apontando para `polarium.com/login`. Você faz login
normalmente (email, senha, 2FA, captcha — tudo no browser). O bot captura
automaticamente:
- SSO token (`ssid`)
- Cookies de sessão
- Broker ID detectado
- Tipo de conta

Salva em `polarium_session.json` (chmod 600) e atualiza `.env`. Próximas
execuções usam a sessão salva — só refazer login quando expirar.

### 2. Configurar APInex

Pegue sua key em `https://apinex.bond/cli` (rode `apinex login` + `apinex api-key`)
e coloque no `.env`:

```
APINEX_API_KEY=apx_xxxxxxxxxxxxxxxxxxxxxxxxxxxx
APINEX_MODEL=gpt-4o-mini
```

### 3. Testar o scanner

```bash
openbot scan --stub --no-llm --top-n 5
```

Mostra os top 5 sinais ranqueados por confiança, sem precisar de conta real.

### 4. Backtest + treinar pesos

```bash
openbot backtest --stub --no-llm --candles 500
openbot train     # mostra pesos aprendidos
```

Depois do backtest, o `openbot_state.json` contém pesos aprendidos para cada
indicador. Indicadores com baixa assertividade são automaticamente
down-weighted; top performers são boostados. Após um loss, top performers
são revalidados.

### 5. Rodar o dashboard autônomo

```bash
openbot autotrade --stub --no-llm
# Modo real (CUIDADO):
openbot autotrade --mode real
```

Abre o TUI premium com:
- Header com 7 stat boxes ao vivo (modo, saldo, P&L, win rate, trades abertos, auto-exec, stop)
- Painel esquerdo: status da banca + top indicadores aprendidos + controles
- Painel direito: tabela de scanner (top 20 sinais) + feed de execução ao vivo

Atalhos:
- `S` = scan agora
- `R` = alternar Paper/Real
- `E` = alternar Auto-Execute on/off
- `Q` = sair

### 6. Ver status

```bash
openbot status
```

Mostra banca atual + estado do learning engine.

## Configuração (config.yaml)

```yaml
trading:
  mode: paper                    # paper | real
  entry_value: 20                # stake fixa
  stop_win_pct: 30               # para ao +30%
  stop_loss_pct: 20              # para ao -20%
  max_trades_per_day: 20
  auto_execute: false            # CUIDADO: true = ordens reais
  min_confidence_to_execute: 0.70

martingale:
  enabled: true
  max_levels: 2
  multiplier: 2.0

scanner:
  assets: [EURUSD-OTC, GBPUSD-OTC, USDJPY-OTC, BTCUSD, ETHUSD]
  timeframes: [M1, M5]
  interval_sec: 15

learning:
  enabled: true
  window: 50                     # últimos 50 trades para recalcular pesos
  rate: 0.1                      # velocidade de ajuste
  revalidate_after_loss: true    # re-pesos top performers após cada loss
  top_indicators: 8              # mantém top 8 com peso alto
```

## Como o learning funciona

Cada trade completado alimenta o `LearningEngine`:

1. Para cada indicador/estratégia que votou na direção do trade, incrementa `correct` (se ganhou) ou `wrong` (se perdeu).
2. A acurácia recente (janela de 50 trades) é calculada.
3. O peso de cada indicador é ajustado por EMA: `w = (1-α)·w_old + α·accuracy`.
4. Se `revalidate_after_loss: true`, após cada loss os top 8 indicadores (por acurácia) recebem +5% de peso, os demais -10%.
5. O estado é persistido em `openbot_state.json` — sobrevive entre execuções.

No próximo scan, indicadores com peso alto têm mais influência na decisão final.
Indicadores consistentemente errados têm peso reduzido até o piso (0.05).

## Self-healing

- **Retry com backoff exponencial** em todas as chamadas HTTP/WS (`tenacity`).
- **CircuitBreaker**: após 5 falhas consecutivas, abre o circuito por 30s (bloqueia novas chamadas para evitar cascata de erros).
- **Watchdog**: monitora tasks críticas (reader loop do WS, settle loop) e reinicia automaticamente se morrerem.
- **Auto-reconnect WS**: se a conexão cair, espera 5s e reconecta sozinho.
- **Fallback clássico**: se LLM falhar, usa só análise clássica (sem parar o bot).
- **Restart on consecutive errors**: após 5 erros consecutivos no reader, força reconexão limpa.

## Segurança

- `auto_execute: false` por padrão — precisa explicitamente ligar.
- `mode: paper` por padrão — só opera em conta demo.
- Mesmo com `mode: real`, se `auto_execute: false`, o bot só sinaliza (não abre ordem).
- Stop win/loss diário sempre ativo — mesmo em modo real, o bot para sozinho se bater o limite.
- Sessão do browser salva com `chmod 600` (só o dono lê).

## Limitações realistas

- **LLMs não preveem mercado**: GPT-4o-mini tem 50-58% de acerto em M1/M5. Não espere 80%.
- **Browser login pode quebrar**: se Polarium mudar a UI, o seletor de URL pode falhar. Re-run `openbot login`.
- **API Quadcode é reverse-engineered**: pode quebrar a qualquer momento. Use `--stub` para desenvolvimento.
- **Backtest ≠ real**: backtest em dados sintéticos valida lógica, não performance. Sempre faça paper trade com dados reais antes de operar com dinheiro.

## Disclaimer

Opções binárias são proibidas em várias jurisdições (EUA, UE para investidores
não profissionais, etc). Verifique a legislação local. Este software é uma
ferramenta de análise técnica — não constitui aconselhamento financeiro. O
risco é inteiramente seu.
