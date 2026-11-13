# PowerShell wrapper to run SSL Certificate generator for Chappie
$ErrorActionPreference = "Stop"

Write-Host "Generating self-signed Root CA and service SSL certificates for Chappie..." -ForegroundColor Cyan
uv run python scripts/generate_certs.py

Write-Host "Certificates generated successfully in ./certs" -ForegroundColor Green
