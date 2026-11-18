# Notas de diseño: cómo se construyó este pipeline y por qué
<p>
  <a href="design-notes.md"><img src="https://raw.githubusercontent.com/mauroemartinez/mauroemartinez/main/assets/lang/en.svg" alt="Read in English" height="30"></a>
  <a href="design-notes.es.md"><img src="https://raw.githubusercontent.com/mauroemartinez/mauroemartinez/main/assets/lang/es-active.svg" alt="Español" height="30"></a>
</p>

Complemento de la [guía de instalación](setup-guide.es.md). Explica las decisiones detrás de cada pieza, cómo se exploraron los datos sin exponerlos y los problemas que aparecieron en el camino, para poder reutilizar o extender el mismo enfoque.

## Objetivos y restricciones

- **Objetivo:** centralizar el historial personal de actividad y salud de una Mi Band, y poder hacerle a un LLM preguntas con fundamento sobre sueño, estrés, actividad aeróbica y peso.
- **Código público, datos privados:** el repositorio es público, mientras que cada registro real, export, digest y credencial queda en la compu o en la base del dueño.
- **Repetible:** los exports llegan de forma manual y ocasional, así que cada paso tiene que poder repetirse sobre datos que se superponen.
- **Permisos mínimos en todo:** cada componente tiene solo el acceso a la base que necesita (un usuario que solo escribe para la carga, otro que solo lee para el conector).

## 1. Fuente: el export de la cuenta de Mi Fitness

El export de datos de la cuenta de Xiaomi es la única fuente. Se pide desde el centro de privacidad de la cuenta y llega por mail como un ZIP con contraseña. Trae muchos CSV, de los que se usan solo tres.

### Archivos que se usan

| Archivo del export | Se convierte en | Notas |
|---|---|---|
| `hlth_center_fitness_data.csv` | `health.observations` | `Key` = métrica, `Time` = segundos epoch (UTC), `Value` = payload JSON |
| `hlth_center_aggregated_fitness_data.csv` | `health.daily_summaries` | `Tag` = `daily_report` (métricas), `daily_mark` (marca `has_data`, se descarta) o `daily_fitness` (objetivos). `Time` es la medianoche UTC de la fecha *local* |
| `hlth_center_sport_record.csv` | `health.activities` | Una fila por entrenamiento; `timezone` en unidades de 15 minutos |

### Archivos que se ignoran a propósito

`hlth_center_sport_track_data.csv` (rutas GPX: ubicación precisa), `user_member_profile.csv`, `user_fitness_profile.csv`, `user_device_setting.csv`, `hlth_center_data_source.csv` (identificadores de dispositivos), los registros sociales y de desafíos, y el `*_data_copy.zip` que viene adentro. `scripts/inspect_export.py` también lee solo los tres archivos de arriba.

### Explorar los datos sin mirarlos

El export se mapeó sin mostrar nunca un valor biométrico ni un identificador:

1. **Sonda de esquema:** para cada archivo, mostrar encabezados, cantidad de filas y, por cada métrica, la *forma* del payload JSON (solo nombres de campos y tipos).
2. **Sonda semántica:** comprobar hipótesis mostrando solo booleanos y conteos. Por ejemplo: ¿hay duplicados por (métrica, momento)? ¿El campo `timezone` coincide con la diferencia horaria esperada si se lee en unidades de 15 minutos? ¿Las filas agregadas están alineadas a la medianoche UTC? ¿Los minutos de cada etapa del sueño suman el total? ¿La distancia está en metros?
3. **Verificación de la salida:** después de normalizar, buscar en la salida cualquier valor identificador que estuviera en el export crudo y cualquier clave con pinta de identificador, y comparar los pasos sumados por día contra los totales diarios del propio Mi Fitness (coincidieron dentro del 2% todos los días). Eso valida de punta a punta el manejo de la zona horaria.

## 2. Normalización (`scripts/import_mifitness.py`)

