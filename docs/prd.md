# PRD: Spotify Liked Songs Sync

**Versión:** 1.1
**Fecha:** 2026-05-26
**Autor:** [Tu nombre]
**Estado:** Draft
**Repo:** GitHub público (TBD)

---

## 1. Contexto

Uso Spotify para descubrir música (principalmente en el carro, vía Tesla) y Apple Music para escuchar en casa, donde tengo un setup de varios Sonos. Prefiero Apple Music en casa por la calidad lossless y Dolby Atmos.

El flujo de descubrimiento funciona así: manejando, cuando una canción me gusta, le doy "Like" en Spotify y se guarda automáticamente en la lista especial de **Liked Songs**. Después en casa quiero escucharla en Apple Music vía Sonos.

El problema es que **Liked Songs no es una playlist normal** en Spotify — es una biblioteca de saved tracks que vive en otro endpoint de la API. Por eso herramientas como SongShift (que sincroniza playlists entre servicios) no la puede ver directamente.

### Solución actual (a reemplazar)

Hoy uso una cadena de tres pasos:

1. **IFTTT** copia automáticamente nuevos likes de Spotify Liked Songs → una playlist regular llamada "Liked Playlist"
2. **SongShift** (iOS) sincroniza "Liked Playlist" → playlist equivalente en Apple Music
3. Escucho en Sonos vía Apple Music

### Problemas con la solución actual

1. **IFTTT es poco confiable.** Falla con frecuencia (`failed to fetch`) sin explicación, sin alerta, sin logs útiles. Me entero cuando noto canciones faltantes días después.
2. **El orden está invertido.** Liked Songs en Spotify muestra la más reciente al principio (LIFO). IFTTT hace append al final de "Liked Playlist", entonces la playlist destino queda FIFO (más vieja primero). SongShift propaga el mismo orden a Apple Music. Resultado: al darle Play en Sonos, empiezo siempre por las canciones más viejas, no las más nuevas.

---

## 2. Objetivos

1. **Reemplazar IFTTT** con un script propio que sincronice Spotify Liked Songs → Spotify "Liked Playlist" de forma confiable y observable.
2. **Resolver el orden invertido.** La playlist destino debe reflejar el mismo orden que Liked Songs (más nueva primero).
3. **Propagar removes.** Si quito un Like en Spotify, la canción debe desaparecer también de "Liked Playlist".
4. **Notificarme cuando algo falla** (no fallar en silencio como IFTTT).
5. **Mantener la integración con SongShift** sin cambios — SongShift seguirá sincronizando "Liked Playlist" → Apple Music.

---

## 3. No objetivos (out of scope)

- **Sincronización directa a Apple Music.** Se considera para una eventual Fase 2 (requeriría cuenta Apple Developer de $99/año y matching inteligente a versiones lossless/Dolby Atmos). No es parte de este PRD.
- **Reemplazar SongShift.** Sigue siendo la herramienta de sincronización a Apple Music.
- **UI/web app.** Es un script CLI.
- **Multiusuario.** Es para uso personal, una sola cuenta de Spotify.

---

## 4. Usuario

Yo, una persona. El script corre desatendido. No hay UI.

---

## 5. Estrategia de fases

El proyecto se entrega en dos fases para reducir riesgo y aislar variables al debuggear.

### Fase 1A — Script standalone (MVP)

Script Python corriendo desde mi **Mac**, **ejecutado manualmente** mientras valido el core. Confirmo a ojo: sincronización correcta, orden bien, manejo de errores razonable. Logs a archivo local. Sin cron, sin notificaciones push, sin Home Assistant — la idea es iterar rápido sin meter complejidad de orquestación. La automatización entra en Fase 1B.

### Fase 1B — Integración Home Assistant

Migrar el script a la Raspberry Pi que hospeda HA. HA orquesta los triggers (cron + WiFi arrival) y dispara notificaciones push al iPhone vía la app Home Assistant Companion (APNs). Capa de observabilidad encima de un core ya validado.

### Fase 2 — Out of scope (futura)

Sincronización directa a Apple Music con matching a lossless/Dolby Atmos.

---

## 6. Arquitectura

### Fase 1A

