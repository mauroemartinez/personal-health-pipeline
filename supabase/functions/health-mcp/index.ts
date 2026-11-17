// Read-only MCP server (Streamable HTTP, stateless) over the private `health` schema.
//
// Lets an LLM client such as Claude (custom connector) answer questions about the
// data with a fixed set of read-only tools. No free-form SQL.
//
// Security:
//  * Reachable only at /health-mcp/<MCP_PATH_TOKEN>; any other path returns 404.
//  * Every query runs inside a transaction with `SET LOCAL ROLE health_reader`
//    (SELECT-only on schema `health`, see migration 0002) and a statement timeout.
//  * Errors are returned without database details.
//
// Deploy with scripts/deploy_connector.py (verify_jwt = false; the path token is the auth).

import postgres from "npm:postgres@3.4.5";

const FUNCTION_NAME = "health-mcp";
const SUPPORTED_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"];
const MAX_RANGE_DAYS = 186;

const sql = postgres(Deno.env.get("SUPABASE_DB_URL")!, { prepare: false, max: 1, idle_timeout: 20 });

type Json = Record<string, unknown>;

// ---------------------------------------------------------------------------
// Tools
// ---------------------------------------------------------------------------

const DATE = { type: "string", pattern: "^\\d{4}-\\d{2}-\\d{2}$", description: "Date YYYY-MM-DD (local date)" };

const TOOLS = [
  {
    name: "data_coverage",
    description:
      "Start here. Returns the first/last date with data, number of days, rows per table and when the " +
      "data was last loaded. Data comes from periodic Mi Fitness (Xiaomi Mi Band) exports, so the most " +
      "recent days may be missing.",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
  },
  {
    name: "weekly_summary",
    description:
      "Weekly averages per ISO week (most recent first): steps, distance km, active minutes, kcal, stand " +
      "hours, average HR, resting HR, stress 0-100, sleep hours (total/deep/REM), sleep score, workouts, " +
      "workout minutes and last weight (kg). days_with_data tells how complete each week is.",
    inputSchema: {
      type: "object",
      properties: {
        weeks: { type: "integer", minimum: 1, maximum: 52, default: 8, description: "How many weeks" },
        until_week: { type: "string", pattern: "^\\d{4}-W\\d{2}$", description: "Last ISO week, e.g. 2026-W46 (default: latest)" },
      },
      additionalProperties: false,
    },
  },
  {
    name: "daily_metrics",
    description:
      "One row per day in a date range (max 186 days): steps, distance_km, active_min, kcal, stand_hours, " +
      "avg_hr, rhr (resting HR), avg_stress (0-100), sleep_h, deep_h, rem_h, sleep_score, weight_kg, " +
      "workouts, workout_min. Sleep is assigned to the day you wake up. Null means no data, not zero.",
    inputSchema: {
      type: "object",
      properties: { start_date: DATE, end_date: DATE },
      required: ["start_date", "end_date"],
      additionalProperties: false,
    },
  },
  {
    name: "activities",
    description:
      "Workouts in a date range (max 186 days): sport (outdoor_walking, indoor_running, strength_training…), " +
      "start time (UTC), duration, distance, pace, avg/max HR, training load, aerobic training effect, " +
      "VO2max estimate, and manual incline_pct / notes when present.",
    inputSchema: {
      type: "object",
      properties: { start_date: DATE, end_date: DATE },
      required: ["start_date", "end_date"],
      additionalProperties: false,
    },
  },
].map((t) => ({ ...t, annotations: { readOnlyHint: true, openWorldHint: false } }));

class ToolError extends Error {}

function dateArg(args: Json, key: string): string {
  const v = args[key];
  if (typeof v !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(v) || isNaN(Date.parse(v))) {
    throw new ToolError(`${key} must be a date YYYY-MM-DD`);
  }
  return v;
}

function rangeArgs(args: Json): [string, string] {
  const start = dateArg(args, "start_date");
  const end = dateArg(args, "end_date");
  const days = (Date.parse(end) - Date.parse(start)) / 86_400_000;
  if (days < 0) throw new ToolError("end_date must be on or after start_date");
  if (days > MAX_RANGE_DAYS) throw new ToolError(`range too long (max ${MAX_RANGE_DAYS} days)`);
  return [start, end];
}

async function readOnly<T>(fn: (tx: postgres.TransactionSql) => Promise<T>): Promise<T> {
  return await sql.begin("read only", async (tx) => {
    await tx`set local role health_reader`;
    await tx`set local statement_timeout = '5s'`;
    return await fn(tx);
  }) as T;
}