- **Claves naturales:** observación = métrica + momento; resumen diario = métrica + fecha local; actividad = deporte + hora de inicio. El id del registro es un SHA-256 truncado de la fuente más la clave natural. El payload *no* forma parte del id, así que un valor que Xiaomi corrige en un export más nuevo actualiza el mismo registro en lugar de crear un duplicado.
- **Varios exports juntos:** los archivos se leen de forma recursiva y, si chocan, gana la fila con el `UpdateTime` más nuevo.
- **Limpieza de identificadores:** se descartan las columnas `Uid`, `Sid` y `Did`, y claves como `sid`, `dbKey`, `sidList`, `did`, `deviceId`, `model` y `serial` se borran en todos los niveles del payload.
- **Fechas locales:** cada registro usa su propio campo `timezone` cuando lo tiene (en unidades de 15 minutos) y, si no, `HEALTH_UTC_OFFSET`. Una noche de sueño pertenece al día en que te despertás.
- **Validación:** rangos razonables por métrica (por ejemplo, pulso 25–240 lpm, estrés 0–100, peso 20–300 kg) y momentos razonables (entre 2000 y 2100). Las filas rechazadas se cuentan por motivo en `manifest.json` y nunca se muestran.
- **Salida determinística:** los registros se ordenan, se escriben como JSONL con las claves ordenadas, y los archivos se reemplazan de forma atómica. Dos corridas con la misma entrada dan archivos idénticos byte a byte.
- **Barandas:** solo escribe dentro de `data/processed/` o `data/private/`, y no pisa una salida buena con un resultado vacío salvo que le pases `--allow-empty`.

## 3. Digest semanal (`scripts/weekly_digest.py`)

- Se arma desde el JSONL local, así que funciona sin ninguna base de datos.
- Una fila de métricas por día: primero los resúmenes diarios y, como respaldo, las observaciones (pulso en reposo, peso).
- **Que falte no es cero:** los días sin una métrica quedan afuera de los promedios.
- **Líneas base:** los promedios se comparan con los valores diarios de las N semanas anteriores. Los totales (entrenamientos, minutos) se comparan con un equivalente de 7 días calculado sobre los días de la línea base que sí tienen datos, para que un historial corto no los sesgue.
- El contexto personal para el LLM sale de `HEALTH_DIGEST_CONTEXT` en el `.env`, nunca del código.
- La salida queda siempre dentro de `data/reports/`, que Git ignora.

### Datos sintéticos de ejemplo (`scripts/make_sample_export.py`)

- Genera un export con los mismos nombres de archivo, columnas y payloads JSON que el real, incluidos identificadores de dispositivo falsos, para que se vea funcionar la limpieza.
- Es coherente por dentro: los pasos por hora suman los totales diarios, y los marcadores de intensidad coinciden con los minutos activos.
- Trae patrones para que un LLM los descubra: después de los días de entrenamiento suele haber más sueño profundo, después de los días de mucho estrés se duerme peor, el peso baja de a poco y el pulso en reposo mejora un poco.
- Es determinístico para una misma semilla. Viaja con una marca, `SYNTHETIC_SAMPLE.txt`: el importer pone `synthetic: true` en el manifest y se niega a escribir en la carpeta de salida real, la carga se niega a subirlo sin `--allow-synthetic`, y el digest muestra un aviso.

### Dashboard (`scripts/dashboard.py`)

- Un solo archivo HTML autocontenido: los datos van embebidos como JSON y los gráficos se dibujan en SVG sin librerías, así que funciona sin conexión y nunca pide nada a internet con tus datos adentro.
- Está armado con reglas explícitas de visualización: la paleta (tres colores, en modo claro y oscuro) se validó con un script para que se distinga con daltonismo, en lugar de elegirla a ojo; no hay gráficos con dos ejes (estrés y sueño van en un gráfico de dispersión, no en dos líneas con dos escalas); los trazos son finos, las líneas de grilla son finas y continuas, y cada gráfico tiene tooltip con el mouse o el teclado y una tabla con los mismos valores.
- La forma de "énfasis" para los pasos: los días con entrenamiento en el color de acento y el resto en gris, en lugar de una escala de colores por valor.
- Protección de privacidad: el HTML se tiene que escribir dentro de `data/reports/`, y una captura de datos reales no se puede guardar fuera de esa carpeta. Las imágenes del README salen de datos sintéticos.

## 4. Almacenamiento y modelo de acceso (Supabase)

Todo vive en un schema independiente, `health`. Las migraciones crean objetos solo adentro de ese schema, más tres roles, así que puede ir en un proyecto nuevo o en uno existente, y se puede exportar o borrar por separado.