```
┌──────────────────────┐
│  Spotify Liked Songs │  (saved tracks library)
│  (LIFO: nueva 1ª)    │
└──────────┬───────────┘
           │
           │  GET /me/tracks (paginated, max 50/page)
           │
           ▼
┌──────────────────────┐         ┌─────────────────────┐
│   Python script      │ ◀────── │  Ejecución manual   │
│   (en mi Mac)        │ trigger │  (yo lo corro)      │
└──────────┬───────────┘         └─────────────────────┘
           │
           │  PUT /playlists/{id} (reorder/replace via /items)
           ▼
┌──────────────────────┐
│  Spotify             │
│  "Liked Playlist"    │
└──────────┬───────────┘
           │  (sin cambios)
           ▼
   SongShift → Apple Music → Sonos
```

### Fase 1B

```
┌──────────────────────┐
│  Spotify Liked Songs │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐         ┌────────────────────────┐
│   Python script      │ ◀────── │  Home Assistant        │
│   (Raspberry Pi)     │ trigger │  (cron + WiFi arrival) │
└──────────┬───────────┘         └────────────────────────┘
           │                                  │
           ▼                                  ▼
┌──────────────────────┐         ┌────────────────────────┐
│  Spotify             │         │  iPhone                │
│  "Liked Playlist"    │         │  (push vía APNs)       │
└──────────┬───────────┘         └────────────────────────┘
           │
           ▼
   SongShift → Apple Music → Sonos
```

---

## 7. Funcionalidad

### 7.1 Sincronización core

En cada corrida, el script:

1. Lee la lista completa de Liked Songs vía `GET /me/tracks` (paginado, **máximo 50 tracks por página** — es el límite oficial de Spotify para este endpoint específico, asimétrico vs. los 100/página de playlists regulares).
2. Lee la lista actual de "Liked Playlist" vía `GET /playlists/{id}/items` (max 100/página).
3. Compara las dos listas (por track URI).
4. Si hay diferencias (adds o removes), reescribe "Liked Playlist" completa con el contenido y orden exactos de Liked Songs.

**Nota crítica sobre la API:** En febrero de 2026, Spotify hizo cambios breaking importantes — el endpoint `/playlists/{id}/tracks` se renombró a `/playlists/{id}/items`, varios endpoints batch fueron removidos, y otros campos eliminados. El script debe usar los endpoints actuales. Verificar que cualquier librería usada esté actualizada post-Feb 2026, o saltar librerías y usar HTTP directo.

**Decisión:** reescribir la playlist completa cada corrida, en lugar de insertar/borrar incrementalmente. Razones:

- 709 canciones (baseline actual) caben en ~15 requests, trivial para los rate limits.
- Idempotente: el estado final siempre refleja Liked Songs, sin riesgo de drift.
- Resuelve naturalmente el caso de removes.
- Más simple, menos puntos de falla.

### 7.2 Orden

La playlist destino debe replicar el orden de Liked Songs (más reciente primero). Spotify devuelve Liked Songs ordenado por `added_at DESC`. El script preserva ese orden al reescribir.

### 7.3 Triggers

**Fase 1A:**

- Ejecución manual (`uv run python -m sync` desde la terminal).
- No hay scheduling. Yo corro el script cuando quiero verificar que la sincronización funciona. Esto es deliberado: aísla la validación del core de cualquier complejidad de orquestación.

**Fase 1B:**

- Time pattern en HA `automation`, cada 15 min (Pi siempre prendida).
- Trigger contextual adicional: state change de `device_tracker.iphone` a `home`. Significa que acabo de llegar del carro — perfecto para tener todo listo en Sonos rápido. Latencia base 15 min, zero-latency cuando llego a casa.

### 7.4 Manejo de errores

| Error                  | Comportamiento                                                                                                     |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------ |
| `429` (rate limit)     | Leer header `Retry-After`. Skip esta corrida. Si `Retry-After > 1h`, notificar (penalty largo = algo anda mal).    |
| `5xx` (Spotify down)   | Reintentar 3 veces con backoff exponencial (1s, 4s, 16s). Si falla las 3, skip esta corrida.                       |
| `401` (token expirado) | Refresh automático del token usando el refresh token, reintentar la corrida una vez.                               |
| Otros `4xx`            | Notificar inmediato y halt — requiere intervención manual (probablemente auth roto o permisos revocados).          |
| Network / timeout      | Reintentar 2 veces, después skip.                                                                                  |
| N corridas fallidas    | Si hay 3 corridas consecutivas fallidas (~45 min sin sync exitoso), notificar tipo warning. Red de seguridad anti-"failed silently" de IFTTT. |

