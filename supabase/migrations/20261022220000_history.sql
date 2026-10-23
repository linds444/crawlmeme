-- Phase 14: scan history of one token for the result page (newest first): every immutable scan record CrawlMeme
-- holds for it, with its fingerprint, the market at scan time, the measured outcomes, and the token's newest market
-- snapshot so the page can show "then vs now". Read-only; the scans themselves are append-only (Phase 13).
create function public.token_history(p_chain text, p_token text, p_limit integer default 20)
returns jsonb
language sql
stable
security invoker
set search_path = ''
as $$
select jsonb_build_object(
  'scans', coalesce((
    select jsonb_agg(x.j order by x.scanned_at desc)
    from (
      select s.scanned_at, jsonb_build_object(
        'scan_id', s.id, 'scanned_at', s.scanned_at, 'band', s.band, 'score', s.score, 'model', s.model,
        'coverage', s.coverage, 'confidence', s.confidence, 'holders_total', s.holders_total,
        'largest_operator_share', s.largest_operator_share, 'record_hash', s.record_hash,
        'rug', s.rug is not null and s.rug <> 'null'::jsonb,
        'price_usd', s.market->'price_usd', 'mcap_usd', s.market->'mcap_usd',
        'liquidity_usd', s.market->'liquidity_usd',
        'outcomes', coalesce((select jsonb_agg(jsonb_build_object('horizon_h', o.horizon_h, 'label', o.label)
                                               order by o.horizon_h)
                              from public.outcomes o where o.scan_id = s.id and o.measured_at is not null),
                             '[]'::jsonb)) as j
      from public.scans s
      where s.chain = p_chain and s.token = p_token
      order by s.scanned_at desc
      limit greatest(1, least(p_limit, 100))
    ) x), '[]'::jsonb),
  'latest', (
    select jsonb_build_object('observed_at', m.observed_at, 'price_usd', m.price_usd, 'mcap_usd', m.mcap_usd,
                              'liquidity_usd', m.liquidity_usd, 'source', m.source)
    from public.market_snapshots m
    where m.chain = p_chain and m.token = p_token
    order by m.observed_at desc limit 1));
$$;

revoke all on function public.token_history(text, text, integer) from public, anon, authenticated;
grant execute on function public.token_history(text, text, integer) to service_role;
