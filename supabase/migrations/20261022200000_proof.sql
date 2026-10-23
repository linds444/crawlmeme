-- Phase 13: historical proof. Scan-time records become immutable and fingerprinted; a tracker samples the market of
-- every scanned token through its observation window; proof_rows returns each token's first scan with what its market
-- did afterwards, for Rug Replay, Gem Replay and Proof of Work (crawler/proof.py classifies; nothing here judges).

-- ---------------------------------------------------------------------------------------------------------------
-- fingerprint: sha256 of the scan-time record, computed by the database when the row is written
-- ---------------------------------------------------------------------------------------------------------------
alter table public.scans add column record_hash text;

create function public.scan_fingerprint(s public.scans) returns text
language sql
immutable
set search_path = ''
as $$
select encode(pg_catalog.sha256(convert_to(jsonb_build_object(
  'chain', s.chain, 'token', s.token, 'scanned_at', s.scanned_at, 'band', s.band, 'score', s.score,
  'model', s.model, 'scores', s.scores, 'market', s.market, 'rug', s.rug, 'coverage', s.coverage,
  'confidence', s.confidence, 'holders_total', s.holders_total,
  'largest_operator_share', s.largest_operator_share)::text, 'UTF8')), 'hex');
$$;

update public.scans s set record_hash = public.scan_fingerprint(s) where record_hash is null;

create function public.scans_fingerprint() returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.record_hash := public.scan_fingerprint(new);
  return new;
end;
$$;
create trigger scans_fingerprint before insert on public.scans
  for each row execute function public.scans_fingerprint();

-- ---------------------------------------------------------------------------------------------------------------
-- immutability: scan records and market snapshots are append-only; an outcome can be measured once, never rewritten
-- ---------------------------------------------------------------------------------------------------------------
create function public.append_only() returns trigger
language plpgsql
set search_path = ''
as $$
begin
  raise exception '% rows are append-only (historical proof)', tg_table_name;
end;
$$;
create trigger scans_append_only before update or delete on public.scans
  for each row execute function public.append_only();
create trigger market_snapshots_append_only before update or delete on public.market_snapshots
  for each row execute function public.append_only();

create function public.outcomes_measured_once() returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if tg_op = 'DELETE' or old.measured_at is not null then
    raise exception 'a measured outcome is immutable (historical proof)';
  end if;
  return new;
end;
$$;
create trigger outcomes_measured_once before update or delete on public.outcomes
  for each row execute function public.outcomes_measured_once();

-- ---------------------------------------------------------------------------------------------------------------
-- tracker queue: tokens scanned within p_window_h whose newest market snapshot is older than p_gap_min
-- ---------------------------------------------------------------------------------------------------------------
create function public.track_due(p_window_h integer, p_gap_min integer, p_limit integer default 300)
returns jsonb
language sql
stable
security invoker
set search_path = ''
as $$
select coalesce(jsonb_agg(jsonb_build_object('chain', t.chain, 'token', t.token) order by t.last_obs nulls first),
                '[]'::jsonb)
from (
  select s.chain, s.token,
         (select max(m.observed_at) from public.market_snapshots m
          where m.chain = s.chain and m.token = s.token) as last_obs
  from public.scans s
  where s.scanned_at > now() - make_interval(hours => greatest(1, p_window_h))
  group by s.chain, s.token
) t
where t.last_obs is null or t.last_obs < now() - make_interval(mins => greatest(1, p_gap_min))
limit greatest(1, least(p_limit, 1000));
$$;

-- ---------------------------------------------------------------------------------------------------------------
-- proof rows: every token's FIRST scan with a price inside [p_since, p_until] (one row per token: no double counting
-- of tokens scanned many times), its scan-time record, and its market inside (scanned_at, scanned_at + p_window_h]
-- ---------------------------------------------------------------------------------------------------------------
create function public.proof_rows(p_since timestamptz, p_until timestamptz, p_window_h integer,
                                  p_chain text default null, p_limit integer default 2000)