En Fase 1A las "notificaciones" son solo entradas en el log. En Fase 1B se convierten en push notifications.

### 7.5 Notificaciones (Fase 1B)

Push notifications al iPhone vía Home Assistant Companion app (APNs). Tres categorías, cada una toggleable en config:

| Categoría        | Default            | Mensaje ejemplo                                       |
| ---------------- | ------------------ | ----------------------------------------------------- |
| Errores fatales  | ON                 | "❌ Sync caído: auth roto. Revisa el script."         |
| Warnings         | ON                 | "⚠️ 3 corridas fallidas seguidas. Última: rate limit." |
| Confirmación add | ON inicialmente    | "🎵 +3 canciones: Title 1, Title 2, Title 3"          |

La idea con "confirmación add" es construir confianza al principio: ver que el sistema sí está agregando lo que esperaba. Una vez que confíe (~1-2 semanas), apago la categoría dejando solo errores y warnings.

**Futuro (no MVP):** notificaciones actionable con botones tipo "Reintentar ahora" o "Silenciar 24h" — HA lo soporta, lo dejamos para v2.

### 7.6 Configuración

Archivo `config.toml` con los settings programables:

```toml
[spotify]
client_id = "..."          # via env var en producción
client_secret = "..."      # via env var en producción
refresh_token = "..."      # via env var en producción
target_playlist_id = "..."

[sync]
poll_interval_minutes = 15

[notifications]
# en Fase 1A estos toggles solo afectan verbosidad de logs
errors = true
warnings = true
adds = true
consecutive_failures_threshold = 3
ha_webhook_url = "..."     # solo Fase 1B

[logging]
level = "INFO"
file = "~/.local/share/spotify-sync/sync.log"
```

Secretos (tokens, IDs sensibles) se inyectan vía variables de entorno, no se commitean.

---

## 8. Stack técnico

### Runtime

- **Python 3.12**
- **`httpx`** — cliente HTTP moderno. Sustituye a `requests`: HTTP/2 nativo, type hints reales (compatible con `mypy --strict`), API consistente sync/async, mejor mantenido.
- **Sin `spotipy`.** Decisión deliberada: escribir un thin wrapper propio sobre `httpx` (~80 líneas). Razones:
  - Control total del OAuth flow y manejo de tokens
  - Type hints limpios (spotipy es débil en este aspecto)
  - Sin riesgo de que la librería no esté actualizada para los cambios breaking de Feb 2026
  - Dependencias minimales, código más "mostrable" en repo público
  - spotipy a veces traga errores en silencio — preferimos explícito
- **`tomllib`** — parser de config (stdlib en 3.11+).
- **`tenacity`** — retries con backoff exponencial.

### Hosting

**Fase 1A:**

- Corre en mi Mac, en virtualenv propio (gestionado por `uv`).
- Ejecución manual: `uv run python -m sync`.
- Logs a archivo local.

**Fase 1B:**

- Migración a Raspberry Pi (la que ya tengo corriendo Home Assistant).
- Virtualenv propio independiente del de HA.
- Invocado por HA via `shell_command` (configurado en `configuration.yaml`).
- Triggers via HA `automation` (time pattern + state change de `device_tracker`).

### Calidad de código

