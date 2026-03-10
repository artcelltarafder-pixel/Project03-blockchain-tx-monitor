#!/bin/bash
# setup.sh — run once when starting the project on a new machine

set -e

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Project 3 — TX Monitor — First Time Setup"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ── Check if .env already exists ────────────────────────────────────────────
if [ -f ".env" ]; then
  echo "⚠️  .env already exists."
  read -p "   Overwrite it? [y/N] " overwrite
  if [[ ! "$overwrite" =~ ^[Yy]$ ]]; then
    echo "   Skipping .env creation."
    echo ""
  else
    rm .env
  fi
fi

# ── Create .env if it doesn't exist ─────────────────────────────────────────
if [ ! -f ".env" ]; then
  echo "📋 You need two API keys:"
  echo "   1. Alchemy WebSocket URL  → https://dashboard.alchemy.com"
  echo "   2. CoinMarketCap API key  → https://coinmarketcap.com/api"
  echo ""

  read -p "   Alchemy WebSocket URL (wss://...): " alchemy_url
  read -p "   CoinMarketCap API key: " cmc_key

  cat > .env << EOF
ALCHEMY_WS_URL=${alchemy_url}
COINMARKETCAP_API_KEY=${cmc_key}
DATABASE_URL=postgresql://monitor:monitor@localhost:5432/txmonitor
PROMETHEUS_PORT=8000
EOF

  echo ""
  echo "✅ .env created."
fi

# ── Install Python deps ──────────────────────────────────────────────────────
echo ""
echo "📦 Installing Python dependencies..."
pip install -r <(python3 -c "
import tomllib
with open('pyproject.toml','rb') as f: d=tomllib.load(f)
deps = d.get('project',{}).get('dependencies',[])
opt = d.get('project',{}).get('optional-dependencies',{}).get('dev',[])
print('\n'.join(deps+opt))
") --break-system-packages -q
echo "✅ Dependencies installed."

# ── Start Docker stack ───────────────────────────────────────────────────────
echo ""
echo "🐳 Starting Docker stack..."
docker compose up -d
echo "✅ Docker stack up."

# ── Wait for TimescaleDB ─────────────────────────────────────────────────────
echo ""
echo "⏳ Waiting for TimescaleDB to be ready..."
until docker exec txmonitor-db pg_isready -U monitor -q 2>/dev/null; do
  sleep 1
done
echo "✅ TimescaleDB ready."

# ── Apply schema ─────────────────────────────────────────────────────────────
echo ""
echo "🗄️  Applying database schema..."
docker exec -i txmonitor-db psql -U monitor -d txmonitor < src/storage/schema.sql
echo "✅ Schema applied."

# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ✅ Setup complete. Ready to run."
echo ""
echo "  Live monitor:  python3 -m src.main"
echo "  Demo mode:     python3 demo/run_demo.py --mode B --speed 5"
echo ""
echo "  Grafana:       http://localhost:3000  (admin / admin)"
echo "  Prometheus:    http://localhost:9090"
echo "  Metrics:       http://localhost:8000/metrics"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
