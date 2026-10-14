# VWAPS2 — Relleno de curvas VWAP por spreads

Hermano de `VWAPS` con **la misma infraestructura** (comandos, `config.toml`, mapeo Excel/CSV con
`use = fill/off`, configuración por producto, salidas, backtest, tune, MLflow, shape). Solo cambia la
**metodología de relleno**:

- **negro**: tu VWAP;
- **azul**: `ancla + EWMA₅(EEX_hueco − EEX_ancla)`;
- **naranja**: sin ancla, `EWMA₅(EEX_hueco)`.

Todo el detalle está en [ALGORITMO.md](ALGORITMO.md) (en inglés, [ALGORITHM.md](ALGORITHM.md)).

## Arranque

```
uv sync
python run.py mapping                 # crea/revisa mappings/products.csv (o .xlsx)
python run.py refill --from 2026-08-10
python run.py daily
python run.py backtest
```

`[eex] offset_days = 0` usa la publicación del día; `-1` usa solo hasta ayer. Las ventanas de
suavizado terminan en esa misma publicación.

## Comparar con VWAPS

```
python compare_methods.py --vwap "ruta/a/tus_vwaps.xlsx"   # datos reales
python compare_methods.py --synthetic                       # datos sintéticos
```

Ejecuta los dos repositorios, cada uno con su código y su configuración, sobre el mismo input y el
mismo EEX, con `offset_days` 0 y −1. Escribe `comparison/SUMMARY.md` y no modifica ninguno de los dos
repositorios.

## Documentos heredados

`BACKTEST`, `CONFIGURATION`, `OUTPUT`, `MLFLOW`, `NOTEBOOK` y `SHAPE` vienen de VWAPS y siguen
valiendo para la infraestructura. Llevan un aviso arriba: los parámetros del método son los de
`[spread]`.