async function runTool(name: string, args: Json): Promise<unknown> {
  switch (name) {
    case "data_coverage":
      return await readOnly(async (tx) => {
        const [range] = await tx`
          select min(local_date)::text as first_date, max(local_date)::text as last_date,
                 count(*)::int as days_with_data
          from health.daily_metrics`;
        const [counts] = await tx`
          select (select count(*) from health.observations)::int    as observations,
                 (select count(*) from health.daily_summaries)::int as daily_summaries,
                 (select count(*) from health.activities)::int      as activities,
                 (select max(started_at) from health.import_runs)   as last_loaded_at`;
        return { ...range, rows: counts };
      });

    case "weekly_summary": {
      const weeks = Number.isInteger(args.weeks) ? Math.min(Math.max(args.weeks as number, 1), 52) : 8;
      const until = args.until_week;
      if (until !== undefined && (typeof until !== "string" || !/^\d{4}-W\d{2}$/.test(until))) {
        throw new ToolError("until_week must look like 2026-W46");
      }
      return await readOnly((tx) => tx`
        select iso_week, week_start::text, days_with_data::int,
               avg_steps::float8, avg_distance_km::float8, avg_active_min::float8, avg_kcal::float8,
               avg_stand_hours::float8, avg_hr::float8, avg_rhr::float8, avg_stress::float8,
               avg_sleep_h::float8, avg_deep_h::float8, avg_rem_h::float8, avg_sleep_score::float8,
               workouts::int, workout_min::float8, last_weight_kg::float8
        from health.weekly_metrics
        where ${until ?? null}::text is null
           or week_start <= to_date(${until ?? null}::text, 'IYYY-"W"IW')
        order by week_start desc
        limit ${weeks}`);
    }

    case "daily_metrics": {
      const [start, end] = rangeArgs(args);
      return await readOnly((tx) => tx`
        select local_date::text,
               steps::float8, round(distance_km, 2)::float8 as distance_km, active_min::float8,
               kcal::float8, stand_hours::float8, avg_hr::float8, rhr::float8, avg_stress::float8,
               round(sleep_h, 2)::float8 as sleep_h, round(deep_h, 2)::float8 as deep_h,
               round(rem_h, 2)::float8 as rem_h, sleep_score::float8, weight_kg::float8,
               workouts::int, round(workout_min)::float8 as workout_min
        from health.daily_metrics
        where local_date between ${start}::date and ${end}::date
        order by local_date`);
    }

    case "activities": {
      const [start, end] = rangeArgs(args);
      return await readOnly((tx) => tx`
        select local_date::text, sport, started_at,
               round(duration_s / 60.0, 1)::float8 as duration_min,
               round(distance_m / 1000.0, 2)::float8 as distance_km,
               case when distance_m >= 100 and duration_s > 0
                    then round(duration_s / (distance_m / 1000.0) / 60.0, 2)::float8 end as pace_min_per_km,
               avg_hr::float8, max_hr::float8, calories_kcal::float8, steps::float8,
               train_load::float8, train_effect::float8, vo2_max::float8, incline_pct::float8, notes
        from health.activities
        where local_date between ${start}::date and ${end}::date
        order by started_at`);
    }

    default:
      throw new ToolError(`unknown tool: ${name}`);
  }
}

// ---------------------------------------------------------------------------
// MCP JSON-RPC over Streamable HTTP (stateless, JSON responses)
// ---------------------------------------------------------------------------

function ok(id: unknown, result: unknown) {
  return { jsonrpc: "2.0", id, result };
}

function fail(id: unknown, code: number, message: string) {
  return { jsonrpc: "2.0", id: id ?? null, error: { code, message } };
}

async function handle(msg: Json): Promise<Json | null> {
  const { id, method } = msg;
  const params = (msg.params ?? {}) as Json;
  if (id === undefined) return null; // notification (e.g. notifications/initialized)

  switch (method) {
    case "initialize": {
      const requested = params.protocolVersion as string;
      return ok(id, {
        protocolVersion: SUPPORTED_VERSIONS.includes(requested) ? requested : SUPPORTED_VERSIONS[0],
        capabilities: { tools: { listChanged: false } },
        serverInfo: { name: "personal-health", version: "1.0.0" },
        instructions:
          "Personal health data from a Xiaomi Mi Band (Mi Fitness exports): sleep, stress, heart rate, " +
          "steps, workouts and weight. Call data_coverage first to learn the available date range. " +
          "Values are wearable estimates, not clinical measurements. Null means no data.",
      });
    }
    case "ping":
      return ok(id, {});
    case "tools/list":
      return ok(id, { tools: TOOLS });
    case "tools/call": {
      const name = params.name as string;
      const args = (params.arguments ?? {}) as Json;
      try {
        const data = await runTool(name, args);
        return ok(id, { content: [{ type: "text", text: JSON.stringify(data) }] });
      } catch (e) {
        const message = e instanceof ToolError ? e.message : "query failed";
        if (!(e instanceof ToolError)) console.error(`tool ${name} failed`); // no details logged
        return ok(id, { content: [{ type: "text", text: message }], isError: true });
      }
    }
    default:
      return fail(id, -32601, `method not found: ${String(method)}`);
  }
}

function safeEqual(a: string, b: string): boolean {
  if (!a || a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

Deno.serve(async (req) => {
  const parts = new URL(req.url).pathname.split("/").filter(Boolean);
  const i = parts.indexOf(FUNCTION_NAME);
  const token = i >= 0 ? parts[i + 1] ?? "" : "";
  const expected = Deno.env.get("MCP_PATH_TOKEN") ?? "";
  if (!expected || !safeEqual(token, expected) || parts.length > i + 2) {
    return new Response("Not found", { status: 404 });
  }
  if (req.method !== "POST") {
    return new Response("Method not allowed", { status: 405, headers: { Allow: "POST" } });
  }

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return Response.json(fail(null, -32700, "parse error"), { status: 400 });
  }

  const messages = Array.isArray(body) ? body : [body];
  const replies = (await Promise.all(messages.map((m) => handle(m as Json)))).filter((r) => r !== null);
  if (replies.length === 0) return new Response(null, { status: 202 });
  return Response.json(Array.isArray(body) ? replies : replies[0]);
});
