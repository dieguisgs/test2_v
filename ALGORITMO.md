# VWAPS2 — Algoritmo de relleno por spreads

VWAPS2 completa cada día la curva de power de cada producto con la **metodología de spreads**:
tus VWAPs son los puntos fijos y EEX solo aporta **las diferencias entre vencimientos**, suavizadas
con una media exponencial de los últimos días.

Es un proyecto hermano de `VWAPS`, con la misma estructura: mismos comandos, mismo `config.toml`
(salvo la sección del método), mismo mapeo Excel/CSV con `use = fill/off` y mismas salidas.
**Solo cambia cómo se rellenan los huecos.** El objetivo es comparar los dos métodos con los mismos
datos (`compare_methods.py`, sección 8).

---

## 1. La idea

La regla de trabajo es: *“nos interesa el spread entre contratos, no el valor outright”*.

| Color | Qué es | Cómo se calcula |
|---|---|---|
| **Negro** | Tu VWAP del día | Tal cual |
| **Azul** | Hueco con un VWAP propio cerca (el *ancla*) | `ancla + EWMA₅(EEX_hueco − EEX_ancla)` |
| **Naranja** | Hueco sin ningún ancla | `EWMA₅(EEX_hueco)` |

El azul también se puede leer como **EEX suavizado + ajuste local**:

```
azul = ancla + EWMA(EEX_hueco − EEX_ancla)
     = EWMA(EEX_hueco) + [ancla − EWMA(EEX_ancla)]
       ───────────────   ───────────────────────
       forma de EEX       tu nivel en el ancla
```

La igualdad es exacta porque la media ponderada es lineal. El naranja es el mismo cálculo sin el
ajuste local.

---

## 2. Paso a paso

Para cada día de referencia `T`, cada producto y cada tenor objetivo:

### Paso 0 — Tenors a fechas de entrega
`M+2`, `Q+1`, `Win+1`… se traducen a fechas reales de entrega `[inicio, fin)`. Es el mismo código
que en VWAPS. Todo se cruza por fechas, así que tu `Q+1` y el trimestre de EEX coinciden aunque
EEX lo etiquete distinto (por ejemplo, tras el *cascading*).

### Paso 1 — Ventana de publicaciones de EEX permitidas
- Corte = `T + offset_days`. Con `0` vale la publicación de `T` (o la última anterior); con `-1`
  nunca se usa la del propio día.
- La ventana son las últimas `window` publicaciones (5 por defecto) hasta ese corte.
- Nada posterior al corte entra en ningún cálculo.

### Paso 2 — Precio EEX de cada contrato en cada publicación
Si EEX no cotiza el contrato exacto, se construye por horas (meses → trimestre, trimestres →
season, días que quedan → BOM…), igual que en VWAPS. Si en una publicación no hay forma de
sacarlo, esa publicación se salta.

### Paso 3 — Pesos de la media exponencial
El peso de una publicación es `2^(−edad / halflife)`, con edad 0 = la más reciente. Los pesos se
normalizan para sumar 1 sobre las publicaciones que tienen dato. Con `window = 5` y `halflife = 2`:

| Publicación | T−4 | T−3 | T−2 | T−1 | T |
|---|---|---|---|---|---|
| Peso | 9 % | 13 % | 18 % | 25 % | 36 % |

Con `weighting = "simple"` todas pesan lo mismo. Si hay menos de `min_obs` publicaciones con dato,
no hay estimación.

### Paso 4 — Azul: anclar en tu VWAP
1. **Anclas válidas**: tus VWAPs del día (con volumen ≥ `min_volume`). Con `anchor_scope = "same_kind"`
   solo anclan contratos del mismo tipo (meses a meses, trimestres a trimestres…). Con `"any"`,
   cualquiera.
2. **Spread suavizado** entre el hueco y el ancla, **contrato absoluto contra contrato absoluto**
   (por ejemplo nov-26 − oct-26). Nunca por etiqueta relativa: así un cambio de mes dentro de la
   ventana no mezcla contratos distintos.
3. **Elección del ancla** (`anchor_rule`):
   - `nearest`: la más cercana en la curva (distancia en log de días hasta la entrega). Si su spread
     no se puede calcular, se prueba la siguiente.
   - `interpolate`: la más cercana a cada lado del hueco. Se mezclan sus dos estimaciones con pesos
     inversos a la distancia, para evitar saltos entre dos anclas.