| Rol | Login | Qué puede hacer | Lo usa |
|---|---|---|---|
| `health_writer` | no | SELECT / INSERT / UPDATE sobre las tablas de `health` (sin DELETE) | rol de grupo |
| `health_loader` | sí (contraseña en el `.env` local) | miembro de `health_writer` | `scripts/load_supabase.py` |
| `health_reader` | no | SELECT sobre las tablas y vistas de `health` | la Edge Function MCP, con `SET LOCAL ROLE` |
| `anon`, `authenticated` | (roles de la API) | nada: sin uso del schema, sin permisos | nadie |

- **Sin acceso por la Data API:** `health` nunca se agrega a los schemas expuestos, así que no hay camino REST/GraphQL hacia él.
- **RLS en todas las tablas,** con políticas solo para `health_writer` (todo) y `health_reader` (lectura). Los roles de la API quedarían bloqueados aunque el schema se expusiera por error.
- **Sin credenciales de superusuario en el pipeline:** no se usan ni la URL de `postgres` ni la clave service-role. Pueden leer todos los schemas, así que se tratan como secretos de nivel salud.
- **Control de proyecto equivocado:** antes de escribir, la carga comprueba que el host de la URL o el usuario del pooler (`rol.<ref>`) coincidan con `HEALTH_SUPABASE_PROJECT_REF`.
- **Errores sin valores:** los errores de base se informan solo con el código SQLSTATE, porque el detalle de Postgres puede incluir valores de las filas.
- **Upserts que no reescriben:** `ON CONFLICT (id) DO UPDATE … WHERE` la fila de origen es igual o más nueva *y* algo cambió de verdad (`IS DISTINCT FROM`). Volver a cargar datos idénticos no escribe nada.
- **Vistas:** `health.daily_metrics` y `health.weekly_metrics` son `security_invoker`, así que corren con los permisos de quien consulta. Una función auxiliar, `health.num(jsonb)`, devuelve NULL para valores JSON que no son números, así un valor raro no rompe una vista entera.

## 5. El conector MCP (`supabase/functions/health-mcp`)

**Por qué MCP:** permite que un asistente de chat (Claude en la web y en el celular) decida solo cuándo traer datos mientras conversás, sin exportar archivos ni armar un dashboard. Un servidor MCP es una API chiquita con un formato JSON-RPC estándar, donde cada "endpoint" es una *herramienta* con una descripción para el modelo y un esquema JSON de sus parámetros.

Diseño:

- **Streamable HTTP sin estado, escrito a mano:** cada POST trae un mensaje JSON-RPC (`initialize`, `ping`, `tools/list`, `tools/call`) y recibe una respuesta JSON; las notificaciones reciben `202`. A este tamaño no hace falta un SDK (son unas 250 líneas).
- **Herramientas fijas de solo lectura:** cuatro herramientas con parámetros validados (formato de fecha, rango máximo de 186 días, límite de filas), marcadas con `readOnlyHint`. No hay SQL libre. `data_coverage` existe para que el modelo conozca primero el rango de fechas disponible, y las instrucciones del servidor le piden que arranque por ahí.
- **Baja de permisos en cada llamada:** la función se conecta con la URL de base que trae el proyecto y corre cada herramienta dentro de `BEGIN READ ONLY; SET LOCAL ROLE health_reader; SET LOCAL statement_timeout = '5s'`.
- **Autenticación:** la función se publica sin verificación de JWT y solo responde en `/health-mcp/<secreto>`, comparando en tiempo constante; todo lo demás recibe un 404. Alcanza para un conector de un solo usuario. Para varios usuarios, el paso siguiente sería OAuth.
- **Errores:** los errores de validación se le explican al modelo para que corrija los parámetros; los errores de base devuelven un `query failed` genérico y no se registra nada.
- **Publicación sin CLI:** `scripts/deploy_connector.py` usa la Management API de Supabase (`database/query`, `secrets`, `functions/deploy`) con un token personal de vida corta y tres permisos, y después hace una prueba rápida del endpoint.

## 6. Problemas encontrados y cómo se resolvieron