| Herramienta  | Propósito                                                                                |
| ------------ | ---------------------------------------------------------------------------------------- |
| **`uv`**     | Gestor de dependencias y virtualenv. Lockfile en `uv.lock`.                              |
| **`black`**  | Formato automático (line length 100).                                                    |
| **`ruff`**   | Linter. Reglas: `E, F, I, N, UP, B, A, C4, SIM, ANN, RET, ARG, PTH`.                     |
| **`mypy`**   | Type checking en modo `strict`. Type hints en todo el código. Sin `Any` salvo justificado. |
| **`pytest`** | Tests unitarios con mocks (`pytest-httpx` para mockear el cliente HTTP).                 |
| **Cobertura mínima** | 80% (`pytest-cov`).                                                              |
| **`pip-audit`** | Auditoría de CVEs en dependencias en CI.                                              |
| **`pyproject.toml`** | Single source of truth (PEP 621): metadata, deps, configs de tooling.            |
| **`pre-commit`** | Hooks: black, ruff, mypy, pip-audit corren antes de cada commit.                     |

### CI

GitHub Actions en cada push y PR:

1. `uv sync` — instalar deps desde lockfile
2. `ruff check` — lint
3. `black --check` — formato
4. `mypy` — types
5. `pytest --cov` — tests + cobertura, falla si <80%
6. `pip-audit` — CVE scan

---

## 9. Métricas de éxito

| Métrica              | Target                                                          |
| -------------------- | --------------------------------------------------------------- |
| Confiabilidad        | ≥99% de corridas exitosas (excluyendo Spotify caído upstream)   |
| Latencia base        | ≤15 min entre Like en Spotify y aparición en "Liked Playlist"   |
| Latencia contextual (Fase 1B)  | ≤1 min cuando llego a casa (trigger de WiFi)          |
| Observabilidad       | 0 fallos en silencio. Todo error me llega como push notification (Fase 1B) o entrada en log (Fase 1A) |
| Orden                | Liked Playlist refleja el orden de Liked Songs en 100% de los casos |

---

## 10. Plan de migración

### Fase 1A

1. **Desarrollo** del script + tests + CI hasta verde en GitHub Actions.
2. **Deploy** a mi Mac: clonar repo, `uv sync`, configurar credenciales via env vars.
3. **Validación manual** (~3-7 días): correr el script a mano varias veces. Verificar a ojo que la playlist destino refleja Liked Songs en contenido y orden. Mientras tanto IFTTT sigue activo en paralelo (sin riesgo, el script reescribe completo así que cualquier diferencia se corrige sola).
4. **Apagar IFTTT** una vez validado.

### Fase 1B

5. **Migración** a Raspberry Pi: copiar script, crear virtualenv, configurar `shell_command` + `automation` en HA.
6. **Configurar notificaciones** push via HA Companion app.
7. **Periodo de confianza** (~2 semanas) con notificaciones de adds activas, después desactivarlas y dejar solo errores/warnings.

---

## 11. Riesgos y mitigaciones

| Riesgo                                                  | Mitigación                                                                                       |
| ------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| Cambios breaking en Spotify API (como Feb 2026)         | CI corre tests semanalmente como canary. Cliente HTTP propio = control total y fácil de actualizar. |
| Refresh token expira / se revoca                        | Notificación inmediata (Fase 1B) o entrada de error en log (Fase 1A).                            |
| Rate limit penalty largo (>1h)                          | Detectado por `Retry-After`; notificación inmediata.                                             |
| Raspberry Pi se cae (Fase 1B)                           | HA mismo se cae con ella, lo cual ya monitoreo independientemente.                               |
| Cambio en estructura de Liked Songs (canciones >10k)    | Paginación robusta desde día 1. No anticipo problema.                                            |
| Spotify marca la app como sospechosa                    | Polling cada 15 min está muy por debajo de cualquier umbral razonable; uso conservador.          |

---

## 12. Consideraciones futuras (no MVP)

- **Fase 2:** sincronización directa a Apple Music con matching inteligente a versiones lossless / Dolby Atmos (usando `audioVariants` de Apple Music API). Requiere cuenta Apple Developer ($99/año). Evaluable después de validar Fase 1.
- **Notificaciones actionable** ("Reintentar ahora", "Silenciar 24h").
- **Dashboard en HA** con stats: última corrida, # de tracks, errores recientes.
- **Soporte para más playlists** (no solo Liked Songs).

---

## 13. Open questions

- Nombre final del repo en GitHub. Sugerencias: `spotify-liked-sync`, `liked-songs-mirror`, `spotipie`.
- ¿Logs locales en la Pi o también a algún servicio externo (Grafana Cloud free tier)?
