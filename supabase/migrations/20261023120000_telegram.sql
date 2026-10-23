-- Phase 15: private Telegram bot and personal alerts.
--   tg_updates    webhook update ids already handled (Telegram retries a webhook it thinks failed: handle once)
--   tg_chats      private chats of allowlisted users (the allowlist itself is TG_ALLOWED_USERS, never stored here)
--   tg_watches    per-chat watchlists, with the radar state last seen for each watched token
--   tg_snapshots  the latest alert snapshot of each watched token (crawler/alerts.snapshot): diffed by the next scan
--   tg_alerts     the alert queue: dedupe key per chat, priority, cooldown, claim / retry / sent / dropped
--   tg_state      small key/value state (last cron run summary)
-- Everything is service_role only (RLS on, no policies, execute revoked from anon / authenticated).

create table public.tg_updates (
  update_id    bigint      primary key,
  received_at  timestamptz not null default now()
);

create table public.tg_chats (
  chat_id        bigint      primary key,
  user_id        bigint      not null,
  username       text,
  global_alerts  boolean     not null default true,
  first_seen_at  timestamptz not null default now(),
  last_seen_at   timestamptz not null default now()
);

create table public.tg_watches (
  chat_id       bigint      not null references public.tg_chats (chat_id) on delete cascade,
  chain         text        not null,
  token         text        not null,
  ticker        text,
  created_at    timestamptz not null default now(),
  checked_at    timestamptz,
  radar_active  boolean     not null default false,
  primary key (chat_id, chain, token)
);
create index tg_watches_token on public.tg_watches (chain, token);

create table public.tg_snapshots (
  chain        text        not null,
  token        text        not null,
  snapshot     jsonb       not null,
  observed_at  timestamptz not null default now(),
  primary key (chain, token)
);

create table public.tg_alerts (
  id          bigint      generated always as identity primary key,
  chat_id     bigint      not null references public.tg_chats (chat_id) on delete cascade,
  chain       text,
  token       text,
  kind        text        not null,
  priority    smallint    not null,                 -- 0 = most urgent
  dedupe_key  text        not null,
  body        jsonb       not null,                 -- {"line": ..., "snapshot": ..., "ticker": ...}
  status      text        not null default 'queued'
                          check (status in ('queued', 'sending', 'sent', 'failed', 'dropped')),
  attempts    integer     not null default 0,
  error       text,
  created_at  timestamptz not null default now(),
  not_before  timestamptz not null default now(),
  claimed_at  timestamptz,
  sent_at     timestamptz,
  unique (chat_id, dedupe_key)
);
create index tg_alerts_queue on public.tg_alerts (priority, created_at) where status = 'queued';
create index tg_alerts_recent on public.tg_alerts (chat_id, token, kind, created_at desc);
create index tg_alerts_sent on public.tg_alerts (chat_id, sent_at) where status = 'sent';

create table public.tg_state (
  key         text        primary key,
  value       jsonb       not null,
  updated_at  timestamptz not null default now()
);

alter table public.tg_updates   enable row level security;
alter table public.tg_chats     enable row level security;
alter table public.tg_watches   enable row level security;
alter table public.tg_snapshots enable row level security;
alter table public.tg_alerts    enable row level security;
alter table public.tg_state     enable row level security;
-- explicit: only the server's service role touches these tables (Supabase grants it by default; stated, not assumed)
grant select, insert, update, delete on public.tg_updates, public.tg_chats, public.tg_watches, public.tg_snapshots,
  public.tg_alerts, public.tg_state to service_role;
-- and nobody else, not even through the API's anon / authenticated roles (RLS would refuse every row anyway)
revoke all on public.tg_updates, public.tg_chats, public.tg_watches, public.tg_snapshots, public.tg_alerts,
  public.tg_state from anon, authenticated;

-- ---------------------------------------------------------------------------------------------------------------
-- webhook idempotency: true the first time an update id is seen
-- ---------------------------------------------------------------------------------------------------------------
create function public.tg_update_seen(p_update_id bigint) returns boolean
language plpgsql
security invoker
set search_path = ''
as $$
begin
  insert into public.tg_updates (update_id) values (p_update_id) on conflict do nothing;
  return found;
end;
$$;

create function public.tg_chat_touch(p_chat_id bigint, p_user_id bigint, p_username text) returns void
language sql
security invoker
set search_path = ''
as $$
insert into public.tg_chats (chat_id, user_id, username) values (p_chat_id, p_user_id, p_username)
on conflict (chat_id) do update set user_id = excluded.user_id, username = excluded.username, last_seen_at = now();
$$;

