-- Phase 12: launch radar. Read-side functions over the memory brain (no new tables): the radar feed, which tokens were
-- scanned recently (discovery skips them), and market snapshots taken between scans (radar refresh). Server only.

-- every token first seen (launched, or first scanned when the launch time is unknown) since p_since, with its latest
-- scan, the scan before it, its bundle at both scans, the newest market snapshot and any measured outcome
create function public.radar_feed(p_since timestamptz, p_limit integer default 300)
returns jsonb
language sql
stable
security invoker
set search_path = ''
as $$
with fresh as (
  select k.chain, k.token, k.ticker, k.name, k.launchpad, k.deployer, k.first_scan_at, l.launched_at
  from public.tokens k
  left join public.launches l on l.chain = k.chain and l.token = k.token
  where coalesce(l.launched_at, k.first_scan_at) >= p_since
  order by coalesce(l.launched_at, k.first_scan_at) desc
  limit greatest(1, least(p_limit, 500))
),
ranked as (
  select s.*, row_number() over (partition by s.chain, s.token order by s.scanned_at desc) as n
  from public.scans s
  join fresh f on f.chain = s.chain and f.token = s.token
),
pick as (
  select f.*, cur.id as scan_id, prev.id as prev_id
  from fresh f
  join ranked cur on cur.chain = f.chain and cur.token = f.token and cur.n = 1
  left join ranked prev on prev.chain = f.chain and prev.token = f.token and prev.n = 2
)
select coalesce(jsonb_agg(jsonb_build_object(
  'chain', p.chain, 'token', p.token, 'ticker', p.ticker, 'name', p.name, 'launchpad', p.launchpad,
  'deployer', p.deployer, 'launched_at', p.launched_at, 'first_scan_at', p.first_scan_at,
  'scan', (select jsonb_build_object('id', s.id, 'scanned_at', s.scanned_at, 'band', s.band, 'score', s.score,
                                     'coverage', s.coverage, 'confidence', s.confidence, 'holders_total',
                                     s.holders_total, 'largest_operator_share', s.largest_operator_share,
                                     'rug', s.rug is not null and s.rug <> 'null'::jsonb, 'market', s.market,
                                     'scores', s.scores)
           from public.scans s where s.id = p.scan_id),
  'prev', (select jsonb_build_object('id', s.id, 'scanned_at', s.scanned_at, 'band', s.band, 'score', s.score,
                                     'largest_operator_share', s.largest_operator_share)
           from public.scans s where s.id = p.prev_id),
  'bundle', (select jsonb_build_object('mode', b.mode, 'confidence', b.confidence, 'initial_share', b.initial_share,
                                       'concentrated_share', b.concentrated_share, 'sold_share', b.sold_share)
             from public.bundles b where b.scan_id = p.scan_id),
  'prev_bundle', (select jsonb_build_object('concentrated_share', b.concentrated_share, 'sold_share', b.sold_share)
                  from public.bundles b where b.scan_id = p.prev_id),
  'market_now', (select jsonb_build_object('observed_at', m.observed_at, 'price_usd', m.price_usd,
                                           'liquidity_usd', m.liquidity_usd, 'mcap_usd', m.mcap_usd,
                                           'source', m.source)
                 from public.market_snapshots m where m.chain = p.chain and m.token = p.token
                 order by m.observed_at desc limit 1),
  'outcome', (select jsonb_build_object('label', o.label, 'horizon_h', o.horizon_h, 'measured_at', o.measured_at)
              from public.outcomes o where o.chain = p.chain and o.token = p.token and o.measured_at is not null
              order by o.measured_at desc limit 1)
)), '[]'::jsonb)
from pick p;
$$;

-- which of these tokens have a scan newer than p_minutes (discovery does not rescan them)
create function public.radar_recent(p_chain text, p_tokens text[], p_minutes integer)
returns jsonb
language sql
stable
security invoker
set search_path = ''
as $$
select coalesce(jsonb_agg(distinct s.token), '[]'::jsonb)
from public.scans s
where s.chain = p_chain and s.token = any (p_tokens)
  and s.scanned_at > now() - make_interval(mins => greatest(1, p_minutes));
$$;

-- market snapshots taken between scans (the radar refresh): rows {chain, token, source, price_usd, mcap_usd,
-- fdv_usd, liquidity_usd}
create function public.market_snapshot_record(p jsonb) returns integer
language plpgsql
security invoker
set search_path = ''
as $$
declare
  n integer;
begin
  insert into public.market_snapshots (chain, token, source, price_usd, mcap_usd, fdv_usd, liquidity_usd)
  select r->>'chain', r->>'token', r->>'source', (r->>'price_usd')::double precision,
         (r->>'mcap_usd')::double precision, (r->>'fdv_usd')::double precision,
         (r->>'liquidity_usd')::double precision
  from jsonb_array_elements(coalesce(p->'rows', '[]'::jsonb)) r
  where r->>'chain' is not null and r->>'token' is not null
    and (r->>'price_usd' is not null or r->>'liquidity_usd' is not null);
  get diagnostics n = row_count;
  return n;
end;
$$;

revoke all on function public.radar_feed(timestamptz, integer) from public, anon, authenticated;
revoke all on function public.radar_recent(text, text[], integer) from public, anon, authenticated;
revoke all on function public.market_snapshot_record(jsonb) from public, anon, authenticated;
grant execute on function public.radar_feed(timestamptz, integer) to service_role;
grant execute on function public.radar_recent(text, text[], integer) to service_role;
grant execute on function public.market_snapshot_record(jsonb) to service_role;
