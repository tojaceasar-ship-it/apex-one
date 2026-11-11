import { describe, expect, it } from "vitest";
import { PGlite } from "@electric-sql/pglite";
import { vector } from "@electric-sql/pglite-pgvector";
import { ApexOneClient } from "../client.js";

describe("@apex-one/agent-runtime", () => {
  it("constructs default client with relative URL for Arena proxy compatibility", () => {
    const client = new ApexOneClient();
    expect(client).toBeDefined();
  });

  it("blocks localhost URLs when running in a browser-like window context", () => {
    const g = globalThis as unknown as { window?: object };
    g.window = {};
    try {
      expect(() => new ApexOneClient("http://localhost:8000")).toThrow(
        /never call localhost/
      );
      expect(() => new ApexOneClient("http://127.0.0.1:8000")).toThrow(
        /never call localhost/
      );
    } finally {
      delete g.window;
    }
  });

  it("verifies PGlite + pgvector strictly as an auxiliary JS-side SQL test helper (not replacing FastAPI PostgreSQL)", async () => {
    const db = new PGlite({ extensions: { vector } });
    await db.exec("CREATE EXTENSION IF NOT EXISTS vector;");
    await db.exec(
      "CREATE TABLE js_agent_memory (id SERIAL PRIMARY KEY, content TEXT NOT NULL, embedding vector(3) NOT NULL);"
    );
    await db.exec(
      "INSERT INTO js_agent_memory (content, embedding) VALUES ('alpha', '[1,0,0]'), ('beta', '[0,1,0]');"
    );
    const res = await db.query<{ content: string }>(
      "SELECT content FROM js_agent_memory ORDER BY embedding <=> '[1,0,0]' LIMIT 1;"
    );
    expect(res.rows[0]?.content).toBe("alpha");
    await db.close();
  });
});