-- ---------------------------------------------------------------------------------------------------------------
-- watchlists
-- ---------------------------------------------------------------------------------------------------------------
create function public.tg_watch(p_chat_id bigint, p_chain text, p_token text, p_ticker text, p_limit integer)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
  n integer;
begin
  perform 1 from public.tg_chats where chat_id = p_chat_id for update;   -- serialise one chat's watch / unwatch
  if exists (select 1 from public.tg_watches where chat_id = p_chat_id and chain = p_chain and token = p_token) then
    update public.tg_watches set ticker = coalesce(p_ticker, ticker)
    where chat_id = p_chat_id and chain = p_chain and token = p_token;
    select count(*) into n from public.tg_watches where chat_id = p_chat_id;
    return jsonb_build_object('status', 'exists', 'count', n, 'limit', p_limit);
  end if;
  select count(*) into n from public.tg_watches where chat_id = p_chat_id;
  if n >= p_limit then
    return jsonb_build_object('status', 'limit', 'count', n, 'limit', p_limit);
  end if;
  insert into public.tg_watches (chat_id, chain, token, ticker) values (p_chat_id, p_chain, p_token, p_ticker);
  return jsonb_build_object('status', 'added', 'count', n + 1, 'limit', p_limit);
end;
$$;

create function public.tg_unwatch(p_chat_id bigint, p_token text, p_chain text default null) returns integer
language sql
security invoker
set search_path = ''
as $$
with d as (
  delete from public.tg_watches
  where chat_id = p_chat_id and token = p_token and (p_chain is null or chain = p_chain)
  returning 1
)
select count(*)::integer from d;
$$;

create function public.tg_watchlist(p_chat_id bigint) returns jsonb
language sql
stable
security invoker
set search_path = ''
as $$
select coalesce(jsonb_agg(jsonb_build_object(
  'chain', w.chain, 'token', w.token, 'ticker', coalesce(w.ticker, k.ticker), 'created_at', w.created_at,
  'checked_at', w.checked_at, 'radar_active', w.radar_active,
  'band', s.snapshot->>'band', 'score', s.snapshot->'score', 'rug', s.snapshot->'rug', 'observed_at', s.observed_at)
  order by w.created_at), '[]'::jsonb)
from public.tg_watches w
left join public.tokens k on k.chain = w.chain and k.token = w.token
left join public.tg_snapshots s on s.chain = w.chain and s.token = w.token
where w.chat_id = p_chat_id;
$$;

-- watched tokens due for a recheck: never checked first, then the stalest
create function public.tg_watch_due(p_min_age_min integer, p_limit integer) returns jsonb
language sql
stable
security invoker
set search_path = ''
as $$
select coalesce(jsonb_agg(jsonb_build_object('chain', t.chain, 'token', t.token) order by t.checked nulls first),
                '[]'::jsonb)
from (
  select chain, token, max(checked_at) as checked
  from public.tg_watches
  group by chain, token
  having max(checked_at) is null or max(checked_at) < now() - make_interval(mins => greatest(1, p_min_age_min))
  order by max(checked_at) nulls first
  limit greatest(1, least(p_limit, 200))
) t;
$$;

create function public.tg_watch_checked(p_chain text, p_token text) returns void
language sql
security invoker
set search_path = ''
as $$
update public.tg_watches set checked_at = now() where chain = p_chain and token = p_token;
$$;

-- every watched token with its chats: the radar eligibility pass
create function public.tg_watched() returns jsonb
language sql
stable
security invoker
set search_path = ''
as $$
select coalesce(jsonb_agg(jsonb_build_object('chat_id', chat_id, 'chain', chain, 'token', token, 'ticker', ticker,
                                             'radar_active', radar_active)), '[]'::jsonb)
from public.tg_watches;
$$;

-- radar state per watch: p_rows [{chat_id, chain, token, active}]; returns how many changed
create function public.tg_radar_set(p jsonb) returns integer
language sql
security invoker
set search_path = ''
as $$
with r as (
  select (x->>'chat_id')::bigint as chat_id, x->>'chain' as chain, x->>'token' as token, (x->>'active')::boolean as active
  from jsonb_array_elements(p) x
), u as (
  update public.tg_watches w set radar_active = r.active
  from r where w.chat_id = r.chat_id and w.chain = r.chain and w.token = r.token and w.radar_active <> r.active
  returning 1
)
select count(*)::integer from u;
$$;

