#!/usr/bin/env bash
set -euo pipefail

# StreamCut Production Deployment Script

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

info() { echo -e "${GREEN}[INFO]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

DRY_RUN=false
NO_BUILD=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --no-build)
            NO_BUILD=true
            shift
            ;;
        *)
            error "Unknown option: $1"
            exit 1
            ;;
    esac
done

COMPOSE_CMD="docker compose -f docker-compose.yml -f docker-compose.gpu.yml -f docker-compose.prod.yml"

info "Starting StreamCut deployment..."

# ── Prerequisites ─────────────────────────────────────────────────────────────

info "Checking prerequisites..."

# Docker Compose v2+
if ! docker compose version &>/dev/null; then
    error "Docker Compose v2+ is required but not found."
    exit 1
fi
COMPOSE_VERSION=$(docker compose version --short 2>/dev/null || echo "unknown")
info "Docker Compose version: $COMPOSE_VERSION"

# nvidia-smi (warn only)
if ! command -v nvidia-smi &>/dev/null; then
    warn "nvidia-smi not found. GPU acceleration may not be available."
else
    info "nvidia-smi found."
fi

# Disk space (50+ GB free on /)
FREE_GB=$(df -BG / | awk 'NR==2 {gsub(/G/,""); print $4}')
if [[ "$FREE_GB" -lt 50 ]]; then
    error "Insufficient disk space. Required: 50+ GB free, Found: ${FREE_GB} GB"
    exit 1
fi
info "Disk space OK: ${FREE_GB} GB free"

# RAM (8+ GB)
TOTAL_RAM_GB=$(free -g 2>/dev/null | awk '/^Mem:/{print $2}' || echo "0")
if [[ "$TOTAL_RAM_GB" -lt 8 ]]; then
    error "Insufficient RAM. Required: 8+ GB, Found: ${TOTAL_RAM_GB} GB"
    exit 1
fi
info "RAM OK: ${TOTAL_RAM_GB} GB"

# .env file exists
if [[ ! -f ".env" ]]; then
    error ".env file not found. Please create it manually before deploying."
    exit 1
fi
info ".env file found"

# ── Validate .env ─────────────────────────────────────────────────────────────

info "Validating .env..."

if grep -qEi '^JWT_SECRET\s*=\s*(changeme|change_me|placeholder|default|secret|YOUR_JWT_SECRET|CHANGE_ME)\s*$' .env 2>/dev/null || \
   ! grep -qE '^JWT_SECRET\s*=\s*[^\s#]+' .env 2>/dev/null; then
    error "JWT_SECRET is not set or is using a placeholder value in .env"
    exit 1
fi
info "JWT_SECRET OK"

# Reject placeholders first
if grep -qEi 'YOUR|PLACEHOLDER|EXAMPLE' .env 2>/dev/null; then
    error "OPENAI_API_KEY appears to be a placeholder in .env"
    exit 1
fi
# Then validate format
if ! grep -qE '^OPENAI_API_KEY\s*=\s*sk-[a-zA-Z0-9_-]{20,}' .env 2>/dev/null; then
    error "OPENAI_API_KEY is missing or invalid in .env"
    exit 1
fi
info "OPENAI_API_KEY OK"

if [[ "$DRY_RUN" == true ]]; then
    info "--dry-run specified. Prerequisites passed. Exiting without build/start."
    exit 0
fi

# ── Create storage directories ────────────────────────────────────────────────

info "Creating storage directories..."
mkdir -p storage/{downloads,processed,temp,cache,footage_library}
info "Storage directories ready"

# ── Build ─────────────────────────────────────────────────────────────────────

if [[ "$NO_BUILD" == true ]]; then
    info "--no-build specified. Skipping image build."
else
    info "Building Docker images..."
    $COMPOSE_CMD build
    info "Build complete"
fi

# ── Start services ────────────────────────────────────────────────────────────

info "Starting services..."
$COMPOSE_CMD up -d
info "Services started"

# ── Health check ──────────────────────────────────────────────────────────────

info "Waiting for backend health check..."
HEALTH_URL="http://localhost:8003/health"
TIMEOUT=120
ELAPSED=0
INTERVAL=2

while [[ $ELAPSED -lt $TIMEOUT ]]; do
    if curl -sf "$HEALTH_URL" &>/dev/null; then
        info "Health check passed: $HEALTH_URL responded with HTTP 200"
        break
    fi
    sleep $INTERVAL
    ELAPSED=$((ELAPSED + INTERVAL))
done

if [[ $ELAPSED -ge $TIMEOUT ]]; then
    error "Health check timed out after ${TIMEOUT}s: $HEALTH_URL did not respond with HTTP 200"
    exit 1
fi

# ── Print status ──────────────────────────────────────────────────────────────

info "Deployment status:"
echo ""
$COMPOSE_CMD ps

echo ""
info "StreamCut deployed successfully!"