| Problema | Solución |
|---|---|
| El CSV agregado tiene dos filas por métrica y día (`daily_report` + `daily_mark`); mezclarlas pisaba las métricas con `{has_data: true}` | Quedarse con `daily_report`, descartar `daily_mark` y darles a las otras etiquetas su propio nombre de métrica |
| El payload traía identificadores de dispositivo anidados dentro de los reportes diarios (`latest_hr.sid`, `dbKey`, `sidList`) | Borrado recursivo de claves antes de escribir cualquier cosa |
| Los ids derivados del payload duplicaban registros cuando Xiaomi corregía un valor | Ids solo desde la clave natural; gana el `UpdateTime` más nuevo |
| El campo `timezone` está en unidades de 15 minutos, no en horas ni en segundos | Se verificó con una sonda booleana contra la diferencia horaria esperada (por ejemplo, `4` ⇔ UTC+01:00) y se convierte en consecuencia |
| El patrón `*_MiFitness*` del `.gitignore` también escondía `scripts/import_mifitness.py` (Git en Windows no distingue mayúsculas) | Patrón acotado a `*_MiFitness_*` más `!scripts/**/*.py`; verificado en un repo espejo descartable antes del primer push |
| Un export vacío o todavía zipeado borraba en silencio una salida buena | Frenar si no hay registros, salvo con `--allow-empty` |
| En Windows, `datetime.fromtimestamp` falla con valores fuera de rango y corta la importación | Aceptar solo epochs entre 2000 y 2100 |
| El host directo de la base es solo IPv6 en el plan gratis de Supabase y falla desde muchas redes hogareñas | Usar la URL del Session pooler con el usuario `rol.<project-ref>` |
| `SET TRANSACTION READ ONLY` después de otras sentencias no se aplica | Abrir la transacción directamente como de solo lectura (`sql.begin("read only", …)`) |
| Desde Postgres 16, crear un rol no le permite a quien lo crea hacer `SET ROLE` a ese rol | `grant health_reader to postgres` explícito |
| Las vistas `security_invoker` fallaban con el rol lector | Dar SELECT sobre las tablas base, EXECUTE sobre `health.num` y agregar políticas RLS para el lector |
| `postgres.js` devuelve `numeric` como texto y `date` como fechas de JS | Castear en SQL a `::float8` y `::text` |
| Convertir la etiqueta de una semana ISO en fecha dentro de SQL | `to_date('2026-W46', 'IYYY-"W"IW')` devuelve el lunes de esa semana |

## 7. Registro de construcción

1. Relevar la estructura del export con sondas que no muestran datos (`inspect_export.py`).
2. Importer idempotente a JSONL privado (`import_mifitness.py`).
3. Digest semanal en Markdown a partir de los datos locales (`weekly_digest.py`).
4. Schema privado, roles y vistas (`0001_health_schema.sql`); carga con control de proyecto (`load_supabase.py`).
5. Revisiones independientes antes de publicar: una de privacidad y requisitos, otra de corrección del código y del SQL. Lo que encontraron se corrigió antes del primer push.
6. Datos cargados en Supabase como `health_loader`; se verificó que volver a cargar escribe cero filas.
7. Conector MCP de solo lectura (`0002_health_reader.sql`, `health-mcp`, `deploy_connector.py`), publicado y probado.
8. Pipeline con un solo comando (`refresh.py`) y un export de ejemplo con datos sintéticos, con protecciones para que no se mezcle con datos reales (`make_sample_export.py`).
9. Dashboard HTML autocontenido (`dashboard.py`) y la imagen de arquitectura del README.

## 8. Limitaciones conocidas

- El historial crece solo con cada export manual, así que las tendencias necesitan varias semanas de uso regular de la banda, y el peso necesita pesajes regulares.
- La banda no mide la inclinación de la cinta; `health.activities.incline_pct` y `notes` existen para cargarla a mano.
- Los puntajes de estrés y de sueño son algoritmos propietarios de Xiaomi: sirven para ver tendencias, pero no son mediciones clínicas.
- El digest se genera desde los archivos locales, no desde `health.weekly_metrics`.

## 9. Ideas a futuro

- **Sincronización automática para Android 14 en adelante.** Desde Android 14, Health Connect es parte del sistema, así que una sincronización desde el celular podría reemplazar los exports por lotes para esos usuarios, mientras el camino por export queda para Android 13 o anterior. **La captura incremental con Health Connect** reutilizaría las mismas tablas y claves naturales para que los exports y los datos incrementales se combinen sin duplicados. Health Connect no tiene un tipo de dato para el estrés, así que el estrés seguiría saliendo de los exports.
- **Datos casi en tiempo real,** para que el conector siempre responda con los datos de hoy.
- **OAuth para el conector** en lugar de un token secreto en la ruta.
- **Una herramienta de escritura para cargar datos a mano** (por ejemplo, "el entrenamiento en cinta de ayer fue al 8% de inclinación"), con su propio rol muy acotado.
