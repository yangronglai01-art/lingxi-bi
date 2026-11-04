-- Run with migration owner. Provision role/password outside this file using Secret.
-- The runtime role cannot mutate grants, releases, active pointer, source rows or schema.
GRANT USAGE ON SCHEMA public TO analytics_runtime;
GRANT SELECT ON analytics_schema_vectors, analytics_schema, analytics_grants, analytics_releases, analytics_active TO analytics_runtime;
GRANT SELECT, INSERT, UPDATE ON analytics_jobs TO analytics_runtime;
GRANT INSERT ON analytics_events TO analytics_runtime;
GRANT USAGE, SELECT ON SEQUENCE analytics_events_id_seq TO analytics_runtime;

GRANT SELECT, INSERT, UPDATE ON analytics_histograms TO analytics_runtime;


-- Apply after explicit schema v3 migration. Runtime cannot adjudicate feedback.
GRANT SELECT ON analytics_feedback TO analytics_runtime;
GRANT INSERT (id,tenant,subject,job,policy_version,scope,release,vote,category,comment) ON analytics_feedback TO analytics_runtime;
GRANT SELECT,INSERT,UPDATE ON analytics_stage_observations TO analytics_runtime;