returns jsonb
language sql
stable
security invoker
set search_path = ''
as $$
with firsts as (
  select distinct on (s.chain, s.token) s.*
  from public.scans s
  where s.scanned_at >= p_since and s.scanned_at <= p_until
    and (p_chain is null or s.chain = p_chain)
    and s.band in ('CLEAN', 'OK', 'RISKY', 'DANGER')
    and (s.market->>'price_usd') is not null
  order by s.chain, s.token, s.scanned_at
),
pick as (select * from firsts order by scanned_at desc limit greatest(1, least(p_limit, 5000)))
select coalesce(jsonb_agg(jsonb_build_object(
  'scan_id', f.id, 'chain', f.chain, 'token', f.token, 'ticker', k.ticker, 'launchpad', k.launchpad,
  'scanned_at', f.scanned_at, 'band', f.band, 'score', f.score, 'model', f.model, 'scores', f.scores,
  'coverage', f.coverage, 'confidence', f.confidence, 'market', f.market, 'record_hash', f.record_hash,
  'n', a.n, 'peak_mcap', a.peak_mcap, 'trough_mcap', a.trough_mcap, 'peak_price', a.peak_price,
  'trough_price', a.trough_price, 'first_obs', a.first_obs, 'last_obs', a.last_obs,
  'peak_at', pk.observed_at, 'trough_at', tr.observed_at,
  'last', case when ls.observed_at is null then null else jsonb_build_object(
    'mcap_usd', ls.mcap_usd, 'price_usd', ls.price_usd, 'liquidity_usd', ls.liquidity_usd,
    'observed_at', ls.observed_at, 'source', ls.source) end) order by f.scanned_at desc), '[]'::jsonb)
from pick f
left join public.tokens k on k.chain = f.chain and k.token = f.token
-- the market inside the observation window (snapshots taken by this scan itself excluded)
cross join lateral (
  select count(*) as n, max(m.mcap_usd) as peak_mcap, min(m.mcap_usd) as trough_mcap,
         max(m.price_usd) as peak_price, min(m.price_usd) as trough_price,
         min(m.observed_at) as first_obs, max(m.observed_at) as last_obs
  from public.market_snapshots m
  where m.chain = f.chain and m.token = f.token and m.scan_id is distinct from f.id
    and m.observed_at > f.scanned_at and m.observed_at <= f.scanned_at + make_interval(hours => p_window_h)
) a
left join lateral (
  select m.observed_at from public.market_snapshots m
  where m.chain = f.chain and m.token = f.token and m.scan_id is distinct from f.id
    and m.observed_at > f.scanned_at and m.observed_at <= f.scanned_at + make_interval(hours => p_window_h)
  order by coalesce(m.mcap_usd, m.price_usd) desc nulls last, m.observed_at limit 1
) pk on true
left join lateral (
  select m.observed_at from public.market_snapshots m
  where m.chain = f.chain and m.token = f.token and m.scan_id is distinct from f.id
    and m.observed_at > f.scanned_at and m.observed_at <= f.scanned_at + make_interval(hours => p_window_h)
  order by coalesce(m.mcap_usd, m.price_usd) asc nulls last, m.observed_at limit 1
) tr on true
left join lateral (
  select m.* from public.market_snapshots m
  where m.chain = f.chain and m.token = f.token and m.scan_id is distinct from f.id
    and m.observed_at > f.scanned_at and m.observed_at <= f.scanned_at + make_interval(hours => p_window_h)
  order by m.observed_at desc limit 1
) ls on true;
$$;

-- the supporting data of one proof row: the immutable scan record, its fingerprint check, every market snapshot
-- after it and every measured outcome
create function public.proof_scan(p_scan_id bigint)
returns jsonb
language sql
stable
security invoker
set search_path = ''
as $$
select jsonb_build_object(
  'scan', to_jsonb(s) - 'data_health',
  'fingerprint_ok', s.record_hash = public.scan_fingerprint(s),
  'snapshots', coalesce((select jsonb_agg(jsonb_build_object('observed_at', m.observed_at, 'source', m.source,
                                                             'price_usd', m.price_usd, 'mcap_usd', m.mcap_usd,
                                                             'liquidity_usd', m.liquidity_usd)
                                          order by m.observed_at)
                         from public.market_snapshots m
                         where m.chain = s.chain and m.token = s.token and m.observed_at > s.scanned_at
                           and m.scan_id is distinct from s.id), '[]'::jsonb),
  'outcomes', coalesce((select jsonb_agg(jsonb_build_object('horizon_h', o.horizon_h, 'label', o.label,
                                                            'measured_at', o.measured_at, 'price_ratio',
                                                            o.price_ratio, 'liquidity_ratio', o.liquidity_ratio)
                                         order by o.horizon_h)
                        from public.outcomes o where o.scan_id = s.id), '[]'::jsonb))
from public.scans s where s.id = p_scan_id;
$$;

revoke all on function public.scan_fingerprint(public.scans) from public, anon, authenticated;
revoke all on function public.track_due(integer, integer, integer) from public, anon, authenticated;
revoke all on function public.proof_rows(timestamptz, timestamptz, integer, text, integer) from public, anon,
  authenticated;
revoke all on function public.proof_scan(bigint) from public, anon, authenticated;
grant execute on function public.scan_fingerprint(public.scans) to service_role;
grant execute on function public.track_due(integer, integer, integer) to service_role;
grant execute on function public.proof_rows(timestamptz, timestamptz, integer, text, integer) to service_role;
grant execute on function public.proof_scan(bigint) to service_role;
