"""Transactional PostgreSQL read model for the public website."""

SITE_DOCUMENTS_SQL = """
CREATE TABLE site_documents (
    id uuid PRIMARY KEY REFERENCES articles(id) ON DELETE CASCADE,
    site text NOT NULL,
    url text NOT NULL,
    title text NOT NULL,
    subtitle text,
    author text,
    date date,
    annotation jsonb,
    category text,
    content_type text,
    fields text[] NOT NULL DEFAULT '{}',
    summary text NOT NULL DEFAULT '',
    keywords text NOT NULL DEFAULT ''
);
CREATE INDEX site_documents_date_idx ON site_documents (date DESC NULLS LAST, id);
CREATE INDEX site_documents_site_date_idx ON site_documents (site, date DESC NULLS LAST, id);
CREATE INDEX site_documents_category_idx ON site_documents (category, content_type);
CREATE INDEX site_documents_fields_idx ON site_documents USING gin (fields);
CREATE INDEX enrichments_latest_idx ON enrichments (article_id, created_at DESC, profile_id);

CREATE FUNCTION site_publication_date(value text) RETURNS date
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE result date;
BEGIN
    IF value IS NULL OR value !~ '^\\d{4}-\\d{2}-\\d{2}($|[ T])' THEN
        RETURN NULL;
    END IF;
    result := substring(value from 1 for 10)::date;
    RETURN CASE WHEN result >= DATE '1990-01-01' THEN result ELSE NULL END;
EXCEPTION WHEN datetime_field_overflow OR invalid_datetime_format THEN
    RETURN NULL;
END;
$$;

CREATE FUNCTION refresh_site_document(target uuid) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE article articles%ROWTYPE; chosen jsonb; analysis jsonb;
BEGIN
    -- Serialize concurrent annotations of one article before picking a profile.
    SELECT * INTO article FROM articles WHERE id = target FOR NO KEY UPDATE;
    IF NOT FOUND OR article.status <> 'accepted' THEN
        DELETE FROM site_documents WHERE id = target;
        RETURN;
    END IF;
    SELECT e.payload || jsonb_build_object(
        'article_id', target::text, 'url', article.url,
        'university', upper(article.site), 'profile_id', p.id,
        'provider', p.provider, 'model', p.model, 'schema_version', p.schema_version,
        'enriched_at', e.created_at, 'run_id', e.run_id
    ) INTO chosen
    FROM enrichments e JOIN enrichment_profiles p ON p.id = e.profile_id
    WHERE e.article_id = target
    ORDER BY e.created_at DESC, e.profile_id LIMIT 1;
    analysis := coalesce(chosen->'analysis', '{}'::jsonb);
    INSERT INTO site_documents
        (id, site, url, title, subtitle, author, date, annotation,
         category, content_type, fields, summary, keywords)
    VALUES (
        target, article.site, article.url,
        coalesce(nullif(article.payload->>'title', ''), 'Notícia sem título'),
        article.payload->>'subtitle', article.payload->>'author',
        site_publication_date(article.payload->>'date'), chosen,
        analysis#>>'{classification,news_category}',
        analysis#>>'{classification,content_type}',
        ARRAY(SELECT jsonb_array_elements_text(
            coalesce(analysis#>'{topics,scientific_fields}', '[]'::jsonb))),
        coalesce(analysis#>>'{summary,one_sentence}', ''),
        coalesce((SELECT string_agg(word, ' ') FROM jsonb_array_elements_text(
            coalesce(analysis#>'{topics,keywords}', '[]'::jsonb)) AS word), '')
    ) ON CONFLICT (id) DO UPDATE SET
        site = EXCLUDED.site, url = EXCLUDED.url, title = EXCLUDED.title,
        subtitle = EXCLUDED.subtitle, author = EXCLUDED.author, date = EXCLUDED.date,
        annotation = EXCLUDED.annotation, category = EXCLUDED.category,
        content_type = EXCLUDED.content_type, fields = EXCLUDED.fields,
        summary = EXCLUDED.summary, keywords = EXCLUDED.keywords;
END;
$$;

CREATE FUNCTION sync_site_document() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_TABLE_NAME = 'articles' THEN
        PERFORM refresh_site_document(NEW.id);
    ELSIF TG_OP = 'DELETE' THEN
        PERFORM refresh_site_document(OLD.article_id);
    ELSE
        PERFORM refresh_site_document(NEW.article_id);
        IF TG_OP = 'UPDATE' AND OLD.article_id <> NEW.article_id THEN
            PERFORM refresh_site_document(OLD.article_id);
        END IF;
    END IF;
    RETURN NULL;
END;
$$;
CREATE TRIGGER articles_site_document AFTER INSERT OR UPDATE ON articles
FOR EACH ROW EXECUTE FUNCTION sync_site_document();
CREATE TRIGGER enrichments_site_document AFTER INSERT OR UPDATE OR DELETE ON enrichments
FOR EACH ROW EXECUTE FUNCTION sync_site_document();

SELECT refresh_site_document(id) FROM articles WHERE status = 'accepted';
"""
