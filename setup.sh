#!/bin/bash
# AgentTasker MCP Server - Quick Setup Script
# This script sets up the MCP server for use with AI agents

set -e

echo "======================================"
echo "  AgentTasker MCP Server Setup"
echo "======================================"
echo ""

# Check Python version
PYTHON_VERSION=$(python3 --version 2>&1 | cut -d' ' -f2 | cut -d'.' -f1,2)
REQUIRED_VERSION="3.10"

if [ "$(printf '%s\n' "$REQUIRED_VERSION" "$PYTHON_VERSION" | sort -V | head -n1)" != "$REQUIRED_VERSION" ]; then
    echo "Error: Python $REQUIRED_VERSION+ is required (found $PYTHON_VERSION)"
    exit 1
fi

echo "[1/3] Python $PYTHON_VERSION detected"

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "[2/3] Creating virtual environment..."
    python3 -m venv venv
else
    echo "[2/3] Virtual environment already exists"
fi

# Activate and install dependencies
echo "[3/3] Installing dependencies..."
source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q

echo ""
echo "======================================"
echo "  Setup Complete!"
echo "======================================"
echo ""
echo "To start the MCP server:"
echo "  source venv/bin/activate"
echo "  python mcp_server.py"
echo ""
echo "Or with custom workers:"
echo "  python mcp_server.py --workers 8"
echo ""
echo "See README.md for Claude Desktop configuration."
echo ""
