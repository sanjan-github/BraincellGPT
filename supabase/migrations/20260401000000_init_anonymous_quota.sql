-- Canonical schema for the production anonymous daily Groq quota system.
-- The original checked-in version used a JSONB RPC and different counters.
-- Production history was inspected before replacing that stale definition.

CREATE SCHEMA IF NOT EXISTS private;
REVOKE ALL ON SCHEMA private FROM PUBLIC, anon, authenticated;

CREATE TABLE IF NOT EXISTS private.anonymous_clients (
  client_id uuid PRIMARY KEY,
  created_at timestamptz NOT NULL DEFAULT pg_catalog.now(),
  last_seen_at timestamptz NOT NULL DEFAULT pg_catalog.now()
);

CREATE TABLE IF NOT EXISTS private.daily_usage (
  client_id uuid NOT NULL REFERENCES private.anonymous_clients(client_id),
  usage_date date NOT NULL,
  successful_messages integer NOT NULL DEFAULT 0
    CHECK (successful_messages BETWEEN 0 AND 10),
  reserved_messages integer NOT NULL DEFAULT 0
    CHECK (reserved_messages >= 0),
  created_at timestamptz NOT NULL DEFAULT pg_catalog.now(),
  updated_at timestamptz NOT NULL DEFAULT pg_catalog.now(),
  PRIMARY KEY (client_id, usage_date),
  CHECK (successful_messages + reserved_messages <= 10)
);

CREATE TABLE IF NOT EXISTS private.usage_events (
  request_id uuid PRIMARY KEY,
  client_id uuid NOT NULL REFERENCES private.anonymous_clients(client_id),
  usage_date date NOT NULL,
  provider text NOT NULL DEFAULT 'groq' CHECK (provider = 'groq'),
  status text NOT NULL CHECK (status IN ('reserved', 'succeeded', 'failed')),
  model text,
  error_code text,
  created_at timestamptz NOT NULL DEFAULT pg_catalog.now(),
  completed_at timestamptz
);

CREATE INDEX IF NOT EXISTS usage_events_client_day_idx
  ON private.usage_events (client_id, usage_date, created_at DESC);

ALTER TABLE private.anonymous_clients ENABLE ROW LEVEL SECURITY;
ALTER TABLE private.daily_usage ENABLE ROW LEVEL SECURITY;
ALTER TABLE private.usage_events ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON ALL TABLES IN SCHEMA private FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA private FROM PUBLIC, anon, authenticated, service_role;

-- The live functions return TABLE rows, which PostgREST encodes as a one-row
-- JSON array. Drop first because PostgreSQL cannot change a function's return
-- type with CREATE OR REPLACE.
DROP FUNCTION IF EXISTS public.reserve_anonymous_message(uuid, uuid);
DROP FUNCTION IF EXISTS public.finalize_anonymous_message(uuid, boolean, text, text);
DROP FUNCTION IF EXISTS public.get_anonymous_quota_status(uuid);