### Paso 5 — Naranja: sin anclas
Según `no_anchor`:
- `eex_ewma`: EWMA del precio EEX del hueco. Es lo descrito en la reunión.
- `eex_level`: el precio EEX de la última publicación permitida, sin suavizar.
- `missing`: se deja vacío.

### Paso 6 — Opcionales comunes con VWAPS
- `layers.arbitrage`: si aun así falta algo, se reconstruye con lo ya rellenado (Q con sus meses…).
- `shape`: el ajuste de forma posterior, idéntico al de VWAPS.

---

## 3. Ejemplo numérico

Día de referencia 30-sep, DE Base. Tienes VWAP en **M+1 (oct) = 105,00**. EEX de las últimas 5
publicaciones (números de ejemplo):

| Publicación | Oct | Nov | Nov − Oct | Peso |
|---|---|---|---|---|
| 24-sep | 100 | 110 | 10 | 8,9 % |
| 25-sep | 101 | 112 | 11 | 12,6 % |
| 28-sep | 102 | 114 | 12 | 17,8 % |
| 29-sep | 103 | 116 | 13 | 25,2 % |
| 30-sep | 104 | 118 | 14 | 35,6 % |

- `EWMA(Nov − Oct) = 12,66`, así que **M+2 (nov) = 105,00 + 12,66 = 117,66** (azul).
- Sin ningún VWAP ese día: `M+2 = EWMA(Nov) = 115,32` (naranja). Va **por detrás** del último
  precio (118): ese es el coste de suavizar el nivel. Con `no_anchor = "eex_level"` saldría 118.
- Con `offset_days = -1` la publicación del 30-sep no cuenta: ventana 24-sep … 29-sep, pesos
  13,8 / 19,5 / 27,6 / 39,1 %, spread 11,92 y M+2 = 116,92.

---

## 4. Todos los casos

| # | Caso | Resultado | `source` |
|---|---|---|---|
| 1 | Tienes VWAP del contrato | Tu VWAP | `own` |
| 2 | Hay un ancla válida y su spread se puede calcular | Azul | `eex_spread` |
| 3 | Con `interpolate`, anclas a ambos lados | Mezcla por distancia | `eex_spread` (`spread_interpolate`) |
| 4 | Ningún ancla del mismo tipo (con `same_kind`), p. ej. un trimestre sin trimestres propios | Naranja | `eex_ewma` / `eex_level` |
| 5 | Ningún VWAP propio ese día | Naranja en toda la curva | `eex_ewma` / `eex_level` |
| 6 | EEX no cotiza el contrato exacto (cascading, Seasons en DE, BOM/BOW) | Se construye por horas y se sigue igual | `eex_method = strip / residual` |
| 7 | EEX no publicó hoy (u `offset_days = -1`) | La ventana termina en la última publicación permitida; queda en el log | — |
| 8 | Menos de `min_obs` publicaciones con dato | Sin estimación (o arbitraje si está activado) | `missing` |
| 9 | VWAP con volumen < `min_volume` | Se publica tal cual, pero no ancla a otros | `own` + `anchor_excluded` |
| 10 | Producto sin EEX en el mapeo | Solo tus VWAPs (y arbitraje opcional) | `own` / `missing` |

**Lo que este método NO hace**, a diferencia de VWAPS:
- No aprende de días anteriores: no hay factor histórico, ni correlaciones, ni ayuda de otros
  productos.
- No usa ratios: solo spreads en €/MWh.
- No mezcla varias anclas, salvo la interpolación entre dos vecinas.

Por eso un `daily` y el mismo día dentro de un `refill` dan exactamente lo mismo, sin calentamiento.

---

## 5. Parámetros (`config.toml`)

| Sección | Parámetro | Defecto | Qué hace |
|---|---|---|---|
| `spread` | `window` | 5 | Publicaciones de EEX en la ventana |
| | `weighting` | ewma | `ewma` (más peso a lo reciente) o `simple` (media) |
| | `halflife` | 2.0 | Vida media de la EWMA, en publicaciones |
| | `min_obs` | 1 | Publicaciones mínimas con dato para estimar |
| | `anchor_rule` | nearest | `nearest` o `interpolate` |
| | `anchor_scope` | same_kind | `same_kind` o `any` |
| | `no_anchor` | eex_ewma | `eex_ewma`, `eex_level` o `missing` |
| `method` | `min_volume` | 0 | Volumen mínimo para que un VWAP ancle |
| | `max_anchor_dev` | 0 | Si > 0, descarta anclas más lejos que esa fracción de EEX |
| `layers` | `arbitrage` | false | Reconstruir lo que falte con lo ya rellenado |
| `eex` | `offset_days` | 0 | 0 = publicación del día; −1 = hasta ayer… |
| | `max_stale_days`, `warn_stale_days` | 0, 3 | Antigüedad máxima / aviso |
| `paths`, `targets`, `conventions`, `timezones`, `shape`, `run`, `vwap_columns` | | | **Igual que VWAPS** |

