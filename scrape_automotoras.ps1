$queries = @(
    @{query="automotora";        category="Automotora"},
    @{query="concesionaria";     category="Automotora"},
    @{query="venta de autos";    category="Automotora"},
    @{query="compraventa autos"; category="Automotora"},
    @{query="repuestos autos";   category="Repuestos"},
    @{query="agencia de autos";  category="Automotora"}
)

foreach ($item in $queries) {
    Write-Host "`n========================================" -ForegroundColor Cyan
    Write-Host "Scrapeando: $($item.query) -> rubro: $($item.category)" -ForegroundColor Cyan
    Write-Host "========================================`n" -ForegroundColor Cyan
    python main.py scrape-multi --query $item.query --max-per-dept 500 --workers 2 --verify-web --category $item.category --skip-branded
}

Write-Host "`nTodas las busquedas completadas." -ForegroundColor Green
