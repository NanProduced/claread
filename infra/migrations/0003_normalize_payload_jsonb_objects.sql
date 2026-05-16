UPDATE user_annotations
SET payload_json = (payload_json #>> '{}')::jsonb
WHERE jsonb_typeof(payload_json) = 'string'
  AND left(payload_json #>> '{}', 1) = '{';

UPDATE favorite_records
SET payload_json = (payload_json #>> '{}')::jsonb
WHERE jsonb_typeof(payload_json) = 'string'
  AND left(payload_json #>> '{}', 1) = '{';
