import type { SupabaseClient } from "@supabase/supabase-js";

type Row = Record<string, unknown>;
type Result = { data: unknown; error: { message: string } | null };

/** Minimal in-memory stand-in for the supabase-js calls used by lib/api/*. */
export class FakeDb {
  tables: Record<string, Row[]> = { events: [], media: [], devices: [], device_assignments: [], sites: [] };
  /** bucket -> path -> size */
  objects: Record<string, Record<string, number>> = {};
  /** bucket -> path -> bytes, for download() (sha256 verification on /complete) */
  contents: Record<string, Record<string, Uint8Array>> = {};
  signed: string[] = [];
  orgId = "org-1";
  /** conflict keys per table */
  keys: Record<string, string[]> = { events: ["id"], media: ["event_id", "filename"] };

  from(table: string) {
    return new Query(this, table);
  }

  storage = {
    from: (bucket: string) => ({
      createSignedUploadUrl: async (path: string) => {
        this.signed.push(path);
        return { data: { signedUrl: `http://storage.test/upload/${path}?token=t`, token: "t", path }, error: null };
      },
      download: async (path: string) => {
        const bytes = this.contents[bucket]?.[path];
        if (!bytes) return { data: null, error: { message: "Object not found" } };
        return { data: { arrayBuffer: async () => bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) }, error: null };
      },
      list: async (folder: string, opts?: { search?: string }) => {
        const objs = this.objects[bucket] ?? {};
        const data = Object.entries(objs)
          .filter(([p]) => p.startsWith(folder + "/") && (!opts?.search || p.includes(opts.search)))
          .map(([p, size]) => ({ name: p.slice(folder.length + 1), metadata: { size } }));
        return { data, error: null };
      },
    }),
  };

  asClient(): SupabaseClient {
    return this as unknown as SupabaseClient;
  }
}

class Query implements PromiseLike<Result> {
  private op: "select" | "insert" | "upsert" | "update" = "select";
  private payload: Row | Row[] | null = null;
  private opts: { ignoreDuplicates?: boolean } = {};
  private filters: ((r: Row) => boolean)[] = [];
  private returning = false;
  private mode: "many" | "single" | "maybe" = "many";

  constructor(
    private db: FakeDb,
    private table: string,
  ) {}

  select(_cols?: string) {
    if (this.op !== "select") this.returning = true;
    return this;
  }
  upsert(p: Row | Row[], opts: { onConflict?: string; ignoreDuplicates?: boolean } = {}) {
    this.op = "upsert";
    this.payload = p;
    this.opts = opts;
    return this;
  }
  insert(p: Row | Row[]) {
    this.op = "insert";
    this.payload = p;
    return this;
  }
  update(p: Row) {
    this.op = "update";
    this.payload = p;
    return this;
  }
  eq(col: string, v: unknown) {
    this.filters.push((r) => r[col] === v);
    return this;
  }
  in(col: string, vs: unknown[]) {
    this.filters.push((r) => vs.includes(r[col]));
    return this;
  }
  is(col: string, v: unknown) {
    this.filters.push((r) => (r[col] ?? null) === v);
    return this;
  }
  order() {
    return this;
  }
  limit() {
    return this;
  }
  single() {
    this.mode = "single";
    return this;
  }
  maybeSingle() {
    this.mode = "maybe";
    return this;
  }

  private run(): Result {
    const rows = (this.db.tables[this.table] ??= []);
    const match = (r: Row) => this.filters.every((f) => f(r));
    let out: Row[] = [];
    if (this.op === "select") {
      out = rows.filter(match);
    } else if (this.op === "update") {
      out = rows.filter(match);
      out.forEach((r) => Object.assign(r, this.payload));
    } else {
      const keys = this.db.keys[this.table] ?? ["id"];
      for (const p of Array.isArray(this.payload) ? this.payload : [this.payload as Row]) {
        const dup = rows.find((r) => keys.every((k) => r[k] === p[k]));
        if (dup) {
          if (this.op === "insert") return { data: null, error: { message: "duplicate key" } };
          if (!this.opts.ignoreDuplicates) {
            Object.assign(dup, p);
            out.push(dup);
          }
          continue;
        }
        const row: Row = { org_id: this.db.orgId, ...p };
        if (this.table === "media") row.confirmed = false;
        rows.push(row);
        out.push(row);
      }
    }
    if (this.op !== "select" && this.op !== "update" && !this.returning) return { data: null, error: null };
    if ((this.op === "update") && !this.returning) return { data: null, error: null };
    if (this.mode === "single") {
      return out.length === 1 ? { data: out[0], error: null } : { data: null, error: { message: `expected 1 row, got ${out.length}` } };
    }
    if (this.mode === "maybe") return { data: out[0] ?? null, error: null };
    return { data: out, error: null };
  }

  then<A, B>(ok?: ((v: Result) => A | PromiseLike<A>) | null, err?: ((e: unknown) => B | PromiseLike<B>) | null) {
    return Promise.resolve().then(() => this.run()).then(ok, err);
  }
}