CREATE FUNCTION public.reserve_anonymous_message(
  p_client_id uuid,
  p_request_id uuid
)
RETURNS TABLE (
  accepted boolean,
  reservation_state text,
  usage_date date,
  successful_messages integer,
  reserved_messages integer,
  remaining_messages integer
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $function$
DECLARE
  v_today date := (pg_catalog.now() AT TIME ZONE 'UTC')::date;
  v_success integer;
  v_reserved integer;
  v_expired integer;
  v_existing_client uuid;
  v_existing_day date;
  v_existing_state text;
BEGIN
  IF p_client_id IS NULL OR p_request_id IS NULL THEN
    RAISE EXCEPTION 'client_id and request_id are required' USING ERRCODE = '22004';
  END IF;

  INSERT INTO private.anonymous_clients (client_id)
  VALUES (p_client_id)
  ON CONFLICT (client_id) DO UPDATE SET last_seen_at = pg_catalog.now();

  INSERT INTO private.daily_usage (client_id, usage_date)
  VALUES (p_client_id, v_today)
  ON CONFLICT (client_id, usage_date) DO NOTHING;

  SELECT du.successful_messages, du.reserved_messages
    INTO v_success, v_reserved
  FROM private.daily_usage AS du
  WHERE du.client_id = p_client_id AND du.usage_date = v_today
  FOR UPDATE;

  -- Recover capacity from requests whose worker died before finalization.
  UPDATE private.usage_events AS ue
  SET status = 'failed', error_code = 'reservation_expired',
      completed_at = pg_catalog.now()
  WHERE ue.client_id = p_client_id AND ue.usage_date = v_today
    AND ue.status = 'reserved'
    AND ue.created_at < pg_catalog.now() - interval '15 minutes';
  GET DIAGNOSTICS v_expired = ROW_COUNT;
  IF v_expired > 0 THEN
    UPDATE private.daily_usage AS du
    SET reserved_messages = GREATEST(0, du.reserved_messages - v_expired),
        updated_at = pg_catalog.now()
    WHERE du.client_id = p_client_id AND du.usage_date = v_today
    RETURNING du.successful_messages, du.reserved_messages
      INTO v_success, v_reserved;
  END IF;

  SELECT ue.client_id, ue.usage_date, ue.status
    INTO v_existing_client, v_existing_day, v_existing_state
  FROM private.usage_events AS ue
  WHERE ue.request_id = p_request_id;

  IF FOUND THEN
    IF v_existing_client <> p_client_id THEN
      RAISE EXCEPTION 'request_id_conflict' USING ERRCODE = '23505';
    END IF;
    IF v_existing_day <> v_today THEN
      SELECT du.successful_messages, du.reserved_messages
        INTO v_success, v_reserved
      FROM private.daily_usage AS du
      WHERE du.client_id = p_client_id AND du.usage_date = v_existing_day;
    END IF;
    RETURN QUERY SELECT false, v_existing_state, v_existing_day,
      v_success, v_reserved, GREATEST(0, 10 - v_success - v_reserved);
    RETURN;
  END IF;

  IF v_success + v_reserved >= 10 THEN
    RETURN QUERY SELECT false, 'limit_reached'::text, v_today,
      v_success, v_reserved, 0;
    RETURN;
  END IF;

  INSERT INTO private.usage_events (request_id, client_id, usage_date, provider, status)
  VALUES (p_request_id, p_client_id, v_today, 'groq', 'reserved')
  ON CONFLICT (request_id) DO NOTHING;
  IF NOT FOUND THEN
    SELECT ue.client_id, ue.usage_date, ue.status
      INTO v_existing_client, v_existing_day, v_existing_state
    FROM private.usage_events AS ue WHERE ue.request_id = p_request_id;
    IF v_existing_client <> p_client_id THEN
      RAISE EXCEPTION 'request_id_conflict' USING ERRCODE = '23505';
    END IF;
    RETURN QUERY SELECT false, v_existing_state, v_existing_day,
      v_success, v_reserved, GREATEST(0, 10 - v_success - v_reserved);
    RETURN;
  END IF;

  UPDATE private.daily_usage AS du
  SET reserved_messages = du.reserved_messages + 1,
      updated_at = pg_catalog.now()
  WHERE du.client_id = p_client_id AND du.usage_date = v_today
  RETURNING du.successful_messages, du.reserved_messages
    INTO v_success, v_reserved;

  RETURN QUERY SELECT true, 'reserved'::text, v_today,
    v_success, v_reserved, GREATEST(0, 10 - v_success - v_reserved);
END;
$function$;

CREATE FUNCTION public.finalize_anonymous_message(
  p_request_id uuid,
  p_success boolean,
  p_model text DEFAULT NULL,
  p_error_code text DEFAULT NULL
)
RETURNS TABLE (
  reservation_state text,
  usage_date date,
  successful_messages integer,
  reserved_messages integer,
  remaining_messages integer
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $function$
DECLARE
  v_client_id uuid;
  v_day date;
  v_state text;
  v_successful integer;
  v_reserved integer;
  v_new_state text;
BEGIN
  IF p_request_id IS NULL OR p_success IS NULL THEN
    RAISE EXCEPTION 'request_id and success are required' USING ERRCODE = '22004';
  END IF;

  SELECT ue.client_id, ue.usage_date INTO v_client_id, v_day
  FROM private.usage_events AS ue WHERE ue.request_id = p_request_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'reservation_not_found' USING ERRCODE = 'P0002';
  END IF;

  SELECT du.successful_messages, du.reserved_messages
    INTO v_successful, v_reserved
  FROM private.daily_usage AS du
  WHERE du.client_id = v_client_id AND du.usage_date = v_day
  FOR UPDATE;
  SELECT ue.status INTO v_state FROM private.usage_events AS ue
  WHERE ue.request_id = p_request_id FOR UPDATE;

  IF v_state = 'reserved' THEN
    v_new_state := CASE WHEN p_success THEN 'succeeded' ELSE 'failed' END;
    UPDATE private.daily_usage AS du
    SET reserved_messages = GREATEST(0, du.reserved_messages - 1),
        successful_messages = du.successful_messages + CASE WHEN p_success THEN 1 ELSE 0 END,
        updated_at = pg_catalog.now()
    WHERE du.client_id = v_client_id AND du.usage_date = v_day
    RETURNING du.successful_messages, du.reserved_messages
      INTO v_successful, v_reserved;
    UPDATE private.usage_events AS ue
    SET status = v_new_state, model = COALESCE(p_model, ue.model),
        error_code = CASE WHEN p_success THEN NULL ELSE p_error_code END,
        completed_at = pg_catalog.now()
    WHERE ue.request_id = p_request_id;
  ELSE
    v_new_state := v_state;
  END IF;

  RETURN QUERY SELECT v_new_state, v_day, v_successful, v_reserved,
    GREATEST(0, 10 - v_successful - v_reserved);
END;
$function$;

CREATE FUNCTION public.get_anonymous_quota_status(p_client_id uuid)
RETURNS TABLE (
  usage_date date,
  successful_messages integer,
  reserved_messages integer,
  remaining_messages integer
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $function$
DECLARE
  v_today date := (pg_catalog.now() AT TIME ZONE 'UTC')::date;
  v_success integer := 0;
  v_reserved integer := 0;
  v_expired integer;
BEGIN
  IF p_client_id IS NULL THEN
    RAISE EXCEPTION 'client_id is required' USING ERRCODE = '22004';
  END IF;
  SELECT du.successful_messages, du.reserved_messages
    INTO v_success, v_reserved
  FROM private.daily_usage AS du
  WHERE du.client_id = p_client_id AND du.usage_date = v_today
  FOR UPDATE;
  IF FOUND THEN
    UPDATE private.usage_events AS ue
    SET status = 'failed', error_code = 'reservation_expired',
        completed_at = pg_catalog.now()
    WHERE ue.client_id = p_client_id AND ue.usage_date = v_today
      AND ue.status = 'reserved'
      AND ue.created_at < pg_catalog.now() - interval '15 minutes';
    GET DIAGNOSTICS v_expired = ROW_COUNT;
    IF v_expired > 0 THEN
      UPDATE private.daily_usage AS du
      SET reserved_messages = GREATEST(0, du.reserved_messages - v_expired),
          updated_at = pg_catalog.now()
      WHERE du.client_id = p_client_id AND du.usage_date = v_today
      RETURNING du.successful_messages, du.reserved_messages
        INTO v_success, v_reserved;
    END IF;
  END IF;
  RETURN QUERY SELECT v_today, COALESCE(v_success, 0), COALESCE(v_reserved, 0),
    GREATEST(0, 10 - COALESCE(v_success, 0) - COALESCE(v_reserved, 0));
END;
$function$;

REVOKE ALL ON FUNCTION public.reserve_anonymous_message(uuid, uuid) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.finalize_anonymous_message(uuid, boolean, text, text) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.get_anonymous_quota_status(uuid) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.reserve_anonymous_message(uuid, uuid) TO service_role;
GRANT EXECUTE ON FUNCTION public.finalize_anonymous_message(uuid, boolean, text, text) TO service_role;
GRANT EXECUTE ON FUNCTION public.get_anonymous_quota_status(uuid) TO service_role;
