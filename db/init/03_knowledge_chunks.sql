-- =====================================================================
-- Phase 2: knowledge-base chunks + embeddings
-- On an existing database apply with:
--   Get-Content db\init\03_knowledge_chunks.sql -Raw | docker exec -i hospital_db psql -U hospital_app -d hospital
-- =====================================================================

-- One row per chunk. Document-level metadata (version, status, dates) is NOT copied here;
-- it is joined from `documents`, so superseding a document is a one-row update.
CREATE TABLE IF NOT EXISTS document_chunks (
    chunk_id       TEXT PRIMARY KEY,                       -- e.g. DOC012-c003
    document_id    TEXT NOT NULL REFERENCES documents(document_id) ON DELETE CASCADE,
    chunk_index    INT  NOT NULL,
    section        TEXT,                                   -- e.g. "3.1"
    section_title  TEXT,                                   -- e.g. "Times"
    heading_path   TEXT NOT NULL,                          -- "ICU Visiting Policy > 3. Visiting hours > 3.1 Times"
    page_start     INT,                                    -- NULL for DOCX (no page concept)
    page_end       INT,
    content        TEXT NOT NULL,                          -- cleaned text (what gets cited / shown to the LLM)
    token_count    INT  NOT NULL,
    embedding      vector(384) NOT NULL,                   -- BAAI/bge-small-en-v1.5
    content_tsv    tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,  -- keyword search (Phase 3)
    created_at     TIMESTAMP NOT NULL DEFAULT now(),
    UNIQUE (document_id, chunk_index)
);

-- Approximate nearest-neighbour index for cosine similarity
CREATE INDEX IF NOT EXISTS document_chunks_embedding_hnsw
    ON document_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS document_chunks_tsv ON document_chunks USING gin (content_tsv);

-- The ONLY place retrieval should read from: chunks of documents that are
-- approved AND in force today. Superseded, draft, retired, expired and
-- not-yet-effective documents are invisible here, without restarting anything.
CREATE OR REPLACE VIEW searchable_chunks AS
SELECT c.chunk_id, c.document_id, c.chunk_index, c.section, c.section_title, c.heading_path,
       c.page_start, c.page_end, c.content, c.token_count, c.embedding, c.content_tsv,
       d.document_name, d.title, d.department, d.document_type, d.lineage_id,
       d.version, d.effective_date, d.expiry_date, d.approval_status, d.authority_level
FROM document_chunks c
JOIN documents d USING (document_id)
WHERE d.approval_status = 'approved'
  AND d.effective_date <= CURRENT_DATE
  AND (d.expiry_date IS NULL OR d.expiry_date >= CURRENT_DATE)
  -- only the newest in-force version of each policy lineage
  AND NOT EXISTS (
      SELECT 1 FROM documents n
      WHERE n.lineage_id = d.lineage_id
        AND n.document_id <> d.document_id
        AND n.approval_status = 'approved'
        AND n.effective_date <= CURRENT_DATE
        AND (n.expiry_date IS NULL OR n.expiry_date >= CURRENT_DATE)
        AND (n.effective_date, string_to_array(n.version, '.')::int[])
          > (d.effective_date, string_to_array(d.version, '.')::int[])
  );
