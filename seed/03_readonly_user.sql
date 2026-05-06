-- Defense-in-depth: even if the guardrail layer is bypassed, the LLM-driven
-- queries run as a user with SELECT-only privileges.

CREATE ROLE readonly_user LOGIN PASSWORD 'readonly_pw';

GRANT CONNECT ON DATABASE shop TO readonly_user;
GRANT USAGE   ON SCHEMA   public TO readonly_user;
GRANT SELECT  ON ALL TABLES IN SCHEMA public TO readonly_user;

-- Future tables created by admin should also be readable.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT ON TABLES TO readonly_user;

-- Explicitly deny everything else (Postgres has no implicit grants here, but
-- being explicit makes intent obvious in audits).
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
    ON ALL TABLES IN SCHEMA public FROM readonly_user;
