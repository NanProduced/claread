-- 0020_reader_semantic_outline_layer.sql
--
-- T5.3a: Semantic outline bounded worker + record-level durable layer.
--
-- Extends:
--   - enhancement_layers.layer_type  with 'semantic_outline'
--   - reader_jobs.job_type            with 'build_semantic_outline'
--   - reader_runtime_spans.worker_type with 'semantic_outline'
--
-- Does not change semantics of existing types. Full CHECK lists restate the
-- union of prior allow-lists (through 0017) so this migration is safe whether
-- applied after 0014 baseline tests or a fully-migrated database.

-- ---------------------------------------------------------------------------
-- enhancement_layers.layer_type
-- ---------------------------------------------------------------------------
ALTER TABLE enhancement_layers
    DROP CONSTRAINT IF EXISTS enhancement_layers_layer_type_check;

ALTER TABLE enhancement_layers
    ADD CONSTRAINT enhancement_layers_layer_type_check CHECK (layer_type IN (
        'translation',
        'vocabulary',
        'grammar_note',
        'sentence_analysis',
        'semantic_outline'
    ));

-- ---------------------------------------------------------------------------
-- reader_jobs.job_type
-- ---------------------------------------------------------------------------
ALTER TABLE reader_jobs
    DROP CONSTRAINT IF EXISTS reader_jobs_job_type_check;

ALTER TABLE reader_jobs
    ADD CONSTRAINT reader_jobs_job_type_check CHECK (job_type IN (
        'build_base',
        'translate_unit',
        'build_vocabulary_layer',
        'build_grammar_bundle',
        'build_grammar_bundle_window',
        'input_artifact_extraction',
        'extracted_artifact_materialization',
        'article_rag_index_build',
        'generate_display_title_zh',
        'translate_article',
        'build_vocabulary_layer_article',
        'build_semantic_outline'
    ));

-- ---------------------------------------------------------------------------
-- reader_runtime_spans.worker_type
-- ---------------------------------------------------------------------------
ALTER TABLE reader_runtime_spans
    DROP CONSTRAINT IF EXISTS reader_runtime_spans_worker_type_check;

ALTER TABLE reader_runtime_spans
    ADD CONSTRAINT reader_runtime_spans_worker_type_check
    CHECK (worker_type IN (
        'display_title',
        'translation',
        'vocabulary',
        'grammar_bundle',
        'grammar_bundle_window',
        'article_rag_index',
        'artifact_extraction',
        'artifact_materialization',
        'translation_batch',
        'vocabulary_batch',
        'semantic_outline'
    ));

COMMENT ON CONSTRAINT enhancement_layers_layer_type_check ON enhancement_layers IS
    'T5.3a: adds semantic_outline as a record-scoped optional enhancement layer.';
