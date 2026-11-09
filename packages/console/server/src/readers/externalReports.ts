/**
 * External reports: `data/external/<id>.json`, written by a person's own scripts
 * (`docs/guide/external-reports.md`). Every file is judged against the contract in
 * `packages/core/cherrypick/core/schemas/external_report.schema.json` -- the same file
 * `cherrypick.core.external_report` reads -- by a small interpreter of the subset that file uses
 * (types, const, pattern, length and size bounds, required/additionalProperties, oneOf), plus the two
 * checks a schema cannot say: the id matches the file name, and every row is as wide as its columns.
 *
 * A valid file comes back as a report; an invalid one comes back with every reason and nothing of its
 * content, so the page can never half-draw it. Read-only, no network, no module name anywhere.
 */
import fs from "node:fs";
import path from "node:path";
import type { ExternalReport, ExternalReportInvalid, ExternalReportsPayload, ExternalSection } from "@console/shared";
import { REPO_ROOT, type ConsoleConfig } from "../config.js";

/** The whole file, in bytes: past this a runaway script is refused before it is parsed. */
export const MAX_REPORT_BYTES = 512 * 1024;

export const SCHEMA_PATH = path.join(
  REPO_ROOT, "packages", "core", "cherrypick", "core", "schemas", "external_report.schema.json",
);

type Json = Record<string, unknown>;

let schemaCache: Json | null = null;
export function loadSchema(): Json {
  schemaCache ??= JSON.parse(fs.readFileSync(SCHEMA_PATH, "utf8")) as Json;
  return schemaCache;
}

const AS_OF = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})$/;

/** `value` as epoch milliseconds, or null unless it is ISO 8601 with a UTC offset. */
export function parseAsOf(value: unknown): number | null {
  if (typeof value !== "string" || !AS_OF.test(value)) return null;
  const ms = Date.parse(value);
  return Number.isNaN(ms) ? null : ms;
}

const isNumber = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const isObject = (v: unknown): v is Json => typeof v === "object" && v !== null && !Array.isArray(v);

const TYPES: Record<string, (v: unknown) => boolean> = {
  string: (v) => typeof v === "string",
  number: isNumber,
  integer: (v) => isNumber(v) && Number.isInteger(v),
  boolean: (v) => typeof v === "boolean",
  null: (v) => v === null,
  array: (v) => Array.isArray(v),
  object: isObject,
};

function kind(v: unknown): string {
  for (const name of ["null", "boolean", "string", "number", "array", "object"]) if (TYPES[name]!(v)) return name;
  return typeof v;
}

function check(value: unknown, schema: Json, at: string): string[] {
  const here = at === "" ? "report" : at;
  if ("const" in schema && value !== schema["const"]) return [`${here}: must be ${JSON.stringify(schema["const"])}`];
  if ("type" in schema) {
    const wanted = Array.isArray(schema["type"]) ? (schema["type"] as string[]) : [schema["type"] as string];
    if (!wanted.some((t) => TYPES[t]!(value))) return [`${here}: must be ${wanted.join(" or ")}, not ${kind(value)}`];
  }
  const problems: string[] = [];
  if (typeof value === "string") {
    const min = schema["minLength"] as number | undefined;
    const max = schema["maxLength"] as number | undefined;
    if (min !== undefined && value.length < min) problems.push(min === 1 ? `${here}: must not be empty` : `${here}: too short`);
    if (max !== undefined && value.length > max) problems.push(`${here}: longer than ${max} characters`);
    if (typeof schema["pattern"] === "string" && !new RegExp(schema["pattern"]).test(value)) {
      problems.push(`${here}: does not match ${schema["pattern"]}`);
    }
    if (schema["format"] === "date-time-offset" && parseAsOf(value) === null) {
      problems.push(`${here}: must be ISO 8601 with a UTC offset (for example 2026-11-09T16:30:00-05:00)`);
    }
  } else if (isNumber(value)) {
    if (typeof schema["maximum"] === "number" && value > schema["maximum"]) problems.push(`${here}: above ${schema["maximum"]}`);
    if (typeof schema["exclusiveMinimum"] === "number" && value <= schema["exclusiveMinimum"]) {
      problems.push(`${here}: must be above ${schema["exclusiveMinimum"]}`);
    }
  } else if (Array.isArray(value)) {
    const min = schema["minItems"] as number | undefined;
    const max = schema["maxItems"] as number | undefined;
    if (min !== undefined && value.length < min) problems.push(`${here}: needs at least ${min} item(s)`);
    if (max !== undefined && value.length > max) problems.push(`${here}: more than ${max} items (${value.length})`);
    if (isObject(schema["items"]) && (max === undefined || value.length <= max)) {
      value.forEach((item, i) => problems.push(...check(item, schema["items"] as Json, `${at}[${i}]`)));
    }
  } else if (isObject(value)) {
    const props = (schema["properties"] ?? {}) as Record<string, Json>;
    for (const name of (schema["required"] ?? []) as string[]) {
      if (!(name in value)) problems.push(`${here}: missing '${name}'`);
    }
    if (schema["additionalProperties"] === false) {
      for (const name of Object.keys(value)) if (!(name in props)) problems.push(`${here}: unknown field '${name}'`);
    }
    for (const [name, sub] of Object.entries(props)) {
      if (name in value) problems.push(...check(value[name], sub, at === "" ? name : `${at}.${name}`));
    }
  }
  if (Array.isArray(schema["oneOf"])) {
    const tried = (schema["oneOf"] as Json[]).map((branch) => check(value, branch, at));
    if (tried.filter((t) => t.length === 0).length !== 1) {
      const nearest = tried.reduce((a, b) => (b.length < a.length ? b : a));
      problems.push(...(nearest.length > 0 ? nearest : [`${here}: matches more than one shape`]));
    }
  }
  return problems;
}