-- ---------------------------------------------------------------------------------------------------------------
-- snapshots: swap in the new one, return the previous and the chats watching the token. Tokens nobody watches
-- are not stored (the table stays the size of the watchlists)
-- ---------------------------------------------------------------------------------------------------------------
create function public.tg_snapshot_swap(p_chain text, p_token text, p_snapshot jsonb) returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
  chats jsonb;
  prev  jsonb;
begin
  select coalesce(jsonb_agg(distinct chat_id), '[]'::jsonb) into chats
  from public.tg_watches where chain = p_chain and token = p_token;
  if chats = '[]'::jsonb then
    return jsonb_build_object('watched', false, 'prev', null, 'chats', chats);
  end if;
  select snapshot into prev from public.tg_snapshots where chain = p_chain and token = p_token for update;
  insert into public.tg_snapshots (chain, token, snapshot) values (p_chain, p_token, p_snapshot)
  on conflict (chain, token) do update set snapshot = excluded.snapshot, observed_at = now();
  return jsonb_build_object('watched', true, 'prev', prev, 'chats', chats);
end;
$$;

-- early buyers' share measured after the scan (/api/early, the cron): written into the latest snapshot
create function public.tg_snapshot_early(p_chain text, p_token text, p_share double precision) returns boolean
language sql
security invoker
set search_path = ''
as $$
with u as (
  update public.tg_snapshots set snapshot = jsonb_set(snapshot, '{early_share}', to_jsonb(p_share))
  where chain = p_chain and token = p_token
  returning 1
)
select exists (select 1 from u);
$$;

-- chats that receive global alerts (suspicious deployer, smart crew)
create function public.tg_global_chats() returns jsonb
language sql
stable
security invoker
set search_path = ''
as $$
select coalesce(jsonb_agg(chat_id), '[]'::jsonb) from public.tg_chats where global_alerts;
$$;

-- the best market outcome label of each token's scans (smart crew: tokens that went up)
create function public.tg_token_labels(p_chain text, p_tokens text[]) returns jsonb
language sql
stable
security invoker
set search_path = ''
as $$
select coalesce(jsonb_object_agg(token, labels), '{}'::jsonb)
from (
  select o.token, jsonb_agg(distinct o.label) as labels
  from public.outcomes o
  where o.chain = p_chain and o.token = any (p_tokens) and o.measured_at is not null and o.label is not null
  group by o.token
) t;
$$;

-- ---------------------------------------------------------------------------------------------------------------
-- alert queue
-- ---------------------------------------------------------------------------------------------------------------
-- p: [{chat_id, chain, token, kind, priority, dedupe_key, cooldown_s, body}]. A row is skipped when the chat already
-- has that dedupe key, or an alert of the same kind for the same token that was queued or sent within cooldown_s.
create function public.tg_alert_enqueue(p jsonb) returns integer
language sql
security invoker
set search_path = ''
as $$
with r as (
  select (x->>'chat_id')::bigint as chat_id, x->>'chain' as chain, x->>'token' as token, x->>'kind' as kind,
         (x->>'priority')::smallint as priority, x->>'dedupe_key' as dedupe_key,
         greatest(0, coalesce((x->>'cooldown_s')::integer, 0)) as cooldown_s, x->'body' as body
  from jsonb_array_elements(p) x
), ins as (
  insert into public.tg_alerts (chat_id, chain, token, kind, priority, dedupe_key, body)
  select r.chat_id, r.chain, r.token, r.kind, r.priority, r.dedupe_key, r.body
  from r
  join public.tg_chats c on c.chat_id = r.chat_id
  where not exists (
    select 1 from public.tg_alerts a
    where a.chat_id = r.chat_id and a.kind = r.kind and a.token is not distinct from r.token
      and a.status in ('queued', 'sending', 'sent') and a.created_at > now() - make_interval(secs => r.cooldown_s))
  on conflict (chat_id, dedupe_key) do nothing
  returning 1
)
select count(*)::integer from ins;
$$;

-- claim up to p_limit due alerts, most urgent first, skipping chats that already got p_hourly_cap alerts in the
-- last hour; concurrent dispatchers never claim the same row (skip locked). Claims older than 5 minutes (a dispatcher
-- that died mid-send) return to the queue; queued alerts older than p_max_age_h are dropped.
create function public.tg_alert_claim(p_limit integer, p_hourly_cap integer, p_max_age_h integer default 24)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
  out jsonb;
