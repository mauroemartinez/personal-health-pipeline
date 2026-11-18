# Guía de instalación: de tu export de Mi Fitness a charlar con Claude sobre tus datos
<p>
  <a href="setup-guide.md"><img src="https://raw.githubusercontent.com/mauroemartinez/mauroemartinez/main/assets/lang/en.svg" alt="Read in English" height="30"></a>
  <a href="setup-guide.es.md"><img src="https://raw.githubusercontent.com/mauroemartinez/mauroemartinez/main/assets/lang/es-active.svg" alt="Español" height="30"></a>
</p>

Esta guía te lleva de cero al pipeline completo con **tus propios datos**. Cada etapa sirve sola, así que podés frenar donde quieras:

| Etapa | Qué obtenés | Qué necesitás |
|---|---|---|
| [1. Conseguí tus datos](#1-conseguí-tus-datos) | Tu historial de Mi Fitness en archivos CSV | Una cuenta de Xiaomi |
| [2. Prepará el repo](#2-prepará-el-repo) | Los scripts configurados para tu zona horaria | Python 3.10+, Git |
| [3. Normalizá el export](#3-normalizá-el-export) | Archivos JSONL limpios y sin duplicados | — |
| [4. Digest semanal](#4-digest-semanal) | Un resumen en Markdown para pegar en cualquier LLM | — |
| [5. Supabase](#5-cargá-los-datos-en-supabase-opcional) *(opcional)* | Tus datos en un schema de Postgres blindado | Un proyecto gratis de Supabase |
| [6. Conector de Claude](#6-charlá-con-claude-opcional) *(opcional)* | Preguntarle a Claude por tus datos desde la web o el celular | Una cuenta de Claude |

Después: [mantenerlo actualizado](#7-mantenerlo-actualizado) · [problemas comunes](#8-problemas-comunes) · [costos](#9-costos) · [desinstalar](#10-desinstalar).

Todo lo personal queda en tu compu o en tu propio proyecto de Supabase. Ningún paso de esta guía manda tus datos a este repositorio.

> **¿Todavía no tenés el export?** Cloná el repo (etapa 2) y corré `python scripts/refresh.py --sample`. Genera ocho semanas de datos sintéticos realistas en el formato de Mi Fitness, los normaliza en `data/processed/sample/` y escribe un digest en `data/reports/sample/`. Los datos sintéticos llevan una marca: el importer se niega a escribirlos en tu carpeta de salida real, y la carga se niega a subirlos salvo que le pases `--allow-synthetic` (solo para una base sin datos reales).

---

## 1. Conseguí tus datos

La fuente es el **export oficial de datos de la cuenta de Xiaomi**. Cubre los dispositivos que sincronizan con la app Mi Fitness, como las Xiaomi Smart Band y los relojes Xiaomi. Solo se soporta este formato de export.

> **¿Por qué un export y no una sincronización automática?** El export funciona con cualquier celular, incluso con Android 13 o anterior. Desde Android 14, Health Connect viene integrado en el sistema, y eso hace posible sincronizar automáticamente en segundo plano, sin pasos por lotes. Ese camino está planeado, todavía no está construido.

1. Entrá a **https://account.xiaomi.com** con la cuenta que usás en Mi Fitness.
2. Andá a **Privacy → Manage your data → Mi Fitness** y tocá **Download**.
3. Seguí los pasos para asociar un email. Los nombres de los menús cambian cada tanto; mirá el [artículo de soporte de Xiaomi](https://www.mi.com/ph/support/article/KA-11566).
4. Esperá el mail. Xiaomi avisa que puede tardar hasta unos 15 días hábiles, pero a veces llega antes. Trae un link de descarga y una **contraseña de un solo uso para el ZIP que vence a los pocos días**, así que bajalo y descomprimilo apenas llegue.

No compartas la contraseña del ZIP ni los archivos: incluyen el id de tu cuenta, ids de tus dispositivos y, en el archivo GPX, ubicaciones precisas.

---

## 2. Prepará el repo

```bash
git clone https://github.com/mauroemartinez/personal-health-pipeline.git
cd personal-health-pipeline
```

Creá tu configuración local a partir de la plantilla. Git ignora el `.env`:

```bash
cp .env.example .env          # macOS / Linux
copy .env.example .env        # Windows (cmd / PowerShell)
```

Abrí `.env` y completá por lo menos:

| Variable | Qué poner | Ejemplo |
|---|---|---|
| `HEALTH_UTC_OFFSET` | Tu diferencia horaria con UTC. Se usa para los registros que no traen su propia zona horaria. | `+01:00`, `-05:00` |
| `HEALTH_DIGEST_CONTEXT` | *(opcional)* Una línea sobre tu rutina y tus objetivos, que se suma al digest para darle contexto al LLM. | `salgo a correr a la mañana, fuerza dos veces por semana; objetivo: dormir mejor` |

Descomprimí el export de Xiaomi en **`data/private/mi_fitness_export/`**. Podés usar subcarpetas, por ejemplo una por export. Git ignora todo `data/private/`.

```text
data/private/mi_fitness_export/
└── 2026-11-export/
    ├── <fecha>_<id>_MiFitness_hlth_center_fitness_data.csv
    ├── <fecha>_<id>_MiFitness_hlth_center_aggregated_fitness_data.csv
    ├── <fecha>_<id>_MiFitness_hlth_center_sport_record.csv
    └── … (el importer ignora el resto de los archivos)
```

**Python:** las etapas 2 a 4 usan solo la librería estándar, así que no hay nada que instalar. En Windows usá `python`; en macOS/Linux, `python3` en todos los comandos de abajo.

---

## 3. Normalizá el export

Control opcional. Muestra nombres de columnas y cantidad de filas, nunca valores:

```bash
python scripts/inspect_export.py
```

Corré el importer:

```bash
python scripts/import_mifitness.py --dry-run   # lee y cuenta, no escribe nada
python scripts/import_mifitness.py             # escribe data/processed/mi_fitness/
```

Deberías ver algo así:

```text
Mi Fitness import:
  observations: 25000
  daily_summaries: 140
  activities: 15
  skipped: 0  duplicates resolved: 0
  output: data/processed/mi_fitness
```

Qué genera, todo dentro de `data/processed/mi_fitness/` (Git lo ignora):

| Archivo | Contenido |
|---|---|
| `observations.jsonl` | Mediciones puntuales: pulso, estrés, pasos, calorías, minutos de intensidad, intervalos de pie, sesiones de sueño, pulso en reposo, peso |
| `daily_summaries.jsonl` | Reportes diarios de Mi Fitness: zonas de pulso, distribución del estrés, etapas y puntaje de sueño, pasos… |
| `activities.jsonl` | Una fila por entrenamiento: duración, distancia, pulso, carga… |
| `manifest.json` | Conteos por métrica, filas descartadas por motivo, rango de fechas |

En qué podés confiar:

- **Idempotente:** si lo corrés de nuevo con los mismos archivos, la salida es idéntica byte a byte, y un export más nuevo actualiza los registros en lugar de duplicarlos.
- **Privado desde el diseño:** borra los identificadores de cuenta y de dispositivo, nunca lee las rutas GPX, los perfiles ni la configuración de los dispositivos, y por consola solo muestra conteos.
- **A prueba de errores:** si no encuentra registros (el export sigue zipeado o está en otra carpeta), frena y no toca la salida anterior.

---

## 4. Digest semanal

```bash
python scripts/weekly_digest.py                  # última semana ISO completa (lun a dom)
python scripts/weekly_digest.py --week 2026-W46  # una semana puntual
python scripts/weekly_digest.py --latest         # la semana más reciente con datos (puede estar incompleta)
python scripts/weekly_digest.py --baseline-weeks 8
```

El archivo se escribe en `data/reports/weekly/<semana ISO>.md` (Git lo ignora). Contiene:

- **Resumen contra la línea base:** promedios y totales de la semana al lado de las N semanas anteriores, con la diferencia y cuántos días tuvieron datos.
- **Peso:** el último pesaje y la diferencia con el anterior.
- **Detalle diario:** pasos, km, minutos activos, pulso en reposo, estrés y sueño de cada día.
- **Entrenamientos:** duración, distancia, ritmo, pulso, carga.
- **Sección para el LLM:** tu `HEALTH_DIGEST_CONTEXT` más preguntas sugeridas.

Pegalo en cualquier asistente (Claude, ChatGPT, Gemini…) y preguntá lo que quieras. Los días sin datos se dejan afuera de los promedios; nunca cuentan como cero. **El texto del digest está en español.** Las etiquetas están en `scripts/weekly_digest.py`, por si las querés traducir.

> Si con el digest te alcanza, podés frenar acá.

---

## 5. Cargá los datos en Supabase (opcional)

Esto pone tus datos en una base Postgres para consultarlos con SQL y desde el conector de Claude (etapa 6). Todo va a un schema independiente llamado `health`, así que podés usar un proyecto nuevo y gratis o uno que ya tengas: las migraciones nunca tocan `public` ni ningún otro schema.

### 5.1 Creá el schema

1. Creá un proyecto en **https://supabase.com** (alcanza con el plan gratis) o abrí uno existente.
2. Abrí **SQL Editor → New query**, pegá el contenido de [`supabase/migrations/0001_health_schema.sql`](../supabase/migrations/0001_health_schema.sql) y tocá **Run**. Crea las tablas, las vistas de análisis y el rol `health_writer`, activa RLS y les saca todo acceso a los roles de la API. Se puede correr más de una vez sin problema.
3. En una consulta nueva, creá el usuario que va a usar la carga. Elegí una contraseña **solo con letras y números**: los símbolos como `@ : / #` rompen las URLs de conexión.

   ```sql
   create role health_loader login password 'TuContraseñaLargaYAleatoria' in role health_writer;
   ```

4. Verificá que `health` **no** figure en **Data API → Exposed schemas** del proyecto. Por defecto no está; simplemente no lo agregues.

### 5.2 Conseguí la URL de conexión

1. En el panel de Supabase tocá **Connect** (barra de arriba) y elegí la URL del **Session pooler**. Evitá la conexión *directa*: en el plan gratis es solo IPv6 y muchas veces no conecta desde redes hogareñas.
2. Tiene esta forma:

   ```text
   postgresql://postgres.<project-ref>:[YOUR-PASSWORD]@aws-0-<region>.pooler.supabase.com:5432/postgres
   ```

3. Cambiá `postgres.<project-ref>` por `health_loader.<project-ref>`, y `[YOUR-PASSWORD]` por la contraseña del paso 5.1. Todo lo que está después del `@` queda igual.
4. Ponela en `.env`, junto con el ref de tu proyecto (el `<ref>` de `https://<ref>.supabase.co`):

   ```text
   HEALTH_SUPABASE_PROJECT_REF=<project-ref>
   HEALTH_DATABASE_URL=postgresql://health_loader.<project-ref>:TuContraseñaLargaYAleatoria@aws-0-<region>.pooler.supabase.com:5432/postgres
   ```

¿Por qué no usar directamente la URL de `postgres`? Funcionaría, pero ese usuario puede leer y borrar todo el proyecto. `health_loader` solo puede escribir en `health`, así que si se filtra el `.env`, no puede tocar nada más.

### 5.3 Cargá

```bash
python -m pip install -r requirements.txt     # instala psycopg (solo hace falta para esta etapa)
python scripts/load_supabase.py               # valida y cuenta, no escribe nada
python scripts/load_supabase.py --apply       # hace el upsert y registra la carga en health.import_runs
```

- Se puede repetir sin problema: las filas se identifican con un id determinístico, y las que no cambiaron ni se tocan.
- La carga no escribe si la URL no corresponde a `HEALTH_SUPABASE_PROJECT_REF`. Los errores de base los informa solo con el código SQLSTATE, nunca con tus valores.

Verificalo en el SQL Editor:

```sql
select * from health.weekly_metrics order by week_start desc;
select * from health.daily_metrics order by local_date desc limit 14;
```

---

## 6. Charlá con Claude (opcional)

Esto publica un **servidor MCP de solo lectura** como Edge Function de Supabase y lo conecta a Claude como *conector personalizado*. Después le podés preguntar a Claude, desde la web o la app del celular, cosas como *"¿dormí peor las noches después de entrenar en cinta?"*, y va a consultar tus datos en el momento.

Qué le das a Claude:

| Herramienta | Devuelve |
|---|---|
| `data_coverage` | Rango de fechas con datos, filas por tabla y hora de la última carga |
| `weekly_summary` | Promedios semanales: pasos, sueño, estrés, pulso, entrenamientos, último peso |
| `daily_metrics` | Una fila por día en un rango de fechas (máximo 186 días) |
| `activities` | Cada entrenamiento: duración, distancia, ritmo, pulso, carga, inclinación y notas |

Cada consulta corre como `health_reader`, un rol sin login que solo puede hacer `SELECT` sobre `health`, dentro de una transacción de solo lectura con un límite de 5 segundos. No hay SQL libre. El endpoint solo responde en una ruta con un token secreto; cualquier otra ruta devuelve 404.

### 6.1 Creá un token de acceso temporal

1. Andá a **https://supabase.com/dashboard/account/tokens → Generate new token**, con el vencimiento más corto que haya.
2. Si te deja elegir permisos, dale solo estos tres (todo lo demás en **None**):

   | Sección | Permiso | Nivel |
   |---|---|---|
   | Database | **Database** | Read-write |
   | Application services | **Edge Functions** | Read-write |
   | Application services | **Edge Function Secrets** | Read-write |

3. Copiá el token (`sbp_…`; se muestra una sola vez) en `.env`, en `SUPABASE_ACCESS_TOKEN=`.

### 6.2 Publicá el conector

Necesita la etapa 5. Después corré:

```bash
python scripts/deploy_connector.py
```

Usa la Management API de Supabase (no hace falta la CLI) para:

1. Aplicar [`0002_health_reader.sql`](../supabase/migrations/0002_health_reader.sql), que crea el rol de solo lectura.
2. Generar una sola vez un token aleatorio para la ruta y guardarlo como secreto de la función, `MCP_PATH_TOKEN`.
3. Publicar [`supabase/functions/health-mcp`](../supabase/functions/health-mcp/index.ts) sin verificación de JWT; la autenticación es el token de la ruta.
4. Hacer una prueba rápida del endpoint, incluido que un token equivocado reciba un 404.
5. Guardar la URL del conector en `data/private/connector_url.txt`.

Salida esperada:

```text
Deploying MCP connector:
  migration 0002 (health_reader): HTTP 201 ok
  secret MCP_PATH_TOKEN: HTTP 201 ok
  deploy function: HTTP 201 ok
  initialize: personal-health (protocol 2025-06-18)
  tools: data_coverage, weekly_summary, daily_metrics, activities
  data_coverage: ok
  wrong token rejected: True (HTTP 404)
Connector URL saved to data/private/connector_url.txt (keep it secret).
```

Después **revocá el token de acceso** en la misma página de Supabase y borralo del `.env`. El conector sigue funcionando; solo vas a necesitar un token nuevo para volver a publicar cambios. Correr el script de nuevo más adelante no rompe nada, y la URL del conector no cambia.

<details>
<summary>¿Preferís la CLI de Supabase? (no es el camino que se probó acá)</summary>

```bash
# 1. Corré supabase/migrations/0002_health_reader.sql en el SQL Editor
# 2. Después:
supabase login
supabase link --project-ref <project-ref>
supabase secrets set MCP_PATH_TOKEN=<un texto largo y aleatorio>
supabase functions deploy health-mcp --no-verify-jwt
# URL del conector: https://<project-ref>.supabase.co/functions/v1/health-mcp/<MCP_PATH_TOKEN>
```
</details>

### 6.3 Agregalo a Claude

1. En **claude.ai** (web), abrí **Settings / Customize → Connectors → Add custom connector**.
2. **Name:** el que quieras, por ejemplo `Mi salud`. **URL:** el contenido de `data/private/connector_url.txt`. No completes nada de OAuth ni de la configuración avanzada.
3. En un chat nuevo, abrí el menú de herramientas, verificá que el conector esté activado y preguntá algo como *"Usá Mi salud: ¿cómo vino mi sueño las últimas dos semanas y tiene relación con el estrés?"*. Permití las herramientas cuando te lo pida; son todas de solo lectura.
4. Los conectores que agregás en la web aparecen solos en las apps de Claude para el celular.

Tratá la URL del conector como una contraseña: quien la tenga puede leer tus datos.

---

## 7. Mantenerlo actualizado

Los datos están tan frescos como tu último export. Cada una o dos semanas:

1. Pedí un export nuevo (etapa 1) y descomprimilo en `data/private/mi_fitness_export/`. Podés dejar los anteriores al lado; de cada registro gana el valor más nuevo.
2. Corré todo el pipeline con un solo comando:

   ```bash
   python scripts/refresh.py
   ```

   Normaliza, carga en Supabase (solo si `HEALTH_DATABASE_URL` está configurada; `--skip-load` para saltearlo) y escribe el digest de la última semana completa (`--week 2026-W46` para otra). Frena en el primer paso que falle. Los tres comandos de base siguen funcionando por separado:

   ```bash
   python scripts/import_mifitness.py
   python scripts/load_supabase.py --apply
   python scripts/weekly_digest.py
   ```

Claude ve los datos nuevos al instante; no hace falta volver a publicar el conector.

---

## 8. Problemas comunes

| Síntoma | Causa / solución |
|---|---|
| `pip: command not found` | Usá `python -m pip install -r requirements.txt` (o `python3 -m pip`). |
| `No records found (is the export still zipped…)` | Descomprimí el export en `data/private/mi_fitness_export/`. Los nombres de archivo tienen que mantener el patrón `…_MiFitness_hlth_center_…csv`. Tu salida anterior no se tocó. |
| Días corridos en uno o entrenamientos en la fecha equivocada | Poné tu diferencia horaria real en `HEALTH_UTC_OFFSET` del `.env` y volvé a correr el importer. |
| `HEALTH_DATABASE_URL does not belong to HEALTH_SUPABASE_PROJECT_REF` | El host o el usuario del pooler (`health_loader.<ref>`) no coincide con el ref. Copiá el ref de `https://<ref>.supabase.co`. |
| `Database error during 'connect' (SQLSTATE …)` | Suele ser una de estas: contraseña equivocada (cambiala con `alter role health_loader password '…';`), usuario que no está escrito como `health_loader.<ref>`, o el host directo `db.<ref>.supabase.co` en lugar de la URL del **Session pooler**. |
| `SQLSTATE 42501` (permiso denegado) o `42P01` (la tabla no existe) | No se aplicó la migración `0001`, o `health_loader` no se creó `in role health_writer`. |
| `deploy_connector.py`: algún paso muestra `HTTP 401/403 FAILED` | El token de acceso venció, se revocó o le falta alguno de los tres permisos de la etapa 6.1. |
| El conector está agregado pero Claude no lo usa | Activalo en el menú de herramientas del chat, o nombralo en la pregunta. |
| La URL del conector devuelve 404 | La URL o el token están mal. Volvé a correr `deploy_connector.py`, que vuelve a cargar el secreto desde `data/private/mcp_path_token`. |

---

## 9. Costos

- **Etapas 1 a 4:** gratis.
- **Supabase:** el plan gratis alcanza para este uso. Como referencia, son unos 9 MB por 20 días de historial (~14 MB por mes), casi todo pulso y estrés minuto a minuto. Frente a la base de 500 MB del plan gratis (al momento de escribir esto), son años de datos. Si algún día falta espacio, cargá solo los resúmenes diarios y los entrenamientos. El conector hace unas pocas llamadas a la Edge Function por pregunta.
- **Claude:** los conectores personalizados están disponibles en los planes de Claude, incluido el gratis, que al momento de escribir esto permite uno solo. Mirá el [artículo de ayuda de Claude](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp).

---

## 10. Desinstalar

En el SQL Editor de Supabase:

```sql
drop schema if exists health cascade;
drop role if exists health_loader;
drop role if exists health_reader;
drop role if exists health_writer;
```

Después borrá la función `health-mcp` y el secreto `MCP_PATH_TOKEN` en **Edge Functions**, quitá el conector en claude.ai, y borrá `data/` y `.env` en tu compu.