/** Every reason `doc` is not a valid report; empty when it is. */
export function validateReport(doc: unknown, expectedId?: string): string[] {
  const problems = check(doc, loadSchema(), "");
  if (problems.length > 0 || !isObject(doc)) return problems;
  if (expectedId !== undefined && doc["id"] !== expectedId) {
    problems.push(`id: ${JSON.stringify(doc["id"])} does not match the file name (${JSON.stringify(expectedId)})`);
  }
  (doc["sections"] as ExternalSection[]).forEach((section, i) => {
    if (!("table" in section)) return;
    const width = section.table.columns.length;
    const bad = section.table.rows.findIndex((row) => row.length !== width);
    if (bad >= 0) problems.push(`sections[${i}].table.rows[${bad}]: ${section.table.rows[bad]!.length} cells for ${width} columns`);
  });
  return problems;
}

export function externalDir(config: ConsoleConfig): string {
  return config.paths.externalDir ?? path.join(config.paths.cherrypick, "data", "external");
}

/** The files in `data/external/`, valid ones as reports (stale flagged against `nowMs`), the rest as
 *  reasons. Never throws on a bad file: every failure is a reason. */
export function readExternalReports(config: ConsoleConfig, nowMs: number = Date.now()): ExternalReportsPayload {
  const dir = externalDir(config);
  let names: string[];
  try {
    names = fs.readdirSync(dir).filter((n) => n.endsWith(".json")).sort();
  } catch {
    return { reports: [], invalid: [], directoryExists: false };
  }
  const reports: ExternalReport[] = [];
  const invalid: ExternalReportInvalid[] = [];
  for (const name of names) {
    const id = name.slice(0, -".json".length);
    const reasons = judge(path.join(dir, name), id);
    if (Array.isArray(reasons)) {
      invalid.push({ id, reasons });
      continue;
    }
    const asOfMs = parseAsOf(reasons["as_of"]);
    const ageHours = asOfMs === null ? null : (nowMs - asOfMs) / 3_600_000;
    const staleAfterHours = reasons["stale_after_hours"] as number;
    reports.push({
      id,
      title: reasons["title"] as string,
      source: reasons["source"] as string,
      asOf: reasons["as_of"] as string,
      staleAfterHours,
      summary: typeof reasons["summary"] === "string" ? reasons["summary"] : null,
      sections: reasons["sections"] as ExternalSection[],
      ageHours,
      stale: ageHours !== null && ageHours > staleAfterHours,
    });
  }
  return { reports, invalid, directoryExists: true };
}

/** The parsed document when the file is valid, else the reasons it is not. */
function judge(file: string, id: string): Json | string[] {
  let size: number;
  try {
    size = fs.statSync(file).size;
  } catch (e) {
    return [`cannot read the file: ${(e as Error).message}`];
  }
  if (size > MAX_REPORT_BYTES) return [`file is ${size} bytes, over the ${MAX_REPORT_BYTES} byte limit`];
  let doc: unknown;
  try {
    doc = JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (e) {
    return [`not readable JSON: ${(e as Error).message}`];
  }
  const problems = validateReport(doc, id);
  return problems.length > 0 ? problems : (doc as Json);
}