begin
  update public.tg_alerts set status = 'queued', claimed_at = null
  where status = 'sending' and claimed_at < now() - interval '5 minutes';
  update public.tg_alerts set status = 'dropped', error = 'expired'
  where status = 'queued' and created_at < now() - make_interval(hours => greatest(1, p_max_age_h));
  with sent as (
    select chat_id, count(*) as n from public.tg_alerts
    where (status = 'sent' and sent_at > now() - interval '1 hour') or status = 'sending'
    group by chat_id
  ), ranked as (   -- the cap counts this batch too: a chat's k-th due alert fits when sent + k <= cap
    select a.id, a.priority, a.created_at,
           coalesce(s.n, 0) + row_number() over (partition by a.chat_id order by a.priority, a.created_at) as k
    from public.tg_alerts a left join sent s on s.chat_id = a.chat_id
    where a.status = 'queued' and a.not_before <= now()
  ), pick as (
    select a.id from public.tg_alerts a
    where a.id in (select id from ranked where k <= p_hourly_cap) and a.status = 'queued'
    order by a.priority, a.created_at
    limit greatest(1, least(p_limit, 500))
    for update skip locked
  ), upd as (
    update public.tg_alerts a set status = 'sending', claimed_at = now(), attempts = a.attempts + 1
    from pick where a.id = pick.id
    returning a.id, a.chat_id, a.chain, a.token, a.kind, a.priority, a.body, a.attempts, a.created_at
  )
  select coalesce(jsonb_agg(to_jsonb(upd) order by upd.priority, upd.created_at), '[]'::jsonb) into out from upd;
  return out;
end;
$$;

-- p: [{id, status: sent | failed | queued, error, retry_s}]
create function public.tg_alert_done(p jsonb) returns integer
language sql
security invoker
set search_path = ''
as $$
with r as (
  select (x->>'id')::bigint as id, x->>'status' as status, x->>'error' as error,
         coalesce((x->>'retry_s')::integer, 0) as retry_s
  from jsonb_array_elements(p) x
  where x->>'status' in ('sent', 'failed', 'queued')
), u as (
  update public.tg_alerts a set status = r.status, error = r.error, claimed_at = null,
         sent_at = case when r.status = 'sent' then now() else a.sent_at end,
         not_before = case when r.status = 'queued' then now() + make_interval(secs => r.retry_s) else a.not_before end
  from r where a.id = r.id and a.status = 'sending'
  returning 1
)
select count(*)::integer from u;
$$;

-- a chat blocked the bot (403): its watches and queued alerts go
create function public.tg_chat_gone(p_chat_id bigint) returns void
language sql
security invoker
set search_path = ''
as $$
delete from public.tg_chats where chat_id = p_chat_id;
$$;

-- ---------------------------------------------------------------------------------------------------------------
-- state, status, housekeeping
-- ---------------------------------------------------------------------------------------------------------------
create function public.tg_state_set(p_key text, p_value jsonb) returns void
language sql
security invoker
set search_path = ''
as $$
insert into public.tg_state (key, value) values (p_key, p_value)
on conflict (key) do update set value = excluded.value, updated_at = now();
$$;

create function public.tg_status(p_chat_id bigint) returns jsonb
language sql
stable
security invoker
set search_path = ''
as $$
select jsonb_build_object(
  'watches', (select count(*) from public.tg_watches where chat_id = p_chat_id),
  'watched_tokens', (select count(distinct (chain, token)) from public.tg_watches),
  'queued', (select count(*) from public.tg_alerts where status in ('queued', 'sending')),
  'sent_24h', (select count(*) from public.tg_alerts where chat_id = p_chat_id and status = 'sent'
               and sent_at > now() - interval '24 hours'),
  'last_alert_at', (select max(sent_at) from public.tg_alerts where chat_id = p_chat_id and status = 'sent'),
  'global_alerts', (select global_alerts from public.tg_chats where chat_id = p_chat_id),
  'cron', (select jsonb_build_object('value', value, 'at', updated_at) from public.tg_state where key = 'alerts_cron'));
$$;

create function public.tg_housekeeping() returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
  u integer; a integer; s integer;
begin
  delete from public.tg_updates where received_at < now() - interval '3 days';
  get diagnostics u = row_count;
  delete from public.tg_alerts where status in ('sent', 'failed', 'dropped') and created_at < now() - interval '30 days';
  get diagnostics a = row_count;
  delete from public.tg_snapshots s where not exists (
    select 1 from public.tg_watches w where w.chain = s.chain and w.token = s.token);
  get diagnostics s = row_count;
  return jsonb_build_object('updates', u, 'alerts', a, 'snapshots', s);
end;
$$;

