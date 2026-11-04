-- Supabase anonymous daily quota system for BraincellGPT shared Groq access
-- Enforces a maximum of 10 successful shared-Groq messages per UTC calendar day per anonymous visitor.

CREATE SCHEMA IF NOT EXISTS private;

-- 1. Anonymous visitor client tracking
CREATE TABLE IF NOT EXISTS private.anonymous_clients (
    client_id UUID PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

-- 2. Daily successful message usage per visitor per UTC date
CREATE TABLE IF NOT EXISTS private.daily_usage (
    client_id UUID NOT NULL REFERENCES private.anonymous_clients(client_id) ON DELETE CASCADE,
    usage_date DATE NOT NULL,
    messages_count INTEGER NOT NULL DEFAULT 0 CHECK (messages_count >= 0 AND messages_count <= 10),
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    PRIMARY KEY (client_id, usage_date)
);

-- 3. Individual request lifecycle tracking (reservation -> finalized / released)
CREATE TABLE IF NOT EXISTS private.usage_events (
    request_id UUID PRIMARY KEY,
    client_id UUID NOT NULL REFERENCES private.anonymous_clients(client_id) ON DELETE CASCADE,
    usage_date DATE NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('reserved', 'finalized', 'released')),
    model TEXT,
    error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    finalized_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_usage_events_client_date ON private.usage_events(client_id, usage_date);

-- Revoke direct access to private tables from anonymous and authenticated roles
REVOKE ALL ON SCHEMA private FROM PUBLIC, anon, authenticated;
REVOKE ALL ON ALL TABLES IN SCHEMA private FROM PUBLIC, anon, authenticated;

-- RPC: public.reserve_anonymous_message
-- Atomically reserves one message slot for today (UTC) if quota is available.
CREATE OR REPLACE FUNCTION public.reserve_anonymous_message(
    p_client_id UUID,
    p_request_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = private, public
AS $$
DECLARE
    v_today DATE := (timezone('utc'::text, now()))::date;
    v_existing_event RECORD;
    v_messages_count INTEGER := 0;
    v_reserved_count INTEGER := 0;
BEGIN
    IF p_client_id IS NULL OR p_request_id IS NULL THEN
        RAISE EXCEPTION 'Client ID and Request ID are required';
    END IF;

    -- Ensure client identity is registered
    INSERT INTO private.anonymous_clients (client_id, last_seen_at)
    VALUES (p_client_id, timezone('utc'::text, now()))
    ON CONFLICT (client_id) DO UPDATE SET last_seen_at = timezone('utc'::text, now());

    -- Idempotency check: request ID already seen
    SELECT * INTO v_existing_event
    FROM private.usage_events
    WHERE request_id = p_request_id;

    IF FOUND THEN
        IF v_existing_event.client_id <> p_client_id THEN
            RAISE EXCEPTION 'Request ID already exists for another client';
        END IF;

        IF v_existing_event.status = 'reserved' THEN
            SELECT COALESCE(messages_count, 0) INTO v_messages_count
            FROM private.daily_usage
            WHERE client_id = p_client_id AND usage_date = v_today;

            RETURN jsonb_build_object(
                'allowed', true,
                'messages_used', COALESCE(v_messages_count, 0),
                'message_limit', 10,
                'status', 'reserved',
                'idempotent', true
            );
        ELSIF v_existing_event.status = 'finalized' THEN
            RAISE EXCEPTION 'Request ID has already been finalized';
        ELSE
            RAISE EXCEPTION 'Request ID has already been released';
        END IF;
    END IF;

    -- Concurrency protection: lock daily usage row for this client and date
    INSERT INTO private.daily_usage (client_id, usage_date, messages_count)
    VALUES (p_client_id, v_today, 0)
    ON CONFLICT (client_id, usage_date) DO NOTHING;

    SELECT messages_count INTO v_messages_count
    FROM private.daily_usage
    WHERE client_id = p_client_id AND usage_date = v_today
    FOR UPDATE;

    -- Count active reservations currently in flight for today
    SELECT COUNT(*) INTO v_reserved_count
    FROM private.usage_events
    WHERE client_id = p_client_id AND usage_date = v_today AND status = 'reserved';

    IF (v_messages_count + v_reserved_count) >= 10 THEN
        RETURN jsonb_build_object(
            'allowed', false,
            'messages_used', v_messages_count,
            'message_limit', 10,
            'error', 'Daily quota reached'
        );
    END IF;

    -- Record the reservation
    INSERT INTO private.usage_events (request_id, client_id, usage_date, status)
    VALUES (p_request_id, p_client_id, v_today, 'reserved');

    RETURN jsonb_build_object(
        'allowed', true,
        'messages_used', v_messages_count + v_reserved_count + 1,
        'message_limit', 10,
        'status', 'reserved',
        'idempotent', false
    );
END;
$$;

-- RPC: public.finalize_anonymous_message
-- Finalizes a previously reserved request. On success, increments daily usage.
-- On failure, releases the reservation so quota is not consumed.
CREATE OR REPLACE FUNCTION public.finalize_anonymous_message(
    p_request_id UUID,
    p_success BOOLEAN,
    p_model TEXT DEFAULT NULL,
    p_error_code TEXT DEFAULT NULL
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = private, public
AS $$
DECLARE
    v_event RECORD;
    v_messages_count INTEGER := 0;
BEGIN
    IF p_request_id IS NULL THEN
        RAISE EXCEPTION 'Request ID is required';
    END IF;

    SELECT * INTO v_event
    FROM private.usage_events
    WHERE request_id = p_request_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Reservation not found for request %', p_request_id;
    END IF;

    -- If already finalized or released, return current state idempotently
    IF v_event.status <> 'reserved' THEN
        SELECT COALESCE(messages_count, 0) INTO v_messages_count
        FROM private.daily_usage
        WHERE client_id = v_event.client_id AND usage_date = v_event.usage_date;

        RETURN jsonb_build_object(
            'success', true,
            'status', v_event.status,
            'messages_used', v_messages_count,
            'message_limit', 10,
            'idempotent', true
        );
    END IF;

    IF p_success THEN
        -- Successful Groq response: increment quota
        UPDATE private.daily_usage
        SET messages_count = LEAST(10, messages_count + 1),
            updated_at = timezone('utc'::text, now())
        WHERE client_id = v_event.client_id AND usage_date = v_event.usage_date
        RETURNING messages_count INTO v_messages_count;

        UPDATE private.usage_events
        SET status = 'finalized',
            model = p_model,
            finalized_at = timezone('utc'::text, now())
        WHERE request_id = p_request_id;

        RETURN jsonb_build_object(
            'success', true,
            'status', 'finalized',
            'messages_used', v_messages_count,
            'message_limit', 10
        );
    ELSE
        -- Failed Groq response: release reservation without consuming quota
        UPDATE private.usage_events
        SET status = 'released',
            model = p_model,
            error_code = p_error_code,
            finalized_at = timezone('utc'::text, now())
        WHERE request_id = p_request_id;

        SELECT COALESCE(messages_count, 0) INTO v_messages_count
        FROM private.daily_usage
        WHERE client_id = v_event.client_id AND usage_date = v_event.usage_date;

        RETURN jsonb_build_object(
            'success', true,
            'status', 'released',
            'messages_used', v_messages_count,
            'message_limit', 10
        );
    END IF;
END;
$$;
