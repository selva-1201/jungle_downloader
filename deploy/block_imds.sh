#!/usr/bin/env bash
# ==============================================================================
# deploy/block_imds.sh (Legacy Compatibility Wrapper)
# ==============================================================================
# Thin wrapper redirecting to deploy/block_internal_egress.sh
# ==============================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${SCRIPT_DIR}/block_internal_egress.sh" "$@"