-- ---------------------------------------------------------------------------------------------------------------
-- /wallet: what memory knows about one address (all chains unless p_chain): tokens it top-held (latest scan of each),
-- tokens it deployed, its funder and the wallets it funded, launch bundles and operators it was part of
-- ---------------------------------------------------------------------------------------------------------------
create function public.wallet_profile(p_address text, p_chain text default null, p_limit integer default 8)
returns jsonb
language sql
stable
security invoker
set search_path = ''
as $$
with lim as (select greatest(1, least(p_limit, 25)) as n),
held as (
  select distinct on (s.chain, s.token) s.chain, s.token, s.id as scan_id, s.scanned_at, s.band, s.score,
         h.rank, h.share, h.flags, h.operator
  from public.holdings h join public.scans s on s.id = h.scan_id
  where h.wallet = p_address and (p_chain is null or h.chain = p_chain)
  order by s.chain, s.token, s.scanned_at desc
),
best as (
  select o.chain, o.token, jsonb_agg(distinct o.label) as labels
  from public.outcomes o
  where o.measured_at is not null and o.label is not null
    and (o.chain, o.token) in (select chain, token from held
                               union select chain, token from public.tokens where deployer = p_address)
  group by o.chain, o.token
)
select jsonb_build_object(
  'address', p_address,
  'held', coalesce((select jsonb_agg(x order by x->>'scanned_at' desc) from (
     select jsonb_build_object('chain', h.chain, 'token', h.token, 'ticker', k.ticker, 'band', h.band, 'score', h.score,
                               'rank', h.rank, 'share', h.share, 'flags', h.flags, 'scanned_at', h.scanned_at,
                               'outcomes', b.labels) as x
     from held h left join public.tokens k on k.chain = h.chain and k.token = h.token
     left join best b on b.chain = h.chain and b.token = h.token
     order by h.scanned_at desc limit (select n from lim)) q), '[]'::jsonb),
  'held_total', (select count(*) from held),
  'deployed', coalesce((select jsonb_agg(x) from (
     select jsonb_build_object('chain', k.chain, 'token', k.token, 'ticker', k.ticker, 'band', k.last_band,
                               'score', k.last_score, 'first_scan_at', k.first_scan_at, 'outcomes', b.labels) as x
     from public.tokens k left join best b on b.chain = k.chain and b.token = k.token
     where k.deployer = p_address and (p_chain is null or k.chain = p_chain)
     order by k.first_scan_at desc limit (select n from lim)) q), '[]'::jsonb),
  'deployed_total', (select count(*) from public.tokens where deployer = p_address and (p_chain is null or chain = p_chain)),
  'funded_by', (select jsonb_agg(jsonb_build_object('chain', f.chain, 'funder', f.funder, 'hub', fs.hub))
                from public.funding f left join public.funder_stats fs on fs.chain = f.chain and fs.address = f.funder
                where f.wallet = p_address and (p_chain is null or f.chain = p_chain)),
  'funded', (select count(*) from public.funding where funder = p_address and (p_chain is null or chain = p_chain)),
  'hub', (select bool_or(hub) from public.funder_stats where address = p_address and (p_chain is null or chain = p_chain)),
  'bundles', (select count(distinct scan_id) from public.bundle_participants
              where wallet = p_address and (p_chain is null or chain = p_chain)),
  'operators', (select count(*) from public.operators
                where wallets @> array[p_address] and cardinality(wallets) > 1 and (p_chain is null or chain = p_chain)),
  'rugged', (select count(*) from best b, jsonb_array_elements_text(b.labels) l where l = 'rugged'),
  'up', (select count(*) from best b, jsonb_array_elements_text(b.labels) l where l = 'up'));
$$;

do $$
declare
  f text;
begin
  foreach f in array array[
    'tg_update_seen(bigint)', 'tg_chat_touch(bigint, bigint, text)',
    'tg_watch(bigint, text, text, text, integer)', 'tg_unwatch(bigint, text, text)', 'tg_watchlist(bigint)',
    'tg_watch_due(integer, integer)', 'tg_watch_checked(text, text)', 'tg_watched()', 'tg_radar_set(jsonb)',
    'tg_snapshot_swap(text, text, jsonb)', 'tg_snapshot_early(text, text, double precision)', 'tg_global_chats()',
    'tg_token_labels(text, text[])', 'tg_alert_enqueue(jsonb)', 'tg_alert_claim(integer, integer, integer)',
    'tg_alert_done(jsonb)', 'tg_chat_gone(bigint)', 'tg_state_set(text, jsonb)', 'tg_status(bigint)',
    'tg_housekeeping()', 'wallet_profile(text, text, integer)']
  loop
    execute format('revoke all on function public.%s from public, anon, authenticated', f);
    execute format('grant execute on function public.%s to service_role', f);
  end loop;
end;
$$;
