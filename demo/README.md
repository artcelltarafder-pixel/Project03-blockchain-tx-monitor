# Demo Mode — Offline Replay

Standalone demo that replays **real Ethereum mainnet transactions** captured during live monitoring.

**No API key. No internet. No Docker required.**

---

## Quick Start

From the project root:

```bash
python3 demo/run_demo.py
```

Or with flags (no prompts):

```bash
python3 demo/run_demo.py --mode B --speed 5
```

---

## Options

| Flag | Values | Description |
|------|--------|-------------|
| `--mode` | `A` or `B` | A = raw scrolling feed, B = structured dashboard |
| `--speed` | `1`, `2`, `5`, `10` | Replay speed multiplier. 5x recommended for demos |

---

## What You'll See

**Mode A — Raw Feed**
- Live coloured transaction stream
- Red = Whale (≥100 ETH), Yellow = Large (≥10 ETH), Orange = Gas spike
- Block confirmations every 20 transactions

**Mode B — Dashboard**
- Live stats panel: ETH/USD, total TX, TX/sec, whale count, large TX, gas spikes
- Scrolling transaction feed with values in ETH and USD
- Block number and uptime counter

---

## Dataset

70 real Ethereum mainnet transactions captured March 2026:

| Category | Count |
|----------|-------|
| Whale (≥100 ETH) | 17 |
| Large (≥10 ETH) | 42 |
| Gas Spikes | 2 |
| Normal | 9 |

Largest transaction: **2,999 ETH** (~$5.8M USD)

---

## Files

```
demo/
├── __init__.py
├── demo_data.py      # 70 real mainnet transactions
├── demo_runner.py    # Replay engine + Mode A/B display
├── run_demo.py       # Entrypoint
└── README.md         # This file
```

---

## Dependencies

Same as the main project (`rich` is the only runtime dependency for demo mode):

```bash
pip install rich
```
