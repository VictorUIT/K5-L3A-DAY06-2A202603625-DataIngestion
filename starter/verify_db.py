import os
from dotenv import load_dotenv
from pathlib import Path
from src.store import PgVectorStore

load_dotenv(Path(__file__).resolve().with_name(".env"))
database_url = os.getenv("DATABASE_URL")
if not database_url:
    raise RuntimeError("DATABASE_URL is missing; configure it in starter/.env.")

dimensions = 384
document_id = "pg17_sql_ddl_queries"
store = PgVectorStore(database_url=database_url, dimensions=dimensions)

with store._get_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT version();")
        print(f"PostgreSQL: {cur.fetchone()[0]}")

        cur.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector';")
        extension = cur.fetchone()
        if extension is None:
            raise RuntimeError("pgvector extension is not installed in the configured database.")
        print(f"pgvector: {extension[0]}")

        cur.execute(
            "SELECT COUNT(*) FROM document_chunks WHERE document_id = %s;",
            (document_id,),
        )
        total_records = cur.fetchone()[0]
        print(f"Chunks for {document_id}: {total_records}")
        if total_records == 0:
            raise RuntimeError(f"No chunks found for active document_id {document_id}.")

        cur.execute(
            "SELECT vector_dims(embedding) FROM document_chunks WHERE document_id = %s LIMIT 1;",
            (document_id,),
        )
        stored_dimensions = cur.fetchone()[0]
        if stored_dimensions != dimensions:
            raise RuntimeError(
                f"Stored vector dimension {stored_dimensions} != expected model dimension {dimensions}."
            )
        print(f"Stored embedding dimension: {stored_dimensions}")

        cur.execute(
            """
            SELECT strategy, COUNT(*)
            FROM document_chunks
            WHERE document_id = %s
            GROUP BY strategy
            ORDER BY strategy;
            """,
            (document_id,),
        )
        for strategy, count in cur.fetchall():
            print(f"  {strategy}: {count}")