Desaparecen, respecto a VWAPS: `[layers] local/correlation/cross/hist`, `basis_mode`, `tau_log`,
`other_kind_weight`, `shrink_k`, `ewma_halflife_days`, `hist_*`, `ratio_eex_floor`,
`max_ratio_deviation`, `[eex_fallback]`, `[correlation]` y `[cross]`.

**Por producto** (Excel/CSV del mapeo): `python run.py mapping --with-parameters` añade una columna
por parámetro de modelo (`spread_window`, `spread_halflife`, `spread_weighting`, `spread_min_obs`,
`anchor_rule`, `anchor_scope`, `no_anchor`, `min_volume`, `max_anchor_dev`, `layer_arbitrage`,
`shape_*`, `tenors`). Con `[run] configuration_mode = "individual"` cada fila usa sus celdas, y una
celda vacía hereda del TOML. La columna `use` (`fill` / `helper` / `off`) enciende o apaga cada
producto, igual que en VWAPS.

**Por ejecución**, sin tocar el TOML: `--spread-window`, `--spread-weighting`, `--spread-halflife`,
`--spread-min-obs`, `--anchor-rule`, `--anchor-scope`, `--no-anchor`, `--eex-offset-days`.

---

## 6. Comandos

Los mismos que en VWAPS:

```
python run.py mapping [--with-parameters]   # crear/revisar el Excel/CSV de productos
python run.py refill --from 2026-08-10      # histórico
python run.py daily [--date 2026-10-14]     # un día
python run.py catchup                       # lo pendiente hasta hoy
python run.py status
python run.py backtest                      # leave-one-out
python run.py tune --anchor-rules nearest,interpolate --spread-windows 3,5,9
```

`backtest` oculta cada VWAP y lo predice con:
- `eex`: EEX del día, la referencia común con VWAPS.
- `eex_ewma` y `eex_level`.
- `spread_nearest` y `spread_interpolate`.
- `pipeline_configured`: la configuración desplegada, rehaciendo toda la curva.

`tune` busca en una rejilla de `spread_window`, `spread_halflife`, `spread_weighting`,
`anchor_rule`, `anchor_scope` y `no_anchor`. Elige con las fechas de calibración y valida en las
posteriores, igual que en VWAPS.

---

## 7. Salida

Mismas columnas que VWAPS para identidad, fechas, precio, `source`, `confidence`, `data_origin`,
`estimation_method`, EEX (`eex_settle`, `eex_method`, `eex_asof`, `eex_cutoff_date`) y flags. Las
columnas propias del método son:

| Columna | Qué es |
|---|---|
| `anchors` | Tenor(s) del ancla usada |
| `anchor_price` | Precio del ancla (cuando hay una sola) |
| `spread_smoothed` | Spread EEX suavizado hueco − ancla |
| `window_obs` | Publicaciones usadas en la ventana |
| `spread_trace` | JSON con cada publicación, su valor y su peso (auditable) |

Desaparecen `basis`, `basis_local`, `basis_hist`, `cross_adj`, `local_weight`, `cross_from` y
`eex_fallback_trace`, que eran de VWAPS. `source` toma los valores `own`, `eex_spread`, `eex_ewma`,
`eex_level`, `arbitrage` y `missing`.

---

## 8. Comparar con VWAPS

```
python compare_methods.py --vwap "ruta/a/tus_vwaps.xlsx"   # con tus datos reales
python compare_methods.py --synthetic                       # con datos sintéticos
```

Cada repositorio se ejecuta con **su propio código** y una copia de **su propio** `config.toml`,
con las mismas rutas y el mismo `offset_days` (0 y −1). No se modifica ninguno de los dos
repositorios. Resultado en `comparison/SUMMARY.md`:

- **Backtest emparejado**: error medio de cada método sobre exactamente las mismas observaciones
  ocultas, junto al de EEX tal cual.
- **Celdas por fuente** de cada método.
- Con datos sintéticos, **error frente a la verdad** en las celdas rellenadas por ambos.
